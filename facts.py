#!/usr/bin/env python3
"""Versioned metric facts shared by anomaly, delta, and chart consumers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


MIN_PRIOR_SPACING_SECONDS = 5 * 60 * 60
VALID_BASES = frozenset(("measured", "sampled", "estimated", "recorded"))
VALID_STATES = frozenset(("current", "stale", "partial", "unavailable"))
ECONOMICS_PUBLICATION_HOLD = (
    "Historical economics values are not republished while the selected snapshot's "
    "economics sources are unavailable or release-held."
)
ECONOMICS_METRIC_SECTIONS = ("price", "tvl", "stablecoins", "dex")
PROVIDER_BENCHMARK_SOURCE = "solana-data-provider-benchmark"
BLOCK_SAMPLE_SOURCE = "solana-rpc-block-sample"
# Measurement sources whose repeats can partially overlap: provider rows
# arrive from a sliding window that providers may revise, and sampled block
# windows from consecutive runs can share boundary block times while sampling
# different subsets. Facts from these sources carry a semantic payload hash
# in their identity: an exact rerun is an idempotent no-op, a changed value
# appends as a distinct retained revision, and dedup stays deterministic.
REVISIONABLE_SOURCES = frozenset((PROVIDER_BENCHMARK_SOURCE, BLOCK_SAMPLE_SOURCE))
VALIDATOR_COMMISSION_SOURCE = "solana-rpc-getVoteAccounts"
VALIDATOR_COMMISSION_FACT_CONTRACT = "validator-commission-v2-no-source-slot"
VALIDATOR_COMMISSION_QUALITY = "getVoteAccounts exposes no source-native observation slot"
SELECTED_STABLECOIN_METRIC_ID = "selected_usd_stablecoin_total_supply"
SELECTED_STABLECOIN_SHARE_METRIC_ID = "selected_usd_stablecoin_share_of_selected_total"
SELECTED_STABLECOIN_SOURCE = "solana-rpc-getTokenSupply-finalized"
SELECTED_STABLECOIN_SOURCE_REVISION = "46091c373d7681a469e4130155187503def93387"
SELECTED_STABLECOIN_SOURCE_PATH = (
    "apps/docs/content/docs/en/payments/production-readiness.mdx"
)
SELECTED_STABLECOIN_SOURCE_URL = (
    "https://github.com/solana-foundation/solana-com/blob/"
    f"{SELECTED_STABLECOIN_SOURCE_REVISION}/{SELECTED_STABLECOIN_SOURCE_PATH}"
    "#L477-L480"
)
SELECTED_STABLECOIN_SOURCE_KEY = (
    "solana-foundation/solana-com:selected-usd-stablecoins"
)
SELECTED_STABLECOIN_SOURCE_LICENSE = "GPL-3.0"
SELECTED_STABLECOIN_REGISTRY_USAGE = "factual mint and issuer identifiers only"
SELECTED_STABLECOIN_UNIT = "selected stablecoin token units"
SELECTED_STABLECOIN_SUMMARY_BASIS = "finalized on-chain total token supply"
SELECTED_STABLECOIN_FACT_VALUE_CONTRACT = (
    "float projection; exact value retained in total_supply_decimal and raw_amount"
)
SELECTED_STABLECOIN_LEGACY_FACT_CONTRACT = "selected-stablecoin-supply-v1"
SELECTED_STABLECOIN_FACT_CONTRACT = "selected-stablecoin-supply-v2"
SELECTED_STABLECOIN_FACT_COVERAGE_BASIS = (
    "one observed mint in the pinned four-mint selected USD stablecoin set"
)
SELECTED_STABLECOIN_FACT_QUALITY = (
    "finalized per-mint total supply; not circulating supply or full stablecoin coverage"
)
SELECTED_STABLECOIN_LIMITATIONS = (
    "Exactly four selected USD stablecoin mints; broader universe coverage "
    "is unknown, so this does not represent all stablecoins. Total token "
    "supply is not circulating supply, USD value, liquidity, reserves, or "
    "executable depth. RPC context slots are retained; event time is unavailable."
)
SELECTED_STABLECOIN_IDENTITIES = (
    ("USDC", "Circle", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
    ("USDT", "Tether", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
    ("PYUSD", "PayPal USD issued by Paxos",
     "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"),
    ("USDG", "Paxos Digital Singapore",
     "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"),
)
SELECTED_STABLECOIN_TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "spl-token",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "spl-token-2022",
}
SPL_TOKEN_U64_MAX = 2**64 - 1
XSTOCK_METRIC_ID = "xstock_labelled_mint_total_supply"
XSTOCK_SOURCE = "solana-rpc-getTokenSupply-finalized-scaled-ui"
XSTOCK_LEGACY_SOURCE = "solana-rpc-getTokenSupply-finalized-ui"
XSTOCK_FACT_CONTRACT = "xstock-labelled-mint-supply-v2"
XSTOCK_FACT_VALUE_CONTRACT = (
    "float projection of rpc_ui_amount_string; exact raw amount, decimals, and UI string retained"
)
XSTOCK_FACT_COVERAGE_BASIS = (
    "one observed labelled mint in the pinned 107-mint registry"
)
XSTOCK_FACT_QUALITY = (
    "finalized per-mint Token-2022 scaled UI total supply; no heterogeneous aggregate"
)
XSTOCK_LEGACY_FACT_QUALITY = (
    "finalized per-mint legacy SPL Token UI total supply; no heterogeneous aggregate"
)
XSTOCK_REGISTRY_SOURCE_REVISION = "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0"
XSTOCK_REGISTRY_SOURCE_PATH = (
    "packages/asset-registry/src/data/xstock-variant-groups.ts"
)
XSTOCK_REGISTRY_SOURCE_URL = (
    "https://raw.githubusercontent.com/solana-foundation/tokens/"
    f"{XSTOCK_REGISTRY_SOURCE_REVISION}/{XSTOCK_REGISTRY_SOURCE_PATH}"
)
XSTOCK_REGISTRY_SOURCE_KEY = "solana-foundation/tokens:xstock-variant-groups"
XSTOCK_REGISTRY_SOURCE_LICENSE = "MIT"
XSTOCK_REGISTRY_EXPECTED_COUNT = 107
XSTOCK_TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
XSTOCK_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
XSTOCK_FRESH_SECONDS = 6 * 60 * 60
XSTOCK_SWEEP_SECONDS = 72 * 60 * 60
XSTOCK_DISPLAY_LIMIT = 12
XSTOCK_COVERAGE_BASIS = (
    "eligible assets with a valid supply observation no older than 72 hours"
)
SOLANA_RPC_DOCS = "https://solana.com/docs/rpc/http/"
GET_RECENT_PERFORMANCE_SAMPLES_URL = SOLANA_RPC_DOCS + "getrecentperformancesamples"
GET_HEALTH_URL = SOLANA_RPC_DOCS + "gethealth"
GET_VOTE_ACCOUNTS_URL = SOLANA_RPC_DOCS + "getvoteaccounts"
GET_SUPPLY_URL = SOLANA_RPC_DOCS + "getsupply"
GET_EPOCH_INFO_URL = SOLANA_RPC_DOCS + "getepochinfo"
GET_SLOT_URL = SOLANA_RPC_DOCS + "getslot"
GET_BLOCK_TIME_URL = SOLANA_RPC_DOCS + "getblocktime"
GET_INFLATION_RATE_URL = SOLANA_RPC_DOCS + "getinflationrate"
GET_INFLATION_GOVERNOR_URL = SOLANA_RPC_DOCS + "getinflationgovernor"
GET_BLOCK_URL = SOLANA_RPC_DOCS + "getblock"
GET_BLOCK_PRODUCTION_URL = SOLANA_RPC_DOCS + "getblockproduction"
GET_TOKEN_SUPPLY_URL = SOLANA_RPC_DOCS + "gettokensupply"


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def publication_history(
    snapshots: list[dict[str, Any]], selected: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return publication-safe history without rewriting recorded snapshots."""
    if not snapshots:
        return snapshots
    current = selected if selected is not None else snapshots[-1]
    economics = current.get("economics")
    if not isinstance(economics, dict):
        return snapshots
    hold_all = economics.get("available") is False
    held_sections = {
        name for name in ECONOMICS_METRIC_SECTIONS
        if isinstance(economics.get(name), dict)
        and economics[name].get("available") is False
    }
    if not hold_all and not held_sections:
        return snapshots

    published = []
    for snapshot in snapshots:
        safe_snapshot = dict(snapshot)
        if hold_all:
            safe_snapshot["economics"] = {
                "available": False,
                "publication_state": "withheld",
                "reason": ECONOMICS_PUBLICATION_HOLD,
            }
        else:
            safe_economics = dict(snapshot.get("economics", {}))
            for name in held_sections:
                safe_economics[name] = {
                    "available": False,
                    "publication_state": "withheld",
                    "reason": ECONOMICS_PUBLICATION_HOLD,
                }
            safe_snapshot["economics"] = safe_economics
        published.append(safe_snapshot)
    return published


# One compatibility table for every snapshot-derived metric used by detect,
# delta, or charts. Schema 9 adds editorial news fields only, so schema-8 metric
# paths remain comparable. A path is listed only where its meaning is unchanged.
METRICS: dict[str, dict[str, Any]] = {
    "latest_tps": {
        "label": "Latest TPS", "path": ("performance", "latest_tps"),
        "schemas": range(1, 10), "unit": "TPS", "basis": "measured",
        "source": "solana-rpc", "event_slot_path": ("performance", "samples", 0, "slot"),
        "source_url": GET_RECENT_PERFORMANCE_SAMPLES_URL,
        "population": "transactions in the latest valid performance sample",
        "denominator": "that sample's samplePeriodSecs",
        "window": "latest retained getRecentPerformanceSamples row",
        "collection_method": "Solana RPC getRecentPerformanceSamples",
        "calculation_method": "numTransactions / samplePeriodSecs",
        "caveat": "Includes vote transactions; source-native observation time is unavailable.",
    },
    "mean_slot_time_secs": {
        "label": "Mean slot time", "path": ("performance", "mean_slot_time_secs"),
        "schemas": range(1, 10), "unit": "s", "basis": "measured", "source": "solana-rpc",
        "source_url": GET_RECENT_PERFORMANCE_SAMPLES_URL,
        "population": "slots across all valid retained performance samples",
        "denominator": "sum of numSlots across valid samples",
        "window": "sum of samplePeriodSecs across valid retained samples",
        "collection_method": "Solana RPC getRecentPerformanceSamples",
        "calculation_method": "sum(samplePeriodSecs) / sum(numSlots)",
        "caveat": "Observed from one RPC endpoint's retained sample window.",
    },
    "delinquent_pct": {
        "label": "Validator delinquency", "path": ("validators", "delinquent_pct"),
        "schemas": range(1, 10), "unit": "%", "basis": "measured", "source": "solana-rpc",
        "source_url": GET_VOTE_ACCOUNTS_URL,
        "population": "current and delinquent vote accounts returned by getVoteAccounts",
        "denominator": "current vote accounts + delinquent vote accounts",
        "window": "point-in-time RPC response",
        "collection_method": "Solana RPC getVoteAccounts",
        "calculation_method": "100 * delinquent count / total vote-account count",
        "caveat": "Count-based delinquency; not stake-weighted delinquency.",
    },
    "active_count": {
        "label": "Active validators", "path": ("validators", "active_count"),
        "schemas": range(1, 10), "unit": "validators", "basis": "measured",
        "source": "solana-rpc",
        "source_url": GET_VOTE_ACCOUNTS_URL,
        "population": "current vote accounts returned by getVoteAccounts",
        "denominator": "not applicable",
        "window": "point-in-time RPC response",
        "collection_method": "Solana RPC getVoteAccounts",
        "calculation_method": "count of current vote-account rows",
        "caveat": "Vote-account count; not a unique operator count.",
    },
    "nakamoto_coefficient": {
        "label": "Nakamoto coefficient", "path": ("validators", "nakamoto_coefficient"),
        "schemas": range(1, 10), "unit": "validators", "basis": "measured",
        "source": "solana-rpc",
        "source_url": GET_VOTE_ACCOUNTS_URL,
        "population": "current vote accounts with numeric activated stake",
        "denominator": "total active stake in that population",
        "window": "point-in-time RPC response",
        "collection_method": "Solana RPC getVoteAccounts",
        "calculation_method": "minimum ranked validators exceeding one-third of active stake",
        "caveat": "Calculated from the responding endpoint's current vote-account set.",
    },
    "active_stake_sol": {
        "label": "Active stake", "path": ("validators", "active_stake_sol"),
        "schemas": range(1, 10), "unit": "SOL", "basis": "measured", "source": "solana-rpc",
        "source_url": GET_VOTE_ACCOUNTS_URL,
        "population": "current vote accounts with numeric activated stake",
        "denominator": "not applicable",
        "window": "point-in-time RPC response",
        "collection_method": "Solana RPC getVoteAccounts",
        "calculation_method": "sum activatedStake lamports / 1,000,000,000",
        "caveat": "Excludes vote-account rows whose activated stake is unavailable.",
    },
    "circulating_sol": {
        "label": "Circulating supply", "path": ("supply", "circulating_sol"),
        "schemas": range(1, 10), "unit": "SOL", "basis": "measured", "source": "solana-rpc",
        "source_url": GET_SUPPLY_URL,
        "population": "circulating lamports reported by getSupply(finalized)",
        "denominator": "not applicable",
        "window": "point-in-time finalized RPC response",
        "collection_method": "Solana RPC getSupply(finalized)",
        "calculation_method": "circulating lamports / 1,000,000,000",
        "caveat": "Protocol supply response; not a market-value observation.",
    },
    "epoch": {
        "label": "Epoch", "path": ("epoch", "epoch"), "schemas": range(1, 10),
        "unit": "epoch", "basis": "measured", "source": "solana-rpc",
        "source_url": GET_EPOCH_INFO_URL,
        "population": "current epoch reported by getEpochInfo",
        "denominator": "not applicable",
        "window": "point-in-time RPC response",
        "collection_method": "Solana RPC getEpochInfo",
        "calculation_method": "recorded epoch field; no calculation",
        "caveat": "Source-native observation time is unavailable.",
    },
    "price_usd": {
        "label": "SOL price", "path": ("economics", "price", "price_usd"),
        "schemas": range(1, 10), "unit": "USD", "basis": "measured", "source": "coingecko",
        "source_url": "https://docs.coingecko.com/reference/simple-price",
        "event_time_path": ("economics", "price", "last_updated_at_unix"),
        "population": "one SOL market-price observation",
        "denominator": "one SOL",
        "window": "provider point-in-time observation",
        "collection_method": "CoinGecko simple-price response",
        "calculation_method": "recorded provider USD price; no local aggregation",
        "caveat": "Provider observation; publication remains subject to source rights and freshness.",
    },
    "tvl_usd": {
        "label": "Total value locked", "path": ("economics", "tvl", "tvl_usd"),
        "schemas": range(1, 10), "unit": "USD", "basis": "measured", "source": "defillama",
        "source_url": "https://api.llama.fi/v2/historicalChainTvl/Solana",
        "population": "DeFiLlama protocols attributed to Solana",
        "denominator": "not applicable",
        "window": "provider point-in-time aggregate",
        "collection_method": "DeFiLlama chain TVL response",
        "calculation_method": "recorded provider aggregate; no local reconstruction",
        "caveat": "Provider methodology and coverage define the reported universe.",
    },
    "usd_pegged_circulating_usd": {
        "label": "USD-pegged circulating supply",
        "path": ("economics", "stablecoins", "usd_pegged_circulating_usd"),
        "schemas": (8, 9), "unit": "USD", "basis": "measured", "source": "defillama",
        "source_url": "https://stablecoins.llama.fi/stablecoinchains",
        "population": "provider-reported USD-pegged assets on Solana",
        "denominator": "not applicable",
        "window": "provider point-in-time aggregate",
        "collection_method": "DeFiLlama stablecoin chain response",
        "calculation_method": "recorded totalCirculatingUSD.peggedUSD field",
        "caveat": "Not all stablecoins, liquidity, reserves, or executable depth.",
    },
    "dex_volume_24h_usd": {
        "label": "DEX volume 24h", "path": ("economics", "dex", "volume_24h_usd"),
        "schemas": range(1, 10), "unit": "USD", "basis": "measured", "source": "defillama",
        "source_url": "https://api.llama.fi/overview/dexs/solana",
        "population": "DeFiLlama-indexed Solana DEX activity",
        "denominator": "not applicable",
        "window": "24 hours",
        "collection_method": "DeFiLlama DEX aggregate response",
        "calculation_method": "recorded provider 24-hour aggregate",
        "caveat": "Provider-indexed market coverage; transport success is not complete coverage.",
    },
    "median_fee_lamports": {
        "label": "Median fee (non-vote)", "path": ("activity", "fees", "median_lamports"),
        "schemas": range(2, 10), "unit": "lamports", "basis": "sampled",
        "source": "solana-rpc-block-sample",
        "source_url": GET_BLOCK_URL,
        "population": "accepted sampled non-vote transactions",
        "denominator": "number of accepted sampled non-vote transactions",
        "window": "evenly sampled block window retained in activity.window",
        "collection_method": "Solana RPC getBlock(transactionDetails=accounts)",
        "calculation_method": "median of accepted non-vote transaction fees",
        "caveat": "Sampled blocks and vote classification do not represent every transaction.",
    },
    # Old `estimated_24h_sol` values are deliberately absent: the sampled
    # window was not 24 hours. Only corrected schema-8/9 facts are comparable.
    "sample_mean_rev_sol": {
        "label": "REV over observed window (sample mean)",
        "path": ("activity", "rev", "sample_mean_estimate_sol"),
        "schemas": (8, 9), "unit": "SOL", "basis": "sampled",
        "source": "solana-rpc-block-sample",
        "source_url": GET_BLOCK_URL,
        "event_time_path": ("activity", "window", "last_block_time"),
        "population": "successfully sampled blocks in activity.window",
        "denominator": "number of successfully sampled blocks",
        "window": "observed sampled slot window and block timestamps",
        "collection_method": "Solana RPC getBlock(transactionDetails=accounts)",
        "calculation_method": "mean sampled-block REV * estimated blocks in observed window",
        "caveat": "Sample dispersion does not correct temporal or endpoint sampling bias.",
    },
}


def _report_metric_group(
    *,
    source: str,
    source_url: str,
    window: str,
    collection_method: str,
    caveat: str,
    rows: tuple[tuple[str, str, tuple[str, ...], str, str, str, str], ...],
    basis: str = "measured",
    record_type: str = "numeric",
) -> dict[str, dict[str, Any]]:
    """Build explicit schema-8/9 publication specs without changing chart history."""
    return {
        metric_id: {
            "label": label,
            "path": path,
            "schemas": (8, 9),
            "unit": unit,
            "basis": basis,
            "source": source,
            "source_url": source_url,
            "population": population,
            "denominator": denominator,
            "window": window,
            "collection_method": collection_method,
            "calculation_method": calculation_method,
            "caveat": caveat,
            "record_type": record_type,
        }
        for metric_id, label, path, unit, population, denominator, calculation_method in rows
    }


# Scalar measurements rendered outside the historical chart/delta series. They
# share the same fact shape and compatibility rules as METRICS, but are emitted
# only into the public observation contract so the append-only history does not
# grow merely because presentation coverage became explicit.
REPORT_METRICS: dict[str, dict[str, Any]] = {}
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getHealth",
    source_url=GET_HEALTH_URL,
    window="point-in-time RPC response",
    collection_method="Solana RPC getHealth",
    caveat="Health describes one responding RPC endpoint, not every validator or RPC provider.",
    record_type="boolean",
    rows=(("network_healthy", "RPC endpoint healthy", ("network", "healthy"), "boolean",
           "one responding Solana RPC endpoint", "not applicable",
           "true when getHealth returned ok"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getHealth",
    source_url=GET_HEALTH_URL,
    window="point-in-time RPC response",
    collection_method="Solana RPC getHealth",
    caveat=(
        "Raw health is the collector's categorical endpoint result: ok, unhealthy, "
        "or unavailable; it is not a network-wide diagnosis."
    ),
    basis="recorded",
    record_type="categorical",
    rows=(("network_health_raw", "Raw RPC endpoint health result",
           ("network", "health_raw"), "status", "one responding Solana RPC endpoint",
           "not applicable", "recorded categorical getHealth collector result"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getSlot",
    source_url=GET_SLOT_URL,
    window="point-in-time RPC response",
    collection_method="Solana RPC getSlot",
    caveat="The responding endpoint's slot is not a network-wide simultaneous observation.",
    rows=(
        ("network_slot", "Current slot", ("network", "slot"), "slot",
         "one responding Solana RPC endpoint", "not applicable",
         "recorded getSlot result; no calculation"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getBlockTime",
    source_url=GET_BLOCK_TIME_URL,
    window="point-in-time lookup for the collected slot",
    collection_method="Solana RPC getBlockTime",
    caveat="Unix time is the block time assigned to the queried slot.",
    rows=(
        ("network_block_time_unix", "Current-slot block time", ("network", "block_time_unix"),
         "unix seconds", "the slot returned by getSlot", "one queried slot",
         "recorded getBlockTime result; no calculation"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getEpochInfo",
    source_url=GET_EPOCH_INFO_URL,
    window="point-in-time RPC response",
    collection_method="Solana RPC getEpochInfo",
    caveat="Source-native observation time is unavailable; values describe one endpoint response.",
    rows=(
        ("epoch_block_height", "Block height", ("epoch", "block_height"), "blocks",
         "the current Solana ledger", "not applicable", "recorded blockHeight field"),
        ("epoch_transaction_count", "Total transactions", ("epoch", "transaction_count"),
         "transactions", "transactions counted by getEpochInfo", "not applicable",
         "recorded transactionCount field"),
        ("epoch_progress_pct", "Epoch progress", ("epoch", "progress_pct"), "%",
         "slots elapsed in the current epoch", "slotsInEpoch",
         "100 * slotIndex / slotsInEpoch"),
        ("epoch_slot_index", "Slot in epoch", ("epoch", "slot_index"), "slots",
         "slots elapsed in the current epoch", "not applicable", "recorded slotIndex field"),
        ("epoch_slots_in_epoch", "Slots in epoch", ("epoch", "slots_in_epoch"), "slots",
         "the current epoch", "not applicable", "recorded slotsInEpoch field"),
        ("epoch_remaining_slots", "Remaining epoch slots", ("epoch", "remaining_slots"), "slots",
         "the current epoch", "slotsInEpoch", "max(0, slotsInEpoch - slotIndex)"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-epoch-performance-derived",
    source_url=GET_RECENT_PERFORMANCE_SAMPLES_URL,
    window="current epoch plus the retained recent performance-sample window",
    collection_method="Solana RPC getEpochInfo and getRecentPerformanceSamples",
    caveat="ETA extrapolates a recent median slot time and is not a protocol schedule guarantee.",
    basis="estimated",
    rows=(
        ("epoch_recent_median_slot_time_secs", "Recent median slot time",
         ("epoch", "recent_median_slot_time_secs"), "s",
         "valid retained performance samples", "number of valid sample rows",
         "median(samplePeriodSecs / numSlots)"),
        ("epoch_eta_samples_used", "Epoch ETA samples", ("epoch", "eta_samples_used"),
         "samples", "valid retained performance samples", "not applicable",
         "count of samples contributing a slot time"),
        ("epoch_estimated_remaining_seconds", "Estimated epoch remaining",
         ("epoch", "estimated_remaining_seconds"), "s", "remaining slots in the current epoch",
         "recent median slot time", "remaining_slots * recent_median_slot_time_secs"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getRecentPerformanceSamples",
    source_url=GET_RECENT_PERFORMANCE_SAMPLES_URL,
    window="all valid retained getRecentPerformanceSamples rows",
    collection_method="Solana RPC getRecentPerformanceSamples",
    caveat="The retained endpoint sample window is not a full-day or network-wide independent sample.",
    rows=(
        ("performance_samples_used", "Performance samples used", ("performance", "samples_used"),
         "samples", "valid returned performance samples", "not applicable", "count of retained rows"),
        ("performance_sample_period_seconds", "Performance sample period",
         ("performance", "sample_period_seconds"), "s", "valid retained samples",
         "number of retained samples", "sum samplePeriodSecs across retained rows"),
        ("mean_tps", "Mean TPS", ("performance", "mean_tps"), "TPS",
         "transactions across retained samples", "sum samplePeriodSecs",
         "sum numTransactions / sum samplePeriodSecs"),
        ("peak_tps", "Peak TPS", ("performance", "peak_tps"), "TPS",
         "retained performance samples", "not applicable", "maximum per-row TPS"),
        ("latest_non_vote_tps", "Latest non-vote TPS",
         ("performance", "latest_non_vote_tps"), "TPS",
         "non-vote transactions in the latest retained sample", "that sample's samplePeriodSecs",
         "numNonVoteTransactions / samplePeriodSecs"),
        ("mean_non_vote_tps", "Mean non-vote TPS", ("performance", "mean_non_vote_tps"),
         "TPS", "non-vote transactions across retained samples", "sum samplePeriodSecs",
         "sum non-vote transactions / sum samplePeriodSecs"),
        ("peak_non_vote_tps", "Peak non-vote TPS", ("performance", "peak_non_vote_tps"),
         "TPS", "retained performance samples", "not applicable", "maximum per-row non-vote TPS"),
        ("mean_vote_share_pct", "Mean vote share", ("performance", "mean_vote_share_pct"),
         "%", "vote and total transactions across retained samples", "total transactions",
         "100 * summed vote transactions / summed transactions"),
    ),
))
REPORT_METRICS["latest_non_vote_tps"]["event_slot_path"] = (
    "performance", "samples", 0, "slot",
)
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getBlock-sample",
    source_url=GET_BLOCK_URL,
    window="evenly sampled slot and block-time bounds recorded in activity.window",
    collection_method="Solana RPC getBlock(transactionDetails=accounts)",
    caveat="Systematic block sampling and one endpoint can retain temporal and endpoint bias.",
    basis="sampled",
    rows=(
        ("activity_window_slots", "Sampled slot-window size", ("activity", "window", "slots"),
         "slots", "configured sampled slot window", "not applicable", "last_slot - first_slot"),
        ("activity_blocks_sampled", "Blocks sampled", ("activity", "window", "blocks_sampled"),
         "blocks", "successfully decoded sampled blocks", "blocks requested", "count of accepted blocks"),
        ("activity_blocks_requested", "Blocks requested", ("activity", "window", "blocks_requested"),
         "blocks", "evenly spaced requested slots", "not applicable", "count of requested sample slots"),
        ("activity_production_rate", "Observed production rate", ("activity", "window", "production_rate"),
         "ratio", "sampled slot window", "slots in sampled window",
         "estimated produced blocks / sampled slot-window slots"),
        ("activity_first_slot", "First sampled slot", ("activity", "window", "first_slot"),
         "slot", "sampled slot window", "not applicable", "lowest retained sampled slot"),
        ("activity_last_slot", "Last sampled slot", ("activity", "window", "last_slot"),
         "slot", "sampled slot window", "not applicable", "highest retained sampled slot"),
        ("activity_first_block_time_unix", "First sampled block time",
         ("activity", "window", "first_block_time"), "unix seconds",
         "successfully sampled blocks", "not applicable", "minimum retained blockTime"),
        ("activity_last_block_time_unix", "Last sampled block time",
         ("activity", "window", "last_block_time"), "unix seconds",
         "successfully sampled blocks", "not applicable", "maximum retained blockTime"),
        ("activity_observed_seconds", "Observed sampled duration",
         ("activity", "window", "observed_seconds"), "s",
         "first and last successfully sampled blocks", "not applicable",
         "last_block_time - first_block_time"),
        ("activity_transactions_sampled", "Transactions sampled",
         ("activity", "fees", "transactions_sampled"), "transactions",
         "transactions in accepted sampled blocks", "not applicable", "count of sampled transactions"),
        ("activity_nonvote_transactions_sampled", "Non-vote transactions sampled",
         ("activity", "fees", "nonvote_transactions_sampled"), "transactions",
         "accepted sampled transactions classified as non-vote", "sampled transactions",
         "count after vote-transaction exclusion"),
        ("activity_vote_share_pct", "Sampled vote-transaction share",
         ("activity", "fees", "vote_share_pct"), "%",
         "sampled vote and non-vote transactions", "sampled transactions",
         "100 * vote transaction count / sampled transaction count"),
        ("activity_failure_rate_pct", "Sampled non-vote failure rate",
         ("activity", "fees", "failure_rate_pct"), "%",
         "sampled non-vote transactions", "sampled non-vote transactions",
         "100 * failed non-vote transaction count / non-vote count"),
        ("mean_fee_lamports", "Mean non-vote fee", ("activity", "fees", "mean_lamports"),
         "lamports", "accepted sampled non-vote transactions", "sampled non-vote transactions",
         "arithmetic mean of transaction fees"),
        ("p90_fee_lamports", "90th-percentile non-vote fee",
         ("activity", "fees", "p90_lamports"), "lamports",
         "accepted sampled non-vote transactions", "sampled non-vote transactions",
         "nearest-rank 90th percentile of sampled fees"),
        ("p99_fee_lamports", "99th-percentile non-vote fee",
         ("activity", "fees", "p99_lamports"), "lamports",
         "accepted sampled non-vote transactions", "sampled non-vote transactions",
         "nearest-rank 99th percentile of sampled fees"),
        ("min_fee_lamports", "Minimum non-vote fee", ("activity", "fees", "min_lamports"),
         "lamports", "accepted sampled non-vote transactions", "not applicable", "minimum sampled fee"),
        ("max_fee_lamports", "Maximum non-vote fee", ("activity", "fees", "max_lamports"),
         "lamports", "accepted sampled non-vote transactions", "not applicable", "maximum sampled fee"),
        ("sampled_transaction_fees_sol", "Sampled transaction fees",
         ("activity", "rev", "sampled_sol", "transaction_fees"), "SOL",
         "accepted sampled blocks", "not applicable", "sum of sampled transaction fees / lamports per SOL"),
        ("sampled_signature_base_fee_lower_bound_sol", "Sampled signature base-fee lower bound",
         ("activity", "rev", "sampled_sol", "message_signature_base_fee_lower_bound"), "SOL",
         "message signatures in accepted sampled transactions", "not applicable",
         "signature count * base fee / lamports per SOL"),
        ("sampled_unclassified_fee_residual_sol", "Sampled unclassified fee residual",
         ("activity", "rev", "sampled_sol", "unclassified_fee_residual"), "SOL",
         "accepted sampled transaction fees", "not applicable",
         "transaction fees - message-signature base-fee lower bound"),
        ("sampled_jito_tips_sol", "Sampled Jito tips",
         ("activity", "rev", "sampled_sol", "jito_tips"), "SOL",
         "balance deltas for the pinned eight-account tip universe in sampled blocks", "8 tip accounts",
         "sum of positive sampled tip-account balance deltas / lamports per SOL"),
        ("sampled_rev_total_sol", "Total sampled REV",
         ("activity", "rev", "sampled_sol", "total"), "SOL",
         "accepted sampled blocks", "not applicable", "transaction fees + detected Jito tips"),
        ("mean_rev_per_block_sol", "Mean sampled-block REV",
         ("activity", "rev", "per_block_sol", "mean"), "SOL/block",
         "accepted sampled blocks", "sampled blocks", "mean per-block REV"),
        ("min_rev_per_block_sol", "Minimum sampled-block REV",
         ("activity", "rev", "per_block_sol", "min"), "SOL/block",
         "accepted sampled blocks", "not applicable", "minimum per-block REV"),
        ("max_rev_per_block_sol", "Maximum sampled-block REV",
         ("activity", "rev", "per_block_sol", "max"), "SOL/block",
         "accepted sampled blocks", "not applicable", "maximum per-block REV"),
        ("sample_mean_rev_low_sol", "Sample-mean REV interval low",
         ("activity", "rev", "sample_mean_interval", "low_sol"), "SOL",
         "sample-mean REV estimate", "sampled-block standard error",
         "lower bound of the recorded normal interval"),
        ("sample_mean_rev_high_sol", "Sample-mean REV interval high",
         ("activity", "rev", "sample_mean_interval", "high_sol"), "SOL",
         "sample-mean REV estimate", "sampled-block standard error",
         "upper bound of the recorded normal interval"),
        ("estimated_blocks_in_activity_window", "Estimated blocks in activity window",
         ("activity", "rev", "estimated_blocks_in_window"), "blocks",
         "sampled slot window", "sampled slot-window slots",
         "slot-window size * sampled production rate"),
        ("rev_estimate_window_seconds", "REV estimate window duration",
         ("activity", "rev", "estimate_window_seconds"), "s",
         "first and last sampled block timestamps", "not applicable",
         "last sampled block time - first sampled block time"),
        ("unique_fee_payers_sampled", "Unique sampled fee payers",
         ("activity", "addresses", "unique_fee_payers_sampled"), "addresses",
         "fee-payer public keys in sampled non-vote transactions", "sampled non-vote transactions",
         "cardinality of sampled fee-payer public keys"),
        ("unique_accounts_sampled", "Unique sampled accounts",
         ("activity", "addresses", "unique_accounts_sampled"), "addresses",
         "account public keys touched by sampled non-vote transactions", "sampled non-vote transactions",
         "cardinality of sampled account public keys"),
        ("mean_fee_payers_per_block", "Mean fee payers per sampled block",
         ("activity", "addresses", "mean_fee_payers_per_block"), "addresses/block",
         "per-block unique non-vote fee-payer counts", "sampled blocks",
         "mean per-block unique fee-payer count"),
        ("address_blocks_sampled", "Blocks in address sample",
         ("activity", "addresses", "blocks_sampled"), "blocks",
         "accepted blocks contributing address identities", "not applicable", "count of accepted blocks"),
        ("fee_split_blocks_reconciled", "Fee-split blocks reconciled",
         ("activity", "fee_split", "blocks_reconciled"), "blocks",
         "sampled blocks with reconciled fee rewards", "sampled blocks", "count of reconciled blocks"),
        ("fee_split_fees_sol", "Reconciled fees", ("activity", "fee_split", "fees_sol"), "SOL",
         "reconciled sampled blocks", "not applicable", "sum of sampled transaction fees"),
        ("fee_split_validator_reward_sol", "Validator fee rewards",
         ("activity", "fee_split", "validator_reward_sol"), "SOL",
         "fee reward entries in reconciled sampled blocks", "not applicable", "sum of fee reward entries"),
        ("fee_split_burned_sol", "Fees burned", ("activity", "fee_split", "burned_sol"), "SOL",
         "reconciled sampled fees", "not applicable", "fees - validator fee rewards"),
        ("fee_split_burned_pct", "Fee burn share", ("activity", "fee_split", "burned_pct"), "%",
         "reconciled sampled fees", "reconciled fee amount",
         "100 * burned fees / reconciled fees"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getSupply",
    source_url=GET_SUPPLY_URL,
    window="point-in-time finalized RPC response",
    collection_method="Solana RPC getSupply(finalized)",
    caveat="Protocol supply values are not market-value observations.",
    rows=(
        ("total_supply_sol", "Total SOL supply", ("supply", "total_sol"), "SOL",
         "total lamports reported by getSupply", "not applicable", "total lamports / 1,000,000,000"),
        ("non_circulating_supply_sol", "Non-circulating SOL supply",
         ("supply", "non_circulating_sol"), "SOL",
         "non-circulating lamports reported by getSupply", "not applicable",
         "non-circulating lamports / 1,000,000,000"),
        ("circulating_supply_pct", "Circulating supply share",
         ("supply", "circulating_pct"), "%", "circulating SOL supply", "total SOL supply",
         "100 * circulating lamports / total lamports"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-inflation",
    source_url=GET_INFLATION_RATE_URL,
    window="point-in-time RPC responses",
    collection_method="Solana RPC getInflationRate and getInflationGovernor(finalized)",
    caveat=f"Current rates come from getInflationRate; policy fields also reference {GET_INFLATION_GOVERNOR_URL}.",
    rows=(
        ("inflation_current_total_pct", "Current total inflation rate",
         ("inflation", "current_total_pct"), "%", "current protocol inflation rate",
         "not applicable", "getInflationRate total * 100"),
        ("inflation_current_validator_pct", "Current validator inflation rate",
         ("inflation", "current_validator_pct"), "%", "current validator inflation rate",
         "not applicable", "getInflationRate validator * 100"),
        ("inflation_current_foundation_pct", "Current foundation inflation rate",
         ("inflation", "current_foundation_pct"), "%", "current foundation inflation rate",
         "not applicable", "getInflationRate foundation * 100"),
        ("inflation_rate_epoch", "Inflation rate epoch", ("inflation", "epoch"), "epoch",
         "getInflationRate response", "not applicable", "recorded epoch field"),
        ("inflation_initial_pct", "Initial inflation policy rate", ("inflation", "initial_pct"), "%",
         "inflation governor policy", "not applicable", "getInflationGovernor initial * 100"),
        ("inflation_terminal_pct", "Terminal inflation policy rate", ("inflation", "terminal_pct"), "%",
         "inflation governor policy", "not applicable", "getInflationGovernor terminal * 100"),
        ("inflation_taper_pct", "Annual inflation taper", ("inflation", "taper_pct"), "%",
         "inflation governor policy", "not applicable", "getInflationGovernor taper * 100"),
        ("inflation_foundation_pct", "Foundation policy rate", ("inflation", "foundation_pct"), "%",
         "inflation governor policy", "not applicable", "getInflationGovernor foundation * 100"),
        ("inflation_foundation_term_years", "Foundation term",
         ("inflation", "foundation_term_years"), "years", "inflation governor policy",
         "not applicable", "recorded foundationTerm field"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getVoteAccounts",
    source_url=GET_VOTE_ACCOUNTS_URL,
    window="point-in-time RPC response",
    collection_method="Solana RPC getVoteAccounts",
    caveat="Vote-account rows are not unique validator operators; stake aggregates use returned activated stake.",
    rows=(
        ("delinquent_validator_count", "Delinquent validators",
         ("validators", "delinquent_count"), "validators",
         "delinquent vote accounts returned by getVoteAccounts", "not applicable", "count of delinquent rows"),
        ("delinquent_stake_sol", "Delinquent activated stake",
         ("validators", "delinquent_stake_sol"), "SOL",
         "delinquent vote accounts with numeric activated stake", "not applicable",
         "sum delinquent activatedStake / 1,000,000,000"),
        ("validator_accounts_with_stake", "Vote accounts with stake",
         ("validators", "accounts_with_stake"), "vote accounts",
         "current and delinquent vote accounts", "all returned vote accounts",
         "count of rows with numeric activated stake"),
        ("validator_accounts_missing_stake", "Vote accounts missing stake",
         ("validators", "accounts_missing_stake"), "vote accounts",
         "current and delinquent vote accounts", "all returned vote accounts",
         "count of rows without numeric activated stake"),
        ("validator_top_10_share_pct", "Top-ten active stake share",
         ("validators", "top_10_share_pct"), "%", "ten highest-stake current vote accounts",
         "total active stake", "100 * top-ten activated stake / total active stake"),
        ("ranked_validator_count", "Ranked validators published",
         ("validators", "ranked_validator_count"), "validators", "ranked vote-account rows",
         "not applicable", "count of published ranked rows"),
        ("all_validator_count", "All vote accounts", ("validators", "all_validator_count"),
         "vote accounts", "current and delinquent vote accounts", "not applicable",
         "count of all returned rows"),
        ("validator_median_commission_pct", "Median validator commission",
         ("validators", "commission", "median_pct"), "%",
         "vote accounts reporting numeric commission", "reporting vote accounts", "median commission"),
        ("validator_mean_commission_pct", "Mean validator commission",
         ("validators", "commission", "mean_pct"), "%",
         "vote accounts reporting numeric commission", "reporting vote accounts", "arithmetic mean commission"),
        ("zero_commission_validator_count", "Zero-commission validators",
         ("validators", "commission", "zero_commission_count"), "validators",
         "vote accounts reporting zero commission", "reporting vote accounts", "count of zero values"),
        ("max_commission_validator_count", "Maximum-commission validators",
         ("validators", "commission", "max_commission_count"), "validators",
         "vote accounts reporting maximum commission", "reporting vote accounts", "count of maximum values"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getBlockProduction",
    source_url=GET_BLOCK_PRODUCTION_URL,
    window="most recent fully completed epoch",
    collection_method="Solana RPC getBlockProduction(finalized)",
    caveat="Completed-epoch production is identity-level; vote-account joins may be unmatched.",
    rows=(
        ("block_production_epoch", "Completed production epoch",
         ("validators", "block_production", "epoch"), "epoch",
         "one fully completed epoch", "not applicable", "recorded completed epoch"),
        ("block_production_first_slot", "Production first slot",
         ("validators", "block_production", "first_slot"), "slot",
         "completed-epoch slot range", "not applicable", "recorded first slot"),
        ("block_production_last_slot", "Production last slot",
         ("validators", "block_production", "last_slot"), "slot",
         "completed-epoch slot range", "not applicable", "recorded last slot"),
        ("block_production_leader_slots", "Completed-epoch leader slots",
         ("validators", "block_production", "leader_slots"), "slots",
         "leader slots assigned in the completed epoch", "not applicable",
         "sum identity-level leader slots"),
        ("block_production_blocks_produced", "Completed-epoch blocks produced",
         ("validators", "block_production", "blocks_produced"), "blocks",
         "leader slots with produced blocks", "leader slots", "sum produced blocks"),
        ("block_production_skipped_slots", "Completed-epoch skipped slots",
         ("validators", "block_production", "skipped_slots"), "slots",
         "leader slots without produced blocks", "leader slots", "leader slots - produced blocks"),
        ("block_production_skip_rate", "Completed-epoch skip rate",
         ("validators", "block_production", "skip_rate"), "ratio",
         "skipped leader slots", "leader slots", "skipped slots / leader slots"),
        ("block_production_identity_count", "Production identities",
         ("validators", "block_production", "identity_count"), "identities",
         "identity rows returned for the completed epoch", "not applicable", "count identity rows"),
        ("block_production_matched_identity_count", "Matched production identities",
         ("validators", "block_production", "matched_identity_count"), "identities",
         "production identities joined to at least one vote account", "production identities",
         "count rows with vote_identity_matched true"),
        ("block_production_unmatched_identity_count", "Unmatched production identities",
         ("validators", "block_production", "unmatched_identity_count"), "identities",
         "production identities without a vote-account join", "production identities",
         "count rows with vote_identity_matched false"),
        ("block_production_vote_join_coverage_pct", "Production vote-account join coverage",
         ("validators", "block_production", "vote_join_coverage_pct"), "%",
         "production identities joined to vote accounts", "production identities",
         "100 * matched identities / production identities"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="coingecko-simple-price",
    source_url="https://docs.coingecko.com/reference/simple-price",
    window="provider point-in-time observation",
    collection_method="CoinGecko simple-price response",
    caveat="Publication remains source-rights and freshness gated.",
    rows=(
        ("price_change_24h_pct", "SOL price change (24h)",
         ("economics", "price", "change_24h_pct"), "%", "one provider SOL market series",
         "provider price 24 hours earlier", "provider-reported 24-hour percentage change"),
        ("sol_market_cap_usd", "SOL market capitalization",
         ("economics", "price", "market_cap_usd"), "USD",
         "provider-defined SOL circulating market capitalization", "not applicable",
         "recorded provider market-cap field"),
        ("sol_volume_24h_usd", "SOL trading volume (24h)",
         ("economics", "price", "volume_24h_usd"), "USD",
         "provider-indexed SOL venues", "not applicable", "recorded provider 24-hour volume"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="defillama",
    source_url="https://api.llama.fi/v2/historicalChainTvl/Solana",
    window="provider observation window",
    collection_method="DeFiLlama chain responses",
    caveat="Provider methodology and source rights define the covered universe.",
    rows=(
        ("tvl_change_7d_pct", "Solana TVL change (7d)",
         ("economics", "tvl", "change_7d_pct"), "%",
         "DeFiLlama protocols attributed to Solana", "provider aggregate seven days earlier",
         "100 * (latest TVL / seven-day-prior TVL - 1)"),
        ("dex_volume_change_1d_pct", "Solana DEX volume change (1d)",
         ("economics", "dex", "change_1d_pct"), "%",
         "DeFiLlama-indexed Solana DEX activity", "provider prior-day aggregate",
         "recorded provider one-day percentage change"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-token-registry-and-rpc",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="current collection and retained supply sweep",
    collection_method="Pinned xStock registry plus Solana RPC getTokenSupply(finalized)",
    caveat="Coverage is the pinned 107-mint registry, not complete tokenized-equity market coverage.",
    rows=(
        ("xstock_registry_asset_count", "xStock registry assets",
         ("growth", "tokenized_equities", "registry_asset_count"), "assets",
         "unique xStock-labelled mints in the pinned registry", "107 expected mints",
         "count of validated unique registry mints"),
        ("xstock_supply_coverage_numerator", "xStock supply coverage numerator",
         ("growth", "tokenized_equities", "supply_coverage", "coverage_numerator"), "assets",
         "eligible registry assets with usable observations within 72 hours", "coverage denominator",
         "count of usable retained per-mint observations"),
        ("xstock_supply_coverage_denominator", "xStock supply coverage denominator",
         ("growth", "tokenized_equities", "supply_coverage", "coverage_denominator"), "assets",
         "eligible registry assets", "not applicable", "count of eligible pinned-registry assets"),
        ("xstock_fresh_supply_asset_count", "Fresh xStock supply assets",
         ("growth", "tokenized_equities", "supply_coverage", "fresh_asset_count"), "assets",
         "eligible registry assets with observations no older than six hours", "coverage denominator",
         "count of fresh retained observations"),
        ("xstock_supply_queried_this_run", "xStock supplies queried this run",
         ("growth", "tokenized_equities", "supply_coverage", "queried_this_run_asset_count"), "assets",
         "assets selected for the current bounded collection tranche", "eligible registry assets",
         "count of attempted getTokenSupply calls"),
        ("xstock_supply_successful_this_run", "xStock supplies successful this run",
         ("growth", "tokenized_equities", "supply_coverage", "successful_this_run_asset_count"), "assets",
         "assets queried in the current collection tranche", "queried assets",
         "count of validated getTokenSupply responses"),
        ("xstock_supply_failed_this_run", "xStock supplies failed this run",
         ("growth", "tokenized_equities", "supply_coverage", "failed_this_run_asset_count"), "assets",
         "assets queried in the current collection tranche", "queried assets",
         "queried count - successful count"),
        ("xstock_supply_observed_asset_count", "xStock assets with retained supply",
         ("growth", "tokenized_equities", "supply_coverage", "observed_asset_count"), "assets",
         "eligible registry assets with any retained supply observation", "coverage denominator",
         "count of retained validated observations"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getTokenSupply-finalized",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="point-in-time finalized observations for the pinned four-mint list",
    collection_method="Pinned Solana Foundation four-mint list plus Solana RPC getTokenSupply(finalized)",
    caveat=SELECTED_STABLECOIN_LIMITATIONS,
    rows=(
        ("selected_stablecoin_coverage_numerator", "Selected stablecoin coverage numerator",
         ("growth", "selected_usd_stablecoins", "coverage_numerator"), "mints",
         "observed mints in the pinned four-mint selected list", "4 selected mints",
         "count of validated getTokenSupply observations"),
        ("selected_stablecoin_coverage_denominator", "Selected stablecoin coverage denominator",
         ("growth", "selected_usd_stablecoins", "coverage_denominator"), "mints",
         "pinned selected USD-stablecoin mint list", "not applicable", "fixed count of selected mints"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getTokenSupply-finalized",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="point-in-time finalized observations for the pinned four-mint list",
    collection_method="Sum exact finalized supplies for the four selected mints",
    caveat=SELECTED_STABLECOIN_LIMITATIONS,
    record_type="decimal-string",
    rows=(("selected_stablecoin_total_supply", "Selected stablecoin total supply",
           ("growth", "selected_usd_stablecoins", "selected_total_supply_decimal"),
           SELECTED_STABLECOIN_UNIT, "four observed mints in the pinned selected list",
           "4 selected mints", "sum exact per-mint decimal total supplies"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="no-canonical-network-wide-source",
    source_url="https://solana.com/data",
    window="24 hours",
    collection_method="no cleared complete-universe collection implemented",
    caveat="Provider-labelled activity rows are not network-wide daily active addresses or unique humans.",
    rows=(("network_wide_daily_active_addresses", "Network-wide daily active addresses",
           ("growth", "daily_active_addresses", "network_wide_value"), "addresses",
           "distinct active addresses across the complete Solana network",
           "every eligible address in every Solana block during the 24-hour window",
           "unavailable; no network-wide union is calculated"),),
))
REPORT_METRICS["network_wide_daily_active_addresses"]["unavailable_reason"] = (
    "No canonical complete-network source with cleared publication rights is implemented."
)
REPORT_METRICS.update(_report_metric_group(
    source="no-cleared-complete-universe-source",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="point-in-time circulating-supply composition",
    collection_method="no cleared complete-universe collection implemented",
    caveat="The recorded four-mint selected supply set is not circulating supply or complete stablecoin coverage.",
    record_type="categorical",
    rows=(("network_wide_circulating_stablecoin_composition",
           "Network-wide circulating stablecoin composition",
           ("growth", "selected_usd_stablecoins", "network_wide_circulating_composition"),
           "composition", "all circulating stablecoin assets on Solana",
           "complete circulating stablecoin universe",
           "unavailable; no complete-universe composition is calculated"),),
))
REPORT_METRICS["network_wide_circulating_stablecoin_composition"]["unavailable_reason"] = (
    "No cleared complete-universe circulating-supply source is implemented."
)
REPORT_METRICS.update(_report_metric_group(
    source="xstock-market-data-unavailable",
    source_url="https://docs.dexscreener.com/api/reference",
    window="provider point-in-time valuation and rolling 24-hour indexed-pool volume",
    collection_method="collection disabled pending source-rights acceptance and complete endpoint semantics",
    caveat="Supply observations do not establish USD valuation, liquidity, reserves, or total market volume.",
    rows=(
        ("xstock_usd_valuation", "xStock USD valuation",
         ("growth", "tokenized_equities", "valuation", "value_usd"), "USD",
         "eligible xStock-labelled Solana mints with timestamped cleared prices",
         "complete eligible valued-asset population",
         "sum per-mint finalized supply multiplied by source-native timestamped USD price"),
        ("xstock_indexed_dex_volume_24h_usd", "xStock indexed DEX volume (24h)",
         ("growth", "tokenized_equities", "volume", "volume_24h_usd"), "USD",
         "deduplicated Solana DEX pools indexed for eligible xStock-labelled mints",
         "all deduplicated indexed pairs returned by the adopted endpoint",
         "sum source-reported rolling h24 volume across deduplicated indexed pairs"),
        ("xstock_indexed_dex_pair_count", "xStock indexed DEX pair count",
         ("growth", "tokenized_equities", "volume", "pair_count"), "pairs",
         "deduplicated Solana DEX pools indexed for eligible xStock-labelled mints",
         "all returned rows after identity validation and deduplication",
         "count unique validated chainId + pairAddress identities"),
        ("xstock_indexed_dex_asset_count", "xStock assets with indexed DEX pairs",
         ("growth", "tokenized_equities", "volume", "assets_with_pairs"), "assets",
         "eligible xStock-labelled mints with at least one indexed Solana DEX pair",
         "eligible pinned-registry assets",
         "count unique eligible mints represented by validated indexed pairs"),
        ("xstock_indexed_dex_volume_covered_pair_count",
         "xStock indexed pairs with 24-hour volume",
         ("growth", "tokenized_equities", "volume", "volume_covered_pair_count"), "pairs",
         "deduplicated indexed Solana DEX pairs with a valid rolling h24 value",
         "deduplicated indexed pair count",
         "count validated pairs with a finite non-negative h24 volume"),
        ("xstock_indexed_dex_invalid_row_count", "Invalid indexed DEX rows",
         ("growth", "tokenized_equities", "volume", "invalid_row_count"), "rows",
         "rows returned by the adopted indexed-pool endpoint",
         "all returned rows before validated-pair deduplication",
         "count rows rejected by schema and identity validation"),
        ("xstock_indexed_dex_conflicting_pair_count", "Conflicting indexed DEX pairs",
         ("growth", "tokenized_equities", "volume", "conflicting_pair_count"), "pairs",
         "pair identities with conflicting non-duplicate source rows",
         "validated pair identities",
         "count pair identities with conflicting payloads"),
        ("xstock_indexed_dex_batches_succeeded", "Indexed DEX batches succeeded",
         ("growth", "tokenized_equities", "volume", "batches_succeeded"), "batches",
         "requested source batches completed successfully",
         "requested source batches",
         "count successful batch responses"),
        ("xstock_indexed_dex_batches_requested", "Indexed DEX batches requested",
         ("growth", "tokenized_equities", "volume", "batches_requested"), "batches",
         "source batches requested for eligible mints",
         "not applicable",
         "count endpoint batches requested"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="xstock-market-data-unavailable",
    source_url="https://docs.dexscreener.com/api/reference",
    window="current bounded indexed-pool collection",
    collection_method="validated endpoint-batch collection state",
    caveat="Transport completeness does not establish complete market coverage.",
    record_type="boolean",
    rows=(("xstock_indexed_dex_transport_complete", "Indexed DEX transport complete",
           ("growth", "tokenized_equities", "volume", "transport_complete"), "boolean",
           "requested endpoint batches", "all requested endpoint batches",
           "true only when every requested batch completed successfully"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="xstock-market-data-unavailable",
    source_url="https://docs.dexscreener.com/api/reference",
    window="current indexed-pool collection",
    collection_method="validated endpoint response plus declared market exclusions",
    caveat="Indexed DEX pools exclude RFQ fills, centralized venues, and unindexed venues.",
    record_type="categorical",
    rows=(("xstock_indexed_dex_market_coverage", "Indexed DEX market coverage",
           ("growth", "tokenized_equities", "volume", "market_coverage"), "coverage state",
           "eligible xStock-labelled Solana trading activity", "complete market universe",
           "recorded coverage classification after transport and exclusion checks"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="no-complete-tokenized-equity-market-source",
    source_url="https://docs.dexscreener.com/api/reference",
    window="24 hours",
    collection_method="no complete-market collection implemented",
    caveat="Indexed DEX pools exclude RFQ fills, centralized venues, and unindexed or unsupported pools.",
    rows=(("xstock_total_trading_volume_24h_usd", "Total tokenized-equity volume (24h)",
           ("growth", "tokenized_equities", "total_market", "volume_24h_usd"), "USD",
           "all Solana tokenized-equity trading venues and execution modes",
           "complete Solana tokenized-equity market universe",
           "unavailable; no complete-market aggregation is calculated"),),
))
REPORT_METRICS["xstock_total_trading_volume_24h_usd"]["unavailable_reason"] = (
    "No complete tokenized-equity market source covers DEX, RFQ, centralized, and unindexed venues."
)
REPORT_METRICS.update(_report_metric_group(
    source="solana-data-provider-observations",
    source_url="https://solana.com/data",
    window="latest retained provider observation date",
    collection_method="retain source-labelled provider rows without cross-provider aggregation",
    caveat="Provider methodologies differ; these ranges are not network-wide daily active addresses or unique humans.",
    rows=(
        ("stablecoin_active_address_provider_range_min",
         "Stablecoin active-address provider range minimum",
         ("growth", "daily_active_addresses", "minimum"), "addresses",
         "latest-date provider observations for Solana stablecoin activity",
         "providers represented on the latest retained date", "minimum provider value"),
        ("stablecoin_active_address_provider_range_max",
         "Stablecoin active-address provider range maximum",
         ("growth", "daily_active_addresses", "maximum"), "addresses",
         "latest-date provider observations for Solana stablecoin activity",
         "providers represented on the latest retained date", "maximum provider value"),
        ("stablecoin_active_address_provider_count",
         "Stablecoin active-address provider count",
         ("growth", "daily_active_addresses", "provider_count"), "providers",
         "providers represented in the latest stablecoin activity range",
         "not applicable", "count distinct providers on the latest retained date"),
        ("transaction_initiator_provider_range_min",
         "Transaction-initiator provider range minimum",
         ("growth", "daily_fee_payers", "minimum"), "initiators",
         "latest-date provider observations labelled Fee Payers",
         "providers represented on the latest retained date", "minimum provider value"),
        ("transaction_initiator_provider_range_max",
         "Transaction-initiator provider range maximum",
         ("growth", "daily_fee_payers", "maximum"), "initiators",
         "latest-date provider observations labelled Fee Payers",
         "providers represented on the latest retained date", "maximum provider value"),
        ("transaction_initiator_provider_count", "Transaction-initiator provider count",
         ("growth", "daily_fee_payers", "provider_count"), "providers",
         "providers represented in the latest transaction-initiator range",
         "not applicable", "count distinct providers on the latest retained date"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="xstock-proof-of-reserves-unavailable",
    source_url="https://docs.xstocks.fi/apis/openapi/proof-of-reserves",
    window="issuer proof-of-reserves response",
    collection_method="collection disabled pending retrieval and redistribution rights",
    caveat="Issuer proof-of-reserves covers all reported chain deployments and is not Solana-only supply.",
    rows=(("xstock_proof_of_reserves_coverage", "Issuer proof-of-reserves coverage",
           ("growth", "tokenized_equities", "proof_of_reserves", "asset_count"), "assets",
           "issuer assets represented in the proof-of-reserves response",
           "issuer-reported reserve asset universe", "count unique represented issuer assets"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="statuspage-status-api",
    source_url="https://status.solana.com/api/v2",
    window="current status and unresolved-incident responses",
    collection_method="Solana Status Statuspage API",
    caveat="Statuspage incident state is operational metadata, not an exhaustive independent network-health measurement.",
    rows=(("active_incident_count", "Active incidents",
           ("news", "current_status", "active_incident_count"), "incidents",
           "unresolved incidents returned by the official Solana status page",
           "official Statuspage unresolved-incident response", "count unresolved incident rows"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getVoteAccounts",
    source_url=GET_VOTE_ACCOUNTS_URL,
    window="selected snapshot",
    collection_method="deterministic transform of validated snapshot fields",
    caveat="Derived only when the required recorded population and denominator are complete.",
    rows=(("current_validator_share_pct", "Current validator share",
           ("validators", "current_share_pct"), "%", "current vote accounts",
           "current plus delinquent vote accounts", "100 * current / (current + delinquent)"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getBlockProduction",
    source_url=GET_BLOCK_PRODUCTION_URL,
    window="completed epoch",
    collection_method="deterministic transform of validated getBlockProduction fields",
    caveat="Derived only when the completed-epoch leader-slot denominator is positive.",
    rows=(("block_production_produced_pct", "Completed-epoch production share",
           ("validators", "block_production", "produced_pct"), "%",
           "produced leader slots in the completed epoch", "completed-epoch leader slots",
           "100 * produced blocks / leader slots"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getTokenSupply-finalized",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="current bounded supply-query tranche",
    collection_method="deterministic transform of validated xStock query counts",
    caveat="A run-success percentage describes the bounded current tranche, not full registry freshness.",
    rows=(("xstock_supply_run_success_pct", "Current xStock supply-run success",
           ("growth", "tokenized_equities", "supply_coverage", "run_success_pct"), "%",
           "successful validated supply queries in the current bounded tranche",
           "queried assets in the current bounded tranche", "100 * successful / queried"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="validated-snapshot-contract",
    source_url="https://github.com/ashtonships/solana-ecosystem-report/blob/main/pipeline.py",
    window="selected snapshot",
    collection_method="validate the selected snapshot before rendering",
    caveat="Schema version identifies the machine contract, not data freshness.",
    basis="recorded",
    rows=(("snapshot_schema_version", "Snapshot schema version",
           ("schema_version",), "schema version", "selected snapshot",
           "not applicable", "recorded pipeline-validated schema_version field"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-foundation-simd-frontmatter",
    source_url="https://github.com/solana-foundation/solana-com",
    window="pinned source revision",
    collection_method="recorded pinned SIMD repository metadata",
    caveat=(
        "Repository document counts describe the pinned source revision; they do not "
        "prove implementation, activation, or complete ecosystem coverage."
    ),
    basis="recorded",
    rows=(
        ("simd_proposal_count", "Recorded SIMD proposals",
         ("news", "sources", "simd_proposals", "proposal_count"), "proposals",
         "validated SIMD proposal records in the pinned source revision",
         "documents scanned in the pinned source revision",
         "count of validated proposal records"),
        ("simd_document_count", "Recorded SIMD documents",
         ("news", "sources", "simd_proposals", "document_count"), "documents",
         "documents scanned in the pinned SIMD source revision", "not applicable",
         "recorded document count from the pinned source scan"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="statuspage-status-api",
    source_url="https://status.solana.com/api/v2/status.json",
    window="current Statuspage response",
    collection_method="Solana Status Statuspage API",
    caveat="Statuspage text is publisher-reported operational metadata.",
    basis="recorded",
    record_type="categorical",
    rows=(("network_status_description", "Network status description",
           ("news", "current_status", "description"), "status",
           "official Solana Statuspage", "not applicable",
           "recorded current status description; no calculation"),),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getTokenSupply-finalized",
    source_url=GET_TOKEN_SUPPLY_URL,
    window="retained finalized supply sweep",
    collection_method="validated per-mint getTokenSupply observation times",
    caveat="Bounds describe retained registry observations, not complete market coverage.",
    basis="recorded",
    record_type="categorical",
    rows=(
        ("xstock_supply_oldest_observation_at", "Oldest retained xStock supply observation",
         ("growth", "tokenized_equities", "supply_coverage", "oldest_observation_at"),
         "timestamp", "eligible retained xStock supply observations", "not applicable",
         "minimum recorded per-mint supply observation time"),
        ("xstock_supply_newest_observation_at", "Newest retained xStock supply observation",
         ("growth", "tokenized_equities", "supply_coverage", "newest_observation_at"),
         "timestamp", "eligible retained xStock supply observations", "not applicable",
         "maximum recorded per-mint supply observation time"),
        ("selected_stablecoin_newest_observation_at",
         "Newest selected-stablecoin supply observation",
         ("growth", "selected_usd_stablecoins", "newest_observation_at"),
         "timestamp", "pinned four-mint selected stablecoin observations", "4 selected mints",
         "maximum recorded selected-mint supply observation time"),
    ),
))
REPORT_METRICS.update(_report_metric_group(
    source="solana-data-provider-observations",
    source_url="https://solana.com/data",
    window="latest retained provider observation date",
    collection_method="retain source-labelled provider rows without cross-provider aggregation",
    caveat="Provider dates do not make the ranges network-wide activity metrics.",
    basis="recorded",
    record_type="categorical",
    rows=(
        ("stablecoin_active_address_provider_date", "Stablecoin provider-range date",
         ("growth", "daily_active_addresses", "date"), "date",
         "latest-date provider observations for Solana stablecoin activity", "not applicable",
         "recorded latest comparable provider date"),
        ("transaction_initiator_provider_date", "Transaction-initiator provider-range date",
         ("growth", "daily_fee_payers", "date"), "date",
         "latest-date provider observations labelled Fee Payers", "not applicable",
         "recorded latest comparable provider date"),
    ),
))
for _recorded_context_metric in (
    "network_health_raw",
    "network_status_description",
    "xstock_supply_oldest_observation_at",
    "xstock_supply_newest_observation_at",
    "selected_stablecoin_newest_observation_at",
    "stablecoin_active_address_provider_date",
    "transaction_initiator_provider_date",
):
    REPORT_METRICS[_recorded_context_metric]["retain_recorded_value"] = True

REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getMultipleAccounts-finalized",
    source_url=SOLANA_RPC_DOCS + "getmultipleaccounts",
    window="finalized RPC response context",
    collection_method="read pinned official Agave feature accounts",
    caveat="Only the pinned feature set; account absence is not a roadmap activation claim.",
    rows=(
        ("feature_activation_coverage_numerator", "Inspected feature gates",
         ("feature_activation", "coverage_numerator"), "feature gates",
         "successfully interpreted pinned feature accounts", "pinned feature set", "count valid account responses"),
        ("feature_activation_coverage_denominator", "Pinned feature gates",
         ("feature_activation", "coverage_denominator"), "feature gates",
         "pinned official Agave feature set", "pinned feature set", "count requested feature addresses"),
        ("feature_activated_count", "Activated observed feature gates",
         ("feature_activation", "activated_feature_count"), "feature gates",
         "successfully inspected pinned feature accounts", "pinned feature set", "count decoded activated states"),
        ("feature_rpc_context_slot", "Feature inspection context slot",
         ("feature_activation", "source", "rpc_context_slot"), "slot",
         "finalized RPC response", "not applicable", "recorded RPC context slot"),
    ),
))

REPORT_METRICS.update(_report_metric_group(
    source="solana-rpc-getClusterNodes", source_url=SOLANA_RPC_DOCS + "getclusternodes",
    window="one RPC-observed gossip-node response",
    collection_method="getClusterNodes; unique returned node pubkeys",
    caveat="Unweighted nodes known to one RPC endpoint; not validator, stake or client adoption.",
    rows=tuple(
        ("cluster_software_" + field, label, ("cluster_software", field), "nodes",
         "unique node pubkeys returned by the queried RPC", "observed node population", method)
        for field, label, method in (
            ("observed_node_count", "RPC-observed cluster nodes", "count unique returned pubkeys"),
            ("version_reported_node_count", "Nodes reporting a software version", "count nodes with a non-null version"),
            ("unknown_version_node_count", "Nodes with unknown software version", "count nodes with a null version"),
            ("other_reported_version_node_count", "Nodes in other reported version groups", "sum version groups beyond the displayed limit"),
        )
    ),
))
for _cluster_metric in tuple(key for key in REPORT_METRICS if key.startswith("cluster_software_")):
    REPORT_METRICS[_cluster_metric]["event_time_path"] = ("cluster_software", "observed_at")

PUBLIC_METRICS: dict[str, dict[str, Any]] = {**METRICS, **REPORT_METRICS}

DERIVED_REPORT_METRIC_INPUTS: dict[str, tuple[str, ...]] = {
    "current_validator_share_pct": ("active_count", "delinquent_validator_count"),
    "block_production_produced_pct": (
        "block_production_blocks_produced", "block_production_leader_slots",
    ),
    "xstock_supply_run_success_pct": (
        "xstock_supply_successful_this_run", "xstock_supply_queried_this_run",
    ),
}


class FactConflictError(ValueError):
    """Two facts claim one source-native identity with different semantics."""


def adapt_fact(fact: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize equivalent legacy facts without rewriting the ledger."""
    coverage = fact.get("coverage")
    if (fact.get("metric_id") == SELECTED_STABLECOIN_METRIC_ID
            and isinstance(coverage, dict)
            and coverage.get("fact_contract") == SELECTED_STABLECOIN_LEGACY_FACT_CONTRACT):
        numerator = coverage.get("coverage_numerator")
        denominator = coverage.get("coverage_denominator")
        expected_state = "current" if numerator == denominator else "partial"
        if (not isinstance(numerator, int) or isinstance(numerator, bool)
                or denominator != len(SELECTED_STABLECOIN_IDENTITIES)
                or not 0 < numerator <= denominator
                or fact.get("state") != expected_state
                or coverage.get("selected_mint_count") != denominator
                or coverage.get("universe_coverage") != "unknown"):
            raise FactConflictError("legacy selected stablecoin fact is invalid")
        adapted = dict(fact)
        adapted_coverage = dict(coverage)
        adapted_coverage.pop("selected_mint_count")
        adapted_coverage.update({
            "coverage_numerator": 1,
            "coverage_denominator": denominator,
            "coverage_label": f"1/{denominator}",
            "coverage_basis": SELECTED_STABLECOIN_FACT_COVERAGE_BASIS,
            "fact_contract": SELECTED_STABLECOIN_FACT_CONTRACT,
        })
        adapted["state"] = "current"
        adapted["coverage"] = adapted_coverage
        return adapted
    if fact.get("metric_id") != "validator_commission_pct":
        return fact
    if not isinstance(coverage, dict):
        return fact
    adapted = dict(fact)
    adapted_coverage = dict(coverage)
    contract = adapted_coverage.get("fact_contract")
    if contract == VALIDATOR_COMMISSION_FACT_CONTRACT:
        if fact.get("event_slot") is not None:
            raise FactConflictError("canonical commission facts cannot carry an event slot")
        return fact
    if contract is not None:
        return fact
    if not (
        fact.get("source") == VALIDATOR_COMMISSION_SOURCE
        and fact.get("source_revision") is None
        and fact.get("source_schema") in (7, 8)
        and fact.get("event_time") is None
        and fact.get("basis") == "measured"
        and fact.get("state") == "current"
        and fact.get("unit") == "%"
        and set(coverage) == {"identity", "vote_state"}
        and isinstance(coverage.get("identity"), str)
        and bool(coverage["identity"])
        and coverage.get("vote_state") in ("current", "delinquent")
    ):
        return adapted
    if fact.get("quality") is not None:
        raise FactConflictError("legacy commission fact has unsupported quality semantics")
    event_slot = adapted.get("event_slot")
    if not isinstance(event_slot, int) or isinstance(event_slot, bool) or event_slot < 0:
        raise FactConflictError("legacy commission snapshot slot is invalid")
    adapted_coverage["snapshot_slot"] = event_slot
    adapted["event_slot"] = None
    adapted_coverage["fact_contract"] = VALIDATOR_COMMISSION_FACT_CONTRACT
    adapted["coverage"] = adapted_coverage
    adapted["quality"] = VALIDATOR_COMMISSION_QUALITY
    return adapted


def lookup(node: Any, path: Iterable[str | int]) -> Any:
    for key in path:
        if isinstance(key, int):
            if not isinstance(node, list) or not 0 <= key < len(node):
                return None
            node = node[key]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
    return node


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _source_event_time(value: Any) -> str | None:
    """Keep an explicit source date as a date; never invent a time of day."""
    timestamp = _timestamp(value)
    if timestamp is not None:
        return timestamp
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
        if parsed.strftime("%Y-%m-%d") == value:
            return value
    return None


def selected_stablecoin_registry_source() -> dict[str, str]:
    """The fixed, pinned four-mint identity registry used by schemas 8 and 9."""
    return {
        "source_key": SELECTED_STABLECOIN_SOURCE_KEY,
        "url": SELECTED_STABLECOIN_SOURCE_URL,
        "path": SELECTED_STABLECOIN_SOURCE_PATH,
        "source_revision": SELECTED_STABLECOIN_SOURCE_REVISION,
        "source_license": SELECTED_STABLECOIN_SOURCE_LICENSE,
        "usage": SELECTED_STABLECOIN_REGISTRY_USAGE,
    }


def xstock_registry_provenance() -> dict[str, Any]:
    """Pinned identity-selection contract for the 107 labelled xStock mints."""
    return {
        "repository": "https://github.com/solana-foundation/tokens",
        "path": XSTOCK_REGISTRY_SOURCE_PATH,
        "revision": XSTOCK_REGISTRY_SOURCE_REVISION,
        "license": XSTOCK_REGISTRY_SOURCE_LICENSE,
        "selection": "address label exactly 'xStock'",
        "expected_unique_group_count": XSTOCK_REGISTRY_EXPECTED_COUNT,
        "expected_unique_mint_count": XSTOCK_REGISTRY_EXPECTED_COUNT,
    }


def xstock_registry_source() -> dict[str, Any]:
    """Fixed source fields shared by the schema gate and per-mint facts."""
    return {
        "url": XSTOCK_REGISTRY_SOURCE_URL,
        "kind": "pinned official token registry",
        "source_key": XSTOCK_REGISTRY_SOURCE_KEY,
        "source_revision": XSTOCK_REGISTRY_SOURCE_REVISION,
        "source_license": XSTOCK_REGISTRY_SOURCE_LICENSE,
        "provenance": xstock_registry_provenance(),
    }


def _timestamp_unix(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return int(parsed.timestamp())
    except (OverflowError, ValueError):
        return None


def valid_xstock_multiplier_provenance(value: Any) -> bool:
    """Validate the exact Token-2022 source evidence without replaying f64 math."""
    if not isinstance(value, dict):
        return False
    state = value.get("state")
    if not isinstance(state, dict):
        return False
    multiplier_values = (state.get("multiplier"), state.get("newMultiplier"))
    if any(isinstance(multiplier, bool) for multiplier in multiplier_values):
        return False
    try:
        multipliers = tuple(Decimal(multiplier) for multiplier in multiplier_values)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return False
    effective = state.get("newMultiplierEffectiveTimestamp")
    account_slot = value.get("rpc_context_slot")
    account_version = value.get("rpc_api_version")
    authority = state.get("authority")
    return (
        value.get("source_method") == "getAccountInfo(finalized,jsonParsed)"
        and value.get("program_id") == XSTOCK_TOKEN_2022_PROGRAM_ID
        and value.get("program") == "spl-token-2022"
        and value.get("extension") == "scaledUiAmountConfig"
        and all(multiplier.is_finite() and multiplier > 0 for multiplier in multipliers)
        and "authority" in state
        and (authority is None
             or (isinstance(authority, str) and bool(authority.strip())))
        and isinstance(effective, int) and not isinstance(effective, bool)
        and effective >= 0
        and isinstance(account_slot, int) and not isinstance(account_slot, bool)
        and account_slot >= 0
        and (account_version is None
             or (isinstance(account_version, str) and bool(account_version.strip())))
    )


def valid_xstock_legacy_account_provenance(value: Any) -> bool:
    """Validate the exact legacy SPL Token mint-account evidence."""
    if not isinstance(value, dict):
        return False
    slot = value.get("rpc_context_slot")
    version = value.get("rpc_api_version")
    return (
        value.get("source_method") == "getAccountInfo(finalized,jsonParsed)"
        and value.get("program_id") == XSTOCK_TOKEN_PROGRAM_ID
        and value.get("program") == "spl-token"
        and isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0
        and (version is None
             or isinstance(version, str) and bool(version.strip()))
    )


def validate_xstock_supply_asset(
    asset: Any, observed_at_unix: Any,
) -> dict[str, Any] | None:
    """Return normalized per-mint evidence or reject the scaled-UI row."""
    if (not isinstance(asset, dict)
            or asset.get("symbol") is not None
            or not isinstance(asset.get("name"), str) or not asset["name"].strip()
            or not isinstance(asset.get("slug"), str)
            or not asset["slug"].startswith("xstock-")
            or not isinstance(asset.get("mint"), str) or not asset["mint"]
            or not isinstance(observed_at_unix, int) or isinstance(observed_at_unix, bool)
            or observed_at_unix < 0):
        return None
    raw = asset.get("supply_raw_amount")
    decimals = asset.get("supply_decimals")
    ui_text = asset.get("supply_rpc_ui_amount_string")
    if (not isinstance(raw, str)
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw) is None
            or int(raw) > SPL_TOKEN_U64_MAX
            or not isinstance(decimals, int) or isinstance(decimals, bool)
            or not 0 <= decimals <= 255
            or not isinstance(ui_text, str)):
        return None
    try:
        ui_decimal = Decimal(ui_text)
        projection = float(ui_decimal)
    except (InvalidOperation, OverflowError, ValueError):
        return None
    supply = asset.get("supply")
    rpc_ui = asset.get("supply_rpc_ui_amount")
    if (not ui_decimal.is_finite() or ui_decimal < 0 or not math.isfinite(projection)
            or not isinstance(supply, (int, float)) or isinstance(supply, bool)
            or not math.isfinite(float(supply)) or float(supply) != projection
            or (rpc_ui is not None
                and (not isinstance(rpc_ui, (int, float)) or isinstance(rpc_ui, bool)
                     or not math.isfinite(float(rpc_ui)) or float(rpc_ui) != projection))):
        return None
    slot = asset.get("supply_context_slot")
    version = asset.get("supply_rpc_api_version")
    collected_at = asset.get("supply_collected_at")
    collected_unix = _timestamp_unix(collected_at)
    age = asset.get("supply_age_seconds")
    multiplier_provenance = asset.get("supply_multiplier_provenance")
    account_provenance = asset.get("supply_account_provenance")
    if (valid_xstock_multiplier_provenance(multiplier_provenance)
            and account_provenance is None):
        provenance = multiplier_provenance
        coverage_key = "multiplier_provenance"
        source = XSTOCK_SOURCE
        quality = XSTOCK_FACT_QUALITY
    elif (multiplier_provenance is None
          and valid_xstock_legacy_account_provenance(account_provenance)):
        provenance = account_provenance
        coverage_key = "account_provenance"
        source = XSTOCK_LEGACY_SOURCE
        quality = XSTOCK_LEGACY_FACT_QUALITY
    else:
        return None
    if (not isinstance(slot, int) or isinstance(slot, bool) or slot < 0
            or not isinstance(version, str) or not version.strip()
            or collected_unix is None or collected_unix > observed_at_unix
            or not isinstance(age, int) or isinstance(age, bool)
            or age != observed_at_unix - collected_unix
            or asset.get("supply_freshness")
            != ("fresh" if age <= XSTOCK_FRESH_SECONDS else "stale")
            or asset.get("supply_fresh_max_age_seconds") != XSTOCK_FRESH_SECONDS
            or asset.get("supply_unit") != "token"
            or asset.get("supply_source_id") != "solana_getTokenSupply"
            or asset.get("supply_source_method") != "getTokenSupply(finalized)"
            or asset.get("basis") != "finalized on-chain token supply"
            or provenance["rpc_context_slot"] > slot):
        return None
    return {
        "ui_decimal": ui_decimal,
        "projection": projection,
        "collected_at": collected_at,
        "collected_unix": collected_unix,
        "age": age,
        "state": "current" if age <= XSTOCK_FRESH_SECONDS else "stale",
        "coverage_key": coverage_key,
        "provenance": provenance,
        "source": source,
        "quality": quality,
    }


def _exact_selected_supply(asset: Any) -> tuple[Decimal, str] | None:
    """Return the exact raw/decimal reconstruction for one validated mint row."""
    if not isinstance(asset, dict):
        return None
    raw = asset.get("raw_amount")
    decimals = asset.get("decimals")
    if (not isinstance(raw, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw) is None
            or not isinstance(decimals, int) or isinstance(decimals, bool)
            or not 0 <= decimals <= 255):
        return None
    raw_integer = int(raw)
    if raw_integer > SPL_TOKEN_U64_MAX:
        return None
    try:
        exact = Decimal(raw).scaleb(-decimals)
    except InvalidOperation:
        return None
    text = format(exact, "f")
    if not exact.is_finite() or exact < 0 or asset.get("total_supply_decimal") != text:
        return None
    return exact, text


def _state(
    snapshot: dict[str, Any], path: tuple[str, ...], *, retain_recorded_value: bool = False,
) -> str:
    node: Any = snapshot
    partial = False
    for key in path[:-1]:
        if not isinstance(node, dict):
            return "unavailable"
        node = node.get(key)
        if not isinstance(node, dict):
            return "unavailable"
        if node.get("available") is False:
            if not retain_recorded_value:
                return "unavailable"
            partial = True
        if (node.get("stale") is True or node.get("source_state") == "last_known_good"
                or node.get("freshness") in ("stale", "missing", "unavailable")):
            return "stale"
        if node.get("coverage_complete") is False or node.get("transport_complete") is False:
            partial = True
    return "partial" if partial else "current"


def collection_source_key(path: tuple[str, ...]) -> str | None:
    """Map a metric's existing source path to its collection clock."""
    if path[:2] == ("validators", "block_production"):
        return "block_production"
    if path[:1] == ("growth",):
        return ("growth_providers" if len(path) > 1 and path[1] in
                ("daily_active_addresses", "daily_fee_payers") else "growth_tokens")
    if path and path[0] in ("activity", "news", "feature_activation", "dune"):
        return path[0]
    return None


def source_snapshot(snapshot: dict[str, Any], key: str | None) -> dict[str, Any]:
    """Keep canonical source facts anchored to their actual successful collection."""
    schedule = snapshot.get("collection_schedule")
    entry = schedule.get(key) if isinstance(schedule, dict) else None
    stamp = entry.get("last_success_at") if isinstance(entry, dict) else None
    section_path = {
        "block_production": ("validators", "block_production"),
        "growth_providers": ("growth", "daily_active_addresses"),
        "growth_tokens": ("growth", "tokenized_equities"),
    }.get(key, (key,))
    section = lookup(snapshot, section_path) if key else None
    if (isinstance(entry, dict) and entry.get("state") == "failed"
            and (not isinstance(section, dict) or section.get("available") is not True)):
        stamp = entry.get("last_attempt_at")
    if _timestamp(stamp) is None:
        return snapshot
    return {**snapshot, "collected_at": stamp}


def fact_from_snapshot(snapshot: dict[str, Any], metric_id: str) -> dict[str, Any]:
    spec = PUBLIC_METRICS[metric_id]
    snapshot = source_snapshot(snapshot, collection_source_key(spec["path"]))
    schema = snapshot.get("schema_version")
    compatible = (
        isinstance(schema, int) and not isinstance(schema, bool) and schema in spec["schemas"]
    )
    raw_value = lookup(snapshot, spec["path"]) if compatible else None
    record_type = spec.get("record_type", "numeric")
    public_value = None
    if record_type == "boolean" and isinstance(raw_value, bool):
        value = float(raw_value)
        public_value = raw_value
    elif record_type == "categorical" and isinstance(raw_value, str) and raw_value:
        value = 1.0
        public_value = raw_value
    elif record_type == "decimal-string" and isinstance(raw_value, str):
        try:
            decimal_value = Decimal(raw_value)
            value = float(decimal_value)
        except (InvalidOperation, OverflowError, ValueError):
            value = None
        if (value is not None and decimal_value.is_finite()
                and math.isfinite(value) and decimal_value >= 0):
            public_value = raw_value
        else:
            value = None
    else:
        value = raw_value
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            value = None
    if metric_id == "latest_non_vote_tps" and lookup(
        snapshot, ("performance", "non_vote_available")
    ) is not True:
        value = None
    state = _state(
        snapshot, spec["path"],
        retain_recorded_value=spec.get("retain_recorded_value") is True,
    ) if compatible and value is not None else "unavailable"
    event_time = _timestamp(lookup(snapshot, spec.get("event_time_path", ()))) \
        if spec.get("event_time_path") else None
    event_slot = lookup(snapshot, spec.get("event_slot_path", ())) \
        if spec.get("event_slot_path") else None
    if not isinstance(event_slot, int) or isinstance(event_slot, bool) or event_slot < 0:
        event_slot = None
    return {
        "metric_id": metric_id,
        "subject_id": None,
        "event_time": event_time,
        "event_slot": event_slot,
        "collected_at": snapshot.get("collected_at"),
        "value": float(value) if value is not None else None,
        "unit": spec["unit"],
        "basis": spec["basis"],
        "state": state,
        "source": spec["source"],
        "source_revision": None,
        "source_schema": schema if isinstance(schema, int) and not isinstance(schema, bool) else None,
        "coverage": {"public_value": public_value} if public_value is not None else None,
        "quality": None if compatible else "incompatible source schema or semantics",
    }


def facts_from_snapshots(
    snapshots: Iterable[dict[str, Any]], metric_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    ids = tuple(metric_ids or METRICS)
    return dedupe_facts(
        fact_from_snapshot(snapshot, metric_id)
        for snapshot in snapshots
        for metric_id in ids
    )


def performance_sample_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact exact performance samples by their source-native ending slot."""
    performance = snapshot.get("performance")
    samples = performance.get("samples") if isinstance(performance, dict) else None
    schema = snapshot.get("schema_version")
    if (not isinstance(samples, list) or not isinstance(schema, int)
            or isinstance(schema, bool)):
        return []
    observations = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        slot = sample.get("slot")
        value = sample.get("tps")
        period = sample.get("sample_period_secs")
        slots = sample.get("slots")
        transactions = sample.get("transactions")
        non_vote = sample.get("non_vote_transactions")
        if (not isinstance(slot, int) or isinstance(slot, bool) or slot < 0
                or not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not isinstance(period, (int, float)) or isinstance(period, bool)
                or period <= 0 or not isinstance(slots, int) or isinstance(slots, bool)
                or slots <= 0 or not isinstance(transactions, int)
                or isinstance(transactions, bool) or transactions < 0
                or (non_vote is not None and (not isinstance(non_vote, int)
                                               or isinstance(non_vote, bool)
                                               or not 0 <= non_vote <= transactions))):
            continue
        observations.append({
            "metric_id": "performance_sample_tps",
            "subject_id": None,
            "event_time": None,
            "event_slot": slot,
            "collected_at": snapshot.get("collected_at"),
            "value": float(value),
            "unit": "TPS",
            "basis": "measured",
            "state": "current",
            "source": "solana-rpc-getRecentPerformanceSamples",
            "source_revision": None,
            "source_schema": schema,
            "coverage": {
                "sample_period_seconds": period,
                "slots": slots,
                "transactions": transactions,
                "non_vote_transactions": non_vote,
            },
            "quality": "source-native sample slot and period; event time unavailable",
        })
    return dedupe_facts(observations)


def validator_commission_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Commission observations keyed by vote account; full evidence starts at v7."""
    schema = snapshot.get("schema_version")
    validators = snapshot.get("validators")
    rows = validators.get("all_validators") if isinstance(validators, dict) else None
    if (not isinstance(schema, int) or isinstance(schema, bool) or schema < 7
            or not isinstance(rows, list)):
        return []
    snapshot_slot = lookup(snapshot, ("network", "slot"))
    snapshot_slot = (snapshot_slot if isinstance(snapshot_slot, int)
                     and not isinstance(snapshot_slot, bool) else None)
    observations = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        vote_account = row.get("vote_account")
        identity = row.get("identity")
        commission = row.get("commission")
        if (not isinstance(vote_account, str) or not vote_account
                or not isinstance(identity, str) or not identity
                or not isinstance(commission, (int, float)) or isinstance(commission, bool)
                or not math.isfinite(float(commission)) or not 0 <= commission <= 100):
            continue
        observations.append({
            "metric_id": "validator_commission_pct",
            "subject_id": vote_account,
            "event_time": None,
            # getVoteAccounts returns no context slot. The separately collected
            # same-run network slot is retained only as contextual coverage.
            "event_slot": None,
            "collected_at": snapshot.get("collected_at"),
            "value": float(commission),
            "unit": "%",
            "basis": "measured",
            "state": "current" if validators.get("available") is True else "unavailable",
            "source": VALIDATOR_COMMISSION_SOURCE,
            "source_revision": None,
            "source_schema": schema,
            "coverage": {
                "identity": identity,
                "vote_state": row.get("state"),
                "snapshot_slot": snapshot_slot,
                "fact_contract": VALIDATOR_COMMISSION_FACT_CONTRACT,
            },
            "quality": VALIDATOR_COMMISSION_QUALITY,
        })
    return dedupe_facts(observations)


def _subject_numeric_fact(
    snapshot: dict[str, Any], *, metric_id: str, subject_id: str | None,
    value: Any, unit: str, basis: str, source: str, event_slot: int | None,
    state: str, coverage: dict[str, Any], quality: str,
) -> dict[str, Any] | None:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value))):
        return None
    return {
        "metric_id": metric_id,
        "subject_id": subject_id,
        "event_time": None,
        "event_slot": event_slot,
        "collected_at": snapshot.get("collected_at"),
        "value": float(value),
        "unit": unit,
        "basis": basis,
        "state": state,
        "source": source,
        "source_revision": None,
        "source_schema": snapshot.get("schema_version"),
        "coverage": coverage,
        "quality": quality,
    }


def performance_sample_detail_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Public row-level metrics shown by the throughput chart and inspector."""
    performance = snapshot.get("performance")
    samples = performance.get("samples") if isinstance(performance, dict) else None
    if snapshot.get("schema_version") not in (8, 9) or not isinstance(samples, list):
        return []
    observations = []
    fields = (
        ("performance_sample_transactions", "transactions", "transactions"),
        ("performance_sample_non_vote_transactions", "non_vote_transactions", "transactions"),
        ("performance_sample_period_seconds", "sample_period_secs", "s"),
        ("performance_sample_slots", "slots", "slots"),
        ("performance_sample_non_vote_tps", "non_vote_tps", "TPS"),
        ("performance_sample_vote_tps", "vote_tps", "TPS"),
        ("performance_sample_vote_share_pct", "vote_share_pct", "%"),
        ("performance_sample_slot_time_secs", "slot_time_secs", "s"),
    )
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        slot = sample.get("slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
            continue
        coverage = {"sample_slot": slot, "sample_period_seconds": sample.get("sample_period_secs")}
        for metric_id, key, unit in fields:
            fact = _subject_numeric_fact(
                snapshot,
                metric_id=metric_id,
                subject_id=f"slot:{slot}",
                value=sample.get(key),
                unit=unit,
                basis="measured",
                source="solana-rpc-getRecentPerformanceSamples",
                event_slot=slot,
                state="current" if performance.get("available") is True else "unavailable",
                coverage={**coverage, "source_field": key},
                quality="source-native sample slot and period; event time unavailable",
            )
            if fact is not None:
                observations.append(fact)
    return dedupe_facts(observations)


def validator_detail_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Public row-level metrics shown in the validator evidence table."""
    validators = snapshot.get("validators")
    rows = validators.get("all_validators") if isinstance(validators, dict) else None
    if snapshot.get("schema_version") not in (8, 9) or not isinstance(rows, list):
        return []
    snapshot_slot = lookup(snapshot, ("network", "slot"))
    snapshot_slot = snapshot_slot if isinstance(snapshot_slot, int) and not isinstance(snapshot_slot, bool) else None
    observations = []
    fields = (
        ("validator_stake_sol", "stake_sol", "SOL"),
        ("validator_stake_share_pct", "share_pct", "%"),
        ("validator_last_vote_slot", "last_vote", "slot"),
        ("validator_root_slot", "root_slot", "slot"),
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        vote_account = row.get("vote_account")
        identity = row.get("identity")
        if not isinstance(vote_account, str) or not vote_account or not isinstance(identity, str):
            continue
        for metric_id, key, unit in fields:
            fact = _subject_numeric_fact(
                snapshot,
                metric_id=metric_id,
                subject_id=vote_account,
                value=row.get(key),
                unit=unit,
                basis="measured",
                source=VALIDATOR_COMMISSION_SOURCE,
                event_slot=None,
                state="current" if validators.get("available") is True else "unavailable",
                coverage={
                    "identity": identity,
                    "vote_state": row.get("state"),
                    "snapshot_slot": snapshot_slot,
                    "source_field": key,
                },
                quality=VALIDATOR_COMMISSION_QUALITY,
            )
            if fact is not None:
                observations.append(fact)
    return dedupe_facts(observations)


def block_production_detail_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Public identity-level metrics shown in completed-epoch production rows."""
    validators = snapshot.get("validators")
    production = validators.get("block_production") if isinstance(validators, dict) else None
    rows = production.get("validators") if isinstance(production, dict) else None
    if (snapshot.get("schema_version") not in (8, 9) or not isinstance(rows, list)
            or production.get("available") is not True):
        return []
    observations = []
    fields = (
        ("validator_leader_slots", "leader_slots", "slots"),
        ("validator_blocks_produced", "blocks_produced", "blocks"),
        ("validator_skipped_slots", "skipped_slots", "slots"),
        ("validator_skip_rate", "skip_rate", "ratio"),
        ("validator_production_vote_account_count", "vote_account_count", "vote accounts"),
    )
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("identity"), str):
            continue
        for metric_id, key, unit in fields:
            fact = _subject_numeric_fact(
                snapshot,
                metric_id=metric_id,
                subject_id=row["identity"],
                value=row.get(key),
                unit=unit,
                basis="measured",
                source="solana-rpc-getBlockProduction",
                event_slot=production.get("last_slot") if isinstance(production.get("last_slot"), int) else None,
                state="current",
                coverage={"completed_epoch": production.get("epoch"), "source_field": key},
                quality="completed-epoch finalized production; vote-account join may be unmatched",
            )
            if fact is not None:
                observations.append(fact)
    return dedupe_facts(observations)


def simd_lifecycle_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Recorded SIMD frontmatter states keyed by proposal and pinned commit."""
    schema = snapshot.get("schema_version")
    news = snapshot.get("news")
    sources = news.get("sources") if isinstance(news, dict) else None
    simd = sources.get("simd_proposals") if isinstance(sources, dict) else None
    proposals = simd.get("proposals") if isinstance(simd, dict) else None
    commit = simd.get("source_commit") if isinstance(simd, dict) else None
    if (schema not in (8, 9) or not isinstance(simd, dict)
            or simd.get("available") is not True
            or not isinstance(proposals, list) or not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        return []
    observations = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        identifier = proposal.get("identifier")
        name = proposal.get("name")
        status = proposal.get("status")
        source = proposal.get("source")
        if (not isinstance(identifier, str) or not identifier
                or not isinstance(name, str) or not name
                or not isinstance(status, str) or not status
                or not isinstance(source, str) or not source.startswith("https://")
                or proposal.get("source_commit") != commit
                or proposal.get("basis") != "recorded"):
            continue
        observations.append({
            "metric_id": "simd_lifecycle_status",
            "subject_id": identifier,
            "event_time": _source_event_time(proposal.get("created")),
            "event_slot": None,
            "collected_at": snapshot.get("collected_at"),
            "value": None,
            "unit": "lifecycle status",
            "basis": "recorded",
            "state": "partial" if simd.get("partial") is True else "current",
            "source": "solana-foundation-simd-frontmatter",
            "source_revision": commit,
            "source_schema": schema,
            "coverage": {
                "status": status,
                "name": name,
                "created": proposal.get("created"),
                "source": source,
                "source_path": proposal.get("source_path"),
                "source_commit": commit,
            },
            "quality": simd.get("lifecycle_note") if isinstance(
                simd.get("lifecycle_note"), str,
            ) else "explicit proposal frontmatter from a pinned source commit",
        })
    return dedupe_facts(observations)


def provider_activity_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain source-native Solana Data provider/date observations."""
    schema = snapshot.get("schema_version")
    if schema not in (8, 9):
        return []
    growth = snapshot.get("growth")
    if not isinstance(growth, dict):
        return []
    observations = []
    for key, expected_id in (
        ("daily_active_addresses", "stablecoin_active_address_provider_range"),
        ("daily_fee_payers", "transaction_initiator_provider_range"),
    ):
        summary = growth.get(key)
        rows = summary.get("provider_observations") if isinstance(summary, dict) else None
        if (not isinstance(summary, dict) or summary.get("history_available") is not True
                or summary.get("semantic_metric_id") != expected_id
                or not isinstance(rows, list)):
            continue
        state = "partial" if summary.get("partial") is True else "current"
        for row in rows:
            if not isinstance(row, dict):
                continue
            provider = row.get("provider")
            event_time = _source_event_time(row.get("date"))
            value = row.get("value")
            if (not isinstance(provider, str) or not provider or event_time is None
                    or not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or value < 0):
                continue
            observations.append({
                "metric_id": expected_id,
                "subject_id": provider,
                "event_time": event_time,
                "event_slot": None,
                "collected_at": snapshot.get("collected_at"),
                "value": float(value),
                "unit": "addresses" if key == "daily_active_addresses" else "initiators",
                "basis": "recorded",
                "state": state,
                "source": "solana-data-provider-benchmark",
                "source_revision": None,
                "source_schema": schema,
                "coverage": {
                    "source_label": summary.get("source_label"),
                    "display_name": summary.get("display_name"),
                    "scope": summary.get("scope"),
                    "source_url": summary.get("source_url"),
                    "source_generated_at": summary.get("source_generated_at"),
                },
                "quality": summary.get("note"),
            })
    return dedupe_facts(observations)


def selected_usd_stablecoin_supply_facts(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retain one exact-evidence fact for each observed selected mint."""
    schema = snapshot.get("schema_version")
    if schema not in (8, 9):
        return []
    growth = snapshot.get("growth")
    summary = growth.get("selected_usd_stablecoins") if isinstance(growth, dict) else None
    assets = summary.get("assets") if isinstance(summary, dict) else None
    if (not isinstance(summary, dict)
            or summary.get("metric_id") != SELECTED_STABLECOIN_METRIC_ID
            or summary.get("registry_source") != selected_stablecoin_registry_source()
            or not isinstance(assets, list)
            or len(assets) != len(SELECTED_STABLECOIN_IDENTITIES)):
        return []
    numerator = summary.get("coverage_numerator")
    denominator = summary.get("coverage_denominator")
    state = summary.get("state")
    if (not isinstance(numerator, int) or isinstance(numerator, bool)
            or denominator != len(SELECTED_STABLECOIN_IDENTITIES)
            or not 0 <= numerator <= denominator
            or state != ("current" if numerator == denominator
                         else ("partial" if numerator else "unavailable"))
            or summary.get("available") is not (numerator == denominator)):
        return []
    observations = []
    for asset, (symbol, issuer, mint) in zip(assets, SELECTED_STABLECOIN_IDENTITIES):
        if (not isinstance(asset, dict)
                or (asset.get("symbol"), asset.get("issuer"), asset.get("mint"))
                != (symbol, issuer, mint)):
            return []
        if asset.get("available") is not True:
            continue
        exact_supply = _exact_selected_supply(asset)
        collected_at = _timestamp(asset.get("collected_at"))
        slot = asset.get("rpc_context_slot")
        api_version = asset.get("rpc_api_version")
        ui_amount = asset.get("rpc_ui_amount_string")
        provenance = asset.get("account_provenance")
        if (exact_supply is None or collected_at is None
                or not isinstance(slot, int) or isinstance(slot, bool) or slot < 0
                or not isinstance(api_version, str) or not api_version
                or not isinstance(ui_amount, str)
                or not isinstance(provenance, dict)):
            return []
        try:
            ui_decimal = Decimal(ui_amount)
            value = float(exact_supply[0])
        except (InvalidOperation, OverflowError, ValueError):
            return []
        program_id = provenance.get("program_id")
        account_slot = provenance.get("rpc_context_slot")
        account_api_version = provenance.get("rpc_api_version")
        if (not ui_decimal.is_finite() or ui_decimal < 0 or not math.isfinite(value)
                or ui_decimal != exact_supply[0]
                or provenance.get("source_method")
                != "getAccountInfo(finalized,jsonParsed)"
                or SELECTED_STABLECOIN_TOKEN_PROGRAMS.get(program_id)
                != provenance.get("program")
                or not isinstance(account_slot, int) or isinstance(account_slot, bool)
                or account_slot < 0
                or (account_api_version is not None
                    and (not isinstance(account_api_version, str)
                         or not account_api_version))):
            return []
        observations.append({
            "metric_id": SELECTED_STABLECOIN_METRIC_ID,
            "subject_id": mint,
            "event_time": None,
            "event_slot": slot,
            "collected_at": collected_at,
            "value": value,
            "unit": SELECTED_STABLECOIN_UNIT,
            "basis": "measured",
            "state": "current",
            "source": SELECTED_STABLECOIN_SOURCE,
            "source_revision": None,
            "source_schema": schema,
            "coverage": {
                "symbol": symbol,
                "issuer": issuer,
                "mint": mint,
                "raw_amount": asset["raw_amount"],
                "decimals": asset["decimals"],
                "total_supply_decimal": exact_supply[1],
                "rpc_ui_amount_string": ui_amount,
                "rpc_api_version": api_version,
                "account_source_method": provenance["source_method"],
                "account_rpc_context_slot": account_slot,
                "account_rpc_api_version": account_api_version,
                "token_program_id": program_id,
                "token_program": provenance["program"],
                "registry_source_key": SELECTED_STABLECOIN_SOURCE_KEY,
                "registry_source_path": SELECTED_STABLECOIN_SOURCE_PATH,
                "registry_source_url": SELECTED_STABLECOIN_SOURCE_URL,
                "registry_source_revision": SELECTED_STABLECOIN_SOURCE_REVISION,
                "registry_source_license": SELECTED_STABLECOIN_SOURCE_LICENSE,
                "coverage_numerator": 1,
                "coverage_denominator": denominator,
                "coverage_label": f"1/{denominator}",
                "coverage_basis": SELECTED_STABLECOIN_FACT_COVERAGE_BASIS,
                "universe_coverage": "unknown",
                "fact_contract": SELECTED_STABLECOIN_FACT_CONTRACT,
                "value_contract": SELECTED_STABLECOIN_FACT_VALUE_CONTRACT,
            },
            "quality": SELECTED_STABLECOIN_FACT_QUALITY,
        })
    return dedupe_facts(observations) if len(observations) == numerator else []


def xstock_labelled_mint_supply_facts(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retain only per-mint validated UI supply facts; never aggregate mints."""
    schema = snapshot.get("schema_version")
    if schema not in (8, 9):
        return []
    growth = snapshot.get("growth")
    equities = growth.get("tokenized_equities") if isinstance(growth, dict) else None
    sources = growth.get("sources") if isinstance(growth, dict) else None
    registry = sources.get("registry") if isinstance(sources, dict) else None
    assets = equities.get("all_assets") if isinstance(equities, dict) else None
    coverage = equities.get("supply_coverage") if isinstance(equities, dict) else None
    observed_at = equities.get("observed_at_unix") if isinstance(equities, dict) else None
    fixed_registry = xstock_registry_source()
    asset_mints = [
        asset.get("mint") for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("mint"), str)
        and asset["mint"]
    ] if isinstance(assets, list) else []
    asset_slugs = [
        asset.get("slug") for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("slug"), str)
        and asset["slug"].startswith("xstock-")
    ] if isinstance(assets, list) else []
    if (not isinstance(equities, dict) or not isinstance(registry, dict)
            or not isinstance(assets, list) or len(assets) != XSTOCK_REGISTRY_EXPECTED_COUNT
            or len(asset_mints) != XSTOCK_REGISTRY_EXPECTED_COUNT
            or len(set(asset_mints)) != XSTOCK_REGISTRY_EXPECTED_COUNT
            or len(asset_slugs) != XSTOCK_REGISTRY_EXPECTED_COUNT
            or len(set(asset_slugs)) != XSTOCK_REGISTRY_EXPECTED_COUNT
            or not isinstance(coverage, dict)
            or coverage.get("registry_complete") is not True
            or coverage.get("fresh_max_age_seconds") != XSTOCK_FRESH_SECONDS
            or coverage.get("registry_asset_count") != XSTOCK_REGISTRY_EXPECTED_COUNT
            or coverage.get("eligible_asset_count") != XSTOCK_REGISTRY_EXPECTED_COUNT
            or coverage.get("coverage_denominator") != XSTOCK_REGISTRY_EXPECTED_COUNT
            or registry.get("available") is not True
            or registry.get("coverage_complete") is not True
            or registry.get("asset_count") != XSTOCK_REGISTRY_EXPECTED_COUNT
            or registry.get("reason") is not None
            or any(registry.get(key) != value for key, value in fixed_registry.items())):
        return []

    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            return []
        if asset.get("supply") is None:
            continue
        validated = validate_xstock_supply_asset(asset, observed_at)
        if validated is None:
            return []
        rows.append((asset, validated))
    observed_count = coverage.get("observed_asset_count")
    fresh_count = coverage.get("fresh_asset_count")
    numerator = coverage.get("coverage_numerator")
    expected_fresh = sum(row[1]["age"] <= XSTOCK_FRESH_SECONDS for row in rows)
    expected_numerator = sum(row[1]["age"] <= XSTOCK_SWEEP_SECONDS for row in rows)
    if (not isinstance(observed_count, int) or isinstance(observed_count, bool)
            or observed_count != len(rows)
            or fresh_count != expected_fresh
            or numerator != expected_numerator):
        return []

    observations = []
    for asset, validated in rows:
        provenance_key = validated["coverage_key"]
        observations.append({
            "metric_id": XSTOCK_METRIC_ID,
            "subject_id": asset["mint"],
            "event_time": None,
            "event_slot": asset["supply_context_slot"],
            "collected_at": validated["collected_at"],
            "value": validated["projection"],
            "unit": "token",
            "basis": "measured",
            # Freshness belongs to the report snapshot. This fact is the
            # immutable source observation at its own collection time.
            "state": "current",
            "source": validated["source"],
            "source_revision": None,
            "source_schema": schema,
            "coverage": {
                "symbol": asset["symbol"],
                "name": asset["name"],
                "slug": asset["slug"],
                "mint": asset["mint"],
                "raw_amount": asset["supply_raw_amount"],
                "decimals": asset["supply_decimals"],
                "rpc_ui_amount": asset["supply_rpc_ui_amount"],
                "rpc_ui_amount_string": asset["supply_rpc_ui_amount_string"],
                "supply_context_slot": asset["supply_context_slot"],
                "rpc_api_version": asset["supply_rpc_api_version"],
                "supply_collected_at": validated["collected_at"],
                provenance_key: validated["provenance"],
                "registry_source_key": XSTOCK_REGISTRY_SOURCE_KEY,
                "registry_source_path": XSTOCK_REGISTRY_SOURCE_PATH,
                "registry_source_url": XSTOCK_REGISTRY_SOURCE_URL,
                "registry_source_revision": XSTOCK_REGISTRY_SOURCE_REVISION,
                "registry_source_license": XSTOCK_REGISTRY_SOURCE_LICENSE,
                "registry_provenance": xstock_registry_provenance(),
                "registry_complete": True,
                "coverage_numerator": 1,
                "coverage_denominator": XSTOCK_REGISTRY_EXPECTED_COUNT,
                "coverage_label": f"1/{XSTOCK_REGISTRY_EXPECTED_COUNT}",
                "coverage_basis": XSTOCK_FACT_COVERAGE_BASIS,
                "fact_contract": XSTOCK_FACT_CONTRACT,
                "value_contract": XSTOCK_FACT_VALUE_CONTRACT,
            },
            "quality": validated["quality"],
        })
    return dedupe_facts(observations)


def source_availability_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """One current boolean observation for every recorded catalog source entry."""
    schema = snapshot.get("schema_version")
    if schema not in (8, 9):
        return []
    observations = []
    for section, metric_id in (
        ("economics", "economic_source_available"),
        ("news", "news_source_available"),
    ):
        node = snapshot.get(section)
        sources = node.get("sources") if isinstance(node, dict) else None
        if not isinstance(sources, dict):
            continue
        for source_id, source in sorted(sources.items()):
            if not isinstance(source_id, str) or not source_id or not isinstance(source, dict):
                continue
            available = source.get("available") is True
            observations.append({
                "metric_id": metric_id,
                "subject_id": source_id,
                "event_time": None,
                "event_slot": None,
                "collected_at": snapshot.get("collected_at"),
                "value": float(available),
                "unit": "boolean",
                "basis": "recorded",
                "state": "current",
                "source": f"snapshot-{section}-source-status",
                "source_revision": None,
                "source_schema": schema,
                "coverage": {
                    "section": section,
                    "label": source.get("label"),
                    "publisher": source.get("publisher"),
                    "reason": source.get("reason"),
                    "partial": source.get("partial"),
                    "source_url": (
                        source.get("url") or source.get("source_url")
                        or source.get("endpoint")
                    ),
                },
                "quality": (
                    "recorded source availability; false means the source entry is unavailable, "
                    "not that any ecosystem metric is zero"
                ),
            })
    return dedupe_facts(observations)


def snapshot_facts(
    snapshot: dict[str, Any], metric_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """One append-only fact pack for a successfully normalized snapshot."""
    return dedupe_facts([
        *facts_from_snapshots((snapshot,), metric_ids),
        *performance_sample_facts(snapshot),
        *validator_commission_facts(snapshot),
        *simd_lifecycle_facts(source_snapshot(snapshot, "news")),
        *provider_activity_facts(source_snapshot(snapshot, "growth_providers")),
        *selected_usd_stablecoin_supply_facts(source_snapshot(snapshot, "growth_tokens")),
        *xstock_labelled_mint_supply_facts(source_snapshot(snapshot, "growth_tokens")),
    ])


def _public_fact_metadata(fact: dict[str, Any]) -> dict[str, Any]:
    """Describe one existing fact without creating a second metric registry."""
    metric_id = fact["metric_id"]
    coverage = fact.get("coverage") if isinstance(fact.get("coverage"), dict) else {}
    spec = PUBLIC_METRICS.get(metric_id)
    if spec is not None:
        coverage_value = coverage.get("public_value")
        return {
            "name": spec["label"],
            "population": spec["population"],
            "denominator": spec["denominator"],
            "window": spec["window"],
            "collection_method": spec["collection_method"],
            "calculation_method": spec["calculation_method"],
            "caveat": spec["caveat"],
            "value": coverage_value if coverage_value is not None else fact.get("value"),
            "type": spec.get("record_type", "numeric"),
            "source_path": ".".join(str(part) for part in spec["path"]),
            "source_url": spec["source_url"],
        }
    if metric_id == "cluster_software_version_nodes":
        return {
            "name": f"Nodes reporting version {fact.get('subject_id')}",
            "population": "unique node pubkeys returned by the queried RPC",
            "denominator": "observed node population", "window": "one getClusterNodes response",
            "collection_method": "Solana RPC getClusterNodes",
            "calculation_method": "count nodes with this exact returned version string",
            "caveat": "Unweighted gossip-node version count, not validator, stake or client adoption.",
            "value": fact.get("value"), "type": "numeric",
            "source_path": "cluster_software.versions", "source_url": SOLANA_RPC_DOCS + "getclusternodes",
        }
    if metric_id in ("economic_source_available", "news_source_available"):
        section = "economics" if metric_id.startswith("economic_") else "news"
        label = coverage.get("label") or fact.get("subject_id")
        source_url = coverage.get("source_url")
        if not isinstance(source_url, str) or not source_url.startswith("https://"):
            source_url = (
                "https://github.com/ashtonships/solana-ecosystem-report/blob/main/facts.py"
            )
        return {
            "name": f"{label} source available",
            "population": f"one recorded {section} source entry",
            "denominator": "not applicable",
            "window": "selected snapshot collection",
            "collection_method": f"read the validated {section}.sources entry",
            "calculation_method": "true when the recorded source available field is true",
            "caveat": fact.get("quality"),
            "value": bool(fact.get("value")),
            "type": "boolean",
            "source_path": (
                f"{section}.sources[{json.dumps(fact.get('subject_id'))}].available"
            ),
            "source_url": source_url,
        }
    if metric_id == "performance_sample_tps":
        period = coverage.get("sample_period_seconds")
        return {
            "name": "Performance sample TPS",
            "population": "transactions in one getRecentPerformanceSamples row",
            "denominator": f"{period} sample seconds" if _is_number(period) else None,
            "window": (
                f"{period} seconds ending at Solana slot {fact.get('event_slot')}"
                if _is_number(period) and isinstance(fact.get("event_slot"), int) else None
            ),
            "collection_method": "Solana RPC getRecentPerformanceSamples",
            "calculation_method": "transactions / sample_period_seconds",
            "caveat": "Source-native sample slot and period; event time unavailable.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": "fact.value derived from coverage.transactions and coverage.sample_period_seconds",
            "source_url": GET_RECENT_PERFORMANCE_SAMPLES_URL,
        }
    performance_sample_fields = {
        "performance_sample_transactions": (
            "Performance-sample transactions", "transactions", "recorded numTransactions field",
        ),
        "performance_sample_non_vote_transactions": (
            "Performance-sample non-vote transactions", "non_vote_transactions",
            "recorded numNonVoteTransactions field",
        ),
        "performance_sample_period_seconds": (
            "Performance-sample period", "sample_period_secs", "recorded samplePeriodSecs field",
        ),
        "performance_sample_slots": (
            "Performance-sample slots", "slots", "recorded numSlots field",
        ),
        "performance_sample_non_vote_tps": (
            "Performance-sample non-vote TPS", "non_vote_tps",
            "non-vote transactions / sample period seconds",
        ),
        "performance_sample_vote_tps": (
            "Performance-sample vote TPS", "vote_tps",
            "(transactions - non-vote transactions) / sample period seconds",
        ),
        "performance_sample_vote_share_pct": (
            "Performance-sample vote share", "vote_share_pct",
            "100 * vote transactions / transactions",
        ),
        "performance_sample_slot_time_secs": (
            "Performance-sample slot time", "slot_time_secs",
            "sample period seconds / slots",
        ),
    }
    if metric_id in performance_sample_fields:
        name, field, calculation = performance_sample_fields[metric_id]
        period = coverage.get("sample_period_seconds")
        return {
            "name": name,
            "population": "one getRecentPerformanceSamples row",
            "denominator": f"{period} sample seconds" if _is_number(period) else "source row",
            "window": f"sample ending at Solana slot {fact.get('event_slot')}",
            "collection_method": "Solana RPC getRecentPerformanceSamples",
            "calculation_method": calculation,
            "caveat": "Source-native sample slot and period; event time unavailable.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": f"performance.samples[].{field}",
            "source_url": GET_RECENT_PERFORMANCE_SAMPLES_URL,
        }
    if metric_id == "validator_commission_pct":
        return {
            "name": "Validator commission",
            "population": "one vote account returned by getVoteAccounts",
            "denominator": "not applicable",
            "window": "point-in-time RPC response",
            "collection_method": "Solana RPC getVoteAccounts",
            "calculation_method": "recorded commission field; no calculation",
            "caveat": "getVoteAccounts exposes no source-native observation slot.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": "fact.value from the vote-account commission field",
            "source_url": GET_VOTE_ACCOUNTS_URL,
        }
    validator_fields = {
        "validator_stake_sol": ("Validator activated stake", "stake_sol", "recorded activated stake in SOL"),
        "validator_stake_share_pct": (
            "Validator active-stake share", "share_pct", "100 * row activated stake / total active stake",
        ),
        "validator_last_vote_slot": ("Validator last vote", "last_vote", "recorded lastVote field"),
        "validator_root_slot": ("Validator root slot", "root_slot", "recorded rootSlot field"),
    }
    if metric_id in validator_fields:
        name, field, calculation = validator_fields[metric_id]
        return {
            "name": name,
            "population": "one vote account returned by getVoteAccounts",
            "denominator": "total active stake" if field == "share_pct" else "not applicable",
            "window": "point-in-time RPC response",
            "collection_method": "Solana RPC getVoteAccounts",
            "calculation_method": calculation,
            "caveat": "A vote account is not necessarily a unique validator operator.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": f"validators.all_validators[].{field}",
            "source_url": GET_VOTE_ACCOUNTS_URL,
        }
    production_fields = {
        "validator_leader_slots": ("Validator leader slots", "leader_slots"),
        "validator_blocks_produced": ("Validator blocks produced", "blocks_produced"),
        "validator_skipped_slots": ("Validator skipped slots", "skipped_slots"),
        "validator_skip_rate": ("Validator skip rate", "skip_rate"),
        "validator_production_vote_account_count": (
            "Matched vote-account count", "vote_account_count",
        ),
    }
    if metric_id in production_fields:
        name, field = production_fields[metric_id]
        calculation = (
            "skipped slots / leader slots" if field == "skip_rate"
            else "recorded completed-epoch identity aggregate"
        )
        return {
            "name": name,
            "population": "one leader identity in the completed-epoch production response",
            "denominator": "leader slots" if field == "skip_rate" else "not applicable",
            "window": f"completed epoch {coverage.get('completed_epoch')}",
            "collection_method": "Solana RPC getBlockProduction",
            "calculation_method": calculation,
            "caveat": "Identity-level production; vote-account joins may be unmatched.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": f"validators.block_production.validators[].{field}",
            "source_url": GET_BLOCK_PRODUCTION_URL,
        }
    if metric_id in {"feature_activation_state", "feature_activated_at_slot"}:
        is_state = metric_id == "feature_activation_state"
        return {
            "name": f"{coverage.get('title')} {'account state' if is_state else 'activation slot'}",
            "population": "one pinned official Agave feature account", "denominator": "not applicable",
            "window": "finalized RPC response context", "collection_method": "getMultipleAccounts with finalized commitment",
            "calculation_method": "decode the Feature program Option<u64> account state",
            "caveat": fact.get("quality"),
            "value": coverage.get("state") if is_state and fact.get("state") != "unavailable" else fact.get("value"),
            "type": "categorical" if is_state else "numeric",
            "source_path": "feature_activation.features[]." + ("state" if is_state else "activated_at_slot"),
            "source_url": SOLANA_RPC_DOCS + "getmultipleaccounts",
        }
    if metric_id in {
        "dune_daily_non_vote_fee_payers", "dune_daily_dex_volume_usd",
        "dune_daily_xstocks_dex_volume_usd", "dune_daily_xstocks_dex_trade_legs",
        "dune_daily_xstocks_dex_priced_trade_legs", "dune_daily_transaction_fees_sol",
    }:
        return {
            "name": coverage.get("name"), "population": coverage.get("population"),
            "denominator": "one UTC day within the registered query coverage",
            "window": (
                (("completed UTC day " if coverage.get("complete_day") else "legacy partial UTC day ")
                 + str(coverage.get("day"))) if coverage.get("day") else "unavailable"
            ),
            "collection_method": "Dune execution results with recorded query/execution identity and result hash",
            "calculation_method": coverage.get("calculation"), "caveat": fact.get("quality"),
            "value": fact.get("value"), "type": "numeric",
            "source_path": coverage.get("source_path"),
            "source_url": coverage.get("source_url"),
        }
    if metric_id == "simd_lifecycle_status":
        status = coverage.get("status")
        return {
            "name": coverage.get("name"),
            "population": "one proposal in the pinned SIMD source revision",
            "denominator": "not applicable",
            "window": "pinned source revision",
            "collection_method": "recorded SIMD repository frontmatter",
            "calculation_method": "recorded lifecycle status; no calculation",
            "caveat": "Proposal frontmatter is not proof of implementation or activation.",
            "value": status if isinstance(status, str) and status else None,
            "type": "categorical",
            "source_path": "coverage.status",
            "source_url": coverage.get("source"),
        }
    if metric_id in (
        "stablecoin_active_address_provider_range",
        "transaction_initiator_provider_range",
    ):
        return {
            "name": coverage.get("display_name"),
            "population": coverage.get("scope"),
            "denominator": "provider-specific count; no common cross-provider denominator",
            "window": "provider observation date",
            "collection_method": "recorded Solana Data provider observation",
            "calculation_method": "recorded provider value; no cross-provider aggregation",
            "caveat": "Provider methodologies may differ; not network-wide daily active addresses.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": "fact.value from the provider observation row",
            "source_url": coverage.get("source_url"),
        }
    if metric_id == SELECTED_STABLECOIN_SHARE_METRIC_ID:
        symbol = coverage.get("symbol")
        return {
            "name": f"{symbol} share of selected stablecoin total" if symbol else None,
            "population": "one selected USD stablecoin mint",
            "denominator": "exact total supply of the complete selected four-mint list",
            "window": "point-in-time finalized token-supply observations",
            "collection_method": "Solana RPC getTokenSupply(finalized) for all four selected mints",
            "calculation_method": "exact per-mint supply / exact selected-list total supply",
            "caveat": SELECTED_STABLECOIN_LIMITATIONS,
            "value": coverage.get("share_of_selected_total"),
            "type": "decimal-string",
            "source_path": "growth.selected_usd_stablecoins.assets[].share_of_selected_total",
            "source_url": GET_TOKEN_SUPPLY_URL,
        }
    if metric_id == SELECTED_STABLECOIN_METRIC_ID:
        symbol = coverage.get("symbol")
        denominator = coverage.get("coverage_denominator")
        return {
            "name": f"{symbol} selected stablecoin total supply" if symbol else None,
            "population": "one selected USD stablecoin mint",
            "denominator": (
                f"{denominator} selected mints in the pinned registry"
                if isinstance(denominator, int) and not isinstance(denominator, bool) else None
            ),
            "window": "point-in-time finalized token-supply observation",
            "collection_method": "Solana RPC getTokenSupply(finalized) with account provenance",
            "calculation_method": "raw amount scaled by decimals and checked against RPC UI amount",
            "caveat": SELECTED_STABLECOIN_LIMITATIONS,
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": "fact.value with exact evidence in coverage.total_supply_decimal",
            "source_url": GET_TOKEN_SUPPLY_URL,
        }
    if metric_id == XSTOCK_METRIC_ID:
        name = coverage.get("name")
        denominator = coverage.get("coverage_denominator")
        legacy = fact.get("source") == XSTOCK_LEGACY_SOURCE
        return {
            "name": f"{name} labelled-mint total supply" if name else None,
            "population": "one labelled xStock mint",
            "denominator": (
                f"{denominator} labelled mints in the pinned registry"
                if isinstance(denominator, int) and not isinstance(denominator, bool) else None
            ),
            "window": (
                "point-in-time finalized legacy SPL Token supply observation" if legacy
                else "point-in-time finalized scaled-UI token-supply observation"
            ),
            "collection_method": (
                "Solana RPC getTokenSupply(finalized) and legacy SPL Token account inspection"
                if legacy else
                "Solana RPC getTokenSupply(finalized) and Token-2022 account inspection"
            ),
            "calculation_method": (
                "recorded UI amount checked against exact legacy account provenance" if legacy
                else "recorded scaled UI amount with pinned multiplier provenance"
            ),
            "caveat": "Per-mint total supply; no heterogeneous aggregate, value, liquidity, or volume.",
            "value": fact.get("value"),
            "type": "numeric",
            "source_path": "fact.value from coverage.rpc_ui_amount_string",
            "source_url": GET_TOKEN_SUPPLY_URL,
        }
    return {}


def public_observation_id(
    *, record_kind: str, metric_id: str, subject_id: str | None,
    snapshot_collected_at: str, observed_at: str | None, observed_slot: int | None,
    source: str, source_revision: str | None = None, source_ordinal: int | None = None,
) -> str:
    """Stable public identity at the metric's actual observation grain."""
    identity = [
        record_kind, metric_id, subject_id, snapshot_collected_at, observed_at,
        observed_slot, source, source_revision, source_ordinal,
    ]
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "obs-v1:" + hashlib.sha256(encoded).hexdigest()


def derived_public_observation_record(
    *, metric_id: str, subject_id: str, name: str, value: int | float | str | None,
    unit: str, population: str, denominator: str, window: str,
    snapshot_collected_at: str, source_path: str, calculation_method: str,
    input_observation_ids: list[str], basis: str = "derived",
    caveat: str = "Deterministic transform of the identified public observations.",
    record_type: str = "numeric",
    source_url: str = (
        "https://github.com/ashtonships/solana-ecosystem-report/blob/main/render.py"
    ),
) -> dict[str, Any]:
    """Build one fully described derived record from identified public inputs."""
    if not all(isinstance(item, str) and item for item in (
        metric_id, subject_id, name, unit, population, denominator, window,
        snapshot_collected_at, source_path, calculation_method, basis, caveat,
        record_type, source_url,
    )):
        raise FactConflictError("derived public observation metadata is incomplete")
    if not input_observation_ids or any(
        not isinstance(item, str) or not item.startswith("obs-v1:")
        for item in input_observation_ids
    ):
        raise FactConflictError("derived public observation inputs are incomplete")
    if record_type not in ("numeric", "decimal-string"):
        raise FactConflictError("derived public observation type is unsupported")
    if value is not None:
        if record_type == "numeric" and not _is_number(value):
            raise FactConflictError("derived public observation value must be finite numeric or null")
        if record_type == "decimal-string":
            try:
                exact_value = Decimal(value) if isinstance(value, str) else None
            except InvalidOperation:
                exact_value = None
            if exact_value is None or not exact_value.is_finite():
                raise FactConflictError(
                    "derived public observation value must be a finite decimal string or null"
                )
    source = "deterministic-public-observation-derivation"
    derivation_revision = hashlib.sha256(json.dumps(
        {
            "calculation_method": calculation_method,
            "derivation_identity_version": 1,
            "input_observation_ids": input_observation_ids,
        },
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    observation_id = public_observation_id(
        record_kind="derived", metric_id=metric_id, subject_id=subject_id,
        snapshot_collected_at=snapshot_collected_at, observed_at=snapshot_collected_at,
        observed_slot=None, source=source, source_revision=derivation_revision,
    )
    status = "current" if value is not None else "unavailable"
    return {
        "observation_id": observation_id,
        "record_kind": "derived",
        "metric_id": metric_id,
        "subject_id": subject_id,
        "name": name,
        "value": (
            value if record_type == "decimal-string"
            else (float(value) if value is not None else None)
        ),
        "type": record_type,
        "source_path": source_path,
        "unit": unit,
        "population": population,
        "denominator": denominator,
        "window": window,
        "observed_at": snapshot_collected_at,
        "observed_slot": None,
        "collected_at": snapshot_collected_at,
        "snapshot_collected_at": snapshot_collected_at,
        "source": source,
        "source_url": source_url if status == "current" else None,
        "collection_method": "reuse identified public observations; no new collection",
        "calculation_method": calculation_method,
        "freshness": "derived from identified public observations" if status == "current" else "unavailable",
        "status": status,
        "basis": basis,
        "quality": "deterministic",
        "caveat": caveat,
        "input_observation_ids": list(input_observation_ids),
        "output_path": f"observations[observation_id={json.dumps(observation_id)}]",
    }


def _public_fact_selector(
    fact: dict[str, Any], spec: dict[str, Any] | None, observation_id: str,
) -> str:
    selector = f"observations[observation_id={json.dumps(observation_id)}]"
    if spec is not None:
        selector += " <- " + ".".join(str(part) for part in spec["path"])
    return selector


def _public_fact_freshness(
    snapshot: dict[str, Any], fact: dict[str, Any], spec: dict[str, Any] | None,
) -> str:
    if fact.get("state") == "unavailable":
        return "unavailable"
    if fact.get("state") == "stale":
        return "stale"
    if spec is not None:
        node: Any = snapshot
        nodes = []
        for part in spec["path"][:-1]:
            if not isinstance(node, dict):
                break
            node = node.get(part)
            if not isinstance(node, dict):
                break
            nodes.append(node)
        for candidate in reversed(nodes):
            freshness = candidate.get("freshness")
            if isinstance(freshness, str) and freshness:
                return freshness
            if candidate.get("stale") is True or candidate.get("source_state") == "last_known_good":
                return "stale"
    return "current at collection"


def _public_fact_reason(
    snapshot: dict[str, Any], fact: dict[str, Any], spec: dict[str, Any] | None,
) -> str | None:
    if (fact.get("state") == "unavailable"
            and isinstance(fact.get("quality"), str) and fact["quality"]):
        return fact["quality"]
    if spec is None:
        return None
    node: Any = snapshot
    nodes = []
    for part in spec["path"][:-1]:
        if not isinstance(node, dict):
            break
        node = node.get(part)
        if not isinstance(node, dict):
            break
        nodes.append(node)
    for candidate in reversed(nodes):
        reason = candidate.get("reason")
        if isinstance(reason, str) and reason:
            return reason
    fallback = spec.get("unavailable_reason")
    if isinstance(fallback, str) and fallback:
        return fallback
    economics = snapshot.get("economics")
    if (spec["path"][:1] == ("economics",)
            and isinstance(economics, dict)
            and economics.get("available") is False):
        return ECONOMICS_PUBLICATION_HOLD
    return None


def _public_observation_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Add renderer-visible deterministic values without mutating evidence."""
    projected = dict(snapshot)
    validators = snapshot.get("validators")
    if isinstance(validators, dict):
        derived_validators = dict(validators)
        active = validators.get("active_count")
        delinquent = validators.get("delinquent_count")
        total = active + delinquent if _is_number(active) and _is_number(delinquent) else 0
        derived_validators["current_share_pct"] = 100 * active / total if total > 0 else None
        production = validators.get("block_production")
        rows = production.get("validators") if isinstance(production, dict) else None
        if isinstance(production, dict) and isinstance(rows, list):
            valid_rows = [row for row in rows if isinstance(row, dict)]
            unmatched = sum(row.get("vote_identity_matched") is False for row in valid_rows)
            matched = len(valid_rows) - unmatched
            produced = production.get("blocks_produced")
            leader = production.get("leader_slots")
            derived_validators["block_production"] = {
                **production,
                "identity_count": len(valid_rows),
                "matched_identity_count": matched,
                "unmatched_identity_count": unmatched,
                "vote_join_coverage_pct": 100 * matched / len(valid_rows) if valid_rows else None,
                "produced_pct": (
                    100 * produced / leader
                    if _is_number(produced) and _is_number(leader) and leader > 0 else None
                ),
            }
        projected["validators"] = derived_validators
    growth = snapshot.get("growth")
    equities = growth.get("tokenized_equities") if isinstance(growth, dict) else None
    coverage = equities.get("supply_coverage") if isinstance(equities, dict) else None
    if isinstance(growth, dict) and isinstance(equities, dict) and isinstance(coverage, dict):
        queried = coverage.get("queried_this_run_asset_count")
        successful = coverage.get("successful_this_run_asset_count")
        derived_coverage = {
            **coverage,
            "run_success_pct": (
                100 * successful / queried
                if _is_number(successful) and _is_number(queried) and queried > 0 else None
            ),
        }
        projected["growth"] = {
            **growth,
            "tokenized_equities": {**equities, "supply_coverage": derived_coverage},
        }
    return projected


def feature_activation_detail_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Public observations at the account-read grain, without new history series."""
    section = snapshot.get("feature_activation")
    if not isinstance(section, dict) or not isinstance(section.get("features"), list):
        return []
    source = section.get("source") or {}
    metadata = section.get("metadata") or {}
    observations = []
    for feature in section["features"]:
        if not isinstance(feature, dict):
            continue
        state = feature.get("state")
        usable = state in {"activated", "pending", "account_absent"}
        for metric, unit, value in (
            ("feature_activation_state", "account state", 1 if usable else None),
            ("feature_activated_at_slot", "slot", feature.get("activated_at_slot") if state == "activated" else None),
        ):
            observations.append({
                "metric_id": metric, "subject_id": feature.get("key"),
                "event_time": None, "event_slot": source.get("rpc_context_slot"),
                "collected_at": snapshot.get("collected_at"), "value": value,
                "unit": unit, "basis": "recorded", "state": "current" if value is not None else "unavailable",
                "source": "solana-rpc-getMultipleAccounts-finalized",
                "source_revision": metadata.get("source_revision"), "source_schema": snapshot.get("schema_version"),
                "coverage": {"title": feature.get("title"), "state": state, "address": feature.get("address")},
                "quality": feature.get("reason") or (
                    "Finalized feature-account state; account absence does not establish roadmap status. "
                    "The observation slot is the RPC context, not the activation slot or a wall-clock timestamp."
                ),
            })
    return dedupe_facts(observations)


def dune_activity_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Daily values keep their query day and cached execution age in the registry."""
    section = snapshot.get("dune")
    if not isinstance(section, dict):
        return []
    record = section if section.get("available") is True else section.get("last_known_good", section)
    if not isinstance(record, dict):
        return []
    aggregates = record.get("aggregates") or {}
    ended = _timestamp(record.get("execution_ended_at"))
    query_id = record.get("query_id") or section.get("query_id")
    source_url = f"https://dune.com/queries/{query_id}" if query_id else None
    observations = []
    for metric, label, key, day_key, unit, population, calculation in (
        ("dune_daily_non_vote_fee_payers", "Dune daily non-vote fee payers", "fee_payers_latest", "fee_payers_day", "fee payers",
         "distinct non-vote transaction fee payers covered by the registered query", "query COUNT(DISTINCT fee payer) over its UTC day"),
        ("dune_daily_dex_volume_usd", "Dune daily DEX trade-leg volume", "dex_volume_total_latest_usd", "dex_volume_total_day", "USD",
         "indexed DEX swap legs covered by the registered query", "query SUM(amount_usd); legitimate multi-hop legs remain separate"),
        ("dune_daily_xstocks_dex_volume_usd", "Dune daily xStock DEX trade-leg volume", "xstocks_dex_volume_latest_usd", "xstocks_dex_day", "USD",
         "DEX trade legs where either mint is in the pinned 107-mint xStock registry", "SUM(amount_usd) only when every matched leg has valid pricing; each OR-matched row counted once"),
        ("dune_daily_xstocks_dex_trade_legs", "Dune daily xStock DEX trade legs", "xstocks_dex_trade_legs", "xstocks_dex_day", "trade legs",
         "DEX trade legs where either mint is in the pinned 107-mint xStock registry", "COUNT(*) after one OR match per DEX trade leg"),
        ("dune_daily_xstocks_dex_priced_trade_legs", "Dune daily priced xStock DEX trade legs", "xstocks_dex_priced_trade_legs", "xstocks_dex_day", "trade legs",
         "matched xStock DEX trade legs with finite nonnegative amount_usd", "COUNT_IF(amount_usd is valid) over matched legs"),
        ("dune_daily_transaction_fees_sol", "Dune daily all-transaction fees", "transaction_fees_latest_sol", "transaction_fees_day", "SOL",
         "vote and non-vote Solana transactions indexed by gas_solana.fees", "SUM(tx_fee) for a complete UTC day; transaction fees only, not REV or Jito tips"),
    ):
        value = aggregates.get(key)
        value = value if _is_number(value) and value >= 0 else None
        day = aggregates.get(day_key)
        day = day[:10] if isinstance(day, str) and re.match(r"^\d{4}-\d{2}-\d{2}", day) else None
        complete = bool(day and ended and day < ended[:10])
        if value is None or day is None or ended is None:
            status = "unavailable"
        elif section.get("available") is not True or record.get("freshness") == "stale":
            status = "stale"
        else:
            status = "current" if complete else "partial"
        observations.append({
            "metric_id": metric, "subject_id": None, "event_time": _source_event_time(day), "event_slot": None,
            "collected_at": snapshot.get("collected_at"), "value": value, "unit": unit,
            "basis": "recorded", "state": status, "source": "dune-registered-activity-query",
            "source_revision": None, "source_schema": snapshot.get("schema_version"),
            "coverage": {"name": label, "population": population, "calculation": calculation,
                         "day": day, "complete_day": complete, "source_url": source_url,
                         "source_path": ("dune.aggregates." if record is section else "dune.last_known_good.aggregates.") + key,
                         "unavailable_reason": (aggregates.get("xstocks_dex_volume_reason")
                                                if metric == "dune_daily_xstocks_dex_volume_usd" else None)},
            "quality": (
                (coverage_reason if isinstance(coverage_reason := (
                    aggregates.get("xstocks_dex_volume_reason")
                    if metric == "dune_daily_xstocks_dex_volume_usd" else None
                ), str) and coverage_reason else "Dune daily aggregate unavailable in selected and archived source.")
                if value is None or day is None else
                "Completed UTC day; execution time is query provenance, not the measurement window."
                if complete else "Legacy partial UTC-day aggregate; not a completed daily total."
            ) + (" Cached previous execution; refresh unavailable." if status == "stale" else ""),
        })
    return dedupe_facts(observations)


def cluster_software_detail_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    section = snapshot.get("cluster_software")
    if not isinstance(section, dict) or section.get("available") is not True:
        return []
    return [{
        "metric_id": "cluster_software_version_nodes", "subject_id": row["version"],
        "event_time": section.get("observed_at"), "event_slot": None,
        "collected_at": snapshot.get("collected_at"), "value": row["node_count"],
        "unit": "nodes", "basis": "measured", "state": "current",
        "source": "solana-rpc-getClusterNodes", "source_revision": None,
        "source_schema": snapshot.get("schema_version"), "quality": None,
        "coverage": {"version": row["version"]},
    } for row in section.get("versions", [])]


def public_observation_records(
    snapshot: dict[str, Any], history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project canonical facts into explicit publication observation records."""
    observation_snapshot = _public_observation_snapshot(snapshot)
    snapshot_collected_at = _timestamp(snapshot.get("collected_at"))
    records = []
    public_facts = dedupe_facts([
        *snapshot_facts(observation_snapshot, PUBLIC_METRICS),
        *performance_sample_detail_facts(observation_snapshot),
        *validator_detail_facts(observation_snapshot),
        *block_production_detail_facts(source_snapshot(observation_snapshot, "block_production")),
        *source_availability_facts(observation_snapshot),
        *cluster_software_detail_facts(observation_snapshot),
        *feature_activation_detail_facts(source_snapshot(observation_snapshot, "feature_activation")),
        *dune_activity_facts(source_snapshot(observation_snapshot, "dune")),
    ])
    for fact in public_facts:
        spec = PUBLIC_METRICS.get(fact["metric_id"])
        metadata = _public_fact_metadata(fact)
        observed_at = fact.get("event_time")
        observed_slot = fact.get("event_slot")
        collected_at = _timestamp(fact.get("collected_at"))
        source_key = collection_source_key(tuple(str(metadata.get("source_path", "")).split(".")))
        schedule = snapshot.get("collection_schedule")
        clock = schedule.get(source_key) if isinstance(schedule, dict) else None
        source_time = _timestamp(clock.get("last_success_at")) if isinstance(clock, dict) else None
        if source_time is not None and fact.get("state") != "unavailable":
            collected_at = source_time
        required = (
            "name", "population", "denominator", "window", "collection_method",
            "calculation_method", "type", "source_path",
        )
        missing = [
            key for key in required
            if not isinstance(metadata.get(key), str) or not metadata[key]
        ]
        status = fact.get("state")
        source_url = metadata.get("source_url")
        if status == "unavailable":
            source_url = None
        elif not isinstance(source_url, str) or not source_url.startswith("https://"):
            source_url = None
            missing.append("source_url")
        value = metadata.get("value")
        unavailable_reason = _public_fact_reason(observation_snapshot, fact, spec)
        caveats = [
            value for value in (
                metadata.get("caveat"),
                unavailable_reason,
            ) if isinstance(value, str) and value
        ]
        if isinstance(clock, dict) and clock.get("state") != "fresh":
            caveats.append(
                "Scheduled reuse; original source collection time retained."
                if clock.get("state") == "reused" else
                "Latest source refresh failed; retained evidence is not a fresh observation."
            )
        if collected_at is None:
            missing.append("collected_at")
        if missing:
            value = None
            status = "unavailable"
            caveats.append(
                "Required public observation metadata is incomplete: "
                + ", ".join(sorted(set(missing))) + "."
            )
        if status == "unavailable":
            value = None
            if unavailable_reason is None:
                caveats.append(
                    "Value is unavailable in the selected snapshot; no source reason was recorded."
                )
        elif value is None and fact["metric_id"] != "simd_lifecycle_status":
            status = "unavailable"
            if unavailable_reason is None:
                caveats.append(
                    "Value is unavailable in the selected snapshot; no source reason was recorded."
                )
        observation_id = public_observation_id(
            record_kind="direct",
            metric_id=fact["metric_id"],
            subject_id=fact.get("subject_id"),
            snapshot_collected_at=snapshot_collected_at or "unavailable",
            observed_at=observed_at,
            observed_slot=observed_slot,
            source=fact["source"],
            source_revision=fact.get("source_revision"),
        )
        record = {
            "observation_id": observation_id,
            "record_kind": "direct",
            "metric_id": fact["metric_id"],
            "subject_id": fact.get("subject_id"),
            "name": metadata.get("name") or fact["metric_id"],
            "value": value,
            "type": metadata.get("type") or "not applicable",
            "source_path": metadata.get("source_path") or "not applicable",
            "unit": fact["unit"],
            "population": metadata.get("population") or "not applicable",
            "denominator": metadata.get("denominator") or "not applicable",
            "window": metadata.get("window") or "not applicable",
            "observed_at": observed_at,
            "observed_slot": observed_slot,
            "collected_at": collected_at,
            "snapshot_collected_at": snapshot_collected_at,
            "source": fact["source"],
            "source_url": source_url,
            "collection_method": metadata.get("collection_method") or "not applicable",
            "calculation_method": metadata.get("calculation_method") or "not applicable",
            "freshness": (
                "unavailable" if status == "unavailable"
                else ("reused at original source time" if isinstance(clock, dict) and clock.get("state") == "reused"
                      else "stale" if isinstance(clock, dict) and clock.get("state") == "failed"
                      else _public_fact_freshness(observation_snapshot, fact, spec))
            ),
            "status": status,
            "basis": fact.get("basis") or "not applicable",
            "quality": fact.get("quality") or "not applicable",
            "caveat": " ".join(dict.fromkeys(caveats)) or "not applicable",
            "input_observation_ids": [],
            "output_path": _public_fact_selector(fact, spec, observation_id),
        }
        records.append(record)

    direct_summaries = {
        record["metric_id"]: record for record in records
        if record["subject_id"] is None and record["metric_id"] in PUBLIC_METRICS
    }
    if not isinstance(snapshot_collected_at, str):
        raise FactConflictError("public derived observations require a snapshot timestamp")
    for metric_id, input_metric_ids in DERIVED_REPORT_METRIC_INPUTS.items():
        source_record = direct_summaries.get(metric_id)
        input_records = [direct_summaries.get(item) for item in input_metric_ids]
        if source_record is None or any(item is None for item in input_records):
            raise FactConflictError(
                f"public derived observation inputs are incomplete: {metric_id}"
            )
        records.remove(source_record)
        records.append(derived_public_observation_record(
            metric_id=metric_id,
            subject_id=snapshot_collected_at,
            name=source_record["name"],
            value=source_record["value"],
            unit=source_record["unit"],
            population=source_record["population"],
            denominator=source_record["denominator"],
            window=source_record["window"],
            snapshot_collected_at=snapshot_collected_at,
            source_path=source_record["source_path"],
            calculation_method=source_record["calculation_method"],
            input_observation_ids=[item["observation_id"] for item in input_records],
            basis="derived",
            caveat=source_record["caveat"],
            record_type=source_record["type"],
            source_url=(
                "https://github.com/ashtonships/solana-ecosystem-report/blob/main/facts.py"
            ),
        ))

    direct_metric_ids = set(PUBLIC_METRICS) - set(DERIVED_REPORT_METRIC_INPUTS)
    summary_ids = [
        record["metric_id"] for record in records
        if record["subject_id"] is None and record["metric_id"] in direct_metric_ids
    ]
    if len(summary_ids) != len(direct_metric_ids) or set(summary_ids) != direct_metric_ids:
        raise FactConflictError("public summary observation coverage is incomplete or duplicated")
    summaries = {
        record["metric_id"]: record for record in records
        if record["subject_id"] is None and record["metric_id"] in direct_metric_ids
    }
    derived_summary_ids = [
        record["metric_id"] for record in records
        if record["record_kind"] == "derived"
        and record["subject_id"] == snapshot_collected_at
        and record["metric_id"] in DERIVED_REPORT_METRIC_INPUTS
    ]
    if (len(derived_summary_ids) != len(DERIVED_REPORT_METRIC_INPUTS)
            or set(derived_summary_ids) != set(DERIVED_REPORT_METRIC_INPUTS)):
        raise FactConflictError("public derived summary coverage is incomplete or duplicated")
    summaries.update({
        record["metric_id"]: record for record in records
        if record["record_kind"] == "derived"
        and record["subject_id"] == snapshot_collected_at
        and record["metric_id"] in DERIVED_REPORT_METRIC_INPUTS
    })
    for metric_id in PUBLIC_METRICS:
        fact = fact_from_snapshot(observation_snapshot, metric_id)
        record = summaries[metric_id]
        if fact["state"] != "unavailable" and fact["value"] is not None:
            expected_value = _public_fact_metadata(fact)["value"]
            if record["status"] == "unavailable" or record["value"] != expected_value:
                raise FactConflictError(
                    f"public observation does not cover rendered metric {metric_id}"
                )
    growth = observation_snapshot.get("growth")
    selected = growth.get("selected_usd_stablecoins") if isinstance(growth, dict) else None
    if isinstance(selected, dict) and selected.get("available") is True:
        ordered_mints = [mint for _, _, mint in SELECTED_STABLECOIN_IDENTITIES]
        expected_subjects = {
            asset.get("mint") for asset in selected.get("assets", [])
            if isinstance(asset, dict) and asset.get("available") is True
        }
        supply_facts = {
            fact["subject_id"]: fact
            for fact in selected_usd_stablecoin_supply_facts(observation_snapshot)
        }
        supply_records = {
            record["subject_id"]: record for record in records
            if record["metric_id"] == SELECTED_STABLECOIN_METRIC_ID
        }
        if (expected_subjects != set(ordered_mints)
                or set(supply_facts) != expected_subjects
                or set(supply_records) != expected_subjects):
            raise FactConflictError("selected stablecoin supply observation coverage is incomplete")
        exact_supplies = {
            mint: _exact_selected_supply(supply_facts[mint]["coverage"])
            for mint in ordered_mints
        }
        if any(value is None for value in exact_supplies.values()):
            raise FactConflictError("selected stablecoin exact supplies are incomplete")
        total = sum((exact_supplies[mint][0] for mint in ordered_mints), Decimal(0))
        assets = {
            asset["mint"]: asset for asset in selected.get("assets", [])
            if isinstance(asset, dict) and isinstance(asset.get("mint"), str)
        }
        input_ids = [supply_records[mint]["observation_id"] for mint in ordered_mints]
        calculation_method = (
            "selected-stablecoin-share-v1: exact subject-mint supply / exact sum "
            "of ordered selected-mint supplies; input subject order: "
            + ", ".join(ordered_mints)
        )
        for symbol, _, mint in SELECTED_STABLECOIN_IDENTITIES:
            share = exact_supplies[mint][0] / total if total else None
            recorded_share = assets[mint].get("share_of_selected_total")
            expected_share = format(share, "f") if share is not None else None
            if recorded_share != expected_share:
                raise FactConflictError("selected stablecoin recorded share disagrees with exact supplies")
            records.append(derived_public_observation_record(
                metric_id=SELECTED_STABLECOIN_SHARE_METRIC_ID,
                subject_id=mint,
                name=f"{symbol} share of selected stablecoin total",
                value=expected_share,
                unit="ratio",
                population="one selected USD stablecoin mint",
                denominator="exact total supply of the complete selected four-mint list",
                window="point-in-time finalized token-supply observations",
                snapshot_collected_at=snapshot_collected_at or "",
                source_path="growth.selected_usd_stablecoins.assets[].share_of_selected_total",
                calculation_method=calculation_method,
                input_observation_ids=input_ids,
                record_type="decimal-string",
                caveat=(
                    SELECTED_STABLECOIN_LIMITATIONS if share is not None else
                    SELECTED_STABLECOIN_LIMITATIONS
                    + " Share is unavailable because the exact selected-list total is zero."
                ),
                source_url=(
                    "https://github.com/ashtonships/solana-ecosystem-report/blob/main/facts.py"
                ),
            ))
        share_records = [
            record for record in records
            if record["metric_id"] == SELECTED_STABLECOIN_SHARE_METRIC_ID
        ]
        if (len(share_records) != len(expected_subjects)
                or {record["subject_id"] for record in share_records} != expected_subjects
                or any(record["record_kind"] != "derived" for record in share_records)
                or any(record["input_observation_ids"] != input_ids for record in share_records)):
            raise FactConflictError("selected stablecoin share observation coverage is incomplete")
    if history is None:
        if len({record["observation_id"] for record in records}) != len(records):
            raise FactConflictError("public observation identities are duplicated")
        return records

    timestamps = [_timestamp(item.get("collected_at")) for item in history]
    if any(value is None for value in timestamps) or len(set(timestamps)) != len(timestamps):
        raise FactConflictError("public history must have unique valid collected_at timestamps")
    selected_at = _timestamp(snapshot.get("collected_at"))
    recent_sample_times = set(timestamps[-5:])
    previous_at = timestamps[-2] if len(timestamps) > 1 else None
    historical_metric_ids = {
        *METRICS, "network_healthy", "network_slot", "performance_samples_used",
        "latest_non_vote_tps",
    }
    for historical_snapshot, historical_at in zip(history, timestamps):
        if historical_at == selected_at:
            continue
        for record in public_observation_records(historical_snapshot):
            metric_id = record["metric_id"]
            keep = metric_id in historical_metric_ids
            keep = keep or (
                metric_id == "performance_sample_tps" and historical_at in recent_sample_times
            )
            keep = keep or (metric_id == "validator_commission_pct" and historical_at == previous_at)
            if keep:
                records.append(record)
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        existing = by_id.get(record["observation_id"])
        if existing is not None and existing != record:
            raise FactConflictError(f"conflicting public observation {record['observation_id']}")
        by_id[record["observation_id"]] = record
    return list(by_id.values())


def fact_identity(fact: dict[str, Any]) -> tuple[Any, ...]:
    fact = adapt_fact(fact)
    event_identity = (fact.get("event_time"), fact.get("event_slot"))
    if event_identity == (None, None):
        event_identity = (None, None) if fact.get("source_revision") else (fact.get("collected_at"), None)
    identity = (fact.get("metric_id"), fact.get("subject_id"), fact.get("source"),
                fact.get("source_revision"), *event_identity)
    if fact.get("source") in REVISIONABLE_SOURCES:
        # Provider benchmark rows come from a sliding 365-day window whose
        # providers may legitimately revise a historical value (late indexing,
        # reclassification, backfill). Key such facts on their semantic
        # payload hash so an exact rerun is idempotent while a revised
        # provider/date observation appends as a distinct revision; the old
        # revision is retained and public consumers select the newest.
        identity = identity + (_semantic_payload(fact).__hash__(),)
    return identity


def validate_fact(fact: dict[str, Any]) -> None:
    """Fail closed when a persisted fact does not meet the public contract."""
    if not isinstance(fact.get("metric_id"), str) or not fact["metric_id"]:
        raise FactConflictError("fact metric_id is missing")
    if fact.get("subject_id") is not None and not isinstance(fact["subject_id"], str):
        raise FactConflictError("fact subject_id is invalid")
    if _timestamp(fact.get("collected_at")) is None:
        raise FactConflictError("fact collected_at is not an aware timestamp")
    if fact.get("event_time") is not None and _source_event_time(fact["event_time"]) is None:
        raise FactConflictError("fact event_time is invalid")
    event_slot = fact.get("event_slot")
    if (event_slot is not None and (not isinstance(event_slot, int)
                                    or isinstance(event_slot, bool) or event_slot < 0)):
        raise FactConflictError("fact event_slot is invalid")
    value = fact.get("value")
    if (value is not None and (not isinstance(value, (int, float))
                               or isinstance(value, bool) or not math.isfinite(float(value)))):
        raise FactConflictError("fact value is invalid")
    if not isinstance(fact.get("unit"), str) or not fact["unit"]:
        raise FactConflictError("fact unit is missing")
    if fact.get("basis") not in VALID_BASES or fact.get("state") not in VALID_STATES:
        raise FactConflictError("fact basis or state is invalid")
    if not isinstance(fact.get("source"), str) or not fact["source"]:
        raise FactConflictError("fact source is missing")
    if (fact.get("source_revision") is not None
            and (not isinstance(fact["source_revision"], str) or not fact["source_revision"])):
        raise FactConflictError("fact source_revision is invalid")
    if fact.get("source_schema") is None:
        raise FactConflictError("fact source_schema is missing")
    if fact.get("coverage") is not None and not isinstance(fact["coverage"], dict):
        raise FactConflictError("fact coverage is invalid")
    if fact.get("quality") is not None and not isinstance(fact["quality"], str):
        raise FactConflictError("fact quality is invalid")
    coverage = fact.get("coverage")
    if fact.get("metric_id") == XSTOCK_METRIC_ID:
        numerator = coverage.get("coverage_numerator") if isinstance(coverage, dict) else None
        denominator = coverage.get("coverage_denominator") if isinstance(coverage, dict) else None
        reconstructed = {
            "symbol": coverage.get("symbol") if isinstance(coverage, dict) else None,
            "name": coverage.get("name") if isinstance(coverage, dict) else None,
            "slug": coverage.get("slug") if isinstance(coverage, dict) else None,
            "mint": coverage.get("mint") if isinstance(coverage, dict) else None,
            "supply": fact.get("value"),
            "supply_raw_amount": coverage.get("raw_amount") if isinstance(coverage, dict) else None,
            "supply_decimals": coverage.get("decimals") if isinstance(coverage, dict) else None,
            "supply_rpc_ui_amount": (
                coverage.get("rpc_ui_amount") if isinstance(coverage, dict) else None
            ),
            "supply_rpc_ui_amount_string": (
                coverage.get("rpc_ui_amount_string") if isinstance(coverage, dict) else None
            ),
            "supply_context_slot": (
                coverage.get("supply_context_slot") if isinstance(coverage, dict) else None
            ),
            "supply_rpc_api_version": (
                coverage.get("rpc_api_version") if isinstance(coverage, dict) else None
            ),
            "supply_collected_at": fact.get("collected_at"),
            "supply_age_seconds": 0,
            "supply_freshness": "fresh",
            "supply_fresh_max_age_seconds": XSTOCK_FRESH_SECONDS,
            "supply_unit": "token",
            "supply_source_id": "solana_getTokenSupply",
            "supply_source_method": "getTokenSupply(finalized)",
            "basis": "finalized on-chain token supply",
            "supply_multiplier_provenance": (
                coverage.get("multiplier_provenance") if isinstance(coverage, dict) else None
            ),
            "supply_account_provenance": (
                coverage.get("account_provenance") if isinstance(coverage, dict) else None
            ),
        }
        observed_at = _timestamp_unix(fact.get("collected_at"))
        validated = validate_xstock_supply_asset(reconstructed, observed_at)
        if (
            not isinstance(coverage, dict)
            or validated is None
            or fact.get("subject_id") != coverage.get("mint")
            or fact.get("event_time") is not None
            or fact.get("event_slot") != coverage.get("supply_context_slot")
            or fact.get("collected_at") != coverage.get("supply_collected_at")
            or fact.get("value") != validated["projection"]
            or fact.get("unit") != "token"
            or fact.get("basis") != "measured"
            or fact.get("state") != "current"
            or fact.get("source") != validated["source"]
            or fact.get("source_revision") is not None
            or fact.get("source_schema") not in (8, 9)
            or fact.get("quality") != validated["quality"]
            or coverage.get("registry_source_key") != XSTOCK_REGISTRY_SOURCE_KEY
            or coverage.get("registry_source_path") != XSTOCK_REGISTRY_SOURCE_PATH
            or coverage.get("registry_source_url") != XSTOCK_REGISTRY_SOURCE_URL
            or coverage.get("registry_source_revision") != XSTOCK_REGISTRY_SOURCE_REVISION
            or coverage.get("registry_source_license") != XSTOCK_REGISTRY_SOURCE_LICENSE
            or coverage.get("registry_provenance") != xstock_registry_provenance()
            or coverage.get("registry_complete") is not True
            or numerator != 1
            or denominator != XSTOCK_REGISTRY_EXPECTED_COUNT
            or coverage.get("coverage_label")
            != f"1/{XSTOCK_REGISTRY_EXPECTED_COUNT}"
            or coverage.get("coverage_basis") != XSTOCK_FACT_COVERAGE_BASIS
            or any(key in coverage for key in (
                "supply_age_seconds", "fresh_max_age_seconds", "observed_at_unix",
            ))
            or coverage.get("fact_contract") != XSTOCK_FACT_CONTRACT
            or coverage.get("value_contract") != XSTOCK_FACT_VALUE_CONTRACT
        ):
            raise FactConflictError("xStock per-mint fact contract is invalid")
    if fact.get("metric_id") == SELECTED_STABLECOIN_METRIC_ID:
        identity = next((
            item for item in SELECTED_STABLECOIN_IDENTITIES
            if item[2] == fact.get("subject_id")
        ), None)
        exact_supply = _exact_selected_supply(coverage)
        ui_amount = coverage.get("rpc_ui_amount_string") if isinstance(coverage, dict) else None
        try:
            ui_decimal = Decimal(ui_amount) if isinstance(ui_amount, str) else None
            expected_value = float(exact_supply[0]) if exact_supply is not None else None
        except (InvalidOperation, OverflowError, ValueError):
            ui_decimal = expected_value = None
        program_id = coverage.get("token_program_id") if isinstance(coverage, dict) else None
        account_slot = (
            coverage.get("account_rpc_context_slot") if isinstance(coverage, dict) else None
        )
        account_api_version = (
            coverage.get("account_rpc_api_version") if isinstance(coverage, dict) else None
        )
        numerator = coverage.get("coverage_numerator") if isinstance(coverage, dict) else None
        denominator = coverage.get("coverage_denominator") if isinstance(coverage, dict) else None
        if (
            not isinstance(coverage, dict)
            or identity is None
            or (coverage.get("symbol"), coverage.get("issuer"), coverage.get("mint"))
            != identity
            or fact.get("event_time") is not None
            or fact.get("event_slot") is None
            or fact.get("unit") != SELECTED_STABLECOIN_UNIT
            or fact.get("basis") != "measured"
            or fact.get("state") != "current"
            or fact.get("source") != SELECTED_STABLECOIN_SOURCE
            or fact.get("source_revision") is not None
            or fact.get("source_schema") not in (8, 9)
            or fact.get("quality") != SELECTED_STABLECOIN_FACT_QUALITY
            or exact_supply is None
            or expected_value is None
            or fact.get("value") != expected_value
            or ui_decimal is None or not ui_decimal.is_finite() or ui_decimal < 0
            or ui_decimal != exact_supply[0]
            or not isinstance(coverage.get("rpc_api_version"), str)
            or not coverage["rpc_api_version"]
            or coverage.get("account_source_method")
            != "getAccountInfo(finalized,jsonParsed)"
            or not isinstance(account_slot, int) or isinstance(account_slot, bool)
            or account_slot < 0
            or (account_api_version is not None
                and (not isinstance(account_api_version, str) or not account_api_version))
            or SELECTED_STABLECOIN_TOKEN_PROGRAMS.get(program_id)
            != coverage.get("token_program")
            or coverage.get("registry_source_key") != SELECTED_STABLECOIN_SOURCE_KEY
            or coverage.get("registry_source_path") != SELECTED_STABLECOIN_SOURCE_PATH
            or coverage.get("registry_source_url") != SELECTED_STABLECOIN_SOURCE_URL
            or coverage.get("registry_source_revision")
            != SELECTED_STABLECOIN_SOURCE_REVISION
            or coverage.get("registry_source_license")
            != SELECTED_STABLECOIN_SOURCE_LICENSE
            or denominator != len(SELECTED_STABLECOIN_IDENTITIES)
            or numerator != 1
            or coverage.get("coverage_label")
            != f"1/{len(SELECTED_STABLECOIN_IDENTITIES)}"
            or coverage.get("coverage_basis")
            != SELECTED_STABLECOIN_FACT_COVERAGE_BASIS
            or "selected_mint_count" in coverage
            or coverage.get("universe_coverage") != "unknown"
            or coverage.get("fact_contract") != SELECTED_STABLECOIN_FACT_CONTRACT
            or coverage.get("value_contract") != SELECTED_STABLECOIN_FACT_VALUE_CONTRACT
        ):
            raise FactConflictError("selected stablecoin fact contract is invalid")
    if (fact.get("metric_id") == "validator_commission_pct"
            and isinstance(coverage, dict)
            and coverage.get("fact_contract") == VALIDATOR_COMMISSION_FACT_CONTRACT):
        snapshot_slot = coverage.get("snapshot_slot")
        if (
            fact.get("source") != VALIDATOR_COMMISSION_SOURCE
            or fact.get("source_revision") is not None
            or fact.get("event_time") is not None
            or fact.get("event_slot") is not None
            or fact.get("unit") != "%"
            or fact.get("basis") != "measured"
            or fact.get("state") not in ("current", "unavailable")
            or not isinstance(fact.get("value"), (int, float))
            or isinstance(fact.get("value"), bool)
            or not 0 <= float(fact["value"]) <= 100
            or fact.get("quality") != VALIDATOR_COMMISSION_QUALITY
            or not isinstance(fact.get("source_schema"), int)
            or isinstance(fact.get("source_schema"), bool)
            or fact["source_schema"] < 7
            or not isinstance(fact.get("subject_id"), str)
            or not fact["subject_id"]
            or not isinstance(coverage.get("identity"), str)
            or not coverage["identity"]
            or coverage.get("vote_state") not in ("current", "delinquent")
            or snapshot_slot is not None
            and (not isinstance(snapshot_slot, int)
                 or isinstance(snapshot_slot, bool) or snapshot_slot < 0)
        ):
            raise FactConflictError("canonical commission fact contract is invalid")


def _semantic_payload(fact: dict[str, Any]) -> tuple[Any, ...]:
    coverage = json.dumps(fact.get("coverage"), sort_keys=True, separators=(",", ":"))
    return tuple(fact.get(key) for key in (
        "value", "unit", "basis", "state", "source_revision", "quality",
    )) + (coverage,)


def dedupe_facts(facts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw_fact in facts:
        fact = adapt_fact(raw_fact)
        validate_fact(fact)
        identity = fact_identity(fact)
        prior = by_identity.get(identity)
        if prior is not None and _semantic_payload(prior) != _semantic_payload(fact):
            raise FactConflictError(f"conflicting fact identity: {identity!r}")
        if prior is None or str(fact.get("collected_at") or "") < str(prior.get("collected_at") or ""):
            by_identity[identity] = fact
    return sorted(by_identity.values(), key=lambda fact: (
        str(fact.get("event_time") or fact.get("collected_at") or ""),
        str(fact.get("metric_id") or ""), str(fact.get("subject_id") or ""),
        fact.get("event_slot") if isinstance(fact.get("event_slot"), int) else -1,
        str(fact.get("collected_at") or ""),
    ))


def eligible(fact: dict[str, Any]) -> bool:
    try:
        fact = adapt_fact(fact)
        validate_fact(fact)
    except FactConflictError:
        return False
    value = fact.get("value")
    return (
        (fact.get("metric_id") != "validator_commission_pct"
         or (isinstance(fact.get("coverage"), dict)
             and fact["coverage"].get("fact_contract")
             == VALIDATOR_COMMISSION_FACT_CONTRACT))
        and fact.get("basis") in VALID_BASES
        and fact.get("state") in ("current", "partial")
        and isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def cadence_eligible(
    facts: Iterable[dict[str, Any]], minimum_seconds: int = MIN_PRIOR_SPACING_SECONDS,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    last: datetime | None = None
    ordered = sorted(dedupe_facts(facts),
                     key=lambda fact: str(fact.get("collected_at") or ""))
    for fact in ordered:
        if not eligible(fact):
            continue
        stamp = _timestamp(fact.get("event_time") or fact.get("collected_at"))
        if stamp is None:
            continue
        moment = datetime.fromisoformat(stamp)
        if last is None or (moment - last).total_seconds() >= minimum_seconds:
            selected.append(fact)
            last = moment
    return selected


def _read_facts_lines(path: Path) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if not path.exists():
        return existing
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise FactConflictError(f"invalid fact JSON at line {line_number}") from error
        if not isinstance(item, dict):
            raise FactConflictError(f"fact line {line_number} is not an object")
        existing.append(item)
    return existing


def jsonl_additions(
    path: Path, new_facts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return validated new identities without touching the filesystem."""
    existing = _read_facts_lines(path)
    known = {fact_identity(fact) for fact in dedupe_facts(existing)}
    combined = dedupe_facts([*existing, *new_facts])
    return [fact for fact in combined if fact_identity(fact) not in known]


def append_jsonl(path: Path, new_facts: Iterable[dict[str, Any]]) -> int:
    """Atomically merge new identities in canonical order after validating every conflict.

    New facts may carry event times older than rows already on disk (first
    collection of a history-bearing metric), so the merged file is rewritten
    in canonical order rather than appended after existing rows.
    """
    additions = jsonl_additions(path, new_facts)
    if not additions:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    combined = dedupe_facts([*_read_facts_lines(path), *new_facts])
    serialized = "".join(
        json.dumps(fact, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for fact in combined
    ).encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return len(additions)
