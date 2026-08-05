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
import statistics
import sys
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Two prior snapshots is the floor for a median that means anything.
DEFAULT_MIN_HISTORY = 3

SEVERITIES = ("critical", "warning", "info")

# Thresholds. Deliberately explicit and in one place so an operator can tune
# them without reading the detector code.
THRESHOLDS = {
    "tps_drop_pct": 40.0,        # TPS below baseline by this much
    "tps_spike_pct": 60.0,       # TPS above baseline by this much
    "slot_time_secs": 0.60,      # network targets 0.40s
    "delinquent_pct": 5.0,       # absolute share of delinquent validators
    "delinquent_jump_pct": 2.0,  # percentage-point jump vs baseline
    "supply_move_pct": 1.0,      # circulating supply change vs baseline
    "stake_move_pct": 5.0,       # active stake change vs baseline
}


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


def _series(history: list[dict[str, Any]], *keys: str) -> list[float]:
    out = []
    for snapshot in history:
        value = _path(snapshot, *keys)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


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

def detect_health(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _path(latest, "network", "healthy") is True:
        return []
    was_healthy = bool(history) and _path(history[-1], "network", "healthy") is True
    return [finding(
        "critical", "network_unhealthy", "Network reports unhealthy",
        "getHealth did not return ok." + (" It was healthy at the previous snapshot." if was_healthy else ""),
        _path(latest, "network", "health_raw"),
    )]


def detect_tps(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = _path(latest, "performance", "latest_tps")
    baseline = _baseline(_series(history, "performance", "latest_tps"))
    if not isinstance(current, (int, float)) or baseline is None or baseline == 0:
        return []

    change = 100 * (current - baseline) / baseline
    if change <= -THRESHOLDS["tps_drop_pct"]:
        return [finding(
            "critical", "tps_drop", "Transaction throughput dropped sharply",
            f"TPS is {abs(change):.1f}% below the {len(history)}-snapshot median.",
            round(current, 2), round(baseline, 2),
        )]
    if change >= THRESHOLDS["tps_spike_pct"]:
        return [finding(
            "info", "tps_spike", "Transaction throughput spiked",
            f"TPS is {change:.1f}% above the {len(history)}-snapshot median.",
            round(current, 2), round(baseline, 2),
        )]
    return []


def detect_slot_time(latest: dict[str, Any], _history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = _path(latest, "performance", "mean_slot_time_secs")
    if not isinstance(current, (int, float)) or current <= THRESHOLDS["slot_time_secs"]:
        return []
    return [finding(
        "warning", "slow_slots", "Slot times are slower than target",
        f"Mean slot time {current:.3f}s exceeds the {THRESHOLDS['slot_time_secs']}s threshold "
        "(network targets 0.400s).",
        current, 0.400,
    )]


def detect_delinquency(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = _path(latest, "validators", "delinquent_pct")
    if not isinstance(current, (int, float)):
        return []

    out = []
    if current >= THRESHOLDS["delinquent_pct"]:
        out.append(finding(
            "critical", "delinquency_high", "Validator delinquency is elevated",
            f"{current:.2f}% of validators are delinquent, at or above the "
            f"{THRESHOLDS['delinquent_pct']}% threshold.",
            current,
        ))

    baseline = _baseline(_series(history, "validators", "delinquent_pct"))
    if baseline is not None and current - baseline >= THRESHOLDS["delinquent_jump_pct"]:
        out.append(finding(
            "warning", "delinquency_jump", "Validator delinquency jumped",
            f"Up {current - baseline:.2f} percentage points against the "
            f"{len(history)}-snapshot median.",
            current, round(baseline, 2),
        ))
    return out


def _relative_move(
    latest: dict[str, Any], history: list[dict[str, Any]], keys: tuple[str, ...],
    threshold: float, code: str, title: str, unit: str,
) -> list[dict[str, Any]]:
    current = _path(latest, *keys)
    baseline = _baseline(_series(history, *keys))
    if not isinstance(current, (int, float)) or baseline is None or baseline == 0:
        return []
    change = 100 * (current - baseline) / baseline
    if abs(change) < threshold:
        return []
    return [finding(
        "warning", code, title,
        f"{'Up' if change > 0 else 'Down'} {abs(change):.2f}% against the "
        f"{len(history)}-snapshot median ({unit}).",
        round(current, 2), round(baseline, 2),
    )]


def detect_supply(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("supply", "circulating_sol"),
        THRESHOLDS["supply_move_pct"], "supply_move", "Circulating supply moved", "SOL",
    )


def detect_stake(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _relative_move(
        latest, history, ("validators", "active_stake_sol"),
        THRESHOLDS["stake_move_pct"], "stake_move", "Active stake moved", "SOL",
    )


def detect_stalled_epoch(latest: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slot must advance between snapshots. If it doesn't, collection or the chain is stuck."""
    if not history:
        return []
    current = _path(latest, "network", "slot")
    previous = _path(history[-1], "network", "slot")
    if not isinstance(current, int) or not isinstance(previous, int) or current > previous:
        return []
    return [finding(
        "critical", "slot_stalled", "Slot did not advance",
        "The current slot is not ahead of the previous snapshot — the chain or "
        "the collector is stuck.",
        current, previous,
    )]


DETECTORS = (
    detect_health,
    detect_tps,
    detect_slot_time,
    detect_delinquency,
    detect_supply,
    detect_stake,
    detect_stalled_epoch,
)


def analyse(
    snapshots: list[dict[str, Any]], min_history: int = DEFAULT_MIN_HISTORY,
) -> dict[str, Any]:
    """Run every detector against the newest snapshot, using the rest as baseline."""
    if not snapshots:
        return {
            "status": "no_data",
            "message": "No snapshots found. Run collect.py first.",
            "findings": [],
            "snapshots_analysed": 0,
        }

    latest = snapshots[-1]
    history = snapshots[:-1]

    if len(history) < min_history:
        # The distinction that matters: no baseline is not the same as all-clear.
        return {
            "status": "insufficient_history",
            "message": (
                f"{len(history)} prior snapshot(s); {min_history} needed before "
                "anomalies can be judged. Absence of findings here means no baseline, "
                "not a healthy network."
            ),
            "findings": [],
            "snapshots_analysed": len(snapshots),
            "baseline_size": len(history),
            "min_history": min_history,
            "collected_at": latest.get("collected_at"),
        }

    findings: list[dict[str, Any]] = []
    for detector in DETECTORS:
        findings.extend(detector(latest, history))

    findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))

    return {
        "status": "ok",
        "findings": findings,
        "snapshots_analysed": len(snapshots),
        "baseline_size": len(history),
        "collected_at": latest.get("collected_at"),
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect anomalies across Solana snapshots.")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = analyse(load_history(args.snapshots), args.min_history)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if result["status"] != "ok":
        print(result["message"])
        return 0

    if not result["findings"]:
        print(f"No anomalies across {result['snapshots_analysed']} snapshots "
              f"(baseline {result['baseline_size']}).")
        return 0

    print(f"{len(result['findings'])} finding(s) against a {result['baseline_size']}-snapshot baseline:\n")
    for item in result["findings"]:
        print(f"  [{item['severity'].upper()}] {item['title']}")
        print(f"      {item['detail']}")
        print(f"      observed={item['observed']} baseline={item['baseline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
