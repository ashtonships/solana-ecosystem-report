#!/usr/bin/env python3
"""Fail-closed checks for public Git data and deployable report bytes."""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import charts
import collect
import detect
import facts
import growth
import pipeline
import render


ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATH = ROOT / "snapshots" / "latest.json"
FACTS_PATH = ROOT / "history" / "facts.jsonl"
STATE_PATH = ROOT / "state" / "xstocks-supply.json"
MANIFEST_SCHEMA_VERSION = 1
DERIVED_ROOTS = frozenset((
    "release", "anomalies", "delta", "history", "upgrades", "observations",
))
FACT_FIELDS = frozenset((
    "metric_id", "subject_id", "event_time", "event_slot", "collected_at",
    "value", "unit", "basis", "state", "source", "source_revision",
    "source_schema", "coverage", "quality",
))
FACT_COVERAGE_FIELDS = {
    "performance_sample_tps": frozenset((
        "sample_period_seconds", "slots", "transactions", "non_vote_transactions",
    )),
    "validator_commission_pct": frozenset((
        "identity", "vote_state", "snapshot_slot", "fact_contract",
    )),
    "simd_lifecycle_status": frozenset((
        "status", "name", "created", "source", "source_path", "source_commit",
    )),
    "stablecoin_active_address_provider_range": frozenset((
        "source_label", "display_name", "scope", "source_url", "source_generated_at",
    )),
    "transaction_initiator_provider_range": frozenset((
        "source_label", "display_name", "scope", "source_url", "source_generated_at",
    )),
}
XSTOCK_COVERAGE_FIELDS = frozenset((
    "symbol", "name", "slug", "mint", "raw_amount", "decimals",
    "rpc_ui_amount", "rpc_ui_amount_string", "supply_context_slot",
    "rpc_api_version", "supply_collected_at", "registry_source_key",
    "registry_source_path", "registry_source_url", "registry_source_revision",
    "registry_source_license", "registry_provenance", "registry_complete",
    "coverage_numerator", "coverage_denominator", "coverage_label",
    "coverage_basis", "fact_contract", "value_contract",
))
STABLECOIN_COVERAGE_FIELDS = frozenset((
    "symbol", "issuer", "mint", "raw_amount", "decimals", "total_supply_decimal",
    "rpc_ui_amount_string", "rpc_api_version", "account_source_method",
    "account_rpc_context_slot", "account_rpc_api_version", "token_program_id",
    "token_program", "registry_source_key", "registry_source_path",
    "registry_source_url", "registry_source_revision", "registry_source_license",
    "coverage_numerator", "coverage_denominator", "coverage_label",
    "coverage_basis", "universe_coverage", "fact_contract", "value_contract",
))
MULTIPLIER_FIELDS = frozenset((
    "source_method", "program_id", "program", "rpc_context_slot",
    "rpc_api_version", "extension", "state",
))
ACCOUNT_FIELDS = frozenset((
    "source_method", "program_id", "program", "rpc_context_slot", "rpc_api_version",
))
MULTIPLIER_STATE_FIELDS = frozenset((
    "authority", "multiplier", "newMultiplier", "newMultiplierEffectiveTimestamp",
))
REGISTRY_PROVENANCE_FIELDS = frozenset((
    "repository", "path", "revision", "license", "selection",
    "expected_unique_group_count", "expected_unique_mint_count",
))
STATE_FIELDS = frozenset((
    "version", "rpc_endpoint_identity", "cursor_mint", "updated_at", "observations",
))
STATE_OBSERVATION_FIELDS = frozenset((
    "collected_at", "decimals", "raw_amount", "rpc_api_version", "rpc_context_slot",
    "ui_amount", "ui_amount_string",
))
ARTIFACT_NAMES = frozenset(("index.html", "report.md", "report.json"))
PROTECTED_PATH_PREFIXES = ("snapshots/", "history/", "state/", "samples/")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
OBSERVATION_ID_PATTERN = re.compile(r"obs-v1:[0-9a-f]{64}")
PRIVATE_TEXT_PATTERNS = (
    re.compile(
        r"(?:/Users/|/private/tmp/|/tmp/|/var/folders/|/home/|file://|"
        r"[A-Za-z]:\\Users\\)"
    ),
    re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?", re.IGNORECASE),
    re.compile(
        r"(?:authorization|api[_-]?key|client[_-]?secret|private[_-]?key)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}",
        re.IGNORECASE,
    ),
    re.compile(
        r'"(?:pid|process_id|process_metadata|session_id|session_metadata|terminal_id|'
        r'private_receipt|hermes_receipt)"\s*:',
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)
PUBLIC_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SENSITIVE_QUERY_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth(?:orization)?|client[_-]?secret|private[_-]?key|secret|token|"
    r"passw(?:or)?d|credential|signature|sig|x-amz-signature|x-amz-credential|"
    r"x-amz-security-token|awsaccesskeyid)$",
    re.IGNORECASE,
)
EXTERNAL_RUNTIME_PATTERN = re.compile(
    r"(?:<(?:script|img|iframe|link|video|audio|source|embed|object|image|use|base|form)"
    r"\b[^>]*(?:src|srcset|href|data|action)\s*=\s*(?:[\"']\s*)?(?:https?:)?//|"
    r"(?:url\(\s*|@import\s+(?:url\(\s*)?)(?:[\"']\s*)?(?:https?:)?//|"
    r"<meta\b[^>]*http-equiv\s*=\s*(?:[\"']\s*)?refresh\b)",
    re.IGNORECASE,
)


class ReleaseVerificationError(ValueError):
    """One release invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseVerificationError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseVerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReleaseVerificationError(f"non-finite JSON value: {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseVerificationError(f"{label} is not strict UTF-8 JSON: {error}") from error


def canonical_json(value: Any, *, sort_keys: bool = False) -> bytes:
    return (json.dumps(
        value, indent=2, sort_keys=sort_keys, allow_nan=False,
    ) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def aware_timestamp(value: Any, label: str) -> str:
    require(isinstance(value, str), f"{label} must be an offset-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseVerificationError(f"{label} is not ISO-8601") from error
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label} must include a UTC offset")
    return value


def reference_time(value: str | None, label: str = "verification time") -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    aware_timestamp(value, label)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fact_event_time(value: str, label: str) -> datetime:
    """Parse an offset-aware timestamp or a bare source date (UTC midnight).

    The facts contract records provider-row source dates as dates and never
    invents a time of day; ordering checks interpret such dates as the start
    of that day in UTC.
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None:
        try:
            return datetime.fromisoformat(f"{value}T00:00:00+00:00")
        except ValueError as error:
            raise ReleaseVerificationError(
                f"{label} is not a valid date"
            ) from error
    aware_timestamp(value, label)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def exact_fields(value: Any, allowed: frozenset[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    keys = frozenset(value)
    require(keys == allowed,
            f"{label} fields mismatch: extra={sorted(keys - allowed)} "
            f"missing={sorted(allowed - keys)}")
    return value


def verify_snapshot(
    path: Path, *, now: datetime | None = None,
) -> tuple[bytes, dict[str, Any], Path]:
    require(path.is_file() and not path.is_symlink(), f"missing regular snapshot: {path}")
    raw = path.read_bytes()
    snapshot = strict_json(raw, str(path))
    require(isinstance(snapshot, dict), "snapshot root must be an object")
    require(raw == canonical_json(snapshot), "snapshot bytes are not canonical collector JSON")
    require(snapshot.get("schema_version") == collect.SCHEMA_VERSION,
            f"snapshot schema must be {collect.SCHEMA_VERSION}")
    require(not DERIVED_ROOTS.intersection(snapshot),
            f"raw snapshot contains derived roots: {sorted(DERIVED_ROOTS.intersection(snapshot))}")
    try:
        projected = render.project_public_envelope(snapshot)
    except ValueError as error:
        raise ReleaseVerificationError(f"snapshot public projection failed: {error}") from error
    require(projected == snapshot, "snapshot contains fields outside the recursive public schema")
    provenance = exact_fields(
        snapshot.get("provenance"), frozenset(("source_revision", "source_tree_dirty")),
        "snapshot provenance",
    )
    require(isinstance(provenance.get("source_revision"), str)
            and SHA_PATTERN.fullmatch(provenance["source_revision"]) is not None,
            "collector source revision must be a 40-character Git SHA")
    require(provenance.get("source_tree_dirty") is False,
            "collector source tree must be clean")
    collected_at = aware_timestamp(snapshot.get("collected_at"), "snapshot collected_at")
    immutable = path.parent / collect.snapshot_filename(collected_at)
    require(immutable.is_file() and not immutable.is_symlink(),
            f"missing named immutable snapshot: {immutable}")
    require(immutable.read_bytes() == raw,
            "latest snapshot bytes do not match the named immutable snapshot")
    reference = now if now is not None else datetime.now(timezone.utc)
    require(reference.tzinfo is not None and reference.utcoffset() is not None,
            "verification time must include a UTC offset")
    gate = pipeline.check_publishable(
        snapshot, now=reference,
        max_age_seconds=collect.PUBLICATION_FRESHNESS_SECONDS,
    )
    require(gate.get("publishable") is True,
            f"snapshot fails the publication gate: {gate.get('failures')}")
    return raw, snapshot, immutable


def verify_snapshot_history(
    snapshot_dir: Path, selected_raw: bytes, selected: dict[str, Any],
    immutable: Path,
) -> list[dict[str, Any]]:
    """Validate every immutable public snapshot instead of silently skipping history."""
    selected_at = datetime.fromisoformat(
        selected["collected_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    records: list[dict[str, Any]] = []
    seen_instants: set[datetime] = set()
    previous: datetime | None = None
    for path in sorted(snapshot_dir.glob("snapshot-*.json")):
        label = str(path)
        require(path.is_file() and not path.is_symlink(),
                f"history snapshot is not a regular file: {path}")
        raw = path.read_bytes()
        value = strict_json(raw, label)
        require(isinstance(value, dict), f"history snapshot root must be an object: {path.name}")
        require(raw == canonical_json(value),
                f"history snapshot is not canonical collector JSON: {path.name}")
        collected_at = aware_timestamp(
            value.get("collected_at"), f"history snapshot {path.name} collected_at",
        )
        require(path.name == collect.snapshot_filename(collected_at),
                f"history snapshot filename does not match collected_at: {path.name}")
        instant = datetime.fromisoformat(
            collected_at.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        require(instant not in seen_instants,
                f"history snapshots duplicate one UTC collection instant: {collected_at}")
        require(previous is None or instant > previous,
                f"history snapshot instants are not strictly increasing: {path.name}")
        require(instant <= selected_at,
                f"history snapshot is after the selected snapshot: {path.name}")
        seen_instants.add(instant)
        previous = instant
        require(not DERIVED_ROOTS.intersection(value),
                f"history snapshot contains derived roots: {path.name}")
        schema = value.get("schema_version")
        require(isinstance(schema, int) and not isinstance(schema, bool),
                f"history snapshot schema is invalid: {path.name}")
        if schema in render.PUBLIC_ROOT_FIELDS:
            try:
                projected = render.project_public_envelope(value)
            except ValueError as error:
                raise ReleaseVerificationError(
                    f"history snapshot public projection failed: {path.name}: {error}"
                ) from error
            require(projected == value,
                    f"history snapshot contains fields outside the recursive public schema: "
                    f"{path.name}")
        else:
            require(schema in render.LEGACY_PUBLIC_HISTORY_SCHEMAS,
                    f"history snapshot schema is unsupported: {path.name}")
            try:
                projected = render.project_legacy_history_envelope(value)
            except ValueError as error:
                raise ReleaseVerificationError(
                    f"legacy history snapshot projection failed: {path.name}: {error}"
                ) from error
            require(projected == value,
                    f"legacy history snapshot contains fields outside the recursive public "
                    f"schema: {path.name}")
        _scan_public_text(raw.decode("utf-8"), f"history snapshot {path.name}")
        records.append({
            "path": path,
            "raw": raw,
            "snapshot": value,
            "instant": instant,
        })
    require(bool(records), "snapshot history is empty")
    final = records[-1]
    require(final["path"] == immutable and final["raw"] == selected_raw,
            "selected immutable snapshot is not the final exact history entry")
    return records


def _verify_xstock_coverage(coverage: dict[str, Any], label: str) -> None:
    provenance_names = [
        name for name in ("multiplier_provenance", "account_provenance")
        if name in coverage
    ]
    require(len(provenance_names) == 1,
            f"{label} must contain exactly one supply provenance object")
    allowed = XSTOCK_COVERAGE_FIELDS | frozenset(provenance_names)
    exact_fields(coverage, allowed, label)
    provenance_name = provenance_names[0]
    provenance = coverage[provenance_name]
    if provenance_name == "multiplier_provenance":
        exact_fields(provenance, MULTIPLIER_FIELDS, f"{label}.{provenance_name}")
        exact_fields(provenance.get("state"), MULTIPLIER_STATE_FIELDS,
                     f"{label}.{provenance_name}.state")
    else:
        exact_fields(provenance, ACCOUNT_FIELDS, f"{label}.{provenance_name}")
    exact_fields(coverage.get("registry_provenance"), REGISTRY_PROVENANCE_FIELDS,
                 f"{label}.registry_provenance")


def verify_fact_record(record: Any, line_number: int) -> dict[str, Any]:
    label = f"fact line {line_number}"
    fact = exact_fields(record, FACT_FIELDS, label)
    require(isinstance(fact.get("source_schema"), int)
            and not isinstance(fact["source_schema"], bool)
            and fact["source_schema"] > 0,
            f"{label}.source_schema must be a positive integer")
    require(fact.get("source_revision") is None or (
        isinstance(fact["source_revision"], str)
        and SHA_PATTERN.fullmatch(fact["source_revision"]) is not None
    ), f"{label}.source_revision must be null or a 40-character Git SHA")
    try:
        adapted = facts.adapt_fact(fact)
        facts.validate_fact(fact)
    except facts.FactConflictError as error:
        raise ReleaseVerificationError(f"{label} is invalid: {error}") from error
    require(adapted == fact, f"{label} uses a legacy or non-canonical fact contract")
    metric_id = fact["metric_id"]
    coverage = fact.get("coverage")
    if metric_id == facts.XSTOCK_METRIC_ID:
        require(isinstance(coverage, dict), f"{label}.coverage must be an object")
        _verify_xstock_coverage(coverage, f"{label}.coverage")
    elif metric_id == facts.SELECTED_STABLECOIN_METRIC_ID:
        exact_fields(coverage, STABLECOIN_COVERAGE_FIELDS, f"{label}.coverage")
    elif metric_id in FACT_COVERAGE_FIELDS:
        exact_fields(coverage, FACT_COVERAGE_FIELDS[metric_id], f"{label}.coverage")
    elif metric_id in facts.PUBLIC_METRICS:
        if coverage is not None:
            exact_fields(coverage, frozenset(("public_value",)), f"{label}.coverage")
            require(not isinstance(coverage["public_value"], (dict, list)),
                    f"{label}.coverage.public_value must be scalar")
    else:
        raise ReleaseVerificationError(f"{label} has unsupported metric_id {metric_id!r}")
    return fact


def verify_facts_bytes(raw: bytes, label: str) -> list[dict[str, Any]]:
    require(raw.endswith(b"\n"), f"{label} must end with a newline")
    rows = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        require(bool(line), f"{label} contains an empty line at {line_number}")
        rows.append(verify_fact_record(strict_json(line, f"{label}:{line_number}"), line_number))
    try:
        canonical_rows = facts.dedupe_facts(rows)
    except facts.FactConflictError as error:
        raise ReleaseVerificationError(f"{label} conflicts: {error}") from error
    expected = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        .encode("utf-8") for row in canonical_rows
    )
    require(raw == expected, f"{label} is not canonical, ordered, and deduplicated")
    return rows


def verify_facts(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    require(path.is_file() and not path.is_symlink(), f"missing regular facts file: {path}")
    raw = path.read_bytes()
    return raw, verify_facts_bytes(raw, str(path))


def _snapshot_state_observations(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    growth_section = snapshot.get("growth")
    equities = growth_section.get("tokenized_equities") \
        if isinstance(growth_section, dict) else None
    if not isinstance(equities, dict):
        return {}
    assets = equities.get("all_assets")
    if assets is None and equities.get("available") is False:
        return {}
    require(isinstance(assets, list), "snapshot xStock all_assets must be a list")
    expected: dict[str, dict[str, Any]] = {}
    for asset in assets:
        require(isinstance(asset, dict), "snapshot xStock asset must be an object")
        mint = asset.get("mint")
        require(isinstance(mint, str) and mint, "snapshot xStock asset mint is invalid")
        if asset.get("supply") is None:
            continue
        observation = {
            "collected_at": asset.get("supply_collected_at"),
            "decimals": asset.get("supply_decimals"),
            "raw_amount": asset.get("supply_raw_amount"),
            "rpc_api_version": asset.get("supply_rpc_api_version"),
            "rpc_context_slot": asset.get("supply_context_slot"),
            "ui_amount": asset.get("supply_rpc_ui_amount"),
            "ui_amount_string": asset.get("supply_rpc_ui_amount_string"),
        }
        multiplier = asset.get("supply_multiplier_provenance")
        account = asset.get("supply_account_provenance")
        if multiplier is not None:
            observation["multiplier_provenance"] = multiplier
        if account is not None:
            observation["account_provenance"] = account
        expected[mint] = observation
    return expected


def verify_state(path: Path, snapshot: dict[str, Any]) -> tuple[bytes, dict[str, Any]] | None:
    if not path.exists():
        require(not path.is_symlink(), f"state path must not be a dangling symlink: {path}")
        require(not _snapshot_state_observations(snapshot),
                "supply state is required for current public supply observations")
        return None
    require(path.is_file() and not path.is_symlink(), f"state path is not a regular file: {path}")
    raw = path.read_bytes()
    state = exact_fields(strict_json(raw, str(path)), STATE_FIELDS, "supply state")
    require(raw == canonical_json(state, sort_keys=True), "supply state is not canonical JSON")
    require(state.get("version") == growth.SUPPLY_STATE_VERSION,
            "supply state version is unsupported")
    require(growth._validated_supply_state(state) is not None,
            "supply state fails its semantic contract")
    endpoint_identity = snapshot.get("source", {}).get("endpoint_identity")
    require(state.get("rpc_endpoint_identity") == endpoint_identity,
            "supply state RPC identity does not match the snapshot")
    cursor = state.get("cursor_mint")
    require(cursor is None or isinstance(cursor, str) and cursor,
            "supply state cursor is invalid")
    if state.get("updated_at") is not None:
        aware_timestamp(state["updated_at"], "supply state updated_at")
    observations = state.get("observations")
    require(isinstance(observations, dict), "supply state observations must be an object")
    for mint, observation in observations.items():
        label = f"supply state observation {mint}"
        provenance_names = [
            name for name in ("multiplier_provenance", "account_provenance")
            if name in observation
        ] if isinstance(observation, dict) else []
        require(len(provenance_names) == 1,
                f"{label} must contain exactly one provenance object")
        exact_fields(
            observation, STATE_OBSERVATION_FIELDS | frozenset(provenance_names), label,
        )
        provenance_name = provenance_names[0]
        provenance = observation[provenance_name]
        if provenance_name == "multiplier_provenance":
            exact_fields(provenance, MULTIPLIER_FIELDS, f"{label}.{provenance_name}")
            exact_fields(provenance.get("state"), MULTIPLIER_STATE_FIELDS,
                         f"{label}.{provenance_name}.state")
        else:
            exact_fields(provenance, ACCOUNT_FIELDS, f"{label}.{provenance_name}")
    current_observations = _snapshot_state_observations(snapshot)
    for mint, observation in current_observations.items():
        require(observations.get(mint) == observation,
                f"supply state does not retain current public observation {mint}")
    snapshot_at = datetime.fromisoformat(
        aware_timestamp(snapshot.get("collected_at"), "snapshot collected_at")
        .replace("Z", "+00:00")
    )
    if state.get("updated_at") is not None:
        require(datetime.fromisoformat(state["updated_at"].replace("Z", "+00:00")) <= snapshot_at,
                "supply state updated_at is after the selected snapshot")
    for mint, observation in observations.items():
        observed_at = datetime.fromisoformat(
            aware_timestamp(observation.get("collected_at"),
                            f"supply state observation {mint} collected_at")
            .replace("Z", "+00:00")
        )
        require(observed_at <= snapshot_at,
                f"supply state observation {mint} is after the selected snapshot")
    return raw, state


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args), cwd=root, check=check, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_blob(root: Path, revision: str, relative_path: str) -> bytes | None:
    target = f"{revision}:{relative_path}"
    exists = _git(root, "cat-file", "-e", target, check=False)
    if exists.returncode != 0:
        return None
    return _git(root, "show", target).stdout


def commit_parents(root: Path, revision: str) -> list[str]:
    return _git(root, "show", "-s", "--format=%P", revision).stdout.decode().split()


def changed_paths(root: Path, older: str, newer: str) -> set[str]:
    raw = _git(
        root, "diff", "--name-only", "-z", "--no-renames", older, newer,
    ).stdout
    paths = [os.fsdecode(value) for value in raw.split(b"\0") if value]
    require(len(paths) == len(set(paths)), "Git transition contains duplicate paths")
    return set(paths)


def last_change_revision(root: Path, paths: set[str]) -> str:
    revision = _git(
        root, "log", "-1", "--format=%H", "HEAD", "--", *sorted(paths),
    ).stdout.decode().strip()
    require(SHA_PATTERN.fullmatch(revision) is not None,
            "revision is missing from HEAD history")
    return revision


def git_tree_inventory(root: Path, revision: str) -> dict[str, tuple[str, int]]:
    raw = _git(root, "ls-tree", "-r", "-z", "-l", "--full-tree", revision).stdout
    inventory: dict[str, tuple[str, int]] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            header, encoded_path = entry.split(b"\t", 1)
            mode, object_type, _object_id, size = header.decode("ascii").split()
            path = os.fsdecode(encoded_path)
        except (UnicodeDecodeError, ValueError) as error:
            raise ReleaseVerificationError(
                f"Git tree inventory is malformed at {revision}"
            ) from error
        require(object_type == "blob" and size.isdigit(),
                f"Git tree inventory contains a non-regular file: {path}")
        inventory[path] = (mode, int(size))
    return inventory


def protected_path(path: str) -> bool:
    return path == "release-manifest.json" or any(
        path.startswith(prefix) for prefix in PROTECTED_PATH_PREFIXES
    )


def protected_inventory(
    root: Path, revision: str,
) -> dict[str, tuple[str, int]]:
    return {
        path: record for path, record in git_tree_inventory(root, revision).items()
        if protected_path(path)
    }


def require_protected_inventory(
    root: Path, revision: str, expected: dict[str, tuple[str, int]], label: str,
) -> None:
    actual = protected_inventory(root, revision)
    require(actual == expected,
            f"{label} protected path inventory is not exact: {sorted(actual)}")


def expected_pending_facts(previous: bytes, snapshot: dict[str, Any]) -> bytes:
    if previous:
        verify_facts_bytes(previous, "committed fact baseline")
    with tempfile.TemporaryDirectory(prefix="report-facts-") as directory:
        path = Path(directory) / "facts.jsonl"
        if previous:
            path.write_bytes(previous)
        try:
            facts.append_jsonl(path, facts.snapshot_facts(snapshot))
        except facts.FactConflictError as error:
            raise ReleaseVerificationError(f"pending fact append is invalid: {error}") from error
        return path.read_bytes() if path.exists() else b""


def require_newer_snapshot(previous_latest: bytes | None, snapshot: dict[str, Any]) -> None:
    if previous_latest is None:
        return
    previous = strict_json(previous_latest, "committed latest snapshot")
    previous_at = aware_timestamp(previous.get("collected_at"), "committed collected_at")
    current_at = aware_timestamp(snapshot.get("collected_at"), "candidate collected_at")
    require(datetime.fromisoformat(current_at.replace("Z", "+00:00"))
            > datetime.fromisoformat(previous_at.replace("Z", "+00:00")),
            "candidate snapshot is not newer than committed latest")


def verify_pending_update(
    root: Path, snapshot: dict[str, Any], immutable: Path, facts_raw: bytes,
) -> None:
    relative_immutable = immutable.relative_to(root).as_posix()
    require(git_blob(root, "HEAD", relative_immutable) is None,
            "pending immutable snapshot already exists in HEAD")
    previous_facts = git_blob(root, "HEAD", FACTS_PATH.relative_to(ROOT).as_posix()) or b""
    require(facts_raw == expected_pending_facts(previous_facts, snapshot),
            "pending facts are not the exact append over the committed ledger")
    previous_latest = git_blob(root, "HEAD", SNAPSHOT_PATH.relative_to(ROOT).as_posix())
    require_newer_snapshot(previous_latest, snapshot)
    head = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    require(snapshot["provenance"]["source_revision"] == head,
            "collector source revision must equal HEAD before staging")


def verify_public_data(
    root: Path = ROOT, snapshot_path: Path | None = None,
    facts_path: Path | None = None, state_path: Path | None = None,
    *, pending_update: bool = False, now: datetime | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    reference = now if now is not None else datetime.now(timezone.utc)
    require(reference.tzinfo is not None and reference.utcoffset() is not None,
            "verification time must include a UTC offset")
    snapshot_path = (snapshot_path or root / "snapshots" / "latest.json").resolve()
    facts_path = (facts_path or root / "history" / "facts.jsonl").resolve()
    state_path = (state_path or root / "state" / "xstocks-supply.json").resolve()
    require(snapshot_path == root / "snapshots" / "latest.json",
            "public snapshot path must be snapshots/latest.json")
    require(facts_path == root / "history" / "facts.jsonl",
            "public facts path must be history/facts.jsonl")
    require(state_path == root / "state" / "xstocks-supply.json",
            "public state path must be state/xstocks-supply.json")
    snapshot_raw, snapshot, immutable = verify_snapshot(snapshot_path, now=reference)
    snapshot_history = verify_snapshot_history(
        snapshot_path.parent, snapshot_raw, snapshot, immutable,
    )
    facts_raw, fact_rows = verify_facts(facts_path)
    state = verify_state(state_path, snapshot)
    snapshot_at = datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
    for index, fact in enumerate(fact_rows, start=1):
        fact_at = datetime.fromisoformat(
            aware_timestamp(fact.get("collected_at"), f"fact line {index} collected_at")
            .replace("Z", "+00:00")
        )
        require(fact_at <= snapshot_at and fact_at <= reference,
                f"fact line {index} collected_at is after the selected snapshot or verification time")
        event_time = fact.get("event_time")
        if event_time is not None:
            event_at = fact_event_time(event_time, f"fact line {index} event_time")
            require(event_at <= fact_at and event_at <= snapshot_at and event_at <= reference,
                    f"fact line {index} event_time is after collection, the selected snapshot, "
                    "or verification time")
    _scan_public_text(snapshot_raw.decode("utf-8"), "snapshot")
    _scan_public_text(facts_raw.decode("utf-8"), "facts")
    if state is not None:
        _scan_public_text(state[0].decode("utf-8"), "state")
    if pending_update:
        verify_pending_update(root, snapshot, immutable, facts_raw)
    return {
        "snapshot_raw": snapshot_raw,
        "snapshot": snapshot,
        "immutable": immutable,
        "snapshot_history": snapshot_history,
        "history": [record["snapshot"] for record in snapshot_history],
        "facts_raw": facts_raw,
        "fact_rows": fact_rows,
        "state_raw": state[0] if state else None,
        "state": state[1] if state else None,
        "verification_time": reference,
    }


def _scan_public_url(value: str, label: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(html.unescape(value))
        host = parsed.hostname
    except ValueError as error:
        raise ReleaseVerificationError(f"{label} contains a malformed public URL") from error
    require(parsed.username is None and parsed.password is None,
            f"{label} contains a prohibited credential-bearing URL")
    require(isinstance(host, str) and host,
            f"{label} contains a URL without a public host")
    lowered = host.rstrip(".").lower()
    require("%" not in lowered, f"{label} contains a prohibited scoped IP URL")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(lowered))
        except OSError:
            address = None
    local_names = (
        "localhost", "local", "lan", "internal", "home", "home.arpa", "localdomain",
    )
    require(address is not None or (
        "." in lowered
        and lowered not in local_names
        and not any(lowered.endswith(f".{name}") for name in local_names)
    ), f"{label} contains a prohibited local-network URL")
    require(address is None or not (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_unspecified or address.is_reserved
    ), f"{label} contains a prohibited non-public IP URL")
    decoded_parts = tuple(
        _percent_decode(part, label)
        for part in (parsed.path, parsed.query, parsed.fragment)
    )
    for parameters in (parsed.query, parsed.fragment):
        for key, query_value in urllib.parse.parse_qsl(
            _percent_decode(parameters, label).replace(";", "&"),
            keep_blank_values=True,
        ):
            require(not query_value or SENSITIVE_QUERY_KEY.fullmatch(key) is None,
                    f"{label} contains a prohibited credential query parameter")
    _scan_private_text("\n".join(decoded_parts), label)


def _percent_decode(value: str, label: str) -> str:
    decoded = value
    for _ in range(5):
        try:
            candidate = urllib.parse.unquote(decoded, errors="strict")
        except UnicodeDecodeError as error:
            raise ReleaseVerificationError(
                f"{label} contains malformed percent-encoded text"
            ) from error
        if candidate == decoded:
            return decoded
        decoded = candidate
    raise ReleaseVerificationError(
        f"{label} contains excessively nested percent-encoded text"
    )


def _scan_private_text(value: str, label: str) -> None:
    for pattern in PRIVATE_TEXT_PATTERNS:
        require(pattern.search(value) is None,
                f"{label} contains prohibited private or credential-shaped text")


def _scan_public_text(value: str, label: str, *, html_artifact: bool = False) -> None:
    require("\x00" not in value, f"{label} contains a NUL byte")
    scannable = html.unescape(value)
    decoded_scannable = _percent_decode(scannable, label)
    scanned_urls: set[str] = set()
    for candidate in (scannable, decoded_scannable):
        for url in PUBLIC_URL_PATTERN.findall(candidate):
            url = url.rstrip(".,;)]}")
            if url not in scanned_urls:
                scanned_urls.add(url)
                _scan_public_url(url, label)
    _scan_private_text(PUBLIC_URL_PATTERN.sub("", scannable), label)
    _scan_private_text(PUBLIC_URL_PATTERN.sub("", decoded_scannable), label)
    if html_artifact:
        require(EXTERNAL_RUNTIME_PATTERN.search(scannable) is None,
                "HTML contains an external runtime resource")


def verify_observations(
    observations: Any, snapshot: dict[str, Any], reference: datetime,
) -> set[str]:
    require(isinstance(observations, list) and observations,
            "report observations must be a non-empty list")
    selected_at = datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
    identifiers: set[str] = set()
    for index, value in enumerate(observations):
        label = f"report observation {index}"
        record = exact_fields(
            value, render.PUBLIC_OBJECT_FIELDS["observations[]"], label,
        )
        observation_id = record.get("observation_id")
        require(isinstance(observation_id, str)
                and OBSERVATION_ID_PATTERN.fullmatch(observation_id) is not None,
                f"{label} has an invalid observation_id")
        require(observation_id not in identifiers,
                f"{label} duplicates observation_id {observation_id}")
        identifiers.add(observation_id)
        require(record.get("record_kind") in ("direct", "derived"),
                f"{label} has an invalid record_kind")
        for field in (
            "metric_id", "name", "type", "unit", "population", "denominator",
            "window", "collected_at", "snapshot_collected_at", "source", "source_path",
            "collection_method", "calculation_method", "freshness", "status", "basis",
            "quality", "caveat", "output_path",
        ):
            require(isinstance(record.get(field), str) and record[field],
                    f"{label}.{field} must be a non-empty string")
        require(record.get("subject_id") is None or (
            isinstance(record["subject_id"], str) and record["subject_id"]
        ), f"{label}.subject_id must be null or a non-empty string")
        for field in ("collected_at", "snapshot_collected_at"):
            timestamp = datetime.fromisoformat(
                aware_timestamp(record[field], f"{label}.{field}").replace("Z", "+00:00")
            )
            require(timestamp <= selected_at and timestamp <= reference,
                    f"{label}.{field} is after the selected snapshot or verification time")
        if record.get("observed_at") is not None:
            observed_at = datetime.fromisoformat(
                aware_timestamp(record["observed_at"], f"{label}.observed_at")
                .replace("Z", "+00:00")
            )
            require(observed_at <= selected_at and observed_at <= reference,
                    f"{label}.observed_at is after the selected snapshot or verification time")
        require(record.get("observed_slot") is None or (
            isinstance(record["observed_slot"], int)
            and not isinstance(record["observed_slot"], bool)
            and record["observed_slot"] >= 0
        ), f"{label}.observed_slot must be null or a non-negative integer")
        record_type = record["type"]
        value = record.get("value")
        require(record_type in ("numeric", "decimal-string", "boolean", "categorical"),
                f"{label}.type is unsupported")
        if value is not None and record_type == "numeric":
            require(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(float(value)), f"{label}.value must be finite numeric")
        elif value is not None and record_type == "decimal-string":
            try:
                decimal_value = Decimal(value) if isinstance(value, str) else None
            except InvalidOperation:
                decimal_value = None
            require(decimal_value is not None and decimal_value.is_finite(),
                    f"{label}.value must be a finite decimal string")
        elif value is not None and record_type == "boolean":
            require(isinstance(value, bool), f"{label}.value must be boolean")
        elif value is not None and record_type == "categorical":
            require(isinstance(value, str), f"{label}.value must be a string")
        require(record["status"] in facts.VALID_STATES,
                f"{label}.status is unsupported")
        require((record["status"] == "unavailable") == (value is None),
                f"{label}.status and value disagree")
        require(record["basis"] in facts.VALID_BASES | frozenset(("derived",)),
                f"{label}.basis is unsupported")
        source_url = record.get("source_url")
        require(
            source_url is None if record["status"] == "unavailable"
            else isinstance(source_url, str) and source_url.startswith("https://"),
            f"{label}.source_url disagrees with status",
        )
        selector = f"observations[observation_id={json.dumps(observation_id)}]"
        require(record["output_path"].startswith(selector),
                f"{label}.output_path does not identify its observation")
        inputs = record.get("input_observation_ids")
        require(isinstance(inputs, list) and len(inputs) == len(set(inputs))
                and all(isinstance(item, str) and OBSERVATION_ID_PATTERN.fullmatch(item)
                        for item in inputs),
                f"{label}.input_observation_ids are invalid")
        require((record["record_kind"] == "direct" and not inputs)
                or (record["record_kind"] == "derived" and bool(inputs)),
                f"{label}.input_observation_ids disagree with record_kind")
    for index, record in enumerate(observations):
        require(set(record["input_observation_ids"]) <= identifiers,
                f"report observation {index} references an unknown input observation")
        require(record["observation_id"] not in record["input_observation_ids"],
                f"report observation {index} references itself")
    return identifiers


def _reconstruct_report(
    data: dict[str, Any], renderer_state: dict[str, Any], generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str | None]:
    """Rebuild the renderer envelope from validated snapshot history."""
    snapshot = data["snapshot"]
    charted = facts.publication_history(
        render.history_for(snapshot, data["history"]), selected=snapshot,
    )
    release = render.build_release_metadata(
        ROOT / "snapshots" / "latest.json",
        data["snapshot_raw"],
        snapshot,
        generated_at=generated_at,
        renderer_state=renderer_state,
        history=charted,
    )
    artifact_snapshot = render.project_public_envelope({
        **snapshot,
        "release": release,
    })
    analysis = detect.analyse(charted, now=generated_at)
    comparison = pipeline.recheck(charted)
    observations = facts.public_observation_records(snapshot, history=charted)
    observations.extend(render.build_derived_observation_records(charted, observations))
    observations.extend(render.build_anomaly_observation_records(
        charted, observations, analysis, generated_at,
    ))
    indexes = render.public_observation_indexes(observations)
    summary_observation_ids = {
        key: record["observation_id"]
        for key, record in indexes["summary"].items()
    }
    public_analysis = render.bind_public_analysis(analysis, indexes, charted)
    public_comparison = render.bind_public_comparison(comparison, indexes)
    history_payload = render.bind_public_history(
        charts.history_json(charted, observation_ids=summary_observation_ids),
        indexes,
        charted,
    )
    observation_digest = sha256(json.dumps(
        render.json_safe(observations), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))
    artifact_snapshot = render.project_public_envelope({
        **artifact_snapshot,
        "release": {
            **artifact_snapshot["release"],
            "observation_contract_sha256": observation_digest,
        },
    })
    envelope = render.project_public_envelope({
        **artifact_snapshot,
        "anomalies": public_analysis,
        "delta": public_comparison,
        "history": history_payload,
        "upgrades": render.recorded_simd_source(artifact_snapshot),
        "observations": observations,
    })
    omitted = render.public_history_omissions(charted)
    notice = render.public_history_omission_notice(omitted, len(charted))
    return envelope, artifact_snapshot, charted, notice


def verify_artifacts(
    artifacts: Path, data: dict[str, Any], *, canonical_selected_path: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    require(artifacts.is_dir() and not artifacts.is_symlink(),
            f"artifact directory is missing: {artifacts}")
    entries = list(artifacts.iterdir())
    actual = {path.name for path in entries}
    require(actual == ARTIFACT_NAMES,
            f"artifact set mismatch: expected={sorted(ARTIFACT_NAMES)} got={sorted(actual)}")
    require(all(path.is_file() and not path.is_symlink() for path in entries),
            "artifact directory must contain only three regular files")
    artifact_bytes = {name: (artifacts / name).read_bytes() for name in ARTIFACT_NAMES}
    for name, raw in artifact_bytes.items():
        require(bool(raw), f"empty artifact: {name}")
    try:
        html_text = artifact_bytes["index.html"].decode("utf-8")
        markdown_text = artifact_bytes["report.md"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseVerificationError("HTML and Markdown must be UTF-8") from error
    csp = (
        '<meta http-equiv="Content-Security-Policy" '
        f'content="{render.CONTENT_SECURITY_POLICY}">'
    )
    require(html_text.count(csp) == 1, "HTML must contain the exact CSP once")
    csp_at = html_text.index(csp)
    resource_positions = [
        position for marker in ("<link", "<script", "<style", "<img", "<iframe")
        if (position := html_text.find(marker)) >= 0
    ]
    require(not resource_positions or csp_at < min(resource_positions),
            "HTML CSP must precede resource, script, and style elements")
    routes = ("overview", "data", "methods", "history", "project")
    for route in routes:
        require(html_text.count(f"id='{route}'") == 1,
                f"HTML must contain exactly one {route} route anchor")
        require(f"href='#{route}'" in html_text,
                f"HTML navigation omits the {route} route")
    require("data-pulse-track" in html_text
            and "data-pulse-previous" in html_text
            and "data-pulse-next" in html_text,
            "HTML omits the Overview chart carousel")
    require(html_text.count("data:image/png;base64,") == 2,
            "HTML omits pinned Project raster illustrations")
    require(html_text.count("data:image/webp;base64,") == len(render.EDITORIAL_ART_ASSETS),
            "HTML omits pinned Project editorial imagery")
    require("data-content-key='project'" in html_text,
            "HTML omits the Project editorial surface")
    report_raw = artifact_bytes["report.json"]
    report = strict_json(report_raw, "report.json")
    require(isinstance(report, dict), "report.json root must be an object")
    require(report_raw == canonical_json(report), "report.json is not canonical renderer JSON")
    schema_version = report.get("schema_version")
    require(schema_version in render.PUBLIC_ROOT_FIELDS, "report.json schema is unsupported")
    expected_root = frozenset(render.project_public_envelope(data["snapshot"])) | DERIVED_ROOTS
    exact_fields(report, expected_root, "report.json root")
    try:
        require(render.project_public_envelope(report) == report,
                "report.json contains fields outside the recursive public schema")
    except ValueError as error:
        raise ReleaseVerificationError(f"report public projection failed: {error}") from error

    snapshot = data["snapshot"]
    report_base = {key: value for key, value in report.items() if key not in DERIVED_ROOTS}
    require(report_base == render.project_public_envelope(snapshot),
            "report.json source fields do not exactly match snapshots/latest.json")
    release = report.get("release")
    require(isinstance(release, dict), "report release metadata is missing")
    selected = release.get("selected_snapshot")
    collector_state = release.get("collector")
    renderer_state = release.get("renderer")
    require(isinstance(selected, dict) and isinstance(collector_state, dict)
            and isinstance(renderer_state, dict), "release provenance objects are missing")
    digest = sha256(data["snapshot_raw"])
    require(release.get("release_id") == digest and selected.get("sha256") == digest,
            "release ID and selected snapshot SHA must match latest.json")
    if canonical_selected_path:
        require(selected.get("path") == "snapshots/latest.json",
                "selected snapshot path must be snapshots/latest.json")
    require(collector_state == snapshot.get("provenance"),
            "release collector provenance does not match the snapshot")
    require(collector_state.get("source_tree_dirty") is False
            and renderer_state.get("source_tree_dirty") is False,
            "collector and renderer source trees must both be clean")
    require(isinstance(renderer_state.get("source_revision"), str)
            and SHA_PATTERN.fullmatch(renderer_state["source_revision"]) is not None,
            "renderer source revision must be a 40-character Git SHA")
    require(release.get("schema_version") == snapshot.get("schema_version"),
            "release schema does not match the snapshot")
    require(release.get("public_projection_version") == render.PUBLIC_PROJECTION_VERSION,
            "release public projection version is not current")
    generated_at = aware_timestamp(release.get("generated_at"), "release generated_at")
    generated_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    snapshot_time = datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
    require(snapshot_time <= generated_time <= data["verification_time"],
            "release generated_at must be between collection and verification time")
    update = release.get("update_status")
    require(isinstance(update, dict), "release update status is missing")
    require(update.get("as_of") == generated_at,
            "release update status is not frozen to generated_at")
    require(update.get("latest_successful_collection_at") == snapshot.get("collected_at")
            and update.get("included_collection_attempt_at") == snapshot.get("collected_at")
            and update.get("included_collection_attempt_outcome") == "success",
            "release update status does not identify the selected collection")

    observations = report.get("observations")
    known_ids = verify_observations(observations, snapshot, data["verification_time"])
    observation_digest = sha256(json.dumps(
        render.json_safe(observations), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))
    require(release.get("observation_contract_sha256") == observation_digest,
            "release observation contract digest is invalid")
    try:
        render.validate_public_observation_bindings(
            html_text, markdown_text, observations, report.get("history", {}),
            report.get("delta"), report.get("anomalies"),
        )
    except ValueError as error:
        raise ReleaseVerificationError(f"artifact observation bindings failed: {error}") from error

    _scan_public_text(html_text, "HTML", html_artifact=True)
    _scan_public_text(markdown_text, "Markdown")
    _scan_public_text(report_raw.decode("utf-8"), "report.json")

    try:
        expected_report, artifact_snapshot, charted, notice = _reconstruct_report(
            data, renderer_state, generated_at,
        )
        expected_report_raw = canonical_json(render.json_safe(expected_report))
        require(report_raw == expected_report_raw,
                "report.json does not match the deterministic renderer envelope")
        expected_markdown = render.render_markdown(
            artifact_snapshot, expected_report["anomalies"], expected_report["delta"],
            charted, observations=expected_report["observations"],
            history_projection_notice=notice,
        ).encode("utf-8")
        expected_html = render.publish(
            artifact_snapshot, expected_report["anomalies"], expected_report["delta"],
            charted, "Recorded snapshot", observations=expected_report["observations"],
            history_projection_notice=notice,
        ).encode("utf-8")
    except (KeyError, ValueError) as error:
        raise ReleaseVerificationError(
            f"artifacts cannot be deterministically reconstructed: {error}"
        ) from error
    require(artifact_bytes["report.md"] == expected_markdown,
            "Markdown does not match deterministic renderer output")
    require(artifact_bytes["index.html"] == expected_html,
            "HTML does not match deterministic renderer output")

    require(set(OBSERVATION_ID_PATTERN.findall(html_text)).issubset(known_ids)
            and set(OBSERVATION_ID_PATTERN.findall(markdown_text)).issubset(known_ids),
            "human artifacts contain observation IDs absent from report.json")
    for label, value in render.release_metadata_fields(report):
        markdown_row = f"| {label} | {render.markdown_text(value, table=True)} |"
        html_row = (
            f"<dt>{html.escape(label)}</dt><dd><code>{html.escape(value)}</code></dd>"
        )
        require(markdown_row in markdown_text, f"Markdown omits release field {label}")
        require(html_row in html_text, f"HTML omits release field {label}")
    return report, artifact_bytes


def file_record(root: Path, path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": sha256(raw),
        "bytes": len(raw),
    }


def build_manifest(
    root: Path, artifacts: Path, data: dict[str, Any], report: dict[str, Any],
    artifact_bytes: dict[str, bytes],
) -> dict[str, Any]:
    release = report["release"]
    state_record = file_record(root, root / "state" / "xstocks-supply.json") \
        if data["state_raw"] is not None else None
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_revision": release["collector"]["source_revision"],
        "data_revision": release["renderer"]["source_revision"],
        "generated_at": release["generated_at"],
        "public_projection_version": release["public_projection_version"],
        "immutable_snapshot": file_record(root, data["immutable"]),
        "latest_snapshot": file_record(root, root / "snapshots" / "latest.json"),
        "facts": file_record(root, root / "history" / "facts.jsonl"),
        "state": state_record,
        "artifacts": {
            f"{artifacts.resolve().relative_to(root.resolve()).as_posix()}/{name}": {
                "sha256": sha256(raw), "bytes": len(raw),
            }
            for name, raw in sorted(artifact_bytes.items())
        },
    }


def release_data_records(root: Path, data: dict[str, Any]) -> list[dict[str, Any] | None]:
    return [
        file_record(root, data["immutable"]),
        file_record(root, root / "snapshots" / "latest.json"),
        file_record(root, root / "history" / "facts.jsonl"),
        file_record(root, root / "state" / "xstocks-supply.json")
        if data["state_raw"] is not None else None,
    ]


def verify_data_revision(
    root: Path, data: dict[str, Any], source_revision: str, data_revision: str,
) -> None:
    for revision, label in ((source_revision, "source"), (data_revision, "data")):
        require(_git(root, "cat-file", "-e", f"{revision}^{{commit}}", check=False).returncode == 0,
                f"{label} revision is not a local commit")
    require(commit_parents(root, data_revision) == [source_revision],
            "data revision must be the single direct child of the source revision")
    records = release_data_records(root, data)
    required_data_paths = {record["path"] for record in records[:3]}
    allowed_data_paths = {
        record["path"] for record in records if record is not None
    }
    transition = changed_paths(root, source_revision, data_revision)
    require(required_data_paths.issubset(transition) and transition <= allowed_data_paths,
            f"source-to-data path set is invalid: {sorted(transition)}")
    immutable_path = records[0]["path"]
    require(git_blob(root, source_revision, immutable_path) is None,
            "immutable snapshot already exists in the source revision")
    previous_latest = git_blob(root, source_revision, "snapshots/latest.json")
    require_newer_snapshot(previous_latest, data["snapshot"])
    previous_facts = git_blob(root, source_revision, "history/facts.jsonl") or b""
    require(data["facts_raw"] == expected_pending_facts(previous_facts, data["snapshot"]),
            "committed facts are not the exact append over the source ledger")
    for record in records:
        if record is None:
            continue
        blob = git_blob(root, data_revision, record["path"])
        require(blob is not None and sha256(blob) == record["sha256"],
                f"data revision does not contain approved bytes for {record['path']}")
        head_blob = git_blob(root, "HEAD", record["path"])
        require(head_blob is not None and sha256(head_blob) == record["sha256"],
                f"HEAD does not contain approved bytes for {record['path']}")
    state_path = "state/xstocks-supply.json"
    if data["state_raw"] is None:
        require(git_blob(root, data_revision, state_path) is None,
                "data revision contains unapproved optional state")
        require(git_blob(root, "HEAD", state_path) is None,
                "HEAD contains unapproved optional state")


def verify_clean_initial_history(
    root: Path, data: dict[str, Any], report: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    source_revision = report["release"]["collector"]["source_revision"]
    data_revision = report["release"]["renderer"]["source_revision"]
    head = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    require(commit_parents(root, source_revision) == [],
            "source revision must be the root source commit")
    require_protected_inventory(
        root, source_revision, {}, "source root commit",
    )
    verify_data_revision(root, data, source_revision, data_revision)

    data_expected = {
        record["path"]: ("100644", record["bytes"])
        for record in release_data_records(root, data) if record is not None
    }
    require_protected_inventory(root, data_revision, data_expected, "data commit")

    package_paths = {"release-manifest.json", *manifest["artifacts"]}
    package_revision = last_change_revision(root, package_paths)
    require(commit_parents(root, package_revision) == [data_revision],
            "package revision must be the single direct child of the data revision")
    package_transition = changed_paths(root, data_revision, package_revision)
    require(package_transition == package_paths,
            f"data-to-package path set is invalid: {sorted(package_transition)}")
    package_expected = dict(data_expected)
    package_expected["release-manifest.json"] = (
        "100644", len(canonical_json(manifest, sort_keys=True))
    )
    package_expected.update({
        path: ("100644", record["bytes"])
        for path, record in manifest["artifacts"].items()
    })
    require_protected_inventory(root, package_revision, package_expected, "package commit")

    protected_touches = _git(
        root, "rev-list", "--full-history", f"{package_revision}..{head}", "--",
        *PROTECTED_PATH_PREFIXES, "release-manifest.json",
    ).stdout.split()
    require(not protected_touches,
            "protected release paths changed after the package commit")
    require_protected_inventory(root, head, package_expected, "HEAD")


def verify_committed_data(
    root: Path, data: dict[str, Any], base_revision: str,
) -> None:
    require(SHA_PATTERN.fullmatch(base_revision) is not None,
            "trusted event base must be a 40-character Git SHA")
    require(_git(
        root, "cat-file", "-e", f"{base_revision}^{{commit}}", check=False,
    ).returncode == 0, "trusted event base is not a local commit")
    require(_git(
        root, "merge-base", "--is-ancestor", base_revision, "HEAD", check=False,
    ).returncode == 0, "trusted event base must be an ancestor of HEAD")
    source_revision = data["snapshot"]["provenance"]["source_revision"]
    paths = {
        record["path"] for record in release_data_records(root, data)
        if record is not None
    }
    data_revision = last_change_revision(root, paths)
    verify_data_revision(root, data, source_revision, data_revision)
    public_touches = _git(
        root, "rev-list", "--full-history", f"{base_revision}..HEAD", "--",
        "snapshots", "history", "state",
    ).stdout.split()
    if public_touches:
        require(_git(
            root, "merge-base", "--is-ancestor", base_revision,
            source_revision, check=False,
        ).returncode == 0, "trusted event base must be an ancestor of the source revision")
        source_data_touches = _git(
            root, "rev-list", "--full-history", f"{base_revision}..{source_revision}", "--",
            "snapshots", "history", "state",
        ).stdout.split()
        require(not source_data_touches,
                "trusted base-to-source range changes public data")
        later_data_touches = _git(
            root, "rev-list", "--full-history", f"{data_revision}..HEAD", "--",
            "snapshots", "history", "state",
        ).stdout.split()
        require(not later_data_touches,
                "data revision-to-HEAD range changes public data")


def verify_git_revisions(
    root: Path, data: dict[str, Any], report: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> None:
    source_revision = report["release"]["collector"]["source_revision"]
    data_revision = report["release"]["renderer"]["source_revision"]
    head = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    if manifest is None:
        verify_data_revision(root, data, source_revision, data_revision)
        require(data_revision == head, "fresh update renderer revision must equal HEAD")
    else:
        verify_clean_initial_history(root, data, report, manifest)


def verify_manifest(
    root: Path, manifest_path: Path, artifacts: Path, data: dict[str, Any],
    report: dict[str, Any], artifact_bytes: dict[str, bytes], *, check_git: bool,
) -> dict[str, Any]:
    require(manifest_path.resolve() == root.resolve() / "release-manifest.json",
            "release manifest path must be release-manifest.json")
    require(artifacts.resolve() == root.resolve() / "samples",
            "manifest-bound artifacts must be the samples directory")
    require(manifest_path.is_file() and not manifest_path.is_symlink(),
            f"missing regular release manifest: {manifest_path}")
    raw = manifest_path.read_bytes()
    manifest = strict_json(raw, str(manifest_path))
    require(isinstance(manifest, dict), "release manifest root must be an object")
    require(raw == canonical_json(manifest, sort_keys=True),
            "release manifest is not canonical JSON")
    expected = build_manifest(root, artifacts, data, report, artifact_bytes)
    require(manifest == expected, "release manifest does not match the exact package bytes")
    if check_git:
        verify_git_revisions(root, data, report, manifest)
        relative_manifest = manifest_path.resolve().relative_to(root.resolve()).as_posix()
        require(git_blob(root, "HEAD", relative_manifest) == raw,
                "release manifest is not the exact committed HEAD blob")
        for name, value in artifact_bytes.items():
            relative = (artifacts / name).resolve().relative_to(root.resolve()).as_posix()
            require(git_blob(root, "HEAD", relative) == value,
                    f"artifact is not the exact committed HEAD blob: {relative}")
    return manifest


def verify_package(
    root: Path, artifacts: Path, *, manifest_path: Path | None = None,
    check_git: bool = True, now: datetime | None = None,
) -> dict[str, Any]:
    data = verify_public_data(root=root, now=now)
    report, artifact_bytes = verify_artifacts(artifacts, data)
    manifest = None
    if manifest_path is not None:
        manifest = verify_manifest(
            root, manifest_path, artifacts, data, report, artifact_bytes,
            check_git=check_git,
        )
    elif check_git:
        verify_git_revisions(root, data, report, None)
    return {
        "release_id": report["release"]["release_id"],
        "artifact_sha256": {
            name: sha256(raw) for name, raw in sorted(artifact_bytes.items())
        },
        "manifest": manifest,
    }


def write_manifest(
    root: Path, artifacts: Path, output: Path, *, now: datetime | None = None,
) -> dict[str, Any]:
    require(not output.exists(), f"refusing to overwrite release manifest: {output}")
    data = verify_public_data(root=root, now=now)
    report, artifact_bytes = verify_artifacts(artifacts, data)
    verify_git_revisions(root, data, report, None)
    manifest = build_manifest(root, artifacts, data, report, artifact_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=f".{output.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            handle.write(canonical_json(manifest, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one public report release.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    data_parser = subparsers.add_parser("verify-data")
    data_mode = data_parser.add_mutually_exclusive_group()
    data_mode.add_argument("--pending-update", action="store_true")
    data_mode.add_argument("--committed", action="store_true")
    data_parser.add_argument("--base-revision")
    data_parser.add_argument("--now")
    package_parser = subparsers.add_parser("verify-package")
    package_parser.add_argument("--artifacts", type=Path, required=True)
    package_parser.add_argument("--manifest", type=Path)
    package_parser.add_argument("--now")
    manifest_parser = subparsers.add_parser("write-manifest")
    manifest_parser.add_argument("--artifacts", type=Path, required=True)
    manifest_parser.add_argument("--output", type=Path, default=ROOT / "release-manifest.json")
    manifest_parser.add_argument("--now")
    args = parser.parse_args()
    try:
        now = reference_time(args.now) if args.now else None
        if args.command == "verify-data":
            result = verify_public_data(pending_update=args.pending_update, now=now)
            if args.committed:
                require(args.base_revision is not None,
                        "--committed requires --base-revision")
                verify_committed_data(ROOT, result, args.base_revision)
            payload = {
                "verified": True,
                "snapshot_sha256": sha256(result["snapshot_raw"]),
                "fact_count": len(result["fact_rows"]),
                "state_present": result["state"] is not None,
            }
        elif args.command == "verify-package":
            payload = {
                "verified": True,
                **verify_package(
                    ROOT, args.artifacts.resolve(),
                    manifest_path=args.manifest.resolve() if args.manifest else None,
                    now=now,
                ),
            }
        else:
            manifest = write_manifest(
                ROOT, args.artifacts.resolve(), args.output.resolve(), now=now,
            )
            payload = {"written": str(args.output), "manifest": manifest}
    except (OSError, ReleaseVerificationError, subprocess.CalledProcessError) as error:
        print(json.dumps({"verified": False, "error": str(error)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
