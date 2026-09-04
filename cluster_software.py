"""Unweighted software-version evidence from Solana cluster gossip records.

This source describes node records known to the queried RPC endpoint. It does
not identify validator clients, prove reachability, or measure stake-weighted
software adoption.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import re
from typing import Any

import growth


MAX_VERSION_GROUPS = 20
MAX_NODE_RECORDS = 100_000
MAX_VERSION_LENGTH = 128
NODE_PUBKEY_PATTERN = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
POPULATION = "unique node pubkeys known to the queried RPC endpoint"
WEIGHTING = "unweighted node count"
COUNT_FIELDS = (
    "observed_node_count",
    "version_reported_node_count",
    "unknown_version_node_count",
    "distinct_reported_version_count",
    "published_version_group_count",
    "other_reported_version_node_count",
)
SECTION_FIELDS = frozenset({
    "available", "observed_at", *COUNT_FIELDS, "version_coverage_pct",
    "versions", "source",
})
SOURCE_FIELDS = frozenset({
    "method", "endpoint", "endpoint_identity", "population", "weighting",
})
VERSION_FIELDS = frozenset({
    "version", "node_count", "share_of_observed_nodes_pct",
})


def _observed_at(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def _source(endpoint: Any) -> dict[str, Any]:
    return {
        "method": "getClusterNodes",
        **growth.rpc_endpoint_reference(endpoint),
        "population": POPULATION,
        "weighting": WEIGHTING,
    }


def _unavailable(endpoint: Any, observed_at: str | None, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "observed_at": observed_at,
        "observed_node_count": None,
        "version_reported_node_count": None,
        "unknown_version_node_count": None,
        "version_coverage_pct": None,
        "distinct_reported_version_count": None,
        "published_version_group_count": None,
        "other_reported_version_node_count": None,
        "versions": [],
        "source": _source(endpoint),
        "reason": reason,
    }


def normalize_cluster_software(
    raw: Any, endpoint: str, observed_at: str,
) -> dict[str, Any]:
    """Normalize exact version strings without retaining node identifiers."""
    stamp = _observed_at(observed_at)
    source = _source(endpoint)
    if stamp is None or source["endpoint_identity"] is None:
        return _unavailable(
            endpoint, stamp, "RPC endpoint or observation time is invalid.",
        )
    if not isinstance(raw, list) or not raw or len(raw) > MAX_NODE_RECORDS:
        return _unavailable(
            endpoint, stamp, "getClusterNodes returned no bounded node list.",
        )

    pubkeys: set[str] = set()
    versions: Counter[str] = Counter()
    unknown = 0
    for row in raw:
        if not isinstance(row, dict):
            return _unavailable(endpoint, stamp, "getClusterNodes returned a malformed node row.")
        pubkey = row.get("pubkey")
        if (not isinstance(pubkey, str)
                or NODE_PUBKEY_PATTERN.fullmatch(pubkey) is None
                or pubkey in pubkeys):
            return _unavailable(
                endpoint, stamp,
                "getClusterNodes returned an invalid or duplicate node pubkey.",
            )
        pubkeys.add(pubkey)
        version = row.get("version")
        if version is None:
            unknown += 1
        elif (not isinstance(version, str) or version != version.strip()
              or not version or len(version) > MAX_VERSION_LENGTH):
            return _unavailable(
                endpoint, stamp, "getClusterNodes returned an invalid software version.",
            )
        else:
            versions[version] += 1

    observed = len(pubkeys)
    reported = observed - unknown
    ranked = sorted(versions.items(), key=lambda item: (-item[1], item[0]))
    published = ranked[:MAX_VERSION_GROUPS]
    other_reported = sum(count for _, count in ranked[MAX_VERSION_GROUPS:])
    return {
        "available": True,
        "observed_at": stamp,
        "observed_node_count": observed,
        "version_reported_node_count": reported,
        "unknown_version_node_count": unknown,
        "version_coverage_pct": round(100 * reported / observed, 2),
        "distinct_reported_version_count": len(ranked),
        "published_version_group_count": len(published),
        "other_reported_version_node_count": other_reported,
        "versions": [
            {
                "version": version,
                "node_count": count,
                "share_of_observed_nodes_pct": round(100 * count / observed, 3),
            }
            for version, count in published
        ],
        "source": source,
    }


def validate_cluster_software(
    section: Any, endpoint_reference: Any, collected_at: Any,
) -> list[str]:
    """Return semantic failures for one normalized public source section."""
    errors: list[str] = []
    if not isinstance(section, dict):
        return ["cluster_software must be an object"]
    available = section.get("available")
    expected_fields = SECTION_FIELDS | ({"reason"} if available is False else set())
    if set(section) != expected_fields:
        errors.append("cluster_software must contain exactly the contracted fields")
    if not isinstance(available, bool):
        errors.append("cluster_software.available must be boolean")

    source = section.get("source")
    if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        errors.append("cluster_software.source must contain exactly the contracted fields")
    else:
        if source.get("method") != "getClusterNodes":
            errors.append("cluster_software.source.method must be getClusterNodes")
        if source.get("population") != POPULATION or source.get("weighting") != WEIGHTING:
            errors.append("cluster_software.source population or weighting is invalid")
        if (not isinstance(endpoint_reference, dict)
                or source.get("endpoint") != endpoint_reference.get("endpoint")
                or source.get("endpoint_identity")
                != endpoint_reference.get("endpoint_identity")):
            errors.append("cluster_software.source must match the snapshot RPC endpoint")

    observed_at = _observed_at(section.get("observed_at"))
    publication_at = _observed_at(collected_at)
    if section.get("observed_at") is not None and observed_at is None:
        errors.append("cluster_software.observed_at must be offset-aware ISO-8601")
    if publication_at is None:
        errors.append("cluster_software requires a valid snapshot collection time")
    elif observed_at is not None:
        observed_stamp = datetime.fromisoformat(observed_at)
        publication_stamp = datetime.fromisoformat(publication_at)
        if observed_stamp > publication_stamp:
            errors.append("cluster_software.observed_at cannot follow collected_at")

    versions = section.get("versions")
    if available is False:
        if observed_at is None and section.get("observed_at") is not None:
            pass  # The malformed timestamp failure above is already precise.
        if any(section.get(field) is not None for field in COUNT_FIELDS):
            errors.append("unavailable cluster_software counts must be null")
        if section.get("version_coverage_pct") is not None:
            errors.append("unavailable cluster_software coverage must be null")
        if versions != []:
            errors.append("unavailable cluster_software versions must be empty")
        reason = section.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("unavailable cluster_software requires a reason")
        return errors

    if available is not True:
        return errors
    if observed_at is None:
        errors.append("available cluster_software requires an observation time")

    counts: dict[str, int | None] = {}
    for field in COUNT_FIELDS:
        value = section.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_NODE_RECORDS:
            errors.append(f"cluster_software.{field} must be a bounded nonnegative integer")
            counts[field] = None
        else:
            counts[field] = value
    observed = counts["observed_node_count"]
    reported = counts["version_reported_node_count"]
    unknown = counts["unknown_version_node_count"]
    distinct = counts["distinct_reported_version_count"]
    published_count = counts["published_version_group_count"]
    other = counts["other_reported_version_node_count"]
    if observed is not None and observed <= 0:
        errors.append("cluster_software.observed_node_count must be positive")
    if None not in (observed, reported, unknown) and reported + unknown != observed:
        errors.append("reported and unknown node counts must equal observed nodes")

    coverage = section.get("version_coverage_pct")
    if (not isinstance(coverage, (int, float)) or isinstance(coverage, bool)
            or coverage < 0 or coverage > 100 or not math.isfinite(float(coverage))):
        errors.append("cluster_software.version_coverage_pct must be finite from 0 to 100")
    elif observed and reported is not None:
        expected = round(100 * reported / observed, 2)
        if abs(float(coverage) - expected) > 1e-6:
            errors.append("cluster_software.version_coverage_pct must match its counts")

    if not isinstance(versions, list):
        errors.append("cluster_software.versions must be a list")
        rows: list[dict[str, Any]] = []
    else:
        rows = [row for row in versions if isinstance(row, dict)]
        if len(rows) != len(versions):
            errors.append("cluster_software.versions contains a non-object row")
    if len(rows) > MAX_VERSION_GROUPS:
        errors.append("cluster_software.versions exceeds its publication bound")
    if published_count is not None and published_count != len(rows):
        errors.append("published version group count must equal len(versions)")
    if distinct is not None:
        expected_published = min(distinct, MAX_VERSION_GROUPS)
        if published_count is not None and published_count != expected_published:
            errors.append("published version group count must match distinct versions")

    seen_versions: set[str] = set()
    row_counts = 0
    sortable: list[tuple[str, int]] = []
    for index, row in enumerate(rows):
        if set(row) != VERSION_FIELDS:
            errors.append(f"cluster_software.versions[{index}] has uncontracted fields")
        version = row.get("version")
        count = row.get("node_count")
        share = row.get("share_of_observed_nodes_pct")
        if (not isinstance(version, str) or not version
                or version != version.strip() or len(version) > MAX_VERSION_LENGTH):
            errors.append(f"cluster_software.versions[{index}].version is invalid")
        elif version in seen_versions:
            errors.append("cluster_software.versions contains duplicate versions")
        else:
            seen_versions.add(version)
        valid_count = (type(count) is int and 0 < count <= MAX_NODE_RECORDS
                       and (observed is None or count <= observed))
        if not valid_count:
            errors.append(f"cluster_software.versions[{index}].node_count must be positive")
        else:
            row_counts += count
            if isinstance(version, str):
                sortable.append((version, count))
        if (not isinstance(share, (int, float)) or isinstance(share, bool)
                or share < 0 or share > 100 or not math.isfinite(float(share))):
            errors.append(
                f"cluster_software.versions[{index}].share_of_observed_nodes_pct is invalid"
            )
        elif observed and valid_count:
            expected_share = round(100 * count / observed, 3)
            if abs(float(share) - expected_share) > 1e-6:
                errors.append(f"cluster_software.versions[{index}] share must match its count")
    if sortable != sorted(sortable, key=lambda item: (-item[1], item[0])):
        errors.append("cluster_software.versions must be sorted by count then version")
    if None not in (reported, other) and row_counts + other != reported:
        errors.append("published and other version counts must equal reported nodes")
    if None not in (distinct, published_count, other):
        if distinct <= MAX_VERSION_GROUPS and other != 0:
            errors.append("other version count must be zero without truncated groups")
        if distinct > MAX_VERSION_GROUPS and other < distinct - published_count:
            errors.append("other version count cannot cover its omitted distinct groups")
    return errors
