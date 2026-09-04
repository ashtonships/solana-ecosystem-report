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
import logging
import math
import statistics
import time
import urllib.error
import urllib.request
from typing import Any

import transport

DEFAULT_ENDPOINT = "https://api.mainnet.solana.com"

LAMPORTS_PER_SOL = 1_000_000_000

# Every message signature costs this much. The accounts-only block response does
# not include instruction data, so supported precompile signatures cannot be
# counted here. This is therefore a base-fee lower bound, never an exact split.
BASE_FEE_LAMPORTS_PER_SIGNATURE = 5_000

# A fixed slot span. Its exact elapsed wall-clock duration comes from the first
# and last sampled block timestamps; it must not be described as a 24-hour day.
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

# The public mainnet endpoint rejects larger getBlockProduction ranges even
# though the RPC method accepts an arbitrary slot range. Contiguous chunks keep
# the keyless default while preserving exact completed-epoch coverage.
BLOCK_PRODUCTION_CHUNK_SLOTS = 5_000

# The public endpoint permits 40 calls to one RPC method per 10 seconds. Keep a
# little headroom for a shared runner/IP instead of turning rate limiting into a
# partial epoch.
BLOCK_PRODUCTION_REQUESTS_PER_WINDOW = 35
BLOCK_PRODUCTION_RATE_WINDOW_SECONDS = 10.0

VOTE_PROGRAM = "Vote111111111111111111111111111111111111111"

# Jito's published tip payment accounts. A tip is a plain SOL transfer into one
# of these, so it is visible as a positive balance delta without any Jito API.
# Tips are included in REV alongside the transaction fee total.
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
JITO_TIP_ACCOUNT_SOURCE_REVISION = "93dec9d9e8ec0f2a20dea9f0a6f2d14bcd9494cd"
JITO_TIP_ACCOUNT_SOURCE_URL = (
    "https://github.com/jito-labs/jito-docs/blob/"
    f"{JITO_TIP_ACCOUNT_SOURCE_REVISION}/docs/source/lowlatencytxnsend.md"
)

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
         timeout: float = 30) -> Any | None:
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
    deadline = time.monotonic() + timeout
    retry_after = 0.0
    last_http_status = None

    def open_request():
        nonlocal retry_after, last_http_status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("RPC request budget exhausted")
        retry_after = 0.0
        try:
            return urllib.request.urlopen(request, timeout=remaining)
        except urllib.error.HTTPError as error:
            last_http_status = error.code
            value = error.headers.get("Retry-After") if error.headers else None
            if value is not None:
                try:
                    retry_after = float(value)
                except ValueError:
                    retry_after = timeout  # Do not retry before an unknown server deadline.
                if not math.isfinite(retry_after) or retry_after < 0:
                    retry_after = timeout
            error.close()
            raise

    def pause(delay):
        delay = max(delay, retry_after)
        if delay >= deadline - time.monotonic():
            raise TimeoutError("RPC retry exceeds request budget")
        time.sleep(delay)

    try:
        with transport.fetch_with_retry(open_request, sleep=pause) as response:
            body = json.loads(transport.read_bounded(response).decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError) as error:
        failure = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
        if last_http_status is not None and not isinstance(error, urllib.error.HTTPError):
            failure += f" after HTTP {last_http_status}"
        logging.getLogger(__name__).warning("RPC %.64s failed: %s", method, failure)
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

    Needed to turn per-block figures into an estimate for the sampled slot
    window. Measuring it costs one small call; assuming a slot time would
    quietly bake in an error instead.
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
    deadline = time.monotonic() + timeout
    for offset in range(skip_forward + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        block = call("getBlock", [slot + offset, BLOCK_CONFIG], endpoint, remaining)
        if isinstance(block, dict):
            return slot + offset, block
    return None


def fetch_block_production(epoch_range: dict[str, Any],
                           endpoint: str = DEFAULT_ENDPOINT,
                           timeout: int = 30) -> Any | None:
    """Fetch and reconcile production for one completed epoch in exact chunks."""
    values = _completed_range_values(epoch_range)
    if values is None:
        return None
    _, first_slot, last_slot, range_slots = values
    request_count = (range_slots + BLOCK_PRODUCTION_CHUNK_SLOTS - 1) // BLOCK_PRODUCTION_CHUNK_SLOTS
    by_identity: dict[str, list[int]] = {}
    context_slots: list[int] = []
    api_version: str | None = None

    for chunk_number, chunk_first in enumerate(
        range(first_slot, last_slot + 1, BLOCK_PRODUCTION_CHUNK_SLOTS), start=1,
    ):
        if (chunk_number > 1
                and (chunk_number - 1) % BLOCK_PRODUCTION_REQUESTS_PER_WINDOW == 0):
            time.sleep(BLOCK_PRODUCTION_RATE_WINDOW_SECONDS)
        chunk_last = min(last_slot, chunk_first + BLOCK_PRODUCTION_CHUNK_SLOTS - 1)
        response = call("getBlockProduction", [{
            "commitment": "finalized",
            "range": {"firstSlot": chunk_first, "lastSlot": chunk_last},
        }], endpoint, timeout)
        if not isinstance(response, dict):
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} is missing"
            )

        context = response.get("context")
        value = response.get("value")
        if not isinstance(context, dict) or not isinstance(value, dict):
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} is malformed"
            )
        context_slot = context.get("slot")
        chunk_api_version = context.get("apiVersion")
        if (not _nonnegative_int(context_slot) or context_slot < chunk_last
                or not isinstance(chunk_api_version, str)
                or not chunk_api_version.strip()
                or chunk_api_version != chunk_api_version.strip()):
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} has invalid context"
            )
        if api_version is not None and chunk_api_version != api_version:
            return _unavailable("block-production chunk API versions do not match")

        returned_range = value.get("range")
        chunk_identities = value.get("byIdentity")
        if (not isinstance(returned_range, dict)
                or returned_range.get("firstSlot") != chunk_first
                or returned_range.get("lastSlot") != chunk_last):
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} range does not match"
            )
        if not isinstance(chunk_identities, dict) or not chunk_identities:
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} has no identities"
            )

        chunk_leader_slots = 0
        for identity, counts in chunk_identities.items():
            if (not isinstance(identity, str) or not identity
                    or identity != identity.strip()
                    or not isinstance(counts, list) or len(counts) != 2
                    or not all(_nonnegative_int(count) for count in counts)):
                return _unavailable(
                    f"block-production chunk {chunk_number} of {request_count} has invalid counts"
                )
            leader_slots, blocks_produced = counts
            if leader_slots == 0 or blocks_produced > leader_slots:
                return _unavailable(
                    f"block-production chunk {chunk_number} of {request_count} has inconsistent counts"
                )
            chunk_leader_slots += leader_slots
            aggregate = by_identity.setdefault(identity, [0, 0])
            aggregate[0] += leader_slots
            aggregate[1] += blocks_produced
        if chunk_leader_slots != chunk_last - chunk_first + 1:
            return _unavailable(
                f"block-production chunk {chunk_number} of {request_count} coverage is incomplete"
            )

        api_version = chunk_api_version
        context_slots.append(context_slot)

    return {
        "context": {"slot": min(context_slots), "apiVersion": api_version},
        "value": {
            "byIdentity": by_identity,
            "range": {"firstSlot": first_slot, "lastSlot": last_slot},
        },
        "collection": {
            "mode": "contiguous_chunks",
            "request_count": request_count,
            "chunk_slot_limit": BLOCK_PRODUCTION_CHUNK_SLOTS,
            "first_slot": first_slot,
            "last_slot": last_slot,
            "coverage_numerator_slots": range_slots,
            "coverage_denominator_slots": range_slots,
            "coverage_complete": True,
            "context_slot_min": min(context_slots),
            "context_slot_max": max(context_slots),
        },
    }


# ── pure transforms (no network, fully testable) ─────────────────────────────

def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _completed_range_values(epoch_range: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(epoch_range, dict) or epoch_range.get("available") is not True:
        return None
    values = tuple(epoch_range.get(key) for key in (
        "epoch", "first_slot", "last_slot", "leader_slots",
    ))
    if not all(_nonnegative_int(value) for value in values):
        return None
    epoch, first_slot, last_slot, leader_slots = values
    if first_slot > last_slot or leader_slots != last_slot - first_slot + 1:
        return None
    return epoch, first_slot, last_slot, leader_slots


def completed_epoch_range(epoch_info: Any, epoch_schedule: Any) -> dict[str, Any]:
    """Derive the exact slot range for current epoch minus one."""
    if not isinstance(epoch_info, dict) or not isinstance(epoch_schedule, dict):
        return _unavailable("epoch info or schedule is missing")

    epoch = epoch_info.get("epoch")
    absolute_slot = epoch_info.get("absoluteSlot")
    slot_index = epoch_info.get("slotIndex")
    slots_in_epoch = epoch_info.get("slotsInEpoch")
    slots_per_epoch = epoch_schedule.get("slotsPerEpoch")
    first_normal_epoch = epoch_schedule.get("firstNormalEpoch")
    first_normal_slot = epoch_schedule.get("firstNormalSlot")
    warmup = epoch_schedule.get("warmup")
    integers = (
        epoch, absolute_slot, slot_index, slots_in_epoch,
        slots_per_epoch, first_normal_epoch, first_normal_slot,
    )
    if not all(_nonnegative_int(value) for value in integers):
        return _unavailable("epoch info or schedule has invalid counts")
    if not isinstance(warmup, bool) or epoch == 0:
        return _unavailable("there is no fully completed prior epoch")
    if slots_in_epoch <= 0 or slots_per_epoch <= 0 or slot_index >= slots_in_epoch:
        return _unavailable("epoch slot counts are inconsistent")

    current_first_slot = absolute_slot - slot_index
    if current_first_slot < 0:
        return _unavailable("epoch slot counts are inconsistent")

    if epoch >= first_normal_epoch:
        expected_first = first_normal_slot + (epoch - first_normal_epoch) * slots_per_epoch
        if slots_in_epoch != slots_per_epoch or current_first_slot != expected_first:
            return _unavailable("epoch info does not match the epoch schedule")
    elif not warmup:
        return _unavailable("epoch info does not match the epoch schedule")

    if warmup and epoch <= first_normal_epoch:
        if slots_in_epoch < 2 or slots_in_epoch % 2:
            return _unavailable("warmup epoch size is invalid")
        previous_slots = slots_in_epoch // 2
    else:
        previous_slots = slots_per_epoch

    last_slot = current_first_slot - 1
    first_slot = current_first_slot - previous_slots
    if first_slot < 0 or last_slot < first_slot:
        return _unavailable("completed epoch range is invalid")
    return {
        "available": True,
        "basis": "most recent fully completed epoch",
        "epoch": epoch - 1,
        "first_slot": first_slot,
        "last_slot": last_slot,
        "leader_slots": previous_slots,
    }


def normalize_block_production(production: Any, vote_accounts: Any,
                               epoch_range: Any,
                               vote_observed_at: Any) -> dict[str, Any]:
    """Validate completed-epoch production and enrich it by node identity."""
    range_values = _completed_range_values(epoch_range)
    if range_values is None:
        return _unavailable("completed epoch range is invalid")
    epoch, first_slot, last_slot, range_slots = range_values
    if not isinstance(vote_observed_at, str) or not vote_observed_at.strip():
        return _unavailable("vote-account observation time is missing")

    if isinstance(production, dict) and production.get("available") is False:
        reason = production.get("reason")
        return _unavailable(
            reason if isinstance(reason, str) and reason.strip()
            else "block-production collection is unavailable"
        )
    if not isinstance(production, dict):
        return _unavailable("block-production response is missing")
    context = production.get("context")
    value = production.get("value")
    if not isinstance(context, dict) or not isinstance(value, dict):
        return _unavailable("block-production response is partial")
    context_slot = context.get("slot")
    api_version = context.get("apiVersion")
    if (not _nonnegative_int(context_slot) or context_slot < last_slot
            or not isinstance(api_version, str) or not api_version.strip()):
        return _unavailable("block-production context is invalid")

    returned_range = value.get("range")
    by_identity = value.get("byIdentity")
    if not isinstance(returned_range, dict) or not isinstance(by_identity, dict):
        return _unavailable("block-production value is partial")
    if (returned_range.get("firstSlot") != first_slot
            or returned_range.get("lastSlot") != last_slot):
        return _unavailable("block-production range does not match the completed epoch")
    if not by_identity:
        return _unavailable("block-production identities are missing")

    production_rows = []
    seen_identities: set[str] = set()
    total_leader_slots = 0
    total_blocks_produced = 0
    for identity, counts in by_identity.items():
        if (not isinstance(identity, str) or not identity
                or identity != identity.strip() or identity in seen_identities):
            return _unavailable("block-production identity is invalid or duplicated")
        if (not isinstance(counts, list) or len(counts) != 2
                or not all(_nonnegative_int(count) for count in counts)):
            return _unavailable("block-production counts are invalid")
        leader_slots, blocks_produced = counts
        if leader_slots == 0 or blocks_produced > leader_slots:
            return _unavailable("block-production counts are inconsistent")
        seen_identities.add(identity)
        total_leader_slots += leader_slots
        total_blocks_produced += blocks_produced
        production_rows.append((identity, leader_slots, blocks_produced))
    if total_leader_slots != range_slots:
        return _unavailable("leader-slot total does not cover the inclusive epoch range")

    collection = production.get("collection")
    if collection is not None:
        if not isinstance(collection, dict):
            return _unavailable("block-production collection provenance is invalid")
        collection_counts = tuple(collection.get(key) for key in (
            "request_count", "chunk_slot_limit", "first_slot", "last_slot",
            "coverage_numerator_slots", "coverage_denominator_slots",
            "context_slot_min", "context_slot_max",
        ))
        if not all(_nonnegative_int(value) for value in collection_counts):
            return _unavailable("block-production collection provenance has invalid counts")
        (requests, chunk_limit, collected_first, collected_last, coverage_numerator,
         coverage_denominator, context_min, context_max) = collection_counts
        if (collection.get("mode") != "contiguous_chunks"
                or requests == 0 or chunk_limit == 0
                or chunk_limit > BLOCK_PRODUCTION_CHUNK_SLOTS
                or requests != (range_slots + chunk_limit - 1) // chunk_limit
                or collected_first != first_slot or collected_last != last_slot
                or coverage_numerator != range_slots or coverage_denominator != range_slots
                or collection.get("coverage_complete") is not True
                or context_min != context_slot or context_max < context_min):
            return _unavailable("block-production collection provenance is inconsistent")

    if not isinstance(vote_accounts, dict):
        return _unavailable("vote-account response is missing")
    current = vote_accounts.get("current")
    delinquent = vote_accounts.get("delinquent")
    if not isinstance(current, list) or not isinstance(delinquent, list):
        return _unavailable("vote-account response is partial")

    votes_by_node: dict[str, list[dict[str, Any]]] = {}
    seen_vote_pubkeys: set[str] = set()
    for state, entries in (("current", current), ("delinquent", delinquent)):
        for entry in entries:
            if not isinstance(entry, dict):
                return _unavailable("vote-account entry is invalid")
            node_pubkey = entry.get("nodePubkey")
            vote_pubkey = entry.get("votePubkey")
            activated_stake = entry.get("activatedStake")
            if (not isinstance(node_pubkey, str) or not node_pubkey
                    or node_pubkey != node_pubkey.strip()
                    or not isinstance(vote_pubkey, str) or not vote_pubkey
                    or vote_pubkey != vote_pubkey.strip()
                    or not _nonnegative_int(activated_stake)):
                return _unavailable("vote-account entry is invalid")
            if vote_pubkey in seen_vote_pubkeys:
                return _unavailable("duplicate vote-account identity")
            seen_vote_pubkeys.add(vote_pubkey)
            votes_by_node.setdefault(node_pubkey, []).append({
                "vote_pubkey": vote_pubkey,
                "state": state,
                "activated_stake_lamports": activated_stake,
            })

    validators = []
    for identity, leader_slots, blocks_produced in sorted(production_rows):
        skipped_slots = leader_slots - blocks_produced
        votes = sorted(votes_by_node.get(identity, []), key=lambda row: row["vote_pubkey"])
        validators.append({
            "identity": identity,
            "leader_slots": leader_slots,
            "blocks_produced": blocks_produced,
            "skipped_slots": skipped_slots,
            "skip_rate": round(skipped_slots / leader_slots, 8),
            "vote_identity_matched": bool(votes),
            "vote_account_count": len(votes),
            "vote_accounts": votes,
            "activated_stake_lamports": (
                sum(vote["activated_stake_lamports"] for vote in votes) if votes else None
            ),
        })

    skipped_slots = total_leader_slots - total_blocks_produced
    normalized = {
        "available": True,
        "basis": "most recent fully completed epoch",
        "epoch": epoch,
        "first_slot": first_slot,
        "last_slot": last_slot,
        "context_slot": context_slot,
        "api_version": api_version,
        "leader_slots": total_leader_slots,
        "blocks_produced": total_blocks_produced,
        "skipped_slots": skipped_slots,
        "skip_rate": round(skipped_slots / total_leader_slots, 8),
        "skip_rate_definition": "skipped_slots / leader_slots",
        "vote_enrichment_observed_at": vote_observed_at,
        "source": {"method": "getBlockProduction", "commitment": "finalized"},
        "validators": validators,
    }
    if collection is not None:
        normalized["collection"] = collection
    return normalized

def sample_slots(head: int, samples: int = DEFAULT_SAMPLES,
                 window_slots: int = WINDOW_SLOTS) -> list[int]:
    """Evenly spaced slots across the window, oldest first.

    Even spacing rather than a random draw: fees are bursty and cyclical over a
    window, so a systematic sample covers temporal variation that a clustered
    one would miss entirely.
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
    message_signature_base_fee_lower_bound = unclassified_fee = total_fee = jito = 0
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
        signature_count = len(signatures) if isinstance(signatures, list) else 0

        # Instruction data is absent at this response detail level, so this can
        # prove only the message-signature floor. The residual may contain
        # priority fees, supported-precompile base fees, or both.
        tx_lower_bound = min(fee, BASE_FEE_LAMPORTS_PER_SIGNATURE * signature_count)
        message_signature_base_fee_lower_bound += tx_lower_bound
        unclassified_fee += fee - tx_lower_bound
        total_fee += fee
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
        # Solana's fee payer is the first account key. Other signers are not fee
        # payers and must not inflate the sampled address count.
        first_key = account_keys[0] if account_keys else None
        if isinstance(first_key, dict) and isinstance(first_key.get("pubkey"), str):
            fee_payers.add(first_key["pubkey"])

    block_time = block.get("blockTime")
    return {
        "slot": slot,
        "block_time": block_time if isinstance(block_time, int) else None,
        "tx_total": vote_count + nonvote_count,
        "tx_vote": vote_count,
        "tx_nonvote": nonvote_count,
        "tx_nonvote_failed": failed_count,
        "message_signature_base_fee_lower_bound_lamports": (
            message_signature_base_fee_lower_bound
        ),
        "unclassified_fee_lamports": unclassified_fee,
        "jito_lamports": jito,
        "fee_lamports": total_fee,
        "rev_lamports": total_fee + jito,
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


def _sol(lamports: float | None, digits: int = 9) -> float | None:
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
                  blocks_in_window: int | None,
                  observed_seconds: int | None = None) -> dict[str, Any]:
    """Real Economic Value: transaction fees + detected Jito tips.

    Reported first as what was actually observed, then as a sample-mean estimate
    for the exact sampled slot window. The per-block spread is published with a
    descriptive interval and an explicit sampling-bias limitation.
    """
    if not summaries:
        return {"available": False}

    per_block = [s["rev_lamports"] for s in summaries]
    mean_per_block = sum(per_block) / len(per_block)
    sample_mean_estimate = mean_per_block * blocks_in_window if blocks_in_window else None

    # Blocks vary enormously — repeat runs of this sampler have moved the window
    # estimate by a third. Publishing a six-figure point estimate without saying
    # how wide it is would imply a precision sixteen blocks cannot support, so
    # the interval is computed and rendered next to the number.
    low = high = None
    if sample_mean_estimate is not None and len(per_block) > 1:
        margin = 1.96 * (statistics.stdev(per_block) / math.sqrt(len(per_block)))
        low = max(0.0, (mean_per_block - margin) * blocks_in_window)
        high = (mean_per_block + margin) * blocks_in_window

    interval = None if low is None else {
        "low_sol": _sol(low, 2),
        "high_sol": _sol(high, 2),
        "method": "95% normal interval on the sampled-block mean",
        "limitation": (
            "Describes dispersion in sampled blocks only; it does not correct "
            "temporal or endpoint/network sampling bias."
        ),
    }

    transaction_fees = sum(s["fee_lamports"] for s in summaries)
    message_signature_lower_bound = sum(
        s["message_signature_base_fee_lower_bound_lamports"] for s in summaries
    )
    unclassified_fee = sum(s["unclassified_fee_lamports"] for s in summaries)

    return {
        "available": True,
        "definition": "transaction fees + detected Jito tips",
        "fee_decomposition": (
            "message-signature base-fee lower bound + unclassified residual; "
            "accounts-only block data cannot identify supported-precompile signatures, "
            "so priority fees are bounded between zero and the residual"
        ),
        "jito_tip_account_source": {
            "publisher": "Jito Labs",
            "label": "Jito Block Engine getTipAccounts",
            "url": JITO_TIP_ACCOUNT_SOURCE_URL,
            "source_revision": JITO_TIP_ACCOUNT_SOURCE_REVISION,
            "source_revision_date": "2026-08-13T14:14:15Z",
            "checked_at": "2026-08-29",
            "coverage": f"{len(JITO_TIP_ACCOUNTS)}/8",
            "license_state": (
                "no repository license detected; factual identifiers and source link only"
            ),
        },
        "sampled_sol": {
            "transaction_fees": _sol(transaction_fees),
            "message_signature_base_fee_lower_bound": _sol(message_signature_lower_bound),
            "unclassified_fee_residual": _sol(unclassified_fee),
            "jito_tips": _sol(sum(s["jito_lamports"] for s in summaries)),
            "total": _sol(sum(per_block)),
        },
        "per_block_sol": {
            "mean": _sol(mean_per_block),
            "min": _sol(min(per_block)),
            "max": _sol(max(per_block)),
        },
        "sample_mean_estimate_sol": _sol(sample_mean_estimate, 2),
        "sample_mean_interval": interval,
        "estimated": True,
        "estimated_blocks_in_window": blocks_in_window,
        "estimate_window_seconds": observed_seconds,
        "method": (
            "mean REV per sampled block x estimated blocks produced in the sampled "
            "slot window; elapsed time comes from sampled block timestamps"
        ),
        "limitation": (
            "Systematic block sampling can retain temporal and endpoint/network "
            "sampling bias; the interval describes sample dispersion, not total error."
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
    observed_slots = max(slots) - min(slots) if len(slots) > 1 else None
    blocks_in_window = (
        round(observed_slots * production_rate)
        if observed_slots and production_rate else None
    )
    first_block_time = min(times) if times else None
    last_block_time = max(times) if times else None
    observed_seconds = (
        last_block_time - first_block_time
        if first_block_time is not None and last_block_time is not None else None
    )

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
            "first_block_time": first_block_time,
            "last_block_time": last_block_time,
            "observed_seconds": observed_seconds,
            "sampling": "evenly spaced across the window",
        },
        "fees": summarize_fees(summaries),
        "rev": summarize_rev(summaries, blocks_in_window, observed_seconds),
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
    deadline = time.monotonic() + budget_seconds
    head = fetch_head(endpoint, timeout=max(0, min(30, budget_seconds)))
    if head is None:
        return {
            "available": False,
            "requires_api_key": False,
            "reason": "could not read the current slot from the endpoint",
        }

    remaining = deadline - time.monotonic()
    production_rate = (
        fetch_production_rate(head, endpoint, timeout=min(30, remaining))
        if remaining > 0 else None
    )
    # Fetch the newest evidence first; a truncated run must not contain only
    # the oldest end of the requested historical window.
    targets = list(reversed(sample_slots(head, samples)))
    summaries = []
    truncated = False
    for index, target in enumerate(targets):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            truncated = index < len(targets)
            break
        fetched = fetch_block(target, endpoint, min(timeout, remaining))
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
                        help=f"blocks to sample across {WINDOW_SLOTS:,} slots (default {DEFAULT_SAMPLES})")
    args = parser.parse_args()
    print(json.dumps(collect_activity(args.endpoint, args.samples), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
