#!/usr/bin/env python3
"""Named evidence pipeline the Methods page cites.

These names are not documentation. `collect.collect` runs Sources → Normalize →
Validate. `render.main` runs Recheck then Publish. A failed source stays
independently unavailable. Missing values are never rewritten as zero.
`check_publishable` is the pre-publish gate: a candidate that fails it must not
be rendered, committed, or deployed.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import delta as delta_module
import detect
import blocks
import facts as facts_module
import growth as growth_module
import news as news_module

STAGES = ("Sources", "Normalize", "Validate", "Publish", "Recheck")


def editorial_url_matches_source(source_id: Any, value: Any) -> bool:
    """Fail closed when an editorial link leaves its adopted source boundary."""
    if not isinstance(source_id, str) or not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or port not in {None, 443} or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    if source_id == "agave_releases":
        return host == "github.com" and parsed.path.startswith("/anza-xyz/agave/releases/")
    if source_id == "firedancer_releases":
        return host == "github.com" and parsed.path.startswith("/firedancer-io/firedancer/releases/")
    if source_id == "x_announcements":
        if host != "x.com":
            return False
        import re as _re
        return _re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/[0-9]+", parsed.path) is not None
    if source_id == "network_status":
        return host in {"status.solana.com", "stspg.io"}
    return False

EVIDENCE_LABELS = {
    "Measured": "direct record",
    "Sampled": "bounded evidence",
    "Unavailable": "never zero",
}

# Methods cards cite these callables. Keep the strings identical to the functions
# that actually run.
STAGE_CALLS = {
    "Sources": "collect.sources",
    "Normalize": "collect.normalize",
    "Validate": "pipeline.validate",
    "Publish": "render.publish",
    "Recheck": "pipeline.recheck",
}

# Pre-publish gate contract. Publication needs the full current section
# structure, so support starts at the schema that introduced the last required
# section (5 added native inflation). collect.SCHEMA_VERSION is 9 and
# additive-only; importing collect here would be circular (collect imports
# pipeline), so keep this range in sync with it.
SUPPORTED_SCHEMA_VERSIONS = frozenset(range(5, 10))
REQUIRED_SECTIONS = (
    "source", "network", "epoch", "performance", "supply", "inflation", "validators",
)
OPTIONAL_SECTIONS = ("economics", "activity", "news", "growth")
CORE_EVIDENCE_SECTIONS = ("epoch", "performance", "supply", "inflation", "validators")
FUTURE_TOLERANCE_SECONDS = 300
DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "latest.json"
DEX_VOLUME_URL = "https://api.dexscreener.com/tokens/v1/solana/"
DEX_VOLUME_SCOPE = "Solana DEX pools indexed by DEX Screener"
DEX_VOLUME_EXCLUSIONS = [
    "RFQ fills", "centralized venues", "unindexed or unsupported pools",
]
DEX_VOLUME_BATCH_SIZE = 30


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sanitize(value: Any) -> Any:
    """Non-finite numbers become unavailable. None stays None — never zero."""
    if _is_number(value):
        return value if math.isfinite(float(value)) else None
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    return value


def validate(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Stage 3. Check values, gaps, and labels.

    Missing stays unavailable. Sampled evidence is not relabelled. A trend still
    needs two usable observations — that check lives in charts.py and is not
    invented here.
    """
    if not isinstance(snapshot, dict):
        raise TypeError("validate expects a snapshot dict")
    cleaned = _sanitize(snapshot)
    cleaned["pipeline"] = {
        "stages": list(STAGES),
        "stage_calls": dict(STAGE_CALLS),
        "evidence_labels": dict(EVIDENCE_LABELS),
    }
    return cleaned


def recheck(
    history_or_previous: list[dict[str, Any]] | dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 5. Compare A/B with declared thresholds. No interpolation."""
    if current is None:
        return delta_module.analyse(history_or_previous)  # type: ignore[arg-type]
    return delta_module.compare(history_or_previous, current)  # type: ignore[arg-type]


def _collect_nonfinite_paths(value: Any, path: str, found: list[str]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        found.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_nonfinite_paths(item, f"{path}.{key}", found)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_nonfinite_paths(item, f"{path}[{index}]", found)


def _parse_aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _timestamp_seconds(value: Any) -> float | None:
    """Normalize a Solana unix block time or an offset-aware ISO timestamp."""
    if _is_number(value) and math.isfinite(float(value)):
        return float(value)
    parsed = _parse_aware_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _validate_selected_usd_stablecoins(
    growth: dict[str, Any], collected_at: datetime | None,
    canonical_rpc_endpoint: Any, canonical_rpc_identity: Any, fail: Any,
) -> None:
    """Validate the optional four-mint finalized-supply contract."""
    sources = growth.get("sources")
    has_summary = "selected_usd_stablecoins" in growth
    has_source = (
        isinstance(sources, dict) and "selected_usd_stablecoins" in sources
    )
    if not has_summary and not has_source:
        return

    path = "growth.selected_usd_stablecoins"
    summary = growth.get("selected_usd_stablecoins")
    source = sources.get("selected_usd_stablecoins") if has_source else None
    if not isinstance(summary, dict):
        fail(path, "must be an object when present")
        if has_source:
            fail("growth.sources.selected_usd_stablecoins", "has no matching summary")
        return
    if not isinstance(source, dict):
        fail("growth.sources.selected_usd_stablecoins", "must mirror the summary")

    exact_fields = {
        "metric_id": facts_module.SELECTED_STABLECOIN_METRIC_ID,
        "unit": facts_module.SELECTED_STABLECOIN_UNIT,
        "basis": facts_module.SELECTED_STABLECOIN_SUMMARY_BASIS,
        "universe_coverage": "unknown",
        "limitations": facts_module.SELECTED_STABLECOIN_LIMITATIONS,
    }
    for field, expected in exact_fields.items():
        if summary.get(field) != expected:
            fail(f"{path}.{field}", f"must be {expected!r}")

    registry = facts_module.selected_stablecoin_registry_source()
    if summary.get("registry_source") != registry:
        fail(f"{path}.registry_source", "must equal the pinned four-mint registry")

    assets = summary.get("assets")
    if not isinstance(assets, list):
        fail(f"{path}.assets", "must be a list")
        assets = []
    if len(assets) != len(facts_module.SELECTED_STABLECOIN_IDENTITIES):
        fail(
            f"{path}.assets",
            f"must contain exactly {len(facts_module.SELECTED_STABLECOIN_IDENTITIES)} assets",
        )

    observed: list[tuple[dict[str, Any], Decimal, int, tuple[float, str]]] = []
    unavailable_evidence = {
        "total_supply_decimal", "raw_amount", "decimals", "rpc_ui_amount_string",
        "rpc_context_slot", "rpc_api_version", "event_time", "collected_at",
        "basis", "account_provenance", "share_of_selected_total",
    }
    for index, identity in enumerate(facts_module.SELECTED_STABLECOIN_IDENTITIES):
        asset_path = f"{path}.assets[{index}]"
        if index >= len(assets) or not isinstance(assets[index], dict):
            if index < len(assets):
                fail(asset_path, "must be an object")
            continue
        asset = assets[index]
        if (asset.get("symbol"), asset.get("issuer"), asset.get("mint")) != identity:
            fail(asset_path, "must match the pinned symbol, issuer, and mint identity")
        available = asset.get("available")
        if not isinstance(available, bool):
            fail(f"{asset_path}.available", "must be boolean")
            continue
        if not available:
            if asset.get("reason") != "Finalized validated mint supply is unavailable.":
                fail(f"{asset_path}.reason", "must explain unavailable finalized supply")
            if unavailable_evidence & asset.keys():
                fail(asset_path, "unavailable assets cannot retain measured supply evidence")
            continue

        if "reason" in asset:
            fail(f"{asset_path}.reason", "must be absent for an available asset")
        raw = asset.get("raw_amount")
        decimals = asset.get("decimals")
        exact: Decimal | None = None
        if (not isinstance(raw, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw) is None
                or int(raw) > facts_module.SPL_TOKEN_U64_MAX):
            fail(f"{asset_path}.raw_amount", "must be a canonical unsigned 64-bit decimal string")
        elif (not isinstance(decimals, int) or isinstance(decimals, bool)
              or not 0 <= decimals <= 255):
            fail(f"{asset_path}.decimals", "must be an unsigned 8-bit integer")
        else:
            try:
                exact = Decimal(raw).scaleb(-decimals)
            except InvalidOperation:
                exact = None
            expected_total = format(exact, "f") if exact is not None else None
            if exact is None or not exact.is_finite() or exact < 0:
                fail(f"{asset_path}.total_supply_decimal", "cannot be reconstructed")
            elif asset.get("total_supply_decimal") != expected_total:
                fail(
                    f"{asset_path}.total_supply_decimal",
                    "must exactly reconstruct raw_amount at decimals",
                )

        ui_amount = asset.get("rpc_ui_amount_string")
        try:
            ui_decimal = Decimal(ui_amount) if isinstance(ui_amount, str) else None
        except InvalidOperation:
            ui_decimal = None
        if ui_decimal is None or not ui_decimal.is_finite() or ui_decimal < 0:
            fail(f"{asset_path}.rpc_ui_amount_string", "must be a finite non-negative decimal string")
        elif exact is not None and ui_decimal != exact:
            fail(
                f"{asset_path}.rpc_ui_amount_string",
                "must equal the exactly reconstructed token supply",
            )

        slot = asset.get("rpc_context_slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
            fail(f"{asset_path}.rpc_context_slot", "must be a non-negative integer")
            slot = None
        version = asset.get("rpc_api_version")
        if not isinstance(version, str) or not version:
            fail(f"{asset_path}.rpc_api_version", "must be a non-empty string")
        if "event_time" not in asset or asset.get("event_time") is not None:
            fail(f"{asset_path}.event_time", "must be explicit null because RPC exposes no event time")
        if asset.get("basis") != facts_module.SELECTED_STABLECOIN_SUMMARY_BASIS:
            fail(f"{asset_path}.basis", "must describe finalized on-chain total token supply")

        observed_at_text = asset.get("collected_at")
        observed_at = _parse_aware_timestamp(observed_at_text)
        if observed_at is None:
            fail(f"{asset_path}.collected_at", "must be offset-aware ISO-8601")
        elif collected_at is not None and observed_at > collected_at:
            fail(f"{asset_path}.collected_at", "cannot be after snapshot collected_at")

        provenance = asset.get("account_provenance")
        if not isinstance(provenance, dict):
            fail(f"{asset_path}.account_provenance", "must be an object")
        else:
            if provenance.get("source_method") != "getAccountInfo(finalized,jsonParsed)":
                fail(
                    f"{asset_path}.account_provenance.source_method",
                    "must be getAccountInfo(finalized,jsonParsed)",
                )
            program_id = provenance.get("program_id")
            expected_program = facts_module.SELECTED_STABLECOIN_TOKEN_PROGRAMS.get(program_id)
            if expected_program is None or provenance.get("program") != expected_program:
                fail(
                    f"{asset_path}.account_provenance.program",
                    "must match the validated SPL token program owner",
                )
            account_slot = provenance.get("rpc_context_slot")
            if (not isinstance(account_slot, int) or isinstance(account_slot, bool)
                    or account_slot < 0):
                fail(
                    f"{asset_path}.account_provenance.rpc_context_slot",
                    "must be a non-negative integer",
                )
            elif slot is not None and account_slot > slot:
                fail(
                    f"{asset_path}.account_provenance.rpc_context_slot",
                    "cannot exceed the subsequent supply context slot",
                )
            account_version = provenance.get("rpc_api_version")
            if account_version is not None and (
                not isinstance(account_version, str) or not account_version
            ):
                fail(
                    f"{asset_path}.account_provenance.rpc_api_version",
                    "must be a non-empty string when present",
                )

        if exact is not None and slot is not None and observed_at is not None:
            observed.append((asset, exact, slot, (observed_at.timestamp(), observed_at_text)))

    numerator = summary.get("coverage_numerator")
    denominator = summary.get("coverage_denominator")
    actual_numerator = len(observed)
    if (not isinstance(numerator, int) or isinstance(numerator, bool)
            or not 0 <= numerator <= len(facts_module.SELECTED_STABLECOIN_IDENTITIES)):
        fail(f"{path}.coverage_numerator", "must be an integer from 0 through 4")
    elif numerator != actual_numerator:
        fail(f"{path}.coverage_numerator", f"must equal {actual_numerator}")
    if denominator != len(facts_module.SELECTED_STABLECOIN_IDENTITIES):
        fail(f"{path}.coverage_denominator", "must be 4")
    if summary.get("coverage_label") != f"{actual_numerator}/4":
        fail(f"{path}.coverage_label", f"must be {actual_numerator}/4")

    expected_available = actual_numerator == 4
    expected_state = "current" if expected_available else (
        "partial" if actual_numerator else "unavailable"
    )
    if summary.get("available") is not expected_available:
        fail(f"{path}.available", f"must be {expected_available!r}")
    if summary.get("state") != expected_state:
        fail(f"{path}.state", f"must be {expected_state!r}")

    slots = [row[2] for row in observed]
    expected_slot_range = {
        "first": min(slots) if slots else None,
        "last": max(slots) if slots else None,
    }
    if summary.get("slot_range") != expected_slot_range:
        fail(f"{path}.slot_range", f"must be {expected_slot_range!r}")
    times = [row[3] for row in observed]
    expected_oldest = min(times)[1] if times else None
    expected_newest = max(times)[1] if times else None
    if summary.get("oldest_observation_at") != expected_oldest:
        fail(f"{path}.oldest_observation_at", f"must be {expected_oldest!r}")
    if summary.get("newest_observation_at") != expected_newest:
        fail(f"{path}.newest_observation_at", f"must be {expected_newest!r}")

    queried = summary.get("queried_asset_count")
    failed = summary.get("failed_asset_count")
    if (not isinstance(queried, int) or isinstance(queried, bool) or not 0 <= queried <= 4):
        fail(f"{path}.queried_asset_count", "must be an integer from 0 through 4")
    elif actual_numerator > queried:
        fail(f"{path}.queried_asset_count", "cannot be less than observed coverage")
    if (not isinstance(failed, int) or isinstance(failed, bool) or failed < 0):
        fail(f"{path}.failed_asset_count", "must be a non-negative integer")
    elif isinstance(queried, int) and not isinstance(queried, bool):
        if failed != queried - actual_numerator:
            fail(f"{path}.failed_asset_count", "must equal queried minus observed")
    deadline_exhausted = summary.get("deadline_exhausted")
    if not isinstance(deadline_exhausted, bool):
        fail(f"{path}.deadline_exhausted", "must be boolean")
    elif isinstance(queried, int) and not isinstance(queried, bool):
        if deadline_exhausted is not (queried < 4):
            fail(f"{path}.deadline_exhausted", "must reflect whether all four queries were attempted")

    total = sum((row[1] for row in observed), Decimal(0))
    if expected_available:
        expected_total = format(total, "f")
        if summary.get("selected_total_supply_decimal") != expected_total:
            fail(f"{path}.selected_total_supply_decimal", f"must be {expected_total!r}")
        for index, (asset, amount, _, _) in enumerate(observed):
            expected_share = format(amount / total, "f") if total else None
            if expected_share is None:
                if "share_of_selected_total" in asset:
                    fail(f"{path}.assets[{index}].share_of_selected_total", "must be absent when total is zero")
            elif asset.get("share_of_selected_total") != expected_share:
                fail(f"{path}.assets[{index}].share_of_selected_total", f"must be {expected_share!r}")
    else:
        if "selected_total_supply_decimal" in summary:
            fail(f"{path}.selected_total_supply_decimal", "requires complete 4/4 coverage")
        for index, asset in enumerate(assets):
            if isinstance(asset, dict) and "share_of_selected_total" in asset:
                fail(f"{path}.assets[{index}].share_of_selected_total", "requires complete 4/4 coverage")

    rpc = summary.get("rpc")
    if not isinstance(rpc, dict):
        fail(f"{path}.rpc", "must be an object")
    else:
        endpoint = rpc.get("endpoint")
        allowed_labels = (
            growth_module.PUBLIC_RPC_ENDPOINTS
            | {growth_module.CUSTOM_RPC_ENDPOINT_LABEL}
        )
        if endpoint not in allowed_labels:
            fail(f"{path}.rpc.endpoint", "must be a sanitized RPC endpoint label")
        elif canonical_rpc_endpoint is not None and endpoint != canonical_rpc_endpoint:
            fail(f"{path}.rpc.endpoint", "must match source.endpoint")
        endpoint_identity = rpc.get("endpoint_identity")
        if endpoint_identity != canonical_rpc_identity:
            fail(f"{path}.rpc.endpoint_identity", "must match source.endpoint_identity")
        if rpc.get("methods") != ["getAccountInfo", "getTokenSupply"]:
            fail(f"{path}.rpc.methods", "must list getAccountInfo and getTokenSupply")
        if rpc.get("commitment") != "finalized":
            fail(f"{path}.rpc.commitment", "must be finalized")

    if isinstance(source, dict):
        expected_source = {
            "available": expected_available,
            "partial": expected_state == "partial",
            "coverage_complete": expected_available,
            "state": expected_state,
            "coverage_numerator": actual_numerator,
            "coverage_denominator": 4,
            "registry_source": summary.get("registry_source"),
            "rpc": summary.get("rpc"),
        }
        for field, expected in expected_source.items():
            if source.get(field) != expected:
                fail(
                    f"growth.sources.selected_usd_stablecoins.{field}",
                    f"must mirror {path}.{field}",
                )


def _validate_schema8_dex_volume(
    growth: dict[str, Any], equities: dict[str, Any], fail: Any,
) -> None:
    """Validate the bounded DEXS-01 derived aggregate and source mirror."""
    path = "growth.tokenized_equities.volume"
    volume = equities.get("volume")
    sources = growth.get("sources")
    source = sources.get("dex_volume") if isinstance(sources, dict) else None
    if not isinstance(volume, dict):
        fail(path, "is required and must be an object")
        volume = None
    if not isinstance(source, dict):
        fail("growth.sources.dex_volume", "is required and must be an object")
        source = None
    if volume is None or source is None:
        return

    for child, child_path in ((volume, path), (source, "growth.sources.dex_volume")):
        for key, value in child.items():
            if key == "top_pairs" or (key != "exclusions" and isinstance(value, list)):
                fail(f"{child_path}.{key}", "per-pair or row lists are not publishable")

    def required_int(key: str) -> int | None:
        value = volume.get(key)
        if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            fail(f"{path}.{key}", "must be a non-negative integer")
            return None
        return value

    def required_bool(obj: dict[str, Any], key: str, child_path: str) -> bool | None:
        value = obj.get(key)
        if not isinstance(value, bool):
            fail(f"{child_path}.{key}", "must be a boolean")
            return None
        return value

    def finite_nonnegative(key: str) -> float | None:
        value = volume.get(key)
        if not _is_number(value) or value < 0:
            fail(f"{path}.{key}", "must be an explicit finite non-negative number")
            return None
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            number = math.inf
        if not math.isfinite(number):
            fail(f"{path}.{key}", "must be an explicit finite non-negative number")
            return None
        return number

    count_keys = (
        "batches_expected", "batches_requested", "batches_succeeded",
        "source_row_count", "pair_count", "exact_duplicate_row_count",
        "conflicting_pair_count", "invalid_row_count", "unrelated_row_count",
        "volume_covered_pair_count", "volume_invalid_pair_count",
        "liquidity_covered_pair_count", "liquidity_invalid_pair_count",
        "pairs_with_volume", "assets_with_pairs", "registry_asset_count",
    )
    counts = {key: required_int(key) for key in count_keys}
    expected = counts["batches_expected"]
    requested = counts["batches_requested"]
    succeeded = counts["batches_succeeded"]
    rows = counts["source_row_count"]
    pairs = counts["pair_count"]
    duplicates = counts["exact_duplicate_row_count"]
    conflicts = counts["conflicting_pair_count"]
    invalid = counts["invalid_row_count"]
    unrelated = counts["unrelated_row_count"]
    volume_covered = counts["volume_covered_pair_count"]
    volume_invalid = counts["volume_invalid_pair_count"]
    liquidity_covered = counts["liquidity_covered_pair_count"]
    liquidity_invalid = counts["liquidity_invalid_pair_count"]
    pairs_with_volume = counts["pairs_with_volume"]
    assets = counts["assets_with_pairs"]
    registry = counts["registry_asset_count"]

    if None not in (requested, expected) and requested > expected:
        fail(f"{path}.batches_requested", "cannot exceed batches_expected")
    if None not in (succeeded, requested) and succeeded > requested:
        fail(f"{path}.batches_succeeded", "cannot exceed batches_requested")
    for key, value in (
        ("pair_count", pairs),
        ("exact_duplicate_row_count", duplicates),
        ("conflicting_pair_count", conflicts),
        ("invalid_row_count", invalid),
    ):
        if value is not None and rows is not None and value > rows:
            fail(f"{path}.{key}", "cannot exceed source_row_count")
    if None not in (rows, pairs, duplicates, invalid):
        if rows < pairs + duplicates + invalid:
            fail(
                f"{path}.source_row_count",
                "must cover retained pairs, exact duplicates, and invalid rows",
            )
    if None not in (rows, conflicts) and conflicts > rows // 2:
        fail(
            f"{path}.conflicting_pair_count",
            "cannot exceed half of source_row_count",
        )
    if None not in (unrelated, invalid) and unrelated > invalid:
        fail(f"{path}.unrelated_row_count", "cannot exceed invalid_row_count")
    if None not in (volume_covered, pairs) and volume_covered > pairs:
        fail(f"{path}.volume_covered_pair_count", "cannot exceed pair_count")
    if None not in (volume_covered, volume_invalid, pairs):
        if volume_covered + volume_invalid != pairs:
            fail(path, "volume covered and invalid pair counts must partition pair_count")
    if pairs_with_volume is not None and volume_covered is not None:
        if pairs_with_volume != volume_covered:
            fail(f"{path}.pairs_with_volume", "must equal volume_covered_pair_count")
    if None not in (liquidity_covered, pairs) and liquidity_covered > pairs:
        fail(f"{path}.liquidity_covered_pair_count", "cannot exceed pair_count")
    if None not in (liquidity_covered, liquidity_invalid, pairs):
        if liquidity_covered + liquidity_invalid != pairs:
            fail(path, "liquidity covered and invalid pair counts must partition pair_count")
    if assets is not None and registry is not None and assets > registry:
        fail(f"{path}.assets_with_pairs", "cannot exceed registry_asset_count")
    if assets is not None and pairs is not None and assets > 2 * pairs:
        fail(f"{path}.assets_with_pairs", "cannot exceed two registry assets per pair")

    eligible = equities.get("eligible_asset_count")
    if registry is not None and registry != eligible:
        fail(f"{path}.registry_asset_count", "must equal eligible_asset_count")
    if registry is not None and expected is not None:
        expected_batches = (
            (registry + DEX_VOLUME_BATCH_SIZE - 1) // DEX_VOLUME_BATCH_SIZE
            if registry else 0
        )
        if expected != expected_batches:
            fail(f"{path}.batches_expected", f"must be {expected_batches}")

    available = required_bool(volume, "available", path)
    partial = required_bool(volume, "partial", path)
    transport = required_bool(volume, "transport_complete", path)
    required_bool(source, "available", "growth.sources.dex_volume")
    required_bool(source, "partial", "growth.sources.dex_volume")
    required_bool(
        source, "transport_complete", "growth.sources.dex_volume",
    )
    if None not in (expected, requested, succeeded, transport):
        expected_transport = bool(
            expected > 0 and requested == expected and succeeded == expected
        )
        if transport is not expected_transport:
            fail(f"{path}.transport_complete", f"must be {expected_transport!r}")
    if transport is not None:
        expected_market = "partial" if transport else "not established"
        if volume.get("market_coverage") != expected_market:
            fail(f"{path}.market_coverage", f"must be {expected_market!r}")
    else:
        expected_market = None

    if volume.get("scope") != DEX_VOLUME_SCOPE:
        fail(f"{path}.scope", f"must be {DEX_VOLUME_SCOPE!r}")
    if volume.get("exclusions") != DEX_VOLUME_EXCLUSIONS:
        fail(f"{path}.exclusions", "must retain the exact economic exclusions")
    if volume.get("source_url") != DEX_VOLUME_URL:
        fail(f"{path}.source_url", f"must be {DEX_VOLUME_URL!r}")
    limitations = volume.get("limitations")
    if not isinstance(limitations, str) or not limitations.strip():
        fail(f"{path}.limitations", "must be a non-empty string")

    if None not in (
        partial, transport, conflicts, invalid, volume_covered, liquidity_covered, pairs,
    ):
        expected_partial = bool(
            not transport or conflicts or invalid
            or volume_covered != pairs or liquidity_covered != pairs
            or volume.get("market_coverage") != "complete"
        )
        if partial is not expected_partial:
            fail(f"{path}.partial", f"must be {expected_partial!r}")

    volume_present = "volume_24h_usd" in volume
    volume_value = finite_nonnegative("volume_24h_usd") if volume_present else None
    expected_available = None
    if None not in (conflicts, pairs, volume_covered, volume_invalid):
        expected_available = bool(
            conflicts == 0 and pairs > 0 and volume_covered == pairs
            and volume_invalid == 0 and volume_value is not None
        )
    if available is not None and expected_available is not None:
        if available is not expected_available:
            fail(f"{path}.available", f"must be {expected_available!r}")
    if available is False:
        if volume_present:
            fail(f"{path}.volume_24h_usd", "must be absent while volume is unavailable")
        reason = volume.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            fail(f"{path}.reason", "must explain why volume is unavailable")
    elif available is True and not volume_present:
        fail(f"{path}.volume_24h_usd", "is required while volume is available")

    liquidity_present = "liquidity_usd" in volume
    if liquidity_present:
        finite_nonnegative("liquidity_usd")
    expected_liquidity = None
    if None not in (expected, conflicts, pairs, liquidity_covered, liquidity_invalid):
        expected_liquidity = bool(
            expected > 0 and conflicts == 0 and pairs > 0
            and liquidity_covered == pairs and liquidity_invalid == 0
        )
    if expected_liquidity is not None and liquidity_present is not expected_liquidity:
        state = "present" if expected_liquidity else "absent"
        fail(f"{path}.liquidity_usd", f"must be {state} for its explicit coverage")

    mirror = {
        "url": volume.get("source_url"),
        "available": available,
        "partial": partial,
        "scope": volume.get("scope"),
        "transport_complete": transport,
        "market_coverage": volume.get("market_coverage"),
        "exclusions": volume.get("exclusions"),
    }
    for key, value in mirror.items():
        if source.get(key) != value:
            fail(f"growth.sources.dex_volume.{key}", f"must mirror {path}")


def _validate_schema8_xstocks(
    snapshot: dict[str, Any], growth: dict[str, Any], equities: dict[str, Any],
    collected_at: datetime | None, fail: Any,
) -> None:
    """Validate pinned-registry xStock evidence at its actual per-mint grain."""
    path = "growth.tokenized_equities"

    def required_int(obj: dict[str, Any], key: str, child_path: str) -> int | None:
        value = obj.get(key)
        if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            fail(f"{child_path}.{key}", "must be a non-negative integer")
            return None
        return value

    count_keys = (
        "registry_asset_count", "eligible_asset_count", "supply_observed_asset_count",
        "fresh_supply_asset_count", "stale_supply_asset_count",
        "supply_queried_this_run_asset_count", "supply_successful_this_run_asset_count",
        "supply_failed_this_run_asset_count", "valued_asset_count",
        "displayed_asset_count", "display_asset_limit",
    )
    counts = {key: required_int(equities, key, path) for key in count_keys}
    registry_count = counts["registry_asset_count"]
    eligible_count = counts["eligible_asset_count"]
    queried_count = counts["supply_queried_this_run_asset_count"]
    successful_count = counts["supply_successful_this_run_asset_count"]
    failed_count = counts["supply_failed_this_run_asset_count"]

    observed_at = equities.get("observed_at_unix")
    if (not isinstance(observed_at, int) or isinstance(observed_at, bool)
            or observed_at < 0):
        fail(f"{path}.observed_at_unix", "must be a non-negative unix timestamp")
        observed_at = None
    elif collected_at is not None and observed_at > int(collected_at.timestamp()):
        fail(f"{path}.observed_at_unix", "cannot be after snapshot collected_at")
    if equities.get("supply_fresh_max_age_seconds") != facts_module.XSTOCK_FRESH_SECONDS:
        fail(f"{path}.supply_fresh_max_age_seconds", "must be 21600")
    if counts["display_asset_limit"] != facts_module.XSTOCK_DISPLAY_LIMIT:
        fail(f"{path}.display_asset_limit", "must be 12")
    for key in equities:
        lowered = key.lower()
        if "supply" in lowered and ("aggregate" in lowered or "total" in lowered):
            fail(f"{path}.{key}", "heterogeneous xStock supplies must not be aggregated")

    all_assets = equities.get("all_assets")
    displayed_assets = equities.get("assets")
    if not isinstance(all_assets, list):
        fail(f"{path}.all_assets", "must be a list")
        all_assets = []
    if not isinstance(displayed_assets, list):
        fail(f"{path}.assets", "must be a list")
        displayed_assets = []

    mints: set[str] = set()
    slugs: set[str] = set()
    validated_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    observed_rows: list[dict[str, Any]] = []
    unavailable_none_fields = (
        "supply", "supply_raw_amount", "supply_decimals", "supply_rpc_ui_amount",
        "supply_rpc_ui_amount_string", "supply_context_slot", "supply_rpc_api_version",
        "supply_collected_at", "supply_age_seconds",
    )
    for index, asset in enumerate(all_assets):
        asset_path = f"{path}.all_assets[{index}]"
        if not isinstance(asset, dict):
            fail(asset_path, "must be an object")
            continue
        mint = asset.get("mint")
        slug = asset.get("slug")
        if (not isinstance(mint, str) or not mint
                or not isinstance(slug, str) or not slug.startswith("xstock-")
                or not isinstance(asset.get("name"), str) or not asset["name"].strip()
                or asset.get("symbol") is not None):
            fail(asset_path, "must retain the pinned xStock identity fields")
        else:
            if mint in mints:
                fail(f"{path}.all_assets", "contains duplicate mint identities")
            if slug in slugs:
                fail(f"{path}.all_assets", "contains duplicate slug identities")
            mints.add(mint)
            slugs.add(slug)

        if asset.get("supply") is not None:
            observed_rows.append(asset)
            validated = facts_module.validate_xstock_supply_asset(asset, observed_at)
            if validated is None:
                fail(asset_path, "does not meet the finalized xStock supply provenance contract")
            else:
                validated_rows.append((asset, validated))
            continue

        if any(asset.get(key) is not None for key in unavailable_none_fields):
            fail(asset_path, "unavailable rows cannot retain partial supply evidence")
        if (asset.get("supply_freshness") != "unavailable"
                or asset.get("supply_fresh_max_age_seconds")
                != facts_module.XSTOCK_FRESH_SECONDS
                or asset.get("supply_unit") != "token"
                or asset.get("supply_source_id") != "solana_getTokenSupply"
                or asset.get("supply_source_method") != "getTokenSupply(finalized)"
                or asset.get("basis") != "finalized on-chain token supply"
                or "supply_multiplier_provenance" in asset
                or "supply_account_provenance" in asset):
            fail(asset_path, "must be an explicit unavailable finalized-supply row")

    expected_displayed = min(len(all_assets), facts_module.XSTOCK_DISPLAY_LIMIT)
    if registry_count is not None and registry_count != len(all_assets):
        fail(f"{path}.registry_asset_count", "must equal len(all_assets)")
    if eligible_count is not None and registry_count is not None and eligible_count != registry_count:
        fail(f"{path}.eligible_asset_count", "must equal the unique pinned registry count")
    if counts["displayed_asset_count"] != len(displayed_assets):
        fail(f"{path}.displayed_asset_count", "must equal len(assets)")
    if counts["displayed_asset_count"] != expected_displayed:
        fail(f"{path}.displayed_asset_count", f"must be {expected_displayed}")
    if displayed_assets != all_assets[:facts_module.XSTOCK_DISPLAY_LIMIT]:
        fail(f"{path}.assets", "must be the exact leading display slice of all_assets")
    displayed_mints = [
        asset.get("mint") for asset in displayed_assets if isinstance(asset, dict)
    ]
    if len(displayed_mints) != len(set(displayed_mints)):
        fail(f"{path}.assets", "contains duplicate displayed mint identities")

    actual_observed = len(observed_rows)
    actual_fresh = sum(
        asset.get("supply_freshness") == "fresh" for asset in observed_rows
    )
    actual_stale = sum(
        asset.get("supply_freshness") == "stale" for asset in observed_rows
    )
    if counts["supply_observed_asset_count"] != actual_observed:
        fail(f"{path}.supply_observed_asset_count", f"must equal {actual_observed}")
    if counts["fresh_supply_asset_count"] != actual_fresh:
        fail(f"{path}.fresh_supply_asset_count", f"must equal {actual_fresh}")
    if counts["stale_supply_asset_count"] != actual_stale:
        fail(f"{path}.stale_supply_asset_count", f"must equal {actual_stale}")
    if actual_fresh + actual_stale != actual_observed:
        fail(path, "fresh and stale supply rows must partition observed rows")
    expected_available = actual_observed > 0
    if equities.get("available") is not expected_available:
        fail(f"{path}.available", f"must be {expected_available!r}")
    if None not in (queried_count, eligible_count) and queried_count > eligible_count:
        fail(f"{path}.supply_queried_this_run_asset_count", "cannot exceed eligible assets")
    if None not in (successful_count, queried_count) and successful_count > queried_count:
        fail(f"{path}.supply_successful_this_run_asset_count", "cannot exceed queried assets")
    if None not in (queried_count, successful_count, failed_count):
        if successful_count + failed_count != queried_count:
            fail(path, "supply successes plus failures must equal queried assets")
    if counts["valued_asset_count"] != 0:
        fail(f"{path}.valued_asset_count", "must be zero while valuation is unavailable")
    sources = growth.get("sources")
    supply_source = sources.get("supply") if isinstance(sources, dict) else None
    deadline_exhausted = equities.get("supply_deadline_exhausted")
    if not isinstance(deadline_exhausted, bool):
        fail(f"{path}.supply_deadline_exhausted", "must be boolean")
    elif None not in (queried_count, eligible_count):
        has_state_error = bool(
            isinstance(supply_source, dict) and "state_error" in supply_source
        )
        state_error = supply_source.get("state_error") if has_state_error else None
        if has_state_error and (
            not isinstance(state_error, str) or not state_error.strip()
        ):
            fail(
                "growth.sources.supply.state_error",
                "must be a non-empty string when present",
            )
        expected_deadline = bool(
            not has_state_error and queried_count < eligible_count
        )
        if deadline_exhausted is not expected_deadline:
            fail(
                f"{path}.supply_deadline_exhausted",
                f"must be {expected_deadline!r} from the bounded query pass",
            )

    valuation = equities.get("valuation")
    if (not isinstance(valuation, dict) or valuation.get("available") is not False
            or valuation.get("scope") != "unavailable"
            or not isinstance(valuation.get("reason"), str) or not valuation["reason"]):
        fail(f"{path}.valuation", "must explicitly retain unavailable valuation evidence")

    coverage = equities.get("supply_coverage")
    if not isinstance(coverage, dict):
        fail(f"{path}.supply_coverage", "is required and must be an object")
        coverage = {}
    coverage_path = f"{path}.supply_coverage"
    coverage_count_keys = (
        "registry_asset_count", "eligible_asset_count", "queried_this_run_asset_count",
        "successful_this_run_asset_count", "failed_this_run_asset_count",
        "observed_asset_count", "fresh_asset_count", "valued_asset_count",
        "coverage_numerator", "coverage_denominator", "fresh_max_age_seconds",
        "sweep_max_age_seconds",
    )
    coverage_counts = {
        key: required_int(coverage, key, coverage_path) for key in coverage_count_keys
    }
    expected_coverage_counts = {
        "registry_asset_count": registry_count,
        "eligible_asset_count": eligible_count,
        "queried_this_run_asset_count": queried_count,
        "successful_this_run_asset_count": successful_count,
        "failed_this_run_asset_count": failed_count,
        "observed_asset_count": actual_observed,
        "fresh_asset_count": actual_fresh,
        "valued_asset_count": 0,
        "coverage_denominator": eligible_count,
    }
    for key, expected in expected_coverage_counts.items():
        if expected is not None and coverage_counts[key] != expected:
            fail(f"{coverage_path}.{key}", f"must be {expected}")
    if coverage_counts["fresh_max_age_seconds"] != facts_module.XSTOCK_FRESH_SECONDS:
        fail(f"{coverage_path}.fresh_max_age_seconds", "must be 21600")
    if coverage_counts["sweep_max_age_seconds"] != facts_module.XSTOCK_SWEEP_SECONDS:
        fail(f"{coverage_path}.sweep_max_age_seconds", "must be 259200")
    if coverage.get("attempt_scope") != "current collection run":
        fail(f"{coverage_path}.attempt_scope", "must identify the current collection run")
    if coverage.get("coverage_basis") != facts_module.XSTOCK_COVERAGE_BASIS:
        fail(f"{coverage_path}.coverage_basis", "must retain the exact 72-hour basis")

    valid_times = sorted(
        (row[1]["collected_unix"], row[1]["collected_at"])
        for row in validated_rows
    )
    expected_numerator = sum(
        row[1]["age"] <= facts_module.XSTOCK_SWEEP_SECONDS
        for row in validated_rows
    )
    if coverage_counts["coverage_numerator"] != expected_numerator:
        fail(f"{coverage_path}.coverage_numerator", f"must be {expected_numerator}")
    expected_oldest = valid_times[0][1] if valid_times else None
    expected_newest = valid_times[-1][1] if valid_times else None
    expected_span = valid_times[-1][0] - valid_times[0][0] if valid_times else None
    if coverage.get("oldest_observation_at") != expected_oldest:
        fail(f"{coverage_path}.oldest_observation_at", f"must be {expected_oldest!r}")
    if coverage.get("newest_observation_at") != expected_newest:
        fail(f"{coverage_path}.newest_observation_at", f"must be {expected_newest!r}")
    if coverage.get("observation_span_seconds") != expected_span:
        fail(f"{coverage_path}.observation_span_seconds", f"must be {expected_span!r}")

    registry_complete = coverage.get("registry_complete")
    if not isinstance(registry_complete, bool):
        fail(f"{coverage_path}.registry_complete", "must be boolean")
    elif registry_complete:
        if registry_count != facts_module.XSTOCK_REGISTRY_EXPECTED_COUNT:
            fail(coverage_path, "complete registry must contain exactly 107 identities")
    elif registry_count != 0:
        fail(coverage_path, "failed exact registry validation cannot publish partial identities")
    expected_sweep = bool(
        registry_complete is True and eligible_count
        and expected_numerator == eligible_count
        and expected_span is not None
        and expected_span <= facts_module.XSTOCK_SWEEP_SECONDS
    )
    if coverage.get("sweep_complete") is not expected_sweep:
        fail(f"{coverage_path}.sweep_complete", f"must be {expected_sweep!r}")
    expected_scope = "registry-wide" if expected_sweep else "observed subset"
    if coverage.get("scope") != expected_scope:
        fail(f"{coverage_path}.scope", f"must be {expected_scope!r}")

    registry_source = sources.get("registry") if isinstance(sources, dict) else None
    fixed_registry = facts_module.xstock_registry_source()
    if not isinstance(registry_source, dict):
        fail("growth.sources.registry", "is required and must be an object")
    else:
        for key, expected in fixed_registry.items():
            if registry_source.get(key) != expected:
                fail(f"growth.sources.registry.{key}", "must match the pinned registry")
        expected_registry_source = {
            "available": bool(registry_count),
            "coverage_complete": registry_complete,
            "asset_count": registry_count,
            "reason": None if registry_complete is True else (
                "Pinned token registry transport or exact xStock identity contract failed."
            ),
        }
        for key, expected in expected_registry_source.items():
            if registry_source.get(key) != expected:
                fail(f"growth.sources.registry.{key}", f"must be {expected!r}")

    if not isinstance(supply_source, dict):
        fail("growth.sources.supply", "is required and must be an object")
    else:
        source = snapshot.get("source")
        canonical_endpoint = source.get("endpoint") if isinstance(source, dict) else None
        canonical_identity = (
            source.get("endpoint_identity") if isinstance(source, dict) else None
        )
        expected_supply_source = {
            "method": "getTokenSupply",
            "endpoint": canonical_endpoint,
            "rpc_endpoint_identity": canonical_identity,
            "available": expected_available,
            "commitment": "finalized",
            "state_version": growth_module.SUPPLY_STATE_VERSION,
            "deadline_exhausted": deadline_exhausted,
            **coverage,
        }
        for key, expected in expected_supply_source.items():
            if supply_source.get(key) != expected:
                fail(f"growth.sources.supply.{key}", f"must mirror {path} evidence")

    _validate_schema8_dex_volume(growth, equities, fail)

    selected_stablecoins = growth.get("selected_usd_stablecoins")
    selected_coverage = (
        selected_stablecoins.get("coverage_numerator")
        if isinstance(selected_stablecoins, dict) else None
    )
    selected_has_evidence = (
        isinstance(selected_coverage, int)
        and not isinstance(selected_coverage, bool)
        and selected_coverage > 0
    )
    child_evidence = bool(
        registry_count
        or expected_available
        or (isinstance(equities.get("volume"), dict)
            and equities["volume"].get("available") is True)
        or (isinstance(equities.get("proof_of_reserves"), dict)
            and equities["proof_of_reserves"].get("available") is True)
        or selected_has_evidence
        or any(
            isinstance(growth.get(key), dict)
            and growth[key].get("history_available") is True
            for key in ("daily_active_addresses", "daily_fee_payers")
        )
    )
    if growth.get("available") is not child_evidence:
        fail("growth.available", f"must be {child_evidence!r} from child evidence")

def semantic_failures(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Return nested semantic contradictions that must fail publication.

    Optional unavailable sections remain valid. When a field is present, its
    type, range, and relationships must be truthful; an available section does
    not get to publish internally impossible evidence.
    """
    failures: list[dict[str, str]] = []

    def fail(path: str, detail: str) -> None:
        failures.append({"check": "semantic", "detail": f"{path}: {detail}"})

    def number(
        obj: dict[str, Any], key: str, path: str, *, minimum: float | None = None,
        maximum: float | None = None, integer: bool = False,
    ) -> int | float | None:
        if key not in obj:
            return None
        value = obj[key]
        valid = (
            isinstance(value, int) and not isinstance(value, bool)
            if integer else _is_number(value)
        )
        if not valid or not math.isfinite(float(value)):
            fail(f"{path}.{key}", "must be a finite " + ("integer" if integer else "number"))
            return None
        if minimum is not None and value < minimum:
            fail(f"{path}.{key}", f"must be >= {minimum}")
        if maximum is not None and value > maximum:
            fail(f"{path}.{key}", f"must be <= {maximum}")
        return value

    def required_number(
        obj: dict[str, Any], key: str, path: str, *, minimum: float | None = None,
        maximum: float | None = None, integer: bool = False,
    ) -> int | float | None:
        if key not in obj:
            fail(f"{path}.{key}", "is required")
            return None
        return number(
            obj, key, path, minimum=minimum, maximum=maximum, integer=integer,
        )

    def unique_records(items: Any, path: str, keys: tuple[str, ...]) -> None:
        if items is None:
            return
        if not isinstance(items, list):
            fail(path, "must be a list")
            return
        for key in keys:
            values: list[str] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    fail(f"{path}[{index}]", "must be an object")
                    continue
                value = item.get(key)
                if not isinstance(value, str) or not value:
                    fail(f"{path}[{index}].{key}", "must be a non-empty string")
                else:
                    values.append(value)
            if len(values) != len(set(values)):
                fail(path, f"contains duplicate {key} values")

    collected_at = _parse_aware_timestamp(snapshot.get("collected_at"))
    schema_version = snapshot.get("schema_version")
    schema_version = (
        schema_version
        if isinstance(schema_version, int) and not isinstance(schema_version, bool)
        else 0
    )

    if schema_version >= 8:
        provenance = snapshot.get("provenance")
        if not isinstance(provenance, dict):
            fail("provenance", "must be an object")
        else:
            revision = provenance.get("source_revision")
            if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
                fail("provenance.source_revision", "must be a full lowercase Git SHA")
            if not isinstance(provenance.get("source_tree_dirty"), bool):
                fail("provenance.source_tree_dirty", "must be boolean")
        source = snapshot.get("source")
        if not isinstance(source, dict):
            fail("source", "must be an object")
        else:
            endpoint = source.get("endpoint")
            endpoint_identity = source.get("endpoint_identity")
            allowed_labels = (
                growth_module.PUBLIC_RPC_ENDPOINTS
                | {growth_module.CUSTOM_RPC_ENDPOINT_LABEL}
            )
            if endpoint not in allowed_labels:
                fail("source.endpoint", "must be a sanitized RPC endpoint label")
            if (not isinstance(endpoint_identity, str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", endpoint_identity) is None):
                fail("source.endpoint_identity", "must be an opaque SHA-256 identity")
            elif endpoint in growth_module.PUBLIC_RPC_ENDPOINTS:
                expected_identity = growth_module.rpc_endpoint_identity(endpoint)
                if endpoint_identity != expected_identity:
                    fail(
                        "source.endpoint_identity",
                        "must match the named public RPC endpoint",
                    )
            expected_key_state = (
                False if endpoint in growth_module.PUBLIC_RPC_ENDPOINTS else None
            )
            if source.get("requires_api_key") is not expected_key_state:
                fail(
                    "source.requires_api_key",
                    f"must be {expected_key_state!r} for the endpoint label",
                )

    for section_name in REQUIRED_SECTIONS + OPTIONAL_SECTIONS:
        section = snapshot.get(section_name)
        if not isinstance(section, dict):
            continue
        if "available" in section and not isinstance(section["available"], bool):
            fail(f"{section_name}.available", "must be boolean")
        if "stale" in section and not isinstance(section["stale"], bool):
            fail(f"{section_name}.stale", "must be boolean")
        if section.get("available") is True:
            if section.get("source_state") == "fresh" and section.get("stale") is True:
                fail(section_name, "fresh source_state cannot also be stale")
            if section.get("source_state") == "last_known_good" and section.get("stale") is not True:
                fail(section_name, "last_known_good must be marked stale")
        last_success = section.get("last_success_at")
        if last_success is not None:
            parsed = _parse_aware_timestamp(last_success)
            if parsed is None:
                fail(f"{section_name}.last_success_at", "must be offset-aware ISO-8601")
            elif collected_at is not None and parsed > collected_at:
                fail(f"{section_name}.last_success_at", "cannot be after collected_at")

    epoch = snapshot.get("epoch")
    if isinstance(epoch, dict) and epoch.get("available") is True:
        number(epoch, "epoch", "epoch", minimum=0, integer=True)
        slot_index = number(epoch, "slot_index", "epoch", minimum=0, integer=True)
        slots_in_epoch = number(epoch, "slots_in_epoch", "epoch", minimum=1, integer=True)
        progress = number(epoch, "progress_pct", "epoch", minimum=0, maximum=100)
        remaining = number(epoch, "remaining_slots", "epoch", minimum=0, integer=True)
        if slot_index is not None and slots_in_epoch is not None:
            if slot_index > slots_in_epoch:
                fail("epoch.slot_index", "cannot exceed slots_in_epoch")
            if remaining is not None and remaining != slots_in_epoch - slot_index:
                fail("epoch.remaining_slots", "must equal slots_in_epoch - slot_index")
            expected = 100 * slot_index / slots_in_epoch
            if progress is not None and not math.isclose(progress, expected, abs_tol=0.02):
                fail("epoch.progress_pct", "does not match slot_index / slots_in_epoch")
        estimated_end = epoch.get("estimated_end_at")
        if estimated_end is not None:
            parsed = _parse_aware_timestamp(estimated_end)
            if parsed is None:
                fail("epoch.estimated_end_at", "must be offset-aware ISO-8601")
            elif collected_at is not None and remaining and parsed < collected_at:
                fail("epoch.estimated_end_at", "cannot precede collected_at while slots remain")

    supply = snapshot.get("supply")
    if isinstance(supply, dict) and supply.get("available") is True:
        total = number(supply, "total_sol", "supply", minimum=0)
        circulating = number(supply, "circulating_sol", "supply", minimum=0)
        non_circulating = number(supply, "non_circulating_sol", "supply", minimum=0)
        circulating_pct = number(supply, "circulating_pct", "supply", minimum=0, maximum=100)
        if total is not None and circulating is not None and circulating > total:
            fail("supply.circulating_sol", "cannot exceed total_sol")
        if total is not None and non_circulating is not None and non_circulating > total:
            fail("supply.non_circulating_sol", "cannot exceed total_sol")
        if None not in (total, circulating, non_circulating) and not math.isclose(
            total, circulating + non_circulating, abs_tol=0.02,
        ):
            fail("supply", "circulating_sol + non_circulating_sol must equal total_sol")
        if total and circulating is not None and circulating_pct is not None and not math.isclose(
            circulating_pct, 100 * circulating / total, abs_tol=0.02,
        ):
            fail("supply.circulating_pct", "does not match circulating_sol / total_sol")

    performance = snapshot.get("performance")
    if isinstance(performance, dict) and performance.get("available") is True:
        samples = performance.get("samples")
        if samples is not None and not isinstance(samples, list):
            fail("performance.samples", "must be a list")
            samples = None
        samples_used = number(performance, "samples_used", "performance", minimum=0, integer=True)
        period = number(performance, "sample_period_seconds", "performance", minimum=0, integer=True)
        if isinstance(samples, list):
            if samples_used is not None and samples_used != len(samples):
                fail("performance.samples_used", "must equal len(samples)")
            slots: list[int] = []
            summed_period = 0
            for index, sample in enumerate(samples):
                path = f"performance.samples[{index}]"
                if not isinstance(sample, dict):
                    fail(path, "must be an object")
                    continue
                slot = number(sample, "slot", path, minimum=0, integer=True)
                transactions = number(sample, "transactions", path, minimum=0, integer=True)
                non_vote = number(sample, "non_vote_transactions", path, minimum=0, integer=True)
                seconds = number(sample, "sample_period_secs", path, minimum=1, integer=True)
                number(sample, "tps", path, minimum=0)
                number(sample, "non_vote_tps", path, minimum=0)
                number(sample, "vote_share_pct", path, minimum=0, maximum=100)
                number(sample, "slot_time_secs", path, minimum=0)
                if slot is not None:
                    slots.append(slot)
                if transactions is not None and non_vote is not None and non_vote > transactions:
                    fail(f"{path}.non_vote_transactions", "cannot exceed transactions")
                if seconds is not None:
                    summed_period += seconds
            if len(slots) != len(set(slots)):
                fail("performance.samples", "contains duplicate slots")
            if len(slots) > 2:
                ascending = all(a < b for a, b in zip(slots, slots[1:]))
                descending = all(a > b for a, b in zip(slots, slots[1:]))
                if not ascending and not descending:
                    fail("performance.samples", "slots must be strictly ordered")
            if period is not None and period != summed_period:
                fail("performance.sample_period_seconds", "must equal the sum of sample periods")

    validators = snapshot.get("validators")
    if isinstance(validators, dict) and validators.get("available") is True:
        counts = {
            key: number(validators, key, "validators", minimum=0, integer=True)
            for key in (
                "active_count", "delinquent_count", "accounts_with_stake",
                "accounts_missing_stake", "all_validator_count",
                "ranked_validator_count", "ranked_validator_limit",
            )
        }
        all_validators = validators.get("all_validators")
        ranked = validators.get("ranked_validators")
        unique_records(all_validators, "validators.all_validators", ("identity", "vote_account"))
        unique_records(ranked, "validators.ranked_validators", ("identity", "vote_account"))
        if isinstance(all_validators, list) and counts["all_validator_count"] is not None:
            if counts["all_validator_count"] != len(all_validators):
                fail("validators.all_validator_count", "must equal len(all_validators)")
        if isinstance(ranked, list) and counts["ranked_validator_count"] is not None:
            if counts["ranked_validator_count"] != len(ranked):
                fail("validators.ranked_validator_count", "must equal len(ranked_validators)")
        if counts["active_count"] is not None and counts["delinquent_count"] is not None:
            total_accounts = counts["active_count"] + counts["delinquent_count"]
            if counts["all_validator_count"] is not None and total_accounts != counts["all_validator_count"]:
                fail("validators", "active_count + delinquent_count must equal all_validator_count")
        if None not in (counts["ranked_validator_count"], counts["ranked_validator_limit"]):
            if counts["ranked_validator_count"] > counts["ranked_validator_limit"]:
                fail("validators.ranked_validator_count", "cannot exceed ranked_validator_limit")

        production = validators.get("block_production")
        if isinstance(production, dict) and production.get("available") is True:
            path = "validators.block_production"
            production_counts = {
                key: required_number(production, key, path, minimum=0, integer=True)
                for key in (
                    "epoch", "first_slot", "last_slot", "context_slot", "leader_slots",
                    "blocks_produced", "skipped_slots",
                )
            }
            first_slot = production_counts["first_slot"]
            last_slot = production_counts["last_slot"]
            context_slot = production_counts["context_slot"]
            leader_slots = production_counts["leader_slots"]
            blocks_produced = production_counts["blocks_produced"]
            skipped_slots = production_counts["skipped_slots"]
            skip_rate = required_number(
                production, "skip_rate", path, minimum=0, maximum=1,
            )
            if first_slot is not None and last_slot is not None:
                if first_slot > last_slot:
                    fail(path, "first_slot cannot exceed last_slot")
                elif leader_slots is not None and leader_slots != last_slot - first_slot + 1:
                    fail(f"{path}.leader_slots", "must equal the inclusive slot range")
                if context_slot is not None and context_slot < last_slot:
                    fail(f"{path}.context_slot", "cannot precede last_slot")
            if None not in (blocks_produced, skipped_slots, leader_slots):
                if blocks_produced + skipped_slots != leader_slots:
                    fail(path, "blocks_produced + skipped_slots must equal leader_slots")
                elif leader_slots and skip_rate is not None and not math.isclose(
                    skip_rate, skipped_slots / leader_slots, abs_tol=1e-8,
                ):
                    fail(f"{path}.skip_rate", "must equal skipped_slots / leader_slots")

            observed = _parse_aware_timestamp(production.get("vote_enrichment_observed_at"))
            if observed is None:
                fail(f"{path}.vote_enrichment_observed_at", "must be offset-aware ISO-8601")
            elif collected_at is not None and observed > collected_at:
                fail(f"{path}.vote_enrichment_observed_at", "cannot be after collected_at")
            source = production.get("source")
            if not isinstance(source, dict) or source.get("method") != "getBlockProduction":
                fail(f"{path}.source.method", "must be getBlockProduction")
            elif source.get("commitment") != "finalized":
                fail(f"{path}.source.commitment", "must be finalized")

            if schema_version >= 8:
                collection = production.get("collection")
                if not isinstance(collection, dict):
                    fail(f"{path}.collection", "must be an object")
                else:
                    collection_counts = {
                        key: required_number(collection, key, f"{path}.collection",
                                             minimum=0, integer=True)
                        for key in (
                            "request_count", "chunk_slot_limit", "first_slot", "last_slot",
                            "coverage_numerator_slots", "coverage_denominator_slots",
                            "context_slot_min", "context_slot_max",
                        )
                    }
                    requests = collection_counts["request_count"]
                    chunk_limit = collection_counts["chunk_slot_limit"]
                    context_min = collection_counts["context_slot_min"]
                    context_max = collection_counts["context_slot_max"]
                    if collection.get("mode") != "contiguous_chunks":
                        fail(f"{path}.collection.mode", "must be contiguous_chunks")
                    if collection.get("coverage_complete") is not True:
                        fail(f"{path}.collection.coverage_complete", "must be true")
                    if None not in (
                        requests, chunk_limit, first_slot, last_slot, leader_slots,
                        collection_counts["first_slot"], collection_counts["last_slot"],
                        collection_counts["coverage_numerator_slots"],
                        collection_counts["coverage_denominator_slots"], context_slot,
                        context_min, context_max,
                    ):
                        expected_requests = (leader_slots + chunk_limit - 1) // chunk_limit \
                            if chunk_limit else None
                        if (requests < 1 or chunk_limit < 1
                                or chunk_limit > blocks.BLOCK_PRODUCTION_CHUNK_SLOTS
                                or requests != expected_requests
                                or collection_counts["first_slot"] != first_slot
                                or collection_counts["last_slot"] != last_slot
                                or collection_counts["coverage_numerator_slots"] != leader_slots
                                or collection_counts["coverage_denominator_slots"] != leader_slots
                                or context_min != context_slot or context_max < context_min):
                            fail(f"{path}.collection", "must cover the exact completed epoch")

            rows = production.get("validators")
            unique_records(rows, f"{path}.validators", ("identity",))
            row_leader_slots = row_blocks_produced = row_skipped_slots = 0
            seen_vote_accounts: set[str] = set()
            if isinstance(rows, list):
                for index, row in enumerate(rows):
                    row_path = f"{path}.validators[{index}]"
                    if not isinstance(row, dict):
                        continue
                    row_counts = {
                        key: required_number(row, key, row_path, minimum=0, integer=True)
                        for key in (
                            "leader_slots", "blocks_produced", "skipped_slots",
                            "vote_account_count",
                        )
                    }
                    row_leaders = row_counts["leader_slots"]
                    row_blocks = row_counts["blocks_produced"]
                    row_skips = row_counts["skipped_slots"]
                    row_rate = required_number(
                        row, "skip_rate", row_path, minimum=0, maximum=1,
                    )
                    if None not in (row_blocks, row_skips, row_leaders):
                        if row_leaders < 1 or row_blocks + row_skips != row_leaders:
                            fail(row_path, "blocks_produced + skipped_slots must equal positive leader_slots")
                        elif row_rate is not None and not math.isclose(
                            row_rate, row_skips / row_leaders, abs_tol=1e-8,
                        ):
                            fail(f"{row_path}.skip_rate", "must equal skipped_slots / leader_slots")
                        row_leader_slots += row_leaders
                        row_blocks_produced += row_blocks
                        row_skipped_slots += row_skips

                    votes = row.get("vote_accounts")
                    if not isinstance(votes, list):
                        fail(f"{row_path}.vote_accounts", "must be a list")
                        continue
                    if (row_counts["vote_account_count"] is not None
                            and row_counts["vote_account_count"] != len(votes)):
                        fail(f"{row_path}.vote_account_count", "must equal len(vote_accounts)")
                    matched = row.get("vote_identity_matched")
                    if not isinstance(matched, bool) or matched != bool(votes):
                        fail(f"{row_path}.vote_identity_matched", "must match vote-account coverage")
                    stake_total = 0
                    for vote_index, vote in enumerate(votes):
                        vote_path = f"{row_path}.vote_accounts[{vote_index}]"
                        if not isinstance(vote, dict):
                            fail(vote_path, "must be an object")
                            continue
                        pubkey = vote.get("vote_pubkey")
                        if not isinstance(pubkey, str) or not pubkey:
                            fail(f"{vote_path}.vote_pubkey", "must be a non-empty string")
                        elif pubkey in seen_vote_accounts:
                            fail(f"{path}.validators", "contains duplicate vote_pubkey values")
                        else:
                            seen_vote_accounts.add(pubkey)
                        if vote.get("state") not in ("current", "delinquent"):
                            fail(f"{vote_path}.state", "must be current or delinquent")
                        stake = required_number(
                            vote, "activated_stake_lamports", vote_path,
                            minimum=0, integer=True,
                        )
                        if stake is not None:
                            stake_total += stake
                    row_stake = row.get("activated_stake_lamports")
                    if votes:
                        if (not isinstance(row_stake, int) or isinstance(row_stake, bool)
                                or row_stake != stake_total):
                            fail(f"{row_path}.activated_stake_lamports", "must equal vote-account stake")
                    elif row_stake is not None:
                        fail(f"{row_path}.activated_stake_lamports", "must be null without a vote account")
            if None not in (leader_slots, blocks_produced, skipped_slots) and (
                row_leader_slots != leader_slots
                or row_blocks_produced != blocks_produced
                or row_skipped_slots != skipped_slots
            ):
                fail(path, "validator rows must reconcile to production totals")

    economics = snapshot.get("economics")
    if schema_version >= 8 and isinstance(economics, dict):
        stablecoins = economics.get("stablecoins")
        if isinstance(stablecoins, dict):
            path = "economics.stablecoins"
            if "stablecoin_usd" in stablecoins:
                fail(f"{path}.stablecoin_usd", "is not a schema-8 metric")
            if stablecoins.get("available") is True:
                required_number(
                    stablecoins, "usd_pegged_circulating_usd", path, minimum=0,
                )
                if stablecoins.get("metric") != "USD-pegged circulating supply":
                    fail(
                        f"{path}.metric",
                        "must be 'USD-pegged circulating supply'",
                    )
                for key in ("provider_field", "scope"):
                    value = stablecoins.get(key)
                    if not isinstance(value, str) or not value.strip():
                        fail(f"{path}.{key}", "must be a non-empty string")

        protocols = economics.get("protocols")
        if isinstance(protocols, dict) and protocols.get("available") is True:
            path = "economics.protocols"
            rows = protocols.get("protocols")
            eligible = required_number(
                protocols, "eligible_protocol_count", path, minimum=0, integer=True,
            )
            required_number(
                protocols, "excluded_child_protocol_count", path, minimum=0, integer=True,
            )
            if not isinstance(rows, list) or not rows:
                fail(f"{path}.protocols", "must be a non-empty ranked list")
            else:
                if eligible is not None and eligible < len(rows):
                    fail(
                        f"{path}.eligible_protocol_count",
                        "cannot be less than len(protocols)",
                    )
                row_ids: set[str] = set()
                row_facts: list[tuple[str | None, str | None, str | None]] = []
                ranked_values: list[float | None] = []
                for index, row in enumerate(rows):
                    row_path = f"{path}.protocols[{index}]"
                    if not isinstance(row, dict):
                        fail(row_path, "must be an object")
                        continue
                    protocol_id = row.get("provider_protocol_id")
                    family_id = row.get("provider_family_id")
                    basis = row.get("ranking_basis")
                    for key, value in (
                        ("provider_protocol_id", protocol_id),
                        ("provider_family_id", family_id),
                    ):
                        if not isinstance(value, str) or not value:
                            fail(f"{row_path}.{key}", "must be a non-empty string")
                    if isinstance(protocol_id, str) and protocol_id:
                        if protocol_id in row_ids:
                            fail(f"{path}.protocols", "contains duplicate provider_protocol_id values")
                        row_ids.add(protocol_id)
                    if basis not in (
                        "provider_parent_aggregate", "provider_child", "provider_standalone",
                    ):
                        fail(f"{row_path}.ranking_basis", "is not a supported provider basis")
                    elif isinstance(protocol_id, str) and isinstance(family_id, str):
                        if basis == "provider_child" and protocol_id == family_id:
                            fail(f"{row_path}.ranking_basis", "child ID must differ from family ID")
                        if basis != "provider_child" and protocol_id != family_id:
                            fail(f"{row_path}.ranking_basis", "aggregate/standalone ID must equal family ID")
                    tvl = required_number(row, "solana_tvl_usd", row_path, minimum=0)
                    ranked_values.append(float(tvl) if tvl is not None else None)
                    row_facts.append((
                        protocol_id if isinstance(protocol_id, str) else None,
                        family_id if isinstance(family_id, str) else None,
                        basis if isinstance(basis, str) else None,
                    ))
                if any(
                    previous is not None and current is not None and previous < current
                    for previous, current in zip(ranked_values, ranked_values[1:])
                ):
                    fail(f"{path}.protocols", "must be ordered by descending solana_tvl_usd")
                for protocol_id, family_id, basis in row_facts:
                    if (basis == "provider_child" and family_id in row_ids
                            and protocol_id != family_id):
                        fail(
                            f"{path}.protocols",
                            f"child {protocol_id!r} duplicates present family {family_id!r}",
                        )

    activity = snapshot.get("activity")
    if isinstance(activity, dict) and activity.get("available") is True:
        if schema_version >= 8:
            activity_source = activity.get("source")
            snapshot_source = snapshot.get("source")
            expected_activity_source = {
                "endpoint": (
                    snapshot_source.get("endpoint")
                    if isinstance(snapshot_source, dict) else None
                ),
                "endpoint_identity": (
                    snapshot_source.get("endpoint_identity")
                    if isinstance(snapshot_source, dict) else None
                ),
                "method": "getBlock (transactionDetails=accounts)",
            }
            if not isinstance(activity_source, dict):
                fail("activity.source", "must be an object")
            else:
                for key, expected in expected_activity_source.items():
                    if activity_source.get(key) != expected:
                        fail(f"activity.source.{key}", f"must be {expected!r}")
            expected_key_state = (
                snapshot_source.get("requires_api_key")
                if isinstance(snapshot_source, dict) else None
            )
            if activity.get("requires_api_key") is not expected_key_state:
                fail(
                    "activity.requires_api_key",
                    f"must be {expected_key_state!r} for the endpoint label",
                )
        window = activity.get("window")
        if isinstance(window, dict):
            first_slot = number(window, "first_slot", "activity.window", minimum=0, integer=True)
            last_slot = number(window, "last_slot", "activity.window", minimum=0, integer=True)
            requested = number(window, "blocks_requested", "activity.window", minimum=0, integer=True)
            sampled = number(window, "blocks_sampled", "activity.window", minimum=0, integer=True)
            observed_seconds = number(window, "observed_seconds", "activity.window", minimum=0)
            if first_slot is not None and last_slot is not None and first_slot > last_slot:
                fail("activity.window", "first_slot cannot exceed last_slot")
            if requested is not None and sampled is not None and sampled > requested:
                fail("activity.window.blocks_sampled", "cannot exceed blocks_requested")
            first_time = _timestamp_seconds(window.get("first_block_time"))
            last_time = _timestamp_seconds(window.get("last_block_time"))
            if "first_block_time" in window and first_time is None:
                fail("activity.window.first_block_time", "must be unix time or offset-aware ISO-8601")
            if "last_block_time" in window and last_time is None:
                fail("activity.window.last_block_time", "must be unix time or offset-aware ISO-8601")
            if first_time is not None and last_time is not None:
                if first_time > last_time:
                    fail("activity.window", "first_block_time cannot exceed last_block_time")
                elif observed_seconds is not None and not math.isclose(
                    observed_seconds, last_time - first_time, abs_tol=1,
                ):
                    fail("activity.window.observed_seconds", "must match block-time bounds")
        rev = activity.get("rev")
        if schema_version >= 8 and isinstance(rev, dict) and rev.get("available") is True:
            path = "activity.rev"
            sampled_sol = rev.get("sampled_sol")
            if not isinstance(sampled_sol, dict):
                fail(f"{path}.sampled_sol", "must be an object")
            else:
                components = {
                    key: required_number(sampled_sol, key, f"{path}.sampled_sol", minimum=0)
                    for key in (
                        "transaction_fees", "message_signature_base_fee_lower_bound",
                        "unclassified_fee_residual", "jito_tips", "total",
                    )
                }
                fees = components["transaction_fees"]
                lower_bound = components["message_signature_base_fee_lower_bound"]
                residual = components["unclassified_fee_residual"]
                tips = components["jito_tips"]
                total = components["total"]
                if None not in (fees, lower_bound, residual) and not math.isclose(
                    fees, lower_bound + residual, abs_tol=1e-9,
                ):
                    fail(f"{path}.sampled_sol", "fee lower bound + residual must equal transaction fees")
                if None not in (fees, tips, total) and not math.isclose(
                    total, fees + tips, abs_tol=1e-9,
                ):
                    fail(f"{path}.sampled_sol.total", "must equal transaction fees + Jito tips")
            estimate_window = required_number(
                rev, "estimate_window_seconds", path, minimum=0,
            )
            required_number(
                rev, "estimated_blocks_in_window", path, minimum=0, integer=True,
            )
            required_number(rev, "sample_mean_estimate_sol", path, minimum=0)
            if (isinstance(window, dict) and observed_seconds is not None
                    and estimate_window is not None and estimate_window != observed_seconds):
                    fail(f"{path}.estimate_window_seconds", "must equal activity.window.observed_seconds")

    news = snapshot.get("news")
    if schema_version >= 8 and isinstance(news, dict) and (
        "available" in news or "sources" in news or "current_status" in news
    ):
        relation_sources = news.get("sources")
        relation_sources = relation_sources if isinstance(relation_sources, dict) else {}
        relation_current = news.get("current_status")
        source_available = any(
            isinstance(source, dict) and source.get("available") is True
            for source in relation_sources.values()
        )
        current_usable = isinstance(relation_current, dict) and (
            relation_current.get("available") is True
            or relation_current.get("partial") is True
        )
        expected_news_available = source_available or current_usable
        if news.get("available") is not expected_news_available:
            fail("news.available", "must reflect source or current-status availability")
        sources_complete = all(
            isinstance(relation_sources.get(name), dict)
            and relation_sources[name].get("available") is True
            and relation_sources[name].get("partial") is False
            for name in ("agave_releases", "solana_news", "simd_proposals", "network_status")
        )
        current_complete = isinstance(relation_current, dict) and (
            relation_current.get("available") is True
            and relation_current.get("partial") is False
        )
        expected_news_partial = expected_news_available and not (
            sources_complete and current_complete
        )
        if (expected_news_available or "partial" in news) and (
            news.get("partial") is not expected_news_partial
        ):
            fail("news.partial", "must reflect incomplete sources or current status")
    if schema_version >= 9 and isinstance(news, dict):
        editorial_items = news.get("items")
        if not isinstance(editorial_items, list):
            fail("news.items", "must be a list")
            editorial_items = []
        elif len(editorial_items) > 24:
            fail("news.items", "cannot exceed 24 normalized editorial items")
        unique_records(editorial_items, "news.items", ("id", "canonical_url"))
        editorial_ids: set[str] = set()
        allowed_categories = {"release", "network", "governance", "event", "ecosystem"}
        for index, item in enumerate(editorial_items):
            path = f"news.items[{index}]"
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id:
                editorial_ids.add(item_id)
            source_id = item.get("source_id")
            relation_sources = news.get("sources")
            if (not isinstance(source_id, str) or not isinstance(relation_sources, dict)
                    or source_id not in relation_sources):
                fail(f"{path}.source_id", "must identify a declared source")
            relation_source = (
                relation_sources.get(source_id)
                if isinstance(source_id, str) and isinstance(relation_sources, dict) else None
            )
            source_items = (
                relation_source.get("items") if isinstance(relation_source, dict) else None
            )
            source_item = next(
                (
                    candidate for candidate in source_items
                    if isinstance(candidate, dict) and candidate.get("id") == item_id
                ),
                None,
            ) if isinstance(source_items, list) else None
            current_status = news.get("current_status")
            status_observed_at = (
                current_status.get("observed_at_unix")
                if isinstance(current_status, dict) else None
            )
            synthetic_status = (
                source_id == "network_status"
                and isinstance(status_observed_at, int)
                and not isinstance(status_observed_at, bool)
                and item_id == f"status-summary:{status_observed_at}"
                and current_status.get("status_available") is True
            )
            if source_item is None and not synthetic_status:
                fail(f"{path}.source_id", "must identify an eligible recorded source item")
            elif (source_id == "agave_releases" and isinstance(source_item, dict)
                  and source_item.get("draft") is True):
                fail(f"{path}.source_id", "cannot feature a draft release")
            for field in ("publisher", "title", "art_seed"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    fail(f"{path}.{field}", "must be a non-empty string")
            if item.get("category") not in allowed_categories:
                fail(f"{path}.category", "must use the fixed editorial vocabulary")
            canonical_url = item.get("canonical_url")
            if not editorial_url_matches_source(source_id, canonical_url):
                fail(f"{path}.canonical_url", "must be HTTPS on the declared source host")
            if (isinstance(source_item, dict)
                    and canonical_url != source_item.get("link")):
                fail(f"{path}.canonical_url", "must match its recorded source item")
            if synthetic_status and canonical_url != "https://status.solana.com/":
                fail(f"{path}.canonical_url", "must match the recorded public status page")
            published_at = item.get("published_at")
            if published_at is not None and _parse_aware_timestamp(published_at) is None:
                fail(f"{path}.published_at", "must be offset-aware ISO-8601 when present")
            recorded_at = item.get("recorded_at")
            if _parse_aware_timestamp(recorded_at) is None:
                fail(f"{path}.recorded_at", "must be offset-aware ISO-8601")
            elif recorded_at != snapshot.get("collected_at"):
                fail(f"{path}.recorded_at", "must equal snapshot collected_at")
            if item.get("state") != "recorded":
                fail(f"{path}.state", "must be recorded")
            note = item.get("editorial_note")
            if note is not None and (not isinstance(note, str) or len(note) > 280):
                fail(f"{path}.editorial_note", "must be null or at most 280 characters")
        featured_item_id = news.get("featured_item_id")
        if featured_item_id is not None and featured_item_id not in editorial_ids:
            fail("news.featured_item_id", "must identify a normalized editorial item")
        if editorial_items and featured_item_id is None:
            fail("news.featured_item_id", "is required when editorial items exist")
    if schema_version >= 8 and isinstance(news, dict) and news.get("available") is True:
        sources = news.get("sources")
        if not isinstance(sources, dict):
            fail("news.sources", "must be an object")
            sources = {}
        missing_sources = {
            "agave_releases", "solana_news", "simd_proposals", "network_status",
        } - set(sources)
        if missing_sources:
            fail("news.sources", "missing declared sources: " + ", ".join(sorted(missing_sources)))
        for source_name, source in sources.items():
            path = f"news.sources.{source_name}"
            if not isinstance(source, dict):
                fail(path, "must be an object")
                continue
            if not isinstance(source.get("available"), bool):
                fail(f"{path}.available", "must be boolean")
            if source.get("available") is not True:
                if source.get("items"):
                    fail(f"{path}.items", "unavailable source cannot publish items")
                if not isinstance(source.get("reason"), str) or not source["reason"].strip():
                    fail(f"{path}.reason", "unavailable source must retain a reason")
                continue
            items = source.get("items")
            if not isinstance(items, list):
                fail(f"{path}.items", "must be a list")
                continue
            unique_records(items, f"{path}.items", ("id",))
            item_count = required_number(source, "item_count", path, minimum=0, integer=True)
            invalid_count = required_number(
                source, "invalid_item_count", path, minimum=0, integer=True,
            ) if source_name not in {"simd_proposals", "simd_proposal_metadata"} else None
            if item_count is not None and item_count != len(items):
                fail(f"{path}.item_count", "must equal len(items)")
            if not isinstance(source.get("partial"), bool):
                fail(f"{path}.partial", "must be boolean")
            elif invalid_count and source.get("partial") is not True:
                fail(f"{path}.partial", "must be true when invalid items were rejected")
            for index, item in enumerate(items):
                item_path = f"{path}.items[{index}]"
                if not isinstance(item, dict):
                    continue
                if not isinstance(item.get("title"), str) or not item["title"].strip():
                    fail(f"{item_path}.title", "must be a non-empty string")
                link = item.get("link")
                if not isinstance(link, str) or not link.startswith("https://"):
                    fail(f"{item_path}.link", "must be HTTPS")
                published = item.get("published")
                if published is not None and _parse_aware_timestamp(published) is None:
                    fail(f"{item_path}.published", "must be offset-aware ISO-8601 when present")
            latest_published = source.get("latest_published")
            if latest_published is not None and _parse_aware_timestamp(latest_published) is None:
                fail(f"{path}.latest_published", "must be offset-aware ISO-8601 when present")

        agave = sources.get("agave_releases")
        if isinstance(agave, dict) and agave.get("available") is True:
            items = agave.get("items")
            covered = missing = 0
            if isinstance(items, list):
                for index, item in enumerate(items):
                    path = f"news.sources.agave_releases.items[{index}]"
                    if not isinstance(item, dict):
                        continue
                    tag = item.get("tag")
                    draft = item.get("draft")
                    prerelease = item.get("prerelease")
                    stable = item.get("stable")
                    if not isinstance(tag, str) or not tag:
                        fail(f"{path}.tag", "must be a non-empty string")
                        tag = ""
                    for field, value in (("draft", draft), ("prerelease", prerelease), ("stable", stable)):
                        if not isinstance(value, bool):
                            fail(f"{path}.{field}", "must be boolean")
                    prerelease_tag = bool(re.search(
                        r"(?:alpha|beta|rc)(?:[-._]?\d+)*$", tag.split("+", 1)[0], re.IGNORECASE,
                    ))
                    expected_stable = draft is False and prerelease is False and not prerelease_tag
                    if stable is not expected_stable:
                        fail(f"{path}.stable", "must match draft, prerelease, and tag suffix")
                    expected_channel = (
                        "draft" if draft is True else
                        "prerelease" if prerelease is True or prerelease_tag else
                        "stable" if expected_stable else "unknown"
                    )
                    if item.get("release_channel") != expected_channel:
                        fail(f"{path}.release_channel", f"must be {expected_channel}")
                    commit = item.get("tag_commit_sha")
                    if commit is None:
                        missing += 1
                    elif not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                        fail(f"{path}.tag_commit_sha", "must be a full lowercase commit SHA")
                    else:
                        covered += 1
                    if stable is True and commit is None:
                        fail(f"{path}.tag_commit_sha", "stable releases require a pinned commit SHA")
            for key, expected in (
                ("tag_commit_covered_count", covered), ("tag_commit_missing_count", missing),
            ):
                value = required_number(agave, key, "news.sources.agave_releases", minimum=0, integer=True)
                if value is not None and value != expected:
                    fail(f"news.sources.agave_releases.{key}", f"must equal {expected}")
            invalid = agave.get("invalid_item_count")
            if (missing or isinstance(invalid, int) and invalid > 0) and agave.get("partial") is not True:
                fail("news.sources.agave_releases.partial", "missing provenance or rejected rows require partial")

        simd = sources.get("simd_proposals")
        if isinstance(simd, dict) and simd.get("available") is True:
            path = "news.sources.simd_proposals"
            commit = simd.get("source_commit")
            valid_commit = isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
            if not valid_commit:
                fail(f"{path}.source_commit", "must be a full lowercase commit SHA")
            source_url = simd.get("url")
            if (not isinstance(source_url, str) or not source_url.startswith("https://")
                    or valid_commit and f"/{commit}" not in source_url):
                fail(f"{path}.url", "must pin the source commit in an HTTPS URL")
            proposals = simd.get("proposals")
            unique_records(proposals, f"{path}.proposals", ("identifier",))
            proposal_count = required_number(simd, "proposal_count", path, minimum=0, integer=True)
            document_count = required_number(simd, "document_count", path, minimum=0, integer=True)
            unparsed = simd.get("unparsed_paths")
            if not isinstance(proposals, list):
                proposals = []
            if not isinstance(unparsed, list) or not all(isinstance(value, str) and value for value in unparsed):
                fail(f"{path}.unparsed_paths", "must be a list of paths")
                unparsed = []
            if proposal_count is not None and proposal_count != len(proposals):
                fail(f"{path}.proposal_count", "must equal len(proposals)")
            if (document_count is not None and proposal_count is not None
                    and document_count != proposal_count + len(unparsed)):
                fail(f"{path}.document_count", "must equal parsed proposals plus unparsed paths")
            coverage_complete = simd.get("coverage_complete")
            if not isinstance(coverage_complete, bool) or coverage_complete != (not unparsed):
                fail(f"{path}.coverage_complete", "must match unparsed_paths")
            if simd.get("partial") != bool(unparsed):
                fail(f"{path}.partial", "must match unparsed_paths")
            proposal_by_id = {}
            for index, proposal in enumerate(proposals):
                proposal_path = f"{path}.proposals[{index}]"
                if not isinstance(proposal, dict):
                    continue
                identifier = proposal.get("identifier")
                if not isinstance(identifier, str) or re.fullmatch(r"SIMD-\d{4,}", identifier) is None:
                    fail(f"{proposal_path}.identifier", "must be a stable SIMD identifier")
                else:
                    proposal_by_id[identifier] = proposal
                for field in ("name", "status"):
                    if not isinstance(proposal.get(field), str) or not proposal[field].strip():
                        fail(f"{proposal_path}.{field}", "must be a non-empty string")
                if proposal.get("basis") != "recorded":
                    fail(f"{proposal_path}.basis", "must be recorded frontmatter")
                if proposal.get("source_commit") != commit:
                    fail(f"{proposal_path}.source_commit", "must match the pinned source commit")
                proposal_source = proposal.get("source")
                if (not isinstance(proposal_source, str) or not proposal_source.startswith("https://")
                        or valid_commit and f"/blob/{commit}/proposals/" not in proposal_source):
                    fail(f"{proposal_path}.source", "must pin the proposal and source commit")
                if "created" not in proposal:
                    fail(f"{proposal_path}.created", "must retain source date metadata")
                created = proposal.get("created")
                if created is not None:
                    try:
                        datetime.strptime(created, "%Y-%m-%d")
                    except (TypeError, ValueError):
                        fail(f"{proposal_path}.created", "must be a source-native YYYY-MM-DD date")
            items = simd.get("items")
            if isinstance(items, list):
                for index, item in enumerate(items):
                    item_path = f"{path}.items[{index}]"
                    if not isinstance(item, dict):
                        continue
                    proposal = proposal_by_id.get(item.get("id"))
                    if proposal is None:
                        fail(f"{item_path}.id", "must identify a retained proposal")
                        continue
                    if item.get("published") is not None:
                        fail(f"{item_path}.published", "must remain null; creation is not publication")
                    if item.get("date_basis") != "proposal_created" or item.get("created") != proposal.get("created"):
                        fail(f"{item_path}.created", "must use the proposal-created date basis")
                    if item.get("status") != proposal.get("status") or item.get("link") != proposal.get("source"):
                        fail(item_path, "must match retained proposal metadata")

        current = news.get("current_status")
        if not isinstance(current, dict):
            fail("news.current_status", "must be an object")
        else:
            path = "news.current_status"
            flags = {}
            for key in ("status_available", "incidents_available", "incident_response_available"):
                value = current.get(key)
                if not isinstance(value, bool):
                    fail(f"{path}.{key}", "must be boolean")
                flags[key] = value
            invalid = required_number(current, "invalid_incident_count", path, minimum=0, integer=True)
            expected_incidents = flags["incident_response_available"] is True and invalid == 0
            if flags["incidents_available"] is not expected_incidents:
                fail(f"{path}.incidents_available", "must reflect response availability and rejected rows")
            expected_available = flags["status_available"] is True and flags["incidents_available"] is True
            if current.get("available") is not expected_available:
                fail(f"{path}.available", "must reflect status and incident availability")
            expected_partial = (not expected_available and
                                (flags["status_available"] is True
                                 or flags["incident_response_available"] is True))
            if current.get("partial") is not expected_partial:
                fail(f"{path}.partial", "must reflect partial source availability")
            if flags["status_available"] is True:
                for key in ("indicator", "description"):
                    if not isinstance(current.get(key), str) or not current[key].strip():
                        fail(f"{path}.{key}", "must be a non-empty string")
            status_sources = current.get("sources")
            if not isinstance(status_sources, dict):
                fail(f"{path}.sources", "must be an object")
            else:
                for key in ("summary", "incidents"):
                    value = status_sources.get(key)
                    if not isinstance(value, str) or not value.startswith("https://"):
                        fail(f"{path}.sources.{key}", "must be HTTPS")
            observed_at = current.get("observed_at_unix")
            if (observed_at is not None and
                    (not isinstance(observed_at, int) or isinstance(observed_at, bool)
                     or observed_at < 0)):
                fail(f"{path}.observed_at_unix", "must be a non-negative unix timestamp")
            active = current.get("incidents")
            history = current.get("incident_history")
            unique_records(active, f"{path}.incidents", ("id",))
            unique_records(history, f"{path}.incident_history", ("id",))
            valid_statuses = {"investigating", "identified", "monitoring", "resolved", "postmortem"}
            for collection_name, rows in (("incidents", active), ("incident_history", history)):
                if not isinstance(rows, list):
                    continue
                for index, incident in enumerate(rows):
                    incident_path = f"{path}.{collection_name}[{index}]"
                    if not isinstance(incident, dict):
                        continue
                    if not isinstance(incident.get("name"), str) or not incident["name"].strip():
                        fail(f"{incident_path}.name", "must be a non-empty string")
                    if incident.get("status") not in valid_statuses:
                        fail(f"{incident_path}.status", "is not a supported status")
                    if collection_name == "incidents" and incident.get("status") not in {
                        "investigating", "identified", "monitoring",
                    }:
                        fail(f"{incident_path}.status", "active incidents must be unresolved")
                    url = incident.get("url")
                    if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
                        fail(f"{incident_path}.url", "must be HTTPS when present")
                    timestamps = 0
                    for key in ("created_at", "updated_at", "monitoring_at", "resolved_at"):
                        value = incident.get(key)
                        if value is not None:
                            timestamps += 1
                            if _parse_aware_timestamp(value) is None:
                                fail(f"{incident_path}.{key}", "must be offset-aware ISO-8601")
                    if not timestamps:
                        fail(incident_path, "must retain a source-native incident timestamp")
                    created_at = _parse_aware_timestamp(incident.get("created_at"))
                    updated_at = _parse_aware_timestamp(incident.get("updated_at"))
                    if created_at is not None and updated_at is not None and updated_at < created_at:
                        fail(incident_path, "updated_at cannot precede created_at")
            if isinstance(active, list) and isinstance(history, list):
                history_ids = {
                    incident.get("id") for incident in history if isinstance(incident, dict)
                }
                for index, incident in enumerate(active):
                    if isinstance(incident, dict) and incident.get("id") not in history_ids:
                        fail(f"{path}.incidents[{index}].id", "must also exist in incident_history")
            active_count = current.get("active_incident_count")
            if flags["incidents_available"] is True:
                if (not isinstance(active_count, int) or isinstance(active_count, bool)
                        or not isinstance(active, list) or active_count != len(active)):
                    fail(f"{path}.active_incident_count", "must equal len(incidents)")
            elif active_count is not None:
                fail(f"{path}.active_incident_count", "must be null without complete incident evidence")

    if schema_version >= 9 and isinstance(news, dict):
        sources = news.get("sources")
        current = news.get("current_status")
        try:
            expected_editorial_items = news_module.normalize_editorial_items(
                sources if isinstance(sources, dict) else {},
                current if isinstance(current, dict) else None,
            )
        except (OSError, OverflowError, ValueError):
            expected_editorial_items = []
            fail("news.items", "must be derivable from the recorded news sources")
        for item in expected_editorial_items:
            item["recorded_at"] = snapshot.get("collected_at")
        if news.get("items") != expected_editorial_items:
            fail("news.items", "must exactly match the recorded-source editorial projection")
        expected_featured = news_module.featured_editorial_item_id(expected_editorial_items)
        if news.get("featured_item_id") != expected_featured:
            fail("news.featured_item_id", "must match the deterministic featured story")

    growth = snapshot.get("growth")
    if schema_version >= 8 and isinstance(growth, dict):
        provider_semantics = {
            "daily_active_addresses": {
                "metric": "Active Addresses",
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "display_name": "Stablecoin active-address provider range",
                "source_label": "Active Addresses",
                "scope": "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
                "unit": "Count",
                "canonical": False,
            },
            "daily_fee_payers": {
                "metric": "Fee Payers",
                "semantic_metric_id": "transaction_initiator_provider_range",
                "display_name": "Transaction-initiator provider range",
                "source_label": "Fee Payers",
                "scope": "provider observations of transaction initiators, not unique humans",
                "unit": "Count",
                "canonical": False,
            },
        }
        provider_summaries: dict[str, dict[str, Any]] = {}
        for key, expected in provider_semantics.items():
            summary = growth.get(key)
            if not isinstance(summary, dict):
                continue
            for flag in ("available", "history_available"):
                if flag in summary and not isinstance(summary[flag], bool):
                    fail(f"growth.{key}.{flag}", "must be boolean")
            if not (summary.get("available") is True
                    or summary.get("history_available") is True):
                observations = summary.get("provider_observations")
                if observations not in (None, []):
                    fail(
                        f"growth.{key}.provider_observations",
                        "must be empty unless history_available is true",
                    )
                continue
            provider_summaries[key] = summary
            path = f"growth.{key}"
            for flag in ("available", "history_available"):
                if not isinstance(summary.get(flag), bool):
                    fail(f"{path}.{flag}", "is required and must be boolean")
            for field, value in expected.items():
                if summary.get(field) != value:
                    fail(f"{path}.{field}", f"must be {value!r}")

            observations = summary.get("provider_observations")
            observation_ids: set[tuple[str, str]] = set()
            dates: set[str] = set()
            providers: set[str] = set()
            if not isinstance(observations, list):
                fail(f"{path}.provider_observations", "must be a list")
                observations = []
            for index, observation in enumerate(observations):
                observation_path = f"{path}.provider_observations[{index}]"
                if not isinstance(observation, dict):
                    fail(observation_path, "must be an object")
                    continue
                date = observation.get("date")
                try:
                    valid_date = (
                        isinstance(date, str)
                        and datetime.strptime(date, "%Y-%m-%d").date().isoformat() == date
                    )
                except ValueError:
                    valid_date = False
                if not valid_date:
                    fail(f"{observation_path}.date", "must be a valid YYYY-MM-DD date")
                provider = observation.get("provider")
                if not isinstance(provider, str) or not provider.strip():
                    fail(f"{observation_path}.provider", "must be a non-empty string")
                required_number(
                    observation, "value", observation_path, minimum=0, integer=True,
                )
                if valid_date and isinstance(provider, str) and provider.strip():
                    identity = (date, provider)
                    if identity in observation_ids:
                        fail(
                            f"{path}.provider_observations",
                            "contains duplicate date/provider identities",
                        )
                    observation_ids.add(identity)
                    dates.add(date)
                    providers.add(provider)

            count_values = {
                count_key: required_number(
                    summary, count_key, path, minimum=0, integer=True,
                )
                for count_key in (
                    "source_row_count", "observed_row_count", "observed_date_count",
                    "observed_provider_count", "invalid_row_count",
                    "exact_duplicate_row_count", "conflicting_identity_count",
                )
            }
            for count_key, actual in (
                ("observed_row_count", len(observations)),
                ("observed_date_count", len(dates)),
                ("observed_provider_count", len(providers)),
            ):
                if count_values[count_key] is not None and count_values[count_key] != actual:
                    fail(f"{path}.{count_key}", f"must equal {actual}")
            if summary.get("history_available") is not bool(observations):
                fail(f"{path}.history_available", "must reflect retained observations")
            if summary.get("available") is True and not observations:
                fail(f"{path}.available", "requires retained provider observations")
            expected_oldest = min(dates) if dates else None
            expected_newest = max(dates) if dates else None
            if summary.get("oldest_date") != expected_oldest:
                fail(f"{path}.oldest_date", f"must be {expected_oldest!r}")
            if summary.get("newest_date") != expected_newest:
                fail(f"{path}.newest_date", f"must be {expected_newest!r}")

            conflicts = summary.get("conflicts")
            conflict_ids: set[tuple[str, str]] = set()
            if not isinstance(conflicts, list):
                fail(f"{path}.conflicts", "must be a list")
                conflicts = []
            for index, conflict in enumerate(conflicts):
                conflict_path = f"{path}.conflicts[{index}]"
                if not isinstance(conflict, dict):
                    fail(conflict_path, "must be an object")
                    continue
                date = conflict.get("date")
                provider = conflict.get("provider")
                try:
                    valid_date = (
                        isinstance(date, str)
                        and datetime.strptime(date, "%Y-%m-%d").date().isoformat() == date
                    )
                except ValueError:
                    valid_date = False
                if not valid_date:
                    fail(f"{conflict_path}.date", "must be a valid YYYY-MM-DD date")
                if not isinstance(provider, str) or not provider.strip():
                    fail(f"{conflict_path}.provider", "must be a non-empty string")
                if valid_date and isinstance(provider, str) and provider.strip():
                    identity = (date, provider)
                    if identity in conflict_ids:
                        fail(f"{path}.conflicts", "contains duplicate identities")
                    conflict_ids.add(identity)
            if count_values["conflicting_identity_count"] is not None and (
                count_values["conflicting_identity_count"] != len(conflict_ids)
            ):
                fail(
                    f"{path}.conflicting_identity_count",
                    "must equal len(conflicts)",
                )
            if observation_ids & conflict_ids:
                fail(
                    f"{path}.provider_observations",
                    "must omit conflicting date/provider identities",
                )
            minimum_source_rows = (
                len(observations)
                + (count_values["invalid_row_count"] or 0)
                + (count_values["exact_duplicate_row_count"] or 0)
                + 2 * len(conflict_ids)
            )
            if (count_values["source_row_count"] is not None
                    and count_values["source_row_count"] < minimum_source_rows):
                fail(
                    f"{path}.source_row_count",
                    f"cannot be less than {minimum_source_rows}",
                )
            expected_partial = bool(
                count_values["invalid_row_count"]
                or count_values["conflicting_identity_count"]
                or (summary.get("history_available") is True
                    and summary.get("available") is not True)
            )
            if summary.get("partial") is not expected_partial:
                fail(f"{path}.partial", "must reflect rejected/conflicting/history-only evidence")

        growth_sources = growth.get("sources")
        activity_source = (
            growth_sources.get("activity_benchmark")
            if isinstance(growth_sources, dict) else None
        )
        if provider_summaries and not isinstance(activity_source, dict):
            fail("growth.sources.activity_benchmark", "is required for retained provider history")
        if isinstance(activity_source, dict):
            addresses = growth.get("daily_active_addresses")
            fee_payers = growth.get("daily_fee_payers")
            expected_source = {
                "available": any(
                    isinstance(summary, dict) and summary.get("history_available") is True
                    for summary in (addresses, fee_payers)
                ),
                "active_addresses_available": (
                    isinstance(addresses, dict) and addresses.get("available") is True
                ),
                "fee_payers_available": (
                    isinstance(fee_payers, dict) and fee_payers.get("available") is True
                ),
                "active_addresses_history_available": (
                    isinstance(addresses, dict) and addresses.get("history_available") is True
                ),
                "fee_payers_history_available": (
                    isinstance(fee_payers, dict) and fee_payers.get("history_available") is True
                ),
                "active_addresses_observed_row_count": (
                    addresses.get("observed_row_count", 0) if isinstance(addresses, dict) else 0
                ),
                "fee_payers_observed_row_count": (
                    fee_payers.get("observed_row_count", 0) if isinstance(fee_payers, dict) else 0
                ),
                "partial": any(
                    isinstance(summary, dict) and summary.get("partial") is True
                    for summary in (addresses, fee_payers)
                ),
                "canonical": False,
            }
            for field, value in expected_source.items():
                if activity_source.get(field) != value:
                    fail(f"growth.sources.activity_benchmark.{field}", f"must be {value!r}")
        source = snapshot.get("source")
        canonical_rpc_endpoint = source.get("endpoint") if isinstance(source, dict) else None
        canonical_rpc_identity = (
            source.get("endpoint_identity") if isinstance(source, dict) else None
        )
        _validate_selected_usd_stablecoins(
            growth, collected_at, canonical_rpc_endpoint, canonical_rpc_identity, fail,
        )
        if "tokenized_equities" in growth:
            schema8_equities = growth.get("tokenized_equities")
            if not isinstance(schema8_equities, dict):
                fail("growth.tokenized_equities", "must be an object when present")
            else:
                _validate_schema8_xstocks(
                    snapshot, growth, schema8_equities, collected_at, fail,
                )
    equities = growth.get("tokenized_equities") if isinstance(growth, dict) else None
    if (schema_version < 8 and isinstance(equities, dict)
            and equities.get("available") is True
            and "supply_coverage" in equities):
        path = "growth.tokenized_equities"
        count_keys = (
            "registry_asset_count", "eligible_asset_count", "supply_observed_asset_count",
            "fresh_supply_asset_count", "stale_supply_asset_count",
            "supply_queried_this_run_asset_count", "supply_successful_this_run_asset_count",
            "supply_failed_this_run_asset_count", "valued_asset_count", "displayed_asset_count",
            "display_asset_limit",
        )
        counts = {
            key: required_number(equities, key, path, minimum=0, integer=True)
            for key in count_keys
        }
        all_assets = equities.get("all_assets")
        assets = equities.get("assets")
        unique_records(all_assets, f"{path}.all_assets", ("mint",))
        unique_records(assets, f"{path}.assets", ("mint",))
        registry = counts["registry_asset_count"]
        eligible = counts["eligible_asset_count"]
        observed = counts["supply_observed_asset_count"]
        fresh = counts["fresh_supply_asset_count"]
        stale = counts["stale_supply_asset_count"]
        queried = counts["supply_queried_this_run_asset_count"]
        successful = counts["supply_successful_this_run_asset_count"]
        failed = counts["supply_failed_this_run_asset_count"]
        displayed = counts["displayed_asset_count"]
        if isinstance(all_assets, list) and registry is not None and registry != len(all_assets):
            fail(f"{path}.registry_asset_count", "must equal len(all_assets)")
        if isinstance(assets, list) and displayed is not None and displayed != len(assets):
            fail(f"{path}.displayed_asset_count", "must equal len(assets)")
        for child, parent, label in (
            (eligible, registry, "eligible_asset_count cannot exceed registry_asset_count"),
            (observed, eligible, "supply_observed_asset_count cannot exceed eligible_asset_count"),
            (fresh, observed, "fresh_supply_asset_count cannot exceed observed supply"),
            (stale, observed, "stale_supply_asset_count cannot exceed observed supply"),
            (queried, eligible, "supply_queried_this_run_asset_count cannot exceed eligible_asset_count"),
            (displayed, registry, "displayed_asset_count cannot exceed registry_asset_count"),
        ):
            if child is not None and parent is not None and child > parent:
                fail(path, label)
        if None not in (fresh, stale, observed) and fresh + stale > observed:
            fail(path, "fresh_supply + stale_supply cannot exceed observed supply")
        if None not in (queried, successful, failed) and successful + failed != queried:
            fail(path, "supply_successful + supply_failed must equal queried")
        if counts["valued_asset_count"] != 0:
            fail(f"{path}.valued_asset_count", "must be zero while valuation is unavailable")
        if displayed is not None and counts["display_asset_limit"] is not None:
            if displayed > counts["display_asset_limit"]:
                fail(f"{path}.displayed_asset_count", "cannot exceed display limit")

        valuation = equities.get("valuation")
        if not isinstance(valuation, dict) or valuation.get("available") is not False:
            fail(f"{path}.valuation", "must explicitly remain unavailable")

        coverage = equities.get("supply_coverage")
        if not isinstance(coverage, dict):
            fail(f"{path}.supply_coverage", "must be an object")
        else:
            coverage_path = f"{path}.supply_coverage"
            coverage_counts = {
                key: required_number(coverage, key, coverage_path, minimum=0, integer=True)
                for key in (
                    "registry_asset_count", "eligible_asset_count", "queried_this_run_asset_count",
                    "successful_this_run_asset_count", "failed_this_run_asset_count", "observed_asset_count",
                    "fresh_asset_count", "valued_asset_count", "coverage_numerator",
                    "coverage_denominator", "fresh_max_age_seconds", "sweep_max_age_seconds",
                )
            }
            expected = {
                "registry_asset_count": registry,
                "eligible_asset_count": eligible,
                "queried_this_run_asset_count": queried,
                "successful_this_run_asset_count": successful,
                "failed_this_run_asset_count": failed,
                "observed_asset_count": observed,
                "fresh_asset_count": fresh,
                "valued_asset_count": counts["valued_asset_count"],
                "coverage_denominator": eligible,
            }
            for key, value in expected.items():
                if value is not None and coverage_counts[key] is not None and coverage_counts[key] != value:
                    fail(f"{coverage_path}.{key}", f"must equal {path} {key}")
            if coverage.get("attempt_scope") != "current collection run":
                fail(f"{coverage_path}.attempt_scope", "must identify the current collection run")
            numerator = coverage_counts["coverage_numerator"]
            denominator = coverage_counts["coverage_denominator"]
            if numerator is not None and denominator is not None and numerator > denominator:
                fail(f"{coverage_path}.coverage_numerator", "cannot exceed coverage_denominator")
            oldest = _timestamp_seconds(coverage.get("oldest_observation_at"))
            newest = _timestamp_seconds(coverage.get("newest_observation_at"))
            span = number(coverage, "observation_span_seconds", coverage_path, minimum=0)
            if (coverage.get("oldest_observation_at") is None
                    or coverage.get("newest_observation_at") is None):
                if observed:
                    fail(coverage_path, "observed supplies require oldest/newest timestamps")
            elif oldest is None or newest is None:
                fail(coverage_path, "observation timestamps must be unix or offset-aware ISO-8601")
            elif oldest > newest:
                fail(coverage_path, "oldest_observation_at cannot exceed newest_observation_at")
            elif span is not None and not math.isclose(span, newest - oldest, abs_tol=1):
                fail(f"{coverage_path}.observation_span_seconds", "must match timestamp bounds")
            sweep_complete = coverage.get("sweep_complete")
            if not isinstance(sweep_complete, bool):
                fail(f"{coverage_path}.sweep_complete", "must be a boolean")
            if sweep_complete is True:
                if (coverage.get("registry_complete") is not True or not denominator
                        or numerator != denominator
                        or (span is not None and coverage_counts["sweep_max_age_seconds"] is not None
                            and span > coverage_counts["sweep_max_age_seconds"])):
                    fail(coverage_path, "complete sweep requires complete registry/full coverage/valid span")
                if coverage.get("scope") != "registry-wide":
                    fail(f"{coverage_path}.scope", "complete sweep must be registry-wide")
            elif coverage.get("scope") != "observed subset":
                fail(f"{coverage_path}.scope", "incomplete sweep must be observed subset")

        sources = growth.get("sources") if isinstance(growth, dict) else None
        dex_source = sources.get("dex_volume") if isinstance(sources, dict) else None
        if isinstance(dex_source, dict) and dex_source.get("available") is True:
            transport_complete = dex_source.get("transport_complete")
            if not isinstance(transport_complete, bool):
                fail("growth.sources.dex_volume.transport_complete", "must be a boolean")
            expected_coverage = "partial" if transport_complete is True else "not established"
            if dex_source.get("market_coverage") != expected_coverage:
                fail(
                    "growth.sources.dex_volume.market_coverage",
                    f"must be {expected_coverage} for this transport state",
                )
            exclusions = dex_source.get("exclusions")
            if not isinstance(exclusions, list) or not exclusions:
                fail("growth.sources.dex_volume.exclusions", "must name economic exclusions")

    if (schema_version < 8 and isinstance(equities, dict)
            and equities.get("available") is True
            and "supply_coverage" not in equities):
        count_keys = (
            "registry_asset_count", "price_covered_asset_count", "fresh_price_asset_count",
            "stale_asset_count", "unknown_price_asset_count", "supply_queried_asset_count",
            "supply_successful_asset_count", "supply_failed_asset_count",
            "valued_asset_count", "displayed_asset_count", "display_asset_limit",
        )
        counts = {key: number(equities, key, "growth.tokenized_equities", minimum=0, integer=True)
                  for key in count_keys}
        all_assets = equities.get("all_assets")
        assets = equities.get("assets")
        unique_records(all_assets, "growth.tokenized_equities.all_assets", ("mint",))
        unique_records(assets, "growth.tokenized_equities.assets", ("mint",))
        registry = counts["registry_asset_count"]
        if isinstance(all_assets, list) and registry is not None and registry != len(all_assets):
            fail("growth.tokenized_equities.registry_asset_count", "must equal len(all_assets)")
        if isinstance(assets, list) and counts["displayed_asset_count"] is not None:
            if counts["displayed_asset_count"] != len(assets):
                fail("growth.tokenized_equities.displayed_asset_count", "must equal len(assets)")
        if registry is not None:
            for key in count_keys[1:]:
                value = counts[key]
                if value is not None and value > registry:
                    fail(f"growth.tokenized_equities.{key}", "cannot exceed registry_asset_count")
        price_covered = counts["price_covered_asset_count"]
        fresh = counts["fresh_price_asset_count"]
        stale = counts["stale_asset_count"]
        unknown = counts["unknown_price_asset_count"]
        if None not in (price_covered, fresh, stale) and fresh + stale != price_covered:
            fail("growth.tokenized_equities", "fresh_price + stale must equal price_covered")
        if None not in (registry, price_covered, unknown) and price_covered + unknown != registry:
            fail("growth.tokenized_equities", "price_covered + unknown_price must equal registry")
        queried = counts["supply_queried_asset_count"]
        successful = counts["supply_successful_asset_count"]
        failed = counts["supply_failed_asset_count"]
        if None not in (queried, successful, failed) and successful + failed != queried:
            fail("growth.tokenized_equities", "supply_successful + supply_failed must equal queried")
        valued = counts["valued_asset_count"]
        if valued is not None and successful is not None and valued > successful:
            fail("growth.tokenized_equities.valued_asset_count", "cannot exceed successful supply count")
        if valued is not None and fresh is not None and valued > fresh:
            fail("growth.tokenized_equities.valued_asset_count", "cannot exceed fresh price count")
        displayed = counts["displayed_asset_count"]
        if displayed is not None and valued is not None and displayed > valued:
            fail("growth.tokenized_equities.displayed_asset_count", "cannot exceed valued count")
        if displayed is not None and counts["display_asset_limit"] is not None:
            if displayed > counts["display_asset_limit"]:
                fail("growth.tokenized_equities.displayed_asset_count", "cannot exceed display limit")

    return failures


def check_publishable(
    snapshot: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int,
    allow_release_held: bool = False,
) -> dict[str, Any]:
    """Pre-publish gate over a candidate snapshot. Judgment, not transformation.

    Returns {"publishable": bool, "failures": [{"check", "detail"}, ...]}.
    Publishable requires a supported schema_version; a timezone-aware ISO-8601
    collected_at no older than max_age_seconds (and not meaningfully in the
    future) relative to `now`; the required top-level sections; finite core
    numbers; at least one available core evidence section; and no release-held
    economics. Optional unavailable or carried-forward last-known-good sources
    degrade independently. ``allow_release_held`` exists only for the explicit
    private ``collect.py --dry-run --with-economics`` research path.
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(snapshot, dict):
        return {
            "publishable": False,
            "failures": [{"check": "structure",
                          "detail": f"snapshot must be a JSON object, got {type(snapshot).__name__}"}],
        }
    failures: list[dict[str, str]] = []

    version = snapshot.get("schema_version")
    version_ok = (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version in SUPPORTED_SCHEMA_VERSIONS
    )
    if not version_ok:
        failures.append({
            "check": "schema_version",
            "detail": f"unsupported schema_version {version!r}; supported "
                      f"{min(SUPPORTED_SCHEMA_VERSIONS)}..{max(SUPPORTED_SCHEMA_VERSIONS)}",
        })

    observed = snapshot.get("collected_at")
    parsed: datetime | None = None
    if not isinstance(observed, str) or not observed.strip():
        failures.append({
            "check": "collected_at",
            "detail": f"missing or non-string collected_at: {observed!r}",
        })
    else:
        try:
            parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            failures.append({
                "check": "collected_at",
                "detail": f"malformed ISO-8601 collected_at: {observed!r}",
            })
        if parsed is not None and parsed.tzinfo is None:
            failures.append({
                "check": "collected_at",
                "detail": f"naive collected_at without a UTC offset: {observed!r}",
            })
            parsed = None
    if parsed is not None:
        age = (reference - parsed).total_seconds()
        if age < -FUTURE_TOLERANCE_SECONDS:
            failures.append({
                "check": "collected_at",
                "detail": f"collected_at {observed!r} is in the future relative to "
                          f"{reference.isoformat()}",
            })
        elif age > max_age_seconds:
            failures.append({
                "check": "collected_at",
                "detail": f"stale collected_at {observed!r}: {round(age)}s old, "
                          f"max_age_seconds is {max_age_seconds}",
            })

    malformed_sections = [
        name for name in REQUIRED_SECTIONS if not isinstance(snapshot.get(name), dict)
    ] + [
        name for name in OPTIONAL_SECTIONS
        if name in snapshot and not isinstance(snapshot[name], dict)
    ]
    if malformed_sections:
        failures.append({
            "check": "structure",
            "detail": "missing or non-object sections: " + ", ".join(malformed_sections),
        })

    nonfinite: list[str] = []
    for name in REQUIRED_SECTIONS:
        section = snapshot.get(name)
        if isinstance(section, dict):
            _collect_nonfinite_paths(section, name, nonfinite)
    if nonfinite:
        failures.append({
            "check": "finite_core_values",
            "detail": "non-finite numbers at: " + ", ".join(nonfinite),
        })

    economics = snapshot.get("economics")
    if (isinstance(economics, dict) and economics.get("available") is True
            and not allow_release_held):
        # The CoinGecko Demo key transport is the one approved keyed source:
        # it alone records requires_api_key=True, and its rights-held siblings
        # stay available:false so their per-source publication hold persists.
        approved_keyed = economics.get("requires_api_key") is True
        if not approved_keyed:
            failures.append({
                "check": "release_policy",
                "detail": "economics is release-held; public candidates must record it "
                          "as unavailable until source rights are approved",
            })

    failures.extend(semantic_failures(snapshot))

    # Core coverage shares detect.source_eligible: a section that is merely
    # retained — stale, carried forward, or explicitly non-fresh — does not
    # satisfy publication coverage any more than an absent one.
    if not any(
        isinstance(snapshot.get(name), dict)
        and snapshot[name].get("available") is True
        and detect.source_eligible(snapshot, name)
        for name in CORE_EVIDENCE_SECTIONS
    ):
        failures.append({
            "check": "core_coverage",
            "detail": 'no core evidence section reports a live "available": true; '
                      "stale or carried-forward sections do not count — "
                      "a minimally truthful report needs recorded on-chain state",
        })

    return {"publishable": not failures, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    """Gate CLI. Exit 0 = publishable; exit 1 = stop before render/deploy."""
    parser = argparse.ArgumentParser(
        description="Pre-publish validation gate over a candidate snapshot.")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH,
                        help="candidate to validate (default: snapshots/latest.json)")
    parser.add_argument("--max-age-seconds", type=int, required=True,
                        help="oldest acceptable collected_at, seconds before the reference time")
    parser.add_argument("--now", default=None,
                        help="ISO-8601 reference time with offset (default: current UTC time)")
    args = parser.parse_args(argv)

    reference: datetime | None = None
    if args.now is not None:
        try:
            reference = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            parser.error(f"--now is not an ISO-8601 timestamp: {args.now}")
        if reference.tzinfo is None:
            parser.error(f"--now must carry a UTC offset: {args.now}")

    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        result = {
            "publishable": False,
            "failures": [{
                "check": "candidate_readable",
                "detail": f"{args.snapshot}: {error}",
            }],
        }
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1

    result = check_publishable(snapshot, now=reference, max_age_seconds=args.max_age_seconds)
    if result["publishable"]:
        print(f"publishable: {args.snapshot} (collected_at {snapshot['collected_at']})")
        return 0
    print(json.dumps(result, indent=2), file=sys.stderr)
    return 1


def record(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach pipeline metadata to an already-validated snapshot (older fixtures)."""
    if not isinstance(snapshot, dict):
        return snapshot
    if isinstance(snapshot.get("pipeline"), dict) and snapshot["pipeline"].get("stages") == list(STAGES):
        return snapshot
    annotated = dict(snapshot)
    annotated["pipeline"] = {
        "stages": list(STAGES),
        "stage_calls": dict(STAGE_CALLS),
        "evidence_labels": dict(EVIDENCE_LABELS),
    }
    return annotated


if __name__ == "__main__":
    raise SystemExit(main())
