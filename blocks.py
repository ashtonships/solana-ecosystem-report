#!/usr/bin/env python3
"""On-chain activity: transaction fees, Real Economic Value, and address activity.

Standard library only. No API keys, no third-party packages, no accounts. Every
figure here is derived from `getBlock` on the same public JSON-RPC endpoint the
rest of the report uses — verified reachable unauthenticated.

Why this exists: the brief names REV, median transaction fees and daily active
addresses, and none of them are available from the cheap summary RPC methods.
They have to come out of block bodies. `transactionDetails: "accounts"` returns
each transaction's fee, signatures, account keys and balance deltas while
dropping instruction data, which keeps a block around 3 MB instead of 30 MB.

Three things make the numbers here honest rather than merely plausible:

  Vote transactions are separated out. They are the majority of Solana's
  transaction count and every one of them pays exactly 5000 lamports, so a
  median taken over all transactions is always 5000 and says nothing about
  what it costs a person to use the network.

  Fee burn is measured, not assumed. Each block carries its own `Fee` reward
  entry — what the leader actually received. Burn is total fees minus that,
  so no fee-split rule is hardcoded and the figure survives a protocol change.

  Daily active addresses are NOT extrapolated. Sums extrapolate from a sample;
  unique-address counts do not, because samples overlap. The daily figure is
  reported unavailable and the sampled count is labelled as what it is.

Same architecture as `economics.py`: network access is confined to the fetch
helpers, every transform is pure, and any failure degrades this section alone
to `available: false` rather than blanking the snapshot or printing a zero.

    python3 blocks.py                 # sample and print
    python3 blocks.py --samples 8     # fewer blocks, faster
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_ENDPOINT = "https://api.mainnet-beta.solana.com"

LAMPORTS_PER_SOL = 1_000_000_000

# Every signature costs this much regardless of congestion; anything above it
# on a transaction is the priority fee. Fixed by the protocol, not a guess.
BASE_FEE_LAMPORTS_PER_SIGNATURE = 5_000

# 24 hours at the 400ms target slot time. The public endpoint was verified to
# still serve blocks this far back, so the window is a real day, not a proxy.
WINDOW_SLOTS = 216_000

# Blocks to fetch, spread evenly across the window. Each is ~3 MB and ~0.7s.
DEFAULT_SAMPLES = 16

# Wall-clock ceiling for the sampling loop. The public endpoint throttles, and
# an unbounded run has been observed taking 2.5 minutes for what normally takes
# 16 seconds. A short sample beats a stalled collection.
DEFAULT_BUDGET_SECONDS = 90.0

# Stay behind the finalized head so a sampled slot is never mid-confirmation.
FINALITY_LAG_SLOTS = 150

# Span used to measure how many slots actually produce a block. Small enough
# to be a 0.3s call, large enough for the rate to be stable.
PRODUCTION_PROBE_SLOTS = 10_000

VOTE_PROGRAM = "Vote111111111111111111111111111111111111111"

# Jito's published tip payment accounts. A tip is a plain SOL transfer into one
# of these, so it is visible as a positive balance delta without any Jito API.
# Tips are the third component of REV alongside base and priority fees.
JITO_TIP_ACCOUNTS = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})

BLOCK_CONFIG = {
    # "accounts" keeps fees, signatures, account keys and balances but drops
    # instruction data — everything needed here, at a tenth of the payload.
    "encoding": "json",
    "transactionDetails": "accounts",
    "rewards": True,
    "maxSupportedTransactionVersion": 0,
}

DAILY_ACTIVE_NOTE = (
    "Unique non-vote fee payers seen in the sampled blocks only, not a daily total. "
    "A true daily active address count is the union over every block in 24 hours; "
    "unique counts do not scale from a sample the way sums do, because samples "
    "overlap. Extrapolating this number would overstate it, so the daily figure is "
    "reported as unavailable instead."
)


# ── network boundary ─────────────────────────────────────────────────────────

def call(method: str, params: list[Any], endpoint: str = DEFAULT_ENDPOINT,
         timeout: int = 30) -> Any | None:
    """One JSON-RPC call. Returns None on any failure — never raises.

    This section is optional enrichment, so unlike `collect.fetch_rpc` a failure
    here must degrade quietly rather than take down the snapshot around it.
    """
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or "error" in body:
        return None
    return body.get("result")


def fetch_head(endpoint: str = DEFAULT_ENDPOINT, timeout: int = 30) -> int | None:
    slot = call("getSlot", [{"commitment": "finalized"}], endpoint, timeout)
    return slot if isinstance(slot, int) else None


def fetch_production_rate(head: int, endpoint: str = DEFAULT_ENDPOINT,
                          timeout: int = 30) -> float | None:
    """Share of slots that actually produced a block, from a short probe range.

    Needed to turn per-block figures into per-day ones. Measuring it costs one
    small call; assuming a slot time would quietly bake in an error instead.
    """
    end = head - FINALITY_LAG_SLOTS
    start = end - PRODUCTION_PROBE_SLOTS
    if start < 0:
        return None
    blocks = call("getBlocks", [start, end], endpoint, timeout)
    if not isinstance(blocks, list) or not blocks:
        return None
    return len(blocks) / (end - start + 1)


def fetch_block(slot: int, endpoint: str = DEFAULT_ENDPOINT, timeout: int = 60,
                skip_forward: int = 5) -> tuple[int, dict[str, Any]] | None:
    """Fetch one block, stepping forward past skipped slots.

    Roughly one slot in a thousand is skipped and returns no block. Walking a
    few slots forward keeps the sample evenly spaced instead of dropping a
    point, which matters when there are only sixteen of them.

    Returns the slot alongside the block: a block body does not carry its own
    slot number, and it cannot be recovered from `parentSlot` because the
    preceding slot may itself have been skipped.
    """
    for offset in range(skip_forward + 1):
        block = call("getBlock", [slot + offset, BLOCK_CONFIG], endpoint, timeout)
        if isinstance(block, dict):
            return slot + offset, block
    return None


# ── pure transforms (no network, fully testable) ─────────────────────────────

def sample_slots(head: int, samples: int = DEFAULT_SAMPLES,
                 window_slots: int = WINDOW_SLOTS) -> list[int]:
    """Evenly spaced slots across the window, oldest first.

    Even spacing rather than a random draw: fees are bursty and cyclical over a
    day, so a systematic sample covers the daily shape that a clustered one
    would miss entirely.
    """
    if samples < 1 or window_slots < 1:
        return []
    end = head - FINALITY_LAG_SLOTS
    start = end - window_slots
    if start < 0 or end <= 0:
        return []
    if samples == 1:
        return [end]
    step = window_slots / (samples - 1)
    return [int(start + round(index * step)) for index in range(samples)]


def jito_tip_lamports(account_keys: list[Any], meta: dict[str, Any]) -> int:
    """Lamports this transaction paid into Jito tip accounts.

    Read as a balance delta rather than by decoding instructions, because the
    "accounts" detail level has no instruction data — and a delta is the truth
    regardless of how the transfer was constructed.
    """
    pre = meta.get("preBalances")
    post = meta.get("postBalances")
    if not isinstance(pre, list) or not isinstance(post, list):
        return 0

    total = 0
    for index, key in enumerate(account_keys):
        pubkey = key.get("pubkey") if isinstance(key, dict) else None
        if pubkey not in JITO_TIP_ACCOUNTS or index >= len(pre) or index >= len(post):
            continue
        before, after = pre[index], post[index]
        if isinstance(before, int) and isinstance(after, int) and after > before:
            total += after - before
    return total


def validator_fee_reward(block: dict[str, Any]) -> int | None:
    """The leader's actual fee reward for this block, if the block reports it.

    None means "not reported", which is different from zero and must stay so —
    it is the divisor-side of the burn calculation.
    """
    rewards = block.get("rewards")
    if not isinstance(rewards, list):
        return None
    entries = [
        r["lamports"] for r in rewards
        if isinstance(r, dict) and r.get("rewardType") == "Fee"
        and isinstance(r.get("lamports"), int)
    ]
    return sum(entries) if entries else None


def summarize_block(block: Any, slot: int | None = None) -> dict[str, Any] | None:
    """Condense one block. Returns None if the body is not usable.

    The slot is passed in rather than read off the body, which does not carry
    it.

    The sets and the fee list this returns are working values consumed by
    `build_activity`; they are aggregated across blocks and never serialised.
    """
    if not isinstance(block, dict):
        return None
    transactions = block.get("transactions")
    if not isinstance(transactions, list):
        return None

    vote_count = nonvote_count = failed_count = 0
    base = priority = jito = 0
    nonvote_fees: list[int] = []
    fee_payers: set[str] = set()
    accounts: set[str] = set()

    for entry in transactions:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("meta")
        inner = entry.get("transaction")
        if not isinstance(meta, dict) or not isinstance(inner, dict):
            continue
        account_keys = inner.get("accountKeys")
        if not isinstance(account_keys, list):
            continue

        fee = meta.get("fee")
        fee = fee if isinstance(fee, int) and fee >= 0 else 0
        signatures = inner.get("signatures")
        signature_count = len(signatures) if isinstance(signatures, list) else 1

        # Clamped so a malformed body can never produce a negative priority fee.
        tx_base = min(fee, BASE_FEE_LAMPORTS_PER_SIGNATURE * max(1, signature_count))
        base += tx_base
        priority += fee - tx_base
        jito += jito_tip_lamports(account_keys, meta)

        pubkeys = [k.get("pubkey") for k in account_keys if isinstance(k, dict)]
        if VOTE_PROGRAM in pubkeys:
            vote_count += 1
            continue

        nonvote_count += 1
        nonvote_fees.append(fee)
        if meta.get("err") is not None:
            failed_count += 1
        accounts.update(p for p in pubkeys if isinstance(p, str))
        fee_payers.update(
            k["pubkey"] for k in account_keys
            if isinstance(k, dict) and k.get("signer") and isinstance(k.get("pubkey"), str)
        )

    block_time = block.get("blockTime")
    return {
        "slot": slot,
        "block_time": block_time if isinstance(block_time, int) else None,
        "tx_total": vote_count + nonvote_count,
        "tx_vote": vote_count,
        "tx_nonvote": nonvote_count,
        "tx_nonvote_failed": failed_count,
        "base_lamports": base,
        "priority_lamports": priority,
        "jito_lamports": jito,
        "fee_lamports": base + priority,
        "rev_lamports": base + priority + jito,
        "validator_fee_reward_lamports": validator_fee_reward(block),
        "nonvote_fees": nonvote_fees,
        "fee_payers": fee_payers,
        "accounts": accounts,
    }


def percentile(sorted_values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile over a pre-sorted list."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _sol(lamports: float | None, digits: int = 6) -> float | None:
    return None if lamports is None else round(lamports / LAMPORTS_PER_SOL, digits)


def summarize_fees(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Fee distribution over non-vote transactions only.

    Median and percentiles together, because the distribution is extremely
    skewed: most transactions pay the 5000-lamport floor while a small tail
    bids far above it, so a median alone reads as "nothing ever costs anything"
    and a mean alone reads as "everything is expensive".
    """
    fees = sorted(fee for s in summaries for fee in s["nonvote_fees"])
    if not fees:
        return {"available": False}

    total_tx = sum(s["tx_total"] for s in summaries)
    vote_tx = sum(s["tx_vote"] for s in summaries)
    failed = sum(s["tx_nonvote_failed"] for s in summaries)

    return {
        "available": True,
        "transactions_sampled": total_tx,
        "nonvote_transactions_sampled": len(fees),
        "vote_share_pct": round(100 * vote_tx / total_tx, 2) if total_tx else None,
        "failure_rate_pct": round(100 * failed / len(fees), 2),
        "median_lamports": int(statistics.median(fees)),
        "mean_lamports": int(sum(fees) / len(fees)),
        "p90_lamports": percentile(fees, 90),
        "p99_lamports": percentile(fees, 99),
        "min_lamports": fees[0],
        "max_lamports": fees[-1],
        "median_sol": _sol(statistics.median(fees), 9),
        "basis": "non-vote transactions only",
    }


def summarize_rev(summaries: list[dict[str, Any]],
                  blocks_in_window: int | None) -> dict[str, Any]:
    """Real Economic Value: base fees + priority fees + Jito tips.

    Reported first as what was actually observed, then as a 24-hour estimate.
    The estimate is a sum, which does extrapolate from a systematic sample, and
    it is scaled by a measured block count rather than an assumed slot time.
    The per-block spread is published alongside it so the estimate cannot be
    read as more precise than sixteen bursty blocks can support.
    """
    if not summaries:
        return {"available": False}

    per_block = [s["rev_lamports"] for s in summaries]
    mean_per_block = sum(per_block) / len(per_block)
    estimated_24h = mean_per_block * blocks_in_window if blocks_in_window else None

    # Blocks vary enormously — repeat runs of this sampler have moved the daily
    # figure by a third. Publishing a six-figure point estimate without saying
    # how wide it is would imply a precision sixteen blocks cannot support, so
    # the interval is computed and rendered next to the number.
    low = high = None
    if estimated_24h is not None and len(per_block) > 1:
        margin = 1.96 * (statistics.stdev(per_block) / math.sqrt(len(per_block)))
        low = max(0.0, (mean_per_block - margin) * blocks_in_window)
        high = (mean_per_block + margin) * blocks_in_window

    return {
        "available": True,
        "definition": "base fees + priority fees + Jito tips",
        "sampled_sol": {
            "base": _sol(sum(s["base_lamports"] for s in summaries)),
            "priority": _sol(sum(s["priority_lamports"] for s in summaries)),
            "jito_tips": _sol(sum(s["jito_lamports"] for s in summaries)),
            "total": _sol(sum(per_block)),
        },
        "per_block_sol": {
            "mean": _sol(mean_per_block),
            "min": _sol(min(per_block)),
            "max": _sol(max(per_block)),
        },
        "estimated_24h_sol": _sol(estimated_24h, 2),
        "estimated_24h_sol_low": _sol(low, 2),
        "estimated_24h_sol_high": _sol(high, 2),
        "confidence": "95% interval on the sample mean" if low is not None else None,
        "estimated": True,
        "blocks_in_window": blocks_in_window,
        "method": (
            "mean REV per sampled block x blocks produced in the window; "
            "block count from a measured production rate, not an assumed slot time"
        ),
    }


def summarize_addresses(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Address activity, with the daily figure explicitly withheld."""
    if not summaries:
        return {"available": False}

    payers: set[str] = set()
    accounts: set[str] = set()
    for summary in summaries:
        payers |= summary["fee_payers"]
        accounts |= summary["accounts"]

    per_block = [len(s["fee_payers"]) for s in summaries]
    return {
        "available": True,
        "unique_fee_payers_sampled": len(payers),
        "unique_accounts_sampled": len(accounts),
        "mean_fee_payers_per_block": round(sum(per_block) / len(per_block), 1),
        "blocks_sampled": len(summaries),
        # Deliberately null, not a number. See DAILY_ACTIVE_NOTE.
        "daily_active_addresses": None,
        "daily_active_available": False,
        "note": DAILY_ACTIVE_NOTE,
        "basis": "non-vote transactions only",
    }


def summarize_fee_distribution(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """How sampled fees split between the leader and the burn.

    Derived from each block's own reward entry, so no burn rate is assumed and
    the figure stays correct across a protocol change. Only blocks that
    reported a reward are counted, and their fees are paired with it.
    """
    paired = [s for s in summaries if isinstance(s["validator_fee_reward_lamports"], int)]
    if not paired:
        return {"available": False}

    fees = sum(s["fee_lamports"] for s in paired)
    rewarded = sum(s["validator_fee_reward_lamports"] for s in paired)
    if fees <= 0 or rewarded > fees:
        # Cannot reconcile — say so rather than publish a negative burn.
        return {"available": False}

    return {
        "available": True,
        "blocks_reconciled": len(paired),
        "fees_sol": _sol(fees),
        "validator_reward_sol": _sol(rewarded),
        "burned_sol": _sol(fees - rewarded),
        "burned_pct": round(100 * (fees - rewarded) / fees, 2),
        "note": "Measured from each block's own fee reward entry; no burn rate assumed.",
    }


def build_activity(summaries: list[dict[str, Any]], endpoint: str,
                   production_rate: float | None = None,
                   window_slots: int = WINDOW_SLOTS,
                   samples_requested: int = DEFAULT_SAMPLES,
                   truncated: bool = False) -> dict[str, Any]:
    """Assemble the activity section. Pure — no network, no clock."""
    if not summaries:
        return {
            "available": False,
            "requires_api_key": False,
            "reason": "no blocks could be sampled from the endpoint",
        }

    times = [s["block_time"] for s in summaries if isinstance(s["block_time"], int)]
    slots = [s["slot"] for s in summaries if isinstance(s["slot"], int)]
    blocks_in_window = round(window_slots * production_rate) if production_rate else None

    return {
        "available": True,
        "requires_api_key": False,
        "source": {"endpoint": endpoint, "method": "getBlock (transactionDetails=accounts)"},
        "window": {
            "slots": window_slots,
            "blocks_sampled": len(summaries),
            "blocks_requested": samples_requested,
            # A short sample is still valid, but the reader has to be told the
            # run was cut off rather than left to assume full coverage.
            "truncated": truncated,
            "production_rate": round(production_rate, 5) if production_rate else None,
            "first_slot": min(slots) if slots else None,
            "last_slot": max(slots) if slots else None,
            # Observed from block timestamps, so it reflects real elapsed time
            # rather than the slot count multiplied by a target slot duration.
            "observed_seconds": max(times) - min(times) if len(times) > 1 else None,
            "sampling": "evenly spaced across the window",
        },
        "fees": summarize_fees(summaries),
        "rev": summarize_rev(summaries, blocks_in_window),
        "addresses": summarize_addresses(summaries),
        "fee_split": summarize_fee_distribution(summaries),
    }


# ── entry point ──────────────────────────────────────────────────────────────

def collect_activity(endpoint: str = DEFAULT_ENDPOINT,
                     samples: int = DEFAULT_SAMPLES,
                     timeout: int = 60,
                     budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict[str, Any]:
    """Sample blocks across the window and summarize them. Never raises.

    Stops early once the budget is spent. The public endpoint throttles under
    load, and without a ceiling a throttled run turns a two-minute collection
    into a fifteen-minute one. Whatever was gathered by then is still a valid
    sample, so a truncated run degrades to fewer blocks rather than to nothing.
    """
    started = time.monotonic()
    head = fetch_head(endpoint, timeout=30)
    if head is None:
        return {
            "available": False,
            "requires_api_key": False,
            "reason": "could not read the current slot from the endpoint",
        }

    production_rate = fetch_production_rate(head, endpoint, timeout=30)
    targets = sample_slots(head, samples)
    summaries = []
    truncated = False
    for index, target in enumerate(targets):
        if time.monotonic() - started > budget_seconds:
            truncated = index < len(targets)
            break
        fetched = fetch_block(target, endpoint, timeout)
        if fetched is None:
            continue
        summary = summarize_block(fetched[1], fetched[0])
        if summary is not None:
            summaries.append(summary)

    return build_activity(summaries, endpoint, production_rate, WINDOW_SLOTS,
                          samples, truncated)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample Solana blocks for fees, REV and address activity.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help=f"blocks to sample across ~24h (default {DEFAULT_SAMPLES})")
    args = parser.parse_args()
    print(json.dumps(collect_activity(args.endpoint, args.samples), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
