#!/usr/bin/env python3
"""Collect a point-in-time snapshot of Solana network state.

Standard library only. No API keys, no third-party packages, no accounts.
Data comes from public Solana JSON-RPC methods.

Network access is confined to `fetch_rpc`. Everything that shapes data is a
pure function over already-fetched results, so the whole transform is testable
offline against fixtures.

    python3 collect.py                    # write a snapshot
    python3 collect.py --dry-run          # print, do not write
    python3 collect.py --endpoint <URL>   # use a different public RPC
    python3 collect.py --no-activity      # skip block sampling (much faster)
    python3 collect.py --no-news          # skip the keyless release/status feeds
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import blocks
import economics
import news as news_module

DEFAULT_ENDPOINT = "https://api.mainnet-beta.solana.com"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
LAMPORTS_PER_SOL = 1_000_000_000
# 2 added the `activity` section; 3 added `news`. Additive only — a v1 snapshot
# still reads, and every consumer looks up each field defensively.
SCHEMA_VERSION = 3

# One batched JSON-RPC request. Order matters: results come back positionally.
RPC_CALLS: list[tuple[str, list[Any]]] = [
    ("getHealth", []),
    ("getSlot", []),
    ("getEpochInfo", []),
    ("getRecentPerformanceSamples", [8]),
    ("getSupply", [{"commitment": "finalized", "excludeNonCirculatingAccountsList": True}]),
    ("getVoteAccounts", []),
]


class CollectionError(RuntimeError):
    """The RPC endpoint could not be reached or returned an unusable body."""


# ── network boundary ─────────────────────────────────────────────────────────

def fetch_rpc(endpoint: str = DEFAULT_ENDPOINT, timeout: int = 30) -> list[dict[str, Any]]:
    """Send the batched RPC request. The only function here that touches the network."""
    payload = [
        {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
        for index, (method, params) in enumerate(RPC_CALLS)
    ]
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise CollectionError(f"RPC returned HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise CollectionError(f"RPC unreachable: {error}") from error
    except json.JSONDecodeError as error:
        raise CollectionError("RPC returned a body that is not JSON") from error

    if not isinstance(body, list):
        raise CollectionError("Expected a batched JSON-RPC array response")
    return body


def fetch_block_time(slot: int, endpoint: str = DEFAULT_ENDPOINT, timeout: int = 30) -> int | None:
    """getBlockTime for a specific slot. Separate because it depends on getSlot."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [slot]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("result")
    except Exception:
        # A missing block time is not worth failing a whole snapshot over —
        # the slot may simply have been skipped.
        return None


# ── pure transforms (no network, fully testable) ─────────────────────────────

def index_results(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Map a positional batch response back to method names by JSON-RPC id."""
    indexed: dict[str, Any] = {}
    for entry in batch:
        if not isinstance(entry, dict):
            continue
        position = entry.get("id")
        if not isinstance(position, int) or position >= len(RPC_CALLS):
            continue
        method = RPC_CALLS[position][0]
        indexed[method] = entry.get("error") if "error" in entry else entry.get("result")
    return indexed


def summarize_performance(samples: Any) -> dict[str, Any]:
    """Derive TPS and slot time from getRecentPerformanceSamples."""
    if not isinstance(samples, list) or not samples:
        return {"available": False}

    per_sample = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        period = sample.get("samplePeriodSecs") or 0
        transactions = sample.get("numTransactions")
        slots = sample.get("numSlots")
        if not period or transactions is None or not slots:
            continue
        per_sample.append({
            "slot": sample.get("slot"),
            "tps": round(transactions / period, 2),
            "slot_time_secs": round(period / slots, 3),
        })

    if not per_sample:
        return {"available": False}

    return {
        "available": True,
        "samples_used": len(per_sample),
        "latest_tps": per_sample[0]["tps"],
        "mean_tps": round(sum(s["tps"] for s in per_sample) / len(per_sample), 2),
        "peak_tps": max(s["tps"] for s in per_sample),
        "mean_slot_time_secs": round(sum(s["slot_time_secs"] for s in per_sample) / len(per_sample), 3),
        "samples": per_sample,
    }


def summarize_validators(vote_accounts: Any, top_n: int = 10) -> dict[str, Any]:
    """Condense getVoteAccounts — the raw response lists thousands of validators."""
    if not isinstance(vote_accounts, dict):
        return {"available": False}

    current = vote_accounts.get("current") or []
    delinquent = vote_accounts.get("delinquent") or []
    if not isinstance(current, list) or not isinstance(delinquent, list):
        return {"available": False}

    def stake(entry: Any) -> int:
        return entry.get("activatedStake", 0) if isinstance(entry, dict) else 0

    active_stake = sum(stake(v) for v in current)
    delinquent_stake = sum(stake(v) for v in delinquent)
    total_validators = len(current) + len(delinquent)

    ranked = sorted(current, key=stake, reverse=True)[:top_n]
    top = [
        {
            # Identity is a public validator key, not a personal identifier.
            "identity": v.get("nodePubkey"),
            "stake_sol": round(stake(v) / LAMPORTS_PER_SOL, 2),
            "commission": v.get("commission"),
            "share_pct": round(100 * stake(v) / active_stake, 3) if active_stake else 0.0,
        }
        for v in ranked
        if isinstance(v, dict)
    ]

    # Nakamoto coefficient: how few validators it takes to pass one third of
    # stake — the standard concentration measure for a proof-of-stake chain.
    nakamoto = 0
    if active_stake:
        running = 0
        for validator in sorted(current, key=stake, reverse=True):
            running += stake(validator)
            nakamoto += 1
            if running > active_stake / 3:
                break

    # Commission tracking across the whole active set, not just the top ten —
    # the brief asks for it and getVoteAccounts already carries it.
    commissions = sorted(
        v["commission"] for v in current
        if isinstance(v, dict) and isinstance(v.get("commission"), (int, float))
    )
    commission_stats: dict[str, Any] = {"available": False}
    if commissions:
        midpoint = len(commissions) // 2
        commission_stats = {
            "available": True,
            "median_pct": commissions[midpoint] if len(commissions) % 2
            else (commissions[midpoint - 1] + commissions[midpoint]) / 2,
            "mean_pct": round(sum(commissions) / len(commissions), 2),
            "zero_commission_count": sum(1 for c in commissions if c == 0),
            "max_commission_count": sum(1 for c in commissions if c == 100),
        }

    return {
        "available": True,
        "active_count": len(current),
        "delinquent_count": len(delinquent),
        "commission": commission_stats,
        "delinquent_pct": round(100 * len(delinquent) / total_validators, 2) if total_validators else 0.0,
        "active_stake_sol": round(active_stake / LAMPORTS_PER_SOL, 2),
        "delinquent_stake_sol": round(delinquent_stake / LAMPORTS_PER_SOL, 2),
        "nakamoto_coefficient": nakamoto,
        "top_validators": top,
    }


def summarize_epoch(epoch_info: Any) -> dict[str, Any]:
    if not isinstance(epoch_info, dict):
        return {"available": False}
    slot_index = epoch_info.get("slotIndex")
    slots_in_epoch = epoch_info.get("slotsInEpoch")
    progress = (
        round(100 * slot_index / slots_in_epoch, 2)
        if isinstance(slot_index, int) and isinstance(slots_in_epoch, int) and slots_in_epoch
        else None
    )
    return {
        "available": True,
        "epoch": epoch_info.get("epoch"),
        "slot_index": slot_index,
        "slots_in_epoch": slots_in_epoch,
        "progress_pct": progress,
        "absolute_slot": epoch_info.get("absoluteSlot"),
        "block_height": epoch_info.get("blockHeight"),
        "transaction_count": epoch_info.get("transactionCount"),
    }


def summarize_supply(supply: Any) -> dict[str, Any]:
    value = supply.get("value") if isinstance(supply, dict) else None
    if not isinstance(value, dict):
        return {"available": False}
    total = value.get("total", 0)
    circulating = value.get("circulating", 0)
    return {
        "available": True,
        "total_sol": round(total / LAMPORTS_PER_SOL, 2),
        "circulating_sol": round(circulating / LAMPORTS_PER_SOL, 2),
        "non_circulating_sol": round(value.get("nonCirculating", 0) / LAMPORTS_PER_SOL, 2),
        "circulating_pct": round(100 * circulating / total, 2) if total else None,
    }


def build_snapshot(
    indexed: dict[str, Any],
    collected_at: str,
    endpoint: str,
    block_time: int | None = None,
    economics: dict[str, Any] | None = None,
    activity: dict[str, Any] | None = None,
    news: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable snapshot. Pure — no network, no clock."""
    health = indexed.get("getHealth")
    slot = indexed.get("getSlot")

    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": collected_at,
        "source": {"endpoint": endpoint, "method": "solana json-rpc", "requires_api_key": False},
        "network": {
            # getHealth returns the string "ok" when healthy, an error object otherwise.
            "healthy": health == "ok",
            "health_raw": health if isinstance(health, str) else "unhealthy",
            "slot": slot if isinstance(slot, int) else None,
            "block_time_unix": block_time,
        },
        "epoch": summarize_epoch(indexed.get("getEpochInfo")),
        "performance": summarize_performance(indexed.get("getRecentPerformanceSamples")),
        "supply": summarize_supply(indexed.get("getSupply")),
        "validators": summarize_validators(indexed.get("getVoteAccounts")),
        "economics": economics if economics is not None else {"available": False},
        "activity": activity if activity is not None else {"available": False},
        "news": news if news is not None else {"available": False},
    }


def snapshot_filename(collected_at: str) -> str:
    """UTC timestamp, filesystem-safe, sorts chronologically."""
    return f"snapshot-{collected_at.replace(':', '').replace('-', '').replace('.', '-')}.json"


# ── entry point ──────────────────────────────────────────────────────────────

def collect(
    endpoint: str = DEFAULT_ENDPOINT,
    with_economics: bool = True,
    with_activity: bool = True,
    with_news: bool = True,
    samples: int = blocks.DEFAULT_SAMPLES,
) -> dict[str, Any]:
    batch = fetch_rpc(endpoint)
    indexed = index_results(batch)
    slot = indexed.get("getSlot")
    block_time = fetch_block_time(slot, endpoint) if isinstance(slot, int) else None
    # Economic sources are third-party and optional: a failure there degrades
    # that section only, and never blocks the on-chain snapshot.
    econ = economics.collect_economics() if with_economics else None
    # Block sampling is the slow part of a run — a dozen multi-megabyte bodies
    # against a rate-limited public endpoint. Same rule as economics: it fails
    # to `available: false` on its own and never takes the snapshot with it.
    activity = blocks.collect_activity(endpoint, samples) if with_activity else None
    # Release and status feeds. Third-party and optional on exactly the same
    # terms: recorded into the snapshot so rendering never re-fetches, and a
    # broken feed costs this section alone.
    feeds = news_module.collect_news() if with_news else None
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return build_snapshot(indexed, collected_at, endpoint, block_time, econ, activity, feeds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a Solana network snapshot.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--dry-run", action="store_true", help="print the snapshot without writing it")
    parser.add_argument("--out-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--no-economics", action="store_true",
                        help="skip third-party economic sources (RPC only)")
    parser.add_argument("--no-activity", action="store_true",
                        help="skip block sampling for fees, REV and address activity")
    parser.add_argument("--no-news", action="store_true",
                        help="skip the keyless release, proposal and status feeds")
    parser.add_argument("--samples", type=int, default=blocks.DEFAULT_SAMPLES,
                        help=f"blocks to sample across ~24h (default {blocks.DEFAULT_SAMPLES})")
    args = parser.parse_args()

    try:
        snapshot = collect(
            args.endpoint,
            with_economics=not args.no_economics,
            with_activity=not args.no_activity,
            with_news=not args.no_news,
            samples=args.samples,
        )
    except CollectionError as error:
        print(f"collection failed: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(snapshot, indent=2))
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / snapshot_filename(snapshot["collected_at"])
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    # latest.json is a copy, not a symlink, so the history stays append-only.
    (args.out_dir / "latest.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
