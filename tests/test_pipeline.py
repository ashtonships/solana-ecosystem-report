"""The Methods page names a real pipeline. These tests keep that claim true."""

import copy
import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect
import dune
import growth
import news
import pipeline


class TestPipelineContract(unittest.TestCase):
    def test_stage_names_match_the_methods_page(self):
        self.assertEqual(
            pipeline.STAGES,
            ("Sources", "Normalize", "Validate", "Publish", "Recheck"),
        )
        self.assertEqual(
            pipeline.EVIDENCE_LABELS,
            {
                "Measured": "direct record",
                "Sampled": "bounded evidence",
                "Unavailable": "never zero",
            },
        )
        for name in pipeline.STAGES:
            self.assertIn(name, pipeline.STAGE_CALLS)

    def test_validate_never_turns_missing_or_nan_into_zero(self):
        snapshot = {
            "performance": {"latest_tps": None, "mean_slot_time_secs": float("nan")},
            "activity": {"available": False, "fees": {"median_lamports": float("inf")}},
        }
        cleaned = pipeline.validate(snapshot)
        self.assertIsNone(cleaned["performance"]["latest_tps"])
        self.assertIsNone(cleaned["performance"]["mean_slot_time_secs"])
        self.assertIsNone(cleaned["activity"]["fees"]["median_lamports"])
        self.assertFalse(cleaned["activity"]["available"])
        self.assertEqual(cleaned["pipeline"]["stages"], list(pipeline.STAGES))

    def test_validate_keeps_finite_measurements(self):
        snapshot = {"performance": {"latest_tps": 4121.07}}
        cleaned = pipeline.validate(snapshot)
        self.assertEqual(cleaned["performance"]["latest_tps"], 4121.07)
        self.assertTrue(math.isfinite(cleaned["performance"]["latest_tps"]))

    def test_collect_exposes_named_source_and_normalize_stages(self):
        self.assertTrue(callable(collect.sources))
        self.assertTrue(callable(collect.normalize))
        self.assertEqual(collect.sources.__name__, "sources")
        self.assertEqual(collect.normalize.__name__, "normalize")


REFERENCE_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
SOURCE_COMMIT = "a" * 40
SELECTED_STABLECOIN_IDENTITIES = (
    ("USDC", "Circle", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
    ("USDT", "Tether", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
    ("PYUSD", "PayPal USD issued by Paxos",
     "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"),
    ("USDG", "Paxos Digital Singapore",
     "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"),
)
SELECTED_STABLECOIN_REGISTRY = {
    "source_key": "solana-foundation/solana-com:selected-usd-stablecoins",
    "url": (
        "https://github.com/solana-foundation/solana-com/blob/"
        "46091c373d7681a469e4130155187503def93387/"
        "apps/docs/content/docs/en/payments/production-readiness.mdx#L477-L480"
    ),
    "path": "apps/docs/content/docs/en/payments/production-readiness.mdx",
    "source_revision": "46091c373d7681a469e4130155187503def93387",
    "source_license": "GPL-3.0",
    "usage": "factual mint and issuer identifiers only",
}
SELECTED_STABLECOIN_LIMITATIONS = (
    "Exactly four selected USD stablecoin mints; broader universe coverage "
    "is unknown, so this does not represent all stablecoins. Total token "
    "supply is not circulating supply, USD value, liquidity, reserves, or "
    "executable depth. RPC context slots are retained; event time is unavailable."
)
XSTOCK_REGISTRY_REVISION = "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0"
XSTOCK_REGISTRY_PATH = "packages/asset-registry/src/data/xstock-variant-groups.ts"
XSTOCK_REGISTRY_URL = (
    "https://raw.githubusercontent.com/solana-foundation/tokens/"
    f"{XSTOCK_REGISTRY_REVISION}/{XSTOCK_REGISTRY_PATH}"
)
XSTOCK_REGISTRY_KEY = "solana-foundation/tokens:xstock-variant-groups"
XSTOCK_EXPECTED_COUNT = 107
DEX_VOLUME_URL = "https://api.dexscreener.com/tokens/v1/solana/"
DEX_VOLUME_SCOPE = "Solana DEX pools indexed by DEX Screener"
DEX_VOLUME_EXCLUSIONS = [
    "RFQ fills", "centralized venues", "unindexed or unsupported pools",
]


def xstock_registry_provenance():
    return {
        "repository": "https://github.com/solana-foundation/tokens",
        "path": XSTOCK_REGISTRY_PATH,
        "revision": XSTOCK_REGISTRY_REVISION,
        "license": "MIT",
        "selection": "address label exactly 'xStock'",
        "expected_unique_group_count": XSTOCK_EXPECTED_COUNT,
        "expected_unique_mint_count": XSTOCK_EXPECTED_COUNT,
    }


def xstock_asset(index, observed=2):
    asset = {
        "symbol": None,
        "name": f"Asset {index}",
        "slug": f"xstock-asset-{index:03d}",
        "mint": f"mint-{index:03d}",
        "supply": None,
        "supply_raw_amount": None,
        "supply_decimals": None,
        "supply_rpc_ui_amount": None,
        "supply_rpc_ui_amount_string": None,
        "supply_context_slot": None,
        "supply_rpc_api_version": None,
        "supply_collected_at": None,
        "supply_age_seconds": None,
        "supply_freshness": "unavailable",
        "supply_fresh_max_age_seconds": 21_600,
        "supply_unit": "token",
        "supply_source_id": "solana_getTokenSupply",
        "supply_source_method": "getTokenSupply(finalized)",
        "basis": "finalized on-chain token supply",
    }
    if index >= observed:
        return asset
    collected_at = (
        "2026-08-24T11:59:00+00:00" if index == 0
        else "2026-08-24T05:00:00+00:00"
    )
    age = 60 if index == 0 else 25_200
    ui_text = "1.25" if index == 0 else "2.5"
    asset.update({
        "supply": float(ui_text),
        "supply_raw_amount": str((index + 1) * 100_000_000),
        "supply_decimals": 8,
        "supply_rpc_ui_amount": float(ui_text),
        "supply_rpc_ui_amount_string": ui_text,
        "supply_context_slot": 321 + index,
        "supply_rpc_api_version": "2.3.7",
        "supply_collected_at": collected_at,
        "supply_age_seconds": age,
        "supply_freshness": "fresh" if age <= 21_600 else "stale",
        "supply_multiplier_provenance": {
            "source_method": "getAccountInfo(finalized,jsonParsed)",
            "program_id": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            "program": "spl-token-2022",
            "rpc_context_slot": 300 + index,
            "rpc_api_version": "2.3.6",
            "extension": "scaledUiAmountConfig",
            "state": {
                "authority": f"authority-{index}",
                "multiplier": "1.25",
                "newMultiplier": "1.5",
                "newMultiplierEffectiveTimestamp": 1_800_000_000,
            },
        },
    })
    return asset


def use_legacy_xstock_provenance(asset):
    asset.pop("supply_multiplier_provenance", None)
    asset["supply_account_provenance"] = {
        "source_method": "getAccountInfo(finalized,jsonParsed)",
        "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "program": "spl-token",
        "rpc_context_slot": 300,
        "rpc_api_version": "2.3.6",
    }


def xstock_candidate(observed=2, registry_complete=True):
    count = XSTOCK_EXPECTED_COUNT if registry_complete else 0
    all_assets = [xstock_asset(index, observed) for index in range(count)]
    observed = min(observed, count)
    fresh = 1 if observed else 0
    stale = max(0, observed - fresh)
    queried = observed
    successful = 1 if observed else 0
    failed = queried - successful
    coverage = {
        "registry_asset_count": count,
        "registry_complete": registry_complete,
        "eligible_asset_count": count,
        "queried_this_run_asset_count": queried,
        "successful_this_run_asset_count": successful,
        "failed_this_run_asset_count": failed,
        "attempt_scope": "current collection run",
        "observed_asset_count": observed,
        "fresh_asset_count": fresh,
        "valued_asset_count": 0,
        "coverage_numerator": observed,
        "coverage_denominator": count,
        "fresh_max_age_seconds": 21_600,
        "sweep_max_age_seconds": 259_200,
        "oldest_observation_at": (
            "2026-08-24T05:00:00+00:00" if observed > 1
            else ("2026-08-24T11:59:00+00:00" if observed else None)
        ),
        "newest_observation_at": "2026-08-24T11:59:00+00:00" if observed else None,
        "observation_span_seconds": 25_140 if observed > 1 else (0 if observed else None),
        "sweep_complete": bool(registry_complete and count and observed == count),
        "scope": "registry-wide" if observed == count and count else "observed subset",
        "coverage_basis": (
            "eligible assets with a valid supply observation no older than 72 hours"
        ),
    }
    equities = {
        "available": bool(observed),
        "registry_asset_count": count,
        "eligible_asset_count": count,
        "supply_observed_asset_count": observed,
        "fresh_supply_asset_count": fresh,
        "stale_supply_asset_count": stale,
        "supply_queried_this_run_asset_count": queried,
        "supply_successful_this_run_asset_count": successful,
        "supply_failed_this_run_asset_count": failed,
        "supply_deadline_exhausted": bool(count and queried < count),
        "valued_asset_count": 0,
        "observed_at_unix": 1_787_572_800,
        "supply_fresh_max_age_seconds": 21_600,
        "assets": all_assets[:12],
        "all_assets": all_assets,
        "displayed_asset_count": min(count, 12),
        "display_asset_limit": 12,
        "supply_coverage": coverage,
        "valuation": {
            "available": False,
            "scope": "unavailable",
            "reason": "No cleared price valuation source.",
        },
        "volume": {
            "available": False,
            "partial": True,
            "scope": DEX_VOLUME_SCOPE,
            "source_row_count": 0,
            "pair_count": 0,
            "exact_duplicate_row_count": 0,
            "conflicting_pair_count": 0,
            "invalid_row_count": 0,
            "unrelated_row_count": 0,
            "volume_covered_pair_count": 0,
            "volume_invalid_pair_count": 0,
            "liquidity_covered_pair_count": 0,
            "liquidity_invalid_pair_count": 0,
            "pairs_with_volume": 0,
            "assets_with_pairs": 0,
            "registry_asset_count": count,
            "batches_expected": 4 if count else 0,
            "batches_requested": 4 if count else 0,
            "batches_succeeded": 0,
            "transport_complete": False,
            "market_coverage": "not established",
            "exclusions": list(DEX_VOLUME_EXCLUSIONS),
            "source_url": DEX_VOLUME_URL,
            "limitations": (
                "Tracked DEX pools only; excludes RFQ fills, centralized venues, "
                "unindexed or unsupported pools. Transport was incomplete."
            ),
            "reason": "DEX Screener transport incomplete; market observations are unavailable.",
        },
        "proof_of_reserves": {"available": False, "scope": "unavailable"},
        "note": "Finalized supply evidence only; no heterogeneous aggregate.",
    }
    registry = {
        "url": XSTOCK_REGISTRY_URL,
        "kind": "pinned official token registry",
        "available": bool(count),
        "coverage_complete": registry_complete,
        "asset_count": count,
        "source_key": XSTOCK_REGISTRY_KEY,
        "source_revision": XSTOCK_REGISTRY_REVISION,
        "source_license": "MIT",
        "provenance": xstock_registry_provenance(),
        "reason": None if registry_complete else (
            "Pinned token registry transport or exact xStock identity contract failed."
        ),
    }
    supply = {
        "method": "getTokenSupply",
        "endpoint": "https://api.mainnet.solana.com",
        "rpc_endpoint_identity": growth.rpc_endpoint_identity(
            "https://api.mainnet.solana.com",
        ),
        "available": bool(observed),
        "commitment": "finalized",
        "state_version": growth.SUPPLY_STATE_VERSION,
        "deadline_exhausted": equities["supply_deadline_exhausted"],
        **copy.deepcopy(coverage),
    }
    return equities, registry, supply


def selected_stablecoin_candidate(observed=4):
    assets = []
    for index, (symbol, issuer, mint) in enumerate(SELECTED_STABLECOIN_IDENTITIES, 1):
        asset = {
            "symbol": symbol,
            "issuer": issuer,
            "mint": mint,
            "available": index <= observed,
        }
        if asset["available"]:
            asset.update({
                "total_supply_decimal": f"{index}.00",
                "raw_amount": str(index * 100),
                "decimals": 2,
                "rpc_ui_amount_string": str(index),
                "rpc_context_slot": 320 + index,
                "rpc_api_version": "2.3.7",
                "event_time": None,
                "collected_at": f"2026-08-24T11:59:0{index}+00:00",
                "basis": "finalized on-chain total token supply",
                "account_provenance": {
                    "source_method": "getAccountInfo(finalized,jsonParsed)",
                    "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "program": "spl-token",
                    "rpc_context_slot": 300 + index,
                    "rpc_api_version": "2.3.7",
                },
            })
        else:
            asset["reason"] = "Finalized validated mint supply is unavailable."
        assets.append(asset)
    state = "current" if observed == 4 else ("partial" if observed else "unavailable")
    summary = {
        "metric_id": "selected_usd_stablecoin_total_supply",
        "available": observed == 4,
        "state": state,
        "coverage_numerator": observed,
        "coverage_denominator": 4,
        "coverage_label": f"{observed}/4",
        "universe_coverage": "unknown",
        "unit": "selected stablecoin token units",
        "basis": "finalized on-chain total token supply",
        "assets": assets,
        "slot_range": {
            "first": 321 if observed else None,
            "last": 320 + observed if observed else None,
        },
        "oldest_observation_at": (
            "2026-08-24T11:59:01+00:00" if observed else None
        ),
        "newest_observation_at": (
            f"2026-08-24T11:59:0{observed}+00:00" if observed else None
        ),
        "registry_source": dict(SELECTED_STABLECOIN_REGISTRY),
        "limitations": SELECTED_STABLECOIN_LIMITATIONS,
        "queried_asset_count": 4,
        "failed_asset_count": 4 - observed,
        "deadline_exhausted": False,
        "rpc": {
            "endpoint": "https://api.mainnet.solana.com",
            "endpoint_identity": growth.rpc_endpoint_identity(
                "https://api.mainnet.solana.com",
            ),
            "methods": ["getAccountInfo", "getTokenSupply"],
            "commitment": "finalized",
        },
    }
    if observed == 4:
        summary["selected_total_supply_decimal"] = "10.00"
        for asset, share in zip(assets, ("0.1", "0.2", "0.3", "0.4")):
            asset["share_of_selected_total"] = share
    source = {
        "available": summary["available"],
        "partial": state == "partial",
        "coverage_complete": summary["available"],
        "state": state,
        "coverage_numerator": observed,
        "coverage_denominator": 4,
        "registry_source": summary["registry_source"],
        "rpc": summary["rpc"],
    }
    return summary, source


def news_candidate():
    """A complete schema-8 first-party news contract."""
    simd_source = (
        "https://github.com/solana-foundation/solana-improvement-documents/"
        f"blob/{SOURCE_COMMIT}/proposals/0326-alpenglow.md"
    )
    return {
        "available": True,
        "partial": False,
        "sources": {
            "agave_releases": {
                "available": True, "partial": False, "invalid_item_count": 0,
                "item_count": 1, "tag_commit_covered_count": 1,
                "tag_commit_missing_count": 0,
                "items": [{
                    "id": "github-release:1", "title": "v4.1.0",
                    "link": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0",
                    "published": "2026-08-03T12:41:49Z", "tag": "v4.1.0",
                    "draft": False, "prerelease": False, "stable": True,
                    "release_channel": "stable", "tag_commit_sha": "b" * 40,
                }],
            },
            "solana_news": {
                "available": True, "partial": False, "invalid_item_count": 0,
                "item_count": 1, "items": [{
                    "id": "solana-news:1", "title": "Update",
                    "link": "https://solana.com/news/update",
                    "published": "2026-08-24T10:00:00Z",
                }],
            },
            "simd_proposals": {
                "available": True, "partial": False, "coverage_complete": True,
                "source_commit": SOURCE_COMMIT,
                "url": (
                    "https://codeload.github.com/solana-foundation/"
                    f"solana-improvement-documents/tar.gz/{SOURCE_COMMIT}"
                ),
                "item_count": 1, "proposal_count": 1, "document_count": 1,
                "unparsed_paths": [],
                "items": [{
                    "id": "SIMD-0326", "title": "SIMD-0326: Alpenglow — Review",
                    "link": simd_source, "published": None, "created": "2025-07-25",
                    "status": "Review", "date_basis": "proposal_created",
                }],
                "proposals": [{
                    "identifier": "SIMD-0326", "name": "Alpenglow",
                    "status": "Review", "created": "2025-07-25",
                    "authors": ["Author"], "category": "Standard", "type": "Core",
                    "source": simd_source, "source_path": "proposals/0326-alpenglow.md",
                    "source_commit": SOURCE_COMMIT, "basis": "recorded",
                }],
                "lifecycle_note": "Explicit frontmatter from the pinned source commit.",
                "latest_published": None,
            },
            "network_status": {
                "available": True, "partial": False, "invalid_item_count": 0,
                "item_count": 0, "items": [],
            },
        },
        "current_status": {
            "available": True, "partial": False, "status_available": True,
            "incidents_available": True, "incident_response_available": True,
            "invalid_incident_count": 0, "indicator": "none",
            "description": "All Systems Operational", "incidents": [],
            "active_incident_count": 0, "incident_history": [],
            "observed_at_unix": 1_724_500_800,
            "sources": {
                "summary": "https://status.solana.com/api/v2/summary.json",
                "incidents": "https://status.solana.com/api/v2/incidents.json",
            },
        },
    }


def provider_history_candidate(metric_name):
    semantics = {
        "Active Addresses": (
            "stablecoin_active_address_provider_range",
            "Stablecoin active-address provider range",
            "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
        ),
        "Fee Payers": (
            "transaction_initiator_provider_range",
            "Transaction-initiator provider range",
            "provider observations of transaction initiators, not unique humans",
        ),
    }
    metric_id, display_name, scope = semantics[metric_name]
    observations = [
        {"date": "2026-08-21", "provider": provider, "value": value}
        for provider, value in (("A", 100), ("B", 200), ("C", 300))
    ]
    return {
        "available": True,
        "history_available": True,
        "partial": False,
        "canonical": False,
        "metric": metric_name,
        "semantic_metric_id": metric_id,
        "display_name": display_name,
        "source_label": metric_name,
        "scope": scope,
        "unit": "Count",
        "provider_observations": observations,
        "source_row_count": 3,
        "observed_row_count": 3,
        "observed_date_count": 1,
        "observed_provider_count": 3,
        "oldest_date": "2026-08-21",
        "newest_date": "2026-08-21",
        "invalid_row_count": 0,
        "exact_duplicate_row_count": 0,
        "conflicting_identity_count": 0,
        "conflicts": [],
        "date": "2026-08-21",
        "provider_count": 3,
        "minimum": 100,
        "maximum": 300,
    }


def publish_candidate(collected_at=None, **overrides):
    """A fresh minimal candidate that should pass every pre-publish check."""
    snapshot = {
        "schema_version": 7,
        "collected_at": REFERENCE_TIME.isoformat() if collected_at is None else collected_at,
        "provenance": {"source_revision": "a" * 40, "source_tree_dirty": False},
        "source": {
            "endpoint": "https://api.mainnet.solana.com",
            "endpoint_identity": growth.rpc_endpoint_identity(
                "https://api.mainnet.solana.com",
            ),
            "requires_api_key": False,
        },
        "network": {"healthy": True},
        "epoch": {"available": True, "epoch": 800},
        "performance": {"available": False},
        "supply": {"available": False},
        "inflation": {"available": False},
        "validators": {"available": False},
        "economics": {"available": False},
        "activity": {"available": False},
        "news": {"available": False},
        "growth": {"available": False},
    }
    snapshot.update(overrides)
    return snapshot


def release_held_economics_candidate():
    """Valid private-research economics that the public gate must reject."""
    return {
        "available": True,
        "stablecoins": {
            "available": True,
            "metric": "USD-pegged circulating supply",
            "provider_field": "totalCirculatingUSD.peggedUSD",
            "scope": "Provider-reported USD-pegged circulating supply on Solana.",
            "usd_pegged_circulating_usd": 15_000_000_000.0,
        },
        "protocols": {
            "available": True,
            "eligible_protocol_count": 2,
            "excluded_child_protocol_count": 1,
            "protocols": [
                {
                    "provider_protocol_id": "parent#family",
                    "provider_family_id": "parent#family",
                    "ranking_basis": "provider_parent_aggregate",
                    "solana_tvl_usd": 700.0,
                },
                {
                    "provider_protocol_id": "solo",
                    "provider_family_id": "solo",
                    "ranking_basis": "provider_standalone",
                    "solana_tvl_usd": 600.0,
                },
            ],
        },
    }


def semantic_candidate(**overrides):
    """A compact candidate with enough nested data to exercise invariants."""
    xstock_equities, xstock_registry, xstock_supply = xstock_candidate()
    snapshot = publish_candidate(
        schema_version=8,
        epoch={
            "available": True,
            "epoch": 800,
            "slot_index": 25,
            "slots_in_epoch": 100,
            "progress_pct": 25.0,
            "remaining_slots": 75,
            "estimated_end_at": "2026-08-24T13:00:00+00:00",
        },
        performance={
            "available": True,
            "samples_used": 2,
            "sample_period_seconds": 120,
            "latest_tps": 10.0,
            "samples": [
                {
                    "slot": 200,
                    "transactions": 600,
                    "non_vote_transactions": 240,
                    "sample_period_secs": 60,
                    "tps": 10.0,
                    "non_vote_tps": 4.0,
                    "vote_share_pct": 60.0,
                    "slot_time_secs": 0.4,
                },
                {
                    "slot": 100,
                    "transactions": 480,
                    "non_vote_transactions": 180,
                    "sample_period_secs": 60,
                    "tps": 8.0,
                    "non_vote_tps": 3.0,
                    "vote_share_pct": 62.5,
                    "slot_time_secs": 0.42,
                },
            ],
        },
        supply={
            "available": True,
            "total_sol": 100.0,
            "circulating_sol": 80.0,
            "non_circulating_sol": 20.0,
            "circulating_pct": 80.0,
        },
        validators={
            "available": True,
            "active_count": 1,
            "delinquent_count": 1,
            "accounts_with_stake": 2,
            "all_validator_count": 2,
            "ranked_validator_count": 1,
            "ranked_validator_limit": 1,
            "all_validators": [
                {"identity": "identity-a", "vote_account": "vote-a", "state": "current"},
                {"identity": "identity-b", "vote_account": "vote-b", "state": "delinquent"},
            ],
            "ranked_validators": [
                {"identity": "identity-a", "vote_account": "vote-a", "state": "current"},
            ],
            "block_production": {
                "available": True,
                "basis": "most recent fully completed epoch",
                "epoch": 799,
                "first_slot": 10,
                "last_slot": 19,
                "context_slot": 20,
                "api_version": "2.3.4",
                "leader_slots": 10,
                "blocks_produced": 8,
                "skipped_slots": 2,
                "skip_rate": 0.2,
                "skip_rate_definition": "skipped_slots / leader_slots",
                "vote_enrichment_observed_at": REFERENCE_TIME.isoformat(),
                "source": {"method": "getBlockProduction", "commitment": "finalized"},
                "collection": {
                    "mode": "contiguous_chunks",
                    "request_count": 1,
                    "chunk_slot_limit": 10,
                    "first_slot": 10,
                    "last_slot": 19,
                    "coverage_numerator_slots": 10,
                    "coverage_denominator_slots": 10,
                    "coverage_complete": True,
                    "context_slot_min": 20,
                    "context_slot_max": 20,
                },
                "validators": [
                    {
                        "identity": "identity-a",
                        "leader_slots": 6,
                        "blocks_produced": 5,
                        "skipped_slots": 1,
                        "skip_rate": round(1 / 6, 8),
                        "vote_identity_matched": True,
                        "vote_account_count": 1,
                        "vote_accounts": [{
                            "vote_pubkey": "vote-a",
                            "state": "current",
                            "activated_stake_lamports": 100,
                        }],
                        "activated_stake_lamports": 100,
                    },
                    {
                        "identity": "identity-b",
                        "leader_slots": 4,
                        "blocks_produced": 3,
                        "skipped_slots": 1,
                        "skip_rate": 0.25,
                        "vote_identity_matched": False,
                        "vote_account_count": 0,
                        "vote_accounts": [],
                        "activated_stake_lamports": None,
                    },
                ],
            },
        },
        activity={
            "available": True,
            "requires_api_key": False,
            "source": {
                "endpoint": "https://api.mainnet.solana.com",
                "endpoint_identity": growth.rpc_endpoint_identity(
                    "https://api.mainnet.solana.com",
                ),
                "method": "getBlock (transactionDetails=accounts)",
            },
            "source_state": "fresh",
            "stale": False,
            "last_success_at": REFERENCE_TIME.isoformat(),
            "window": {
                "first_slot": 10,
                "last_slot": 20,
                "blocks_requested": 4,
                "blocks_sampled": 3,
                "first_block_time": 1_725_188_400,
                "last_block_time": 1_725_190_200,
                "observed_seconds": 1800,
            },
            "rev": {
                "available": True,
                "sampled_sol": {
                    "transaction_fees": 0.03,
                    "message_signature_base_fee_lower_bound": 0.01,
                    "unclassified_fee_residual": 0.02,
                    "jito_tips": 0.005,
                    "total": 0.035,
                },
                "sample_mean_estimate_sol": 1.2,
                "estimated_blocks_in_window": 100,
                "estimate_window_seconds": 1800,
            },
        },
        economics={"available": False},
        news=news_candidate(),
        growth={
            "available": True,
            "daily_active_addresses": provider_history_candidate("Active Addresses"),
            "daily_fee_payers": provider_history_candidate("Fee Payers"),
            "tokenized_equities": xstock_equities,
            "sources": {
                "registry": xstock_registry,
                "supply": xstock_supply,
                "activity_benchmark": {
                    "available": True,
                    "active_addresses_available": True,
                    "fee_payers_available": True,
                    "active_addresses_history_available": True,
                    "fee_payers_history_available": True,
                    "active_addresses_observed_row_count": 3,
                    "fee_payers_observed_row_count": 3,
                    "partial": False,
                    "canonical": False,
                },
                "dex_volume": {
                    "url": DEX_VOLUME_URL,
                    "available": False,
                    "partial": True,
                    "scope": DEX_VOLUME_SCOPE,
                    "transport_complete": False,
                    "market_coverage": "not established",
                    "exclusions": list(DEX_VOLUME_EXCLUSIONS),
                },
            },
        },
    )
    snapshot.update(overrides)
    return snapshot


def enable_complete_dex_volume(candidate):
    """Replace the shared unavailable envelope with one complete derived aggregate."""
    volume = candidate["growth"]["tokenized_equities"]["volume"]
    volume.update({
        "available": True,
        "partial": True,
        "source_row_count": 3,
        "pair_count": 2,
        "exact_duplicate_row_count": 1,
        "conflicting_pair_count": 0,
        "invalid_row_count": 0,
        "unrelated_row_count": 0,
        "volume_covered_pair_count": 2,
        "volume_invalid_pair_count": 0,
        "liquidity_covered_pair_count": 2,
        "liquidity_invalid_pair_count": 0,
        "pairs_with_volume": 2,
        "assets_with_pairs": 2,
        "batches_requested": 4,
        "batches_succeeded": 4,
        "transport_complete": True,
        "market_coverage": "partial",
        "limitations": (
            "Tracked DEX pools only; excludes RFQ fills, centralized venues, "
            "unindexed or unsupported pools."
        ),
        "volume_24h_usd": 12.5,
        "liquidity_usd": 20.0,
    })
    volume.pop("reason", None)
    source = candidate["growth"]["sources"]["dex_volume"]
    source.update({
        "available": True,
        "partial": True,
        "transport_complete": True,
        "market_coverage": "partial",
    })


class TestPublishGate(unittest.TestCase):
    """The scheduled update fails closed: nothing deploys from a bad candidate."""

    def gate_result(self, snapshot, max_age_seconds=21600):
        return pipeline.check_publishable(
            snapshot, now=REFERENCE_TIME, max_age_seconds=max_age_seconds,
        )

    def failure_checks(self, result):
        return [failure["check"] for failure in result["failures"]]

    def test_fresh_minimal_candidate_passes(self):
        result = self.gate_result(publish_candidate())
        self.assertTrue(result["publishable"], result)
        self.assertEqual(result["failures"], [])

    def test_schema_eight_requires_exact_collection_revision_provenance(self):
        for provenance in (
            None,
            {"source_revision": "short", "source_tree_dirty": False},
            {"source_revision": "a" * 40, "source_tree_dirty": "no"},
        ):
            result = self.gate_result(semantic_candidate(provenance=provenance))
            self.assertFalse(result["publishable"])
            self.assertTrue(any("provenance" in failure["detail"]
                                for failure in result["failures"]))

    def test_unsupported_schema_version_fails(self):
        for version in (99, 4, "7", None):
            result = self.gate_result(publish_candidate(schema_version=version))
            self.assertFalse(result["publishable"], version)
            self.assertIn("schema_version", self.failure_checks(result))

    def test_missing_or_malformed_collected_at_fails(self):
        candidate = publish_candidate()
        del candidate["collected_at"]
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"])
        self.assertIn("collected_at", self.failure_checks(result))
        malformed = self.gate_result(
            publish_candidate(collected_at="24/08/2026 noon UTC"))
        self.assertFalse(malformed["publishable"])
        self.assertIn("collected_at", self.failure_checks(malformed))

    def test_naive_timestamp_fails(self):
        naive = "2026-08-24T11:59:00"
        result = self.gate_result(publish_candidate(collected_at=naive))
        self.assertFalse(result["publishable"])
        self.assertIn("collected_at", self.failure_checks(result))

    def test_future_timestamp_fails(self):
        future = (REFERENCE_TIME + timedelta(hours=1)).isoformat()
        result = self.gate_result(publish_candidate(collected_at=future))
        self.assertFalse(result["publishable"])
        self.assertIn("collected_at", self.failure_checks(result))

    def test_stale_timestamp_fails_and_boundary_holds(self):
        just_within = (REFERENCE_TIME - timedelta(seconds=21600)).isoformat()
        within = self.gate_result(publish_candidate(collected_at=just_within))
        self.assertTrue(within["publishable"], within)
        one_second_late = (REFERENCE_TIME - timedelta(seconds=21601)).isoformat()
        stale = self.gate_result(publish_candidate(collected_at=one_second_late))
        self.assertFalse(stale["publishable"])
        self.assertIn("collected_at", self.failure_checks(stale))

    def test_non_finite_core_values_fail(self):
        result = self.gate_result(publish_candidate(
            performance={"available": True, "latest_tps": float("nan")},
            supply={"available": True, "total_sol": float("inf")},
        ))
        self.assertFalse(result["publishable"])
        self.assertIn("finite_core_values", self.failure_checks(result))

    def test_missing_required_section_fails(self):
        candidate = publish_candidate()
        del candidate["supply"]
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"])
        self.assertIn("structure", self.failure_checks(result))

    def test_optional_unavailable_sources_never_block_publication(self):
        candidate = publish_candidate(activity={
            "available": True,
            "source_state": "last_known_good",
            "stale": True,
            "last_success_at": "2026-08-23T12:00:00+00:00",
        })
        result = self.gate_result(candidate)
        self.assertTrue(result["publishable"], result)
        self.assertEqual(result["failures"], [])

    def test_no_available_core_section_fails(self):
        result = self.gate_result(publish_candidate(epoch={"available": False}))
        self.assertFalse(result["publishable"])
        self.assertIn("core_coverage", self.failure_checks(result))

    def test_stale_or_carried_core_sections_do_not_satisfy_coverage(self):
        # Every core section retains a number and even claims availability,
        # but none is a live reading: coverage must not be satisfied.
        carried = {"available": True, "stale": True, "source_state": "last_known_good"}
        candidate = publish_candidate(
            epoch={"available": True, "stale": True, "epoch": 800},
            performance={"available": True, "freshness": "stale"},
            supply=carried,
            inflation=dict(carried),
            validators={"available": True, "source_state": "last_known_good"},
        )
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"])
        self.assertIn("core_coverage", self.failure_checks(result))

    def test_semantically_valid_nested_candidate_passes(self):
        result = self.gate_result(semantic_candidate())
        self.assertTrue(result["publishable"], result)

    def test_dune_transaction_fees_only_passes_the_full_publish_gate(self):
        rows = [{
            "metric_id": "daily_transaction_fees_sol", "day": "2026-08-23",
            "dimension": None, "value": 400.5, "unit": "sol", "sample_count": 100,
        }]
        section = dune._success_section(
            "8590950", "https://dune.com/queries/8590950", dune.SOURCE_URL,
            {
                "state": "QUERY_STATE_COMPLETED", "execution_id": "fees-only",
                "result": {
                    "execution_started_at": "2026-08-24T11:58:00+00:00",
                    "execution_ended_at": "2026-08-24T11:59:00+00:00",
                    "row_count": 1, "datapoint_count": 6, "rows": rows,
                },
            },
            "fresh", REFERENCE_TIME,
        )
        result = self.gate_result(semantic_candidate(dune=section))
        self.assertTrue(result["publishable"], result)

    def test_dune_xstock_counts_only_passes_the_full_publish_gate(self):
        rows = [
            {
                "metric_id": "daily_xstocks_dex_trade_legs", "day": "2026-08-23",
                "dimension": dune.XSTOCK_DIMENSION, "value": 2,
                "unit": "trade_legs", "sample_count": 2,
            },
            {
                "metric_id": "daily_xstocks_dex_priced_trade_legs", "day": "2026-08-23",
                "dimension": dune.XSTOCK_DIMENSION, "value": 1,
                "unit": "trade_legs", "sample_count": 2,
            },
        ]
        section = dune._success_section(
            "8590950", "https://dune.com/queries/8590950", dune.SOURCE_URL,
            {
                "state": "QUERY_STATE_COMPLETED", "execution_id": "xstocks-counts-only",
                "result": {
                    "execution_started_at": "2026-08-24T11:58:00+00:00",
                    "execution_ended_at": "2026-08-24T11:59:00+00:00",
                    "row_count": 2, "datapoint_count": 12, "rows": rows,
                },
            },
            "fresh", REFERENCE_TIME,
        )
        result = self.gate_result(semantic_candidate(dune=section))
        self.assertTrue(result["publishable"], result)

    def test_release_held_economics_fail_closed_outside_private_dry_run(self):
        candidate = semantic_candidate(economics=release_held_economics_candidate())

        public = self.gate_result(candidate)
        self.assertFalse(public["publishable"], public)
        self.assertIn("release_policy", self.failure_checks(public))

        private = pipeline.check_publishable(
            candidate,
            now=REFERENCE_TIME,
            max_age_seconds=21600,
            allow_release_held=True,
        )
        self.assertTrue(private["publishable"], private)

    def test_schema8_selected_stablecoin_contract_is_optional_but_valid_when_present(self):
        candidate = semantic_candidate()
        summary, source = selected_stablecoin_candidate()
        candidate["growth"]["selected_usd_stablecoins"] = summary
        candidate["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertTrue(self.gate_result(candidate)["publishable"])

        partial = semantic_candidate()
        summary, source = selected_stablecoin_candidate(3)
        partial["growth"]["selected_usd_stablecoins"] = summary
        partial["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertTrue(self.gate_result(partial)["publishable"])

        unavailable = semantic_candidate()
        summary, source = selected_stablecoin_candidate(0)
        unavailable["growth"]["selected_usd_stablecoins"] = summary
        unavailable["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertTrue(self.gate_result(unavailable)["publishable"])

        # Schema 8 predates this additive field; absence on both sides remains valid.
        self.assertTrue(self.gate_result(semantic_candidate())["publishable"])

    def test_schema8_invalid_selected_stablecoin_coverage_fails_without_crashing(self):
        candidate = semantic_candidate()
        equities, registry, supply = xstock_candidate(
            observed=0, registry_complete=False,
        )
        candidate["growth"]["tokenized_equities"] = equities
        candidate["growth"]["sources"]["registry"] = registry
        candidate["growth"]["sources"]["supply"] = supply
        summary, source = selected_stablecoin_candidate()
        summary["coverage_numerator"] = "four"
        candidate["growth"]["selected_usd_stablecoins"] = summary
        candidate["growth"]["sources"]["selected_usd_stablecoins"] = source

        result = self.gate_result(candidate)

        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_selected_stablecoin_identity_and_scope_fail_closed(self):
        mutations = (
            ("metric", ("metric_id",), "circulating_stablecoin_supply"),
            ("unit", ("unit",), "USD"),
            ("basis", ("basis",), "circulating supply"),
            ("universe", ("universe_coverage",), "complete"),
            ("denominator", ("coverage_denominator",), 5),
            ("label", ("coverage_label",), "4/all"),
            ("limitations", ("limitations",), "Stablecoin composition."),
            ("symbol", ("assets", 0, "symbol"), "USDC.e"),
            ("issuer", ("assets", 1, "issuer"), "Unknown"),
            ("mint", ("assets", 2, "mint"), SELECTED_STABLECOIN_IDENTITIES[0][2]),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                summary, source = selected_stablecoin_candidate()
                target = summary
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                candidate["growth"]["selected_usd_stablecoins"] = summary
                candidate["growth"]["sources"]["selected_usd_stablecoins"] = source
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_selected_stablecoin_exact_supply_and_time_fail_closed(self):
        mutations = (
            ("raw format", ("assets", 0, "raw_amount"), "1.00"),
            ("raw u64", ("assets", 0, "raw_amount"), str(2**64)),
            ("decimals", ("assets", 0, "decimals"), -1),
            ("reconstruction", ("assets", 0, "total_supply_decimal"), "1.01"),
            ("ui decimal", ("assets", 0, "rpc_ui_amount_string"), "NaN"),
            ("ui mismatch", ("assets", 0, "rpc_ui_amount_string"), "1.01"),
            ("slot", ("assets", 0, "rpc_context_slot"), -1),
            ("event time", ("assets", 0, "event_time"), "2026-08-24T11:59:01Z"),
            ("naive collection time", ("assets", 0, "collected_at"),
             "2026-08-24T11:59:01"),
            ("future collection time", ("assets", 0, "collected_at"),
             "2026-08-24T12:00:01+00:00"),
            ("account method", ("assets", 0, "account_provenance", "source_method"),
             "getTokenSupply"),
            ("account program", ("assets", 0, "account_provenance", "program"),
             "spl-token-2022"),
            ("slot lower bound", ("slot_range", "first"), 320),
            ("oldest bound", ("oldest_observation_at",),
             "2026-08-24T11:59:02+00:00"),
            ("total", ("selected_total_supply_decimal",), "9.99"),
            ("share", ("assets", 3, "share_of_selected_total"), "0.5"),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                summary, source = selected_stablecoin_candidate()
                target = summary
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                candidate["growth"]["selected_usd_stablecoins"] = summary
                candidate["growth"]["sources"]["selected_usd_stablecoins"] = source
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_selected_stablecoin_state_and_source_mirror_fail_closed(self):
        mutations = (
            ("available", ("available",), False),
            ("state", ("state",), "partial"),
            ("numerator", ("coverage_numerator",), 3),
            ("queried", ("queried_asset_count",), 3),
            ("failed", ("failed_asset_count",), 1),
            ("deadline", ("deadline_exhausted",), True),
            ("rpc method", ("rpc", "methods"), ["getTokenSupply"]),
            ("rpc commitment", ("rpc", "commitment"), "confirmed"),
            ("rpc endpoint", ("rpc", "endpoint"), "https://rpc.other.example"),
            ("registry revision", ("registry_source", "source_revision"), "b" * 40),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                summary, source = selected_stablecoin_candidate()
                target = summary
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                candidate["growth"]["selected_usd_stablecoins"] = summary
                candidate["growth"]["sources"]["selected_usd_stablecoins"] = source
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        partial = semantic_candidate()
        summary, source = selected_stablecoin_candidate(3)
        summary["selected_total_supply_decimal"] = "6.00"
        summary["assets"][0]["share_of_selected_total"] = "0.1666666666666666666666666667"
        partial["growth"]["selected_usd_stablecoins"] = summary
        partial["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertFalse(self.gate_result(partial)["publishable"])

        mismatched = semantic_candidate()
        summary, source = selected_stablecoin_candidate()
        source["coverage_numerator"] = 3
        mismatched["growth"]["selected_usd_stablecoins"] = summary
        mismatched["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertFalse(self.gate_result(mismatched)["publishable"])

        missing_source = semantic_candidate()
        summary, _ = selected_stablecoin_candidate()
        missing_source["growth"]["selected_usd_stablecoins"] = summary
        self.assertFalse(self.gate_result(missing_source)["publishable"])

        orphan_source = semantic_candidate()
        _, source = selected_stablecoin_candidate()
        orphan_source["growth"]["sources"]["selected_usd_stablecoins"] = source
        self.assertFalse(self.gate_result(orphan_source)["publishable"])

    def test_schema8_xstocks_validate_available_and_registry_only_evidence(self):
        self.assertTrue(self.gate_result(semantic_candidate())["publishable"])

        registry_only = semantic_candidate()
        equities, registry, supply = xstock_candidate(observed=0)
        registry_only["growth"]["tokenized_equities"] = equities
        registry_only["growth"]["sources"]["registry"] = registry
        registry_only["growth"]["sources"]["supply"] = supply
        self.assertTrue(self.gate_result(registry_only)["publishable"])

        missing_coverage = copy.deepcopy(registry_only)
        del missing_coverage["growth"]["tokenized_equities"]["supply_coverage"]
        result = self.gate_result(missing_coverage)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        legacy = semantic_candidate()
        legacy["growth"]["tokenized_equities"] = {"available": False}
        result = self.gate_result(legacy)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_xstocks_accept_one_strict_legacy_row_with_token_2022_rows(self):
        candidate = semantic_candidate()
        equities, registry, supply = xstock_candidate(observed=XSTOCK_EXPECTED_COUNT)
        use_legacy_xstock_provenance(equities["all_assets"][0])
        candidate["growth"]["tokenized_equities"] = equities
        candidate["growth"]["sources"]["registry"] = registry
        candidate["growth"]["sources"]["supply"] = supply

        result = self.gate_result(candidate)

        self.assertTrue(result["publishable"], result)
        self.assertEqual(
            equities["supply_coverage"]["coverage_numerator"],
            XSTOCK_EXPECTED_COUNT,
        )
        self.assertEqual(
            equities["supply_coverage"]["coverage_denominator"],
            XSTOCK_EXPECTED_COUNT,
        )

    def test_schema8_xstock_legacy_supply_provenance_fails_closed(self):
        mutations = (
            ("wrong token program id", ("program_id",), "wrong-program-id"),
            ("wrong parsed program", ("program",), "spl-token-2022"),
            ("wrong account method", ("source_method",), "getAccountInfo"),
            ("future account slot", ("rpc_context_slot",), 999),
            ("missing account api", ("rpc_api_version",), ""),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                asset = candidate["growth"]["tokenized_equities"]["all_assets"][0]
                use_legacy_xstock_provenance(asset)
                asset["supply_account_provenance"][path[0]] = value

                result = self.gate_result(candidate)

                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        mixed = semantic_candidate()
        mixed_asset = mixed["growth"]["tokenized_equities"]["all_assets"][0]
        scaled = copy.deepcopy(mixed_asset["supply_multiplier_provenance"])
        use_legacy_xstock_provenance(mixed_asset)
        mixed_asset["supply_multiplier_provenance"] = scaled

        result = self.gate_result(mixed)

        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_accepts_detailed_unavailable_and_complete_aggregate(self):
        self.assertTrue(self.gate_result(semantic_candidate())["publishable"])

        available = semantic_candidate()
        enable_complete_dex_volume(available)
        self.assertTrue(self.gate_result(available)["publishable"])

    def test_schema8_dex_volume_counts_are_nonnegative_integers_without_raising(self):
        count_keys = (
            "batches_expected", "batches_requested", "batches_succeeded",
            "source_row_count", "pair_count", "exact_duplicate_row_count",
            "conflicting_pair_count", "invalid_row_count", "unrelated_row_count",
            "volume_covered_pair_count", "volume_invalid_pair_count",
            "liquidity_covered_pair_count", "liquidity_invalid_pair_count",
            "pairs_with_volume", "assets_with_pairs", "registry_asset_count",
        )
        for key in count_keys:
            with self.subTest(key=key):
                candidate = semantic_candidate()
                candidate["growth"]["tokenized_equities"]["volume"][key] = "1"
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        for value in (-1, True, 1.5, None, {"bad": "type"}):
            with self.subTest(source_row_count=value):
                candidate = semantic_candidate()
                candidate["growth"]["tokenized_equities"]["volume"][
                    "source_row_count"
                ] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_relational_bounds_fail_closed(self):
        mutations = (
            ("requested exceeds expected", "batches_requested", 5),
            ("succeeded exceeds requested", "batches_succeeded", 5),
            ("pair exceeds source rows", "pair_count", 4),
            ("duplicates exceed source rows", "exact_duplicate_row_count", 4),
            ("conflicts exceed source rows", "conflicting_pair_count", 4),
            ("invalid exceeds source rows", "invalid_row_count", 4),
            ("unrelated exceeds invalid", "unrelated_row_count", 1),
            ("volume coverage exceeds pairs", "volume_covered_pair_count", 3),
            ("volume invalid partition", "volume_invalid_pair_count", 1),
            ("liquidity coverage exceeds pairs", "liquidity_covered_pair_count", 3),
            ("liquidity invalid partition", "liquidity_invalid_pair_count", 1),
            ("pairs-with-volume mirror", "pairs_with_volume", 1),
            ("assets exceed registry", "assets_with_pairs", 108),
            ("registry mirror", "registry_asset_count", 106),
            ("batch count mirror", "batches_expected", 3),
        )
        for label, key, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                enable_complete_dex_volume(candidate)
                candidate["growth"]["tokenized_equities"]["volume"][key] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        collective = semantic_candidate()
        volume = collective["growth"]["tokenized_equities"]["volume"]
        volume.update({
            "source_row_count": 2,
            "pair_count": 1,
            "exact_duplicate_row_count": 1,
            "invalid_row_count": 1,
            "volume_covered_pair_count": 0,
            "volume_invalid_pair_count": 1,
            "liquidity_covered_pair_count": 0,
            "liquidity_invalid_pair_count": 1,
            "assets_with_pairs": 1,
        })
        result = self.gate_result(collective)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        impossible_conflicts = semantic_candidate()
        volume = impossible_conflicts["growth"]["tokenized_equities"]["volume"]
        volume.update({"source_row_count": 1, "conflicting_pair_count": 1})
        result = self.gate_result(impossible_conflicts)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_availability_and_missing_values_fail_closed(self):
        incomplete = semantic_candidate()
        enable_complete_dex_volume(incomplete)
        volume = incomplete["growth"]["tokenized_equities"]["volume"]
        volume.update({
            "available": False,
            "partial": True,
            "volume_covered_pair_count": 1,
            "volume_invalid_pair_count": 1,
            "pairs_with_volume": 1,
            "reason": (
                "Explicit finite non-negative 24h volume covers 1 of 2 retained "
                "unique pairs; derived aggregate withheld."
            ),
        })
        volume.pop("volume_24h_usd")
        incomplete["growth"]["sources"]["dex_volume"]["available"] = False
        self.assertTrue(self.gate_result(incomplete)["publishable"])

        synthesized_zero = copy.deepcopy(incomplete)
        synthesized_zero["growth"]["tokenized_equities"]["volume"][
            "volume_24h_usd"
        ] = 0
        self.assertFalse(self.gate_result(synthesized_zero)["publishable"])

        cases = []
        conflict = semantic_candidate()
        enable_complete_dex_volume(conflict)
        conflict["growth"]["tokenized_equities"]["volume"][
            "conflicting_pair_count"
        ] = 1
        cases.append(conflict)
        zero_pairs = semantic_candidate()
        enable_complete_dex_volume(zero_pairs)
        for key in (
            "source_row_count", "pair_count", "exact_duplicate_row_count",
            "volume_covered_pair_count", "liquidity_covered_pair_count",
            "pairs_with_volume", "assets_with_pairs",
        ):
            zero_pairs["growth"]["tokenized_equities"]["volume"][key] = 0
        cases.append(zero_pairs)
        missing_aggregate = semantic_candidate()
        enable_complete_dex_volume(missing_aggregate)
        del missing_aggregate["growth"]["tokenized_equities"]["volume"][
            "volume_24h_usd"
        ]
        cases.append(missing_aggregate)
        for value in (-1, "12.5", True, float("nan"), float("inf"), 10**1000):
            malformed = semantic_candidate()
            enable_complete_dex_volume(malformed)
            malformed["growth"]["tokenized_equities"]["volume"][
                "volume_24h_usd"
            ] = value
            cases.append(malformed)
        for candidate in cases:
            result = self.gate_result(candidate)
            self.assertFalse(result["publishable"], result)
            self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_liquidity_requires_complete_explicit_coverage(self):
        for value in (-1, "20", True, float("nan"), float("inf"), 10**1000):
            with self.subTest(liquidity=value):
                candidate = semantic_candidate()
                enable_complete_dex_volume(candidate)
                candidate["growth"]["tokenized_equities"]["volume"][
                    "liquidity_usd"
                ] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        synthesized_zero = semantic_candidate()
        enable_complete_dex_volume(synthesized_zero)
        volume = synthesized_zero["growth"]["tokenized_equities"]["volume"]
        volume["liquidity_covered_pair_count"] = 1
        volume["liquidity_invalid_pair_count"] = 1
        volume["liquidity_usd"] = 0
        result = self.gate_result(synthesized_zero)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_scope_state_and_source_mirror_fail_closed(self):
        mutations = (
            ("partial", ("metric", "partial"), False),
            ("transport", ("metric", "transport_complete"), True),
            ("market coverage", ("metric", "market_coverage"), "partial"),
            ("exclusions", ("metric", "exclusions"), ["centralized venues"]),
            ("source URL", ("metric", "source_url"), "https://example.invalid"),
            ("source available", ("source", "available"), True),
            ("source partial", ("source", "partial"), False),
            ("source scope", ("source", "scope"), "all Solana volume"),
            ("source transport", ("source", "transport_complete"), True),
            ("source market", ("source", "market_coverage"), "partial"),
            ("source exclusions", ("source", "exclusions"), ["RFQ fills"]),
            ("source URL mirror", ("source", "url"), "https://example.invalid"),
        )
        for label, (target_name, key), value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = (
                    candidate["growth"]["tokenized_equities"]["volume"]
                    if target_name == "metric"
                    else candidate["growth"]["sources"]["dex_volume"]
                )
                target[key] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        for target_name in ("metric", "source"):
            with self.subTest(missing_partial=target_name):
                candidate = semantic_candidate()
                target = (
                    candidate["growth"]["tokenized_equities"]["volume"]
                    if target_name == "metric"
                    else candidate["growth"]["sources"]["dex_volume"]
                )
                del target["partial"]
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_dex_volume_rejects_per_pair_lists_and_malformed_shapes(self):
        for target_name, key in (
            ("metric", "top_pairs"),
            ("metric", "pairs"),
            ("metric", "pair_rows"),
            ("metric", "rows"),
            ("source", "top_pairs"),
        ):
            with self.subTest(target=target_name, key=key):
                candidate = semantic_candidate()
                target = (
                    candidate["growth"]["tokenized_equities"]["volume"]
                    if target_name == "metric"
                    else candidate["growth"]["sources"]["dex_volume"]
                )
                target[key] = []
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        for target_name in ("metric", "source"):
            with self.subTest(malformed=target_name):
                candidate = semantic_candidate()
                if target_name == "metric":
                    candidate["growth"]["tokenized_equities"]["volume"] = []
                else:
                    candidate["growth"]["sources"]["dex_volume"] = []
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_xstock_registry_counts_sources_and_growth_state_fail_closed(self):
        mutations = (
            ("registry revision", ("growth", "sources", "registry", "source_revision"), "b" * 40),
            ("registry path", ("growth", "sources", "registry", "provenance", "path"), "wrong"),
            ("registry asset count", ("growth", "sources", "registry", "asset_count"), 106),
            ("supply mirror", ("growth", "sources", "supply", "coverage_numerator"), 3),
            ("supply endpoint", ("growth", "sources", "supply", "endpoint"), "https://rpc.other"),
            ("supply endpoint identity", (
                "growth", "sources", "supply", "rpc_endpoint_identity",
            ), "sha256:" + "0" * 64),
            ("eligible count", ("growth", "tokenized_equities", "eligible_asset_count"), 106),
            ("display count", ("growth", "tokenized_equities", "displayed_asset_count"), 11),
            ("growth state", ("growth", "available"), False),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        duplicate = semantic_candidate()
        duplicate["growth"]["tokenized_equities"]["all_assets"][1]["mint"] = "mint-000"
        result = self.gate_result(duplicate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_xstock_deadline_state_must_exactly_match_incomplete_sweep(self):
        candidate = semantic_candidate()
        equities = candidate["growth"]["tokenized_equities"]
        supply = candidate["growth"]["sources"]["supply"]
        self.assertLess(
            equities["supply_queried_this_run_asset_count"],
            equities["eligible_asset_count"],
        )
        equities["supply_deadline_exhausted"] = False
        supply["deadline_exhausted"] = False

        failures = pipeline.semantic_failures(candidate)
        self.assertTrue(any(
            "supply_deadline_exhausted" in failure["detail"]
            for failure in failures
        ), failures)

        state_error = semantic_candidate()
        equities = state_error["growth"]["tokenized_equities"]
        supply = state_error["growth"]["sources"]["supply"]
        equities["supply_deadline_exhausted"] = False
        supply["deadline_exhausted"] = False
        supply["state_error"] = "unsupported or malformed supply state"
        self.assertEqual(pipeline.semantic_failures(state_error), [])

        malformed_error = copy.deepcopy(state_error)
        malformed_error["growth"]["sources"]["supply"]["state_error"] = False
        failures = pipeline.semantic_failures(malformed_error)
        self.assertTrue(any(
            "growth.sources.supply.state_error" in failure["detail"]
            for failure in failures
        ), failures)

    def test_schema8_accepts_only_sanitized_custom_rpc_provenance(self):
        candidate = semantic_candidate()
        endpoint = "https://user:password@rpc.example/v2/secret?api-key=SUPERSECRET"
        identity = growth.rpc_endpoint_identity(endpoint)
        candidate["source"].update({
            "endpoint": growth.CUSTOM_RPC_ENDPOINT_LABEL,
            "endpoint_identity": identity,
            "requires_api_key": None,
        })
        candidate["growth"]["sources"]["supply"].update({
            "endpoint": growth.CUSTOM_RPC_ENDPOINT_LABEL,
            "rpc_endpoint_identity": identity,
        })
        candidate["activity"].update({"requires_api_key": None})
        candidate["activity"]["source"].update({
            "endpoint": growth.CUSTOM_RPC_ENDPOINT_LABEL,
            "endpoint_identity": identity,
        })
        self.assertEqual(pipeline.semantic_failures(candidate), [])
        self.assertNotIn("SUPERSECRET", json.dumps(candidate))
        self.assertNotIn("password", json.dumps(candidate))

        raw = semantic_candidate()
        raw["source"].update({
            "endpoint": endpoint,
            "endpoint_identity": identity,
            "requires_api_key": None,
        })
        raw["growth"]["sources"]["supply"].update({
            "endpoint": endpoint,
            "rpc_endpoint_identity": identity,
        })
        raw["activity"].update({"requires_api_key": None})
        raw["activity"]["source"].update({
            "endpoint": endpoint,
            "endpoint_identity": identity,
        })
        failures = pipeline.semantic_failures(raw)
        self.assertTrue(any(
            "source.endpoint" in failure["detail"] for failure in failures
        ), failures)

    def test_real_growth_transform_matches_endpoint_and_state_gate_fields(self):
        endpoint = "https://api.mainnet.solana.com"
        products = [{
            "slug": f"xstock-{index}",
            "name": f"Asset {index}",
            "symbol": None,
            "solana_mint": f"mint-{index:03d}",
        } for index in range(107)]
        fixed_registry = pipeline.facts_module.xstock_registry_source()
        registry_result = {
            "products": products,
            "coverage_complete": True,
            "source_url": fixed_registry["url"],
            "source_kind": fixed_registry["kind"],
            "source_key": fixed_registry["source_key"],
            "source_revision": fixed_registry["source_revision"],
            "source_license": fixed_registry["source_license"],
            "provenance": fixed_registry["provenance"],
            "reason": None,
        }
        invalid_state = growth.empty_supply_state(endpoint)
        invalid_state["load_error"] = "unsupported or malformed supply state"
        with patch("growth.fetch_xstocks_registry", return_value=registry_result), patch(
            "growth.fetch_dex_pairs", return_value={
                "rows": [], "batches_expected": 4,
                "batches_requested": 4, "batches_succeeded": 4,
            },
        ), patch(
            "growth._fetch_validated_token_supply", return_value=None,
        ), patch("growth.time.monotonic", return_value=0), patch(
            "growth.time.time", return_value=1_787_572_800,
        ):
            report, next_state = growth.collect_growth(
                endpoint, supply_state=invalid_state,
            )

        candidate = semantic_candidate()
        candidate["growth"] = report
        self.assertIsNone(next_state)
        self.assertEqual(pipeline.semantic_failures(candidate), [])
        self.assertEqual(
            report["sources"]["supply"]["rpc_endpoint_identity"],
            candidate["source"]["endpoint_identity"],
        )
        self.assertNotIn("endpoint_identity", report["sources"]["supply"])

        display_mismatch = semantic_candidate()
        display_mismatch["growth"]["tokenized_equities"]["assets"][0] = copy.deepcopy(
            display_mismatch["growth"]["tokenized_equities"]["all_assets"][20],
        )
        result = self.gate_result(display_mismatch)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_xstock_scaled_supply_rows_fail_closed(self):
        mutations = (
            ("raw format", ("supply_raw_amount",), "1.0"),
            ("raw u64", ("supply_raw_amount",), str(2**64)),
            ("decimals", ("supply_decimals",), 256),
            ("ui string", ("supply_rpc_ui_amount_string",), "NaN"),
            ("float projection", ("supply",), 9.0),
            ("rpc ui projection", ("supply_rpc_ui_amount",), 9.0),
            ("context slot", ("supply_context_slot",), -1),
            ("api version", ("supply_rpc_api_version",), ""),
            ("future collection", ("supply_collected_at",), "2026-08-24T12:00:01+00:00"),
            ("age mismatch", ("supply_age_seconds",), 61),
            ("freshness mismatch", ("supply_freshness",), "stale"),
            ("fresh maximum", ("supply_fresh_max_age_seconds",), 1),
            ("account method", ("supply_multiplier_provenance", "source_method"), "getAccountInfo"),
            ("token program", ("supply_multiplier_provenance", "program"), "spl-token"),
            ("extension", ("supply_multiplier_provenance", "extension"), "unknown"),
            ("multiplier", ("supply_multiplier_provenance", "state", "multiplier"), "0"),
            ("boolean multiplier", ("supply_multiplier_provenance", "state", "multiplier"), True),
            ("new multiplier", ("supply_multiplier_provenance", "state", "newMultiplier"), "NaN"),
            ("authority", ("supply_multiplier_provenance", "state", "authority"), ""),
            ("activation", ("supply_multiplier_provenance", "state", "newMultiplierEffectiveTimestamp"), -1),
            ("account slot", ("supply_multiplier_provenance", "rpc_context_slot"), -1),
            ("future account slot", ("supply_multiplier_provenance", "rpc_context_slot"), 999),
            ("account api", ("supply_multiplier_provenance", "rpc_api_version"), ""),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate["growth"]["tokenized_equities"]["all_assets"][0]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        # Scaled UI Amount uses time-dependent f64 conversion. Raw base units
        # remain exact evidence, but are not required to reproduce the UI value.
        scaled = semantic_candidate()
        scaled["growth"]["tokenized_equities"]["all_assets"][0][
            "supply_raw_amount"
        ] = "100000001"
        self.assertTrue(self.gate_result(scaled)["publishable"])

        immutable_multiplier = semantic_candidate()
        immutable_multiplier["growth"]["tokenized_equities"]["all_assets"][0][
            "supply_multiplier_provenance"
        ]["state"]["authority"] = None
        self.assertTrue(self.gate_result(immutable_multiplier)["publishable"])

    def test_schema8_selected_stablecoin_account_slot_cannot_follow_supply_slot(self):
        candidate = semantic_candidate()
        summary, source = selected_stablecoin_candidate()
        summary["assets"][0]["account_provenance"]["rpc_context_slot"] = (
            summary["assets"][0]["rpc_context_slot"] + 1
        )
        candidate["growth"]["selected_usd_stablecoins"] = summary
        candidate["growth"]["sources"]["selected_usd_stablecoins"] = source

        failures = pipeline.semantic_failures(candidate)
        self.assertTrue(any(
            "account_provenance.rpc_context_slot" in failure["detail"]
            for failure in failures
        ), failures)

    def test_schema8_xstock_coverage_timestamps_and_unavailable_rows_fail_closed(self):
        mutations = (
            ("observed time", ("observed_at_unix",), 1_787_572_801),
            ("observed count", ("supply_observed_asset_count",), 3),
            ("fresh count", ("fresh_supply_asset_count",), 2),
            ("stale count", ("stale_supply_asset_count",), 0),
            ("coverage numerator", ("supply_coverage", "coverage_numerator"), 3),
            ("coverage denominator", ("supply_coverage", "coverage_denominator"), 106),
            ("oldest reversed", ("supply_coverage", "oldest_observation_at"), "2026-08-24T12:00:00+00:00"),
            ("newest future", ("supply_coverage", "newest_observation_at"), "2026-08-24T12:00:01+00:00"),
            ("span", ("supply_coverage", "observation_span_seconds"), 1),
            ("fresh bound", ("supply_coverage", "fresh_max_age_seconds"), 1),
            ("sweep bound", ("supply_coverage", "sweep_max_age_seconds"), 1),
            ("basis", ("supply_coverage", "coverage_basis"), "all assets"),
            ("sweep state", ("supply_coverage", "sweep_complete"), True),
            ("scope", ("supply_coverage", "scope"), "registry-wide"),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate["growth"]["tokenized_equities"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        registry_only = semantic_candidate()
        equities, registry, supply = xstock_candidate(observed=0)
        registry_only["growth"]["tokenized_equities"] = equities
        registry_only["growth"]["sources"]["registry"] = registry
        registry_only["growth"]["sources"]["supply"] = supply
        equities["all_assets"][0]["supply_raw_amount"] = "1"
        result = self.gate_result(registry_only)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_provider_range_semantics_fail_closed(self):
        mutations = (
            ("missing", "daily_active_addresses", "semantic_metric_id", None),
            ("swapped", "daily_active_addresses", "semantic_metric_id", "transaction_initiator_provider_range"),
            ("misleading", "daily_fee_payers", "scope", "network-wide daily active users"),
        )
        for label, section, field, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                if value is None:
                    del candidate["growth"][section][field]
                else:
                    candidate["growth"][section][field] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_provider_history_contract_fails_closed(self):
        mutations = (
            ("observations type", ("growth", "daily_active_addresses",
                                   "provider_observations"), {}),
            ("invalid date", ("growth", "daily_active_addresses",
                              "provider_observations", 0, "date"), "2026-02-30"),
            ("blank provider", ("growth", "daily_active_addresses",
                                "provider_observations", 0, "provider"), " "),
            ("negative value", ("growth", "daily_active_addresses",
                                "provider_observations", 0, "value"), -1),
            ("observed rows", ("growth", "daily_active_addresses",
                               "observed_row_count"), 2),
            ("observed dates", ("growth", "daily_active_addresses",
                                "observed_date_count"), 2),
            ("observed providers", ("growth", "daily_active_addresses",
                                    "observed_provider_count"), 2),
            ("oldest date", ("growth", "daily_active_addresses",
                             "oldest_date"), "2026-08-20"),
            ("newest date", ("growth", "daily_active_addresses",
                             "newest_date"), "2026-08-22"),
            ("invalid count", ("growth", "daily_active_addresses",
                               "invalid_row_count"), -1),
            ("duplicate count", ("growth", "daily_active_addresses",
                                 "exact_duplicate_row_count"), -1),
            ("conflict count", ("growth", "daily_active_addresses",
                                "conflicting_identity_count"), 1),
            ("source rows", ("growth", "daily_active_addresses",
                             "source_row_count"), 2),
            ("source history flag", ("growth", "sources", "activity_benchmark",
                                     "active_addresses_history_available"), False),
            ("source observed rows", ("growth", "sources", "activity_benchmark",
                                      "active_addresses_observed_row_count"), 2),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        duplicate = semantic_candidate()
        observations = duplicate["growth"]["daily_active_addresses"]["provider_observations"]
        observations[1] = dict(observations[0])
        result = self.gate_result(duplicate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        conflict = semantic_candidate()
        summary = conflict["growth"]["daily_active_addresses"]
        summary.update({
            "conflicts": [{"date": "2026-08-21", "provider": "A"}],
            "conflicting_identity_count": 1,
            "source_row_count": 5,
            "partial": True,
        })
        conflict["growth"]["sources"]["activity_benchmark"]["partial"] = True
        result = self.gate_result(conflict)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_provider_history_without_headline_remains_publishable(self):
        candidate = semantic_candidate()
        summary = candidate["growth"]["daily_active_addresses"]
        summary.update({
            "available": False,
            "partial": True,
            "provider_observations": [summary["provider_observations"][0]],
            "source_row_count": 1,
            "observed_row_count": 1,
            "observed_date_count": 1,
            "observed_provider_count": 1,
        })
        for key in ("date", "provider_count", "minimum", "maximum"):
            summary.pop(key)
        source = candidate["growth"]["sources"]["activity_benchmark"]
        source.update({
            "active_addresses_available": False,
            "active_addresses_observed_row_count": 1,
            "partial": True,
        })
        result = self.gate_result(candidate)
        self.assertTrue(result["publishable"], result)

    def test_schema8_empty_provider_histories_remain_optional(self):
        candidate = semantic_candidate()
        candidate["growth"]["daily_active_addresses"] = {"available": False}
        candidate["growth"]["daily_fee_payers"] = {"available": False}
        candidate["growth"]["sources"].pop("activity_benchmark")
        result = self.gate_result(candidate)
        self.assertTrue(result["publishable"], result)

    def test_schema8_provider_rows_require_explicit_history_availability(self):
        candidate = semantic_candidate()
        summary = candidate["growth"]["daily_active_addresses"]
        summary.pop("available")
        summary.pop("history_available")

        result = self.gate_result(candidate)

        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_economics_semantics_fail_closed(self):
        mutations = (
            ("legacy stablecoin field", ("economics", "stablecoins", "stablecoin_usd"), 1),
            ("wrong stablecoin metric", ("economics", "stablecoins", "metric"), "Stablecoins"),
            ("missing provider field", ("economics", "stablecoins", "provider_field"), None),
            ("blank stablecoin scope", ("economics", "stablecoins", "scope"), ""),
            ("negative circulating supply",
             ("economics", "stablecoins", "usd_pegged_circulating_usd"), -1),
            ("duplicate protocol ID",
             ("economics", "protocols", "protocols", 1, "provider_protocol_id"),
             "parent#family"),
            ("missing family ID",
             ("economics", "protocols", "protocols", 0, "provider_family_id"), None),
            ("invalid ranking basis",
             ("economics", "protocols", "protocols", 0, "ranking_basis"), "summed_children"),
            ("negative TVL", ("economics", "protocols", "protocols", 0,
                              "solana_tvl_usd"), -1),
            ("wrong rank order", ("economics", "protocols", "protocols", 1,
                                  "solana_tvl_usd"), 800),
            ("eligible below displayed", ("economics", "protocols",
                                           "eligible_protocol_count"), 1),
            ("negative excluded children", ("economics", "protocols",
                                              "excluded_child_protocol_count"), -1),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate(
                    economics=release_held_economics_candidate(),
                )
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                if value is None:
                    del target[path[-1]]
                else:
                    target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        parent_and_child = semantic_candidate(
            economics=release_held_economics_candidate(),
        )
        parent_and_child["economics"]["protocols"]["protocols"][1].update({
            "provider_protocol_id": "child",
            "provider_family_id": "parent#family",
            "ranking_basis": "provider_child",
        })
        result = self.gate_result(parent_and_child)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_unavailable_economics_sources_remain_optional(self):
        candidate = semantic_candidate()
        candidate["economics"] = {
            "available": True,
            "stablecoins": {"available": False},
            "protocols": {"available": False},
        }
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("release_policy", self.failure_checks(result))

    def test_schema8_news_source_items_fail_closed(self):
        mutations = (
            ("sources mapping", ("news", "sources"), []),
            ("duplicate IDs", ("news", "sources", "solana_news", "items"), [
                {
                    "id": "duplicate", "title": "One", "link": "https://solana.com/one",
                    "published": "2026-08-24T10:00:00Z",
                },
                {
                    "id": "duplicate", "title": "Two", "link": "https://solana.com/two",
                    "published": "2026-08-24T11:00:00Z",
                },
            ]),
            ("unsafe link", ("news", "sources", "solana_news", "items", 0, "link"),
             "http://solana.com/news/update"),
            ("naive timestamp", ("news", "sources", "solana_news", "items", 0, "published"),
             "2026-08-24T10:00:00"),
            ("item count", ("news", "sources", "solana_news", "item_count"), 2),
            ("invalid rows require partial",
             ("news", "sources", "solana_news", "invalid_item_count"), 1),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        unavailable = semantic_candidate()
        unavailable["news"]["sources"]["solana_news"] = {
            "available": False, "reason": "source unreachable",
        }
        unavailable["news"]["partial"] = True
        self.assertTrue(self.gate_result(unavailable)["publishable"])
        del unavailable["news"]["sources"]["solana_news"]["reason"]
        self.assertFalse(self.gate_result(unavailable)["publishable"])

    def test_schema9_normalized_editorial_contract_fails_closed(self):
        candidate = semantic_candidate()
        candidate["schema_version"] = 9
        candidate["news"]["items"] = news.normalize_editorial_items(
            candidate["news"]["sources"], candidate["news"]["current_status"],
        )
        for item in candidate["news"]["items"]:
            item["recorded_at"] = candidate["collected_at"]
        candidate["news"]["featured_item_id"] = news.featured_editorial_item_id(
            candidate["news"]["items"],
        )
        self.assertTrue(self.gate_result(candidate)["publishable"])

        mutations = (
            ("featured membership", ("featured_item_id",), "missing"),
            ("source membership", ("items", 0, "source_id"), "unknown"),
            ("title projection", ("items", 0, "title"), "Fabricated headline"),
            ("publisher projection", ("items", 0, "publisher"), "Fabricated publisher"),
            ("category projection", ("items", 0, "category"), "event"),
            ("note projection", ("items", 0, "editorial_note"), "Fabricated note."),
            ("unsafe URL", ("items", 0, "canonical_url"), "javascript:alert(1)"),
            ("wrong source host", ("items", 0, "canonical_url"),
             "https://attacker.example/anza-xyz/agave/releases/tag/v4.1.0"),
            ("credentialed URL", ("items", 0, "canonical_url"),
             "https://user:pass@github.com/anza-xyz/agave/releases/tag/v4.1.0"),
            ("nonstandard port", ("items", 0, "canonical_url"),
             "https://github.com:444/anza-xyz/agave/releases/tag/v4.1.0"),
            ("recorded time", ("items", 0, "recorded_at"), "2026-08-23T00:00:00Z"),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                broken = copy.deepcopy(candidate)
                target = broken["news"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(broken)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema9_accepts_only_pinned_legacy_x_projection(self):
        candidate = semantic_candidate()
        candidate["schema_version"] = 9
        candidate["provenance"]["source_revision"] = next(
            iter(pipeline.LEGACY_X_CURRENT_SOURCE_REVISIONS)
        )
        x_source = {
            "available": True,
            "requires_api_key": False,
            "partial": False,
            "invalid_item_count": 0,
            "item_count": 1,
            "account_allowlist": list(news.xnews.X_ACCOUNT_ALLOWLIST),
            "max_posts_per_run": news.xnews.MAX_POSTS,
            "items": [{
                "id": "123456789",
                "author": "solana",
                "text": "Recorded announcement https://t.co/example",
                "title": "Recorded announcement https://t.co/example",
                "published": "2026-08-24T10:00:00Z",
                "link": "https://x.com/solana/status/123456789",
                "like_count": 1,
                "retweet_count": 2,
            }],
        }
        candidate["news"]["sources"]["x_announcements"] = x_source
        candidate["news"]["items"] = news.normalize_editorial_items(
            candidate["news"]["sources"], candidate["news"]["current_status"],
            legacy_x_titles=True,
        )
        for item in candidate["news"]["items"]:
            item["recorded_at"] = candidate["collected_at"]
        candidate["news"]["featured_item_id"] = news.featured_editorial_item_id(
            candidate["news"]["items"],
        )
        self.assertTrue(self.gate_result(candidate)["publishable"])

        candidate["provenance"]["source_revision"] = "c" * 40
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"])
        details = [failure["detail"] for failure in result["failures"]]
        self.assertTrue(any("requires_api_key" in detail for detail in details))
        self.assertTrue(any("latest_published" in detail for detail in details))
        self.assertTrue(any("editorial projection" in detail for detail in details))

        empty = copy.deepcopy(candidate)
        empty["news"]["items"] = []
        empty["news"]["featured_item_id"] = None
        result = self.gate_result(empty)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        alternate = copy.deepcopy(candidate)
        alternate["news"]["featured_item_id"] = alternate["news"]["items"][-1]["id"]
        result = self.gate_result(alternate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        draft = copy.deepcopy(candidate)
        release = draft["news"]["sources"]["agave_releases"]["items"][0]
        release.update({"draft": True, "stable": False, "release_channel": "draft"})
        result = self.gate_result(draft)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_news_partial_status_is_usable_partial_evidence(self):
        candidate = semantic_candidate()
        candidate["news"] = {
            "available": True,
            "partial": True,
            "sources": {
                name: {"available": False, "reason": "source unreachable"}
                for name in (
                    "agave_releases", "solana_news", "simd_proposals", "network_status",
                )
            },
            "current_status": {
                "available": False,
                "partial": True,
                "status_available": True,
                "incidents_available": False,
                "incident_response_available": False,
                "invalid_incident_count": 0,
                "indicator": "none",
                "description": "All Systems Operational",
                "incidents": [],
                "incident_history": [],
                "active_incident_count": None,
                "sources": {
                    "summary": "https://status.solana.com/api/v2/summary.json",
                    "incidents": "https://status.solana.com/api/v2/incidents.json",
                },
            },
        }
        result = self.gate_result(candidate)
        self.assertTrue(result["publishable"], result)

        candidate["news"]["available"] = False
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))
        candidate["news"]["available"] = True

        candidate["news"]["partial"] = False
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        complete = semantic_candidate()
        complete["news"]["partial"] = True
        result = self.gate_result(complete)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_agave_and_simd_provenance_fail_closed(self):
        mutations = (
            ("stable without commit", ("news", "sources", "agave_releases", "items", 0,
                                        "tag_commit_sha"), None),
            ("draft stable", ("news", "sources", "agave_releases", "items", 0, "draft"), True),
            ("wrong release channel", ("news", "sources", "agave_releases", "items", 0,
                                       "release_channel"), "prerelease"),
            ("short SIMD commit", ("news", "sources", "simd_proposals", "source_commit"), "abc"),
            ("proposal count", ("news", "sources", "simd_proposals", "proposal_count"), 2),
            ("proposal commit", ("news", "sources", "simd_proposals", "proposals", 0,
                                 "source_commit"), "c" * 40),
            ("missing proposal date metadata", ("news", "sources", "simd_proposals",
                                                "proposals", 0, "created"), "missing"),
            ("published SIMD item", ("news", "sources", "simd_proposals", "items", 0,
                                     "published"), "2025-07-25T00:00:00Z"),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                if label == "missing proposal date metadata":
                    del target[path[-1]]
                else:
                    target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_schema8_status_missing_or_invalid_evidence_never_becomes_zero(self):
        mutations = (
            ("missing response as zero", "incident_response_available", False, "active_incident_count", 0),
            ("invalid response as zero", "incidents_available", False, "active_incident_count", 0),
            ("count mismatch", "active_incident_count", 1, None, None),
        )
        for label, key, value, other_key, other_value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                status = candidate["news"]["current_status"]
                status[key] = value
                if other_key is not None:
                    status[other_key] = other_value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

        duplicate = semantic_candidate()
        incident = {
            "id": "incident-1", "name": "RPC issue", "status": "resolved",
            "url": "https://status.solana.com/incidents/1",
            "created_at": "2026-08-23T10:00:00Z",
            "updated_at": "2026-08-23T11:00:00Z",
        }
        duplicate["news"]["current_status"].update({
            "incident_history": [incident, dict(incident)],
        })
        result = self.gate_result(duplicate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_nested_counts_and_ranges_fail_closed(self):
        mutations = (
            ("string validator count", ("validators", "active_count"), "1"),
            ("negative validator count", ("validators", "active_count"), -1),
            ("epoch progress", ("epoch", "progress_pct"), 150),
            ("xstocks coverage", ("growth", "tokenized_equities", "valued_asset_count"), 3),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                candidate = semantic_candidate()
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = self.gate_result(candidate)
                self.assertFalse(result["publishable"], result)
                self.assertIn("semantic", self.failure_checks(result))

    def test_duplicate_validator_and_mint_identities_fail_closed(self):
        validator = semantic_candidate()
        validator["validators"]["all_validators"][1]["identity"] = "identity-a"
        result = self.gate_result(validator)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        mint = semantic_candidate()
        mint["growth"]["tokenized_equities"]["all_assets"][1]["mint"] = "mint-000"
        result = self.gate_result(mint)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_supply_and_count_relationships_fail_closed(self):
        cases = []
        circulating = semantic_candidate()
        circulating["supply"]["circulating_sol"] = 101
        cases.append(circulating)
        validators = semantic_candidate()
        validators["validators"]["all_validator_count"] = 3
        cases.append(validators)
        coverage = semantic_candidate()
        coverage["growth"]["tokenized_equities"]["supply_successful_this_run_asset_count"] = 2
        coverage["growth"]["tokenized_equities"]["supply_failed_this_run_asset_count"] = 1
        cases.append(coverage)
        numerator = semantic_candidate()
        numerator["growth"]["tokenized_equities"]["supply_coverage"]["coverage_numerator"] = 3
        cases.append(numerator)
        for candidate in cases:
            result = self.gate_result(candidate)
            self.assertFalse(result["publishable"], result)
            self.assertIn("semantic", self.failure_checks(result))

    def test_completed_epoch_production_relationships_fail_closed(self):
        candidate = semantic_candidate()
        candidate["validators"]["block_production"]["validators"][0]["blocks_produced"] = 7
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_completed_epoch_collection_provenance_fails_closed(self):
        cases = []
        missing = semantic_candidate()
        del missing["validators"]["block_production"]["collection"]
        cases.append(missing)
        incomplete = semantic_candidate()
        incomplete["validators"]["block_production"]["collection"][
            "coverage_numerator_slots"
        ] = 9
        cases.append(incomplete)
        wrong_requests = semantic_candidate()
        wrong_requests["validators"]["block_production"]["collection"][
            "request_count"
        ] = 2
        cases.append(wrong_requests)
        for candidate in cases:
            result = self.gate_result(candidate)
            self.assertFalse(result["publishable"], result)
            self.assertIn("semantic", self.failure_checks(result))

    def test_activity_fee_components_must_reconcile(self):
        candidate = semantic_candidate()
        candidate["activity"]["rev"]["sampled_sol"]["unclassified_fee_residual"] = 0.03
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_timestamp_and_performance_relationships_fail_closed(self):
        reversed_window = semantic_candidate()
        reversed_window["activity"]["window"]["first_block_time"] = 1_725_191_100
        result = self.gate_result(reversed_window)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

        sample_count = semantic_candidate()
        sample_count["performance"]["samples_used"] = 3
        result = self.gate_result(sample_count)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_available_section_with_impossible_state_fails_closed(self):
        candidate = semantic_candidate()
        candidate["activity"]["stale"] = True
        result = self.gate_result(candidate)
        self.assertFalse(result["publishable"], result)
        self.assertIn("semantic", self.failure_checks(result))

    def test_optional_unavailable_section_with_no_values_still_passes(self):
        candidate = semantic_candidate(activity={"available": False})
        result = self.gate_result(candidate)
        self.assertTrue(result["publishable"], result)


class TestPublishGateCli(unittest.TestCase):
    """The gate is a command so the workflow can stop before render/deploy."""

    def write_candidate(self, directory, snapshot, name="latest.json"):
        path = Path(directory) / name
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def run_gate_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = pipeline.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def gate_argv(self, path):
        return [
            "--snapshot", str(path),
            "--max-age-seconds", "21600",
            "--now", REFERENCE_TIME.isoformat(),
        ]

    def test_publishable_candidate_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_candidate(tmp, publish_candidate())
            code, out, err = self.run_gate_cli(*self.gate_argv(path))
        self.assertEqual(code, 0, err)
        self.assertIn("publishable", out)

    def test_unpublishable_candidate_exits_one_with_structured_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_candidate(tmp, publish_candidate(schema_version=99))
            code, _, err = self.run_gate_cli(*self.gate_argv(path))
        self.assertEqual(code, 1)
        payload = json.loads(err)
        self.assertFalse(payload["publishable"])
        self.assertIn(
            "schema_version", [f["check"] for f in payload["failures"]])

    def test_unreadable_candidate_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "absent.json"
            code, _, err = self.run_gate_cli(*self.gate_argv(absent))
        self.assertEqual(code, 1)
        payload = json.loads(err)
        self.assertFalse(payload["publishable"])
        self.assertEqual(payload["failures"][0]["check"], "candidate_readable")


class TestWorkflowGateCoverage(unittest.TestCase):
    """The release workflow must verify, stage, and deploy through separate gates."""

    WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "update.yml"

    def step_block(self, name: str) -> str:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        return text.split(f"- name: {name}", 1)[1].split("- name:", 1)[0]

    def test_push_is_verify_only_and_update_requires_explicit_enablement(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        verify = text.split("  verify:", 1)[1].split("\n  update:", 1)[0]
        update = text.split("  update:", 1)[1].split("\n  bootstrap:", 1)[0]
        self.assertIn("pull_request:", text)
        self.assertIn("github.event_name == 'pull_request'", verify)
        self.assertIn("python3 -m unittest discover -s tests", verify)
        self.assertIn("python3 pipeline.py", verify)
        self.assertIn("python3 verify_release.py verify-data --committed", verify)
        self.assertIn('github.event.pull_request.base.sha', verify)
        self.assertIn('github.event.before', verify)
        self.assertIn('--base-revision "$BASE_REVISION"', verify)
        self.assertNotIn("verify-package", verify)
        self.assertNotIn("collect.py", verify)
        self.assertNotIn("render.py", verify)
        self.assertNotIn("upload-pages-artifact", verify)
        self.assertIn("vars.REPORT_AUTOMATION_ENABLED == 'true'", update)
        self.assertIn("inputs.mode == 'update'", update)
        self.assertIn("contents: write", update)

    def test_gate_precedes_analysis_stage_commit_push_render_and_deploy(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        markers = (
            "- name: Collect live snapshot",
            "- name: Validate candidate snapshot",
            "- name: Detect anomalies",
            "- name: Assert complete update path set",
            "- name: Verify public update data",
            "- name: Commit new snapshot",
            "- name: Render committed revision",
            "- name: Verify publication package",
            "- name: Push committed snapshot",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e",
        )
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    def test_third_party_actions_are_pinned_to_immutable_commits(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        uses = [
            line.strip().split("uses:", 1)[1].strip().split()[0]
            for line in text.splitlines()
            if "uses:" in line
        ]
        self.assertTrue(uses)
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertEqual(text.count("fetch-depth: 0"), 3)
        self.assertEqual(
            text.count("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"),
            2,
        )
        self.assertEqual(text.count("tar --dereference --hard-dereference"), 2)
        self.assertEqual(text.count("retention-days: 1"), 2)
        self.assertEqual(text.count("if-no-files-found: error"), 2)

    def test_commit_persists_only_exact_update_paths(self):
        commit = self.step_block("Commit new snapshot")
        self.assertIn('git add -- "$IMMUTABLE_SNAPSHOT"', commit)
        self.assertIn("git add -- snapshots/latest.json", commit)
        self.assertIn("git add -- history/facts.jsonl", commit)
        self.assertIn(
            'if [ -n "$(git status --porcelain=v1 -- state/xstocks-supply.json)" ]; then',
            commit,
        )
        self.assertIn("git add -- state/xstocks-supply.json", commit)
        self.assertNotIn("git add snapshots/", commit)
        self.assertNotIn("git add .", commit)
        self.assertNotIn("git push", commit)

    def test_data_and_package_verifiers_precede_staging_push_and_upload(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        data_gate = self.step_block("Verify public update data")
        render_step = self.step_block("Render committed revision")
        package_gate = self.step_block("Verify publication package")
        push = self.step_block("Push committed snapshot")
        self.assertIn("verify_release.py verify-data --pending-update", data_gate)
        self.assertIn('--now "$RUN_TIMESTAMP"', data_gate)
        self.assertIn('GITHUB_SHA= python3 render.py --generated-at "$RUN_TIMESTAMP"',
                      render_step)
        self.assertIn("verify_release.py verify-package --artifacts dist", package_gate)
        self.assertIn('--now "$RUN_TIMESTAMP"', package_gate)
        self.assertIn('git push origin "HEAD:${GITHUB_REF_NAME}"', push)
        self.assertLess(
            text.index("- name: Verify public update data"),
            text.index("- name: Commit new snapshot"),
        )
        self.assertLess(
            text.index("- name: Render committed revision"),
            text.index("- name: Verify publication package"),
        )
        self.assertLess(
            text.index("- name: Verify publication package"),
            text.index("- name: Push committed snapshot"),
        )
        self.assertLess(
            text.index("- name: Push committed snapshot"),
            text.index("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"),
        )

    def test_bootstrap_uses_committed_samples_and_deploy_alone_has_pages_permissions(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        bootstrap = text.split("  bootstrap:", 1)[1].split("\n  deploy:", 1)[0]
        update = text.split("  update:", 1)[1].split("\n  bootstrap:", 1)[0]
        deploy = text.split("  deploy:", 1)[1]
        self.assertIn("inputs.mode == 'bootstrap'", bootstrap)
        self.assertIn(
            "git ls-files --error-unmatch release-manifest.json",
            bootstrap,
        )
        self.assertIn("samples/index.html samples/report.md samples/report.json", bootstrap)
        self.assertIn(
            "verify_release.py verify-package --artifacts samples",
            bootstrap,
        )
        self.assertIn("--manifest release-manifest.json", bootstrap)
        self.assertIn(
            "APPROVED_MANIFEST_SHA256: ${{ secrets.REPORT_BOOTSTRAP_MANIFEST_SHA256 }}",
            bootstrap,
        )
        self.assertIn("requires owner-set REPORT_BOOTSTRAP_MANIFEST_SHA256", bootstrap)
        self.assertLess(
            bootstrap.index("approved manifest SHA-256 mismatch"),
            bootstrap.index("verify_release.py verify-package"),
        )
        self.assertIn("--directory samples", bootstrap)
        self.assertIn("--directory dist", update)
        self.assertIn("path: ${{ runner.temp }}/artifact.tar", bootstrap)
        self.assertIn("name: github-pages", bootstrap)
        self.assertNotIn("upload-pages-artifact", text)
        self.assertNotIn("collect.py", bootstrap)
        self.assertNotIn("render.py", bootstrap)
        self.assertNotIn("REPORT_RPC_ENDPOINT", bootstrap)
        self.assertNotIn("pages: write", update)
        self.assertIn("pages: write", deploy)
        self.assertIn("id-token: write", deploy)

    def test_update_requires_an_owner_approved_rpc_secret(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        update = text.split("  update:", 1)[1].split("\n  bootstrap:", 1)[0]
        self.assertIn("- name: Require approved RPC endpoint", update)
        self.assertIn("REPORT_RPC_ENDPOINT: ${{ secrets.REPORT_RPC_ENDPOINT }}", update)
        self.assertIn(
            "REPORT_RPC_ENDPOINT must identify an owner-approved production RPC",
            update,
        )
        self.assertIn(
            'python3 collect.py --with-price --with-dune --endpoint "$REPORT_RPC_ENDPOINT"',
            update,
        )
        # Dune rides on the repo variable query ID + refresh window; the key
        # stays a secret. The adapter is opt-in per run and degrades alone.
        self.assertIn("DUNE_QUERY_ID: ${{ vars.DUNE_QUERY_ID }}", update)
        self.assertIn("DUNE_API_KEY: ${{ secrets.DUNE_API_KEY }}", update)
        # The Demo key rides only in the environment and as a request header;
        # it must never appear in a URL or a command line.
        self.assertIn(
            "COINGECKO_DEMO_API_KEY: ${{ secrets.COINGECKO_DEMO_API_KEY }}",
            update,
        )
        self.assertNotIn("x-cg-demo-api-key", update)

    def test_update_exports_release_outputs_for_smoke_check(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        update = text.split("  update:", 1)[1].split("\n  bootstrap:", 1)[0]
        # The publication timestamp is exported both to the environment and
        # as a job output, and the rendered release id is recorded as an
        # output, so the deploy smoke check can bind the hosted artifact to
        # this exact run.
        self.assertIn('echo "run_timestamp=$run_timestamp" >> "$GITHUB_OUTPUT"', update)
        self.assertIn("Record release id for the smoke check", update)

    def test_bootstrap_exports_verified_archival_release_metadata(self):
        import os
        import textwrap

        text = self.WORKFLOW.read_text(encoding="utf-8")
        bootstrap = text.split("  bootstrap:", 1)[1].split("\n  deploy:", 1)[0]
        self.assertIn("run_timestamp: ${{ steps.release_metadata.outputs.run_timestamp }}", bootstrap)
        self.assertIn("release_id: ${{ steps.release_metadata.outputs.release_id }}", bootstrap)
        self.assertLess(bootstrap.index("verify_release.py verify-package"),
                        bootstrap.index("- id: release_metadata"))
        step = bootstrap.split("- id: release_metadata", 1)[1]
        script = textwrap.dedent(step.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        release = {"generated_at": "2026-08-31T19:54:16+00:00", "release_id": "archival-release"}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                    patch.object(Path, "read_text", return_value=json.dumps({"release": release})):
                exec(compile(script, "bootstrap-metadata", "exec"), {})
            self.assertEqual(output.read_text(),
                             "run_timestamp=2026-08-31T19:54:16+00:00\nrelease_id=archival-release\n")

    def test_smoke_retries_old_cdn_release_but_rejects_persistent_mismatch(self):
        import os
        import textwrap

        deploy = self.WORKFLOW.read_text().split("  deploy:", 1)[1]
        script = textwrap.dedent(deploy.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        stamp = "2026-09-04T23:00:00+00:00"
        current = {"release": {"generated_at": stamp, "release_id": "expected"},
                   "observations": [{}]}
        old = copy.deepcopy(current)
        old["release"]["release_id"] = "previous"
        for responses, succeeds in (([old, current], True), ([old] * 6, False)):
            with self.subTest(succeeds=succeeds), patch.dict(os.environ, {
                "REPORT_URL": "https://example.com/report", "RUN_TIMESTAMP": stamp,
                "SMOKE_MAX_AGE_SECONDS": "600", "RELEASE_ID": "expected",
            }), patch("urllib.request.urlopen", side_effect=[
                io.BytesIO(json.dumps(payload).encode()) for payload in responses
            ]) as fetch, patch("time.sleep"), redirect_stdout(io.StringIO()):
                if succeeds:
                    exec(compile(script, "deploy-smoke", "exec"), {})
                else:
                    with self.assertRaisesRegex(SystemExit, "does not match"):
                        exec(compile(script, "deploy-smoke", "exec"), {})
                self.assertEqual(fetch.call_count, len(responses))

    def test_deploy_smoke_check_binds_hosted_artifact_to_the_run(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        deploy = text.split("  deploy:", 1)[1]
        self.assertIn("Smoke check live report", deploy)
        self.assertIn("REPORT_URL: ${{ steps.deploy.outputs.page_url }}", deploy)
        self.assertIn(
            "generated_at = payload.get(\"release\", {}).get(\"generated_at\")",
            deploy,
        )
        self.assertIn(
            "RUN_TIMESTAMP: ${{ needs.update.outputs.run_timestamp "
            "|| needs.bootstrap.outputs.run_timestamp }}",
            deploy,
        )
        self.assertIn(
            "RELEASE_ID: ${{ needs.update.outputs.release_id "
            "|| needs.bootstrap.outputs.release_id }}",
            deploy,
        )
        self.assertIn("release_id != expected_release_id", deploy)
        self.assertIn("observations must be a non-empty list", deploy)
        self.assertIn("REPORT_SMOKE_MAX_AGE_SECONDS", deploy)

    def test_failure_notification_opens_one_deduplicated_issue(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("notify-failure:", text)
        self.assertIn("if: failure()", text)
        self.assertIn("issues: write", text)
        self.assertIn(
            "[solana-ecosystem-report] scheduled update failed", text,
        )
        # Dedupe: search for an existing open issue before creating one.
        # The job runs without a checkout, so every gh call passes -R explicitly.
        self.assertIn("gh issue list -R \"$GITHUB_REPOSITORY\"", text)
        self.assertIn("gh issue comment -R \"$GITHUB_REPOSITORY\"", text)
        self.assertIn("gh issue create -R \"$GITHUB_REPOSITORY\"", text)
