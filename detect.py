#!/usr/bin/env python3
"""Detect anomalies across accumulated snapshots.

Standard library only. Pure functions over a list of snapshots — no network,
no clock. Because `collect.py` writes an append-only history, this needs no
new data source and no new infrastructure: the history is already on disk.

    python3 detect.py                 # analyse snapshots/, print findings
    python3 detect.py --json          # machine-readable
    python3 detect.py --min-history 5 # require more baseline before judging

Design note: with too little history there is no baseline, and the honest
answer is "I cannot tell yet" — never "nothing is wrong". `analyse` returns
status `insufficient_history` in that case, and the report says so.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import facts

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Two prior snapshots is the floor for a median that means anything.
DEFAULT_MIN_HISTORY = 3

# Same floor, but per metric: a value eligible in fewer snapshots than this
# has no baseline, and silence about that metric is not evidence of health.
DEFAULT_MIN_BASELINE = DEFAULT_MIN_HISTORY

SEVERITIES = ("critical", "warning", "info")

# Thresholds. Deliberately explicit and in one place so an operator can tune
# them without reading the detector code.
THRESHOLDS = {
    "tps_drop_pct": 40.0,        # TPS below baseline by this much
    "tps_spike_pct": 60.0,       # TPS above baseline by this much
    "slot_time_secs": 0.60,      # this report's slow-slot alert threshold
    "delinquent_pct": 5.0,       # absolute share of delinquent validators
    "delinquent_jump_pct": 2.0,  # percentage-point jump vs baseline
    "supply_move_pct": 1.0,      # circulating supply change vs baseline
    "stake_move_pct": 5.0,       # active stake change vs baseline
    "sol_price_move_pct": 15.0,  # large market move vs snapshot median
    "tvl_move_pct": 15.0,        # large ecosystem liquidity move
    # Cadence-aware freshness policy: seven hours of slack over the six-hour
    # collection cron, tight enough to catch day-old data (matches the
    # --max-age-seconds the publication gate runs in CI).
    "stale_after_secs": 25200.0,
}

# Where each judged metric lives, so coverage can be reported per metric
# rather than inferred from whatever happened to be present.
METRICS: dict[str, str] = {
    "tps": "latest_tps",
    "slot_time": "mean_slot_time_secs",
    "delinquency": "delinquent_pct",
    "supply": "circulating_sol",
    "stake": "active_stake_sol",
    "sol_price": "price_usd",
    "tvl": "tvl_usd",
}

# Health is a boolean state, not a numeric series, but it obeys the same
# availability/staleness eligibility before it may clear or flag anything.
HEALTH_KEYS = ("network", "healthy")


def load_history(directory: Path = SNAPSHOT_DIR) -> list[dict[str, Any]]:
    """Read every snapshot, oldest first. `latest.json` is a duplicate — skipped."""
    if not directory.exists():
        return []
    snapshots = []
    for path in sorted(directory.glob("snapshot-*.json")):
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            # One corrupt file must not blind the whole detector.
            continue
    return sorted(snapshots, key=lambda s: s.get("collected_at", ""))


def _path(snapshot: dict[str, Any], *keys: str) -> Any:
    """Safe nested lookup — returns None rather than raising on absent keys."""
    node: Any = snapshot
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _baseline(values: list[float]) -> float | None:
    """Median, which a single outlier cannot drag the way a mean can."""
    return statistics.median(values) if values else None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a `collected_at` ISO timestamp; None when unreadable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# Explicit source freshness states that disqualify a retained number: a value
# the source itself marked stale/missing/unavailable is not evidence.
INELIGIBLE_FRESHNESS_STATES = ("stale", "missing", "unavailable")


def _block_live(node: dict[str, Any]) -> bool:
    """One source block is a live reading, not an unavailable/carried state."""
    return not (
        node.get("available") is False
        or node.get("stale") is True
        or node.get("source_state") == "last_known_good"
        or node.get("freshness") in INELIGIBLE_FRESHNESS_STATES
    )


def source_eligible(snapshot: dict[str, Any], *keys: str) -> bool:
    """Shared eligibility predicate for a source path.

    Every dict along `keys` must be a live source reading: not explicitly
    unavailable, not stale, not a carried-forward last-known-good copy, and
    not carrying a stale/missing/unavailable freshness state. Publication
    coverage, chart extraction, and current-value rendering all judge sources
    through this one predicate so no surface can disagree about what counts
    as current.
    """
    node: Any = snapshot
    for key in keys:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
        if isinstance(node, dict) and not _block_live(node):
            return False
    return True


def _source_eligible(snapshot: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """Every container on the path must be available, fresh, and not carried forward."""
    return source_eligible(snapshot, *keys[:-1])


def _eligible_value(snapshot: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """A metric observation usable under the shared versioned fact contract."""
    schedule = snapshot.get("collection_schedule")
    key = facts.collection_source_key(keys)
    clock = schedule.get(key) if isinstance(schedule, dict) else None
    if isinstance(clock, dict):
        success = _parse_timestamp(clock.get("last_success_at"))
        publication = _parse_timestamp(snapshot.get("collected_at"))
        interval = clock.get("interval_seconds")
        if (clock.get("state") == "failed" or success is None or publication is None
                or type(interval) is not int
                or not 0 <= (publication - success).total_seconds() <= interval):
            return False
    metric_id = next((name for name, spec in facts.METRICS.items()
                      if spec["path"] == keys), None)
    return bool(metric_id and facts.eligible(facts.fact_from_snapshot(snapshot, metric_id)))


def _current_value(latest: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """The latest value for a metric, or None when it is not eligible evidence."""
    if not _eligible_value(latest, keys):
        return None
    metric_id = next(name for name, spec in facts.METRICS.items() if spec["path"] == keys)
    return facts.fact_from_snapshot(latest, metric_id)["value"]


def metric_evidence(
    history: list[dict[str, Any]], metric_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return raw, eligible, and cadence-selected canonical metric facts."""
    raw = [facts.fact_from_snapshot(snapshot, metric_id) for snapshot in history]
    eligible = [item for item in raw if facts.eligible(item)]
    return {
        "raw": raw,
        "eligible": eligible,
        "cadence": facts.cadence_eligible(eligible),
    }


def _baseline_values(history: list[dict[str, Any]], keys: tuple[str, ...]) -> list[float]:
    metric_id = next(name for name, spec in facts.METRICS.items() if spec["path"] == keys)
    observations = metric_evidence(history, metric_id)["cadence"]
    return [float(item["value"]) for item in observations]


def health_eligible(snapshot: dict[str, Any]) -> bool:
    """A usable health state: a raw boolean eligible under the canonical fact contract."""
    value = _path(snapshot, *HEALTH_KEYS)
    return (
        isinstance(value, bool)
        and facts.eligible(facts.fact_from_snapshot(snapshot, "network_healthy"))
    )


def finding(
    severity: str, code: str, title: str, detail: str, observed: Any, baseline: Any = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "observed": observed,
        "baseline": baseline,
    }


# ── detectors ────────────────────────────────────────────────────────────────

def detect_health(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    if not health_eligible(latest):
        # An unavailable or carried-forward health field is not evidence either
        # way: it cannot read as healthy, and it cannot claim an outage.
        return [finding(
            "warning", "network_health_unavailable", "RPC endpoint health is unavailable",
            "getHealth did not record a usable RPC endpoint health state (missing, "
            "unavailable, or carried-forward last-known-good); this does not establish "
            "network-wide health or an outage.",
            _path(latest, "network", "health_raw"),
        )]
    state = _path(latest, "network", "healthy")
    if state is True:
        return []
    was_healthy = (
        bool(history)
        and health_eligible(history[-1])
        and _path(history[-1], "network", "healthy") is True
    )
    return [finding(
        "critical", "network_unhealthy", "RPC endpoint reports unhealthy",
        "getHealth did not return ok for the recorded RPC endpoint."
        + (" That endpoint was healthy at the previous snapshot." if was_healthy else ""),
        _path(latest, "network", "health_raw"),
    )]


def detect_tps(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    current = _current_value(latest, ("performance", "latest_tps"))
    values = _baseline_values(history, ("performance", "latest_tps"))
    baseline = _baseline(values)
    if current is None or len(values) < min_baseline or baseline is None or baseline == 0:
        return []

    change = 100 * (current - baseline) / baseline
    if change <= -THRESHOLDS["tps_drop_pct"]:
        return [finding(
            "critical", "tps_drop", "Transaction throughput dropped sharply",
            f"TPS is {abs(change):.1f}% below the {len(values)}-snapshot median.",
            round(current, 2), round(baseline, 2),
        )]
    if change >= THRESHOLDS["tps_spike_pct"]:
        return [finding(
            "info", "tps_spike", "Transaction throughput spiked",
            f"TPS is {change:.1f}% above the {len(values)}-snapshot median.",
            round(current, 2), round(baseline, 2),
        )]
    return []


def detect_slot_time(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    current = _current_value(latest, ("performance", "mean_slot_time_secs"))
    if current is None or current <= THRESHOLDS["slot_time_secs"]:
        return []
    threshold = THRESHOLDS["slot_time_secs"]
    return [finding(
        "warning", "slow_slots", "Mean slot time exceeded this report's alert threshold",
        f"Mean slot time {current:.3f}s exceeds this report's {threshold:.2f}s "
        "slow-slot alert threshold.",
        current, threshold,
    )]


def detect_delinquency(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    current = _current_value(latest, ("validators", "delinquent_pct"))
    if current is None:
        return []

    out = []
    if current >= THRESHOLDS["delinquent_pct"]:
        out.append(finding(
            "critical", "delinquency_high", "Validator delinquency is elevated",
            f"{current:.2f}% of validators are delinquent, at or above the "
            f"{THRESHOLDS['delinquent_pct']}% threshold.",
            current,
        ))

    values = _baseline_values(history, ("validators", "delinquent_pct"))
    baseline = _baseline(values)
    if len(values) >= min_baseline and baseline is not None and current - baseline >= THRESHOLDS["delinquent_jump_pct"]:
        out.append(finding(
            "warning", "delinquency_jump", "Validator delinquency jumped",
            f"Up {current - baseline:.2f} percentage points against the "
            f"{len(values)}-snapshot median.",
            current, round(baseline, 2),
        ))
    return out


def _relative_move(
    latest: dict[str, Any], history: list[dict[str, Any]], keys: tuple[str, ...],
    threshold: float, code: str, title: str, unit: str,
    *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    current = _current_value(latest, keys)
    values = _baseline_values(history, keys)
    baseline = _baseline(values)
    if current is None or len(values) < min_baseline or baseline is None or baseline == 0:
        return []
    change = 100 * (current - baseline) / baseline
    if abs(change) < threshold:
        return []
    return [finding(
        "warning", code, title,
        f"{'Up' if change > 0 else 'Down'} {abs(change):.2f}% against the "
        f"{len(values)}-snapshot median ({unit}).",
        round(current, 2), round(baseline, 2),
    )]


def detect_supply(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("supply", "circulating_sol"),
        THRESHOLDS["supply_move_pct"], "supply_move", "Circulating supply moved", "SOL",
        min_baseline=min_baseline,
    )


def detect_stake(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("validators", "active_stake_sol"),
        THRESHOLDS["stake_move_pct"], "stake_move", "Active stake moved", "SOL",
        min_baseline=min_baseline,
    )


def detect_sol_price(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("economics", "price", "price_usd"),
        THRESHOLDS["sol_price_move_pct"], "sol_price_move", "SOL price moved sharply", "USD",
        min_baseline=min_baseline,
    )


def detect_tvl(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("economics", "tvl", "tvl_usd"),
        THRESHOLDS["tvl_move_pct"], "tvl_move", "Solana TVL moved sharply", "USD",
        min_baseline=min_baseline,
    )


def detect_stalled_epoch(
    latest: dict[str, Any], history: list[dict[str, Any]], *, min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[dict[str, Any]]:
    """Flag non-advancing slot evidence only across the report cadence."""
    if not history:
        return []
    current = _path(latest, "network", "slot")
    previous_snapshot = history[-1]
    previous = _path(previous_snapshot, "network", "slot")
    if (
        isinstance(current, bool) or not isinstance(current, int)
        or isinstance(previous, bool) or not isinstance(previous, int)
    ):
        return []
    current_fact = facts.fact_from_snapshot(latest, "network_slot")
    previous_fact = facts.fact_from_snapshot(previous_snapshot, "network_slot")
    if not facts.eligible(current_fact) or not facts.eligible(previous_fact) or current > previous:
        return []
    current_at = _parse_timestamp(latest.get("collected_at"))
    previous_at = _parse_timestamp(previous_snapshot.get("collected_at"))
    if current_at is None or previous_at is None:
        return []
    elapsed_seconds = (current_at - previous_at).total_seconds()
    if elapsed_seconds < facts.MIN_PRIOR_SPACING_SECONDS:
        return []
    return [finding(
        "critical", "slot_stalled", "Recorded slot did not advance",
        f"Across RPC observations {elapsed_seconds / 3600:.1f} hours apart, the "
        "current slot is not ahead of the previous observation. This may reflect "
        "endpoint, collector, or network conditions; it does not by itself "
        "establish a network-wide outage.",
        current, previous,
    )]


DETECTORS = (
    detect_health,
    detect_tps,
    detect_slot_time,
    detect_delinquency,
    detect_supply,
    detect_stake,
    detect_sol_price,
    detect_tvl,
    detect_stalled_epoch,
)


def analyse(
    snapshots: list[dict[str, Any]], min_history: int = DEFAULT_MIN_HISTORY,
    min_baseline: int | None = None,
    *, now: datetime | str | None = None, max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Run every detector against the newest snapshot, using the rest as baseline.

    Baselines use the shared per-metric fact adapters, so unchanged semantics
    survive schema changes while corrected metrics remain explicit gaps. Rapid
    observations are excluded by the five-hour spacing rule. Freshness and history are
    evaluated together so a snapshot can report both limitations at once.
    """
    if min_baseline is None:
        min_baseline = min_history
    snapshots = facts.publication_history(snapshots)
    if not snapshots:
        return {
            "status": "no_data",
            "message": "No snapshots found. Run collect.py first.",
            "findings": [],
            "snapshots_analysed": 0,
        }

    latest = snapshots[-1]
    history = snapshots[:-1]

    coverage: dict[str, dict[str, Any]] = {}
    for code, metric_id in METRICS.items():
        evidence = metric_evidence(history, metric_id)
        raw_eligible = evidence["eligible"]
        priors = evidence["cadence"]
        coverage[code] = {
            "evaluated": len(priors),
            "eligible_priors": len(priors),
            "raw_eligible": len(raw_eligible),
            "cadence_excluded": len(raw_eligible) - len(priors),
            "unavailable": len(history) - len(raw_eligible),
            "current_eligible": facts.eligible(
                facts.fact_from_snapshot(latest, metric_id)
            ),
            "insufficient_baseline": len(priors) < min_baseline,
        }
    health_evidence = metric_evidence(history, "network_healthy")
    health_eligible_count = len(health_evidence["eligible"])
    coverage["network_health"] = {
        "evaluated": health_eligible_count,
        "eligible_priors": None,
        "raw_eligible": health_eligible_count,
        "cadence_excluded": 0,
        "unavailable": len(history) - health_eligible_count,
        "current_eligible": health_eligible(latest),
        "insufficient_baseline": False,
    }
    baseline_size = max(
        (entry["eligible_priors"] or 0 for entry in coverage.values()), default=0,
    )

    reference = _parse_timestamp(now) if isinstance(now, str) else (
        now if now is not None else datetime.now(timezone.utc)
    )
    max_age = THRESHOLDS["stale_after_secs"] if max_age_seconds is None else max_age_seconds
    collected = _parse_timestamp(latest.get("collected_at"))
    age_seconds = None if (reference is None or collected is None) else (
        (reference - collected).total_seconds()
    )
    conditions = []
    if age_seconds is None or age_seconds < 0 or age_seconds > max_age:
        if age_seconds is None:
            conditions.append("latest snapshot has no readable collected_at")
        elif age_seconds < 0:
            conditions.append(f"latest snapshot is {-age_seconds:.0f}s in the future")
        else:
            conditions.append(
                f"latest snapshot is {age_seconds:.0f}s old; freshness limit is {max_age:.0f}s"
            )
    if baseline_size < min_history:
        conditions.append(
            f"only {baseline_size} time-eligible prior observation(s); {min_history} needed"
        )
    stale = age_seconds is None or age_seconds < 0 or age_seconds > max_age
    findings: list[dict[str, Any]] = []
    if not stale:
        for detector in DETECTORS:
            findings.extend(detector(latest, history, min_baseline=min_baseline))
        findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    if conditions:
        return {
            "status": "stale_snapshot" if stale else "insufficient_history",
            "message": (
                f"Cannot judge anomalies: {'; '.join(conditions)}. "
                + (
                    "Absence of findings here means stale evidence, not a healthy network."
                    if stale else
                    "Current-state findings remain reported, but baseline-dependent rules "
                    "are not assessable; absence of other findings is not a healthy network verdict."
                )
            ),
            "findings": findings,
            "snapshots_analysed": len(snapshots),
            "baseline_size": baseline_size,
            "min_history": min_history,
            "min_baseline": min_baseline,
            "collected_at": latest.get("collected_at"),
            "age_seconds": age_seconds,
            "max_age_seconds": max_age,
            "conditions": conditions,
            "coverage": coverage,
            "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
        }

    gaps = sorted(
        code for code, entry in coverage.items()
        if entry["insufficient_baseline"] or not entry["current_eligible"]
    )

    return {
        "status": "partial_coverage" if gaps else "ok",
        "message": (
            f"No eligible baseline for {', '.join(gaps)}; absence of findings for "
            "those metrics is not evidence of health."
            if gaps else ""
        ),
        "findings": findings,
        "snapshots_analysed": len(snapshots),
        "baseline_size": baseline_size,
        "min_history": min_history,
        "min_baseline": min_baseline,
        "collected_at": latest.get("collected_at"),
        "coverage": coverage,
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect anomalies across Solana snapshots.")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    parser.add_argument("--min-baseline", type=int, default=None,
                        help="eligible values required per metric; defaults to --min-history")
    parser.add_argument("--max-age-seconds", type=float, default=THRESHOLDS["stale_after_secs"],
                        help="latest snapshot older than this is stale, never ok")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = analyse(
        load_history(args.snapshots), args.min_history, args.min_baseline,
        now=datetime.now(timezone.utc), max_age_seconds=args.max_age_seconds,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if result["status"] != "ok":
        print(result["message"])
        if result["status"] not in ("partial_coverage", "insufficient_history"):
            return 0

    if not result["findings"]:
        if result["status"] != "ok":
            return 0
        print(f"No anomalies across {result['snapshots_analysed']} snapshots "
              f"(baseline {result['baseline_size']}).")
        return 0

    print(f"{len(result['findings'])} finding(s) with {result['baseline_size']} eligible prior snapshot(s):\n")
    for item in result["findings"]:
        print(f"  [{item['severity'].upper()}] {item['title']}")
        print(f"      {item['detail']}")
        print(f"      observed={item['observed']} baseline={item['baseline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
