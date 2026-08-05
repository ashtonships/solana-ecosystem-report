#!/usr/bin/env python3
"""What changed between the two newest snapshots.

Standard library only. A pure function over two snapshot dicts — no network,
no clock, no randomness. The same two snapshots always produce byte-identical
output, which is the whole point: a narrative layer that cannot drift is a
narrative layer that cannot be caught inventing something.

    python3 delta.py            # human-readable
    python3 delta.py --json     # machine-readable

Three rules carry this module:

**A metric missing from either side is not a change.** It is reported as
*not comparable*, with the side that is missing named. Treating an absent
value as zero would manufacture a -100% move out of an outage; treating it as
unchanged would hide one. Neither is true, so neither is printed.

**Movement is judged against a per-metric threshold, not eyeballed.** The
thresholds sit in the metric table below so an operator can see exactly what
counts as "moved" for each series.

**Every moved metric carries its own "why it matters" and "what to verify".**
Both are static text attached to the metric, not generated per run — the tool
explains what a metric means, and never narrates what it thinks happened.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import detect

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Metric table. Everything the delta section knows lives here — path into the
# snapshot, how to describe it, how much movement is worth reporting, and the
# two lines of context printed when it does move.
#
#   move_pct   relative move (%) at or above which the metric counts as moved
#   move_abs   absolute move at or above which it counts as moved; used on its
#              own for metrics where a percentage is meaningless (a count of
#              validators, a coefficient) and as the floor when a previous
#              value of zero makes a percentage undefined
#   basis      "measured" (read directly off the wire) or "sampled" (derived
#              from a sample or extrapolation). Rendered distinctly, because a
#              6% move in an extrapolated figure is not the same evidence as a
#              6% move in a measured one.
METRICS: list[dict[str, Any]] = [
    {
        "key": "latest_tps",
        "label": "Latest TPS",
        "path": ("performance", "latest_tps"),
        "unit": " TPS",
        "decimals": 2,
        "move_pct": 10.0,
        "basis": "measured",
        "why": "Throughput is the headline liveness signal; sustained drops precede "
               "degraded user experience before any validator goes delinquent.",
        "verify": "getRecentPerformanceSamples on the same endpoint, or a second public RPC.",
    },
    {
        "key": "mean_slot_time_secs",
        "label": "Mean slot time",
        "path": ("performance", "mean_slot_time_secs"),
        "unit": "s",
        "decimals": 3,
        "move_pct": 5.0,
        "basis": "measured",
        "why": "The network targets 0.400s. Slot time rising is the earliest visible "
               "sign of block-production strain.",
        "verify": "Compare against the 0.400s target and the slow_slots anomaly threshold.",
    },
    {
        "key": "delinquent_pct",
        "label": "Validator delinquency",
        "path": ("validators", "delinquent_pct"),
        "unit": "%",
        "decimals": 2,
        "move_pct": 10.0,
        "move_abs": 0.25,
        "basis": "measured",
        "why": "Delinquent validators are not voting. A rising share means stake is "
               "dropping out of consensus.",
        "verify": "getVoteAccounts — compare the delinquent list against the active one.",
    },
    {
        "key": "active_count",
        "label": "Active validators",
        "path": ("validators", "active_count"),
        "unit": "",
        "decimals": 0,
        "move_abs": 5,
        "basis": "measured",
        "why": "The size of the voting set. Large swings mean operators joining or "
               "leaving, not routine churn.",
        "verify": "getVoteAccounts current list length.",
    },
    {
        "key": "nakamoto_coefficient",
        "label": "Nakamoto coefficient",
        "path": ("validators", "nakamoto_coefficient"),
        "unit": " validators",
        "decimals": 0,
        "move_abs": 1,
        "basis": "measured",
        "why": "How few validators it takes to control a third of stake. Down is more "
               "concentrated, which is worse for censorship resistance.",
        "verify": "Recompute from the getVoteAccounts stake distribution.",
    },
    {
        "key": "active_stake_sol",
        "label": "Active stake",
        "path": ("validators", "active_stake_sol"),
        "unit": " SOL",
        "decimals": 2,
        "move_pct": 1.0,
        "basis": "measured",
        "why": "Stake securing the chain. Moves of this size are usually epoch-boundary "
               "activations rather than anything alarming.",
        "verify": "Check whether the epoch number also changed in this delta.",
    },
    {
        "key": "circulating_sol",
        "label": "Circulating supply",
        "path": ("supply", "circulating_sol"),
        "unit": " SOL",
        "decimals": 2,
        "move_pct": 0.25,
        "basis": "measured",
        "why": "Circulating supply moves with unlocks and inflation. A sharp move over "
               "hours is more likely a source problem than a monetary event.",
        "verify": "getSupply with excludeNonCirculatingAccountsList set.",
    },
    {
        "key": "epoch",
        "label": "Epoch",
        "path": ("epoch", "epoch"),
        "unit": "",
        "decimals": 0,
        "move_abs": 1,
        "identifier": True,
        "basis": "measured",
        "why": "An epoch boundary crossed between these two snapshots. Stake activations, "
               "deactivations and commission changes all take effect here.",
        "verify": "getEpochInfo — the boundary explains most stake and commission moves.",
    },
    {
        "key": "price_usd",
        "label": "SOL price",
        "path": ("economics", "price", "price_usd"),
        "unit": " USD",
        "decimals": 2,
        "move_pct": 2.0,
        "basis": "measured",
        "why": "Price is the market's read on everything else on this page, and it is "
               "the multiplier under every USD figure in the report.",
        "verify": "CoinGecko simple/price — keyless, and cross-checkable on any exchange.",
    },
    {
        "key": "tvl_usd",
        "label": "Total value locked",
        "path": ("economics", "tvl", "tvl_usd"),
        "unit": " USD",
        "decimals": 2,
        "move_pct": 2.0,
        "basis": "measured",
        "why": "TVL is denominated in USD, so it moves with the SOL price even when no "
               "capital enters or leaves. Read it beside the price row.",
        "verify": "DeFiLlama historicalChainTvl/Solana — keyless.",
    },
    {
        "key": "stablecoin_usd",
        "label": "Stablecoin supply",
        "path": ("economics", "stablecoins", "stablecoin_usd"),
        "unit": " USD",
        "decimals": 2,
        "move_pct": 1.0,
        "basis": "measured",
        "why": "Unlike TVL, stablecoin float does not move with the SOL price. A change "
               "here is capital actually arriving or leaving.",
        "verify": "DeFiLlama stablecoinchains — the Solana row, peggedUSD only.",
    },
    {
        "key": "dex_volume_24h_usd",
        "label": "DEX volume 24h",
        "path": ("economics", "dex", "volume_24h_usd"),
        "unit": " USD",
        "decimals": 2,
        "move_pct": 10.0,
        "basis": "measured",
        "why": "A trailing 24h window, so consecutive snapshots overlap heavily; only "
               "large moves mean anything at this cadence.",
        "verify": "DeFiLlama overview/dexs/solana total24h.",
    },
    {
        "key": "median_fee_lamports",
        "label": "Median fee (non-vote)",
        "path": ("activity", "fees", "median_lamports"),
        "unit": " lamports",
        "decimals": 0,
        "move_pct": 10.0,
        "basis": "sampled",
        "why": "What it actually costs a person to transact. Above the 5,000-lamport "
               "base fee means priority bidding is live.",
        "verify": "Re-sample block bodies; this is a median over sampled non-vote "
                  "transactions, not a network-wide figure.",
    },
    {
        "key": "estimated_24h_rev_sol",
        "label": "REV 24h (estimated)",
        "path": ("activity", "rev", "estimated_24h_sol"),
        "unit": " SOL",
        "decimals": 2,
        "move_pct": 25.0,
        "basis": "sampled",
        "why": "Extrapolated from a small block sample, so it carries wide sampling "
               "noise; the threshold here is deliberately loose for that reason.",
        "verify": "Check the published 95% interval — consecutive estimates routinely "
                  "overlap even when the point estimates differ.",
    },
]


def _value(snapshot: dict[str, Any], path: tuple[str, ...]) -> float | None:
    """Numeric lookup. Anything absent, non-numeric or boolean reads as None."""
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def _elapsed_seconds(previous: dict[str, Any], current: dict[str, Any]) -> int | None:
    """Seconds between the two collection timestamps, or None if unparseable."""
    try:
        before = datetime.fromisoformat(str(previous.get("collected_at")))
        after = datetime.fromisoformat(str(current.get("collected_at")))
    except (TypeError, ValueError):
        return None
    return int((after - before).total_seconds())


def _moved(spec: dict[str, Any], before: float, after: float) -> tuple[bool, float | None]:
    """Did this metric move enough to report, and by what percentage?

    Returns (moved, change_pct). change_pct is None when the previous value is
    zero — a percentage against zero is undefined, and printing "+inf%" or
    silently substituting 0 would both be lies.
    """
    difference = after - before
    change_pct = (100 * difference / before) if before else None

    if difference == 0:
        return False, change_pct

    threshold_abs = spec.get("move_abs")
    if threshold_abs is not None and abs(difference) >= threshold_abs:
        return True, change_pct

    threshold_pct = spec.get("move_pct")
    if change_pct is not None and threshold_pct is not None:
        if abs(change_pct) >= threshold_pct:
            return True, change_pct
        return False, change_pct

    # No percentage available (previous was zero) and no absolute threshold met:
    # fall back to "any movement counts" only when no absolute floor was given.
    if change_pct is None and threshold_abs is None:
        return True, None
    return False, change_pct


def _round(value: float, decimals: int) -> float | int:
    return int(round(value)) if decimals == 0 else round(value, decimals)


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Deterministic delta between two snapshots. Pure: same input, same output."""
    changes: list[dict[str, Any]] = []
    steady: list[dict[str, Any]] = []
    not_comparable: list[dict[str, Any]] = []

    for spec in METRICS:
        before = _value(previous, spec["path"])
        after = _value(current, spec["path"])

        if before is None or after is None:
            # Never a change. Which side is missing is the useful part.
            if before is None and after is None:
                reason = "not present in either snapshot"
            elif before is None:
                reason = "not present in the earlier snapshot"
            else:
                reason = "not present in the newer snapshot"
            not_comparable.append({
                "key": spec["key"], "label": spec["label"], "reason": reason,
                "previous": _round(before, spec["decimals"]) if before is not None else None,
                "current": _round(after, spec["decimals"]) if after is not None else None,
            })
            continue

        moved, change_pct = _moved(spec, before, after)
        decimals = spec["decimals"]
        entry = {
            "key": spec["key"],
            "label": spec["label"],
            "unit": spec["unit"],
            "basis": spec["basis"],
            "previous": _round(before, decimals),
            "current": _round(after, decimals),
            "change": _round(after - before, decimals),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "direction": "up" if after > before else ("down" if after < before else "flat"),
            "identifier": bool(spec.get("identifier")),
        }
        if moved:
            entry["why_it_matters"] = spec["why"]
            entry["what_to_verify"] = spec["verify"]
            changes.append(entry)
        else:
            steady.append(entry)

    return {
        "status": "ok",
        "previous_collected_at": previous.get("collected_at"),
        "current_collected_at": current.get("collected_at"),
        "elapsed_seconds": _elapsed_seconds(previous, current),
        "changes": changes,
        "steady": steady,
        "not_comparable": not_comparable,
        "counts": {
            "changed": len(changes),
            "steady": len(steady),
            "not_comparable": len(not_comparable),
        },
    }


def analyse(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the two newest snapshots in a history, oldest-first."""
    if len(snapshots) < 2:
        return {
            "status": "insufficient_history",
            "message": (
                f"{len(snapshots)} snapshot(s) on disk; two are needed before anything "
                "can be compared. This is not a statement that nothing changed."
            ),
            "changes": [],
            "steady": [],
            "not_comparable": [],
            "counts": {"changed": 0, "steady": 0, "not_comparable": 0},
        }
    return compare(snapshots[-2], snapshots[-1])


def delta_for(snapshot: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """Delta ending at `snapshot`: history collected after it is dropped.

    Same rule as the anomaly panel. Re-rendering an older snapshot must not
    borrow a newer snapshot's comparison — the page would describe one moment
    with a delta belonging to another.
    """
    cutoff = snapshot.get("collected_at")
    if isinstance(cutoff, str) and cutoff:
        history = [s for s in history if s.get("collected_at", "") <= cutoff]
    return analyse(history)


def format_elapsed(seconds: Any) -> str:
    if not isinstance(seconds, int) or seconds < 0:
        return "an unknown interval"
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare the two newest Solana snapshots.")
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = analyse(detect.load_history(args.snapshots))

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if result["status"] != "ok":
        print(result["message"])
        return 0

    print(f"{result['previous_collected_at']} → {result['current_collected_at']} "
          f"({format_elapsed(result['elapsed_seconds'])})\n")

    if not result["changes"]:
        print(f"No metric moved past its threshold. "
              f"{result['counts']['steady']} metric(s) compared and steady.")
    for item in result["changes"]:
        pct = f" ({item['change_pct']:+.2f}%)" if item["change_pct"] is not None else ""
        basis = " [sampled]" if item["basis"] == "sampled" else ""
        print(f"  {item['label']}{basis}: {item['previous']} → {item['current']}"
              f"{item['unit']}{pct}")
        print(f"      why: {item['why_it_matters']}")
        print(f"      verify: {item['what_to_verify']}")

    if result["not_comparable"]:
        print(f"\nNot comparable ({len(result['not_comparable'])}) — reported as such, "
              "never as a change:")
        for item in result["not_comparable"]:
            print(f"  {item['label']}: {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
