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

**A value is only compared when both sides are trustworthy.** A section that
says it is unavailable, stale, or a last-known-good copy carried forward from
an earlier run disqualifies every metric inside it: those are reported as
*not comparable*, with the cause named, never as steady. Re-publishing a stale
value as "unchanged" would manufacture confidence nothing earned.

**The interval and schema are checked before any metric is read.** Two
snapshots whose collected_at does not parse into comparable UTC instants —
malformed, equal, reversed, or written without an explicit UTC offset — return
an explicit invalid_interval status and publish no comparisons at all.
Snapshots whose schema_version is missing, mistyped, unsupported, or unequal
are refused the same way: numbers collected under different layouts are not
evidence of movement.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import detect
import facts

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Schema versions eligible for snapshot-to-snapshot comparison. Two sources,
# stated apart so neither silently widens the other:
#   * schemas 1-4 are the intentional recorded history already on disk;
#   * schemas 5-9 are the publishable current contract — keep in sync with
#     SUPPORTED_SCHEMA_VERSIONS in pipeline.py (5 introduced the last required
#     section; schema 8 corrects activity and coverage semantics; schema 9 adds
#     editorial fields without changing the registered metric meanings).
SUPPORTED_SCHEMA_VERSIONS = frozenset(range(1, 10))

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
        "unit": "s",
        "decimals": 3,
        "move_pct": 5.0,
        "basis": "measured",
        "why": "Sustained slot-time increases can be an early sign of block-production "
               "strain.",
        "verify": "Compare the measured window with this report's 0.60s slow-slot alert threshold.",
    },
    {
        "key": "delinquent_pct",
        "label": "Validator delinquency",
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
        "unit": " USD",
        "decimals": 2,
        "move_pct": 2.0,
        "basis": "measured",
        "why": "TVL is denominated in USD, so it moves with the SOL price even when no "
               "capital enters or leaves. Read it beside the price row.",
        "verify": "DeFiLlama historicalChainTvl/Solana — keyless.",
    },
    {
        "key": "usd_pegged_circulating_usd",
        "label": "USD-pegged circulating supply",
        "unit": " USD",
        "decimals": 2,
        "move_pct": 1.0,
        "basis": "measured",
        "why": "Unlike TVL, USD-pegged circulating supply does not move with the SOL price. A change "
               "here is capital actually arriving or leaving.",
        "verify": "DeFiLlama stablecoinchains — the Solana row, peggedUSD only.",
    },
    {
        "key": "dex_volume_24h_usd",
        "label": "DEX volume 24h",
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
        "key": "sample_mean_rev_sol",
        "label": "REV over observed window (sample mean)",
        "unit": " SOL",
        "decimals": 2,
        "move_pct": 25.0,
        "basis": "sampled",
        "why": "Estimated from a bounded block sample, so temporal and endpoint sampling "
               "bias make a deliberately loose movement threshold necessary.",
        "verify": "Check the exact observed duration and published sample-mean interval.",
    },
]


def _parse_instant(raw: Any) -> datetime | None:
    """Parse collected_at into an aware UTC instant, or None if unparseable.

    A string without an explicit offset — including a bare date, which
    fromisoformat silently reads as midnight — is refused rather than assumed
    UTC. A guessed offset could quietly reorder two snapshots.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _interval_defect(previous: dict[str, Any], current: dict[str, Any]) -> str | None:
    """Why this pair of timestamps cannot bound a delta, or None if it can."""
    parsed_before = _parse_instant(previous.get("collected_at"))
    parsed_after = _parse_instant(current.get("collected_at"))
    unparseable = [
        side for side, parsed in (("earlier", parsed_before), ("newer", parsed_after))
        if parsed is None
    ]
    if unparseable:
        return ("collected_at does not parse as a timezone-aware ISO-8601 "
                f"instant on the {' and '.join(unparseable)} snapshot; "
                "offset-free and date-only strings are refused")
    if parsed_after == parsed_before:
        return ("both snapshots carry the same collected_at instant; zero time "
                "cannot separate a change from a repeat of the same collection")
    if parsed_after < parsed_before:
        return ("the newer snapshot's collected_at is earlier than the older "
                "one's; comparing them would narrate time running backwards")
    return None


def _schema_version_problem(version: Any) -> str | None:
    """Why one declared schema_version is not comparison-grade, or None."""
    if version is None:
        return "declares no schema_version"
    if isinstance(version, bool) or not isinstance(version, int):
        return f"declares schema_version {version!r}, which is not an integer"
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return (f"declares unsupported schema_version {version}; supported "
                f"{min(SUPPORTED_SCHEMA_VERSIONS)}..{max(SUPPORTED_SCHEMA_VERSIONS)}")
    return None


def _schema_defect(previous: dict[str, Any], current: dict[str, Any]) -> str | None:
    """Undeclared/unsupported schemas are refused; metric adapters handle differences."""
    before_problem = _schema_version_problem(previous.get("schema_version"))
    after_problem = _schema_version_problem(current.get("schema_version"))
    if before_problem or after_problem:
        parts = [
            f"the {side} snapshot {problem}"
            for side, problem in (("earlier", before_problem), ("newer", after_problem))
            if problem
        ]
        return "snapshot schemas are not comparably declared: " + "; ".join(parts)
    return None


def _refused(status: str, message: str,
             previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """A comparison that was not published, and why. No metric rows at all."""
    return {
        "status": status,
        "message": message,
        "previous_collected_at": previous.get("collected_at"),
        "current_collected_at": current.get("collected_at"),
        "elapsed_seconds": None,
        "changes": [],
        "steady": [],
        "not_comparable": [],
        "counts": {"changed": 0, "steady": 0, "not_comparable": 0},
    }


def _section_defect(snapshot: dict[str, Any], path: tuple[str, ...]) -> str | None:
    """Availability/source-state defect in a metric's enclosing sections.

    For ("economics", "price", "price_usd") that means economics and then
    economics.price. A section that is present but marked unavailable, stale,
    past its freshness window, or a last-known-good copy carried forward
    disqualifies everything inside it; the deepest verdict wins because it
    names the closest source. A section that is absent entirely is ignored
    here — absence is worded later, off the value lookup itself.
    """
    defect: str | None = None
    node: Any = snapshot
    dotted = ""
    for key in path[:-1]:
        dotted = f"{dotted}.{key}" if dotted else key
        if not isinstance(node, dict):
            return defect
        node = node.get(key)
        if not isinstance(node, dict):
            return defect
        if node.get("available") is False:
            defect = f"{dotted} marked unavailable"
        elif node.get("stale") is True:
            defect = f"{dotted} stale"
        elif node.get("freshness") == "stale":
            defect = f"{dotted} freshness reported stale"
        elif node.get("freshness") == "missing":
            # A number whose age cannot be established at all: comparing it
            # against anything would imply a freshness nobody verified.
            defect = f"{dotted} has no freshness evidence"
        elif node.get("freshness") == "unavailable":
            defect = f"{dotted} freshness unavailable"
        elif node.get("source_state") == "last_known_good":
            defect = f"{dotted} carried forward as last-known-good"
    return defect


# How to describe a numeric lookup that failed. Missing and invalid are kept
# apart: an outage and a garbage value are different problems with different
# fixes, and a delta section should not flatten them into one word.
_PRESENCE_PHRASES = {
    "missing": "not present in the {side} snapshot",
    "invalid": "present but not a usable number in the {side} snapshot",
}


def _presence_defect(snapshot: dict[str, Any], path: tuple[str, ...]) -> str:
    """Classify why a numeric lookup failed: 'missing', 'invalid', or ''."""
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return "missing"
        node = node[key]
    if node is None:
        return "missing"
    if (isinstance(node, bool) or not isinstance(node, (int, float))
            or not math.isfinite(node)):
        return "invalid"
    return ""


def _value(snapshot: dict[str, Any], path: tuple[str, ...]) -> float | None:
    """Numeric lookup. Anything absent, non-numeric or boolean reads as None."""
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if (isinstance(node, bool) or not isinstance(node, (int, float))
            or not math.isfinite(node)):
        return None
    return float(node)


def _elapsed_seconds(previous: dict[str, Any], current: dict[str, Any]) -> int | None:
    """Seconds between the two collection timestamps, or None if unparseable."""
    parsed_before = _parse_instant(previous.get("collected_at"))
    parsed_after = _parse_instant(current.get("collected_at"))
    if parsed_before is None or parsed_after is None:
        return None
    return int((parsed_after - parsed_before).total_seconds())


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


def _validator_commission_comparison(previous: dict[str, Any],
                                     current: dict[str, Any]) -> dict[str, Any]:
    """Recorded commission changes joined by stable vote-account identity."""
    try:
        before_facts = facts.validator_commission_facts(previous)
        after_facts = facts.validator_commission_facts(current)
    except facts.FactConflictError as exc:
        return {"status": "not_comparable", "reason": str(exc), "changes": []}

    before = {item["subject_id"]: item for item in before_facts
              if item.get("state") == "current"}
    after = {item["subject_id"]: item for item in after_facts
             if item.get("state") == "current"}

    def exact_count(snapshot: dict[str, Any], path: tuple[str, ...]) -> int | None:
        value = facts.lookup(snapshot, path)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    def vote_accounts(snapshot: dict[str, Any]) -> set[str]:
        rows = facts.lookup(snapshot, ("validators", "all_validators"))
        return {
            row["vote_account"] for row in rows if isinstance(row, dict)
            and isinstance(row.get("vote_account"), str) and row["vote_account"]
        } if isinstance(rows, list) else set()

    previous_epoch = exact_count(previous, ("epoch", "epoch"))
    current_epoch = exact_count(current, ("epoch", "epoch"))
    previous_slot = exact_count(previous, ("network", "slot"))
    current_slot = exact_count(current, ("network", "slot"))
    before_accounts, after_accounts = vote_accounts(previous), vote_accounts(current)
    shared_comparable = before.keys() & after.keys()
    base = {
        "previous_snapshot_epoch": previous_epoch,
        "current_snapshot_epoch": current_epoch,
        "previous_snapshot_slot": previous_slot,
        "current_snapshot_slot": current_slot,
        "previous_account_count": len(before_accounts),
        "current_account_count": len(after_accounts),
        "matched_account_count": len(before_accounts & after_accounts),
        "new_account_count": len(after_accounts - before_accounts),
        "missing_account_count": len(before_accounts - after_accounts),
        "previous_comparable_count": len(before),
        "current_comparable_count": len(after),
        "matched_comparable_count": len(shared_comparable),
        "changed_count": 0,
        "changes": [],
    }
    if any(value is None for value in (previous_epoch, current_epoch,
                                       previous_slot, current_slot)):
        return {"status": "not_comparable",
                "reason": "same-run snapshot epoch and slot context is required",
                **base}
    if current_slot <= previous_slot:
        return {"status": "not_comparable",
                "reason": "snapshot slot context is not strictly increasing", **base}
    if current_epoch < previous_epoch:
        return {"status": "not_comparable",
                "reason": "snapshot epoch context regresses", **base}
    missing_sides = [side for side, rows in (("earlier", before), ("newer", after))
                     if not rows]
    if missing_sides:
        return {"status": "not_comparable",
                "reason": ("no current identity-keyed commission observations in the "
                           f"{' and '.join(missing_sides)} snapshot"), **base}

    shared = sorted(shared_comparable)
    if not shared:
        return {"status": "not_comparable",
                "reason": "the snapshots have no shared vote accounts with comparable commissions",
                **base}

    changes = []
    for vote_account in shared:
        old, new = before[vote_account], after[vote_account]
        if old["value"] == new["value"]:
            continue
        changes.append({
            "vote_account": vote_account,
            "previous_identity": old["coverage"]["identity"],
            "current_identity": new["coverage"]["identity"],
            "previous_commission_pct": old["value"],
            "current_commission_pct": new["value"],
            "change_percentage_points": round(new["value"] - old["value"], 6),
        })
    return {"status": "ok", **base, "changed_count": len(changes),
            "changes": changes}


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Deterministic delta between two snapshots. Pure: same input, same output."""
    previous, current = facts.publication_history([previous, current])
    interval_problem = _interval_defect(previous, current)
    if interval_problem:
        return _refused("invalid_interval", interval_problem, previous, current)

    schema_problem = _schema_defect(previous, current)
    if schema_problem:
        return _refused("incompatible_schemas", schema_problem, previous, current)

    changes: list[dict[str, Any]] = []
    steady: list[dict[str, Any]] = []
    not_comparable: list[dict[str, Any]] = []

    for spec in METRICS:
        metric_id = spec["key"]
        metric_spec = facts.METRICS[metric_id]
        path = metric_spec["path"]
        before_fact = facts.fact_from_snapshot(previous, metric_id)
        after_fact = facts.fact_from_snapshot(current, metric_id)
        before = before_fact["value"]
        after = after_fact["value"]

        incompatible = []
        for side, fact in (("earlier", before_fact), ("newer", after_fact)):
            if fact.get("quality"):
                incompatible.append(
                    f"{metric_id} is not semantically compatible with schema "
                    f"{fact.get('source_schema')} in the {side} snapshot"
                )
        if incompatible:
            not_comparable.append({
                "key": metric_id, "label": spec["label"],
                "reason": "; ".join(incompatible),
                "previous": _round(before, spec["decimals"]) if before is not None else None,
                "current": _round(after, spec["decimals"]) if after is not None else None,
            })
            continue

        # Source state is judged before movement is. An unavailable, stale or
        # carried-forward section never becomes a steady comparison, however
        # identical its numbers look.
        source_defects = []
        for side, snapshot in (("earlier", previous), ("newer", current)):
            defect = _section_defect(snapshot, path)
            if defect:
                source_defects.append(f"{defect} in the {side} snapshot")
        if source_defects:
            not_comparable.append({
                "key": spec["key"],
                "label": spec["label"],
                "reason": "; ".join(source_defects),
                "previous": _round(before, spec["decimals"]) if before is not None else None,
                "current": _round(after, spec["decimals"]) if after is not None else None,
            })
            continue

        if before is None or after is None:
            # Never a change. Which side, and whether the value is absent or
            # garbage, is the useful part.
            before_presence = _presence_defect(previous, path)
            after_presence = _presence_defect(current, path)
            if before_presence == after_presence == "missing":
                reason = "not present in either snapshot"
            else:
                reason = "; ".join(
                    _PRESENCE_PHRASES[presence].format(side=side)
                    for side, presence in (("earlier", before_presence),
                                           ("newer", after_presence))
                    if presence
                )
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
        "validator_commission": _validator_commission_comparison(previous, current),
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
