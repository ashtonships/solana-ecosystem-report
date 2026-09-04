#!/usr/bin/env python3
"""Collect a point-in-time snapshot of Solana network state.

Standard library only. No API keys, no third-party packages, no accounts.
Data comes from public Solana JSON-RPC plus explicitly adopted, keyless source
metadata. Held sources are never contacted by the release-safe path.

Network access is isolated in this module and the bounded source collectors it
calls. Everything that shapes data is a pure function over already-fetched
results, so the transforms remain testable offline against fixtures.

    python3 collect.py                    # write a snapshot
    python3 collect.py --dry-run          # print, do not write
    python3 collect.py --endpoint <URL>   # use a different public RPC
    python3 collect.py --no-activity      # skip block sampling (much faster)
    python3 collect.py --no-news          # skip the keyless release/status feeds
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import blocks
import economics
import facts
import feature_accounts
import dune as dune_module
import growth as growth_module
import news as news_module
import pipeline
import transport

DEFAULT_ENDPOINT = "https://api.mainnet.solana.com"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
HISTORY_FACTS_PATH = Path(__file__).parent / "history" / "facts.jsonl"
LAMPORTS_PER_SOL = 1_000_000_000
PUBLICATION_FRESHNESS_SECONDS = 25_200
# 2 added `activity`; 3 added `news`; 4 adds non-vote performance and richer
# validator rows; 5 adds native inflation policy; 6 adds current status and
# CoinGecko freshness. 7 adds full validator and registry evidence arrays.
# 8 corrects sampled activity, fee decomposition, xStocks coverage, and adds
# completed-epoch validator production. 9 adds normalized editorial items and a
# replay-stable featured story.
# Additive only — a v1 snapshot still reads, and every consumer looks up each field defensively.
SCHEMA_VERSION = 9


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def source_code_state(root: Path = Path(__file__).parent) -> dict[str, Any]:
    """Return the exact Git revision and whether collection code is dirty."""
    revision = os.environ.get("GITHUB_SHA")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=False,
                capture_output=True, text=True, timeout=2,
            )
            revision = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            revision = None
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        revision = None
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".",
             ":(exclude)snapshots/**", ":(exclude)history/**",
             ":(exclude)state/**", ":(exclude)preview/**", ":(exclude)dist/**"],
            cwd=root, check=False,
            capture_output=True, text=True, timeout=2,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        dirty = None
    return {"source_revision": revision, "source_tree_dirty": dirty}

# The collector asks for these methods individually. Several public endpoints
# accept small JSON-RPC calls but stall indefinitely on the combined response
# (especially when getVoteAccounts is present). Bounded calls are slower by
# a few seconds and materially more reliable; one failed method degrades only
# its own section.
RPC_CALLS: list[tuple[str, list[Any]]] = [
    ("getHealth", []),
    ("getSlot", []),
    ("getEpochInfo", []),
    ("getEpochSchedule", []),
    ("getRecentPerformanceSamples", [8]),
    ("getSupply", [{"commitment": "finalized", "excludeNonCirculatingAccountsList": True}]),
    ("getVoteAccounts", []),
    ("getInflationRate", []),
    ("getInflationGovernor", [{"commitment": "finalized"}]),
]


class CollectionError(RuntimeError):
    """The RPC endpoint could not be reached or returned an unusable body."""


# ── network boundary ─────────────────────────────────────────────────────────

def fetch_rpc_call(
    endpoint: str,
    index: int,
    method: str,
    params: list[Any],
    timeout: int,
) -> dict[str, Any]:
    """Fetch one JSON-RPC method so a large method cannot stall the whole run."""
    payload = {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(transport.read_bounded(response).decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise CollectionError(f"RPC returned HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise CollectionError(f"RPC unreachable: {error}") from error
    except json.JSONDecodeError as error:
        raise CollectionError("RPC returned a body that is not JSON") from error
    except ValueError as error:
        raise CollectionError(str(error)) from error

    if not isinstance(body, dict):
        raise CollectionError(f"{method} returned a body that is not a JSON-RPC object")
    return body


def fetch_rpc(endpoint: str = DEFAULT_ENDPOINT, timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch every core method; preserve partial results and fail only if all fail."""
    batch: list[dict[str, Any]] = []
    succeeded = 0
    for index, (method, params) in enumerate(RPC_CALLS):
        try:
            batch.append(fetch_rpc_call(endpoint, index, method, params, timeout))
            succeeded += 1
        except CollectionError as error:
            batch.append({
                "jsonrpc": "2.0",
                "id": index,
                "error": {"code": -1, "message": str(error)},
            })
    if not succeeded:
        raise CollectionError("all RPC methods failed")
    return batch


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
            return json.loads(transport.read_bounded(response).decode("utf-8")).get("result")
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
        if not isinstance(position, int) or isinstance(position, bool) or not 0 <= position < len(RPC_CALLS):
            continue
        method = RPC_CALLS[position][0]
        indexed[method] = entry.get("error") if "error" in entry else entry.get("result")
    return indexed


def summarize_performance(samples: Any) -> dict[str, Any]:
    """Derive total/non-vote TPS and slot time from one RPC sample window."""
    if not isinstance(samples, list) or not samples:
        return {"available": False}

    per_sample = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        period = sample.get("samplePeriodSecs") or 0
        transactions = sample.get("numTransactions")
        non_vote_transactions = sample.get("numNonVoteTransactions")
        slots = sample.get("numSlots")
        if not is_finite_number(period) or period <= 0:
            continue
        if not is_finite_number(transactions) or transactions < 0:
            continue
        if not is_finite_number(slots) or slots <= 0:
            continue
        total_tps = round(transactions / period, 2)
        non_vote_valid = (
            is_finite_number(non_vote_transactions)
            and 0 <= non_vote_transactions <= transactions
        )
        non_vote_tps = round(non_vote_transactions / period, 2) if non_vote_valid else None
        vote_tps = round((transactions - non_vote_transactions) / period, 2) if non_vote_valid else None
        vote_share_pct = (
            round(100 * (transactions - non_vote_transactions) / transactions, 2)
            if non_vote_valid and transactions else None
        )
        per_sample.append({
            "slot": sample.get("slot"),
            "transactions": transactions,
            "non_vote_transactions": non_vote_transactions if non_vote_valid else None,
            "sample_period_secs": period,
            "slots": slots,
            "tps": total_tps,
            "non_vote_tps": non_vote_tps,
            "vote_tps": vote_tps,
            "vote_share_pct": vote_share_pct,
            "slot_time_secs": round(period / slots, 3),
        })

    if not per_sample:
        return {"available": False}

    non_vote_values = [sample["non_vote_tps"] for sample in per_sample
                       if sample["non_vote_tps"] is not None]
    total_period = sum(sample["sample_period_secs"] for sample in per_sample)
    total_transactions = sum(sample["transactions"] for sample in per_sample)
    non_vote_samples = [sample for sample in per_sample if sample["non_vote_transactions"] is not None]
    non_vote_period = sum(sample["sample_period_secs"] for sample in non_vote_samples)
    non_vote_transactions = sum(sample["non_vote_transactions"] for sample in non_vote_samples)
    comparable_transactions = sum(sample["transactions"] for sample in non_vote_samples)
    return {
        "available": True,
        "samples_used": len(per_sample),
        "sample_period_seconds": total_period,
        "latest_tps": per_sample[0]["tps"],
        "mean_tps": round(total_transactions / total_period, 2),
        "peak_tps": max(s["tps"] for s in per_sample),
        "non_vote_available": bool(non_vote_values),
        "latest_non_vote_tps": per_sample[0]["non_vote_tps"],
        "mean_non_vote_tps": round(non_vote_transactions / non_vote_period, 2)
        if non_vote_period else None,
        "peak_non_vote_tps": max(non_vote_values) if non_vote_values else None,
        "mean_vote_share_pct": round(
            100 * (comparable_transactions - non_vote_transactions) / comparable_transactions, 2
        ) if comparable_transactions else None,
        "mean_slot_time_secs": round(
            total_period / sum(sample["slots"] for sample in per_sample),
            3,
        ),
        "samples": per_sample,
    }


def summarize_validators(vote_accounts: Any, top_n: int = 30) -> dict[str, Any]:
    """Condense vote-account state without converting missing stake to zero."""
    if not isinstance(vote_accounts, dict):
        return {"available": False}

    if "current" not in vote_accounts or "delinquent" not in vote_accounts:
        return {"available": False}
    current_raw = vote_accounts.get("current")
    delinquent_raw = vote_accounts.get("delinquent")
    if not isinstance(current_raw, list) or not isinstance(delinquent_raw, list):
        return {"available": False}
    current = [entry for entry in current_raw if isinstance(entry, dict)]
    delinquent = [entry for entry in delinquent_raw if isinstance(entry, dict)]

    def stake(entry: dict[str, Any]) -> int | None:
        value = entry.get("activatedStake")
        if not is_finite_number(value) or value < 0:
            return None
        return int(value)

    def sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
        amount = stake(entry)
        return (
            amount is None,
            -(amount or 0),
            str(entry.get("nodePubkey") or ""),
            str(entry.get("votePubkey") or ""),
        )

    active_values = [amount for entry in current if (amount := stake(entry)) is not None]
    delinquent_values = [amount for entry in delinquent if (amount := stake(entry)) is not None]
    active_stake = sum(active_values)
    delinquent_stake = sum(delinquent_values)
    total_stake = active_stake + delinquent_stake
    total_vote_accounts = len(current) + len(delinquent)
    accounts_with_stake = len(active_values) + len(delinquent_values)
    accounts_missing_stake = total_vote_accounts - accounts_with_stake

    def validator_row(
        entry: dict[str, Any], state: str, rank: int, denominator: int,
    ) -> dict[str, Any]:
        amount = stake(entry)
        return {
            "rank": rank,
            "identity": entry.get("nodePubkey"),
            "vote_account": entry.get("votePubkey"),
            "state": state,
            "stake_lamports": amount,
            "stake_sol": round(amount / LAMPORTS_PER_SOL, 2) if amount is not None else None,
            "share_pct": round(100 * amount / denominator, 3)
            if amount is not None and denominator else None,
            "commission": entry.get("commission")
            if isinstance(entry.get("commission"), (int, float)) else None,
            "inflation_rewards_commission_bps": entry.get("inflationRewardsCommissionBps")
            if isinstance(entry.get("inflationRewardsCommissionBps"), int) else None,
            "epoch_vote_account": entry.get("epochVoteAccount")
            if isinstance(entry.get("epochVoteAccount"), bool) else None,
            "last_vote": entry.get("lastVote") if isinstance(entry.get("lastVote"), int) else None,
            "root_slot": entry.get("rootSlot") if isinstance(entry.get("rootSlot"), int) else None,
        }

    ranked_current = sorted(current, key=sort_key)
    top = [
        validator_row(entry, "current", index, active_stake)
        for index, entry in enumerate(ranked_current[:top_n], start=1)
    ]

    combined = [(entry, "current") for entry in current] + [
        (entry, "delinquent") for entry in delinquent
    ]
    combined.sort(key=lambda item: sort_key(item[0]))
    all_validators = [
        validator_row(entry, state, index, total_stake)
        for index, (entry, state) in enumerate(combined, start=1)
    ]
    ranked_validators = all_validators[:top_n]
    top_delinquent = [
        validator_row(entry, "delinquent", index, total_stake)
        for index, entry in enumerate(sorted(delinquent, key=sort_key)[:min(10, top_n)], start=1)
    ]

    nakamoto = 0
    if active_stake:
        running = 0
        for validator in ranked_current:
            amount = stake(validator)
            if amount is None:
                continue
            running += amount
            nakamoto += 1
            if running > active_stake / 3:
                break

    commissions = sorted(
        entry["commission"] for entry in current
        if isinstance(entry.get("commission"), (int, float))
        and not isinstance(entry.get("commission"), bool)
    )
    commission_stats: dict[str, Any] = {"available": False}
    if commissions:
        midpoint = len(commissions) // 2
        commission_stats = {
            "available": True,
            "median_pct": commissions[midpoint] if len(commissions) % 2
            else (commissions[midpoint - 1] + commissions[midpoint]) / 2,
            "mean_pct": round(sum(commissions) / len(commissions), 2),
            "zero_commission_count": sum(1 for value in commissions if value == 0),
            "max_commission_count": sum(1 for value in commissions if value == 100),
        }

    top_ten_stake = 0
    for entry in ranked_current[:10]:
        amount = stake(entry)
        if amount is not None:
            top_ten_stake += amount
    return {
        "available": True,
        "active_count": len(current),
        "delinquent_count": len(delinquent),
        "commission": commission_stats,
        "delinquent_pct": round(100 * len(delinquent) / total_vote_accounts, 2)
        if total_vote_accounts else 0.0,
        "active_stake_sol": round(active_stake / LAMPORTS_PER_SOL, 2),
        "delinquent_stake_sol": round(delinquent_stake / LAMPORTS_PER_SOL, 2),
        "accounts_with_stake": accounts_with_stake,
        "accounts_missing_stake": accounts_missing_stake,
        "stake_share_basis": "current and delinquent vote accounts with numeric activated stake",
        "nakamoto_coefficient": nakamoto,
        "top_10_share_pct": round(100 * top_ten_stake / active_stake, 2) if active_stake else None,
        "ranked_validator_limit": top_n,
        "ranked_validator_count": len(ranked_validators),
        "all_validator_count": len(all_validators),
        "ranked_validators": ranked_validators,
        "all_validators": all_validators,
        "top_validators": top,
        "top_delinquent": top_delinquent,
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
    required = ("total", "circulating", "nonCirculating")
    if not all(is_finite_number(value.get(key)) and value[key] >= 0 for key in required):
        return {"available": False}
    total = value["total"]
    circulating = value["circulating"]
    return {
        "available": True,
        "total_sol": round(total / LAMPORTS_PER_SOL, 2),
        "circulating_sol": round(circulating / LAMPORTS_PER_SOL, 2),
        "non_circulating_sol": round(value["nonCirculating"] / LAMPORTS_PER_SOL, 2),
        "circulating_pct": round(100 * circulating / total, 2) if total else None,
    }


def summarize_inflation(rate: Any, governor: Any) -> dict[str, Any]:
    """Current effective inflation plus the native policy curve, in percent."""
    rate = rate if isinstance(rate, dict) else {}
    governor = governor if isinstance(governor, dict) else {}

    def percent(value: Any) -> float | None:
        if not is_finite_number(value) or value < 0:
            return None
        return round(float(value) * 100, 4)

    current_total = percent(rate.get("total"))
    current_validator = percent(rate.get("validator"))
    current_foundation = percent(rate.get("foundation"))
    initial = percent(governor.get("initial"))
    terminal = percent(governor.get("terminal"))
    taper = percent(governor.get("taper"))
    foundation = percent(governor.get("foundation"))
    foundation_term = governor.get("foundationTerm")
    foundation_term = float(foundation_term) if is_finite_number(foundation_term) else None
    epoch = rate.get("epoch") if isinstance(rate.get("epoch"), int) else None
    return {
        "available": any(value is not None for value in (
            current_total, current_validator, current_foundation,
            initial, terminal, taper, foundation,
        )),
        "current_total_pct": current_total,
        "current_validator_pct": current_validator,
        "current_foundation_pct": current_foundation,
        "epoch": epoch,
        "initial_pct": initial,
        "terminal_pct": terminal,
        "taper_pct": taper,
        "foundation_pct": foundation,
        "foundation_term_years": foundation_term,
        "source_methods": ["getInflationRate", "getInflationGovernor"],
    }


def build_snapshot(
    indexed: dict[str, Any],
    collected_at: str,
    endpoint: str,
    block_time: int | None = None,
    block_production: dict[str, Any] | None = None,
    economics: dict[str, Any] | None = None,
    activity: dict[str, Any] | None = None,
    news: dict[str, Any] | None = None,
    growth: dict[str, Any] | None = None,
    dune: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    feature_activation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable snapshot. Pure — no network, no clock."""
    health = indexed.get("getHealth")
    slot = indexed.get("getSlot")
    if health == "ok":
        health_state: bool | None = True
        health_raw = "ok"
    elif isinstance(health, dict) and health.get("code") == -32005:
        health_state = False
        health_raw = "unhealthy"
    else:
        health_state = None
        health_raw = "unavailable"

    epoch = summarize_epoch(indexed.get("getEpochInfo"))
    performance = summarize_performance(indexed.get("getRecentPerformanceSamples"))
    remaining_slots = None
    if epoch.get("available"):
        slot_index = epoch.get("slot_index")
        slots_in_epoch = epoch.get("slots_in_epoch")
        if isinstance(slot_index, int) and isinstance(slots_in_epoch, int):
            remaining_slots = max(0, slots_in_epoch - slot_index)
    slot_times = sorted(
        float(sample["slot_time_secs"])
        for sample in performance.get("samples", [])
        if isinstance(sample, dict) and isinstance(sample.get("slot_time_secs"), (int, float))
    )
    median_slot_time = None
    if slot_times:
        midpoint = len(slot_times) // 2
        median_slot_time = (
            slot_times[midpoint] if len(slot_times) % 2
            else (slot_times[midpoint - 1] + slot_times[midpoint]) / 2
        )
    if remaining_slots is not None and median_slot_time is not None:
        estimated_seconds = round(remaining_slots * median_slot_time)
        epoch["remaining_slots"] = remaining_slots
        epoch["slot_time_statistic"] = "median"
        epoch["recent_median_slot_time_secs"] = round(median_slot_time, 3)
        epoch["eta_samples_used"] = len(slot_times)
        epoch["estimated_remaining_seconds"] = estimated_seconds
        epoch["eta_basis"] = "median recent slot time"
        try:
            observed = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
            epoch["estimated_end_at"] = (
                observed + timedelta(seconds=estimated_seconds)
            ).isoformat(timespec="seconds")
        except ValueError:
            epoch["estimated_end_at"] = None
    else:
        epoch["remaining_slots"] = remaining_slots
        epoch["slot_time_statistic"] = "median"
        epoch["recent_median_slot_time_secs"] = None
        epoch["eta_samples_used"] = len(slot_times)
        epoch["estimated_remaining_seconds"] = None
        epoch["estimated_end_at"] = None
        epoch["eta_basis"] = "unavailable"

    endpoint_reference = growth_module.rpc_endpoint_reference(endpoint)
    activity_output = activity if activity is not None else {"available": False}
    if isinstance(activity_output, dict) and isinstance(activity_output.get("source"), dict):
        activity_output = copy.deepcopy(activity_output)
        activity_output["source"] = {
            **endpoint_reference,
            "method": activity_output["source"].get("method"),
        }
        activity_output["requires_api_key"] = (
            False
            if endpoint_reference["endpoint"] in growth_module.PUBLIC_RPC_ENDPOINTS
            else None
        )
    validators = summarize_validators(indexed.get("getVoteAccounts"))
    validators["block_production"] = (
        block_production if block_production is not None else {"available": False}
    )
    news_output = copy.deepcopy(news) if isinstance(news, dict) else {
        "available": False,
        "partial": False,
        "requires_api_key": False,
        "featured_item_id": None,
        "items": [],
    }
    editorial_items = news_output.get("items")
    if isinstance(editorial_items, list):
        for item in editorial_items:
            if isinstance(item, dict):
                item["recorded_at"] = collected_at
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": collected_at,
        "provenance": provenance if provenance is not None else {
            "source_revision": None, "source_tree_dirty": None,
        },
        "source": {
            **endpoint_reference,
            "method": "solana json-rpc",
            "requires_api_key": (
                False
                if endpoint_reference["endpoint"] in growth_module.PUBLIC_RPC_ENDPOINTS
                else None
            ),
        },
        "network": {
            # getHealth describes the responding RPC endpoint, not the whole
            # network. Missing/transport-failed calls remain unavailable.
            "healthy": health_state,
            "health_raw": health_raw,
            "health_scope": "rpc_endpoint",
            "health_method": "getHealth",
            "slot": slot if isinstance(slot, int) else None,
            "block_time_unix": block_time,
        },
        "epoch": epoch,
        "performance": performance,
        "supply": summarize_supply(indexed.get("getSupply")),
        "inflation": summarize_inflation(
            indexed.get("getInflationRate"), indexed.get("getInflationGovernor"),
        ),
        "validators": validators,
        "economics": economics if economics is not None else {"available": False},
        "activity": activity_output,
        "news": news_output,
        "growth": growth if growth is not None else {"available": False},
        # 'dune' is recorded only when explicitly collected (with_dune=True);
        # default snapshots keep their exact prior shape.
        **({"dune": dune} if dune is not None else {}),
        **({"feature_activation": feature_activation} if feature_activation is not None else {}),
    }


def snapshot_filename(collected_at: str) -> str:
    """UTC timestamp, filesystem-safe, sorts chronologically."""
    return f"snapshot-{collected_at.replace(':', '').replace('-', '').replace('.', '-')}.json"


def _check_immutable_text(path: Path, text: str) -> bool:
    """Return whether the exact immutable payload exists; reject collisions."""
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        return False
    if existing != text.encode("utf-8"):
        raise FileExistsError(f"immutable snapshot collision: {path}")
    return True


def _write_immutable_text(path: Path, text: str) -> None:
    """Publish a complete immutable file without replacing an existing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise FileExistsError(f"immutable snapshot collision: {path}")
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_replace_text(path: Path, text: str) -> None:
    """Replace a mutable text file atomically with a fully flushed payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def apply_activity_last_known_good(
    snapshot: dict[str, Any], previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry a prior block sample only within the publication freshness limit."""
    result = copy.deepcopy(snapshot)

    def evidence_age(activity, fallback):
        window = activity.get("window")
        observed = window.get("last_block_time") if isinstance(window, dict) else None
        try:
            now = datetime.fromisoformat(str(result.get("collected_at")).replace("Z", "+00:00"))
            event = (
                datetime.fromtimestamp(observed, timezone.utc)
                if is_finite_number(observed)
                else datetime.fromisoformat(str(fallback).replace("Z", "+00:00"))
            )
            if now.utcoffset() is None or event.utcoffset() is None:
                return None
            return (now - event).total_seconds()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    current = result.get("activity")
    if isinstance(current, dict) and current.get("available") is True:
        age_seconds = evidence_age(current, result.get("collected_at"))
        stale = age_seconds is None or age_seconds < 0 or age_seconds > PUBLICATION_FRESHNESS_SECONDS
        current["source_state"] = "stale" if stale else "fresh"
        current["last_success_at"] = result.get("collected_at")
        current["carried_forward_at"] = None
        current["age_seconds"] = round(age_seconds) if age_seconds is not None else None
        current["stale"] = stale
        return result
    prior = previous.get("activity") if isinstance(previous, dict) else None
    if not isinstance(prior, dict) or prior.get("available") is not True:
        return result
    previous_collected = previous.get("collected_at") if isinstance(previous, dict) else None
    last_success = prior.get("last_success_at") or previous_collected
    age_seconds = evidence_age(prior, last_success)
    if age_seconds is None or age_seconds < 0 or age_seconds > PUBLICATION_FRESHNESS_SECONDS:
        return result
    carried = copy.deepcopy(prior)
    carried["source_state"] = "last_known_good"
    carried["last_success_at"] = last_success
    carried["carried_forward_at"] = result.get("collected_at")
    carried["age_seconds"] = round(age_seconds)
    carried["stale"] = True
    result["activity"] = carried
    return result


# ── entry point ──────────────────────────────────────────────────────────────

def sources(
    endpoint: str = DEFAULT_ENDPOINT,
    with_economics: bool = False,
    with_price: bool = False,
    with_activity: bool = True,
    with_news: bool = True,
    samples: int = blocks.DEFAULT_SAMPLES,
    with_growth: bool = True,
    supply_state: dict[str, Any] | None = None,
    with_dune: bool = False,
) -> dict[str, Any]:
    """Stage 1 — collect attributable inputs.

    Public Solana RPC, sampled block bodies, selected optional data providers,
    and accepted first-party metadata. A failed source remains independently
    unavailable; release-held sources are not fetched by the default path.
    The approved CoinGecko Demo price transport is the only keyed third-party
    source, and only when explicitly requested with ``with_price``.
    The Dune adapter runs only when explicitly requested with ``with_dune``:
    it is keyed (DUNE_API_KEY) and unconfigured (DUNE_QUERY_ID) until Ashton's
    query exists, so the default path never touches it.
    """
    batch = fetch_rpc(endpoint)
    indexed = index_results(batch)
    slot = indexed.get("getSlot")
    block_time = fetch_block_time(slot, endpoint) if isinstance(slot, int) else None
    epoch_range = blocks.completed_epoch_range(
        indexed.get("getEpochInfo"), indexed.get("getEpochSchedule"),
    )
    raw_production = blocks.fetch_block_production(epoch_range, endpoint)
    vote_observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block_production = blocks.normalize_block_production(
        raw_production, indexed.get("getVoteAccounts"), epoch_range, vote_observed_at,
    )
    # Economic sources are third-party and optional: a failure there degrades
    # that section only, and never blocks the on-chain snapshot.
    if with_economics:
        econ = economics.collect_economics()
    elif with_price:
        econ = economics.collect_price_economics()
    else:
        econ = None
    # Block sampling is the slow part of a run — a dozen multi-megabyte bodies
    # against a rate-limited public endpoint. Same rule as economics: it fails
    # to `available: false` on its own and never takes the snapshot with it.
    activity = blocks.collect_activity(endpoint, samples) if with_activity else None
    # Adopted release and status metadata is recorded into the snapshot so
    # rendering never re-fetches, and a broken source costs this section alone.
    feeds = news_module.collect_news() if with_news else None
    feature_activation = feature_accounts.collect_feature_accounts(endpoint)
    if with_growth:
        growth_data, next_supply_state = growth_module.collect_growth(
            endpoint, supply_state=supply_state,
        )
    else:
        growth_data = None
        next_supply_state = None
    # Dune is keyed and paid (credits); like economics it degrades to
    # `available: false` on its own and never blocks the on-chain snapshot.
    dune_data = dune_module.collect_dune() if with_dune else None
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "indexed": indexed,
        "collected_at": collected_at,
        "endpoint": endpoint,
        "block_time": block_time,
        "block_production": block_production,
        "economics": econ,
        "activity": activity,
        "news": feeds,
        "feature_activation": feature_activation,
        "growth": growth_data,
        "dune": dune_data,
        "provenance": source_code_state(),
        "_growth_supply_state": next_supply_state,
    }


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Stage 2 — apply deterministic transforms.

    Numeric types, UTC observation times, and stable field definitions.
    Malformed values degrade to unavailable inside the summarizers.
    """
    return build_snapshot(
        raw["indexed"], raw["collected_at"], raw["endpoint"], raw.get("block_time"),
        block_production=raw.get("block_production"),
        economics=raw.get("economics"), activity=raw.get("activity"),
        news=raw.get("news"), growth=raw.get("growth"),
        dune=raw.get("dune"),
        provenance=raw.get("provenance"),
        feature_activation=raw.get("feature_activation"),
    )


def collect(
    endpoint: str = DEFAULT_ENDPOINT,
    with_economics: bool = False,
    with_price: bool = False,
    with_activity: bool = True,
    with_news: bool = True,
    samples: int = blocks.DEFAULT_SAMPLES,
    with_growth: bool = True,
    with_dune: bool = False,
) -> dict[str, Any]:
    """One-shot Sources → Normalize → Validate without persisting cursor state.

    The command-line entry point uses ``collect_with_state`` and persists its
    returned cursor only after the validated snapshot files are written.
    """
    snapshot, _ = collect_with_state(
        endpoint,
        with_economics=with_economics,
        with_price=with_price,
        with_activity=with_activity,
        with_news=with_news,
        samples=samples,
        with_growth=with_growth,
        with_dune=with_dune,
    )
    return snapshot


def collect_with_state(
    endpoint: str = DEFAULT_ENDPOINT,
    with_economics: bool = False,
    with_price: bool = False,
    with_activity: bool = True,
    with_news: bool = True,
    samples: int = blocks.DEFAULT_SAMPLES,
    with_growth: bool = True,
    supply_state: dict[str, Any] | None = None,
    with_dune: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate a snapshot and return, but do not persist, its next cursor."""
    raw = sources(
        endpoint,
        with_economics=with_economics,
        with_price=with_price,
        with_activity=with_activity,
        with_news=with_news,
        samples=samples,
        with_growth=with_growth,
        supply_state=supply_state,
        with_dune=with_dune,
    )
    next_supply_state = raw.pop("_growth_supply_state", None)
    snapshot = pipeline.validate(normalize(raw))
    return snapshot, next_supply_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a Solana network snapshot.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--dry-run", action="store_true", help="print the snapshot without writing it")
    parser.add_argument("--out-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--facts-path", type=Path, default=HISTORY_FACTS_PATH)
    parser.add_argument(
        "--growth-state-path", type=Path,
        help="override the tokenized-equity supply cursor path",
    )
    economics_group = parser.add_mutually_exclusive_group()
    economics_group.add_argument(
        "--with-economics", dest="with_economics", action="store_true",
        help="include held third-party economics in --dry-run research output only",
    )
    economics_group.add_argument(
        "--no-economics", dest="with_economics", action="store_false",
        help="skip held third-party economic sources (the default)",
    )
    economics_group.add_argument(
        "--with-price", dest="with_price", action="store_true",
        help=(
            "include the approved CoinGecko Demo price observation "
            "(uses COINGECKO_DEMO_API_KEY; rights-held sources stay held)"
        ),
    )
    parser.set_defaults(with_economics=False, with_price=False)
    parser.add_argument(
        "--with-dune", dest="with_dune", action="store_true",
        help=(
            "include the Dune query section (uses DUNE_API_KEY + DUNE_QUERY_ID; "
            "reports unavailable when unconfigured; off by default)"
        ),
    )
    parser.set_defaults(with_dune=False)
    parser.add_argument("--no-activity", action="store_true",
                        help="skip block sampling for fees, REV and address activity")
    parser.add_argument("--no-news", action="store_true",
                        help="skip the adopted keyless Agave and Solana Status sources")
    parser.add_argument("--no-growth", action="store_true",
                        help="skip tokenized-equity registry, supply and market context")
    parser.add_argument("--samples", type=int, default=blocks.DEFAULT_SAMPLES,
                        help=(
                            f"blocks to sample across {blocks.WINDOW_SLOTS:,} slots "
                            f"(default {blocks.DEFAULT_SAMPLES})"
                        ))
    args = parser.parse_args()

    if args.with_economics and not args.dry_run:
        print(
            "collection failed: --with-economics is held-data research and requires --dry-run",
            file=sys.stderr,
        )
        return 2

    supply_state = None
    if not args.no_growth:
        supply_state = (
            growth_module.load_supply_state(args.growth_state_path)
            if args.growth_state_path is not None
            else growth_module.load_supply_state()
        )
    try:
        snapshot, next_supply_state = collect_with_state(
            args.endpoint,
            with_economics=args.with_economics,
            with_price=args.with_price,
            with_activity=not args.no_activity,
            with_news=not args.no_news,
            with_growth=not args.no_growth,
            with_dune=args.with_dune,
            samples=args.samples,
            supply_state=supply_state,
        )
    except CollectionError as error:
        print(f"collection failed: {error}", file=sys.stderr)
        return 1

    previous = None
    previous_path = args.out_dir / "latest.json"
    if previous_path.exists():
        try:
            candidate = json.loads(previous_path.read_text(encoding="utf-8"))
            previous = candidate if isinstance(candidate, dict) else None
        except (OSError, json.JSONDecodeError, ValueError):
            previous = None
    snapshot = apply_activity_last_known_good(snapshot, previous)
    if isinstance(previous, dict) and isinstance(snapshot.get("news"), dict):
        snapshot["news"] = news_module.apply_last_known_good(
            snapshot["news"], previous.get("news"), previous.get("collected_at"),
        )
    gate = pipeline.check_publishable(
        snapshot,
        max_age_seconds=PUBLICATION_FRESHNESS_SECONDS,
        allow_release_held=args.dry_run and args.with_economics,
    )
    if not gate["publishable"]:
        print(json.dumps({
            "collection failed": "candidate is not publishable",
            "failures": gate["failures"],
        }, indent=2), file=sys.stderr)
        return 1
    try:
        serialized = json.dumps(snapshot, indent=2, allow_nan=False) + "\n"
        fact_pack = facts.snapshot_facts(snapshot)
        facts.jsonl_additions(args.facts_path, fact_pack)
    except (facts.FactConflictError, OSError, TypeError, ValueError) as error:
        print(f"collection failed: fact history conflict: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(serialized, end="")
        return 0

    path = args.out_dir / snapshot_filename(snapshot["collected_at"])
    try:
        immutable_exists = _check_immutable_text(path, serialized)
        if not immutable_exists:
            _write_immutable_text(path, serialized)
        facts.append_jsonl(args.facts_path, fact_pack)
        if not args.no_growth and next_supply_state is not None:
            if args.growth_state_path is None:
                growth_module.save_supply_state(next_supply_state)
            else:
                growth_module.save_supply_state(next_supply_state, args.growth_state_path)
        # This mutable copy is the publication commit marker and is always last.
        _atomic_replace_text(args.out_dir / "latest.json", serialized)
    except (facts.FactConflictError, OSError, TypeError, ValueError) as error:
        print(f"collection failed: persistence: {error}", file=sys.stderr)
        return 1

    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
