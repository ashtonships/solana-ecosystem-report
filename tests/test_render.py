"""Offline tests for the renderers.

Both renderers are pure functions over a snapshot dict, so these run with no
network and no filesystem beyond reading the fixture.
"""

import hashlib
import json
import re
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render  # noqa: E402
import pipeline  # noqa: E402
import collect  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample-snapshot.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def editorial_fixture():
    snapshot = load_fixture()
    recorded_at = snapshot["collected_at"]
    items = [
        {
            "id": "status:1", "source_id": "network_status",
            "publisher": "Solana Status", "category": "network",
            "title": "Network update", "canonical_url": "https://status.solana.com/notices/1",
            "published_at": "2026-08-05T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "Recorded network update.",
            "art_seed": "status:1",
        },
        {
            "id": "github-release:1", "source_id": "agave_releases",
            "publisher": "Anza", "category": "release", "title": "Agave v4.1.0",
            "canonical_url": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0",
            "published_at": "2026-08-04T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "Recorded validator release.",
            "art_seed": "github-release:1",
        },
        {
            "id": "status:2", "source_id": "network_status",
            "publisher": "Solana Status", "category": "network",
            "title": "Scheduled maintenance completed",
            "canonical_url": "https://status.solana.com/notices/2",
            "published_at": "2026-08-03T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "Recorded status follow-up.",
            "art_seed": "status:2",
        },
        {
            "id": "github-release:2", "source_id": "agave_releases",
            "publisher": "Anza", "category": "release", "title": "Agave v4.0.0",
            "canonical_url": "https://github.com/anza-xyz/agave/releases/tag/v4.0.0",
            "published_at": "2026-08-02T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "Earlier recorded validator release.",
            "art_seed": "github-release:2",
        },
    ]
    snapshot["news"] = {
        "available": True, "partial": True, "featured_item_id": "status:1", "items": items,
        "sources": {
            "agave_releases": {"label": "Agave releases", "available": True, "items": []},
            "network_status": {"label": "Network status", "available": True, "items": []},
            "solana_news": {"label": "Solana News", "available": False, "reason": "not collected"},
            "simd_proposals": {"label": "SIMD proposals", "available": False, "reason": "not collected"},
        },
    }
    return snapshot


def recorded_history_fixture(count=24):
    """Deterministic schema-8 history; public-package tests never read snapshots/."""
    snapshots = []
    for index in range(count):
        snapshot = load_fixture()
        observed_at = datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=6 * index)
        slot = 302_400_000 + index * 1_000
        tps = 4_000.0 + index * 10
        snapshot.update({
            "schema_version": 8,
            "collected_at": observed_at.isoformat(),
            "activity": {
                "available": True,
                "window": {"last_block_time": int(observed_at.timestamp())},
                "fees": {"available": True, "median_lamports": 5_000 + index},
                "rev": {"available": True, "sample_mean_estimate_sol": 10.0 + index},
            },
            "economics": {
                "available": True,
                "price": {"available": True, "price_usd": 150.0 + index},
                "tvl": {"available": True, "tvl_usd": 9_000_000_000.0 + index},
            },
            "news": {"available": True, "items": []},
        })
        snapshot["performance"].update({
            "latest_tps": tps,
            "mean_tps": tps - 15,
            "mean_slot_time_secs": 0.40 + index / 10_000,
            "samples": [
                {"slot": slot - offset * 150, "tps": tps - offset * 25,
                 "slot_time_secs": 0.40 + offset / 1_000}
                for offset in range(8)
            ],
        })
        snapshot["performance"]["samples_used"] = len(snapshot["performance"]["samples"])
        snapshot["validators"]["delinquent_pct"] = 3.0 + index / 100
        snapshots.append(snapshot)
    snapshots[-1]["economics"] = {
        "available": False,
        "publication_state": "withheld",
        "reason": render.facts_module.ECONOMICS_PUBLICATION_HOLD,
    }
    return snapshots


def single_snapshot_schema9_fixture():
    """Schema-9 bootstrap input with a complete source-native sample window."""
    snapshot = load_fixture()
    snapshot.update({
        "schema_version": 9,
        "collected_at": "2026-08-05T09:00:00+00:00",
    })
    samples = []
    for index in range(4):
        transactions = 240_000 + index * 10_000
        non_vote = 120_000 + index * 4_000
        samples.append({
            "slot": 302_400_000 + index * 180,
            "transactions": transactions,
            "non_vote_transactions": non_vote,
            "sample_period_secs": 60,
            "slots": 150 + index,
            "tps": transactions / 60,
            "non_vote_tps": non_vote / 60,
            "vote_tps": (transactions - non_vote) / 60,
            "vote_share_pct": 100 * (transactions - non_vote) / transactions,
            "slot_time_secs": 60 / (150 + index),
        })
    snapshot["performance"].update({
        "available": True,
        "samples": samples,
        "samples_used": len(samples),
        "latest_tps": samples[-1]["tps"],
        "mean_slot_time_secs": sum(item["slot_time_secs"] for item in samples) / len(samples),
    })
    return snapshot


def selected_stablecoins_fixture(count=4):
    identities = (
        ("USDC", "Circle", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
        ("USDT", "Tether", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
        ("PYUSD", "PayPal USD issued by Paxos", "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"),
        ("USDG", "Paxos Digital Singapore", "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"),
    )
    assets = []
    for index, (symbol, issuer, mint) in enumerate(identities, 1):
        available = index <= count
        row = {"symbol": symbol, "issuer": issuer, "mint": mint,
               "available": available}
        if available:
            row.update({
                "total_supply_decimal": f"{index}.00",
                "raw_amount": str(index * 100), "decimals": 2,
                "rpc_ui_amount_string": str(index),
                "rpc_context_slot": 320 + index,
                "rpc_api_version": "2.3.7", "event_time": None,
                "collected_at": f"2026-08-25T12:00:0{index}+00:00",
                "basis": "finalized on-chain total token supply",
            })
            if count == 4:
                row["share_of_selected_total"] = f"0.{index}"
        else:
            row["reason"] = "Finalized validated mint supply is unavailable."
        assets.append(row)
    summary = {
        "metric_id": "selected_usd_stablecoin_total_supply",
        "available": count == 4,
        "state": "current" if count == 4 else ("partial" if count else "unavailable"),
        "coverage_numerator": count, "coverage_denominator": 4,
        "coverage_label": f"{count}/4", "universe_coverage": "unknown",
        "unit": "selected stablecoin token units",
        "basis": "finalized on-chain total token supply",
        "assets": assets,
        "slot_range": {"first": 321 if count else None,
                       "last": 320 + count if count else None},
        "oldest_observation_at": "2026-08-25T12:00:01+00:00" if count else None,
        "newest_observation_at": f"2026-08-25T12:00:0{count}+00:00" if count else None,
        "limitations": (
            "Exactly four selected USD stablecoin mints; broader universe coverage is "
            "unknown. Total token supply is not circulating supply, USD value, liquidity, "
            "reserves, or executable depth."
        ),
    }
    if count == 4:
        summary["selected_total_supply_decimal"] = "10.00"
    return summary


class TestMarkdown(unittest.TestCase):
    def test_generated_markdown_has_no_trailing_whitespace(self):
        markdown = render.render_markdown(load_fixture())
        self.assertFalse(any(line != line.rstrip() for line in markdown.splitlines()))

    def test_provenance_distinguishes_current_snapshot_from_recorded_history(self):
        markdown = render.render_markdown(load_fixture(), history=[load_fixture()])
        self.assertIn(
            "Current claims come from one selected snapshot; history, anomaly, and delta "
            "sections use the append-only recorded snapshot series.",
            markdown,
        )
        self.assertNotIn("Generated from a single snapshot", markdown)

    def test_rev_markdown_omits_usd_parentheses_when_price_is_unavailable(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["economics"] = {"available": False}
        snapshot["activity"] = {
            "available": True,
            "window": {
                "blocks_sampled": 2,
                "estimated_blocks_in_window": 2,
                "first_slot": 1,
                "last_slot": 2,
                "first_block_time": 1_700_000_000,
                "last_block_time": 1_700_000_060,
                "observed_seconds": 60,
            },
            "fees": {"available": False},
            "addresses": {},
            "fee_split": {},
            "rev": {
                "available": True,
                "sample_mean_estimate_sol": 12.5,
                "method": "sample mean over the observed window",
                "limitation": "Temporal and endpoint sampling bias remain.",
                "sampled_sol": {
                    "message_signature_base_fee_lower_bound": 1.0,
                    "unclassified_fee_residual": 1.0,
                    "jito_tips": 0.5,
                    "total": 2.5,
                },
                "per_block_sol": {"min": 1.0, "max": 1.5, "mean": 1.25},
                "sample_mean_interval": {
                    "low_sol": 10.0,
                    "high_sol": 15.0,
                    "method": "descriptive interval",
                },
            },
        }

        markdown = render.render_markdown(snapshot)

        self.assertIn("12.50 SOL**", markdown)
        self.assertIn("descriptive interval 10–15 SOL", markdown)
        self.assertNotIn("(—)", markdown)
        self.assertNotIn("(—–—)", markdown)

    def test_renders_every_section(self):
        markdown = render.render_markdown(load_fixture())
        for heading in ("# Solana Ecosystem Report", "## Network", "## Epoch",
                        "## Performance", "## Supply", "## Validators"):
            self.assertIn(heading, markdown)

    def test_includes_real_values_from_the_snapshot(self):
        markdown = render.render_markdown(load_fixture())
        self.assertIn("4,213", markdown)     # TPS, thousands-separated
        self.assertIn("1,024", markdown)     # active validator count
        self.assertIn("no API key", markdown)

    def test_custom_rpc_access_requirements_are_not_claimed_keyless(self):
        snapshot = load_fixture()
        snapshot["source"].update({
            "endpoint": "custom RPC endpoint",
            "requires_api_key": None,
        })

        markdown = render.render_markdown(snapshot)
        html_output = render.render_html(snapshot)

        self.assertIn(
            "**Source:** custom RPC endpoint (recorded JSON-RPC source; access requirements unknown)",
            markdown,
        )
        self.assertIn(
            "custom RPC endpoint</code> · recorded JSON-RPC source; access requirements unknown",
            html_output,
        )
        self.assertNotIn("custom RPC endpoint (public JSON-RPC, no API key)", markdown)
        self.assertNotIn("custom RPC endpoint</code> · no API key required", html_output)

    def test_markdown_keeps_non_vote_epoch_eta_and_ranked_validator_parity(self):
        snapshot = load_fixture()
        snapshot["performance"].update({
            "non_vote_available": True, "latest_non_vote_tps": 2689.17,
            "mean_non_vote_tps": 2600.0, "mean_vote_share_pct": 38.14,
        })
        snapshot["epoch"].update({
            "estimated_remaining_seconds": 86_400,
            "estimated_end_at": "2026-08-06T00:00:00+00:00",
            "eta_basis": "mean recent slot time",
        })
        snapshot["validators"]["ranked_validators"] = [
            {"rank": 1, "identity": "node-A", "vote_account": "vote-A", "state": "current",
             "stake_sol": 17_000_000.0, "share_pct": 3.92, "commission": 7,
             "last_vote": 439_909_939, "root_slot": 439_909_908},
        ]
        markdown = render.render_markdown(snapshot)
        self.assertIn("| Latest non-vote TPS | 2,689.17 |", markdown)
        self.assertIn("| Estimated epoch remaining | 1d |", markdown)
        self.assertIn("| # | Node identity | Vote account | Stake (SOL) | Share | Commission | State | Last vote | Root slot |", markdown)
        self.assertIn("| 1 | node-A | vote-A | 17,000,000 | 3.92% | 7% | current | 439,909,939 | 439,909,908 |", markdown)

    def test_protocol_tvl_leaderboard_keeps_html_markdown_and_json_parity(self):
        snapshot = load_fixture()
        snapshot["economics"] = {
            "available": True,
            "protocols": {"available": True,
                "scope": "non-CEX protocols with reported Solana chain TVL",
                "excluded_categories": ["CEX"],
                "protocols": [
                    {"id": "1", "name": "Solana Lender", "slug": "lender",
                     "category": "Lending", "solana_tvl_usd": 900_000_000.0},
                ]},
            "price": {"available": False}, "tvl": {"available": False},
            "stablecoins": {"available": False}, "dex": {"available": False},
            "sources": {},
        }
        html_output = render.render_economics_html(snapshot)
        markdown = render.render_markdown(snapshot)
        self.assertIn("Top protocols by Solana TVL", html_output)
        self.assertIn("Solana Lender", html_output)
        self.assertIn("$900.0M", html_output)
        self.assertIn("### Top protocols by Solana TVL", markdown)
        self.assertIn("| 1 | Solana Lender | Lending | $900,000,000 |", markdown)

    def test_tokenized_equity_supply_is_visible_without_claiming_valuation_or_volume(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {
            "available": True,
            "daily_active_addresses": {"available": False, "reason": "source not proven"},
            "tokenized_equities": {
                "available": True, "registry_asset_count": 2, "eligible_asset_count": 2,
                "supply_observed_asset_count": 1, "fresh_supply_asset_count": 1,
                "valued_asset_count": 0, "displayed_asset_count": 1,
                "supply_coverage": {
                    "coverage_numerator": 1, "coverage_denominator": 2,
                    "fresh_asset_count": 1, "queried_this_run_asset_count": 1,
                    "successful_this_run_asset_count": 1, "failed_this_run_asset_count": 0,
                    "scope": "observed subset", "sweep_complete": False,
                    "oldest_observation_at": "2026-08-25T11:00:00+00:00",
                    "newest_observation_at": "2026-08-25T11:00:00+00:00",
                    "observation_span_seconds": 0,
                },
                "valuation": {"available": False, "scope": "unavailable",
                              "reason": "No cleared timestamped price source."},
                "volume": {"available": False, "reason": "No source"},
                "note": "Supply is not trading volume, issuer AUM, liquidity, reserves, or USD valuation.",
                "assets": [{"symbol": "NVDAx", "name": "NVIDIA xStock", "mint": "mint-nvda",
                            "supply": 321577.7386022,
                            "supply_raw_amount": "32157773860220", "supply_decimals": 8,
                            "supply_rpc_ui_amount_string": "321577.7386022",
                            "supply_context_slot": 439_900_000,
                            "supply_rpc_api_version": "2.3.7",
                            "supply_collected_at": "2026-08-25T11:00:00+00:00",
                            "supply_age_seconds": 100, "supply_freshness": "fresh",
                            "supply_source_method": "getTokenSupply(finalized)",
                            "basis": "finalized on-chain token supply"}],
            },
        }
        workbench = render.render_growth_workbench(snapshot, "desktop")
        markdown = render.render_markdown(snapshot)
        mobile = render.render_mobile_data(snapshot)
        self.assertIn("Tokenized equities", workbench)
        self.assertIn("NVDAx", workbench)
        self.assertIn("321,577.74", workbench)
        self.assertIn("1 / 2", workbench)
        self.assertIn("oldest 2026-08-25T11:00:00+00:00", workbench)
        self.assertIn("newest 2026-08-25T11:00:00+00:00", workbench)
        self.assertIn("No source", workbench)
        self.assertNotIn("trading volume unavailable", workbench.lower())
        self.assertIn("USD valuation unavailable", workbench)
        self.assertIn("## Ecosystem growth", markdown)
        self.assertIn("Tokenized-equity trading volume: unavailable — No source", markdown)
        self.assertIn(
            "<strong>Indexed Solana DEX-pool volume</strong>"
            "<span class='mobile-source-status'>Unavailable</span>"
            "<small>No source ·",
            mobile,
        )
        self.assertIn("Fresh · 100s", workbench)
        self.assertIn("RPC slot 439,900,000", workbench)
        self.assertIn("| NVDAx | NVIDIA xStock | mint-nvda | 321,577.7386022 | 32157773860220 | 8 | 439900000 / 2.3.7 |", markdown)
        self.assertIn("Supply observation bounds: oldest 2026-08-25T11:00:00+00:00 · newest 2026-08-25T11:00:00+00:00", markdown)
        for output in (workbench, markdown):
            self.assertNotIn("DeFiLlama Coins", output)
            self.assertNotIn("Issuance value", output)
            self.assertNotIn("$72.7M", output)

    def test_selected_stablecoin_supply_has_exact_html_markdown_json_and_catalog_parity(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        stablecoins = selected_stablecoins_fixture()
        snapshot["growth"] = {
            "available": True,
            "selected_usd_stablecoins": stablecoins,
            "daily_active_addresses": {"available": False},
            "daily_fee_payers": {"available": False},
            "tokenized_equities": {"available": False},
            "sources": {"selected_usd_stablecoins": {
                "available": True, "partial": False, "coverage_complete": True,
                "coverage_numerator": 4, "coverage_denominator": 4,
            }},
        }
        workbench = render.render_growth_workbench(snapshot, "desktop")
        markdown = render.render_markdown(snapshot)
        catalog = render.render_data_catalog(snapshot, None, None, [])
        mobile = render.render_mobile_data(snapshot)
        serialized = render.json_safe(snapshot)["growth"]["selected_usd_stablecoins"]

        for output in (workbench, markdown):
            self.assertIn("Selected USD-stablecoin supply", output)
            self.assertIn("10.00", output)
            self.assertIn("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", output)
            self.assertIn("not circulating supply", output)
        self.assertIn("Selected four-mint stablecoin total supply", catalog)
        self.assertIn("4 / 4 selected mints", catalog)
        self.assertIn("Selected four-mint stablecoin total supply", mobile)
        self.assertIn("4 / 4 selected mints", mobile)
        self.assertEqual(serialized["assets"][0]["raw_amount"], "100")
        self.assertEqual(serialized["assets"][0]["rpc_ui_amount_string"], "1")
        self.assertEqual(serialized["selected_total_supply_decimal"], "10.00")

    def test_partial_selected_stablecoin_supply_withholds_total_and_shares(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        stablecoins = selected_stablecoins_fixture(3)
        snapshot["growth"] = {
            "available": True,
            "selected_usd_stablecoins": stablecoins,
            "tokenized_equities": {"available": False},
            "sources": {"selected_usd_stablecoins": {
                "available": False, "partial": True, "coverage_complete": False,
                "coverage_numerator": 3, "coverage_denominator": 4,
            }},
        }
        outputs = (
            render.render_growth_workbench(snapshot, "desktop"),
            render.render_markdown(snapshot),
            render.render_data_catalog(snapshot, None, None, []),
            render.render_mobile_data(snapshot),
        )
        mobile_workbench = render.render_growth_workbench(snapshot, "mobile")
        for output in outputs:
            self.assertIn("3 / 4", output)
        self.assertIn("Combined total and shares are withheld", outputs[0])
        self.assertIn("combined total and shares are withheld", outputs[1])
        self.assertIn("Combined total and shares withheld.", mobile_workbench)
        self.assertIn("USD-stablecoin supply</h3></div><small>Partial</small>", mobile_workbench)
        self.assertNotIn("Combined total withheld nominal units", mobile_workbench)
        self.assertNotIn("Selected-list total:", outputs[0])
        self.assertNotIn("Selected-list total:", outputs[1])
        self.assertNotIn("selected_total_supply_decimal", stablecoins)
        self.assertTrue(all(
            "share_of_selected_total" not in asset for asset in stablecoins["assets"]
        ))

    def test_growth_workbench_labels_partial_dex_volume_and_provider_activity_range(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {
            "available": True,
            "sources": {
                "registry": {"available": True, "coverage_complete": True,
                             "url": "https://raw.githubusercontent.com/solana-foundation/tokens/661a6f0ca466ccf74ea967dae7e3abbcdc088bc0/packages/asset-registry/src/data/xstock-variant-groups.ts",
                             "kind": "pinned official token registry"},
                "supply": {"available": True, "sweep_complete": False,
                           "coverage_numerator": 7, "coverage_denominator": 10,
                           "fresh_asset_count": 5,
                           "queried_this_run_asset_count": 7,
                           "successful_this_run_asset_count": 7},
                "dex_volume": {"available": True, "transport_complete": True,
                               "market_coverage": "partial"},
                "proof_of_reserves": {"available": True, "coverage_complete": True},
                "activity_benchmark": {"available": True, "canonical": False},
            },
            "daily_active_addresses": {
                "available": True, "canonical": False, "date": "2026-08-21",
                "provider_count": 2, "minimum": 100, "maximum": 200,
                "median": 150,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "display_name": "Stablecoin active-address provider range",
                "source_label": "Active Addresses",
                "scope": "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
            },
            "daily_fee_payers": {
                "available": True, "canonical": False, "date": "2026-08-21",
                "provider_count": 2, "minimum": 300, "maximum": 500,
                "median": 400,
                "semantic_metric_id": "transaction_initiator_provider_range",
                "display_name": "Transaction-initiator provider range",
                "source_label": "Fee Payers",
                "scope": "provider observations of transaction initiators, not unique humans",
            },
            "tokenized_equities": {
                "available": True, "registry_asset_count": 10, "eligible_asset_count": 10,
                "displayed_asset_count": 5, "supply_observed_asset_count": 7,
                "fresh_supply_asset_count": 5, "valued_asset_count": 0,
                "supply_coverage": {
                    "coverage_numerator": 7, "coverage_denominator": 10,
                    "fresh_asset_count": 5, "queried_this_run_asset_count": 7,
                    "successful_this_run_asset_count": 7,
                    "failed_this_run_asset_count": 0, "scope": "observed subset",
                    "sweep_complete": False,
                    "oldest_observation_at": "2026-08-24T12:00:00+00:00",
                    "newest_observation_at": "2026-08-25T12:00:00+00:00",
                    "observation_span_seconds": 86_400,
                },
                "valuation": {"available": False, "reason": "No cleared timestamped price source."},
                "volume": {
                    "available": True, "volume_24h_usd": 123_456.78,
                    "assets_with_pairs": 5, "pair_count": 5,
                    "volume_covered_pair_count": 5,
                    "invalid_row_count": 0, "conflicting_pair_count": 0,
                    "batches_requested": 2, "batches_succeeded": 2,
                    "transport_complete": True, "market_coverage": "partial",
                    "exclusions": ["RFQ fills", "centralized venues", "unindexed or unsupported pools"],
                    "limitations": "Tracked DEX pools only; excludes RFQ fills, centralized venues, and unsupported pools.",
                },
                "proof_of_reserves": {"available": True, "asset_count": 9},
                "note": "Synthetic supply coverage contains seven assets; five are shown.",
                "assets": [],
            },
        }
        workbench = render.render_growth_workbench(snapshot, "desktop")
        markdown = render.render_markdown(snapshot)
        catalog = render.render_data_catalog(snapshot, None, None, [])
        mobile = render.render_mobile_data(snapshot)
        self.assertIn("Indexed DEX-pool volume", workbench)
        self.assertIn("$123.5K", workbench)
        exact_dex_evidence = (
            "5 assets · volume coverage 5 / 5 pools · 0 invalid rows · "
            "0 conflicting pair identities · batches 2 succeeded / 2 attempted · "
            "transport complete · market coverage partial"
        )
        self.assertIn(exact_dex_evidence, workbench)
        self.assertIn("Stablecoin active-address provider range", workbench)
        self.assertIn("100–200", workbench)
        self.assertIn("provider benchmark", workbench)
        self.assertIn("Indexed Solana DEX-pool volume (24h): $123,456.78", markdown)
        self.assertIn("Stablecoin active-address provider range (source-labelled ‘Active Addresses’): 100–200", markdown)
        self.assertIn("Transaction-initiator provider range (source-labelled ‘Fee Payers’): 300–500", markdown)
        self.assertIn("not total market volume", markdown)
        self.assertIn(exact_dex_evidence, markdown)
        self.assertIn("raw.githubusercontent.com/solana-foundation/tokens", catalog)
        self.assertIn("7 / 10 within 72h · 5 fresh · 7 successful / 7 queried this run", catalog)
        self.assertIn("oldest 2026-08-24T12:00:00+00:00 · newest 2026-08-25T12:00:00+00:00", catalog)
        self.assertIn("USD valuation unavailable", catalog)
        self.assertIn(exact_dex_evidence, catalog)
        self.assertIn(
            "<strong>Indexed Solana DEX-pool volume</strong>"
            "<span class='mobile-source-status'>Partial</span>"
            f"<small>{exact_dex_evidence} ·",
            mobile,
        )
        self.assertIn("xStocks API held pending source-rights clarification", catalog)
        self.assertIn("Not collected or republished", catalog)
        self.assertIn("2 stablecoin-address providers · 2 transaction-initiator providers", catalog)
        self.assertNotIn("xstocks.fi/products", catalog)
        self.assertNotIn("declared watchlist", catalog)

    def test_growth_markdown_keeps_independent_sources_when_issuance_is_unavailable(self):
        snapshot = load_fixture()
        snapshot["growth"] = {
            "available": True,
            "daily_active_addresses": {"available": False},
            "daily_fee_payers": {
                "available": True, "date": "2026-08-21", "provider_count": 4,
                "minimum": 2_000_000, "maximum": 3_000_000,
            },
            "tokenized_equities": {
                "available": False, "registry_asset_count": 10,
                "volume": {
                    "available": True, "volume_24h_usd": 100_000,
                    "assets_with_pairs": 10, "pair_count": 12,
                },
                "proof_of_reserves": {"available": True, "asset_count": 9},
                "assets": [],
            },
        }
        markdown = render.render_markdown(snapshot)
        self.assertIn("Tokenized-equity supply: unavailable", markdown)
        self.assertIn("Indexed Solana DEX-pool volume (24h): $100,000", markdown)
        self.assertIn("Issuer proof-of-reserves coverage: 9 assets", markdown)
        self.assertIn("Legacy provider activity semantics are not comparable", markdown)
        self.assertNotIn("Transaction-initiator provider range (source-labelled ‘Fee Payers’): 2,000,000–3,000,000", markdown)

    def test_legacy_provider_activity_never_receives_corrected_schema8_labels(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {
            "available": True,
            "sources": {"activity_benchmark": {
                "available": True, "active_addresses_available": True,
                "fee_payers_available": True,
            }},
            "daily_active_addresses": {
                "available": True, "date": "2026-08-21", "provider_count": 2,
                "minimum": 100, "maximum": 200,
            },
            "daily_fee_payers": {
                "available": True, "date": "2026-08-21", "provider_count": 2,
                "minimum": 300, "maximum": 400,
            },
            "tokenized_equities": {"available": False},
        }
        outputs = (
            render.render_markdown(snapshot),
            render.render_growth_workbench(snapshot, "desktop"),
            render.render_data_catalog(snapshot, None, None, []),
            render.render_mobile_data(snapshot),
        )
        for output in outputs:
            self.assertIn("Legacy provider activity semantics", output)
            self.assertNotIn("100–200", output)
            self.assertNotIn("300–400", output)

    def test_native_inflation_is_visible_in_markdown_and_data_catalog(self):
        snapshot = load_fixture()
        snapshot["inflation"] = {
            "available": True, "current_total_pct": 4.12,
            "current_validator_pct": 4.12, "current_foundation_pct": 0.0,
            "epoch": 1021, "initial_pct": 8.0, "terminal_pct": 1.5,
            "taper_pct": 15.0, "foundation_pct": 5.0,
            "foundation_term_years": 7.0,
        }
        markdown = render.render_markdown(snapshot)
        catalog = render.render_data_catalog(snapshot, None, None, [])
        self.assertIn("## Inflation", markdown)
        self.assertIn("| Current total rate | 4.12% |", markdown)
        self.assertIn("| Terminal rate | 1.50% |", markdown)
        self.assertIn("Inflation policy", catalog)
        self.assertIn("getInflationRate + getInflationGovernor", catalog)
        self.assertIn("4.12% current · 1.50% terminal", catalog)

    def test_schema8_stablecoin_headline_uses_usd_pegged_circulating_contract(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["economics"] = {
            "available": True,
            "price": {"available": False}, "tvl": {"available": False},
            "dex": {"available": False}, "protocols": {"available": False},
            "sources": {},
            "stablecoins": {
                "available": True,
                "metric": "USD-pegged circulating supply",
                "provider_field": "totalCirculatingUSD.peggedUSD",
                "scope": "Provider-reported circulating supply of USD-pegged assets on Solana; not all stablecoins, liquidity, or executable depth.",
                "usd_pegged_circulating_usd": 12_345_678.90,
                "non_usd_pegged_usd": 12_345.67,
            },
        }
        outputs = (
            render.render_markdown(snapshot),
            render.render_economics_html(snapshot),
            render.render_mobile_data(snapshot),
        )
        for output in outputs:
            self.assertIn("USD-pegged circulating supply", output)
            self.assertIn("composition unavailable", output)
            self.assertNotIn("Stablecoin supply", output)
        self.assertIn("$12,345,678.90", outputs[0])

    def test_schema9_full_render_retains_additive_schema8_data_contracts(self):
        snapshot = editorial_fixture()
        snapshot["schema_version"] = 9
        selected = {
            "metric_id": "selected_usd_stablecoin_total_supply",
            "coverage_numerator": 0,
            "coverage_denominator": 4,
            "assets": [],
            "available": False,
            "state": "withheld",
        }
        snapshot.setdefault("growth", {})["selected_usd_stablecoins"] = selected

        page = render.render_html(snapshot)

        self.assertTrue(render.corrected_activity_contract(snapshot))
        self.assertIs(render.selected_stablecoin_contract(snapshot), selected)
        self.assertIn("Selected USD-stablecoin supply", page)
        self.assertEqual(page.count("data-project-editorial>"), 2)

    def test_degraded_snapshot_says_unavailable_rather_than_zero(self):
        degraded = {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "https://example.invalid"},
            "network": {"healthy": None, "health_raw": "unavailable"},
            "epoch": {"available": False},
            "performance": {"available": False},
            "supply": {"available": False},
            "validators": {"available": False},
            "economics": {"available": False},
        }
        markdown = render.render_markdown(degraded)
        # Every data section must name itself unavailable rather than print 0.
        for section in ("Epoch", "Performance", "Supply", "Validator", "Economic"):
            self.assertRegex(markdown, rf"_{section}[^_]*unavailable[^_]*_")
        self.assertIn("**RPC endpoint health:** unavailable", markdown)
        self.assertNotIn("| 0 |", markdown)

    def test_every_recorded_string_leaf_is_neutralised_in_markdown(self):
        payload = "<svg/onload=alert('markdown-probe')>"

        def string_paths(value, prefix=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from string_paths(item, prefix + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from string_paths(item, prefix + (index,))
            elif isinstance(value, str):
                yield prefix

        def replace_at(value, path, replacement):
            cursor = value
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = replacement

        fixture = load_fixture()
        for path in string_paths(fixture):
            hostile = json.loads(json.dumps(fixture))
            replace_at(hostile, path, payload)
            markdown = render.render_markdown(hostile)
            self.assertNotIn(payload, markdown, path)

    def test_every_recorded_numeric_leaf_degrades_without_markdown_crashes(self):
        payload = "<b>not a number</b>"

        def numeric_paths(value, prefix=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from numeric_paths(item, prefix + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from numeric_paths(item, prefix + (index,))
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                yield prefix

        def replace_at(value, path, replacement):
            cursor = value
            for part in path[:-1]:
                cursor = cursor[part]
            cursor[path[-1]] = replacement

        fixture = load_fixture()
        for path in numeric_paths(fixture):
            malformed = json.loads(json.dumps(fixture))
            replace_at(malformed, path, payload)
            markdown = render.render_markdown(malformed)
            self.assertNotIn(payload, markdown, path)


class TestHtml(unittest.TestCase):
    def test_is_a_self_contained_document(self):
        page = render.render_html(load_fixture())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)
        self.assertIn("<style>", page)
        self.assertIn('<link rel="icon" href="data:,">', page)
        # No build step, no CDN, no external asset of any kind.
        self.assertNotIn("<script src=", page)
        self.assertNotIn("https://cdn", page)

    def test_the_page_fetches_no_subresource_of_any_kind(self):
        """Charts, badges and feeds must not have introduced an external asset.

        Anchors to primary sources are fine and wanted — they are citations a
        reader follows deliberately. What must never appear is a tag the
        browser fetches on its own: without the network, or from a file:// URL,
        the page has to draw itself completely.
        """
        page = render.render_html(load_fixture())
        non_fetching_links = (
            '<link rel="icon" href="data:,">',
            '<link rel="alternate" type="application/json" title="Recorded report data" href="report.json">',
            '<link rel="alternate" type="text/markdown" title="Readable report" href="report.md">',
        )
        page_without_inline_icon = page
        for link in non_fetching_links:
            page_without_inline_icon = page_without_inline_icon.replace(link, "")
        for tag in ("<script src", "<script type=", "<link", "<img", "<iframe", "<object", "<embed",
                    "<use", "<image"):
            self.assertNotIn(tag, page_without_inline_icon)
        self.assertEqual(page.count("<script data-mobile-controller>"), 1)
        for runtime_network_api in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource"):
            self.assertNotIn(runtime_network_api, page)
        self.assertNotIn("@import", page)
        # Render-time embedded assets are data URIs. Remote and relative url()
        # fetches remain forbidden.
        for match in re.findall(r'url\(([^)]+)\)', page):
            self.assertTrue(
                match.strip("\"' ").startswith(
                    ("data:font/woff", "data:image/png", "data:image/webp")
                ),
                f"unexpected css url: {match}",
            )
        self.assertNotIn("srcset", page)

    def test_embedded_archivo_asset_is_pinned_and_licensed(self):
        root = render.ARCHIVO_PATH.parent.parent
        font = render.ARCHIVO_PATH.read_bytes()
        license_path = root / "LICENSES" / "OFL-1.1-Archivo.txt"
        license_text = license_path.read_text(encoding="utf-8")
        notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        page = render.render_html(load_fixture())
        self.assertEqual(hashlib.sha256(font).hexdigest(), render.ARCHIVO_SHA256)
        self.assertEqual(
            hashlib.sha256(license_path.read_bytes()).hexdigest(),
            "1778201b7bd33e8c08a2eda32a4ad2f69bc38ced9731b01cc3fc47f268c8ef3c",
        )
        self.assertIn(render.ARCHIVO_SHA256, notice)
        self.assertIn("b5d63988ce19d044d3e10362de730af00526b672", notice)
        self.assertIn("fonts/variable/Archivo[wdth,wght].ttf", notice)
        self.assertIn("derived subset/re-encoded", notice)
        self.assertIn("derivation recipe is `UNKNOWN`", notice)
        self.assertIn("terminal space is normalized", notice)
        self.assertIn("SIL Open Font License 1.1", notice)
        self.assertIn("id='artifact-notices'", page)
        self.assertIn("Embedded font licence", page)
        self.assertIn(render.html.escape(license_text), page)
        self.assertIn("AI-generated presentation illustrations", page)

    def test_editorial_system_theme_is_applied(self):
        page = render.render_html(load_fixture())
        self.assertIn('<html lang="en" data-theme="system">', page)
        self.assertIn("--prototype-canvas: light-dark(#ffffff, #09090b)", page)
        self.assertIn(":root[data-theme='dark'] { color-scheme: dark; }", page)
        self.assertIn(":root[data-theme='system'] { color-scheme: light dark; }", page)
        self.assertEqual(page.count("<details class='theme-menu"), 2)
        self.assertEqual(page.count("data-theme-option='light'"), 2)
        self.assertEqual(page.count("aria-label='Color theme: System'"), 2)
        self.assertEqual(page.count("data-theme-option='system' aria-pressed='true'"), 2)
        self.assertIn("solana-report-theme", page)
        self.assertNotIn("--bg: #f7f6f2", page)
        self.assertIn("Solana Ecosystem Report", page)
        self.assertIn("Overview", page)

    def test_theme_storage_clear_or_invalid_value_synchronizes_to_system(self):
        page = render.render_html(load_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("const next = themeValues.has(theme) ? theme : 'system';", controller)
        self.assertIn(
            "if (event.key === THEME_KEY) applyTheme(event.newValue, false);",
            controller,
        )
        self.assertIn("event.key !== 'Escape'", controller)
        self.assertIn("optionMenu.open = false;", controller)
        self.assertIn("optionMenu.querySelector('summary').focus();", controller)
        self.assertIn("`Color theme: ${themeLabels[next]}`", controller)
        self.assertNotIn("event.key === THEME_KEY && themeValues.has(event.newValue)", controller)

    def test_overview_preserves_the_original_carousel_composition(self):
        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertNotIn("class='evidence-ribbon", page)
        self.assertNotIn("<span aria-hidden='true'>Solana Report</span>", page)
        mobile = render.render_mobile_overview(fixture, None, [fixture], "Latest")
        self.assertLess(mobile.index("Network pulse"), mobile.index("Key signals"))
        self.assertEqual(mobile.count("Latest TPS"), 1)
        self.assertNotIn(".prototype-page--report .chart-card:first-child { grid-column: span 2; }", render.CSS)
        self.assertNotIn(".prototype-page--report .pulse-dots { display: none !important; }", render.CSS)
        self.assertNotIn(".prototype-page--report .metrics { grid-template-columns:repeat(6,minmax(0,1fr)); }", render.CSS)
        self.assertIn("scroll-snap-type: x mandatory", render.CSS)
        self.assertIn("data-pulse-previous", page)
        self.assertIn("data-pulse-next", page)

    def test_includes_explicit_ui_test_states_without_replacing_real_content(self):
        page = render.render_html(load_fixture())
        self.assertIn("class='ui-state-surface'", page)
        self.assertIn("<h1 id='ui-state-title' data-ui-state-title tabindex='-1'>Overview</h1>", page)
        self.assertIn("data-ui-state-skeleton", page)
        self.assertIn("data-ui-state-empty", page)
        self.assertIn("data-ui-state-error", page)
        self.assertIn("new URLSearchParams(location.search)", page)
        self.assertIn("['loading', 'empty', 'error']", page)
        self.assertIn("UI test state · not live data", page)
        self.assertIn("4,213", page)

    def test_ui_test_states_hide_the_fixed_ticker_and_keep_a_safe_top_inset(self):
        page = render.render_html(load_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("body[data-ui-state] .report-ticker { display: none; }", render.CSS)
        self.assertIn(
            "body[data-ui-state] .ui-state-surface { padding-top: max(18px, env(safe-area-inset-top)); }",
            render.CSS,
        )
        self.assertIn(
            ".ui-state-head h1[tabindex='-1']:focus { outline: 2px solid var(--prototype-violet)",
            render.CSS,
        )
        self.assertIn(
            "if (document.body.dataset.uiState || !mobileViewport.matches || !mobileTopbar)",
            controller,
        )

    def test_mobile_routes_share_one_product_status_header(self):
        page = render.render_html(load_fixture())
        self.assertIn("class='mobile-topbar'", page)
        self.assertNotIn("body:has(#data:target) .mobile-topbar { display:none", page)
        self.assertNotIn("body:has(#history:target) .mobile-topbar { display:none", page)
        self.assertNotIn("class='mobile-data-offline'", page)
        self.assertNotIn("class='mobile-history-offline'", page)

    def test_primary_views_keep_the_compact_prototype_information_architecture(self):
        page = render.render_html(load_fixture())
        for removed_extension in (
            "Recorded network context",
            "All recorded series",
            "Delta thresholds &amp; verification",
        ):
            self.assertNotIn(removed_extension, page)
        self.assertIn("<details class='data-appendix'>", page)
        self.assertNotIn("<details class='data-appendix' open", page)
        for prototype_section in (
            "Network pulse · recorded history",
            "Data catalog",
            "Measured vs sampled",
            "Comparing snapshots",
            "Key metric deltas",
            "About this report",
            "Current snapshot",
        ):
            self.assertIn(prototype_section, page)

    def test_overview_charts_use_the_prototype_card_grammar_with_real_data(self):
        history = recorded_history_fixture()
        charts = render.render_overview_charts(history)
        wanted = {
            "latest_tps", "latest_non_vote_tps", "mean_slot_time_secs", "delinquent_pct", "price_usd",
            "tvl_usd", "median_fee_lamports", "sample_mean_rev_sol",
        }
        specs = [spec for spec in render.charts_module.SERIES if spec["key"] in wanted]
        stats = {
            spec["key"]: render.charts_module.series_stats(
                render.charts_module.extract(history, spec), gap_factor=float("inf"))
            for spec in specs
        }
        chartable = [spec for spec in specs if stats[spec["key"]]["chartable"]]
        chartable_cards = [
            spec for spec in chartable if spec["key"] != "latest_non_vote_tps"
        ]
        titles = {
            "latest_tps": "Total Transactions / Second",
            "latest_non_vote_tps": "Non-vote Transactions / Second",
            "mean_slot_time_secs": "Avg. Slot Time",
            "delinquent_pct": "Validator Delinquency",
            "price_usd": "SOL Price",
            "tvl_usd": "Total Value Locked",
            "median_fee_lamports": "Median Fee (non-vote)",
            "sample_mean_rev_sol": "REV over observed window",
        }
        for spec in chartable:
            self.assertIn(titles[spec["key"]], charts)
        for key in ("price_usd", "tvl_usd"):
            if not stats[key]["chartable"]:
                self.assertNotIn(f"data-pulse-key='{key}'", charts)
        self.assertEqual(charts.count("viewBox='0 0 300 86' preserveAspectRatio='none'"), len(chartable_cards))
        self.assertEqual(charts.count("class='grid-line'"), len(chartable_cards) * 3)
        self.assertEqual(charts.count("class='axis-label'"), len(chartable_cards) * 3)
        self.assertEqual(charts.count("class='chart-time-range'"), len(chartable_cards))
        # A card can contain more than one honest segment when history has a gap.
        self.assertGreaterEqual(charts.count("<polyline points="), len(chartable_cards))
        self.assertEqual(charts.count("data-window='Recorded'"),
                         sum(spec["basis"] != "sampled" for spec in chartable_cards))
        self.assertEqual(charts.count("data-window='Sampled'"),
                         sum(spec["basis"] == "sampled" for spec in chartable_cards))
        self.assertEqual(charts.count("aria-roledescription='slide'"), len(chartable_cards))
        self.assertIn("class='chart-carousel'", charts)
        self.assertIn("data-pulse-track", charts)
        self.assertIn("data-pulse-previous", charts)
        self.assertIn("data-pulse-next", charts)
        self.assertEqual(charts.count("data-pulse-dot="), len(chartable_cards))
        rev_stats = stats["sample_mean_rev_sol"]
        if rev_stats["chartable"]:
            self.assertIn("data-pulse-key='sample_mean_rev_sol'", charts)
        else:
            self.assertIn(
                f"REV over observed window (sample mean): {rev_stats['points']} usable observation(s)",
                charts,
            )

        self.assertNotIn("snapshots'>", charts)
        self.assertNotIn("measured · gaps preserved", charts)
        self.assertNotIn("Coverage and source", charts)
        overview_specs = [spec for spec in specs if spec["key"] != "latest_non_vote_tps"]
        unavailable_count = len(overview_specs) - len(chartable_cards)
        if unavailable_count:
            self.assertIn("chart-disclosure--availability", charts)
            self.assertIn(
                f"<summary>{unavailable_count} charts unavailable — see why (compact disclosure)</summary>", charts,
            )
        else:
            self.assertNotIn("chart-disclosure--availability", charts)

    def test_overview_history_overlays_total_and_non_vote_tps_distinctly(self):
        history = recorded_history_fixture()
        for index, snapshot in enumerate(history):
            snapshot["performance"].update({
                "non_vote_available": True,
                "latest_non_vote_tps": 1_500.0 + index * 5,
            })

        markup = render.render_overview_charts(history)

        self.assertIn("data-pulse-key='latest_tps'", markup)
        self.assertNotIn("data-pulse-key='latest_non_vote_tps'", markup)
        self.assertIn("data-overview-tps-overlay", markup)
        self.assertIn("Total Transactions / Second", markup)
        self.assertIn("Non-vote Transactions / Second", markup)
        self.assertIn("chart-series--total", markup)
        self.assertIn("chart-series--non-vote", markup)
        self.assertIn(
            ".prototype-page--report .chart-series--non-vote polyline",
            render.CSS,
        )

    def test_schema9_single_snapshot_keeps_overview_carousel_from_real_samples(self):
        snapshot = single_snapshot_schema9_fixture()
        charts = render.render_overview_charts([snapshot])

        self.assertIn("aria-label='Current snapshot performance samples'", charts)
        self.assertNotIn("No history chart yet", charts)
        self.assertEqual(charts.count("data-pulse-card"), 4)
        self.assertEqual(charts.count("data-overview-chart-point"), 16)
        for index in range(1, 5):
            self.assertIn(f"aria-label='{index} of 4:", charts)
        for title in (
            "Transactions / Second", "Non-vote Transactions / Second",
            "Sampled Slot Time", "Vote Share",
        ):
            self.assertIn(title, charts)
        self.assertIn("within-snapshot sample window", charts)
        self.assertIn("slot 302400000", charts)

    def test_schema9_single_snapshot_sample_carousel_binds_slot_observations(self):
        snapshot = single_snapshot_schema9_fixture()
        observations = render.facts_module.public_observation_records(snapshot)
        indexes = render.public_observation_indexes(observations)
        charts = render.render_overview_charts([snapshot], indexes)
        sample_ids = {
            record["observation_id"] for record in observations
            if record["metric_id"].startswith("performance_sample_")
            and record["observed_slot"] is not None
        }

        bound_ids = set(re.findall(r"data-observation-id='([^']+)'", charts))
        self.assertEqual(len(bound_ids), 16)
        self.assertTrue(bound_ids <= sample_ids)
        self.assertEqual(charts.count("data-observation-id="), 16)

    def available_economics_history(self):
        history = recorded_history_fixture()
        available = next(
            snapshot["economics"] for snapshot in reversed(history[:-1])
            if snapshot.get("economics", {}).get("available")
        )
        history[-1]["economics"] = deepcopy(available)
        return history

    def test_overview_availability_disclosure_deduplicates_a_shared_reason(self):
        history = recorded_history_fixture()
        charts = render.render_overview_charts(history)
        plain = render.html.unescape(charts)
        reason = render.facts_module.ECONOMICS_PUBLICATION_HOLD

        self.assertIn("<summary>2 charts unavailable — see why (compact disclosure)</summary>", charts)
        self.assertIn("<li>SOL price</li><li>Total value locked</li>", charts)
        self.assertEqual(plain.count(reason), 1)
        self.assertNotIn("Not charted, and not drawn as zero", charts)

    def test_overview_availability_disclosure_handles_zero_and_one_metric(self):
        history = self.available_economics_history()
        self.assertNotIn(
            "chart-disclosure--availability", render.render_overview_charts(history),
        )

        history[-1]["economics"]["price"].update({
            "publication_state": "withheld",
            "reason": "Price <unsafe> & unavailable",
        })
        charts = render.render_overview_charts(history)
        self.assertIn("<summary>1 chart unavailable — see why (compact disclosure)</summary>", charts)
        self.assertIn("<li>SOL price</li>", charts)
        self.assertIn("Price &lt;unsafe&gt; &amp; unavailable", charts)
        self.assertNotIn("Price <unsafe>", charts)

    def test_overview_availability_keeps_different_reasons_with_their_metrics(self):
        history = self.available_economics_history()
        history[-1]["economics"]["price"].update({
            "publication_state": "withheld",
            "reason": "Price publication held",
        })
        for snapshot in history:
            rev = snapshot.get("activity", {}).get("rev", {})
            rev.pop("sample_mean_estimate_sol", None)

        charts = render.render_overview_charts(history)
        self.assertIn("<summary>2 charts unavailable — see why (compact disclosure)</summary>", charts)
        self.assertIn(
            "<li><strong>SOL price:</strong> Price publication held</li>", charts,
        )
        self.assertIn(
            "<li><strong>REV over observed window (sample mean):</strong> "
            "0 usable observation(s); at least two required</li>",
            charts,
        )

    def test_overview_renders_one_idless_availability_disclosure_per_variant(self):
        history = recorded_history_fixture()
        page = render.render_html(history[-1], history=history)
        tags = re.findall(
            r"<details class='chart-disclosure chart-disclosure--availability'[^>]*>",
            page,
        )
        self.assertEqual(len(tags), 2)
        self.assertFalse(any(" id=" in tag for tag in tags))

    def test_overview_availability_disclosure_has_mobile_and_focus_contracts(self):
        self.assertIn(".chart-disclosure--availability > summary", render.CSS)
        self.assertIn("margin: 12px var(--pulse-edge) 0", render.CSS)
        self.assertIn("min-height: 44px;", render.CSS)
        self.assertIn(
            ".chart-disclosure--availability > summary:focus-visible", render.CSS,
        )
        self.assertIn(
            ".mobile-network-pulse .chart-disclosure--availability { margin-inline:16px; }",
            render.CSS,
        )

    def test_network_pulse_carousel_is_progressively_enhanced_and_keyboard_operable(self):
        page = render.render_html(load_fixture(), history=[load_fixture(), load_fixture()])
        self.assertIn("data-pulse-controls hidden", page)
        self.assertIn("pulseTrack.addEventListener('keydown'", page)
        self.assertIn("pulsePrevious.addEventListener('click'", page)
        self.assertIn("pulseNext.addEventListener('click'", page)
        self.assertIn("pulseTrack.scrollTo", page)
        self.assertIn("scroll-snap-type: x mandatory", render.CSS)
        self.assertIn("grid-auto-flow: column", render.CSS)

    def test_network_instruments_compare_total_and_non_vote_tps_with_bounded_epoch(self):
        snapshot = load_fixture()
        snapshot["performance"].update({
            "latest_tps": 200.0,
            "latest_non_vote_tps": 150.0,
            "mean_vote_share_pct": 25.0,
            "non_vote_available": True,
            "samples": [
                {"slot": 103, "tps": 200.0, "non_vote_tps": 150.0, "vote_tps": 50.0},
                {"slot": 102, "tps": 180.0, "non_vote_tps": 130.0, "vote_tps": 50.0},
                {"slot": 101, "tps": 160.0, "non_vote_tps": 120.0, "vote_tps": 40.0},
            ],
        })
        snapshot["epoch"].update({
            "epoch": 700, "slot_index": 216_000, "slots_in_epoch": 432_000,
            "progress_pct": 50.0, "estimated_remaining_seconds": 86_400,
            "estimated_end_at": "2026-08-06T00:00:00+00:00",
            "eta_basis": "mean recent slot time",
        })
        instruments = render.render_network_instruments(snapshot, "desktop")
        self.assertIn("data-network-instruments", instruments)
        self.assertIn("Total TPS", instruments)
        self.assertIn("Non-vote TPS", instruments)
        self.assertIn("200", instruments)
        self.assertIn("150", instruments)
        self.assertIn("Vote share 25%", instruments)
        self.assertIn("aria-label='Epoch 700 progress: 50%'", instruments)
        self.assertIn("1d remaining", instruments)
        self.assertIn("class='throughput-point' tabindex='0'", instruments)
        self.assertIn("<details class='throughput-data'>", instruments)
        self.assertIn("<caption>Exact recent throughput samples</caption>", instruments)
        self.assertNotIn("<div class='visually-hidden'><table>", instruments)

    def test_network_instruments_never_invent_non_vote_tps_when_rpc_field_is_missing(self):
        snapshot = load_fixture()
        snapshot["performance"].update({
            "non_vote_available": False,
            "latest_non_vote_tps": None,
            "samples": [{"slot": 101, "tps": 160.0, "non_vote_tps": None}],
        })
        instruments = render.render_network_instruments(snapshot, "desktop")
        self.assertIn("Non-vote split unavailable", instruments)
        self.assertNotIn(">0<", instruments)

    def test_network_instruments_omit_epoch_progress_when_unavailable(self):
        snapshot = load_fixture()
        for stale_progress in (None, 50.0):
            with self.subTest(stale_progress=stale_progress):
                snapshot["epoch"] = {
                    "available": False,
                    "epoch": 700,
                    "progress_pct": stale_progress,
                }
                instruments = render.render_network_instruments(snapshot, "desktop")
                epoch = instruments.split("epoch-instrument", 1)[1]
                self.assertIn("progress unavailable", epoch)
                self.assertNotIn("<progress", epoch)
                self.assertNotIn("progress: 50%", epoch)

    def test_network_instruments_ship_in_desktop_and_mobile_overviews_with_unique_ids(self):
        snapshot = load_fixture()
        snapshot["performance"].update({
            "non_vote_available": True,
            "latest_non_vote_tps": 150.0,
            "mean_vote_share_pct": 25.0,
            "samples": [
                {"slot": 101, "tps": 200.0, "non_vote_tps": 150.0, "vote_tps": 50.0},
            ],
        })
        page = render.render_html(snapshot, history=[snapshot, snapshot])
        self.assertEqual(page.count("data-network-instruments"), 2)
        self.assertEqual(page.count("id='desktop-network-instruments-title'"), 1)
        self.assertEqual(page.count("id='mobile-network-instruments-title'"), 1)

    def test_mobile_throughput_preserves_native_chart_geometry_and_two_row_header(self):
        self.assertIn(".prototype-page--report .throughput-instrument { overflow: visible; }", render.CSS)
        self.assertIn(
            ".mobile-briefing .throughput-instrument .instrument-card__head > div:first-child {\n        display:grid;\n        min-width:0;\n        gap:2px;\n        align-content:start;\n        white-space:normal;\n      }",
            render.CSS,
        )
        self.assertIn(
            ".mobile-briefing .throughput-instrument .instrument-card__head > div:first-child strong { font-size:15px; line-height:1.15; }",
            render.CSS,
        )
        self.assertIn(
            ".mobile-briefing .throughput-now span { display:grid; grid-template-columns:16px minmax(0,1fr) max-content; align-items:baseline; column-gap:5px; }",
            render.CSS,
        )
        self.assertIn(".mobile-briefing .throughput-now b { justify-self:end; }", render.CSS)
        self.assertIn(".mobile-briefing .throughput-chart-wrap { padding:10px 12px 0 8px; }", render.CSS)
        self.assertNotIn(".mobile-briefing .throughput-chart { height:198px; }", render.CSS)
        self.assertIn(".mobile-briefing .throughput-guides text { fill:var(--prototype-muted); font-size:10px; }", render.CSS)
        self.assertIn(".instrument-grid", render.CSS)
        self.assertIn(".epoch-gauge__value", render.CSS)

    def test_every_overview_chart_has_rich_pointer_and_keyboard_inspection(self):
        history = recorded_history_fixture()
        page = render.render_html(history[-1], history=history)
        charts = render.render_overview_charts(history)
        chart_count = charts.count("data-pulse-card")
        self.assertEqual(charts.count("data-overview-chart "), chart_count)
        self.assertGreaterEqual(charts.count("data-basis='sampled'"), 1)
        self.assertGreater(charts.count("data-inspector-basis="), 100)
        self.assertEqual(charts.count("class='chart-inspector'"), chart_count)
        self.assertGreater(charts.count("data-overview-chart-point"), 100)
        self.assertIn("overviewCharts.forEach", page)
        self.assertIn("event.key === 'ArrowRight'", page)
        self.assertIn(".prototype-page--report .sparkline .chart-observation,\n      .prototype-page--report .throughput-point,", render.CSS)
        self.assertIn("chart.addEventListener('pointerleave', rest);", page)
        self.assertNotIn("if (guide) guide.hidden = true;", page)

    def test_throughput_hover_guide_defaults_to_the_latest_sample(self):
        snapshot = load_fixture()
        snapshot["performance"].update({
            "non_vote_available": True,
            "latest_non_vote_tps": 150.0,
            "mean_vote_share_pct": 25.0,
            "samples": [
                {"slot": 103, "tps": 190.0, "non_vote_tps": 140.0, "vote_tps": 50.0},
                {"slot": 102, "tps": 210.0, "non_vote_tps": 155.0, "vote_tps": 55.0},
                {"slot": 101, "tps": 200.0, "non_vote_tps": 150.0, "vote_tps": 50.0},
            ],
        })
        instruments = render.render_network_instruments(snapshot, "desktop")
        points = re.findall(r"data-throughput-point[^>]*data-index='(\d+)'[^>]*data-x='([^']+)'", instruments)
        self.assertGreater(len(points), 1)
        latest_index = max(int(index) for index, _ in points)
        latest_x = next(x for index, x in points if int(index) == latest_index)
        guide = re.search(r"data-throughput-guide x1='([^']+)' y1='[^']+' x2='([^']+)'", instruments)
        self.assertIsNotNone(guide)
        self.assertEqual(guide.group(1), latest_x)
        self.assertEqual(guide.group(2), latest_x)
        self.assertNotIn("data-throughput-guide x1='46.0'", instruments)
        page = render.render_html(snapshot, history=[snapshot])
        self.assertIn("latestPoint", page)
        self.assertIn("guide.setAttribute('x1', latestPoint.dataset.x || '0');", page)

    def test_epoch_gauge_reading_is_bounded_inside_the_ring(self):
        snapshot = load_fixture()
        snapshot["epoch"].update({"progress_pct": 34.85, "estimated_remaining_seconds": 100})
        instruments = render.render_network_instruments(snapshot, "desktop")
        self.assertIn("class='epoch-gauge__reading'", instruments)
        self.assertIn("font-size:min(27px,7vw)", render.CSS)
        self.assertIn(".mobile-briefing .epoch-gauge__reading strong { font-size:min(25px,7vw); }", render.CSS)
        self.assertIn("white-space:nowrap", render.CSS)

    def test_archive_normalizes_comparison_legend_and_equal_height_columns(self):
        previous = load_fixture()
        current = json.loads(json.dumps(previous))
        previous["collected_at"] = "2026-08-05T08:00:00+00:00"
        comparison = render.delta_module.compare(previous, current)
        chart = render.render_ab_chart(previous, current, comparison, "history-test")
        self.assertLess(chart.index("class='comparison-chart'"), chart.index("class='chart-legend "))
        self.assertIn(".prototype-page--archive .snapshot-panel {\n      display: flex;\n      flex-direction: column;\n      align-self: stretch;", render.CSS)
        self.assertIn(".prototype-page--archive .change-panel {\n      display:flex;", render.CSS)

    def test_dense_tables_and_support_panels_have_explicit_containment_and_bottom_space(self):
        self.assertIn(".prototype-page--data .validator-table-scroll {", render.CSS)
        self.assertIn("width:max-content", render.CSS)
        self.assertNotIn("max-height:640px", render.CSS)
        self.assertIn("padding: 18px 18px 26px", render.CSS)
        self.assertIn("margin-bottom: 22px", render.CSS)
        self.assertIn("align-items: stretch", render.CSS)
        self.assertIn(".prototype-page--data .validator-production { margin-top:32px; }", render.CSS)
        self.assertIn("background:transparent", render.CSS)
        self.assertNotIn("data-pagination__range", render.CSS)

    def test_data_schema_is_a_scannable_contract_not_raw_inline_json(self):
        snapshot = load_fixture()
        snapshot["release"] = {
            "release_id": "release-id",
            "selected_snapshot": {"path": "snapshots/latest.json"},
            "generated_at": "2026-08-30T12:00:00+00:00",
            "collector": {"source_revision": "collector-revision"},
        }
        history = [snapshot]
        catalog = render.render_data_catalog(snapshot, None, None, history)
        self.assertIn("class='data-contract'", catalog)
        self.assertIn("Schema version", catalog)
        self.assertIn("Output formats", catalog)
        self.assertIn("Recorded snapshots", catalog)
        self.assertIn("Chartable series", catalog)
        self.assertNotIn("<pre class='code-block'", catalog)
        self.assertIn("Full precision stays in <code>report.json</code>", catalog)
        summary, full_trace = catalog.split("<details class='provenance-details'>", 1)
        self.assertIn("Release ID", summary)
        self.assertNotIn("Collector source revision", summary)
        self.assertIn("<summary>Full release trace</summary>", full_trace)
        self.assertIn("Collector source revision", full_trace)

    def test_validator_workbench_is_ranked_sortable_filterable_and_mobile_readable(self):
        snapshot = load_fixture()
        snapshot["validators"].update({
            "top_10_share_pct": 24.4,
            "ranked_validator_limit": 30,
            "commission": {"available": True, "median_pct": 5, "mean_pct": 7,
                           "zero_commission_count": 2, "max_commission_count": 0},
            "ranked_validators": [
                {"rank": 1, "identity": "node-A", "vote_account": "vote-A", "state": "current",
                 "stake_sol": 17_000_000.0, "share_pct": 3.92, "commission": 7,
                 "last_vote": 439_909_939, "root_slot": 439_909_908},
                {"rank": 2, "identity": "node-D", "vote_account": "vote-D", "state": "delinquent",
                 "stake_sol": 12_000_000.0, "share_pct": 2.81, "commission": 10,
                 "last_vote": 439_900_000, "root_slot": 439_899_000},
            ],
        })
        workbench = render.render_validator_workbench(snapshot, "desktop")
        mobile_workbench = render.render_validator_workbench(snapshot, "mobile")
        self.assertIn("data-validator-workbench", workbench)
        self.assertIn("Top validator concentration", workbench)
        self.assertIn(
            "<caption>Top validators by active stake</caption>",
            render.render_supply_validators_html(snapshot),
        )
        self.assertIn("data-validator-filter", workbench)
        self.assertIn("data-validator-table", workbench)
        self.assertIn("data-sort-key='stake_sol'", workbench)
        self.assertIn("vote-A", workbench)
        self.assertIn("439,909,939", workbench)
        self.assertIn("439,909,908", workbench)
        self.assertIn("Delinquent", workbench)
        self.assertIn("class='validator-bar__fill'", workbench)
        self.assertNotIn("class='validator-mobile-row'", workbench)
        self.assertIn("class='validator-mobile-row'", mobile_workbench)
        self.assertIn("<strong>390.0M SOL</strong>", workbench)
        self.assertIn("<strong>5%</strong>", workbench)
        self.assertIn(".mobile-data-workbench .validator-mobile-row {", render.CSS)
        self.assertIn("border-radius:0", render.CSS)
        self.assertIn("box-shadow:none", render.CSS)
        self.assertIn(".mobile-data-workbench .validator-mobile-row:last-child { border-bottom:0; }", render.CSS)
        self.assertIn(".mobile-data-workbench .validator-mobile-row[open] {", render.CSS)
        self.assertIn(".mobile-source-group, .mobile-project-section {", render.CSS)
        self.assertIn(".validator-mobile-row {\n        border-radius: 0;", render.CSS)

    def test_validator_metrics_use_visual_carousels_before_ranked_distribution(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        commissions = [0] * 246 + [1] * 47 + [5] * 261 + [6] * 30 + [10] * 29 + [11] * 3 + [100] * 69
        current_rows = [
            {
                "rank": index + 1, "identity": f"current-{index}", "state": "current",
                "share_pct": 24.51 / 10 if index < 10 else 0.1,
                "commission": commission,
            }
            for index, commission in enumerate(commissions)
        ]
        production_rows = [
            {"identity": f"producer-{index}", "vote_identity_matched": index >= 8}
            for index in range(678)
        ]
        snapshot["validators"].update({
            "active_count": 685,
            "delinquent_count": 12,
            "delinquent_pct": 1.72,
            "active_stake_sol": 432_684_178.49,
            "nakamoto_coefficient": 18,
            "top_10_share_pct": 24.51,
            "commission": {"available": True, "median_pct": 5},
            "all_validators": current_rows + [
                {"rank": 686 + index, "identity": f"delinquent-{index}", "state": "delinquent", "commission": 5}
                for index in range(12)
            ],
            "block_production": {
                "available": True, "epoch": 1022, "first_slot": 1, "last_slot": 432_000,
                "leader_slots": 432_000, "blocks_produced": 431_522,
                "skipped_slots": 478, "skip_rate": 478 / 432_000,
                "validators": production_rows,
            },
        })
        desktop = render.render_validator_workbench(snapshot, "desktop")
        mobile = render.render_validator_workbench(snapshot, "mobile")

        for surface in (desktop, mobile):
            self.assertLess(
                surface.index("class='validator-metric-components'"),
                surface.index("Top validator concentration"),
            )
            self.assertIn("class='validator-metric-carousel chart-carousel'", surface)
            self.assertEqual(surface.count("data-validator-metric-card data-pulse-card"), 4)
            self.assertEqual(surface.count("data-validator-metric-dot="), 4)
            self.assertIn("data-pulse-track tabindex='0'", surface)
            self.assertIn("data-pulse-previous aria-label='Previous validator metric'", surface)
            self.assertIn("data-pulse-next aria-label='Next validator metric'", surface)
            for component in (
                "validator-participation",
                "validator-stake-concentration",
                "validator-commission-distribution",
                "validator-epoch-production",
            ):
                self.assertIn(f"data-validator-component='{component}'", surface)
        self.assertIn("aria-labelledby='desktop-validator-concentration-title desktop-validator-concentration-desc'", desktop)
        self.assertIn("aria-labelledby='mobile-validator-concentration-title mobile-validator-concentration-desc'", mobile)
        self.assertEqual(desktop.count("<tr data-page-row='desktop'>"), 100)
        self.assertEqual(mobile.count("class='validator-mobile-row' data-page-row='mobile'"), 100)
        self.assertIn("Showing 100 of 678 identities", desktop)
        self.assertIn("Download all exact identity rows", mobile)
        self.assertEqual(
            {
                metric
                for group in re.findall(r"data-metric-ids='([^']+)'", mobile)
                for metric in group.split(",")
            },
            {
                "active", "delinquent", "active_stake", "nakamoto",
                "top_10_share", "median_commission", "completed_epoch",
                "produced", "skipped", "skip_rate", "unmatched_identities",
            },
        )

        validators = snapshot["validators"]
        current = [row for row in validators["all_validators"] if row["state"] == "current"]
        buckets = [
            sum(row["commission"] == 0 for row in current),
            sum(1 <= row["commission"] <= 4 for row in current),
            sum(row["commission"] == 5 for row in current),
            sum(6 <= row["commission"] <= 9 for row in current),
            sum(row["commission"] == 10 for row in current),
            sum(11 <= row["commission"] <= 99 for row in current),
            sum(row["commission"] == 100 for row in current),
        ]
        self.assertEqual(buckets, [246, 47, 261, 30, 29, 3, 69])
        self.assertIn("data-commission-counts='246,47,261,30,29,3,69'", mobile)

        production = validators["block_production"]
        self.assertEqual(
            production["blocks_produced"] + production["skipped_slots"],
            production["leader_slots"],
        )
        unmatched = sum(
            row.get("vote_identity_matched") is False
            for row in production["validators"]
        )
        self.assertIn(f"{len(production['validators']) - unmatched} of {len(production['validators'])} matched", mobile)
        epoch_card = mobile[
            mobile.index("data-validator-component='validator-epoch-production'"):
            mobile.index("</article>", mobile.index("data-validator-component='validator-epoch-production'"))
        ]
        self.assertIn("Finalized slots 1–432000", epoch_card)
        self.assertIn("of 432,000 leader slots", epoch_card)
        self.assertNotIn(".mobile-source-group, .mobile-project-section, .validator-mobile-row {", render.CSS)
        self.assertNotIn("validator-table-note", render.CSS)

    def test_growth_metrics_use_a_carousel_in_both_layouts_and_cover_every_summary_fact(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {
            "available": True,
            "selected_usd_stablecoins": selected_stablecoins_fixture(),
            "daily_active_addresses": {"available": False},
            "daily_fee_payers": {"available": False},
            "tokenized_equities": {
                "available": True,
                "registry_asset_count": 107,
                "eligible_asset_count": 107,
                "supply_coverage": {
                    "coverage_numerator": 106,
                    "coverage_denominator": 107,
                    "fresh_asset_count": 72,
                    "queried_this_run_asset_count": 72,
                    "successful_this_run_asset_count": 72,
                    "failed_this_run_asset_count": 0,
                    "scope": "observed subset",
                    "oldest_observation_at": "2026-08-26T04:14:58+00:00",
                    "newest_observation_at": "2026-08-27T17:11:19+00:00",
                },
                "valuation": {"available": False, "reason": "No cleared price source."},
                "volume": {
                    "available": True,
                    "volume_24h_usd": 10_048_527.42,
                    "assets_with_pairs": 43,
                    "pair_count": 43,
                    "volume_covered_pair_count": 43,
                    "invalid_row_count": 0,
                    "conflicting_pair_count": 0,
                    "batches_requested": 4,
                    "batches_succeeded": 4,
                    "transport_complete": True,
                    "market_coverage": "partial",
                },
                "assets": [],
            },
        }
        workbenches = {
            context: render.render_growth_workbench(snapshot, context)
            for context in ("desktop", "mobile")
        }
        carousels = {
            context: workbench[
                workbench.index("class='growth-metric-carousel"):
                workbench.index("class='growth-unavailable'")
            ]
            for context, workbench in workbenches.items()
        }

        for context, carousel in carousels.items():
            with self.subTest(context=context):
                self.assertIn(
                    "class='growth-metric-carousel validator-metric-carousel chart-carousel'",
                    carousel,
                )
                self.assertEqual(carousel.count("data-growth-metric-card data-pulse-card"), 4)
                self.assertEqual(carousel.count("data-growth-metric-dot="), 4)
                self.assertLess(
                    workbenches[context].index("class='growth-metric-carousel"),
                    workbenches[context].index("class='growth-unavailable'"),
                )
                self.assertEqual(
                    {
                        metric
                        for group in re.findall(r"data-growth-metric-ids='([^']+)'", carousel)
                        for metric in group.split(",")
                    },
                    {
                        "registry_assets", "finalized_supply_coverage",
                        "fresh_finalized_supply", "current_supply_run",
                        "usd_valuation", "indexed_dex_pool_volume",
                        "selected_usd_stablecoin_supply",
                    },
                )
                for expected in (
                    "107 registry assets", "106 / 107", "72 / 107", "72 / 72",
                    "0 failed", "$10.0M", "43 / 43", "USD valuation", "Unavailable", "4 / 4",
                    "10.00 nominal units",
                ):
                    self.assertIn(expected, carousel)
                self.assertIn("Market evidence</h3></div><small>Partial</small>", carousel)
        self.assertIn("id='desktop-growth-title'", workbenches["desktop"])
        self.assertNotIn("id='mobile-growth-title'", workbenches["desktop"])
        self.assertIn(
            "class='validator-workbench validator-workbench--desktop "
            "growth-workbench growth-workbench--desktop'",
            workbenches["desktop"],
        )
        self.assertIn(
            "document.querySelectorAll('.chart-carousel').forEach((pulseCarousel)",
            render.MOBILE_CONTROLLER,
        )
        self.assertIn(
            "pulseCarousel.querySelector('[data-pulse-track]')",
            render.MOBILE_CONTROLLER,
        )
        mobile = workbenches["mobile"]
        carousel = carousels["mobile"]

        snapshot["growth"]["tokenized_equities"]["supply_coverage"].update({
            "queried_this_run_asset_count": 0,
            "successful_this_run_asset_count": 0,
        })
        unavailable_run = render.render_growth_workbench(snapshot, "mobile")
        run_start = unavailable_run.index("data-growth-metric-ids='current_supply_run'")
        run_card = unavailable_run[run_start:unavailable_run.index("</article>", run_start)]
        self.assertIn("Current supply run</h3></div><small>Unavailable</small>", run_card)

        snapshot["growth"]["tokenized_equities"]["volume"] = {
            "available": False,
            "reason": "Indexed volume withheld for this run.",
        }
        snapshot["growth"]["daily_active_addresses"] = {"available": True}
        degraded = render.render_growth_workbench(snapshot, "mobile")
        market_start = degraded.index("data-growth-metric-ids='usd_valuation,indexed_dex_pool_volume'")
        market_card = degraded[market_start:degraded.index("</article>", market_start)]
        self.assertIn("Market evidence</h3></div><small>Unavailable</small>", market_card)
        provider_start = degraded.index("data-growth-metric-ids='legacy_provider_activity_semantics'")
        provider_card = degraded[provider_start:degraded.index("</article>", provider_start)]
        self.assertIn("Provider benchmarks</h3></div><small>Unavailable</small>", provider_card)

    def test_completed_epoch_block_production_is_visible_in_markdown_and_both_workbenches(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["validators"]["block_production"] = {
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
            "vote_enrichment_observed_at": "2026-08-05T09:00:00+00:00",
            "source": {"method": "getBlockProduction", "commitment": "finalized"},
            "validators": [
                {"identity": "production-node-A", "leader_slots": 6,
                 "blocks_produced": 5, "skipped_slots": 1,
                 "skip_rate": round(1 / 6, 8), "vote_identity_matched": True,
                 "vote_account_count": 1, "vote_accounts": [{"vote_pubkey": "vote-A"}],
                 "activated_stake_lamports": 100},
                {"identity": "production-node-B", "leader_slots": 4,
                 "blocks_produced": 3, "skipped_slots": 1,
                 "skip_rate": 0.25, "vote_identity_matched": False,
                 "vote_account_count": 0, "vote_accounts": [],
                 "activated_stake_lamports": None},
            ],
        }
        markdown = render.render_markdown(snapshot)
        workbenches = (
            render.render_validator_workbench(snapshot, "desktop"),
            render.render_validator_workbench(snapshot, "mobile"),
        )
        self.assertIn("### Completed-epoch block production", markdown)
        self.assertIn("| Finalized slot range | 10–19 (10 leader slots) |", markdown)
        self.assertIn("| Aggregate skip rate | 20% |", markdown)
        self.assertIn("| Unmatched production identities | 1 of 2 |", markdown)
        self.assertIn(
            "Showing 2 of 2 identities, highest leader-slot counts first. "
            "Complete exact identity rows and subject observations remain in "
            "[report.json](report.json).",
            markdown,
        )
        self.assertIn("production-node-B", markdown)
        for workbench in workbenches:
            self.assertIn("Completed epoch", workbench)
            self.assertIn("finalized slots 10–19", workbench)
            self.assertIn("<span>Produced</span><strong>8</strong>", workbench)
            self.assertIn("<span>Skipped</span><strong>2</strong>", workbench)
            self.assertIn("<span>Skip rate</span><strong>20%</strong>", workbench)
            self.assertIn("<span>Unmatched identities</span><strong>1 / 2</strong>", workbench)
            self.assertIn("production-node-A", workbench)
            self.assertIn("production-node-B", workbench)
            self.assertNotIn("skip-rate enrichment are intentionally omitted", workbench)
            self.assertNotIn("validator-table-note", workbench)

    def test_completed_epoch_markdown_bounds_human_table_without_mutating_rows(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        production_rows = [
            {
                "identity": f"production-node-{index:03d}",
                "leader_slots": index + 1,
                "blocks_produced": index + 1,
                "skipped_slots": 0,
                "skip_rate": 0.0,
                "vote_identity_matched": True,
                "vote_account_count": 1,
            }
            for index in range(105)
        ]
        snapshot["validators"]["block_production"] = {
            "available": True,
            "basis": "most recent fully completed epoch",
            "epoch": 799,
            "first_slot": 10,
            "last_slot": 5_574,
            "leader_slots": sum(row["leader_slots"] for row in production_rows),
            "blocks_produced": sum(row["blocks_produced"] for row in production_rows),
            "skipped_slots": 0,
            "skip_rate": 0.0,
            "vote_enrichment_observed_at": "2026-08-05T09:00:00+00:00",
            "validators": production_rows,
        }

        markdown = render.render_markdown(snapshot)
        section = markdown[
            markdown.index("### Completed-epoch block production"):
            markdown.index("## Releases and announcements")
        ]

        self.assertEqual(
            len(re.findall(r"^\| production-node-\d{3} \|", section, re.MULTILINE)),
            100,
        )
        self.assertLess(
            section.index("production-node-104"),
            section.index("production-node-103"),
        )
        self.assertNotIn("production-node-004", section)
        self.assertIn(
            "Showing 100 of 105 identities, highest leader-slot counts first. "
            "Complete exact identity rows and subject observations remain in "
            "[report.json](report.json).",
            section,
        )
        self.assertEqual(len(snapshot["validators"]["block_production"]["validators"]), 105)

    def test_data_collections_ship_progressive_independent_pagination(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["validators"]["block_production"] = {
            "available": True,
            "epoch": 799,
            "first_slot": 10,
            "last_slot": 19,
            "leader_slots": 10,
            "blocks_produced": 8,
            "skipped_slots": 2,
            "skip_rate": 0.2,
            "validators": [{
                "identity": "production-node-A", "leader_slots": 10,
                "blocks_produced": 8, "skipped_slots": 2,
                "skip_rate": 0.2, "vote_identity_matched": True,
                "vote_account_count": 1,
            }],
        }
        workbench = render.render_validator_workbench(snapshot, "desktop")
        mobile_workbench = render.render_validator_workbench(snapshot, "mobile")
        catalog = render.render_data_catalog(snapshot, None, None, [])

        self.assertEqual(workbench.count("data-pagination data-page-size='10' data-mobile-page-size='10'"), 1)
        self.assertEqual(workbench.count("data-pagination data-page-size='25' data-mobile-page-size='10'"), 1)
        self.assertIn("aria-label='Ranked validator pages'", workbench)
        self.assertIn("aria-label='Completed-epoch production pages'", workbench)
        self.assertIn("data-validator-row data-page-row='desktop'", workbench)
        self.assertIn("<tr data-page-row='desktop'>", workbench)
        self.assertNotIn("data-page-row='mobile'", workbench)
        self.assertIn("data-validator-row data-page-row='mobile'", mobile_workbench)
        self.assertIn("validator-mobile-row' data-page-row='mobile'", mobile_workbench)
        self.assertNotIn("data-page-row='desktop'", mobile_workbench)
        self.assertNotIn("data-page-row='desktop' hidden", workbench)
        self.assertNotIn("data-page-row='mobile' hidden", workbench)

        self.assertIn("catalog-shell' id='desktop-data-sources' data-pagination data-page-size='10' data-mobile-page-size='10'", catalog)
        self.assertEqual(
            catalog.count("<tr data-page-row='desktop'>"),
            len(render.DATA_CATALOG_DATASETS),
        )
        self.assertIn("aria-label='Dataset catalog pages'", catalog)
        self.assertIn("data-pagination-controls hidden", catalog)
        self.assertIn("data-pagination-filter-controls hidden", catalog)
        self.assertIn("id='desktop-data-catalog-search'", catalog)
        self.assertIn("data-pagination-filter disabled", catalog)
        self.assertIn("aria-controls='desktop-data-catalog-table'", catalog)
        self.assertIn("id='desktop-data-catalog-filter-status' aria-live='polite'", catalog)
        self.assertIn(
            f"{len(render.DATA_CATALOG_DATASETS)} of "
            f"{len(render.DATA_CATALOG_DATASETS)} datasets",
            catalog,
        )
        self.assertIn("data-pagination-filter-empty hidden", catalog)
        self.assertIn("data-pagination-filter-reset", catalog)
        self.assertNotIn("<tr data-page-row='desktop' hidden", catalog)
        self.assertNotIn("data-page-status", catalog)
        self.assertNotIn("data-page-status", workbench)

        controller = render.MOBILE_CONTROLLER
        self.assertIn("const paginationWindow = (current, total) =>", controller)
        self.assertIn("Math.ceil(matchingRows.length / size)", controller)
        self.assertIn("paginationByRoot.forEach((pagination) => pagination.reset())", controller)
        self.assertIn("row.toggleAttribute('data-page-filtered', !match)", controller)
        self.assertIn("const searchable = (row.dataset.search || row.textContent || '').toLowerCase();", controller)
        self.assertIn("paginationFilter.addEventListener('input', applyPaginationFilter);", controller)
        self.assertIn("paginationFilterStatus.textContent = `${matchingCount} of ${rows.length} datasets`;", controller)
        self.assertIn("if (paginationFilterEmpty) paginationFilterEmpty.hidden = matchingCount !== 0;", controller)
        self.assertIn("paginationFilter.value = '';", controller)
        self.assertIn("paginationFilterControls.hidden = false", controller)
        self.assertIn("event?.detail === 0", controller)

    def test_methods_flow_uses_only_selected_snapshot_simd_metadata(self):
        snapshot = load_fixture()
        source_commit = "a" * 40
        snapshot["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True, "source_commit": source_commit,
            "proposal_count": 326, "document_count": 330,
            "coverage_complete": True, "partial": False, "items": [],
        }}}
        flow = render.render_source_flow(snapshot)
        self.assertIn("Pinned SIMD metadata", flow)
        self.assertIn(source_commit, flow)
        self.assertIn("326 proposals / 330 documents", flow)
        self.assertNotIn("Static SIMD references", flow)

        missing = render.render_source_flow(load_fixture())
        self.assertIn("SIMD lifecycle metadata unavailable in the selected snapshot", missing)
        self.assertNotIn("Static SIMD references", missing)

    def test_methods_flow_distinguishes_recorded_and_unavailable_selected_sources(self):
        snapshot = load_fixture()
        snapshot["economics"] = {"available": False}
        snapshot["activity"] = {"available": True}
        snapshot["news"] = {"available": True, "sources": {
            "agave_releases": {"available": True},
            "solana_news": {"available": False},
            "simd_proposals": {"available": False},
            "network_status": {"available": True},
        }}

        flow = render.render_source_flow(snapshot)

        self.assertIn("Selected-snapshot source state", flow)
        self.assertIn("CoinGecko unavailable", flow)
        self.assertIn("DeFiLlama unavailable", flow)
        self.assertIn("Official feeds 2/3 recorded", flow)
        self.assertIn("SIMD unavailable", flow)
        self.assertIn("<rect x='8' y='12' width='320'", flow)
        self.assertEqual(flow.count("class='flow-interface' text-anchor='middle'"), 6)
        self.assertIn("Unavailable sources remain explicit gaps and are not described as collected", flow)
        self.assertNotIn("Actual selected-snapshot inputs", flow)

    def test_unavailable_catalog_sources_do_not_borrow_snapshot_freshness(self):
        snapshot = load_fixture()
        snapshot["collected_at"] = "2026-08-27T17:11:23+00:00"
        snapshot["economics"] = {"available": False}
        snapshot["growth"] = {
            "available": True,
            "sources": {"activity_benchmark": {"available": False, "held": True}},
            "daily_active_addresses": {"available": False, "provider_observations": []},
            "daily_fee_payers": {"available": False, "provider_observations": []},
            "tokenized_equities": {},
        }

        catalog = render.render_data_catalog(snapshot, None, None, [])
        economics_row = catalog.split(
            "<span class='dataset-name'>Economic indicators</span>", 1
        )[1].split("</tr>", 1)[0]
        daa_row = catalog.split(
            "<span class='dataset-name'>Network-wide daily active addresses</span>", 1
        )[1].split("</tr>", 1)[0]
        dex_row = catalog.split(
            "<span class='dataset-name'>Indexed Solana DEX-pool volume</span>", 1
        )[1].split("</tr>", 1)[0]

        self.assertIn("<td data-label='Freshness'>No source observation</td>", economics_row)
        self.assertIn("No eligible source observations recorded", economics_row)
        self.assertNotIn("0 of 0 sources recorded", economics_row)
        self.assertIn("evidence-term--unavailable", economics_row)
        self.assertNotIn("evidence-term--measured", economics_row)
        self.assertIn("evidence-term--unavailable", dex_row)
        self.assertNotIn("evidence-term--measured", dex_row)
        self.assertIn("No provider observations collected; network-wide count unavailable", daa_row)
        self.assertIn("<td data-label='Freshness'>No source observation</td>", daa_row)
        self.assertNotIn(snapshot["collected_at"], economics_row)
        self.assertNotIn("Provider observations recorded", daa_row)

        mobile = render.render_mobile_data(snapshot)
        self.assertIn("CoinGecko · No source observation", mobile)
        self.assertIn("No cleared timestamped price source · No source observation", mobile)
        self.assertNotIn(f"CoinGecko · {render.timestamp_label(snapshot['collected_at'])}", mobile)

    def test_reproduction_copy_does_not_claim_an_uncommitted_snapshot(self):
        catalog = render.render_data_catalog(load_fixture(), None, None, [])
        self.assertIn("Regenerate every local artifact from the selected snapshot", catalog)
        self.assertNotIn("from the committed snapshot", catalog)

    def test_data_tables_keep_trailing_columns_without_vertical_scrollbars(self):
        self.assertIn(".prototype-page--data .catalog-shell table {", render.CSS)
        self.assertIn("min-width: 1040px", render.CSS)
        self.assertIn("table-layout: auto", render.CSS)
        self.assertIn(".prototype-page--data .catalog-shell th:nth-child(6)", render.CSS)
        self.assertNotIn(".prototype-page--data th:nth-child(6),\n.prototype-page--data td:nth-child(6)", render.CSS)
        self.assertIn("width:max-content", render.CSS)
        self.assertIn("border-collapse:separate", render.CSS)
        self.assertIn("table-layout:auto", render.CSS)
        self.assertIn("scrollbar-width:thin", render.CSS)
        self.assertIn("overflow-y:clip", render.CSS)
        self.assertIn("::-webkit-scrollbar { width:0; height:6px; }", render.CSS)
        self.assertNotIn("max-height:640px", render.CSS)
        self.assertNotIn("scrollbar-gutter:stable", render.CSS)
        self.assertNotIn("min-width:1240px", render.CSS)
        self.assertIn("text-overflow:ellipsis", render.CSS)

        catalog = render.render_data_catalog(load_fixture(), None, None, [load_fixture()])
        self.assertIn("evidence-term--confirmed", catalog)
        self.assertIn("evidence-term--unavailable", catalog)
        self.assertIn("popover", catalog)

    def test_validator_workbench_ships_once_per_data_form_factor_with_unique_ids(self):
        snapshot = load_fixture()
        snapshot["validators"]["ranked_validators"] = [
            {"rank": 1, "identity": "node-A", "vote_account": "vote-A", "state": "current",
             "stake_sol": 17_000_000.0, "share_pct": 3.92, "commission": 7,
             "last_vote": 439_909_939, "root_slot": 439_909_908},
        ]
        page = render.render_html(snapshot, history=[snapshot, snapshot])
        self.assertEqual(page.count("data-validator-workbench aria-labelledby"), 2)
        self.assertEqual(page.count("id='desktop-validator-title'"), 1)
        self.assertEqual(page.count("id='mobile-validator-title'"), 1)
        self.assertIn("validatorTables.forEach", page)

    def test_data_community_news_reuses_project_stories_as_a_carousel(self):
        snapshot = editorial_fixture()
        desktop = render.render_community_news(snapshot, "desktop")
        mobile = render.render_community_news(snapshot, "mobile")

        for surface in (desktop, mobile):
            self.assertIn("class='community-news", surface)
            self.assertIn("Community news", surface)
            self.assertEqual(surface.count("data-pulse-card data-community-story"), 4)
            self.assertIn("data-pulse-controls hidden", surface)
            self.assertIn("data-pulse-track tabindex='0'", surface)
            self.assertIn("Previous community news story", surface)
            self.assertIn("Next community news story", surface)

        page = render.render_html(snapshot, history=[snapshot, snapshot])
        self.assertEqual(page.count("data-community-news>"), 2)
        self.assertLess(
            page.index("id='desktop-community-news'"),
            page.index("id='desktop-validator-evidence'"),
        )
        self.assertLess(
            page.index("id='mobile-community-news'"),
            page.index("id='mobile-validator-evidence'"),
        )
        self.assertIn("href='#mobile-community-news'", page)

    def test_development_stream_uses_real_source_lanes_priority_watch_and_filters(self):
        snapshot = load_fixture()
        simd_sha = "a" * 40
        snapshot["news"] = {
            "available": True,
            "sources": {
                "agave_releases": {"label": "Agave releases", "available": True,
                    "latest_published": "2026-08-17T15:25:24Z", "items": [
                        {"title": "Release v4.3.0-beta.0", "link": "https://example.com/release",
                         "published": "2026-08-17T15:25:24Z", "author": "bot",
                         "release_channel": "prerelease", "stable": False,
                         "tag": "v4.3.0-beta.0", "tag_commit_sha": "b" * 40}]},
                "solana_news": {"label": "Solana News", "available": True,
                    "latest_published": "2026-08-16T12:00:00Z", "items": [
                        {"title": "Foundation update", "link": "https://solana.com/news/update",
                         "published": "2026-08-16T12:00:00Z", "author": None}]},
                "simd_proposals": {"label": "SIMD activity", "available": True,
                    "source_commit": simd_sha, "items": [
                        {"title": "SIMD-0550: Double disinflation", "link": "https://example.com/simd",
                         "created": "2026-07-31", "status": "Review", "author": "author"}],
                    "proposals": [{"identifier": "SIMD-0550", "name": "Double disinflation",
                        "status": "Review", "created": "2026-07-31", "authors": ["author"],
                        "category": "Standard", "type": "Core", "source": "https://example.com/simd",
                        "source_commit": simd_sha}]},
                "network_status": {"label": "Network status", "available": True,
                    "latest_published": "2024-02-06T15:09:24Z", "items": [
                        {"title": "Cluster incident", "link": "https://example.com/status",
                         "published": "2024-02-06T15:09:24Z", "author": None}]},
            },
        }
        stream = render.render_development_stream(snapshot, "desktop")
        self.assertIn("Upgrade watch", stream)
        self.assertIn("Recorded SIMD lifecycle", stream)
        self.assertIn("Double disinflation", stream)
        self.assertIn("Release v4.3.0-beta.0", stream)
        self.assertIn("Foundation update", stream)
        self.assertIn("SIMD-0550: Double disinflation", stream)
        self.assertIn("Cluster incident", stream)
        self.assertIn("data-development-filter='release'", stream)
        self.assertIn("<select data-development-source", stream)
        self.assertIn("<select data-development-window", stream)
        self.assertIn("<option value='all'>All sources</option>", stream)
        self.assertIn("<option value='all'>All dates</option>", stream)
        self.assertNotIn("aria-haspopup='listbox'", stream)
        self.assertNotIn("development-picker-popover", stream)
        self.assertIn("aria-label='Primary source for Double disinflation", stream)
        self.assertIn("Lifecycle state comes only from proposal frontmatter", stream)
        self.assertIn("Pinned source commit", stream)
        self.assertIn("data-development-lane='agave'", stream)
        self.assertIn("data-development-lane='news'", stream)
        self.assertIn("data-development-lane='simd'", stream)
        self.assertIn("data-development-lane='status'", stream)
        self.assertIn("Connectors show chronology within each source lane", stream)

    def test_development_stream_renders_every_recorded_event_without_silent_truncation(self):
        snapshot = load_fixture()
        items = [
            {"title": f"Release {index:02d}", "published": "2026-08-01T00:00:00Z"}
            for index in range(1, 26)
        ]
        snapshot["news"] = {
            "available": True,
            "sources": {"agave_releases": {"label": "Agave releases", "available": True,
                "latest_published": "2026-08-01T00:00:00Z", "items": items}},
        }
        stream = render.render_development_stream(snapshot, "desktop")
        self.assertEqual(stream.count("<li data-development-event "), 25)

    def test_development_stream_date_only_timestamps_join_age_windows_and_render_as_dates(self):
        snapshot = load_fixture()
        snapshot["collected_at"] = "2026-08-26T04:15:20+00:00"
        snapshot["news"] = {
            "available": True,
            "sources": {"simd_proposals": {"label": "SIMD activity", "available": True, "items": [
                {"title": "SIMD-0600: Recent proposal", "created": "2026-08-10"}]}},
        }
        stream = render.render_development_stream(snapshot, "desktop")
        self.assertRegex(stream, r"data-development-age-days='\d+'")
        self.assertIn("Created Aug 10, 2026", stream)
        self.assertNotIn("<time datetime=''", stream)

    def test_development_stream_does_not_invent_timezone_for_naive_datetimes(self):
        self.assertIsNone(render.development_event_moment("2026-08-10T12:30:00"))
        self.assertIsNotNone(render.development_event_moment("2026-08-10"))
        self.assertIsNotNone(render.development_event_moment("2026-08-10T12:30:00Z"))

    def test_development_events_without_timestamps_do_not_emit_invalid_datetime(self):
        snapshot = load_fixture()
        snapshot["collected_at"] = "2026-08-26T04:15:20+00:00"
        snapshot["news"] = {
            "available": True,
            "sources": {"network_status": {"label": "Network status", "available": True, "items": [
                {"title": "Cluster incident"}]}},
        }
        stream = render.render_development_stream(snapshot, "desktop")
        self.assertNotIn("<time datetime=''>", stream)
        self.assertIn("Time unavailable", stream)

    def test_development_proposal_watch_without_created_date_has_no_empty_time(self):
        snapshot = load_fixture()
        snapshot["news"] = {
            "available": True,
            "sources": {"simd_proposals": {"label": "SIMD activity", "available": True,
                "proposals": [{"identifier": "SIMD-0600", "name": "Undated proposal"}]}},
        }
        stream = render.render_development_stream(snapshot, "desktop")
        # The old behavior rendered a misleading "Created unavailable";
        # now an undated proposal simply omits the created element.
        self.assertNotIn("<time datetime=''>", stream)
        self.assertNotIn("Created unavailable", stream)
        self.assertIn("SIMD-0600", stream)

    def test_development_controls_keep_44px_targets_and_native_selects_in_css(self):
        self.assertNotIn("min-height:2.25rem", render.CSS)
        self.assertIn(".development-select select {", render.CSS)
        self.assertIn("min-height:44px", render.CSS)
        self.assertNotIn(".development-picker-trigger {", render.CSS)
        self.assertNotIn(".development-picker-popover", render.CSS)
        self.assertIn(".development-freshness > li > .development-event-untime { grid-column:2; }", render.CSS)

    def test_development_stream_ships_in_project_desktop_and_mobile_with_unique_ids(self):
        snapshot = load_fixture()
        page = render.render_html(snapshot, history=[snapshot, snapshot])
        self.assertEqual(page.count("<section class='development-stream"), 2)
        self.assertEqual(page.count("id='desktop-development-title'"), 1)
        self.assertEqual(page.count("id='mobile-development-title'"), 1)
        self.assertIn("developmentStreams.forEach", page)

    def test_mobile_development_stream_is_always_visible_and_date_grouped(self):
        snapshot = load_fixture()
        snapshot["news"] = {
            "available": True,
            "sources": {"agave_releases": {"label": "Agave releases", "available": True,
                "items": [
                    {"title": "Release one", "published": "2026-08-21T14:34:00Z"},
                    {"title": "Release two", "published": "2026-08-21T12:47:00Z"},
                    {"title": "Release three", "published": "2026-08-14T18:34:00Z"},
                ]}},
        }
        stream = render.render_development_stream(snapshot, "mobile")
        page = render.render_mobile_project(snapshot)

        self.assertEqual(stream.count("data-development-date-group"), 2)
        self.assertIn("<h4>AUG 21, 2026</h4>", stream)
        self.assertIn("<span class='development-event-time'>14:34 UTC</span>", stream)
        self.assertIn("<details class='development-more-filters'><summary>Filters</summary>", stream)
        self.assertIn("id='mobile-development-source-status'", stream)
        self.assertIn("Chronology does not imply dependency, merge, or causality.", stream)
        self.assertIn("<section class='mobile-project-development'", page)
        self.assertNotIn("<details class='mobile-project-section mobile-project-development'", page)

    def test_mobile_development_date_only_events_do_not_invent_a_time(self):
        snapshot = load_fixture()
        snapshot["news"] = {
            "available": True,
            "sources": {"simd_proposals": {"label": "SIMD activity", "available": True,
                "items": [{"title": "SIMD-0600", "created": "2026-08-10"}]}},
        }
        stream = render.render_development_stream(snapshot, "mobile")

        self.assertIn("<h4>AUG 10, 2026</h4>", stream)
        self.assertIn("<span class='development-event-time'>Created date</span>", stream)
        self.assertNotIn("00:00 UTC", stream)

    def test_mobile_development_filters_hide_empty_date_groups(self):
        controller = render.MOBILE_CONTROLLER

        self.assertIn("querySelectorAll('[data-development-date-group]')", controller)
        self.assertIn("group.hidden = !Array.from", controller)
        self.assertIn(".development-more-filters > summary", render.CSS)
        self.assertIn("min-height:44px", render.CSS)

    def test_overview_evidence_is_a_text_ledger_without_a_score_graphic(self):
        evidence = render.render_overview_evidence(load_fixture(), None)
        self.assertIn("class='confidence-scale evidence-ledger'", evidence)
        self.assertNotIn("confidence-ring", evidence)
        self.assertNotIn("No</strong><span>score", evidence)
        self.assertNotIn("confidence-ring", render.CSS)
        self.assertNotIn("conic-gradient(", render.CSS)
        for status in ("Recorded", "Sampled", "Unavailable"):
            self.assertIn(status, evidence)
        for fake_threshold in ("0.87", "0.70", "0.40"):
            self.assertNotIn(fake_threshold, evidence)

    def test_methods_footer_uses_three_truthful_formula_slots_without_inventing_confidence(self):
        methods = render.render_methodology_content(load_fixture())
        self.assertIn("Confidence model · unavailable", methods)
        self.assertIn("class='formula-wrap formula-wrap--evidence'", methods)
        self.assertEqual(methods.count("class='evidence-slot'"), 3)
        for label in ("Classification", "Calculation", "Range"):
            self.assertIn(f"<span class='formula-label'>{label}</span>", methods)
        for truth in ("measured · sampled · unavailable", "deterministic rules + recorded A/B", "no aggregate confidence score"):
            self.assertIn(truth, methods)
        for invented in ("confidence =", "0.00 (low)", "1.00 (high)"):
            self.assertNotIn(invented, methods)

    def test_overview_connects_ad_hoc_intervals_but_breaks_explicit_missing_values(self):
        history = []
        for hour, tps in enumerate((100.0, 110.0, None, 120.0, 130.0)):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            history.append(snapshot)
        charts = render.render_overview_charts(history)
        tps_card = charts.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]
        self.assertEqual(tps_card.count("<polyline points="), 2)
        polylines = re.findall(r"<polyline points='([^']+)'", tps_card)
        self.assertEqual([line.split()[0].split(",")[0] for line in polylines], ["0.0", "216.0"])
        self.assertEqual([line.split()[-1].split(",")[0] for line in polylines], ["72.0", "288.0"])

    def test_overview_aligns_labels_with_first_and_last_present_observations(self):
        history = []
        for hour, tps in enumerate((None, 100.0, 110.0, None)):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            history.append(snapshot)
        charts = render.render_overview_charts(history)
        tps_card = charts.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]
        match = re.search(r"<polyline points='([^']+)'", tps_card)
        if match is None:
            self.fail("TPS chart did not render a polyline")
        polyline = match.group(1)
        self.assertEqual([pair.split(",")[0] for pair in polyline.split()], ["0.0", "288.0"])
        self.assertIn("<span>01:00 UTC</span>", tps_card)
        self.assertIn("<span>02:00 UTC</span>", tps_card)

    def test_overview_explains_when_usable_observations_are_isolated_by_missing_gaps(self):
        history = []
        for hour, tps in enumerate((100.0, None, 120.0)):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            history.append(snapshot)
        charts = render.render_overview_charts(history)
        self.assertIn("2 usable observations, but none are contiguous across explicit gaps", charts)
        self.assertNotIn("2 usable point(s); two required", charts)

    def test_overview_uses_even_sample_spacing_instead_of_stretched_wall_clock_spacing(self):
        history = []
        for hour, tps in ((0, 100.0), (1, 110.0), (9, 120.0), (10, 130.0)):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            history.append(snapshot)
        charts = render.render_overview_charts(history)
        tps_card = charts.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]
        polyline = tps_card.split("<polyline points='", 1)[1].split("'", 1)[0]
        x_values = [float(pair.split(",", 1)[0]) for pair in polyline.split()]
        gaps = [round(after - before, 2) for before, after in zip(x_values, x_values[1:])]
        self.assertEqual(gaps, [96.0, 96.0, 96.0])
        self.assertIn("spaced by sample order rather than elapsed time", tps_card)

    def test_overview_keeps_duplicate_timestamp_observations_in_stable_input_order(self):
        history = []
        for tps in (100.0, 120.0, 110.0):
            snapshot = load_fixture()
            snapshot["collected_at"] = "2026-08-05T01:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            history.append(snapshot)
        charts = render.render_overview_charts(history)
        tps_card = charts.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]
        polyline = re.search(r"<polyline points='([^']+)'", tps_card)
        if polyline is None:
            self.fail("TPS chart did not render a polyline")
        x_values = [pair.split(",")[0] for pair in polyline.group(1).split()]
        self.assertEqual(x_values, ["0.0", "144.0", "288.0"])
        self.assertIn("across 3 recorded observations", tps_card)

    def test_each_route_uses_the_approved_prototype_component_contract(self):
        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        for contract in (
            "<section class='overview'",
            "<ul class='metrics'",
            "<div class='chart-grid'",
            "<section class='insights'",
            "<section class='catalog-shell'",
            "<section class='support-grid'",
            "<figure class='source-flow panel'",
            "<div class='flow-frame'>",
            "<div class='methods-grid'>",
            "<ol class='snapshot-list' aria-label='Recorded snapshot timeline'>",
            "<a class='change-link' href='report.json'>",
            "<div class='select-pair'",
            "<div class='range-controls'",
            "<div class='archive-support'",
            "<div class='chart-wrap'>",
            "<div class='about-reader'>",
            "<header class='about-hero'>",
        ):
            self.assertIn(contract, page)
        self.assertEqual(page.count("class='select-like select-like--static'"), 2)
        self.assertEqual(page.count("<output class='select-like select-like--static'"), 2)
        self.assertNotIn("<button class='select-like'", page)
        self.assertNotIn("class='chevron'", page)
        self.assertEqual(page.count("<button class='range-control"), 0)
        self.assertIn("<span class='range-control is-active is-wide'>Samples</span>", page)
        self.assertNotIn(">1D</", page)
        self.assertNotIn(">7D</", page)
        self.assertNotIn(">Custom</", page)
        self.assertNotIn(">9H</button>", page)
        comparison_at = page.index("id='history-comparison-title'")
        deltas_at = page.index("id='history-delta-title'")
        support_at = page.index("class='archive-support'")
        self.assertLess(comparison_at, deltas_at)
        self.assertLess(deltas_at, support_at)

    def test_desktop_history_pair_is_native_static_output_not_a_fake_selector(self):
        fixture = load_fixture()
        workspace = render.render_history_workspace([fixture, fixture], None, None)
        self.assertLess(workspace.index("class='chart-meta'"), workspace.index("class='select-pair'"))
        self.assertEqual(workspace.count("class='chart-meta'"), 1)
        self.assertEqual(workspace.count("<output class='select-like select-like--static'"), 2)
        self.assertNotIn("<button class='select-like'", workspace)
        self.assertNotIn("class='chevron'", workspace)
        self.assertIn("<ol class='snapshot-list' aria-label='Recorded snapshot timeline'>", workspace)
        self.assertIn("<li class='snapshot is-selected' aria-current='true'>", workspace)
        self.assertNotIn("name='history-snapshot'", workspace)
        self.assertNotIn("class='static-button'", workspace)

    def test_about_route_keeps_the_machine_readable_contract(self):
        page = render.render_html(load_fixture())
        for contract in (
            "class='about-reader'",
            "class='about-hero'",
            "class='about-snapshot'",
            "class='about-outcomes'",
            "class='about-inspect'",
            "data-evidence-basis='recorded'",
            "data-evidence-basis='sampled'",
            "data-evidence-basis='estimated'",
            "data-evidence-basis='unavailable'",
            "data-artifact='markdown'",
            "data-artifact='json'",
            "data-route='project' data-variant='desktop' data-content-key='project'",
            "data-route='project' data-variant='mobile' data-content-key='project'",
            'rel="alternate" type="application/json"',
            'rel="alternate" type="text/markdown"',
        ):
            self.assertIn(contract, page)
        self.assertIn("--paper: var(--prototype-paper)", render.CSS)
        self.assertIn("--super-purple: light-dark(#5522e0, #b9a6ff)", render.CSS)
        self.assertNotIn("--paper: #faf9f6", render.CSS)
        self.assertNotIn("--violet: #6641c5", render.CSS)
        self.assertLess(page.index("class='about-reader'"), page.index("class='development-stream development-stream--desktop'"))

    def test_about_snapshot_art_is_pinned_embedded_and_available_to_both_layouts(self):
        page = render.render_html(load_fixture())
        notice = (Path(__file__).resolve().parent.parent / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(render.ABOUT_SNAPSHOT_ART_PATH.name, "about-snapshot-solana-8bit-768.png")
        self.assertLess(render.ABOUT_SNAPSHOT_ART_PATH.stat().st_size, 500_000)
        self.assertEqual(render.ABOUT_RECORDED_ART_PATH.name, "about-recorded-cable-box-1400.png")
        self.assertLess(render.ABOUT_RECORDED_ART_PATH.stat().st_size, 600_000)
        self.assertEqual(
            hashlib.sha256(render.ABOUT_SNAPSHOT_ART_PATH.read_bytes()).hexdigest(),
            render.ABOUT_SNAPSHOT_ART_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(render.ABOUT_RECORDED_ART_PATH.read_bytes()).hexdigest(),
            render.ABOUT_RECORDED_ART_SHA256,
        )
        self.assertIn("ef39927f37edc796b4116e7bfa92b1521fdaf55741f75b27dfa58f68b54321bf", notice)
        self.assertIn(render.ABOUT_SNAPSHOT_ART_SHA256, notice)
        self.assertIn(render.ABOUT_RECORDED_ART_SHA256, notice)
        self.assertIn("OpenAI Media Service API", notice)
        self.assertIn("trainedAlgorithmicMedia", notice)
        self.assertIn("Prompt and reference-image record: `UNKNOWN`", notice)
        self.assertEqual(
            page.count("background-image:url(\"data:image/png;base64,"),
            2,
        )
        self.assertEqual(page.count("class='about-snapshot__art'"), 2)
        self.assertEqual(
            page.count("aria-label='Pixel art golden Solana coin under a magnifying glass'"),
            2,
        )
        self.assertNotIn("about-snapshot__recording", page)
        self.assertEqual(page.count("class='about-recorded-inline'"), 2)
        self.assertEqual(page.count("class='about-recorded-inline__art'"), 2)
        self.assertEqual(
            page.count("aria-label='Pixel art cable recorder with a display reading Recorded, not live'"),
            2,
        )
        self.assertNotIn("about-recorded-bumper", page)
        self.assertNotIn("about-snapshot__status", page)
        self.assertEqual(
            page.count("<p>A saved reading of the report's sources at one point in time.</p>"),
            2,
        )
        self.assertEqual(page.count("class='about-actions'"), 2)

        desktop = render.render_project_content(editorial_fixture())
        mobile = render.render_mobile_project(editorial_fixture())
        for surface, snapshot_class in (
            (desktop, "class='about-snapshot'"),
            (mobile, "class='mobile-about-snapshot'"),
        ):
            self.assertEqual(surface.count("Compare recorded snapshots"), 1)
            self.assertLess(surface.index("class='about-snapshot__art'"), surface.index(snapshot_class))
            self.assertLess(surface.index(snapshot_class), surface.index("class='about-recorded-inline'"))

    def test_editorial_art_is_pinned_small_and_embedded_once(self):
        page = render.render_html(editorial_fixture())
        notice = (Path(__file__).resolve().parent.parent / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(page.count("data:image/webp;base64,"), len(render.EDITORIAL_ART_ASSETS))
        self.assertLess(
            sum(path.stat().st_size for path, _expected_hash in render.EDITORIAL_ART_ASSETS.values()),
            600_000,
        )
        for key, (path, expected_hash) in render.EDITORIAL_ART_ASSETS.items():
            with self.subTest(key=key):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
                self.assertIn(expected_hash, notice)
                self.assertEqual(page.count(f".project-editorial__art--{key}{{"), 1)
        self.assertIn("publisher images are not scraped or hotlinked", page)

    def test_desktop_about_snapshot_places_art_left_and_copy_right(self):
        self.assertIn(
            "grid-template-columns: minmax(20rem, 30rem) minmax(22rem, 28rem);",
            render.CSS,
        )
        self.assertRegex(
            render.CSS,
            r"\.prototype-page--about \.about-snapshot__copy \{\s*grid-column: 2;",
        )
        self.assertIn("justify-content: center;", render.CSS)
        self.assertIn(
            ".prototype-page--about .about-recorded-inline__art { grid-column: 1;",
            render.CSS,
        )
        self.assertIn(
            ".prototype-page--about .about-recorded-inline time { grid-column: 2;",
            render.CSS,
        )

    def test_project_navigation_tracks_nested_fragments_for_people_and_assistive_tech(self):
        page = render.render_html(load_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("querySelectorAll('.prototype-nav__link')", controller)
        self.assertIn("navigationLinks.forEach((link)", controller)
        self.assertIn("const desktopRoutes = new Map", controller)
        self.assertIn("body:has(#project :target) .skip-project", render.CSS)
        self.assertIn(".prototype-shell:has(#project :target) .prototype-nav__link--about", render.CSS)
        self.assertIn("scroll-margin-top: 4.5rem", render.CSS)
        self.assertIn(".prototype-page--about .about-reader", render.CSS)
        self.assertIn(".mobile-project-reader .mobile-about-intro", render.CSS)
        self.assertNotIn("transition: color 150ms ease, padding", render.CSS)

    def test_development_filters_announce_the_visible_result_count(self):
        page = render.render_html(load_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertEqual(page.count("<span class='development-result-count' data-development-result-count"), 2)
        self.assertIn("role='status' aria-live='polite'", page)
        self.assertIn("resultCount.textContent = `${visibleCount}", controller)
        self.assertIn("Upgrade watch unavailable", page)

    def test_development_stream_can_switch_between_timeline_and_grid(self):
        page = render.render_html(load_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertEqual(page.count("data-development-view='timeline'"), 2)
        self.assertEqual(page.count("data-development-view='grid'"), 2)
        self.assertIn("id='desktop-development-events'", page)
        self.assertIn("id='mobile-development-events'", page)
        self.assertIn("eventList.dataset.view", controller)
        self.assertIn(".development-events[data-view='grid']", render.CSS)
        self.assertIn(
            ".development-events[data-view='grid'] .development-graph-node { display:none; }",
            render.CSS,
        )

    def test_stable_routes_map_to_the_prototype_view_classes(self):
        page = render.render_html(load_fixture())
        mapping = {
            "overview": "prototype-page--report",
            "data": "prototype-page--data",
            "methods": "prototype-page--methods",
            "history": "prototype-page--archive",
            "project": "prototype-page--about",
        }
        for route, prototype_class in mapping.items():
            self.assertRegex(
                page,
                rf"<section class='view prototype-page {prototype_class}' id='{route}'",
            )
            self.assertIn(f"href='#{route}'", page)

    def test_mobile_flow_and_inline_labels_keep_their_spacing_contract(self):
        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertIn(".prototype-page--methods .mobile-stage", page)
        self.assertNotIn(".prototype-page--methods .flow-mobile-stage", page)
        self.assertNotIn("</strong>This is", page)
        self.assertNotIn("</strong>Snapshot", page)
        self.assertEqual(page.count("class='prototype-status'"), 1)

    def test_all_five_substantive_views_are_always_present(self):
        page = render.render_html(load_fixture())
        expected = {
            "overview": "Solana Ecosystem Report",
            "data": "Data catalog",
            "methods": "How We Measure",
            "history": "Report Archive",
            "project": "A clearer view of Solana, saved for inspection.",
        }
        for view_id, heading in expected.items():
            self.assertEqual(page.count(f"id='{view_id}'"), 1)
            self.assertIn(heading, page)

    def test_every_fragment_link_resolves_and_ids_are_unique(self):
        page = render.render_html(load_fixture())
        ids = re.findall(r"\bid=['\"]([^'\"]+)", page)
        fragments = re.findall(r"\bhref=['\"]#([^'\"]+)", page)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(fragments) - set(ids))

    def test_data_view_exposes_the_local_output_bundle(self):
        page = render.render_html(load_fixture())
        self.assertIn("href='report.md'", page)
        self.assertIn("href='report.json'", page)
        self.assertIn("href='report.md' download", page)
        self.assertIn("href='report.json' download", page)
        self.assertIn("Download JSON", page)
        self.assertIn("Full precision stays in report.json", page)
        self.assertIn("Data catalog", page)

    def test_data_exports_and_source_cells_have_local_feedback_states(self):
        page = render.render_html(load_fixture())
        catalog = render.render_data_catalog(load_fixture(), None, None, [load_fixture()])
        self.assertIn(".prototype-page--data .download-link:first-child {", render.CSS)
        self.assertIn("box-shadow: inset 0 -2px 0", render.CSS)
        self.assertIn("--action-fill: light-dark(#5522e0, #6d4aff)", render.CSS)
        self.assertIn("--action-border-strong: light-dark(#5522e0, #38129e)", render.CSS)
        self.assertIn("--action-edge: light-dark(#3f1aa8, #38129e)", render.CSS)
        self.assertIn("background: var(--action-fill);", render.CSS)
        self.assertNotIn("rgba(139,92,246", render.CSS)
        self.assertNotIn("1px 0 #3f1aa8", render.CSS)
        self.assertIn("data-copy-value='getSupply'", catalog)
        self.assertEqual(catalog.count("class='source-copy'"), render.DATA_CATALOG_DATASET_COUNT)
        self.assertIn("source-copy__feedback", catalog)
        self.assertIn(".prototype-page--data .catalog-shell td[data-label='Source']", render.CSS)
        self.assertIn("inset:0", render.CSS)
        self.assertNotIn("min-height:45px", render.CSS)
        self.assertIn("background:var(--violet);", render.CSS)
        self.assertIn("catalog-shell tbody tr:hover td", render.CSS)
        self.assertIn("const sourceCopyButtons", page)
        self.assertIn("navigator.clipboard", page)
        self.assertIn("}, 1000);", page)

    def test_method_history_and_project_views_are_substantive(self):
        page = render.render_html(load_fixture())
        for text in ("Measured vs sampled", "Confidence model · unavailable",
                     "Comparing snapshots", "Change markers", "Key metric deltas",
                     "About this report", "Current snapshot", "Upgrade watch", "Inspect the work"):
            self.assertIn(text, page)

    def test_upgrades_are_visible_in_desktop_mobile_and_machine_output_source(self):
        snapshot = load_fixture()
        source_commit = "a" * 40
        proposal = {"identifier": "SIMD-0326", "name": "Alpenglow", "status": "Review",
                    "created": "2025-07-25", "authors": ["A. Author"], "category": "Standard",
                    "type": "Core", "source": "https://example.com/simd-0326",
                    "source_commit": source_commit}
        snapshot["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True, "label": "SIMD proposal lifecycle", "source_commit": source_commit,
            "proposal_count": 1, "document_count": 1, "coverage_complete": True,
            "proposals": [proposal], "items": [{"title": "SIMD-0326: Alpenglow — Review",
                "link": proposal["source"], "created": proposal["created"], "status": "Review"}],
        }}}
        page = render.render_html(snapshot)
        for value in ("Alpenglow", "SIMD-0326", "Recorded SIMD lifecycle", "Created 2025-07-25"):
            self.assertIn(value, page)
        self.assertNotIn("Static reference data", page)
        self.assertNotIn("last checked", page)
        self.assertIn("id='project-upgrades'", page)
        self.assertIn("id='mobile-project-upgrades'", page)
        self.assertEqual(render.recorded_simd_source(snapshot)["source_commit"], source_commit)

    def test_latest_recorded_snapshot_populates_history_and_real_values(self):
        history = recorded_history_fixture()
        snapshot = history[-1]
        comparison = render.delta_module.delta_for(snapshot, history)
        page = render.render_html(
            snapshot,
            render.analysis_for(snapshot, history),
            comparison,
            history,
        )
        current_tps = render.fmt(snapshot.get("performance", {}).get("latest_tps"))
        median_fee = render.fmt(snapshot.get("activity", {}).get("fees", {}).get("median_lamports"))
        for text in (current_tps, f"{median_fee} lamports", "Recent TPS Samples", "Measured",
                     "feeds captured with B"):
            self.assertIn(text, page)
        self.assertEqual(comparison.get("status"), "ok")
        not_comparable = comparison.get("not_comparable", [])
        self.assertEqual(comparison.get("counts", {}).get("not_comparable"), len(not_comparable))
        for item in not_comparable:
            self.assertIn(render.html.escape(str(item.get("reason"))), page)
        comparable_count = sum(
            comparison.get("counts", {}).get(key, 0)
            for key in ("changed", "steady")
        )
        self.assertGreater(comparable_count, 0)
        self.assertIn(f"{comparable_count} comparable metrics", page)
        self.assertIn("<div class='delta-grid'>", page)

    def test_history_chart_uses_recorded_linear_paths_without_extra_observations(self):
        history = recorded_history_fixture()
        previous, current = history[-3], history[-2]
        comparison = render.delta_module.delta_for(current, history[:-1])
        chart = render.render_ab_chart(previous, current, comparison)
        self.assertEqual(chart.count("<path class='line-"), 2)
        self.assertEqual(chart.count("<circle class='point-a'"), 4)
        self.assertEqual(chart.count("<circle class='point-b'"), 4)
        for css_class in ("line-a", "line-b"):
            path_markup = chart.split(f"<path class='{css_class}' d='", 1)[1].split("'/>", 1)[0]
            self.assertIn(" L", path_markup)
            self.assertNotIn(" C", path_markup)
        self.assertIn("Two independent recent-performance sample windows", chart)
        self.assertIn("<h3 class='chart-title'>Recent TPS Samples <span class='chart-basis'>Measured</span></h3>", chart)
        tps_delta = render.comparison_item(comparison, "latest_tps")
        if tps_delta is None:
            self.fail("Latest TPS comparison was not rendered")
        arrow = "↑" if tps_delta["change"] > 0 else "↓"
        sign = "+" if tps_delta["change"] > 0 else ""
        self.assertIn(
            f"<strong>{arrow} {abs(tps_delta['change_pct']):.2f}%</strong>"
            f"<span>{sign}{render.fmt_delta_value(tps_delta['change'], tps_delta['unit'])}</span>",
            chart,
        )
        self.assertIn(
            "<span class='legend-tag'>A</span><span class='legend-copy'><strong>Previous snapshot</strong>"
            f"<span>{render.compact_snapshot_label(previous.get('collected_at'))}</span>", chart,
        )
        self.assertIn(
            "<span class='legend-tag'>B</span><span class='legend-copy'><strong>Current snapshot</strong>"
            f"<span>{render.compact_snapshot_label(current.get('collected_at'))}</span>", chart,
        )
        self.assertGreater(chart.index("class='chart-legend "), chart.index("class='chart-wrap'"))
        self.assertEqual(chart.count("data-chart-point"), 8)
        self.assertEqual(chart.count("tabindex='0' aria-label='Snapshot"), 8)
        self.assertIn("data-chart-tooltip role='tooltip' hidden", chart)
        self.assertIn("data-chart-a-at=", chart)
        self.assertIn("data-chart-b-at=", chart)
        self.assertIn("<strong></strong><span></span><small></small>", chart)
        self.assertIn("class='grid-line axis-baseline'", chart)
        self.assertEqual(chart.count("class='x-axis-tick'"), 8)
        self.assertIn(">Recorded sample</text>", chart)

    def test_history_chart_tooltip_and_limits_disclosure_are_interactive(self):
        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertIn(".prototype-page--archive .chart-disclosure[open] > summary::after", render.CSS)
        self.assertIn(".prototype-page--archive .chart-tooltip", render.CSS)
        self.assertIn("desktopChart.addEventListener('pointermove'", page)
        self.assertIn("data-chart-series-a=", page)
        self.assertIn("data-chart-hover-guide", page)
        self.assertIn("showChartSampleTooltip", page)
        self.assertIn("background: var(--prototype-paper);", render.CSS)
        self.assertIn("stroke-dasharray: 4 4;", render.CSS)
        self.assertIn("point.addEventListener('focus'", page)

    def test_history_restores_reference_comparison_details_and_notes(self):
        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertIn("<section class='comparison-context'", page)
        self.assertIn(">Comparison details</h2>", page)
        self.assertIn(">Snapshots compared</dt>", page)
        self.assertIn(">Recorded samples</dt>", page)
        self.assertIn(">Notes</h2>", page)
        self.assertIn("Missing samples remain explicit gaps", page)

    def test_overview_change_values_share_one_left_aligned_column(self):
        self.assertIn(
            "grid-template-columns: 7px minmax(0, 1fr) minmax(170px, 34%);",
            render.CSS,
        )
        self.assertIn(
            ".prototype-page--report .change-list .direction {\n      text-align: left;",
            render.CSS,
        )
        self.assertIn(
            "grid-template-columns:minmax(0,1fr) minmax(150px,42%);",
            render.CSS,
        )
        self.assertIn("white-space:normal;", render.CSS)
        self.assertIn(
            ".prototype-page--report .insights { width:min(1600px,100%); margin-inline:auto; }",
            render.CSS,
        )
        self.assertIn(
            ".prototype-page--report .insights { gap:clamp(14px,1.17vw,26px); padding:0 var(--prototype-content-gutter)",
            render.CSS,
        )
        self.assertIn(
            ".prototype-page--report .change-panel::after,\n    .prototype-page--report .confidence-panel::after {",
            render.CSS,
        )
        self.assertIn("border-top: 1px solid var(--prototype-rule);", render.CSS)

        fixture = load_fixture()
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertEqual(page.count("<article class='change-panel'>"), 1)
        self.assertEqual(page.count("<article class='confidence-panel'>"), 1)

    def test_history_change_markers_use_natural_content_height(self):
        self.assertIn(".prototype-page--archive .change-list {\n      display: block;", render.CSS)
        self.assertIn("flex: 0 0 auto;", render.CSS)
        self.assertIn("flex-direction: column;\n      align-self: start;", render.CSS)
        self.assertIn("grid-template-columns: 5px minmax(0, 1fr);", render.CSS)
        self.assertIn(".prototype-page--archive .change-link {\n      width: 100%;", render.CSS)

    def test_history_snapshot_timeline_hugs_content_and_centers_markers(self):
        self.assertIn("flex-direction: column;\n      align-self: stretch;\n      padding: 17px 15px 14px;", render.CSS)
        self.assertIn(".prototype-page--archive .snapshot-panel {\n        display: block;\n        align-self: start;", render.CSS)
        self.assertIn("display: grid;\n      flex: 1;\n      align-content: space-between;", render.CSS)
        self.assertIn("list-style: none;", render.CSS)
        self.assertNotIn(".prototype-page--archive .static-button", render.CSS)
        self.assertIn("left: var(--timeline-center);", render.CSS)
        self.assertIn("justify-self: center;", render.CSS)
        self.assertIn("transform: translateX(-50%);", render.CSS)

    def test_history_ab_chart_breaks_gaps_and_keeps_isolated_observations(self):
        previous = load_fixture()
        current = load_fixture()
        previous["performance"]["samples"] = [
            {"tps": value} for value in reversed((100.0, 110.0, None, 120.0, 130.0))
        ]
        current["performance"]["samples"] = [
            {"tps": value} for value in reversed((200.0, None, 220.0))
        ]
        chart = render.render_ab_chart(previous, current, None)
        self.assertEqual(chart.count("<path class='line-a'"), 2)
        self.assertEqual(chart.count("<path class='line-b'"), 0)
        self.assertEqual(chart.count("<circle class='point-b'"), 2)
        self.assertIn("Missing samples remain explicit gaps; isolated observations remain visible", chart)

    def test_escapes_values_that_could_break_the_document(self):
        hostile = load_fixture()
        hostile["validators"]["top_validators"][0]["identity"] = "<script>alert(1)</script>"
        page = render.render_html(hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_health_metric_reflects_actual_state(self):
        healthy = load_fixture()
        healthy["schema_version"] = 8
        healthy_page = render.render_html(healthy)
        self.assertIn("metric__value--healthy", healthy_page)
        self.assertIn("class='metric__health-icon-slot'", healthy_page)
        self.assertIn("class='metric__health-icon'", healthy_page)
        self.assertIn("<circle cx='12' cy='12' r='10'></circle>", healthy_page)
        self.assertIn("<path d='m6.8 12.4 3.5 3.3 7.2-7.8'></path>", healthy_page)
        self.assertIn("<span>Healthy</span>", healthy_page)
        self.assertIn(
            "grid-template-rows:minmax(clamp(20px,1.56vw,34px),auto) minmax(clamp(44px,3.39vw,74px),auto) auto;",
            render.CSS,
        )
        self.assertIn("flex: 0 0 1em;", render.CSS)
        self.assertIn("place-items: center;", render.CSS)

        unhealthy = load_fixture()
        unhealthy["schema_version"] = 8
        unhealthy["network"]["healthy"] = False
        unhealthy_page = render.render_html(unhealthy)
        self.assertIn("class='metric__value '>Unhealthy", unhealthy_page)
        self.assertIn("RPC Endpoint Health", unhealthy_page)
        self.assertNotIn("class='metric__health-icon'", unhealthy_page)

        unavailable = load_fixture()
        unavailable["schema_version"] = 8
        unavailable["network"] = {"available": False, "healthy": True}
        unavailable_page = render.render_html(unavailable)
        self.assertIn("class='metric__value '>Unavailable</strong><span class='delta'>unavailable</span>", unavailable_page)
        self.assertNotIn("class='metric__health-icon'", unavailable_page)

    def test_fee_metric_keeps_number_and_unit_in_one_value_line(self):
        fixture = load_fixture()
        fixture["activity"] = {"fees": {"median_lamports": 5514}}
        page = render.render_html(fixture)
        self.assertIn(
            "<strong class='metric__value'><span class='metric__number'>5,514</span>"
            "<span class='metric__unit'>lamports</span></strong>",
            page,
        )
        self.assertIn("white-space: nowrap;\n      font-variant-numeric: tabular-nums;", render.CSS)

    def test_degraded_snapshot_renders_without_raising(self):
        page = render.render_html({
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": False},
            "epoch": {"available": False},
            "performance": {"available": False},
            "supply": {"available": False},
            "validators": {"available": False},
            "activity": {"available": False},
        })
        # Each degraded section says so; none of them renders a zero.
        # Ecosystem Pulse adds SOL-price and sampled-REV states, while the
        # mobile audit detail repeats the same honest block-sampling state.
        self.assertEqual(page.count("unavailable in this snapshot"), 8)
        self.assertNotIn(">$0<", page)


class TestMobileFirstContracts(unittest.TestCase):
    def page(self, *, history=None, comparison=None, analysis=None):
        fixture = load_fixture()
        history = history if history is not None else [fixture, fixture]
        return render.render_html(
            fixture, analysis=analysis, comparison=comparison, history=history,
        )

    @staticmethod
    def distinct_history():
        snapshots = []
        for hour, tps, slot_time in (
            (8, 1000.0, 0.4),
            (9, 2000.0, 0.4),
            (10, 2000.0, 0.8),
        ):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
            snapshot["performance"]["latest_tps"] = tps
            snapshot["performance"]["mean_slot_time_secs"] = slot_time
            snapshots.append(snapshot)
        return snapshots

    def test_shared_mobile_shell_has_safe_fixed_navigation_and_route_hooks(self):
        page = self.page()
        self.assertIn("<header class='mobile-topbar'", page)
        self.assertIn("<nav class='mobile-dock' aria-label='Mobile report views'>", page)
        for fragment, label in (("overview", "Overview"), ("data", "Data"),
                                ("methods", "Methods"), ("history", "History"),
                                ("project", "Project")):
            self.assertRegex(page, rf"class='mobile-dock__link[^']*' href='#{fragment}'.*?{label}</a>")
        dock = page.split("<nav class='mobile-dock'", 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("aria-current=", dock)
        self.assertIn("min-height: 44px", page)
        self.assertIn("padding-bottom: calc(var(--mobile-dock-height) + env(safe-area-inset-bottom))", page)
        self.assertIn("padding:4px 5px env(safe-area-inset-bottom)", page)
        self.assertIn("html { scroll-padding-top: 52px; overflow-x: hidden; }", page)
        self.assertRegex(page, re.compile(r"\.mobile-signal-rail\s*\{[^}]*grid-template-columns:repeat\(3", re.DOTALL))
        self.assertIn("backdrop-filter:blur(14px)", page)
        self.assertIn("@media (prefers-reduced-motion: reduce)", page)

    def test_shared_header_and_footer_form_one_responsive_report_shell(self):
        page = self.page()
        self.assertIn("class='report-mark' aria-hidden='true'", page)
        self.assertNotIn("Recorded network intelligence", page)
        self.assertIn("class='prototype-status__dot'", page)
        self.assertIn("<footer class='report-footer' aria-label='Report footer'>", page)
        self.assertIn("<nav class='report-footer__nav' aria-label='Footer report views'>", page)
        self.assertIn("title='Latest recorded snapshot'", page)
        self.assertRegex(render.CSS, re.compile(r"\.report-footer\s*\{[^}]*background: var\(--prototype-paper\)", re.DOTALL))
        self.assertNotIn(".report-footer::before", render.CSS)
        self.assertIn("min-height: 64px", render.CSS)
        self.assertIn("padding: 12px clamp(24px, 4vw, 72px)", render.CSS)
        self.assertIn(".theme-menu__trigger {\n      display: inline-flex;", render.CSS)
        self.assertRegex(render.CSS, re.compile(r"\.theme-menu__option\s*\{[^}]*min-height: 44px", re.DOTALL))
        self.assertIn("@keyframes theme-menu-in", render.CSS)
        self.assertNotIn(".theme-control select", render.CSS)
        self.assertIn(
            ".theme-menu__orb, .theme-menu__chevron, .theme-menu__option, .theme-menu__option-mark { transition: none; }",
            render.CSS,
        )
        self.assertIn(".report-footer__brand { min-height: 44px; font-size: 12px; }", render.CSS)
        self.assertIn(".report-footer__nav { display: none; }", page)
        self.assertIn("min-height: 60px; padding: 12px 16px 14px", render.CSS)
        self.assertIn(
            ".prototype-page h1[tabindex='-1']:focus { outline: 2px solid var(--prototype-violet)",
            render.CSS,
        )
        self.assertIn(".prototype-page h1[tabindex='-1']:focus-visible", render.CSS)
        self.assertIn("scroll-margin-top: 76px", render.CSS)

    def test_hash_navigation_focus_is_keyboard_scoped_except_for_skip_links(self):
        page = self.page(history=[])
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("let keyboardHashNavigation = false", controller)
        self.assertIn("event.detail === 0", controller)
        self.assertIn("requestAnimationFrame(() => syncRoute(true))", controller)
        self.assertIn("syncRoute(keyboardHashNavigation)", controller)
        self.assertIn("keyboardHashNavigation = false", controller)

    def test_skip_links_focus_the_visible_heading_for_each_route(self):
        page = self.page(history=[])
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        routes = {
            "overview": ("overview-title", "mobile-overview-title"),
            "data": ("data-title", "mobile-data-title"),
            "methods": ("methods-title", "mobile-methods-title"),
            "history": ("history-title", "mobile-history-title"),
            "project": ("project-title", "mobile-project-title"),
        }
        for route, (desktop_title, mobile_title) in routes.items():
            self.assertIn(f"data-skip-route='{route}'", page)
            self.assertRegex(page, rf"id='{desktop_title}'[^>]*tabindex='-1'")
            self.assertRegex(page, rf"id='{mobile_title}'[^>]*tabindex='-1'")
        self.assertIn("const routeHeadingId = (route) =>", controller)
        self.assertIn("mobileViewport.matches ? routes : desktopRoutes", controller)
        self.assertIn("link.setAttribute('href', `#${targetId}`)", controller)
        self.assertIn("link.matches('[data-skip-route]') || event.detail === 0", controller)
        self.assertIn("document.body.dataset.uiState && uiStateTitle ? uiStateTitle", controller)
        for route in routes:
            self.assertIn(f"body[data-ui-state][data-ui-route='{route}']", render.CSS)

    def test_project_is_the_route_label_while_about_remains_descriptive_copy(self):
        page = self.page(history=[])
        self.assertIn("Skip to Project</a>", page)
        self.assertIn("Skip to Data</a>", page)
        self.assertIn("Skip to Methods</a>", page)
        self.assertIn("href='#project'>Project</a></nav>", page)
        self.assertIn("href='#overview'>Overview</a>", page)
        self.assertIn("href='#data'>Data</a>", page)
        self.assertIn("href='#methods'>Methods</a>", page)
        self.assertIn("href='#history'>History</a><a href='#project'>Project</a>", page)
        self.assertRegex(page, r"class='mobile-dock__link' href='#project'.*?Project</a>")
        self.assertNotIn("Skip to Data &amp; Exports", page)
        self.assertNotIn("Skip to Methodology", page)
        self.assertNotIn("href='#project'>About</a>", page)
        self.assertIn("About this report", page)

    def test_desktop_carousel_controls_share_the_network_pulse_heading_row(self):
        self.assertIn(".prototype-page--report .chart-carousel { position: static; }", render.CSS)
        self.assertIn(".prototype-page--report .pulse-controls { top: clamp(15px, .98vw, 22px); }", render.CSS)
        self.assertIn(".mobile-network-pulse .pulse-controls { top:-44px; right:16px; }", render.CSS)
        self.assertIn(
            ".prototype-page--report .pulse { padding:clamp(15px,.98vw,22px) 0 clamp(14px,1.17vw,26px); }",
            render.CSS,
        )
        self.assertIn("padding-inline: var(--pulse-edge)", render.CSS)
        self.assertIn("padding: 10px clamp(18px, 3vw, 44px) 28px", render.CSS)

    def test_overview_snapshot_provenance_is_compact_and_human_readable(self):
        page = self.page()
        self.assertNotIn("class='site-footer snapshot-footer'", page)
        self.assertIn("<footer class='report-footer' aria-label='Report footer'>", page)
        self.assertIn("title='Latest recorded snapshot'", page)
        self.assertIn("datetime='2026-08-05T09:00:00+00:00'", page)
        self.assertIn("Aug 05, 2026 · 09:00 UTC", page)
        self.assertNotIn("Snapshot: 2026-08-05T09:00:00+00:00", page)

    def test_mobile_overview_is_a_semantic_command_deck_with_full_chart_carousel(self):
        history = recorded_history_fixture()
        page = self.page(history=history)
        self.assertIn("<article class='mobile-view mobile-briefing' aria-labelledby='mobile-overview-title'>", page)
        self.assertIn("<header class='mobile-overview-header'>", page)
        self.assertIn("<h1 id='mobile-overview-title' class='mobile-network-state'", page)
        self.assertIn("<div class='mobile-health-status'", page)
        self.assertIn("class='mobile-health-status__mark mobile-status-pill__dot'", page)
        self.assertNotIn("class='mobile-health-instrument'", page)
        self.assertIn("class='mobile-network-pulse'", page)
        mobile = page.split("class='mobile-network-pulse'", 1)[1].split("</section>", 1)[0]
        expected_charts = render.render_overview_charts(history).count("data-pulse-card")
        self.assertEqual(mobile.count("data-pulse-card"), expected_charts)
        self.assertIn("data-pulse-controls hidden", mobile)
        self.assertIn("class='mobile-signal-rail mobile-signal-rail--quick-links'", page)
        self.assertEqual(page.count("class='mobile-signal-card'"), 5)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", page)
        self.assertIn("aria-labelledby='mobile-evidence-title'", page)
        self.assertIn("class='mobile-history-action' href='#history'", page)
        self.assertIn("<strong>View metric history</strong>", page)
        self.assertRegex(
            render.CSS,
            re.compile(
                r"\.mobile-history-action\s*\{[^}]*border:1px solid var\(--prototype-rule-strong\);",
                re.DOTALL,
            ),
        )
        self.assertIn("id='mobile-changed-title'", page)
        self.assertIn("What changed", page)
        self.assertIn("id='mobile-status-title'>Evidence status</h2>", page)
        self.assertIn("<span>SOL price</span><strong>", page)
        self.assertIn("<span>Network-wide daily active addresses</span><strong>—</strong>", page)
        self.assertNotIn("Swipe metric", page)
        self.assertNotIn("<details class='mobile-evidence'", page)

    def test_mobile_overview_does_not_republish_legacy_provider_activity_as_network_daa(self):
        fixture = load_fixture()
        fixture["growth"] = {}
        fixture["growth"]["daily_active_addresses"] = {
            "available": True,
            "daily_active_addresses": 150,
        }
        page = render.render_html(fixture, history=[fixture, fixture])
        self.assertIn(
            "<span>Network-wide daily active addresses</span><strong>—</strong>", page,
        )
        self.assertNotIn(
            "<span>Network-wide daily active addresses</span><strong>150</strong>", page,
        )

    def test_mobile_key_signals_rail_is_scroll_snap_with_recorded_cards(self):
        page = self.page()
        overview = page.split(
            "<section class='mobile-signal-cluster' aria-labelledby='mobile-evidence-title'>", 1
        )[1].split("</section>", 1)[0]
        self.assertIn(
            "<div class='mobile-signal-rail mobile-signal-scroller' role='group' "
            "aria-label='Latest recorded throughput' tabindex='0'>",
            overview,
        )
        cards = re.findall(r"<a class='mobile-signal-card' href='#data'><span>([^<]+)</span><strong>([^<]*)</strong>", overview)
        self.assertEqual([name for name, _ in cards], ["Latest TPS", "Peak TPS", "Non-vote TPS"])
        values = dict(cards)
        self.assertEqual(values["Latest TPS"], "4,213.50")
        self.assertEqual(values["Peak TPS"], "4,400")
        self.assertEqual(values["Non-vote TPS"], "—")
        self.assertIn("<small><b class='is-unknown'>window high</b><span>sampled window</span></small>", overview)
        self.assertRegex(
            render.CSS,
            re.compile(
                r"\.mobile-signal-cluster \.mobile-signal-scroller\s*\{[^}]*scroll-snap-type: x mandatory",
                re.DOTALL,
            ),
        )
        self.assertIn("grid-auto-columns:minmax(248px,78vw)", render.CSS)
        self.assertIn("scroll-snap-align: start", render.CSS)
        self.assertIn(".mobile-signal-cluster .mobile-signal-scroller::-webkit-scrollbar { display: none; }", render.CSS)
        self.assertIn("@media (max-width: 340px) {\n        .mobile-signal-cluster .mobile-signal-scroller { margin-inline: -12px;", render.CSS)
        final_phone_override = render.CSS.rsplit("@media (max-width:340px)", 1)[1]
        self.assertIn(".mobile-network-pulse,", final_phone_override)
        self.assertIn(".mobile-briefing .network-instruments--mobile { margin-inline:-12px; }", final_phone_override)
        self.assertIn(".mobile-signal-cluster .mobile-signal-scroller { scroll-padding-inline:12px; }", final_phone_override)
        self.assertIn(".mobile-data-workbench .community-news,", final_phone_override)
        self.assertIn(".mobile-data-workbench .validator-metric-carousel { margin-inline:-12px; }", final_phone_override)
        self.assertNotIn("data-signal-autoscroll", overview)

    def test_mobile_slot_and_fee_links_are_a_compact_grouped_list(self):
        page = self.page()
        slot_fee = page.split("<div class='mobile-signal-rail mobile-signal-rail--quick-links' aria-label='Slot time and median fee'>", 1)[1].split("</div>", 1)[0]
        self.assertEqual(slot_fee.count("class='mobile-signal-card'"), 2)
        self.assertNotIn("data-signal-autoscroll", slot_fee)
        self.assertEqual(slot_fee.count("class='mobile-quick-link__destination'>View data <svg"), 2)
        self.assertEqual(slot_fee.count("class='mobile-quick-link__cue' viewBox='0 0 12 12' aria-hidden='true'"), 2)
        self.assertNotIn("↗", slot_fee)
        self.assertEqual(slot_fee.count("class='mobile-quick-link__meta'>"), 2)
        self.assertIn("aria-label='View mean slot data'", slot_fee)
        self.assertIn("aria-label='View median fee data'", slot_fee)
        self.assertIn(".mobile-signal-rail--quick-links {", render.CSS)
        self.assertIn(".mobile-signal-rail--quick-links .mobile-signal-card:active", render.CSS)
        self.assertIn(".mobile-signal-rail--quick-links .mobile-signal-card:focus-visible", render.CSS)
        self.assertIn("min-height:52px", render.CSS)
        self.assertIn(".mobile-signal-rail--quick-links {\n        display:block;\n        margin: 8px 0 14px;\n        padding:0;", render.CSS)
        self.assertIn(".mobile-signal-card .mobile-quick-link__value { display:block; margin:0;", render.CSS)
        self.assertIn("overflow:hidden;\n        border: 1px solid var(--zinc-200);", render.CSS)
        self.assertIn(".mobile-signal-card + .mobile-signal-card { border-top:1px solid var(--zinc-200); }", render.CSS)

    def test_mobile_overview_preserves_truth_without_a_fabricated_score(self):
        page = self.page(comparison=TestDeltaRendering.comparison())
        topbar = page.split("<header class='mobile-topbar'", 1)[1].split("</header>", 1)[0]
        mobile = page.split("<article class='mobile-view mobile-briefing'", 1)[1].split("</article>", 1)[0]
        overview_context = topbar.split("<div class='mobile-topbar__overview'>", 1)[1].split("</div><div class='mobile-topbar__route'", 1)[0]
        self.assertIn("Solana Ecosystem Report", overview_context)
        self.assertIn("Recorded snapshot · offline", overview_context)
        self.assertIn("Recorded RPC endpoint health", mobile)
        self.assertIn("Snapshot Aug 05, 2026", mobile)
        for label in ("TPS", "Mean slot", "Median fee", "Key signals", "What changed", "Evidence status"):
            self.assertIn(label, mobile)
        visible_mobile = re.sub(r"<[^>]+>", " ", mobile)
        for fabricated in ("86", "100", "86/100", "86 of 100"):
            self.assertNotIn(fabricated, visible_mobile)

    def test_mobile_overview_heading_uses_recorded_network_state(self):
        healthy_snapshot = load_fixture()
        healthy_snapshot["schema_version"] = 8
        healthy = render.render_mobile_overview(healthy_snapshot, None, [], "Latest")
        self.assertIn("data-state='healthy'", healthy)
        self.assertIn("data-state='healthy' tabindex='-1'><svg class='mobile-network-state__check'", healthy)
        self.assertIn("</svg>RPC healthy</h1>", healthy)
        self.assertIn("class='mobile-health-status' data-state='healthy'", healthy)
        self.assertIn("<strong>Measured</strong><small>healthy endpoint response</small>", healthy)

        unhealthy_snapshot = load_fixture()
        unhealthy_snapshot["schema_version"] = 8
        unhealthy_snapshot["network"]["healthy"] = False
        unhealthy = render.render_mobile_overview(unhealthy_snapshot, None, [], "Latest")
        self.assertIn("data-state='unhealthy'", unhealthy)
        self.assertIn("data-state='unhealthy' tabindex='-1'>RPC unhealthy</h1>", unhealthy)
        self.assertNotIn("mobile-network-state__check", unhealthy)
        self.assertIn("class='mobile-health-status' data-state='unhealthy'", unhealthy)

        unavailable_snapshot = load_fixture()
        unavailable_snapshot["schema_version"] = 8
        unavailable_snapshot["network"].pop("healthy")
        unavailable = render.render_mobile_overview(unavailable_snapshot, None, [], "Latest")
        self.assertIn("data-state='unavailable'", unavailable)
        self.assertIn("data-state='unavailable' tabindex='-1'>RPC health unavailable</h1>", unavailable)
        self.assertIn("class='mobile-health-status' data-state='unavailable'", unavailable)
        self.assertIn("<strong>No reading</strong><small>endpoint status unavailable</small>", unavailable)
        self.assertIn(".mobile-network-state[data-state='unhealthy'] { color:var(--prototype-secondary); }", render.CSS)
        self.assertIn(".mobile-network-state[data-state='unavailable'] { color:var(--prototype-muted); }", render.CSS)

    def test_mobile_overview_signal_cards_render_comparison_values(self):
        material = TestDeltaRendering.comparison()
        mobile = render.render_mobile_overview(load_fixture(), material, [], "Latest")
        self.assertIn(render.fmt_delta_change(material["changes"][0]), mobile)
        self.assertIn("class='is-positive'", mobile)

    def test_mobile_overview_treatment_has_grouped_evidence_and_unboxed_active_dock(self):
        page = self.page()
        self.assertIn(
            ".mobile-signal-cluster .mobile-signal-rail { margin: 0; grid-template-columns: 1fr; gap:12px; }",
            page,
        )
        self.assertIn(".mobile-signal-card:first-child { border-left:1px solid var(--prototype-rule)", page)
        self.assertIn(".mobile-signal-card:last-child { border-radius:0 6px 6px 0; }", page)
        self.assertIn(".mobile-signal-card:first-child { padding-left:12px; border-left:0; }", page)
        self.assertIn(".mobile-signal-card:last-child { padding-right:12px; }", page)
        self.assertIn(".mobile-context-item { padding:14px 12px 15px; }", page)
        self.assertIn(".mobile-dock__link[aria-current='page'] { background:transparent;", page)
        self.assertNotIn(".mobile-dock__link[aria-current='page'] { background:#f0ebff", page)
        self.assertIn("border-radius:999px", page)

    def test_public_route_hashes_and_other_route_surfaces_remain_unchanged(self):
        page = self.page()
        expected = ["overview", "data", "methods", "history", "project"]
        desktop_nav = page.split("<nav class='prototype-nav'", 1)[1].split("</nav>", 1)[0]
        mobile_dock = page.split("<nav class='mobile-dock'", 1)[1].split("</nav>", 1)[0]
        self.assertEqual(re.findall(r"href='#([^']+)'", desktop_nav), expected)
        self.assertEqual(re.findall(r"href='#([^']+)'", mobile_dock), expected)
        for contract in ("mobile-data-workbench", "mobile-methods-reader", "mobile-history-workbench", "mobile-project-reader"):
            self.assertIn(contract, page)

    def test_mobile_route_headers_follow_descendant_hash_targets(self):
        page = self.page()
        for route in ("data", "methods", "history", "project"):
            self.assertIn(f"body:has(#{route} :target) .mobile-topbar__overview", page)
            self.assertIn(f"body:has(#{route} :target) .mobile-topbar__route", page)
        self.assertIn("target.closest('.prototype-page')", page)

    def test_mobile_parity_exposes_activity_and_comparison_details(self):
        snapshot = load_fixture()
        mobile = render.render_mobile_data(snapshot)
        self.assertIn("Inspect sampled fees and address activity", mobile)
        self.assertIn(render.render_activity_html(snapshot), mobile)
        methods = render.render_mobile_methods()
        self.assertIn("Technical comparison rules", methods)
        self.assertIn("three prior snapshots", methods)
        self.assertIn("No aggregate confidence score", methods)
        previous = deepcopy(snapshot)
        previous["collected_at"] = "2026-08-01T00:00:00Z"
        current = deepcopy(snapshot)
        current["collected_at"] = "2026-08-02T00:00:00Z"
        history = render.render_mobile_history([previous, current], None)
        self.assertIn("Key metric deltas and threshold findings", history)
        self.assertIn("Threshold findings", history)
        self.assertNotIn("class='visually-hidden' data-history-comparison-limits", history)
        snapshots, observations, indexes = TestPublicObservationBindings.observation_fixture(count=3)
        bound_history = render.render_mobile_history(snapshots, None, indexes)
        self.assertIn("Threshold totals are published for the latest comparison", bound_history)
        self.assertIn("Threshold findings", bound_history)


    def test_mobile_data_has_evidence_section_index_and_explicit_caps(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["growth"] = {"tokenized_equities": {
            "available": True, "registry_asset_count": 10,
            "supply_coverage": {"coverage_numerator": 2, "coverage_denominator": 10,
                                "fresh_asset_count": 2, "successful_this_run_asset_count": 2,
                                "queried_this_run_asset_count": 2, "failed_this_run_asset_count": 0},
            "valuation": {"available": False}, "volume": {"available": False},
            "assets": [{"symbol": "AAA", "name": "Asset A", "mint": "mint-a", "supply": 60.0,
                        "supply_freshness": "fresh", "supply_source_method": "getTokenSupply(finalized)"},
                       {"symbol": "BBB", "name": "Asset B", "mint": "mint-b", "supply": 40.0,
                        "supply_freshness": "fresh", "supply_source_method": "getTokenSupply(finalized)"}],
        }, "sources": {}}
        mobile = render.render_mobile_data(snapshot)
        self.assertIn("class='data-domain-rail data-domain-rail--mobile'", mobile)
        self.assertIn("href='#mobile-validator-evidence'", mobile)
        self.assertIn("href='#mobile-people-markets'", mobile)
        self.assertIn("href='#mobile-data-sources'", mobile)
        self.assertIn("id='mobile-validator-evidence'", mobile)
        self.assertIn("id='mobile-people-markets'", mobile)
        self.assertIn("id='mobile-data-sources'", mobile)
        self.assertIn("top ranked evidence", mobile)
        self.assertIn("2 supply observations shown; not value-ranked", mobile)

    def test_mobile_data_is_a_searchable_filterable_source_workbench(self):
        page = self.page()
        self.assertIn("id='mobile-source-search'", page)
        for evidence in ("measured", "sampled", "unavailable"):
            self.assertIn(f"data-source-filter='{evidence}'", page)
        self.assertIn("data-source-group", page)
        self.assertIn("data-source-row", page)
        self.assertIn("class='mobile-data-snapshot' aria-label='Recorded snapshot'", page)
        self.assertNotIn("class='mobile-snapshot-strip'", page)
        snapshot = page.index("class='mobile-data-snapshot'")
        title = page.index("id='mobile-data-title'")
        links = page.index("class='data-domain-rail data-domain-rail--mobile'")
        evidence = page.index("id='mobile-validator-evidence'")
        people = page.index("id='mobile-people-markets'")
        sources = page.index("id='mobile-data-sources'")
        controls = page.index("id='mobile-source-search'")
        catalog = page.index("class='mobile-source-catalog'")
        self.assertLess(snapshot, title)
        self.assertLess(title, links)
        self.assertLess(links, evidence)
        self.assertLess(evidence, people)
        self.assertLess(people, sources)
        self.assertLess(sources, controls)
        self.assertLess(controls, catalog)
        self.assertIn("class='mobile-source-section-title'>Source catalog</h2>", page)
        self.assertIn(".mobile-search-wrap input:focus-visible { outline:0; }", render.CSS)
        self.assertIn("class='mobile-source-methods'", page)
        self.assertIn("body:has(#data:target) .mobile-topbar", page)
        self.assertIn("id='mobile-source-empty'", page)
        self.assertIn("data-reset-sources", page)
        self.assertIn("data-open-export", page)
        self.assertIn("<dialog id='mobile-export-dialog'", page)
        self.assertIn("class='mobile-sheet-handle'", page)
        self.assertIn("aria-label='Download report format'", page)
        for artifact in ("index.html", "report.md", "report.json"):
            self.assertIn(f"href='{artifact}'", page)

        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("sourceFilters.find", controller)
        self.assertIn("row.dataset.evidence === activeFilter", controller)
        self.assertIn("event.target === exportDialog", controller)
        self.assertIn("exportDialog.addEventListener('close'", controller)

    def test_mobile_methods_use_the_approved_reader_hierarchy(self):
        page = self.page()
        methods = render.render_mobile_methods()
        self.assertIn("mobile-methods-reader'", methods)
        self.assertIn("<h1 id='mobile-methods-title'", methods)
        self.assertIn("<ol class='mobile-method-pipeline'", methods)
        self.assertEqual(methods.count("class='mobile-method-stage'"), 5)
        self.assertEqual(methods.count("data-method-stage"), 5)
        self.assertNotIn("<details class='mobile-method-disclosure'", methods)
        self.assertNotIn("class='mobile-method-footer'", methods)
        for stage in ("Sources", "Normalize", "Validate", "Publish", "Recheck"):
            self.assertIn(f"<h2>{stage}</h2>", methods)
        for call in (
            "collect.sources", "collect.normalize", "pipeline.validate",
            "render.publish", "pipeline.recheck",
        ):
            self.assertIn(f"data-pipeline-call='{call}'", methods)
        self.assertIn("font-family:\"Archivo\"", page)
        self.assertIn('font-variation-settings: "wdth" 112.5', page)

    def test_mobile_methods_explain_evidence_rules_and_real_destinations(self):
        methods = render.render_mobile_methods()
        self.assertIn("<h2 id='mobile-evidence-labels-title'>Evidence labels</h2>", methods)
        for label, example in (("Measured", "network TPS"),
                               ("Sampled", "median non-vote transaction fee"),
                               ("Unavailable", "daily active addresses")):
            self.assertIn(f"<strong>{label}</strong>", methods)
            self.assertIn(example, methods)
        rules = methods.split("class='mobile-method-rules'", 1)[1].split("</ul>", 1)[0]
        self.assertEqual(rules.count("<li>"), 5)
        for href, label in (("#data", "Browse data sources"),
                            ("#history", "Compare recorded snapshots"),
                            ("report.md", "Read technical documentation"),
                            ("report.json", "Download JSON")):
            self.assertIn(f"href='{href}'", methods)
            self.assertIn(label, methods)
        self.assertIn("HTML, Markdown and JSON come from the same recorded snapshot", methods)
        self.assertIn(".mobile-methods-reader {", render.CSS)
        self.assertIn(".mobile-method-card {", render.CSS)
        self.assertIn("min-height: 44px", render.CSS)

    def test_mobile_history_starts_with_comparison_and_exposes_truthful_controls(self):
        fixture = load_fixture()
        earlier = json.loads(json.dumps(fixture))
        earlier["collected_at"] = "2026-08-05T13:36:00+00:00"
        comparison = TestDeltaRendering.comparison()
        page = self.page(history=[earlier, fixture], comparison=comparison)
        comparison_index = page.index("id='mobile-history-comparison'")
        selector_index = page.index("id='mobile-history-selector'")
        chart_index = page.index("class='mobile-history-chart-card'", selector_index)
        timeline_index = page.index("class='mobile-snapshot-timeline'", chart_index)
        ledger_index = page.index("class='mobile-history-ledger'", timeline_index)
        chronology_index = page.index("class='mobile-history-chronology'", ledger_index)
        self.assertLess(comparison_index, selector_index)
        self.assertLess(selector_index, chart_index)
        self.assertLess(chart_index, timeline_index)
        self.assertLess(timeline_index, ledger_index)
        self.assertLess(ledger_index, chronology_index)
        self.assertIn("class='mobile-history-header'", page)
        self.assertIn("class='mobile-history-pair-surface' id='mobile-history-selector'", page)
        self.assertNotIn("id='mobile-history-dialog'", page)
        self.assertNotIn("data-open-history-filter", page)
        self.assertIn("data-history-select-a", page)
        self.assertIn("data-history-select-b", page)
        self.assertIn("data-history-picker-trigger='a'", page)
        self.assertIn("data-history-picker-trigger='b'", page)
        self.assertIn("aria-label='Previous snapshot A:", page)
        self.assertIn("aria-label='Current snapshot B:", page)
        self.assertIn("id='mobile-history-picker-listbox' role='listbox'", page)
        self.assertIn(".mobile-history-picker-popover[hidden] { display:none; }", render.CSS)
        self.assertIn("aria-label='Recent snapshot timeline'", page)
        self.assertIn("role='status' aria-live='polite'", page)
        self.assertIn("Compare two recorded snapshots.", page)
        self.assertNotIn("data-interpolate", page)

    def test_mobile_history_combines_delta_hero_legend_chart_and_timeline(self):
        history = self.distinct_history()
        page = self.page(history=history)
        comparison_index = page.index("id='mobile-history-comparison'")
        newest_index = page.index("data-history-pair='1:2'", comparison_index)
        selector_index = page.index("id='mobile-history-selector'")
        chart_index = page.index("class='mobile-history-trend'", newest_index)
        legend_index = page.index("class='mobile-history-legend'", chart_index)
        timeline_index = page.index("class='mobile-snapshot-timeline'", legend_index)
        ledger_index = page.index("class='mobile-history-ledger'", timeline_index)
        chronology_index = page.index("class='mobile-history-chronology'", ledger_index)
        self.assertLess(comparison_index, selector_index)
        self.assertLess(selector_index, chart_index)
        self.assertLess(chart_index, legend_index)
        self.assertLess(legend_index, timeline_index)
        self.assertLess(timeline_index, ledger_index)
        self.assertLess(ledger_index, chronology_index)
        combined = page[newest_index:chronology_index]
        self.assertIn("Snapshot comparison", combined)
        self.assertIn("TPS (Transactions Per Second)", combined)
        self.assertIn("Mean slot time", combined)
        self.assertIn("Median fee", combined)
        self.assertIn("Delinquency", combined)
        self.assertIn("Missing samples remain explicit gaps", combined)
        self.assertIn("class='history-endpoint history-endpoint--a'", combined)
        self.assertIn("class='history-endpoint history-endpoint--b'", combined)
        self.assertNotIn("data-history-range", combined)
        self.assertNotIn("mobile-history-seam", combined)
        self.assertIn(".mobile-history-trend .chart-wrap { height:250px; }", render.CSS)
        self.assertIn(".mobile-history-chart-card { margin:0; overflow:hidden;", render.CSS)
        self.assertIn("background:var(--prototype-paper); box-shadow:var(--shadow-rest);", render.CSS)

    def test_mobile_history_keeps_delta_values_and_chart_type_legible(self):
        history = self.distinct_history()
        page = self.page(history=history)
        newest = page.split("data-history-pair='1:2'", 1)[1]
        self.assertIn("class='mobile-history-pair-surface' id='mobile-history-selector'", page)
        self.assertIn("data-history-picker-trigger='a'", page)
        self.assertIn("data-history-picker-trigger='b'", page)
        self.assertIn("<i class='mobile-history-versus' aria-hidden='true'>vs.</i>", page)
        self.assertIn("grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)", render.CSS)
        self.assertIn("data-history-select-a", page)
        self.assertIn("data-history-select-b", page)
        self.assertIn(".mobile-history-trend .chart-meta,.mobile-history-trend .chart-legend", render.CSS)
        self.assertIn(".mobile-history-trend .axis-label,.mobile-history-trend .annotation-label { fill:var(--prototype-muted); font-size:18px;", render.CSS)
        self.assertIn("Recorded · offline", newest)

    def test_mobile_history_wraps_only_unavailable_values(self):
        page = self.page(history=self.distinct_history())
        self.assertIn(
            "<td class='is-unavailable'>Unavailable</td>"
            "<td class='is-unavailable'>Unavailable</td>",
            page,
        )
        self.assertIn("<td>2,000.00</td>", page)
        self.assertIn(
            ".mobile-history-ledger td.is-unavailable { white-space:normal; overflow-wrap:anywhere; }",
            render.CSS,
        )
        self.assertNotIn(
            ".mobile-history-ledger td { white-space:normal; overflow-wrap:anywhere; }",
            render.CSS,
        )

    def test_mobile_history_pair_selector_controls_every_comparison_surface(self):
        history = self.distinct_history()
        page = self.page(history=history)
        history_markup = render.render_mobile_history(history, None)
        self.assertIn("data-history-pair='0:1'", history_markup)
        self.assertIn("data-history-pair='0:2'", history_markup)
        self.assertIn("data-history-pair='1:2'", history_markup)
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("panel.dataset.historyPair !== pair", controller)
        self.assertIn("option.disabled = Number(option.value) >= b", controller)
        self.assertIn("option.disabled = Number(option.value) <= a", controller)
        self.assertIn("optionButton.setAttribute('role', 'option')", controller)
        self.assertIn("filter((option) => !option.disabled)", controller)
        self.assertIn("footer.textContent = 'View all snapshots'", controller)
        self.assertIn("relative.textContent = 'Selected'", controller)
        self.assertIn("closeHistoryPicker(true)", controller)
        self.assertIn("event.key === 'Escape'", controller)
        self.assertIn("'ArrowDown', 'ArrowUp', 'Home', 'End'", controller)
        self.assertIn("trigger.setAttribute('aria-label', `${role}: ${selected.textContent}`)", controller)
        self.assertIn("item.setAttribute('aria-current', 'true')", controller)
        self.assertIn("item.removeAttribute('aria-current')", controller)
        self.assertIn("historySelectA.value = String(Math.max(0, b - 1))", controller)
        self.assertIn(
            ".mobile-history-panel .mobile-snapshot-timeline button[aria-current='true']::before"
            " { top:2px; width:22px; height:22px; border-color:var(--prototype-violet);",
            render.CSS,
        )
        self.assertIn("const viewport = window.visualViewport", controller)
        self.assertIn("window.visualViewport?.addEventListener('resize'", controller)
        self.assertIn("window.visualViewport?.addEventListener('scroll'", controller)
        self.assertIn("safe-area-inset-top", render.CSS)

    def test_mobile_history_picker_nesting_spacing_and_motion(self):
        history = self.distinct_history()
        page = self.page(history=history)
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn(
            ".mobile-history-picker-row .mobile-history-picker-trigger { display:grid;"
            " grid-template-columns:22px minmax(0,1fr) 16px; width:100%; min-height:44px;",
            render.CSS,
        )
        self.assertIn(
            ".mobile-history-picker-row { display:block; min-width:0; padding:0; }",
            render.CSS,
        )
        self.assertIn(
            ".mobile-history-pair-surface { position:relative; display:grid;"
            " grid-template-columns:minmax(0,1fr) 20px minmax(0,1fr);",
            render.CSS,
        )
        self.assertIn(
            ".mobile-history-picker-option { display:grid; grid-template-columns:minmax(0,1fr) auto;"
            " min-height:44px; align-items:center;",
            render.CSS,
        )
        self.assertIn(
            ".mobile-history-picker-option:focus-visible { outline:2px solid var(--prototype-violet); outline-offset:-2px; }",
            render.CSS,
        )
        self.assertIn(
            ".prototype-page--archive .mobile-history-picker-option:focus-visible { border-radius:5px;"
            " outline:2px solid var(--prototype-violet); outline-offset:-2px; }",
            render.CSS,
        )
        self.assertIn(
            ".mobile-history-picker-popover.is-closing { opacity:0; transform:translateY(-4px) scale(.98); }",
            render.CSS,
        )
        self.assertIn(
            "@starting-style { .mobile-history-picker-popover:not(.is-closing) { opacity:0;"
            " transform:translateY(-4px) scale(.98); } }",
            render.CSS,
        )
        self.assertIn("transition:opacity 140ms ease, transform 140ms ease", render.CSS)
        self.assertIn("historyPicker.classList.add('is-closing')", controller)
        self.assertIn("historyPickerHideTimer = setTimeout(finishHistoryPickerClose, 140)", controller)
        self.assertIn("historyPickerCloseMotion.matches", controller)
        self.assertIn("historyPicker.classList.remove('is-closing')", controller)
        self.assertIn("const menuWidth = Math.min(Math.max(rect.width, 300), rightBound - leftBound)", controller)
        self.assertIn("rightBound - menuWidth", controller)
        self.assertIn(
            ".prototype-page--archive .mobile-history-picker-row .mobile-history-picker-trigger"
            " { padding-inline:5px; font-size:11px; }",
            render.CSS,
        )
        self.assertLess(
            controller.index("const finishHistoryPickerClose = () => {"),
            controller.index("historyPicker.hidden = true"),
        )
        self.assertLess(
            controller.index("historyPicker.hidden = true"),
            controller.index("const closeHistoryPicker"),
        )

    def test_full_document_has_unique_ids_with_desktop_and_mobile_history_charts(self):
        history = self.distinct_history()
        page = self.page(history=history)
        ids = re.findall(r"\bid=['\"]([^'\"]+)", page)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("history-chart-desktop-title", ids)
        self.assertIn("history-chart-mobile-1-2-title", ids)

    def test_mobile_history_caps_interactive_pairs_but_keeps_full_history_in_exports(self):
        history = []
        for index in range(20):
            snapshot = load_fixture()
            snapshot["collected_at"] = f"2026-08-{index + 1:02d}T00:00:00+00:00"
            snapshot["performance"]["latest_tps"] = 1_000 + index
            history.append(snapshot)
        markup = render.render_mobile_history(history, None)
        self.assertIn("Compare two recorded snapshots.", markup)
        self.assertEqual(markup.count("data-history-pair="), 10)
        self.assertNotIn("Recent 4 of 20 snapshots", markup)
        self.assertLess(len(markup), 1_000_000)

    def test_truthful_desktop_content_is_not_height_clipped(self):
        self.assertIn(
            ".prototype-page--methods .flow-frame { max-height: none; overflow-x: auto; overflow-y: visible; }",
            render.CSS,
        )
        self.assertNotIn("max-height: 214px", render.CSS)
        self.assertIn(
            ".prototype-page--archive .archive-layout { min-height: 401px; max-height: none; }",
            render.CSS,
        )
        self.assertIn(".prototype-page--archive .change-panel { overflow: visible; }", render.CSS)

    def test_desktop_history_footer_uses_the_full_viewport_shell(self):
        self.assertIn("#history:target,", render.CSS)
        self.assertIn("#history > .page-footnote {\n        margin-top: auto;", render.CSS)

    def test_wide_history_uses_the_available_canvas(self):
        self.assertIn("width: min(1840px, 100%);", render.CSS)
        self.assertIn(".prototype-page--archive .archive-layout { gap: 16px; }", render.CSS)
        self.assertNotIn("min-height: clamp(650px, 36vw, 800px)", render.CSS)
        self.assertIn("aspect-ratio: 2.95 / 1;", render.CSS)
        self.assertNotIn(".prototype-page--archive .chart-wrap { aspect-ratio: 2.1 / 1; }", render.CSS)

    def test_desktop_history_uses_its_summary_rail_instead_of_capping_the_chart(self):
        self.assertIn("<aside class='comparison-summary'", self.page())
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(280px, .34fr);", render.CSS)
        self.assertIn(".prototype-page--archive .chart-disclosure {\n        margin-top: 32px;", render.CSS)
        self.assertIn(".prototype-page--archive .comparison-summary-metric span { min-width: 0; }", render.CSS)
        self.assertNotIn("width: min(100%, 680px);", render.CSS)

    def test_portrait_desktop_uses_stable_kpis_and_carousel_layout(self):
        self.assertIn("@media (min-width: 701px) and (max-width: 1279px)", render.CSS)
        self.assertIn("grid-auto-columns: clamp(320px, 27vw, 420px);", render.CSS)
        self.assertIn("scroll-snap-type: x mandatory;", render.CSS)
        self.assertIn("grid-template-columns: .9fr repeat(4, 1fr);", render.CSS)
        self.assertIn(".prototype-page--report .metric { grid-column: auto; }", render.CSS)
        self.assertNotIn("padding-top:clamp(12px,1.2vw,16px); border-top:1px solid var(--rule)", render.CSS)
        self.assertNotIn(".prototype-page--report .chart-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }", render.CSS)
        self.assertIn("@media (min-width: 701px) and (max-aspect-ratio: 4 / 5)", render.CSS)
        self.assertIn(".prototype-page--report .insights { grid-template-columns:1fr; }", render.CSS)
        self.assertIn("@media (min-width: 701px) and (max-width: 820px)", render.CSS)
        self.assertIn(".prototype-header { grid-template-columns:auto minmax(0,1fr) auto; }", render.CSS)
        self.assertIn(".prototype-status { display:none; }", render.CSS)
        self.assertIn(".prototype-page--report .metric:last-child .metric__label", render.CSS)
        self.assertIn("white-space:normal;", render.CSS)

    def test_global_visually_hidden_utility_and_method_rules_are_present(self):
        page = self.page()
        global_rule = page.split("/* Mobile command deck", 1)[1].split(".mobile-topbar", 1)[0]
        self.assertIn(".visually-hidden", global_rule)
        self.assertIn("clip: rect(0, 0, 0, 0)", global_rule)
        self.assertIn("id='mobile-method-rules-title'", page)
        for truth in ("Missing data never becomes zero", "Sampled values stay labelled",
                      "Chart lines stop across missing observations",
                      "crossing a declared threshold"):
            self.assertIn(truth, page)

    def test_no_javascript_history_hides_inert_controls_and_lists_recorded_facts(self):
        history = self.distinct_history()
        mobile_history = render.render_mobile_history(history, None)
        fallback = mobile_history.split("<noscript>", 1)[1].split("</noscript>", 1)[0]

        self.assertIn("#mobile-history-selector { display: none !important; }", fallback)
        self.assertIn("<section class='mobile-noscript-history'", fallback)
        for hour, tps in ((8, "1,000.00 TPS"), (9, "2,000.00 TPS"), (10, "2,000.00 TPS")):
            self.assertIn(f"datetime='2026-08-05T{hour:02d}:00:00+00:00'", fallback)
            self.assertIn(tps, fallback)
        self.assertIn("Latest-pair comparison is shown above.", fallback)
        self.assertIn("Interactive snapshot and comparison selection requires JavaScript.", fallback)
        self.assertNotIn("delta", fallback.lower())

    def test_no_javascript_history_does_not_claim_a_pair_without_two_snapshots(self):
        fallback = render.render_mobile_history([], None).split("<noscript>", 1)[1].split("</noscript>", 1)[0]
        self.assertIn("No latest-pair comparison is available", fallback)
        self.assertNotIn("Latest-pair comparison is shown above.", fallback)

    def test_mobile_history_distinguishes_one_snapshot_from_empty_history(self):
        markup = render.render_mobile_history([load_fixture()], None)

        self.assertIn("One recorded snapshot", markup)
        self.assertIn("A second snapshot is needed before comparison.", markup)
        self.assertNotIn("No recorded snapshots", markup)

    def test_no_javascript_export_hides_dead_button_and_exposes_direct_artifacts(self):
        mobile_data = render.render_mobile_data(load_fixture())
        fallback = mobile_data.split("<noscript>", 1)[1].split("</noscript>", 1)[0]

        self.assertIn("[data-open-export] { display: none !important; }", fallback)
        self.assertIn("<nav class='mobile-noscript-exports' aria-label='Export artifacts'>", fallback)
        for artifact, label in (("index.html", "HTML report"),
                                ("report.md", "Markdown report"),
                                ("report.json", "Recorded JSON")):
            self.assertIn(f"<a href='{artifact}'>{label}</a>", fallback)

    def test_mobile_project_is_an_anchored_editorial_reader(self):
        page = self.page()
        mobile = page.split("<article class='mobile-project-reader'", 1)[1].split("</article>", 1)[0]
        self.assertIn("<article class='mobile-project-reader'", page)
        self.assertNotIn("<nav class='mobile-section-index'", mobile)
        self.assertEqual(mobile.count("mobile-project-section"), 0)
        self.assertEqual(mobile.count("<section class='mobile-project-development'"), 1)
        for copy in ("About this report", "Current snapshot", "What it helps you understand",
                     "Inspect the work"):
            self.assertIn(copy, page)

    def test_project_editorial_briefing_has_a_permanent_hero_and_supporting_feed(self):
        page = render.render_html(editorial_fixture())
        self.assertEqual(page.count("data-project-editorial>"), 2)
        self.assertEqual(page.count("data-editorial-hero>"), 2)
        self.assertIn("Recorded ecosystem briefing", page)
        self.assertIn("Latest across Solana", page)
        self.assertIn("Latest updates", page)
        self.assertIn("data-editorial-filter='all' aria-pressed='true'", page)
        self.assertIn("Sources and recording state", page)
        self.assertIn("publisher images are not scraped or hotlinked", page)
        self.assertIn("aspect-ratio: 16 / 9;", render.CSS)
        self.assertIn("font-size: clamp(18px, 1.4vw, 22px);", render.CSS)
        self.assertIn(
            "background: radial-gradient(ellipse at center, transparent 72%, var(--paper) 100%);",
            render.CSS,
        )

        for context in ("desktop", "mobile"):
            markup = render.render_project_editorial(editorial_fixture(), context)
            self.assertEqual(markup.count("data-editorial-hero>"), 1)
            self.assertEqual(markup.count("<li data-editorial-item "), 3)
            self.assertEqual(markup.count("Read the primary source →"), 1)
            self.assertIn("target='_blank' rel='noopener noreferrer'", markup)
            self.assertNotIn("<img", markup)

        desktop = render.render_project_content(editorial_fixture())
        mobile = render.render_mobile_project(editorial_fixture())
        for surface in (desktop, mobile):
            self.assertLess(surface.index("Current snapshot"), surface.index("data-project-editorial>"))
            self.assertLess(surface.index("data-project-editorial>"), surface.index("What it helps you understand"))

    def test_release_stories_use_the_pinned_hero_and_card_rasters(self):
        snapshot = editorial_fixture()
        snapshot["news"]["featured_item_id"] = "github-release:1"
        snapshot["news"]["items"][1]["title"] = "Release v4.3.0-beta.2"
        markup = render.render_project_editorial(snapshot, "desktop")

        self.assertIn(
            "project-editorial__art' data-release-art='v4.3.0-beta.2'", markup,
        )
        self.assertIn(
            "project-editorial__card-art project-editorial__art--release", markup,
        )
        self.assertIn("project-editorial__art--network", markup)
        self.assertNotIn("project-editorial__release-art", markup)

    def test_editorial_hero_and_cards_have_one_canonical_whole_surface_link(self):
        expected = (
            ("Network update", "Solana Status", "https://status.solana.com/notices/1"),
            ("Agave v4.1.0", "Anza", "https://github.com/anza-xyz/agave/releases/tag/v4.1.0"),
            ("Scheduled maintenance completed", "Solana Status", "https://status.solana.com/notices/2"),
            ("Agave v4.0.0", "Anza", "https://github.com/anza-xyz/agave/releases/tag/v4.0.0"),
        )

        for context in ("desktop", "mobile"):
            with self.subTest(context=context):
                markup = render.render_project_editorial(editorial_fixture(), context)
                hero_match = re.search(
                    r"<article class='project-editorial__hero' data-editorial-hero>.*?</article>",
                    markup,
                    re.DOTALL,
                )
                self.assertIsNotNone(hero_match)
                articles = [
                    hero_match.group(0),
                    *re.findall(
                        r"<li data-editorial-item[^>]*>(<article>.*?</article>)</li>",
                        markup,
                        re.DOTALL,
                    ),
                ]

                self.assertEqual(len(articles), 4)
                self.assertEqual(markup.count("data-editorial-story-link"), 4)
                self.assertEqual(markup.count("<a "), 5)
                hrefs = []
                for article in articles:
                    self.assertEqual(article.count("<a "), 1)
                    self.assertEqual(
                        article.count("target='_blank' rel='noopener noreferrer'"),
                        1,
                    )
                    self.assertIn("class='project-editorial__story-link'", article)
                    self.assertIn("data-editorial-story-link", article)
                    self.assertNotIn("tabindex=", article)
                    self.assertNotIn("onclick=", article)
                    self.assertNotIn("role='link'", article)
                    href_match = re.search(r"href='([^']+)'", article)
                    self.assertIsNotNone(href_match)
                    hrefs.append(href_match.group(1))

                self.assertCountEqual(hrefs, [href for _title, _publisher, href in expected])
                for title, publisher, _href in expected:
                    article = next(fragment for fragment in articles if title in fragment)
                    self.assertIn(f"aria-label='Read {title} at {publisher}'", article)

        self.assertRegex(
            render.CSS,
            re.compile(r"\.project-editorial__hero\s*\{[^}]*position:\s*relative;", re.DOTALL),
        )
        self.assertRegex(
            render.CSS,
            re.compile(
                r"\.project-editorial__stories > li\s*\{[^}]*position:\s*relative;",
                re.DOTALL,
            ),
        )
        overlay = render.CSS.split(".project-editorial__story-link::after {", 1)[1].split("}", 1)[0]
        self.assertIn("position: absolute", overlay)
        self.assertIn("z-index: 1", overlay)
        self.assertIn("inset: 0", overlay)
        focus = render.CSS.split(
            ".project-editorial__story-link:focus-visible::after {", 1
        )[1].split("}", 1)[0]
        self.assertIn("outline: 2px solid var(--violet)", focus)
        self.assertRegex(
            render.CSS,
            re.compile(
                r"\.project-editorial__hero:has\([^}]+:hover,[^{]+\{[^}]+"
                r"box-shadow:\s*var\(--shadow-raised\);",
                re.DOTALL,
            ),
        )

    def test_editorial_filters_are_progressive_and_do_not_hide_the_hero(self):
        page = render.render_html(editorial_fixture())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("const editorialBriefings", controller)
        self.assertIn("controls.hidden = false", controller)
        self.assertIn("story.hidden = !matches", controller)
        self.assertNotIn("data-editorial-hero", controller)
        markup = render.render_project_editorial(editorial_fixture(), "desktop")
        self.assertIn("data-editorial-controls hidden", markup)
        self.assertGreater(markup.count("data-editorial-item"), 0)

    def test_hostile_normalized_editorial_values_are_escaped(self):
        snapshot = editorial_fixture()
        payload = "<img src=x onerror=alert('editorial-probe')>"
        snapshot["news"]["items"] = [{
            "id": "hostile",
            "source_id": "agave_releases",
            "publisher": payload,
            "category": "release",
            "title": payload,
            "canonical_url": "javascript:alert(1)",
            "published_at": None,
            "recorded_at": snapshot["collected_at"],
            "state": "recorded",
            "editorial_note": payload,
            "art_seed": "hostile",
        }]
        snapshot["news"]["featured_item_id"] = "hostile"
        markup = render.render_project_editorial(snapshot, "desktop")
        self.assertNotIn(payload, markup)
        self.assertNotIn("javascript:", markup)
        self.assertIn("&lt;img src=x onerror=alert", markup)

    def test_off_host_https_editorial_link_is_not_activated_during_replay(self):
        snapshot = editorial_fixture()
        snapshot["news"]["items"][0]["canonical_url"] = "https://attacker.example/story"

        markup = render.render_project_editorial(snapshot, "desktop")

        self.assertNotIn("attacker.example", markup)
        hero = markup.split("data-editorial-hero>", 1)[1].split("</article>", 1)[0]
        self.assertIn("<h3><span>Network update</span></h3>", hero)
        self.assertNotIn("data-editorial-story-link", hero)
        self.assertNotIn("<a ", hero)
        self.assertIn("Primary source link unavailable", hero)

    def test_inline_controller_is_single_safe_and_updates_accessibility_state(self):
        page = self.page()
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("addEventListener('hashchange'", controller)
        self.assertIn("setAttribute('aria-current', 'page')", controller)
        self.assertIn("syncRoute(false);", controller)
        self.assertIn("focus({preventScroll: true})", controller)
        self.assertIn("showModal()", controller)
        self.assertIn("textContent", controller)
        for forbidden in ("innerHTML", "eval(", "new Function", "fetch(",
                          "XMLHttpRequest", "WebSocket", "EventSource"):
            self.assertNotIn(forbidden, controller)

    def test_mobile_network_state_is_compact_and_inline_with_its_label(self):
        page = self.page()
        self.assertRegex(
            page,
            re.compile(r"\.mobile-overview-header\s*\{[^}]*display:flex[^}]*align-items:baseline", re.DOTALL),
        )
        self.assertRegex(
            page,
            re.compile(r"\.mobile-view \.mobile-network-state\s*\{[^}]*font-size:40px[^}]*font-weight:800", re.DOTALL),
        )

    def test_mobile_network_state_distinguishes_unhealthy_from_unavailable(self):
        healthy = load_fixture()
        unhealthy = load_fixture()
        unhealthy["network"]["healthy"] = False
        unavailable = load_fixture()
        unavailable["network"].pop("healthy")
        malformed = load_fixture()
        malformed["network"]["healthy"] = "unknown"
        for snapshot in (healthy, unhealthy, unavailable, malformed):
            snapshot["schema_version"] = 8

        healthy_overview = render.render_mobile_overview(healthy, None, [], "Latest")
        unhealthy_overview = render.render_mobile_overview(unhealthy, None, [], "Latest")
        self.assertIn("data-state='healthy'", healthy_overview)
        self.assertIn("data-state='healthy' tabindex='-1'>", healthy_overview)
        self.assertIn("</svg>RPC healthy</h1>", healthy_overview)
        self.assertIn("data-state='unhealthy'", unhealthy_overview)
        self.assertIn("data-state='unhealthy' tabindex='-1'>RPC unhealthy</h1>", unhealthy_overview)
        for degraded in (unavailable, malformed):
            overview = render.render_mobile_overview(degraded, None, [], "Latest")
            self.assertIn("data-state='unavailable'", overview)
            self.assertIn("data-state='unavailable' tabindex='-1'>RPC health unavailable</h1>", overview)
            self.assertNotIn("data-state='unhealthy'", overview)

    def test_noncomparable_notice_is_neutral_unless_metrics_are_unavailable(self):
        complete_history = self.distinct_history()
        for snapshot in complete_history:
            snapshot["schema_version"] = 8
            for spec in render.delta_module.facts.METRICS.values():
                node = snapshot
                for key in spec["path"][:-1]:
                    node = node.setdefault(key, {})
                node[spec["path"][-1]] = 1
        neutral = render.render_mobile_history(complete_history, None)
        complete_history[-1]["performance"]["latest_tps"] = None
        alert = render.render_mobile_history(complete_history, None)

        self.assertNotIn("data-history-comparison-limits", neutral)
        self.assertIn("data-history-comparison-limits", alert)
        self.assertIn("Latest TPS:", alert)
        self.assertIn("<td class='is-unavailable'>Unavailable</td>", alert)

    def test_programmatic_mobile_heading_focus_has_visible_outline(self):
        page = self.page()
        self.assertIn(
            ".mobile-view h1[tabindex='-1']:focus{outline:2px solid var(--prototype-violet);outline-offset:2px}",
            page,
        )
        self.assertNotIn(".mobile-view h1[tabindex='-1']:focus{outline:none}", page)
        self.assertIn(".mobile-view :focus-visible", page)
        self.assertNotIn(".mobile-view :focus { outline: none; }", page)

    def test_hash_controller_focuses_visible_mobile_headings(self):
        page = self.page()
        for route in ("overview", "data", "methods", "history", "project"):
            self.assertIn(f"id='mobile-{route}-title'", page)
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        for route in ("overview", "data", "methods", "history", "project"):
            self.assertIn(f"['{route}', 'mobile-{route}-title']", controller)
        self.assertIn("matchMedia('(max-width: 700px)')", controller)

    def test_history_selection_updates_a_truthful_recorded_snapshot_inspector(self):
        fixture = load_fixture()
        earlier = json.loads(json.dumps(fixture))
        earlier["collected_at"] = "2026-08-05T08:00:00+00:00"
        earlier["performance"]["latest_tps"] = 1234.5
        page = self.page(history=[earlier, fixture])
        self.assertIn("class='mobile-history-ledger'", page)
        self.assertIn("<td>1,234.50</td>", page)
        self.assertIn("<td>4,213.50</td>", page)
        self.assertIn("class='mobile-history-chronology'", page)
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("panel.hidden = panel.dataset.historyPair !== pair", controller)
        self.assertIn("const pair = `${a}:${b}`", controller)
        self.assertNotIn(">←</button>", page)
        self.assertNotIn(">→</button>", page)

    def test_every_valid_mobile_snapshot_pair_has_a_truthful_comparison_panel(self):
        history = self.distinct_history()
        page = self.page(history=history, comparison=TestDeltaRendering.comparison())
        panels = re.findall(
            r"<section class='mobile-history-panel' data-history-panel data-history-pair='([0-9:]+)'(.*?)</section>",
            page,
            re.DOTALL,
        )
        self.assertEqual([pair for pair, _ in panels], ["0:1", "0:2", "1:2"])
        first_pair, wide_pair, latest_pair = [body for _, body in panels]
        self.assertIn("<td>1,000.00</td><td>2,000.00</td><td class='is-good'>+1,000.00</td>", first_pair)
        self.assertIn("<td>1,000.00</td><td>2,000.00</td><td class='is-good'>+1,000.00</td>", wide_pair)
        self.assertIn("<td>2,000.00</td><td>2,000.00</td><td class='is-neutral'>0</td>", latest_pair)
        self.assertIn("Mean slot time", latest_pair)
        self.assertIn("<td>0.400 s</td><td>0.800 s</td><td class='is-bad'>+0.400 s</td>", latest_pair)
        self.assertNotIn("previous 3,000 TPS", page)

    def test_mobile_history_counts_only_changed_metrics_not_the_empty_placeholder(self):
        earlier = load_fixture()
        later = deepcopy(earlier)
        earlier["collected_at"] = "2026-08-05T03:00:00+00:00"
        later["collected_at"] = "2026-08-05T09:00:00+00:00"

        mobile = render.render_mobile_history([earlier, later], None)

        self.assertEqual(mobile.count("<tr><th scope='row'>"), 4)
        self.assertNotIn("No metric moved past its threshold", mobile)

    def test_mobile_history_states_the_refusal_reason_for_a_non_ok_comparison(self):
        # Equivalent metrics remain comparable across schemas while corrected
        # metrics preserve an explicit semantic gap.
        earlier, later = self.distinct_history()[:2]
        earlier["schema_version"] = 3
        later["schema_version"] = 4
        mobile = render.render_mobile_history([earlier, later], None)

        self.assertIn("sample_mean_rev_sol is not semantically compatible", mobile)
        self.assertNotIn("Comparison not published", mobile)
        self.assertIn("data-history-comparison-limits", mobile)
        self.assertNotIn(
            "No additional noncomparable metrics were recorded", mobile)

    def test_mobile_history_delta_classes_match_positive_and_negative_signed_values(self):
        def history(previous_tps, current_tps):
            snapshots = []
            for hour, tps in ((8, previous_tps), (9, current_tps)):
                snapshot = load_fixture()
                snapshot["collected_at"] = f"2026-08-05T{hour:02d}:00:00+00:00"
                snapshot["performance"]["latest_tps"] = tps
                snapshots.append(snapshot)
            return snapshots

        positive = render.render_mobile_history(history(1000.0, 2000.0), None)
        negative = render.render_mobile_history(history(2000.0, 1000.0), None)

        self.assertIn("<td>1,000.00</td><td>2,000.00</td><td class='is-good'>+1,000.00</td>", positive)
        self.assertIn("<td>2,000.00</td><td>1,000.00</td><td class='is-bad'>−1,000.00</td>", negative)
        self.assertIn(".mobile-history-ledger td.is-good { color:#16863d; }", render.CSS)
        self.assertIn(".mobile-history-ledger td.is-bad { color:#b42318; }", render.CSS)

    def test_history_controller_switches_matching_snapshot_pair_together(self):
        page = self.page(history=self.distinct_history())
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("querySelectorAll('[data-history-panel]')", controller)
        self.assertIn("panel.dataset.historyPair !== pair", controller)
        self.assertIn("const historySelectA", controller)
        self.assertIn("const historySelectB", controller)
        self.assertIn("Comparing A ${labelA} with B ${labelB}", controller)

    def test_nested_project_hashes_keep_project_active_and_focus_requested_section(self):
        page = self.page()
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("document.getElementById(fragment)", controller)
        self.assertIn("target ? target.closest('.prototype-page') : null", controller)
        self.assertIn("if (page && routes.has(page.id)) return page.id", controller)
        self.assertIn("target.focus({preventScroll: true})", controller)
        self.assertIn("id='mobile-project-development' tabindex='-1'", page)

    def test_mobile_route_navigation_scrolls_the_visible_heading_into_view(self):
        page = self.page()
        controller = page.split("<script data-mobile-controller>", 1)[1].split("</script>", 1)[0]
        self.assertIn("routes.has(fragment) ? document.getElementById(routeTargets.get(route))", controller)
        self.assertIn("target.scrollIntoView({block: 'start'})", controller)

    def test_mobile_data_derives_degraded_source_and_analysis_states(self):
        degraded = {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {},
            "network": {},
            "performance": {"available": False},
            "validators": {"available": False},
            "activity": {"available": False},
            "economics": {"available": False},
            "news": {"available": False},
        }
        mobile_data = render.render_mobile_data(degraded)
        for source in ("Public Solana JSON-RPC", "Anomaly analysis", "Snapshot delta"):
            row = mobile_data.split(f"<strong>{source}</strong>", 1)[1].split("</article>", 1)[0]
            self.assertNotIn("Recorded", row)
        self.assertIn("<strong>Public Solana JSON-RPC</strong><span class='mobile-source-status'>Unavailable</span>", mobile_data)
        self.assertIn("<strong>Anomaly analysis</strong><span class='mobile-source-status'>Not assessable</span>", mobile_data)
        self.assertIn("<strong>Snapshot delta</strong><span class='mobile-source-status'>Not comparable</span>", mobile_data)
        self.assertIn(".mobile-source-row[data-availability='unavailable'] .mobile-source-status{color:var(--prototype-muted)}", render.CSS)
        self.assertIn(".mobile-source-row[data-availability='unavailable']::before { background:var(--prototype-muted); box-shadow:0 0 0 1px var(--prototype-muted); }", render.CSS)
        self.assertNotIn(".mobile-source-row[data-availability='unavailable'] .mobile-source-status{color:#c84b35}", render.CSS)

    def test_mobile_data_records_analysis_and_delta_only_when_inputs_are_ok(self):
        analysis = {"status": "ok"}
        comparison = TestDeltaRendering.comparison()
        mobile_data = render.render_mobile_data(load_fixture(), analysis, comparison)
        for source in ("Anomaly analysis", "Snapshot delta"):
            self.assertIn(
                f"<strong>{source}</strong><span class='mobile-source-status'>Derived</span>",
                mobile_data,
            )

    def test_mobile_data_marks_incomplete_growth_source_batches_partial(self):
        snapshot = load_fixture()
        snapshot["growth"] = {
            "available": True,
            "sources": {
                "registry": {"available": True, "coverage_complete": True},
                "supply": {"available": True, "sweep_complete": False},
                "dex_volume": {"available": True, "transport_complete": True,
                               "market_coverage": "partial"},
                "proof_of_reserves": {"available": True, "coverage_complete": False},
                "activity_benchmark": {"available": True, "canonical": False},
            },
        }
        mobile_data = render.render_mobile_data(snapshot)
        for source, evidence in (("Finalized token supply", "measured"),
                                 ("Indexed Solana DEX-pool volume", "measured")):
            self.assertIn(
                f"<strong>{source}</strong><span class='mobile-source-status'>Partial</span>",
                mobile_data,
            )
            row = mobile_data.split(f"<strong>{source}</strong>", 1)[0].rsplit("<article", 1)[1]
            self.assertIn("data-availability='available'", row)
            self.assertIn(f"data-evidence='{evidence}'", row)
        self.assertIn(
            "<strong>Solana Foundation xStock registry</strong><span class='mobile-source-status'>Recorded</span>",
            mobile_data,
        )
        self.assertIn(
            "<strong>Issuer proof of reserves</strong><span class='mobile-source-status'>Unavailable</span>",
            mobile_data,
        )
        benchmark_row = mobile_data.split("<strong>Solana Data provider ranges</strong>", 1)[0].rsplit("<article", 1)[1]
        self.assertIn("data-availability='unavailable'", benchmark_row)
        self.assertIn("data-evidence='recorded'", benchmark_row)
        self.assertIn("<span class='mobile-source-status'>Unavailable</span>", mobile_data)
        self.assertIn("[data-source-row].is-filtered-out {", render.CSS)
        self.assertIn("min-height: 0;", render.CSS)

    def test_held_unavailable_growth_source_never_renders_as_partial_or_available(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 9
        snapshot["growth"] = {
            "available": True,
            "tokenized_equities": {"available": False, "volume": {
                "available": False,
                "reason": "Redistribution rights unresolved.",
            }},
            "sources": {"dex_volume": {
                "available": False,
                "partial": True,
                "held": True,
                "reason": "Redistribution rights unresolved.",
            }},
        }

        catalog = render.render_data_catalog(snapshot, None, None, [])
        mobile = render.render_mobile_data(snapshot)

        self.assertIn("Indexed Solana DEX-pool volume", catalog)
        self.assertIn("Unavailable", catalog)
        row = mobile.split("<strong>Indexed Solana DEX-pool volume</strong>", 1)[0].rsplit(
            "<article", 1,
        )[1]
        self.assertIn("data-availability='unavailable'", row)
        self.assertIn("<span class='mobile-source-status'>Unavailable</span>", mobile)
        self.assertNotIn(
            "<strong>Indexed Solana DEX-pool volume</strong>"
            "<span class='mobile-source-status'>Partial</span>",
            mobile,
        )

    def test_catalog_and_mobile_keep_partial_news_and_provider_coverage_partial(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 8
        snapshot["news"] = {
            "available": True,
            "current_status": {"available": False, "partial": True,
                               "description": "Status response unavailable"},
            "sources": {
                "agave_releases": {"available": True, "partial": False},
                "network_status": {"available": False, "reason": "unreachable"},
            },
        }
        snapshot["growth"] = {
            "available": True,
            "sources": {"activity_benchmark": {
                "available": True, "active_addresses_available": True,
                "fee_payers_available": False,
            }},
            "daily_active_addresses": {
                "available": True, "date": "2026-08-21", "provider_count": 2,
                "minimum": 100, "maximum": 200,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "display_name": "Stablecoin active-address provider range",
                "source_label": "Active Addresses",
                "scope": "provider observations for Solana stablecoin activity, not network-wide DAA or unique humans",
            },
            "daily_fee_payers": {"available": False},
            "tokenized_equities": {"available": False},
        }
        catalog = render.render_data_catalog(snapshot, None, None, [])
        mobile = render.render_mobile_data(snapshot)
        for label, output in (("Official release feeds", catalog),
                              ("Solana Data provider ranges", catalog),
                              ("Official release feeds", mobile),
                              ("Solana Data provider ranges", mobile)):
            row = output.split(label, 1)[1].split("</tr>" if output is catalog else "</article>", 1)[0]
            self.assertIn("Partial", row)

    def test_mobile_about_preserves_the_core_orientation_and_links(self):
        page = self.page()
        mobile = page.split("<article class='mobile-project-reader'", 1)[1].split("</article>", 1)[0]
        for copy in (
            "About this report", "Current snapshot",
            "What it helps you understand", "Inspect the work", "Development stream",
        ):
            self.assertIn(copy, mobile)
        for label, target in (
            ("Methodology", "#methods"), ("Data catalog", "#data"),
        ):
            self.assertIn(f"href='{target}'><strong>{label}</strong>", mobile)

    def test_about_route_orients_readers_without_repeating_methods(self):
        page = self.page()
        desktop = page.split("<section class='view prototype-page prototype-page--about'", 1)[1]
        desktop = desktop.split("<div class='mobile-view' data-route='project'", 1)[0]
        mobile = page.split("<article class='mobile-project-reader'", 1)[1].split("</article>", 1)[0]
        for surface in (desktop, mobile):
            self.assertIn("About this report", surface)
            self.assertIn("Current snapshot", surface)
            self.assertIn("What it helps you understand", surface)
            self.assertIn("Inspect the work", surface)
            self.assertIn("href='#methods'", surface)
            self.assertIn("href='#data'", surface)
            self.assertNotIn("Evidence states", surface)
            self.assertNotIn("Our commitment", surface)
            self.assertNotIn("How it is made", surface)
        self.assertEqual(mobile.count("mobile-project-section"), 0)
        self.assertEqual(mobile.count("<section class='mobile-project-development'"), 1)

    def test_hostile_recorded_values_are_escaped_in_mobile_surfaces(self):
        hostile = load_fixture()
        payload = "<img src=x onerror=alert('mobile-probe')>"
        hostile["collected_at"] = payload
        hostile["source"]["endpoint"] = payload
        page = render.render_html(hostile, history=[hostile])
        self.assertIn("class='mobile-about-snapshot'", page)
        self.assertNotIn(payload, page)
        self.assertIn("&lt;img src=x onerror=alert(&#x27;mobile-probe&#x27;)&gt;", page)

    def test_an_absent_activity_section_degrades_like_any_other(self):
        # A v1 snapshot predates block sampling entirely and has no such key.
        page = render.render_html({
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
        })
        self.assertIn("Block sampling unavailable in this snapshot", page)

    def test_epoch_progress_bar_cannot_overflow(self):
        odd = load_fixture()
        odd["epoch"]["progress_pct"] = 140.0
        self.assertIn("width:100%", render.render_html(odd))

    def test_malformed_numeric_fields_degrade_without_executing_markup(self):
        odd = load_fixture()
        progress_payload = "<script>alert(1)</script>"
        stake_payload = "<b>not a number</b>"
        odd["epoch"]["progress_pct"] = progress_payload
        odd["validators"]["top_validators"][0]["stake_sol"] = stake_payload
        page = render.render_html(odd)
        for payload, escaped_payload in (
            (progress_payload, "&lt;script&gt;alert(1)&lt;/script&gt;"),
            (stake_payload, "&lt;b&gt;not a number&lt;/b&gt;"),
        ):
            self.assertNotIn(payload, page)
            self.assertNotIn(escaped_payload, page)
        self.assertIn('<div class="label">Epoch 700</div><div class="value">—</div>', page)
        self.assertIn("<td data-label='Stake (SOL)'>—</td>", page)


class TestActivityRendering(unittest.TestCase):
    """The activity section renders SOL from the chain and USD from CoinGecko.

    Those two sources fail independently, so the tests below pin the behaviour
    when the price is gone: SOL figures survive, dollar figures become dashes,
    and nothing anywhere turns into "$0.00".
    """

    def snapshot(self, price=True):
        body = {
            "schema_version": 8,
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
            "activity": {
                "available": True,
                "window": {
                    "slots": 216_000, "blocks_sampled": 16, "blocks_requested": 16,
                    "first_slot": 439_700_000, "last_slot": 439_916_000,
                    "first_block_time": 1_786_000_000,
                    "last_block_time": 1_786_091_395,
                    "observed_seconds": 91_395, "sampling": "evenly spaced across the window",
                },
                "fees": {
                    "available": True, "median_lamports": 5_391, "mean_lamports": 34_311,
                    "p90_lamports": 18_000, "p99_lamports": 555_000, "median_sol": 5.391e-06,
                    "vote_share_pct": 43.7, "failure_rate_pct": 44.88,
                    "nonvote_transactions_sampled": 14_176,
                },
                "rev": {
                    "available": True, "definition": "transaction fees + detected Jito tips",
                    "fee_decomposition": "message-signature base-fee lower bound + unclassified residual",
                    "jito_tip_account_source": {
                        "publisher": "Jito Labs",
                        "label": "Jito Block Engine getTipAccounts",
                        "url": "https://example.com/jito-tip-accounts",
                        "source_revision": "93dec9d9e8ec0f2a20dea9f0a6f2d14bcd9494cd",
                        "coverage": "8/8",
                    },
                    "sampled_sol": {
                        "transaction_fees": 0.54187,
                        "message_signature_base_fee_lower_bound": 0.13146,
                        "unclassified_fee_residual": 0.41041,
                        "jito_tips": 0.10789, "total": 0.64976,
                    },
                    "per_block_sol": {"mean": 0.04061, "min": 0.016146, "max": 0.113629},
                    "sample_mean_estimate_sol": 8760.35, "estimated": True,
                    "sample_mean_interval": {
                        "low_sol": 5900.0, "high_sol": 11620.7,
                        "method": "95% normal interval on the sampled-block mean",
                        "limitation": "Does not correct temporal or endpoint/network sampling bias.",
                    },
                    "estimated_blocks_in_window": 215_719,
                    "estimate_window_seconds": 91_395,
                    "method": "mean REV per sampled block x estimated blocks in the observed slot window",
                    "limitation": "Systematic sampling can retain temporal and endpoint/network sampling bias.",
                },
                "addresses": {
                    "available": True, "unique_fee_payers_sampled": 4_019,
                    "unique_accounts_sampled": 32_646, "mean_fee_payers_per_block": 377.2,
                    "blocks_sampled": 16, "daily_active_addresses": None,
                    "daily_active_available": False,
                    "note": "Unique non-vote fee payers seen in the sampled blocks only, "
                            "not a daily total.",
                },
                "fee_split": {
                    "available": True, "blocks_reconciled": 16, "fees_sol": 0.54187,
                    "validator_reward_sol": 0.476097, "burned_sol": 0.065772, "burned_pct": 12.14,
                },
            },
        }
        if price:
            body["economics"] = {"available": True,
                                 "price": {"available": True, "price_usd": 100.0}}
        return body

    def test_markdown_states_the_fee_basis_next_to_the_number(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("5,391 lamports", markdown)
        self.assertIn("non-vote transactions", markdown)
        self.assertIn("43.70%", markdown)

    def test_daily_active_is_rendered_as_withheld_never_as_a_number(self):
        for text in (render.render_markdown(self.snapshot()),
                     render.render_html(self.snapshot())):
            self.assertIn("not a daily total", text)
            self.assertNotIn("Daily active addresses</div><div class='value'>0", text)
        self.assertIn("not derivable", render.render_markdown(self.snapshot()))
        self.assertIn("not derivable", render.render_html(self.snapshot()))

    def test_rev_components_and_observed_window_estimate_appear(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("8,760.35 SOL", markdown)
        self.assertIn("Message-signature base-fee lower bound", markdown)
        self.assertIn("Unclassified fee residual", markdown)
        self.assertIn("Jito tips", markdown)
        self.assertIn("$876,035", markdown)  # synthetic 8760.35 x 100.0
        self.assertNotIn("Priority fees", markdown)
        self.assertNotIn("Estimated 24h REV", markdown)

    def test_jito_tip_account_provenance_appears_in_both_human_outputs(self):
        for output in (render.render_markdown(self.snapshot()), render.render_html(self.snapshot())):
            self.assertIn("Jito Block Engine getTipAccounts", output)
            self.assertIn("93dec9d9e8ec0f2a20dea9f0a6f2d14bcd9494cd", output)
            self.assertIn("8/8", output)

    def test_the_observed_window_rev_figure_is_labelled_as_sample_mean(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("Sample-mean estimate, not a measured total", markdown)
        self.assertIn("temporal and endpoint/network sampling bias", markdown)
        self.assertIn("91,395 seconds", markdown)
        self.assertIn("slots **439700000–439916000**", markdown)
        self.assertIn("2026-08-06T07:06:40+00:00", markdown)
        self.assertIn("2026-08-07T08:29:55+00:00", markdown)
        self.assertNotIn("daily REV", markdown)

    def test_the_sample_mean_estimate_is_printed_with_its_interval(self):
        for text in (render.render_markdown(self.snapshot()),
                     render.render_html(self.snapshot())):
            self.assertIn("5,900", text)
            self.assertIn("11,620.7", text)

    def test_an_estimate_without_an_interval_omits_it_rather_than_faking_one(self):
        narrow = self.snapshot()
        narrow["activity"]["rev"]["sample_mean_interval"] = None
        markdown = render.render_markdown(narrow)
        self.assertIn("8,760.35 SOL", markdown)
        self.assertNotIn("95% normal interval", markdown)

    def test_a_truncated_run_says_so_on_the_page(self):
        cut = self.snapshot()
        cut["activity"]["window"].update({"truncated": True, "blocks_sampled": 4,
                                          "blocks_requested": 16})
        for text in (render.render_markdown(cut), render.render_html(cut)):
            self.assertIn("Sampling stopped early", text)
        self.assertNotIn("Sampling stopped early", render.render_markdown(self.snapshot()))

    def test_burn_is_labelled_as_measured_rather_than_assumed(self):
        self.assertIn("no burn rate is assumed", render.render_markdown(self.snapshot()))

    def test_a_sub_cent_fee_does_not_round_away_to_zero(self):
        # A median fee is a fraction of a cent; "$0.00" would be a false claim
        # that using Solana is free.
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("$0.000539", markdown)  # synthetic 5.391e-06 SOL x $100.0
        self.assertNotIn("| $0.00 |", markdown)

    def test_a_missing_price_source_dashes_the_usd_not_zeroes_it(self):
        markdown = render.render_markdown(self.snapshot(price=False))
        self.assertIn("8,760.35 SOL", markdown)   # chain figures unaffected
        self.assertNotIn("$0", markdown)
        self.assertIn("| 5,391 lamports | — |", markdown)

    def test_html_leads_with_available_sol_and_lamports_when_price_is_held(self):
        page = render.render_activity_html(self.snapshot(price=False))
        self.assertIn(
            '<div class="label">REV over observed window (sample mean)</div>'
            '<div class="value">8,760.35 SOL</div>',
            page,
        )
        self.assertIn(
            '<div class="label">Median fee</div>'
            '<div class="value">5,391 lamports</div>',
            page,
        )
        self.assertNotIn(
            '<div class="label">Median fee</div><div class="value">—</div>',
            page,
        )

    def test_html_renders_the_section_without_raising(self):
        page = render.render_html(self.snapshot())
        self.assertIn("Fees, REV and activity", page)
        self.assertIn("Address activity", page)
        self.assertIn("Transaction fee distribution", page)

    def test_html_degrades_malformed_sample_window_and_rev_range_scalars(self):
        hostile = self.snapshot()
        payload = "<svg/onload=alert('codex-probe')>"
        escaped_payload = "&lt;svg/onload=alert(&#x27;codex-probe&#x27;)&gt;"
        hostile["activity"]["window"]["blocks_sampled"] = payload
        hostile["activity"]["rev"]["per_block_sol"].update(
            {"min": payload, "max": payload})
        page = render.render_html(hostile)
        self.assertNotIn(payload, page)
        self.assertNotIn(escaped_payload, page)
        self.assertIn("— blocks evenly spaced across an exact observed duration of 91,395 seconds (25.4h)", page)
        self.assertIn("sampled blocks ranged —–— SOL", page)

    def test_legacy_rev_and_fee_split_are_preserved_as_an_explicit_gap(self):
        legacy = self.snapshot()
        legacy["schema_version"] = 7
        legacy["activity"]["rev"] = {
            "available": True,
            "sampled_sol": {"base": 1, "priority": 2, "total": 3},
            "estimated_24h_sol": 99,
        }
        for output in (render.render_markdown(legacy), render.render_html(legacy)):
            self.assertIn("Legacy REV decomposition", output)
            self.assertNotIn("99 SOL", output)
            self.assertNotIn("Priority fees", output)

    def test_unavailable_subsections_are_skipped_not_zeroed(self):
        partial = self.snapshot()
        partial["activity"]["rev"] = {"available": False}
        partial["activity"]["fee_split"] = {"available": False}
        markdown = render.render_markdown(partial)
        self.assertNotIn("Real economic value", markdown)
        self.assertIn("5,391 lamports", markdown)  # fees still render


class TestDeltaRendering(unittest.TestCase):
    """The delta panel in Markdown and HTML.

    The rendering rules mirror the module's: a not-comparable metric never
    appears as a change, a sampled metric never looks measured, and an absent
    comparison degrades to a stated "not yet comparable" rather than silence.
    """

    @staticmethod
    def comparison(**overrides):
        body = {
            "status": "ok",
            "previous_collected_at": "2026-08-05T11:00:00+00:00",
            "current_collected_at": "2026-08-05T17:00:00+00:00",
            "elapsed_seconds": 21_600,
            "changes": [{
                "key": "latest_tps", "label": "Latest TPS", "unit": " TPS",
                "basis": "measured", "previous": 3000.0, "current": 4000.0,
                "change": 1000.0, "change_pct": 33.33, "direction": "up",
                "identifier": False,
                "why_it_matters": "Throughput is the headline liveness signal.",
                "what_to_verify": "getRecentPerformanceSamples on the same endpoint.",
            }],
            "steady": [{
                "key": "delinquent_pct", "label": "Validator delinquency", "unit": "%",
                "basis": "measured", "previous": 1.0, "current": 1.0, "change": 0.0,
                "change_pct": 0.0, "direction": "flat", "identifier": False,
            }],
            "not_comparable": [{
                "key": "price_usd", "label": "SOL price",
                "reason": "not present in the newer snapshot",
                "previous": 74.0, "current": None,
            }],
            "counts": {"changed": 1, "steady": 1, "not_comparable": 1},
        }
        body.update(overrides)
        return body

    @staticmethod
    def refused_comparison(**overrides):
        """A comparison the delta module declined to publish, with its reason."""
        return TestDeltaRendering.comparison(
            status="incompatible_schemas",
            message="snapshots declare different schema versions (3 vs 4); "
                    "values collected under different layouts are not comparable evidence",
            elapsed_seconds=None,
            changes=[], steady=[], not_comparable=[],
            counts={"changed": 0, "steady": 0, "not_comparable": 0},
            **overrides,
        )

    def test_frequent_delta_surfaces_summarize_refusal_while_data_and_history_keep_detail(self):
        refused = self.refused_comparison()
        fixture = load_fixture()
        summary = "Not comparable to previous snapshot."

        markers = render.render_change_markers(fixture, None, refused)
        self.assertIn("Comparison not published", markers)
        self.assertIn("different schema versions", markers)
        self.assertNotIn("Not yet comparable", markers)
        self.assertNotIn("crossed a declared threshold", markers)

        catalog = render.render_data_catalog(fixture, None, refused, [])
        self.assertIn("different schema versions", catalog)
        self.assertNotIn("comparable metrics", catalog)

        overview = render.render_mobile_overview(fixture, refused, [], "test")
        self.assertIn(summary, overview)
        self.assertNotIn("values collected under different layouts", overview)
        self.assertNotIn("Prior snapshot unavailable", overview)
        self.assertNotIn("vs prior unavailable", overview)

        desktop_overview = render.render_overview_comparison(refused)
        self.assertIn(summary, desktop_overview)
        self.assertNotIn("values collected under different layouts", desktop_overview)

        mobile_data = render.render_mobile_data(fixture, None, refused)
        self.assertIn(
            "delta.py · A/B thresholds · snapshots declare different schema versions",
            mobile_data)

        ticker = render.render_ticker(fixture, refused)
        self.assertIn(
            f"<span class='report-ticker__basis'>{summary}",
            ticker)
        self.assertNotIn("values collected under different layouts", ticker)

    def test_an_ok_comparison_still_renders_values_not_refusals(self):
        fixture = load_fixture()
        ok = self.comparison()
        self.assertNotIn("Comparison not published",
                         render.render_change_markers(fixture, None, ok))
        self.assertNotIn("different schema versions", render.render_ticker(fixture, ok))
        overview = render.render_mobile_overview(fixture, ok, [fixture], "test")
        self.assertNotIn("different schema versions", overview)

    def test_markdown_prints_the_moved_metric_with_both_context_lines(self):
        markdown = render.render_markdown(load_fixture(), None, self.comparison())
        self.assertIn("## What changed since the last snapshot", markdown)
        self.assertIn("| Latest TPS | 3,000.0 TPS | 4,000.0 TPS |", markdown)
        self.assertIn("+1,000.0 TPS (+33.33%)", markdown)
        self.assertIn("headline liveness signal", markdown)
        self.assertIn("_Verify:_", markdown)

    def test_a_not_comparable_metric_is_named_not_shown_as_a_change(self):
        for text in (render.render_markdown(load_fixture(), None, self.comparison()),
                     render.render_html(load_fixture(), None, self.comparison())):
            self.assertIn("not present in the newer snapshot", text)
        markdown = render.render_markdown(load_fixture(), None, self.comparison())
        # It appears under the not-comparable list, never in the change table.
        self.assertNotIn("| SOL price |", markdown)

    def test_html_marks_a_sampled_metric_distinctly_from_a_measured_one(self):
        sampled = self.comparison()
        sampled["changes"][0]["basis"] = "sampled"
        self.assertIn("basis-badge sampled", render.render_delta_html(sampled))
        self.assertNotIn("basis-badge sampled",
                         render.render_delta_html(self.comparison()))

    def test_markdown_names_the_basis_of_every_moved_metric(self):
        self.assertIn("| measured |",
                      render.render_markdown(load_fixture(), None, self.comparison()))
        sampled = self.comparison()
        sampled["changes"][0]["basis"] = "sampled"
        self.assertIn("| sampled/extrapolated |",
                      render.render_markdown(load_fixture(), None, sampled))

    def test_no_movement_reads_as_no_movement_not_as_no_data(self):
        quiet = self.comparison(changes=[], counts={"changed": 0, "steady": 12,
                                                    "not_comparable": 0},
                                not_comparable=[], observation_ids={
                                    "steady_count": "obs-test-steady-count",
                                })
        markdown = render.render_markdown(load_fixture(), None, quiet)
        self.assertIn("No metric moved past its threshold", markdown)
        self.assertIn("12 compared metric(s)", markdown)
        self.assertIn("observation-ids:obs-test-steady-count", markdown)
        self.assertIn("anomaly-note clear",
                      render.render_html(load_fixture(), None, quiet))

    def test_no_commission_change_is_explicit_in_markdown_and_html(self):
        quiet = self.comparison(validator_commission={
            "status": "ok", "previous_snapshot_epoch": 1022,
            "current_snapshot_epoch": 1022,
            "previous_snapshot_slot": 100, "current_snapshot_slot": 200,
            "previous_account_count": 695, "current_account_count": 695,
            "matched_account_count": 695, "new_account_count": 0,
            "missing_account_count": 0, "previous_comparable_count": 695,
            "current_comparable_count": 695, "matched_comparable_count": 695,
            "changed_count": 0, "changes": [],
        })
        for output in (render.render_delta_markdown(quiet),
                       [render.render_delta_html(quiet)]):
            text = "\n".join(output)
            self.assertIn("Validator commission changes", text)
            self.assertIn(
                "No recorded commission changes among 695 matched vote accounts", text)

    def test_commission_changes_render_only_recorded_identity_transitions(self):
        changed = self.comparison(validator_commission={
            "status": "ok", "previous_snapshot_epoch": 1022,
            "current_snapshot_epoch": 1023,
            "previous_snapshot_slot": 100, "current_snapshot_slot": 200,
            "previous_account_count": 2, "current_account_count": 2,
            "matched_account_count": 1, "new_account_count": 1,
            "missing_account_count": 1, "previous_comparable_count": 2,
            "current_comparable_count": 2, "matched_comparable_count": 1,
            "changed_count": 1,
            "changes": [{
                "vote_account": "vote-a", "previous_identity": "node-old",
                "current_identity": "node-new", "previous_commission_pct": 5.0,
                "current_commission_pct": 8.0, "change_percentage_points": 3.0,
            }],
        })
        markdown = "\n".join(render.render_delta_markdown(changed))
        page = render.render_delta_html(changed)
        for output in (markdown, page):
            self.assertIn("vote-a", output)
            self.assertIn("node-old", output)
            self.assertIn("node-new", output)
            self.assertIn("+3", output)
            self.assertIn("1 matched", output)
            self.assertIn("1 new", output)
            self.assertIn("1 missing", output)
            self.assertIn("commissions comparable for 1/1", output)

    def test_unavailable_commission_comparison_is_not_rendered_as_no_change(self):
        unavailable = self.comparison(validator_commission={
            "status": "not_comparable", "reason": "no shared vote accounts",
            "changes": [],
        })
        for output in ("\n".join(render.render_delta_markdown(unavailable)),
                       render.render_delta_html(unavailable)):
            self.assertIn("no shared vote accounts", output)
            self.assertNotIn("No recorded commission changes", output)

    def test_insufficient_history_is_stated_and_styled_as_pending(self):
        pending = {"status": "insufficient_history",
                   "message": "1 snapshot(s) on disk; two are needed.",
                   "changes": [], "steady": [], "not_comparable": [],
                   "counts": {"changed": 0, "steady": 0, "not_comparable": 0}}
        markdown = render.render_markdown(load_fixture(), None, pending)
        self.assertIn("Not yet comparable", markdown)
        page = render.render_html(load_fixture(), None, pending)
        # Grey, not green — "cannot compare" must not read as "nothing moved".
        self.assertIn("anomaly-note pending", page)

    def test_the_section_is_absent_entirely_when_no_comparison_is_supplied(self):
        markdown = render.render_markdown(load_fixture())
        self.assertNotIn("What changed since the last snapshot", markdown)
        self.assertNotIn("What changed since the last snapshot",
                         render.render_html(load_fixture()))

    def test_a_percentage_against_zero_is_declared_rather_than_invented(self):
        from_zero = self.comparison()
        from_zero["changes"][0].update({"previous": 0.0, "current": 3.0,
                                        "change": 3.0, "change_pct": None})
        markdown = render.render_markdown(load_fixture(), None, from_zero)
        self.assertIn("% n/a from zero", markdown)
        self.assertNotIn("+0.00%", markdown)

    def test_hostile_text_in_a_comparison_cannot_break_the_page(self):
        hostile = self.comparison()
        hostile["changes"][0]["label"] = "<script>alert(1)</script>"
        page = render.render_html(load_fixture(), None, hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_malformed_count_values_degrade_without_breaking_the_page(self):
        hostile = self.comparison(changes=[], not_comparable=[])
        payload = "<svg/onload=alert('count-probe')>"
        escaped_payload = "&lt;svg/onload=alert(&#x27;count-probe&#x27;)&gt;"
        hostile["counts"] = {"changed": payload, "steady": payload,
                             "not_comparable": payload}
        page = render.render_delta_html(hostile)
        self.assertNotIn(payload, page)
        self.assertNotIn(escaped_payload, page)
        self.assertIn("· — moved · — steady · — not comparable", page)
        self.assertIn("across — compared metric(s)", page)


class TestNewsRendering(unittest.TestCase):
    """The releases panel keeps three states apart, in both output formats."""

    @staticmethod
    def snapshot(**sources):
        return {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
            "news": {
                "available": any(s.get("available") for s in sources.values()),
                "requires_api_key": False,
                "note": "Official first-party feeds, fetched without credentials.",
                "sources": sources,
            },
        }

    @staticmethod
    def source(available=True, items=None, reason="", label="Agave validator releases"):
        body = {
            "label": label, "publisher": "anza-xyz/agave (GitHub)",
            "why": "Agave is the validator client most of the network runs.",
            "url": "https://github.com/anza-xyz/agave/releases.atom",
            "requires_api_key": False, "available": available,
        }
        if reason:
            body["reason"] = reason
        if items is not None:
            body["items"] = items
            body["item_count"] = len(items)
        return body

    ITEM = {"title": "Release v4.2.0-rc.1",
            "link": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0-rc.1",
            "published": "2026-08-03T12:41:49Z", "author": "github-actions[bot]"}

    def test_entries_render_with_their_dates_and_links(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[self.ITEM]))
        markdown = render.render_markdown(snapshot)
        self.assertIn("## Releases and announcements", markdown)
        self.assertIn("Release v4.2.0-rc.1", markdown)
        self.assertIn("2026-08-03T12:41:49Z", markdown)
        page = render.render_html(snapshot)
        self.assertIn("releases/tag/v4.2.0-rc.1", page)

    def test_agave_release_channel_stability_and_tag_commit_are_visible(self):
        item = {**self.ITEM, "release_channel": "prerelease", "stable": False,
                "tag": "v4.2.0-rc.1", "tag_commit_sha": "a" * 40}
        snapshot = self.snapshot(agave_releases=self.source(items=[item]))
        for output in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("channel prerelease", output)
            self.assertIn("stable no", output)
            self.assertIn("tag v4.2.0-rc.1", output)
            self.assertIn("tag commit " + "a" * 40, output)

    def test_simd_source_native_created_date_is_not_called_published(self):
        source = self.source(items=[{
            "title": "SIMD-0326: Alpenglow — Review", "link": "https://example.com/simd",
            "created": "2025-07-25", "status": "Review",
        }], label="SIMD proposal lifecycle")
        source.update({"source_commit": "a" * 40, "proposal_count": 1,
                       "document_count": 1, "coverage_complete": True})
        snapshot = self.snapshot(simd_proposals=source)
        markdown = render.render_markdown(snapshot)
        page = render.render_html(snapshot)
        self.assertIn("Created: 2025-07-25", markdown)
        self.assertNotIn("Published: 2025-07-25", markdown)
        self.assertIn("Created</small>2025-07-25", page)
        self.assertIn("status Review", page)

    def test_partial_status_never_turns_missing_incident_response_into_zero(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[self.ITEM]))
        snapshot["news"]["current_status"] = {
            "available": False, "partial": True, "status_available": True,
            "incidents_available": False, "description": "All Systems Operational",
            "active_incident_count": None,
        }
        for output in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("partial evidence", output)
            self.assertIn("— active incident", output)
            self.assertNotIn("0 active incident", output)

    def test_current_status_keeps_source_times_distinct_from_collection_time(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[self.ITEM]))
        snapshot["news"]["current_status"] = {
            "available": True, "partial": False, "status_available": True,
            "incidents_available": True, "description": "All Systems Operational",
            "active_incident_count": 0,
            "summary_source_updated_at": "2026-08-23T20:05:00Z",
            "incidents_source_updated_at": "2026-08-23T20:06:00Z",
        }
        for output in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("Summary source updated", output)
            self.assertIn("Aug 23, 2026 · 20:05 UTC", output)
            self.assertIn("Incident index updated", output)
            self.assertIn("Aug 23, 2026 · 20:06 UTC", output)
            self.assertIn("Collected Aug 05, 2026 · 00:00 UTC", output)

    def test_a_failed_feed_says_unavailable_and_never_implies_quiet(self):
        # One feed up, one down — the down one must name its failure rather
        # than render as a source with nothing to report.
        snapshot = self.snapshot(
            agave_releases=self.source(items=[self.ITEM]),
            network_status=self.source(
                available=False, label="Network status history",
                reason="feed unreachable or not a parseable Atom document"),
        )
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("navailable", text)
            self.assertIn("feed unreachable", text)
            self.assertNotIn("no releases", text.lower())

    def test_a_feed_that_published_nothing_reads_differently_from_one_that_failed(self):
        empty = self.snapshot(agave_releases=self.source(items=[], reason="the feed parsed and published no entries"))
        broken = self.snapshot(agave_releases=self.source(available=False, reason="feed unreachable"))
        self.assertNotEqual(render.render_html(empty), render.render_html(broken))
        self.assertIn("published no entries", render.render_html(empty))

    def test_one_broken_feed_does_not_hide_a_working_one(self):
        snapshot = self.snapshot(
            agave_releases=self.source(items=[self.ITEM]),
            network_status=self.source(available=False, reason="feed unreachable",
                                       label="Network status history"),
        )
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("Release v4.2.0-rc.1", text)
            self.assertIn("Network status history", text)

    def test_every_feed_failing_states_it_is_about_the_fetch(self):
        snapshot = self.snapshot(agave_releases=self.source(
            available=False, reason="feed unreachable"))
        snapshot["news"]["available"] = False
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("statement about the fetch, not about the ecosystem", text)

    def test_a_snapshot_predating_the_feature_says_so_rather_than_reporting_a_failure(self):
        bare = {"collected_at": "2026-08-05T00:00:00+00:00",
                "source": {"endpoint": "x"}, "network": {"healthy": True}}
        for text in (render.render_markdown(bare), render.render_html(bare)):
            self.assertIn("predates the releases section", text)

    def test_feed_content_is_escaped_because_it_is_third_party_input(self):
        hostile = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "title": "<script>alert(1)</script>"}]))
        page = render.render_html(hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_an_entry_without_a_link_still_renders_its_title(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "link": None}]))
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("Release v4.2.0-rc.1", text)
        self.assertNotIn("](None)", render.render_markdown(snapshot))

    def test_renderer_rejects_a_non_http_feed_link_even_if_the_snapshot_is_tampered(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "link": "javascript:alert(1)"}]))
        page = render.render_html(snapshot)
        self.assertNotIn("href='javascript:", page)
        self.assertIn("Release v4.2.0-rc.1", page)

    def test_markdown_neutralises_recorded_markup_and_unsafe_links(self):
        title = "<script>alert(1)</script> ![remote](https://attacker.invalid/pixel)"
        snapshot = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "title": title, "link": "javascript:alert(1)"}]))
        markdown = render.render_markdown(snapshot)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("![remote]", markdown)
        self.assertNotIn("(javascript:", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        newline_link = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM,
            "link": "https://safe.invalid/x\n<img src=https://attacker.invalid/pixel>"}]))
        newline_markdown = render.render_markdown(newline_link)
        self.assertNotIn("<img", newline_markdown)
        self.assertNotIn("attacker.invalid", newline_markdown)


class TestFormatting(unittest.TestCase):
    def test_none_renders_as_a_dash_not_zero(self):
        # "unknown" and "zero" must not look the same on a dashboard.
        self.assertEqual(render.fmt(None), "—")
        self.assertEqual(render.fmt(0), "0")

    def test_non_finite_numbers_fail_closed_in_all_numeric_formatters(self):
        formatters = (
            render.fmt,
            render.fmt_pct,
            render.fmt_sol,
            lambda value: render.fmt_usd(value, 100.0),
            render.fmt_lamports,
            render.hours,
            render.fmt_id,
            render.compact_number,
        )
        for value in (float("nan"), float("inf"), float("-inf")):
            for formatter in formatters:
                with self.subTest(value=value, formatter=formatter):
                    self.assertEqual(formatter(value), "—")
            self.assertEqual(render.fmt_delta_value(value, " TPS"), "—")

    def test_json_safe_replaces_non_finite_numbers_recursively(self):
        payload = {"a": float("nan"), "nested": [float("inf"), {"b": float("-inf")}],
                   "finite": 3.5}
        safe = render.json_safe(payload)
        self.assertEqual(safe, {"a": None, "nested": [None, {"b": None}], "finite": 3.5})
        encoded = json.dumps(safe, allow_nan=False)
        self.assertNotRegex(encoded, r"NaN|Infinity")

    def test_non_finite_snapshot_values_are_unavailable_in_markdown_and_html(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                snapshot = load_fixture()
                snapshot["epoch"]["progress_pct"] = value
                snapshot["validators"]["top_validators"][0]["stake_sol"] = value
                markdown = render.render_markdown(snapshot)
                page = render.render_html(snapshot)
                self.assertIn("| Progress | — |", markdown)
                self.assertIn('<div class="label">Epoch 700</div><div class="value">—</div>', page)
                self.assertIn("<td data-label='Stake (SOL)'>—</td>", page)

    def test_thousands_separators(self):
        self.assertEqual(render.fmt(1234567), "1,234,567")

    def test_block_time_converts_unix_to_iso(self):
        snapshot = {"network": {"block_time_unix": 1786000000}}
        self.assertTrue(render.block_time_iso(snapshot).startswith("2026-"))

    def test_missing_block_time_is_a_dash(self):
        self.assertEqual(render.block_time_iso({"network": {}}), "—")

    def test_identifiers_print_without_thousands_separators(self):
        # Epoch 1012 is a name, not a count — "1,012" reads as a quantity.
        self.assertEqual(render.fmt_id(1012), "1012")
        self.assertEqual(render.fmt_id(None), "—")

    def test_sol_amounts_are_compact_in_cards(self):
        # A 9-digit SOL figure wraps the card and cannot be scanned.
        self.assertEqual(render.fmt_sol(434_421_657.36), "434.4M SOL")
        self.assertEqual(render.fmt_sol(12_500.0), "12.5K SOL")
        self.assertEqual(render.fmt_sol(42.5), "42.50 SOL")
        self.assertEqual(render.fmt_sol(None), "—")

    def test_billion_scale_figures_get_their_own_tier(self):
        # Market cap and TVL are USD billions; "$42,919.6M" is the amateur read.
        self.assertEqual(render.fmt_sol(42_919_600_000), "42.92B SOL")
        self.assertEqual(render.fmt_sol(999_999_999), "1,000.0M SOL")

    def test_html_keeps_the_exact_figure_alongside_the_compact_one(self):
        page = render.render_html(load_fixture())
        self.assertIn("600.0M SOL", page)      # compact headline
        self.assertIn("600,000,000", page)     # exact value still present

    def test_markdown_keeps_exact_large_integer_values(self):
        # The readable report must not abbreviate exact count values.
        markdown = render.render_markdown(load_fixture())
        self.assertIn("600,000,000", markdown)
        self.assertNotIn("600.0M", markdown)

    def test_epoch_is_not_comma_formatted_in_output(self):
        snapshot = load_fixture()
        snapshot["epoch"]["epoch"] = 1012
        self.assertIn("| Epoch | 1012 |", render.render_markdown(snapshot))
        self.assertIn("Epoch 1012", render.render_html(snapshot))

    def test_mobile_ticker_is_anchored_below_the_header_and_keeps_motion(self):
        page = render.render_html(load_fixture())
        self.assertIn("--mobile-topbar-height: 52px", page)
        self.assertIn(".mobile-topbar { position: relative;", page)
        self.assertIn("top:0;", page)
        self.assertIn("transform:translateY(var(--mobile-ticker-offset, var(--mobile-topbar-height)))", page)
        self.assertIn("let mobileTickerFrame = 0;", page)
        self.assertIn("mobileTickerFrame = requestAnimationFrame(() => {", page)
        self.assertIn("const headerBottom = mobileTopbar.getBoundingClientRect().bottom", page)
        self.assertIn("`${Math.max(0, headerBottom)}px`", page)
        self.assertNotIn(".report-ticker__track { animation:none; }\n    }\n\n    @media (prefers-reduced-motion", page)
        self.assertIn(".report-ticker:active .report-ticker__track { animation-play-state:paused; }", page)


class TestAnalysisSelection(unittest.TestCase):
    """The anomaly panel must describe the snapshot being rendered.

    Rendering an older snapshot with the newest snapshot's verdict is the
    known_zero-class failure: a confident panel about the wrong moment.
    """

    @staticmethod
    def snap(hour, slot):
        return {
            "collected_at": f"2026-08-05T0{hour}:00:00+00:00",
            "network": {"healthy": True, "slot": slot},
        }

    def test_history_after_the_target_snapshot_is_excluded(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        target = history[3]
        analysis = render.analysis_for(target, history)
        self.assertEqual(analysis["collected_at"], target["collected_at"])
        self.assertEqual(analysis["snapshots_analysed"], 4)

    def test_newest_snapshot_keeps_the_full_history(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        analysis = render.analysis_for(history[-1], history)
        self.assertEqual(analysis["collected_at"], history[-1]["collected_at"])
        self.assertEqual(analysis["snapshots_analysed"], 5)

    def test_snapshot_without_timestamp_does_not_borrow_another_moment(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        analysis = render.analysis_for({"network": {}}, history)
        self.assertEqual(analysis["snapshots_analysed"], 1)
        self.assertEqual(analysis["status"], "stale_snapshot")
        self.assertIn("latest snapshot has no readable collected_at", analysis["conditions"])
        self.assertIn("only 0 time-eligible prior observation(s); 3 needed", analysis["conditions"])

    def test_explicit_target_is_added_when_it_is_not_a_sibling_history_file(self):
        history = [self.snap(hour, 100 + hour) for hour in range(3)]
        target = self.snap(3, 103)
        aligned = render.history_for(target, history)
        self.assertEqual(aligned[-1], target)
        self.assertEqual(len(aligned), 4)

    def test_explicit_target_replaces_a_same_timestamp_sibling(self):
        sibling = self.snap(3, 3)
        target = self.snap(3, 999)
        aligned = render.history_for(target, [*[
            self.snap(hour, 100 + hour) for hour in range(3)], sibling])
        self.assertIs(aligned[-1], target)
        self.assertEqual(aligned[-1]["network"]["slot"], 999)
        self.assertEqual(len(aligned), 4)

    def test_publication_history_withholds_unavailable_economics_without_mutation(self):
        earlier = load_fixture()
        earlier["collected_at"] = "2026-08-27T09:00:00+00:00"
        earlier["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 123.45},
            "tvl": {"available": True, "tvl_usd": 987654321.0},
            "dex": {"available": True, "volume_24h_usd": 456789.0},
        }
        current = deepcopy(earlier)
        current["collected_at"] = "2026-08-27T15:00:00+00:00"
        current["economics"] = {"available": False}

        published = render.facts_module.publication_history([earlier, current])
        comparison = render.delta_module.compare(*published)
        history_json = render.charts_module.history_json(published)

        self.assertEqual(earlier["economics"]["price"]["price_usd"], 123.45)
        self.assertEqual(published[0]["economics"]["publication_state"], "withheld")
        self.assertEqual(history_json["series"]["price_usd"]["stats"]["points"], 0)
        self.assertEqual(history_json["series"]["tvl_usd"]["stats"]["points"], 0)
        self.assertEqual(history_json["series"]["price_usd"]["publication_state"], "withheld")
        held_delta = {row["key"]: row for row in comparison["not_comparable"]}
        for key in ("price_usd", "tvl_usd", "dex_volume_24h_usd"):
            self.assertIsNone(held_delta[key]["previous"])
            self.assertIsNone(held_delta[key]["current"])

    def test_publication_history_preserves_available_economics(self):
        snapshot = load_fixture()
        snapshot["economics"] = {"available": True}
        published = render.facts_module.publication_history([snapshot])
        self.assertIs(published[0], snapshot)


class TestAnalysisNotOkIsNeverHealthy(unittest.TestCase):
    """Review regressions: stale and partially-covered evidence must reach the
    published page as pending/not assessable — never as a clean panel."""

    FRESH_NOW = "2026-08-05T09:45:00+00:00"   # 45m after the newest snapshot
    STALE_NOW = "2026-08-05T21:00:00+00:00"   # 12h after it; policy allows 7h

    @staticmethod
    def snap(hour, slot):
        snapshot = load_fixture()
        snapshot["collected_at"] = f"2026-08-05T0{hour}:00:00+00:00"
        snapshot["network"]["slot"] = slot
        return snapshot

    def history(self):
        return [self.snap(hour, 100 + hour) for hour in (6, 7, 8, 9)]

    def test_stale_latest_snapshot_renders_pending_end_to_end(self):
        latest = self.snap(9, 109)
        analysis = render.analysis_for(latest, self.history(), now=self.STALE_NOW)
        self.assertEqual(analysis["status"], "stale_snapshot")

        html_panel = render.render_anomalies_html(analysis)
        self.assertIn("anomaly-note pending", html_panel)
        self.assertIn("Not yet assessable", html_panel)
        self.assertNotIn("anomaly-note clear", html_panel)

        markdown = "\n".join(render.render_anomalies_markdown(analysis))
        self.assertIn("**Not yet assessable**", markdown)
        self.assertNotIn("No finding for this snapshot", markdown)

        mobile = render.render_mobile_data(latest, analysis, None)
        self.assertIn(
            "<strong>Anomaly analysis</strong>"
            "<span class='mobile-source-status'>Not assessable</span>", mobile)
        markers = render.render_change_markers(latest, analysis, None)
        self.assertIn("Anomaly baseline not yet assessable", markers)

    def test_partial_coverage_renders_pending_end_to_end(self):
        history = []
        for collected_at, hour, slot in (
            ("2026-08-04T15:00:00+00:00", 6, 106),
            ("2026-08-04T21:00:00+00:00", 7, 107),
            ("2026-08-05T03:00:00+00:00", 8, 108),
        ):
            snapshot = self.snap(hour, slot)
            snapshot["collected_at"] = collected_at
            snapshot["validators"] = {"available": False}
            history.append(snapshot)
        latest = self.snap(9, 109)
        latest["validators"] = {"available": False}
        history.append(latest)

        analysis = render.analysis_for(latest, history, now=self.FRESH_NOW)
        self.assertEqual(analysis["status"], "partial_coverage")

        html_panel = render.render_anomalies_html(analysis)
        self.assertIn("anomaly-note pending", html_panel)
        self.assertIn("<strong>Partial coverage.</strong>", html_panel)
        self.assertNotIn("anomaly-note clear", html_panel)

        markdown = "\n".join(render.render_anomalies_markdown(analysis))
        self.assertIn("**Partial coverage**", markdown)
        self.assertNotIn("No finding for this snapshot", markdown)

        mobile = render.render_mobile_data(latest, analysis, None)
        self.assertIn(
            "<strong>Anomaly analysis</strong>"
            "<span class='mobile-source-status'>Partial coverage</span>", mobile)
        catalog = render.render_data_catalog(latest, analysis, None, history)
        anomaly_row = catalog.split("<span class='dataset-name'>Anomaly analysis</span>", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">Partial coverage</button>", anomaly_row)
        markers = render.render_change_markers(latest, analysis, None)
        self.assertIn("Partial coverage", markers)
        self.assertNotIn("Anomaly baseline not yet assessable", markers)

    def test_partial_coverage_keeps_eligible_findings_in_html_and_markdown(self):
        analysis = {
            "status": "partial_coverage",
            "message": "SOL price and TVL are unavailable.",
            "baseline_size": 3,
            "findings": [{
                "severity": "critical",
                "title": "Transaction throughput dropped sharply",
                "detail": "TPS is 50.0% below the 3-snapshot median.",
                "observed": 1000,
                "baseline": 2000,
            }],
        }

        html_panel = render.render_anomalies_html(analysis)
        markdown = "\n".join(render.render_anomalies_markdown(analysis))
        for output in (html_panel, markdown):
            self.assertIn("Partial coverage", output)
            self.assertIn("Transaction throughput dropped sharply", output)

    def test_insufficient_history_keeps_current_state_findings_in_outputs(self):
        analysis = {
            "status": "insufficient_history",
            "message": "Only one eligible prior; three are needed for baseline rules.",
            "baseline_size": 1,
            "findings": [{
                "severity": "critical",
                "title": "RPC endpoint reports unhealthy",
                "detail": "Current endpoint evidence remains assessable.",
                "observed": "unhealthy",
                "baseline": None,
            }],
        }

        for output in (
            render.render_anomalies_html(analysis),
            "\n".join(render.render_anomalies_markdown(analysis)),
        ):
            self.assertIn("Not yet assessable", output)
            self.assertIn("RPC endpoint reports unhealthy", output)


class TestHealthEligibilityLabels(unittest.TestCase):
    """Health labels derive from eligibility, never a raw retained boolean."""

    @staticmethod
    def snapshot_with_network(network):
        snapshot = load_fixture()
        snapshot["network"] = network
        return snapshot

    def test_carried_forward_healthy_flag_reads_unavailable_everywhere(self):
        network = {"available": True, "stale": True,
                   "source_state": "last_known_good",
                   "carried_forward_at": "2026-08-05T09:00:00+00:00",
                   "healthy": True, "health_raw": "ok", "slot": 500}
        snapshot = self.snapshot_with_network(network)

        markdown = render.render_markdown(snapshot)
        self.assertIn("**RPC endpoint health:** unavailable", markdown)

        page = render.render_html(snapshot, None, None, [], "Recorded snapshot")
        self.assertIn(">Unavailable</strong>", page)
        self.assertNotIn(">Healthy</strong>", page)
        self.assertNotIn(">Unhealthy</strong>", page)

        mobile = render.render_mobile_overview(snapshot, None, [], "Recorded snapshot")
        self.assertIn("data-state='unavailable'", mobile)
        self.assertIn("<strong>No reading</strong>", mobile)

    def test_unavailable_block_with_retained_true_reads_unavailable(self):
        snapshot = self.snapshot_with_network(
            {"available": False, "healthy": True, "health_raw": "ok", "slot": 500})
        markdown = render.render_markdown(snapshot)
        self.assertIn("**RPC endpoint health:** unavailable", markdown)


class TestCarriedForwardValueLabels(unittest.TestCase):
    """Carried-forward fees/prices are visibly labelled on headline surfaces."""

    @staticmethod
    def snapshot_with_stale_sources():
        snapshot = load_fixture()
        activity = dict(snapshot.get("activity")) if isinstance(snapshot.get("activity"), dict) else {}
        fees = {"available": True, "median_lamports": 5527, "median_sol": 0.0000055}
        activity["available"] = True
        activity["fees"] = fees
        activity["source_state"] = "last_known_good"
        activity["stale"] = True
        activity["carried_forward_at"] = "2026-08-05T09:00:00+00:00"
        snapshot["activity"] = activity
        economics = dict(snapshot.get("economics")) if isinstance(snapshot.get("economics"), dict) else {}
        economics["price"] = {
            "available": True, "price_usd": 123.45, "freshness": "stale",
            "updated_at_unix": 1754000000,
        }
        snapshot["economics"] = economics
        return snapshot

    def test_ticker_labels_carried_forward_fee_and_stale_price(self):
        ticker = render.render_ticker(self.snapshot_with_stale_sources(), None)
        self.assertIn("last-known-good", ticker)
        self.assertIn("· stale", ticker)

    def test_desktop_overview_cards_carry_the_label(self):
        page = render.render_html(self.snapshot_with_stale_sources())
        self.assertIn("last-known-good", page)
        self.assertIn("· stale", page)

    def test_mobile_signals_carry_the_label(self):
        mobile = render.render_mobile_overview(
            self.snapshot_with_stale_sources(), None, [], "Recorded snapshot")
        # Retained fee value stays visible with its adjacent label; the stale
        # price is suppressed rather than shown as current market context.
        self.assertIn("<strong class='mobile-quick-link__value'>5,527 lamports</strong>", mobile)
        self.assertIn("<small class='mobile-quick-link__meta'>last-known-good · sample collected Aug 05, 2026 · 09:00 UTC</small>", mobile)
        self.assertIn("<span>SOL price</span><strong>—</strong>", mobile)

    def test_mobile_quick_links_keep_retained_fee_provenance_and_natural_height(self):
        mobile = render.render_mobile_overview(
            self.snapshot_with_stale_sources(), None, [], "Recorded snapshot")
        self.assertIn("<strong class='mobile-quick-link__value'>5,527 lamports</strong>", mobile)
        self.assertIn("<small class='mobile-quick-link__meta'>last-known-good · sample collected Aug 05, 2026 · 09:00 UTC</small>", mobile)
        self.assertEqual(mobile.count("class='mobile-quick-link__destination'>View data"), 2)
        quick_link_rules = re.findall(r"[^{}]*\.mobile-quick-link[^{}]*\{([^}]*)\}", render.CSS)
        self.assertTrue(quick_link_rules)
        self.assertFalse(any(re.search(r"min-height:\s*94px", rule) for rule in quick_link_rules))

    def test_desktop_median_fee_label_is_scoped_to_the_card_sub(self):
        page = render.render_html(self.snapshot_with_stale_sources())
        self.assertIn(
            '<div class="label">Median fee</div><div class="value">5,527 lamports</div>'
            '<div class="sub">non-vote only · last-known-good</div>',
            page)

    def test_stale_core_metrics_do_not_render_as_current_in_the_ticker(self):
        snapshot = self.snapshot_with_stale_sources()
        performance = {"available": True, "stale": True,
                       "source_state": "last_known_good",
                       "latest_tps": 4213.5, "mean_slot_time_secs": 0.4,
                       "peak_tps": 4400}
        snapshot["performance"] = performance
        ticker = render.render_ticker(snapshot, None)
        self.assertNotIn("Latest TPS", ticker)
        self.assertNotIn("Avg slot time", ticker)
        self.assertNotIn("Peak TPS", ticker)

    def test_stale_performance_is_unavailable_in_desktop_headlines_and_instruments(self):
        snapshot = self.snapshot_with_stale_sources()
        snapshot["performance"] = {
            "available": True, "stale": True, "source_state": "last_known_good",
            "latest_tps": 4213.5, "mean_slot_time_secs": 0.4,
            "latest_non_vote_tps": 2689.17, "mean_vote_share_pct": 36.2,
            "samples_used": 12, "non_vote_available": True,
            "samples": [{"slot": 100, "tps": 4213.5, "non_vote_tps": 2689.17}],
        }
        page = render.render_html(snapshot)
        instruments = render.render_network_instruments(snapshot, "desktop")
        self.assertIn("<span class='metric__label'>Mean Slot Time</span><strong class='metric__value'>—</strong>", page)
        self.assertIn("<span class='metric__label'>Latest TPS</span><strong class='metric__value'>—</strong>", page)
        self.assertIn("Throughput unavailable", instruments)
        self.assertNotIn("4,213.5", instruments)
        self.assertNotIn("2,689.17", instruments)

    def test_stale_price_hides_desktop_price_derived_cards(self):
        economics = self.snapshot_with_stale_sources()["economics"]
        economics["available"] = True
        economics["price"].update({"market_cap_usd": 42_000_000_000, "volume_24h_usd": 1_000_000_000})
        snapshot = load_fixture()
        snapshot["economics"] = economics
        cards = render.render_economics_html(snapshot)
        self.assertIn('<div class="label">Market cap</div><div class="value">—</div>', cards)
        self.assertIn('<div class="label">Spot volume 24h</div><div class="value">—</div>', cards)
        self.assertNotIn("$42.00B", cards)
        self.assertNotIn("$1.00B", cards)

    def test_stale_price_never_powers_usd_conversions(self):
        snapshot = self.snapshot_with_stale_sources()
        self.assertIsNone(render.sol_price_usd(snapshot))
        # The lamports figure stays visible with its label; the USD conversion
        # that the stale price would have powered renders as unavailable.
        self.assertEqual(render.fmt_usd(0.0000055, render.sol_price_usd(snapshot)), "—")

    def test_unavailable_economics_hides_nested_current_values(self):
        snapshot = load_fixture()
        snapshot["economics"] = {
            "available": False,
            "price": {"available": True, "price_usd": 123.45},
            "tvl": {"available": True, "tvl_usd": 444_444_444},
            "dex": {"available": True, "volume_24h_usd": 222_222_222},
        }

        ticker = render.render_ticker(snapshot, None)
        desktop = render.render_economics_html(snapshot)
        mobile = render.render_mobile_overview(snapshot, None, [], "Recorded snapshot")

        self.assertNotIn("SOL price", ticker)
        self.assertNotIn("$123.45", ticker)
        self.assertIn("Economic sources unavailable in this snapshot", desktop)
        self.assertNotIn("444.44M", desktop)
        self.assertNotIn("222.22M", desktop)
        self.assertIn("<span>SOL price</span><strong>—</strong>", mobile)


class TestMixedSourceTruthfulness(unittest.TestCase):
    """Held economics fail release while views still hide retained stale values."""

    @staticmethod
    def mixed_snapshot():
        snapshot = deepcopy(load_fixture())
        snapshot.update({
            "schema_version": 7,
            "collected_at": "2026-08-24T12:00:00+00:00",
            "source": {"endpoint": "https://api.mainnet.solana.com"},
            "network": {"healthy": True},
            "epoch": {"available": True, "epoch": 800, "progress_pct": 50},
            "inflation": {"available": False},
            "activity": {"available": False},
            "news": {"available": False},
            "growth": {"available": False},
        })
        snapshot["performance"].update({
            "stale": True, "source_state": "last_known_good", "latest_tps": 9991,
        })
        snapshot["supply"].update({
            "stale": True,
            "source_state": "last_known_good",
            "total_sol": 888_888_888,
            "circulating_sol": 800_000_000,
            "non_circulating_sol": 88_888_888,
            "circulating_pct": 90.0,
        })
        snapshot["validators"].update({
            "stale": True, "source_state": "last_known_good",
        })
        snapshot["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 123.45},
            "tvl": {"available": True, "stale": True, "tvl_usd": 444_444_444},
            "stablecoins": {"available": True, "source_state": "last_known_good", "stablecoin_usd": 333_333_333},
            "dex": {"available": True, "freshness": "stale", "volume_24h_usd": 222_222_222},
            "protocols": {"available": False}, "sources": {},
        }
        return snapshot

    def test_release_gate_holds_economics_and_views_hide_stale_source_matrix(self):
        snapshot = self.mixed_snapshot()
        gate = pipeline.check_publishable(
            snapshot, now=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
            max_age_seconds=21_600,
        )
        self.assertFalse(gate["publishable"], gate)
        self.assertEqual(gate["failures"][0]["check"], "release_policy")

        markdown = render.render_markdown(snapshot)
        desktop = render.render_html(snapshot)
        ticker = render.render_ticker(snapshot, {"status": "ok", "changes": {}})
        workbench = render.render_validator_workbench(snapshot, "desktop")
        mobile_data = render.render_mobile_data(snapshot)

        self.assertIn("source-specific rights", desktop)
        self.assertNotIn("keyless public APIs", desktop)
        self.assertIn(
            "keyless access is not permission to republish", markdown,
        )

        for output in (markdown, desktop, ticker, workbench):
            for retained_value in ("9,991", "888,888,888", "7,777", "444,444,444", "333,333,333", "222,222,222"):
                self.assertNotIn(retained_value, output)
        self.assertIn("_Performance samples unavailable in this snapshot._", markdown)
        self.assertIn("_Supply data unavailable in this snapshot._", markdown)
        self.assertIn("_Validator data unavailable in this snapshot._", markdown)
        self.assertNotIn("Latest TPS", ticker)
        self.assertNotIn("Active validators", ticker)
        self.assertIn("Validator ranking unavailable", workbench)
        for source in ("Performance samples", "Total value locked", "USD-pegged circulating supply", "DeFiLlama-indexed Solana DEX volume"):
            row = mobile_data.split(f"<strong>{source}</strong>", 1)[1].split("</article>", 1)[0]
            self.assertIn("<span class='mobile-source-status'>Unavailable</span>", row)
            self.assertNotIn("Recorded", row)
        price_row = mobile_data.split("<strong>SOL price</strong>", 1)[1].split("</article>", 1)[0]
        self.assertIn("<span class='mobile-source-status'>Measured</span>", price_row)


class TestPublicObservationBindings(unittest.TestCase):
    """Human artifacts bind visible values to the exact public observation grain."""

    @staticmethod
    def observation_fixture(count=2, configure_latest=None):
        history = recorded_history_fixture(count)
        for snapshot in history:
            for sample in snapshot["performance"]["samples"]:
                sample.update({
                    "sample_period_secs": 60,
                    "slots": 150,
                    "transactions": int(sample["tps"] * 60),
                    "non_vote_transactions": None,
                })
            snapshot["validators"] = {
                "available": False,
                "reason": "not collected for focused observation fixture",
            }
        if configure_latest is not None:
            configure_latest(history[-1])
        history = render.facts_module.publication_history(
            history, selected=history[-1],
        )
        direct = render.facts_module.public_observation_records(
            history[-1], history=history,
        )
        observations = [
            *direct,
            *render.build_derived_observation_records(history, direct),
        ]
        return history, observations, render.public_observation_indexes(observations)

    @staticmethod
    def configure_current_state_anomalies(history):
        for index, snapshot in enumerate(history):
            slot = 302_400_000 + index * 1_000
            snapshot["network"]["slot"] = slot
            for offset, sample in enumerate(snapshot["performance"]["samples"]):
                sample["slot"] = slot - offset * 150
            snapshot["validators"]["delinquent_pct"] = 4.5
        latest = history[-1]
        previous_slot = history[-2]["network"]["slot"]
        latest["network"]["slot"] = previous_slot
        for offset, sample in enumerate(latest["performance"]["samples"]):
            sample["slot"] = previous_slot - offset * 150
        latest["performance"]["mean_slot_time_secs"] = 0.75
        latest["validators"]["delinquent_pct"] = 5.25
        latest["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 153.0},
            "tvl": {"available": True, "tvl_usd": 9_000_000_003.0},
        }

    @staticmethod
    def configure_baseline_anomalies(history):
        for index, snapshot in enumerate(history):
            slot = 302_400_000 + index * 1_000
            snapshot["network"]["slot"] = slot
            for offset, sample in enumerate(snapshot["performance"]["samples"]):
                sample["slot"] = slot - offset * 150
            snapshot["performance"]["latest_tps"] = 4_000.0
            snapshot["performance"]["samples"][0]["tps"] = 4_000.0
            snapshot["supply"]["circulating_sol"] = 480_000_000.0
            snapshot["validators"]["delinquent_pct"] = 4.0
        latest = history[-1]
        latest["performance"]["latest_tps"] = 2_000.0
        latest["performance"]["samples"][0]["tps"] = 2_000.0
        latest["supply"]["circulating_sol"] = 490_000_000.0
        latest["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 153.0},
            "tvl": {"available": True, "tvl_usd": 9_000_000_003.0},
        }

    @staticmethod
    def anomaly_observation_fixture(configure_history):
        history = recorded_history_fixture(4)
        configure_history(history)
        for snapshot in history:
            for sample in snapshot["performance"]["samples"]:
                sample.update({
                    "sample_period_secs": 60,
                    "slots": 150,
                    "transactions": int(sample["tps"] * 60),
                    "non_vote_transactions": None,
                })
        history = render.facts_module.publication_history(
            history, selected=history[-1],
        )
        generated_at = history[-1]["collected_at"]
        analysis = render.analysis_for(history[-1], history, now=generated_at)
        direct = render.facts_module.public_observation_records(
            history[-1], history=history,
        )
        observations = [
            *direct,
            *render.build_derived_observation_records(history, direct),
        ]
        observations.extend(render.build_anomaly_observation_records(
            history, observations, analysis, generated_at,
        ))
        indexes = render.public_observation_indexes(observations)
        bound_analysis = render.bind_public_analysis(analysis, indexes, history)
        return history, analysis, bound_analysis, observations, indexes

    @classmethod
    def catalog_observation_fixture(cls):
        def configure(snapshot):
            snapshot["economics"] = {
                "available": True,
                "price": {"available": True, "price_usd": 151.0, "freshness": "fresh"},
                "sources": {
                    "zeta": {"available": False, "label": "Zeta"},
                    "alpha": {"available": True, "label": "Alpha"},
                },
            }
            snapshot["news"] = {
                "available": True,
                "current_status": {
                    "available": True,
                    "description": "All Systems Operational",
                },
                "items": [],
                "sources": {
                    "zeta_feed": {"available": False, "label": "Zeta feed"},
                    "alpha_feed": {"available": True, "label": "Alpha feed"},
                },
            }
            snapshot["growth"] = {
                "available": True,
                "daily_active_addresses": {"available": False},
                "daily_fee_payers": {"available": False},
                "tokenized_equities": {
                    "available": True,
                    "registry_asset_count": 0,
                    "supply_coverage": {},
                    "valuation": {"available": False},
                    "volume": {
                        "available": True,
                        "volume_24h_usd": 0.0,
                        "assets_with_pairs": 0,
                        "volume_covered_pair_count": 0,
                        "pair_count": 0,
                        "invalid_row_count": 0,
                        "conflicting_pair_count": 0,
                        "batches_succeeded": 0,
                        "batches_requested": 0,
                        "transport_complete": False,
                        "market_coverage": "partial",
                    },
                    "proof_of_reserves": {"available": False},
                    "assets": [],
                },
                "sources": {
                    "dex_volume": {
                        "available": True,
                        "partial": True,
                        "market_coverage": "partial",
                    },
                },
            }
            snapshot["dune"] = {
                "available": False,
                "requires_api_key": True,
                "reason": (
                    "Dune result read skipped: finite owner-approved credit "
                    "allowance is missing or spent"
                ),
                "query_id": "8590950",
                "last_known_good": {
                    "query_id": "8590950",
                    "execution_ended_at": "2026-09-03T01:19:20Z",
                    "row_count": 175,
                },
            }
            snapshot["feature_activation"] = {
                "available": True,
                "observed_at": snapshot["collected_at"],
                "coverage_complete": True,
                "coverage_numerator": 10,
                "coverage_denominator": 10,
                "activated_feature_count": 3,
                "source": {
                    "method": "getMultipleAccounts",
                    "commitment": "finalized",
                    "rpc_context_slot": 444326576,
                },
                "features": [],
            }

        return cls.observation_fixture(configure_latest=configure)

    @staticmethod
    def catalog_row_ids(catalog, name):
        marker = f"<span class='dataset-name'>{render.html.escape(name)}</span>"
        marker_at = catalog.index(marker)
        row_at = catalog.rfind("<tr data-page-row='desktop'", 0, marker_at)
        row_open = catalog[row_at:catalog.index(">", row_at) + 1]
        match = re.search(r" data-observation-ids='([^']+)'", row_open)
        return match.group(1).split() if match else []

    @staticmethod
    def mobile_source_row_ids(mobile, name):
        marker_at = mobile.index(f"<strong>{name}</strong>")
        row_at = mobile.rfind("<article", 0, marker_at)
        row_open = mobile[row_at:mobile.index(">", row_at) + 1]
        match = re.search(r" data-observation-ids='([^']+)'", row_open)
        return match.group(1).split() if match else []

    def test_data_catalog_rows_and_support_values_use_exact_ordered_observations(self):
        history, observations, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        history_subject = f"{history[0]['collected_at']}->{snapshot_at}"
        analysis = render.analysis_for(history[-1], history, now=snapshot_at)
        observations = [
            *observations,
            *render.build_anomaly_observation_records(
                history, observations, analysis, snapshot_at,
            ),
        ]
        indexes = render.public_observation_indexes(observations)
        analysis = render.bind_public_analysis(analysis, indexes, history)
        comparison = pipeline.recheck(history)
        catalog = render.render_data_catalog(
            history[-1], analysis, comparison, history, indexes,
        )

        summary = lambda *metric_ids: [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in metric_ids
        ]
        derived = lambda subject_id, *metric_ids: [
            indexes["derived"][(metric_id, subject_id)]["observation_id"]
            for metric_id in metric_ids
        ]
        expected = {
            "Network & epoch": summary(
                "network_healthy", "network_slot", "network_block_time_unix",
                "epoch", "epoch_block_height", "epoch_transaction_count",
            ),
            "Performance samples": summary("performance_samples_used"),
            "Supply": summary(
                "total_supply_sol", "circulating_sol", "non_circulating_supply_sol",
            ),
            "Inflation policy": summary(
                "inflation_current_total_pct", "inflation_terminal_pct",
            ),
            "Validator set": summary("active_count"),
            "Block activity": summary("activity_blocks_sampled"),
            "Economic indicators": [
                *summary("price_usd"),
                *derived(
                    snapshot_at, "catalog_economic_available_source_count",
                    "catalog_economic_source_count",
                ),
            ],
            "Dune registered activity query": summary(
                *render.DUNE_CATALOG_METRICS,
            ),
            "Official release feeds": [
                *summary("network_status_description"),
                *derived(
                    snapshot_at, "catalog_news_available_source_count",
                    "catalog_news_source_count",
                ),
            ],
            "Selected feature-account activation": summary(
                "feature_activation_coverage_numerator",
                "feature_activation_coverage_denominator",
                "feature_activated_count",
                "feature_rpc_context_slot",
            ),
            "Tokenized-equity registry": summary("xstock_registry_asset_count"),
            "Tokenized-equity supply": summary(
                "xstock_supply_coverage_numerator",
                "xstock_supply_coverage_denominator",
                "xstock_fresh_supply_asset_count",
                "xstock_supply_successful_this_run",
                "xstock_supply_queried_this_run",
                "xstock_supply_oldest_observation_at",
                "xstock_supply_newest_observation_at",
            ),
            "Selected four-mint stablecoin total supply": summary(
                "selected_stablecoin_coverage_numerator",
                "selected_stablecoin_coverage_denominator",
                "selected_stablecoin_newest_observation_at",
            ),
            "Tokenized-equity valuation": summary("xstock_usd_valuation"),
            "Indexed Solana DEX-pool volume": summary(
                "xstock_indexed_dex_volume_24h_usd",
                "xstock_indexed_dex_asset_count",
                "xstock_indexed_dex_volume_covered_pair_count",
                "xstock_indexed_dex_pair_count",
                "xstock_indexed_dex_invalid_row_count",
                "xstock_indexed_dex_conflicting_pair_count",
                "xstock_indexed_dex_batches_succeeded",
                "xstock_indexed_dex_batches_requested",
                "xstock_indexed_dex_transport_complete",
                "xstock_indexed_dex_market_coverage",
            ),
            "Issuer proof of reserves": summary("xstock_proof_of_reserves_coverage"),
            "Solana Data provider ranges": summary(
                "stablecoin_active_address_provider_count",
                "transaction_initiator_provider_count",
                "stablecoin_active_address_provider_date",
                "transaction_initiator_provider_date",
            ),
            "Network-wide daily active addresses": summary(
                "network_wide_daily_active_addresses",
                "stablecoin_active_address_provider_count",
                "transaction_initiator_provider_count",
                "stablecoin_active_address_provider_date",
                "transaction_initiator_provider_date",
            ),
            "Total tokenized-equity volume": summary(
                "xstock_total_trading_volume_24h_usd",
            ),
            "Anomaly analysis": render.anomaly_summary_observation_ids(analysis),
            "Snapshot delta": derived(
                history_subject, "latest_delta_comparable_count",
            ),
        }

        rendered_names = re.findall(
            r"<span class='dataset-name'>(.*?)</span>", catalog,
        )
        self.assertEqual(
            rendered_names,
            [render.html.escape(name) for name in render.DATA_CATALOG_DATASETS],
        )
        self.assertEqual(len(expected), len(render.DATA_CATALOG_DATASETS))
        for name, expected_ids in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.catalog_row_ids(catalog, name), expected_ids)

        self.assertIn("Dune query 8590950", catalog)
        self.assertIn("Current result read paused", catalog)
        self.assertIn("last-known-good execution metadata only", catalog)
        self.assertIn("daily aggregate values unavailable in this snapshot", catalog)
        self.assertIn("10 / 10 pinned feature accounts inspected", catalog)
        self.assertIn("selected set, not a complete upgrade inventory", catalog)

        dex_records = [
            indexes["summary"][(metric_id, snapshot_at)]
            for metric_id in (
                "xstock_indexed_dex_volume_24h_usd",
                "xstock_indexed_dex_asset_count",
                "xstock_indexed_dex_volume_covered_pair_count",
                "xstock_indexed_dex_pair_count",
                "xstock_indexed_dex_invalid_row_count",
                "xstock_indexed_dex_conflicting_pair_count",
                "xstock_indexed_dex_batches_succeeded",
                "xstock_indexed_dex_batches_requested",
                "xstock_indexed_dex_transport_complete",
                "xstock_indexed_dex_market_coverage",
            )
        ]
        self.assertEqual([record["value"] for record in dex_records[:8]], [0.0] * 8)
        self.assertIs(dex_records[8]["value"], False)
        self.assertEqual(dex_records[8]["type"], "boolean")
        self.assertEqual(dex_records[9]["value"], "partial")
        self.assertEqual(dex_records[9]["type"], "categorical")

        catalog_count = indexes["derived"][("catalog_dataset_count", snapshot_at)]
        schema = indexes["summary"][("snapshot_schema_version", snapshot_at)]
        history_count = indexes["derived"][("history_snapshot_count", history_subject)]
        chartable_count = indexes["derived"][(
            "history_chartable_series_count", history_subject,
        )]
        self.assertEqual(catalog_count["value"], len(render.DATA_CATALOG_DATASETS))
        self.assertEqual(schema["value"], history[-1]["schema_version"])
        self.assertEqual(history_count["value"], len(history))
        self.assertEqual(
            chartable_count["value"],
            sum(
                series["charted"]
                for series in render.charts_module.history_json(history)["series"].values()
            ),
        )
        self.assertIn(
            f"<caption data-observation-ids='{catalog_count['observation_id']}'>"
            f"{len(render.DATA_CATALOG_DATASETS)} repository-backed datasets",
            catalog,
        )
        self.assertIn(
            f"<div data-observation-ids='{schema['observation_id']}'><dt>Schema version</dt>",
            catalog,
        )
        self.assertIn(
            f"<div data-observation-ids='{history_count['observation_id']}'><dt>Recorded snapshots</dt>",
            catalog,
        )
        self.assertIn(
            f"<div data-observation-ids='{chartable_count['observation_id']}'><dt>Chartable series</dt>",
            catalog,
        )

        page = render.publish(
            history[-1], analysis, comparison, history, "Recorded snapshot",
            observations=observations,
        )
        self.assertIn(
            "<div class='snapshot-note' data-observation-ids='"
            f"{history_count['observation_id']} {schema['observation_id']}'>"
            f"Recorded snapshot · {len(history)} snapshots · schema "
            f"{history[-1]['schema_version']}</div>",
            page,
        )

    def test_dated_subject_observations_keep_one_record_per_source_date(self):
        """Provider benchmark rows keep one public record per (subject, source date)."""
        def configure_latest(snapshot):
            snapshot["growth"] = {
                "available": True,
                "daily_active_addresses": {
                    "available": True,
                    "history_available": True,
                    "partial": False,
                    "semantic_metric_id": "stablecoin_active_address_provider_range",
                    "display_name": "Stablecoin active-address provider range",
                    "source_label": "Active Addresses",
                    "scope": "provider observations for Solana stablecoin activity",
                    "source_url": "https://solana.com/api/databricks/data?days=365",
                    "provider_observations": [
                        {"date": "2026-08-01", "provider": "Allium", "value": 1_000},
                        {"date": "2026-08-02", "provider": "Allium", "value": 1_100},
                        {"date": "2026-08-02", "provider": "Helius", "value": 1_200},
                    ],
                },
            }

        history, observations, indexes = self.observation_fixture(
            configure_latest=configure_latest,
        )
        snapshot_at = history[-1]["collected_at"]
        provider_records = [
            record for record in observations
            if record["metric_id"] == "stablecoin_active_address_provider_range"
            and record["snapshot_collected_at"] == snapshot_at
        ]
        self.assertEqual(
            sorted((record["subject_id"], record["observed_at"]) for record in provider_records),
            [("Allium", "2026-08-01"), ("Allium", "2026-08-02"), ("Helius", "2026-08-02")],
        )
        self.assertEqual(len({record["observation_id"] for record in provider_records}), 3)
        for record in provider_records:
            self.assertIs(indexes["subject"][(
                record["metric_id"], record["subject_id"], record["observed_at"], snapshot_at,
            )], record)

    def test_catalog_source_counts_use_sorted_source_ids_and_schema_for_empty_sets(self):
        history, _, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        for prefix, metric_id in (
            ("economic", "economic_source_available"),
            ("news", "news_source_available"),
        ):
            records = sorted(
                (
                    subject_id,
                    indexes["subject"][(metric_id, subject_id, None, snapshot_at)],
                )
                for candidate_metric, subject_id, _candidate_at, candidate_at in indexes["subject"]
                if candidate_metric == metric_id and candidate_at == snapshot_at
            )
            expected_inputs = [record["observation_id"] for _, record in records]
            self.assertEqual([subject_id for subject_id, _ in records], sorted(
                subject_id for subject_id, _ in records
            ))
            self.assertEqual([record["value"] for _, record in records], [True, False])
            for suffix, value in (("source_count", 2), ("available_source_count", 1)):
                derived_record = indexes["derived"][(
                    f"catalog_{prefix}_{suffix}", snapshot_at,
                )]
                self.assertEqual(derived_record["value"], value)
                self.assertEqual(
                    derived_record["input_observation_ids"], expected_inputs,
                )

        empty_history, _, empty_indexes = self.observation_fixture()
        empty_at = empty_history[-1]["collected_at"]
        schema_id = empty_indexes["summary"][(
            "snapshot_schema_version", empty_at,
        )]["observation_id"]
        for prefix in ("economic", "news"):
            for suffix in ("source_count", "available_source_count"):
                record = empty_indexes["derived"][(
                    f"catalog_{prefix}_{suffix}", empty_at,
                )]
                self.assertEqual(record["value"], 0)
                self.assertEqual(record["input_observation_ids"], [schema_id])

    def test_catalog_comparable_total_uses_only_changed_and_steady_counts(self):
        history, _, indexes = self.observation_fixture()
        comparison = pipeline.recheck(history)
        pair_subject = (
            f"{history[-2]['collected_at']}->{history[-1]['collected_at']}"
        )
        changed = indexes["derived"][("latest_delta_changed_count", pair_subject)]
        steady = indexes["derived"][("latest_delta_steady_count", pair_subject)]
        comparable = indexes["derived"][(
            "latest_delta_comparable_count", pair_subject,
        )]
        self.assertEqual(comparable["value"], changed["value"] + steady["value"])
        self.assertEqual(
            comparable["input_observation_ids"],
            [changed["observation_id"], steady["observation_id"]],
        )
        catalog = render.render_data_catalog(
            history[-1], None, comparison, history, indexes,
        )
        self.assertEqual(
            self.catalog_row_ids(catalog, "Snapshot delta"),
            [comparable["observation_id"]],
        )

        refused_history, _, refused_indexes = self.observation_fixture(count=1)
        refused = pipeline.recheck(refused_history)
        self.assertNotEqual(refused.get("status"), "ok")
        self.assertFalse(any(
            metric_id == "latest_delta_comparable_count"
            for metric_id, _ in refused_indexes["derived"]
        ))
        refused_catalog = render.render_data_catalog(
            refused_history[-1], None, refused, refused_history, refused_indexes,
        )
        self.assertEqual(
            self.catalog_row_ids(refused_catalog, "Snapshot delta"), [],
        )

    def test_catalog_fails_when_required_summary_or_derived_record_is_missing(self):
        history, _, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        comparison = pipeline.recheck(history)
        for kind, key in (
            ("summary", ("network_slot", snapshot_at)),
            ("derived", ("catalog_dataset_count", snapshot_at)),
        ):
            broken = {
                name: dict(records) for name, records in indexes.items()
            }
            del broken[kind][key]
            with self.subTest(kind=kind), self.assertRaisesRegex(
                ValueError, "public observation",
            ):
                render.render_data_catalog(
                    history[-1], None, comparison, history, broken,
                )

    def test_latest_delta_counts_use_all_ordered_direct_pair_inputs(self):
        history, observations, indexes = self.observation_fixture()
        previous_at, current_at = [item["collected_at"] for item in history]
        pair_subject = f"{previous_at}->{current_at}"
        expected_inputs = [
            indexes["summary"][(spec["key"], snapshot_at)]["observation_id"]
            for spec in render.delta_module.METRICS
            for snapshot_at in (previous_at, current_at)
        ]

        self.assertEqual(len(render.delta_module.METRICS), 14)
        self.assertEqual(len(expected_inputs), 28)
        self.assertEqual(len(set(expected_inputs)), 28)
        direct_by_id = {
            record["observation_id"]: record
            for record in observations if record["record_kind"] == "direct"
        }
        self.assertTrue(set(expected_inputs).issubset(direct_by_id))
        for bucket in ("changed", "steady", "not_comparable"):
            record = indexes["derived"][(
                f"latest_delta_{bucket}_count", pair_subject,
            )]
            self.assertEqual(record["input_observation_ids"], expected_inputs)

        for metric_id in (
            "price_usd", "tvl_usd", "usd_pegged_circulating_usd",
            "dex_volume_24h_usd",
        ):
            for snapshot_at in (previous_at, current_at):
                record = indexes["summary"][(metric_id, snapshot_at)]
                self.assertEqual(record["status"], "unavailable")
                self.assertIsNone(record["value"])
                self.assertIn(record["observation_id"], expected_inputs)

    def test_full_render_keeps_summary_tps_distinct_from_slot_sample_tps(self):
        history, observations, indexes = self.observation_fixture()
        current_at = history[-1]["collected_at"]
        current_slot = history[-1]["performance"]["samples"][0]["slot"]
        latest = indexes["summary"][("latest_tps", current_at)]
        sample = indexes["slot"][("performance_sample_tps", current_at, current_slot)]

        self.assertEqual(latest["metric_id"], "latest_tps")
        self.assertEqual(sample["metric_id"], "performance_sample_tps")
        self.assertEqual(latest["value"], sample["value"])
        self.assertNotEqual(latest["observation_id"], sample["observation_id"])

        comparison = render.bind_public_comparison(
            pipeline.recheck(history), indexes,
        )
        html_page = render.publish(
            history[-1], None, comparison, history, "Recorded snapshot",
            observations=observations,
        )
        markdown = render.render_markdown(
            history[-1], None, comparison, history, observations=observations,
        )
        summary_ids = {
            key: record["observation_id"]
            for key, record in indexes["summary"].items()
        }
        history_payload = render.bind_public_history(
            render.charts_module.history_json(history, observation_ids=summary_ids),
            indexes,
            history,
        )
        render.validate_public_observation_bindings(
            html_page, markdown, observations, history_payload, comparison,
        )
        self.assertIn(sample["observation_id"], html_page)
        self.assertRegex(
            html_page,
            rf"<li class='metric' data-observation-id='{re.escape(latest['observation_id'])}'>"
            r"<span class='metric__label'>Latest TPS</span>",
        )
        self.assertIn(
            f"| Latest TPS | 4,010 <!-- observation-ids:{latest['observation_id']} --> |",
            markdown,
        )

    def test_unknown_emitted_observation_binding_fails(self):
        _, observations, _ = self.observation_fixture()
        unknown_id = "obs-v1:" + "f" * 64
        self.assertNotIn(
            unknown_id, {record["observation_id"] for record in observations},
        )

        with self.assertRaisesRegex(ValueError, "unknown observation IDs"):
            render.validate_public_observation_bindings(
                f"<div data-observation-id='{unknown_id}'></div>",
                "",
                observations,
                {"series": {}},
                None,
            )

    def test_required_known_observation_binding_omission_fails(self):
        history, observations, indexes = self.observation_fixture()
        required_id = indexes["summary"][(
            "latest_tps", history[-1]["collected_at"],
        )]["observation_id"]
        history_payload = {
            "series": {
                "latest_tps": {"points": [{"observation_id": required_id}]},
            },
        }

        with self.assertRaisesRegex(ValueError, "omit required observation bindings"):
            render.validate_public_observation_bindings(
                "", "", observations, history_payload, None,
            )

    def test_current_state_anomalies_bind_and_emit_exact_observations(self):
        history, _, analysis, observations, indexes = self.anomaly_observation_fixture(
            self.configure_current_state_anomalies,
        )
        latest_at = history[-1]["collected_at"]
        previous_at = history[-2]["collected_at"]
        pair_subject = f"{previous_at}->{latest_at}"
        summary = lambda metric_id, snapshot_at=latest_at: indexes["summary"][(
            metric_id, snapshot_at,
        )]["observation_id"]
        derived = lambda metric_id, subject_id=latest_at: indexes["derived"][(
            metric_id, subject_id,
        )]["observation_id"]
        expected = {
            "delinquency_high": {
                "observed": summary("delinquent_pct"),
                "threshold": derived("anomaly_delinquency_high_threshold_pct"),
            },
            "slot_stalled": {
                "observed": summary("network_slot"),
                "baseline": summary("network_slot", previous_at),
                "elapsed_hours": derived(
                    "anomaly_slot_stalled_elapsed_hours", pair_subject,
                ),
            },
            "slow_slots": {
                "observed": summary("mean_slot_time_secs"),
                "threshold": derived("anomaly_slow_slots_threshold_secs"),
                "baseline": derived("anomaly_slow_slots_threshold_secs"),
            },
        }
        self.assertEqual(
            [item["code"] for item in analysis["findings"]],
            ["delinquency_high", "slot_stalled", "slow_slots"],
        )
        for finding in analysis["findings"]:
            self.assertEqual(
                finding["observation_ids"], expected[finding["code"]],
            )

        root_ids = render.anomaly_summary_observation_ids(analysis)
        coverage_ids = list(dict.fromkeys(
            observation_id
            for coverage in analysis["coverage"].values()
            for observation_id in coverage["observation_ids"].values()
        ))
        self.assertTrue(set(coverage_ids).issubset(root_ids))
        finding_ids = [
            render.bound_observation_ids(finding)
            for finding in analysis["findings"]
        ]
        anomaly_html = render.render_anomalies_html(analysis)
        anomaly_markdown = "\n".join(render.render_anomalies_markdown(analysis))
        self.assertEqual(
            [item.split() for item in re.findall(
                r"data-observation-ids='([^']+)'", anomaly_html,
            )],
            [root_ids, *finding_ids],
        )
        self.assertEqual(
            [item.split() for item in re.findall(
                r"<!-- observation-ids:([^>]+) -->",
                anomaly_markdown,
            )],
            [root_ids, *finding_ids],
        )

        render.validate_public_observation_bindings(
            anomaly_html, anomaly_markdown, observations, {"series": {}}, None, analysis,
        )
        omitted = analysis["coverage"]["sol_price"]["observation_ids"][
            "unavailable"
        ]
        with self.assertRaisesRegex(ValueError, "omit required observation bindings"):
            render.validate_public_observation_bindings(
                anomaly_html.replace(omitted, ""),
                anomaly_markdown.replace(omitted, ""),
                observations, {"series": {}}, None, analysis,
            )

        markers = render.render_change_markers(
            history[-1], analysis, pipeline.recheck(history),
        )
        self.assertEqual(
            re.findall(r"data-observation-ids='([^']+)'", markers),
            [" ".join(root_ids)],
        )

    def test_baseline_anomalies_use_exact_cadence_observation_inputs(self):
        history, _, analysis, _, indexes = self.anomaly_observation_fixture(
            self.configure_baseline_anomalies,
        )
        latest_at = history[-1]["collected_at"]
        findings = {item["code"]: item for item in analysis["findings"]}
        self.assertEqual(list(findings), ["tps_drop", "supply_move"])

        for code, coverage_code, metric_id, change_metric_id in (
            ("tps_drop", "tps", "latest_tps", "anomaly_tps_drop_absolute_change_pct"),
            ("supply_move", "supply", "circulating_sol", "anomaly_supply_move_change_pct"),
        ):
            with self.subTest(code=code):
                current = indexes["summary"][(metric_id, latest_at)]
                cadence = render.detect.metric_evidence(
                    history[:-1], metric_id,
                )["cadence"]
                cadence_ids = [
                    indexes["summary"][(
                        metric_id, fact["collected_at"],
                    )]["observation_id"]
                    for fact in cadence
                ]
                observed = indexes["derived"][(
                    f"anomaly_{code}_observed_value", latest_at,
                )]
                baseline = indexes["derived"][(
                    f"anomaly_{code}_baseline_value", latest_at,
                )]
                change = indexes["derived"][(change_metric_id, latest_at)]
                eligible = indexes["derived"][(
                    f"anomaly_coverage_{coverage_code}_eligible_prior_count",
                    latest_at,
                )]
                self.assertEqual(observed["input_observation_ids"], [
                    current["observation_id"],
                ])
                self.assertEqual(baseline["input_observation_ids"], cadence_ids)
                self.assertEqual(
                    change["input_observation_ids"],
                    [current["observation_id"], *cadence_ids],
                )
                self.assertEqual(eligible["value"], len(cadence_ids))
                self.assertEqual(findings[code]["observation_ids"], {
                    "observed": observed["observation_id"],
                    "baseline": baseline["observation_id"],
                    "eligible_priors": eligible["observation_id"],
                    "change_pct": change["observation_id"],
                })

    def test_anomaly_projection_retains_only_allowed_nested_observation_ids(self):
        _, _, analysis, _, _ = self.anomaly_observation_fixture(
            self.configure_current_state_anomalies,
        )
        hostile = deepcopy(analysis)
        hostile["private_debug"] = {"secret": "not public"}
        hostile["observation_ids"]["private_id"] = "private-root"
        hostile["findings"][0]["observation_ids"]["private_id"] = "private-finding"
        hostile["coverage"]["tps"]["observation_ids"]["private_id"] = "private-coverage"
        hostile["counts"]["observation_ids"]["private_id"] = "private-count"

        projected = render.project_public_envelope({
            "schema_version": 9,
            "anomalies": hostile,
        })["anomalies"]

        self.assertNotIn("private_debug", projected)
        self.assertEqual(projected["observation_ids"], analysis["observation_ids"])
        self.assertEqual(
            projected["findings"][0]["observation_ids"],
            analysis["findings"][0]["observation_ids"],
        )
        self.assertEqual(
            projected["coverage"]["tps"]["observation_ids"],
            analysis["coverage"]["tps"]["observation_ids"],
        )
        self.assertEqual(
            projected["counts"]["observation_ids"],
            analysis["counts"]["observation_ids"],
        )

    def test_anomaly_binding_rejects_unknown_codes_and_numeric_mismatches(self):
        history, raw_analysis, _, _, indexes = self.anomaly_observation_fixture(
            self.configure_current_state_anomalies,
        )
        unknown = deepcopy(raw_analysis)
        unknown["findings"][0]["code"] = "future_finding"
        with self.assertRaisesRegex(ValueError, "unsupported anomaly finding codes"):
            render.bind_public_analysis(unknown, indexes, history)

        mismatched = deepcopy(raw_analysis)
        mismatched["findings"][0]["observed"] += 0.01
        with self.assertRaisesRegex(ValueError, "disagrees with public observation"):
            render.bind_public_analysis(mismatched, indexes, history)

    def test_anomaly_observations_are_deterministic_for_fixed_inputs_and_time(self):
        first = self.anomaly_observation_fixture(self.configure_baseline_anomalies)
        second = self.anomaly_observation_fixture(self.configure_baseline_anomalies)
        first_history, first_raw, first_bound, first_observations, _ = first
        second_history, second_raw, second_bound, second_observations, _ = second

        self.assertEqual(
            first_history[-1]["collected_at"], "2026-08-01T18:00:00+00:00",
        )
        self.assertEqual(first_history, second_history)
        self.assertEqual(first_raw, second_raw)
        self.assertEqual(first_bound, second_bound)
        self.assertEqual(first_observations, second_observations)

    def test_mobile_growth_and_commission_slides_bind_complete_denominators(self):
        def article_ids(markup, marker):
            marker_at = markup.index(marker)
            article_at = markup.rfind("<article", 0, marker_at)
            tag = markup[article_at:markup.index(">", article_at) + 1]
            match = re.search(r" data-observation-ids='([^']+)'", tag)
            return match.group(1).split() if match else []

        history, _, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        growth_workbenches = [
            render.render_growth_workbench(history[-1], context, indexes)
            for context in ("desktop", "mobile")
        ]
        growth_metrics = (
            "xstock_usd_valuation", "xstock_indexed_dex_volume_24h_usd",
            "xstock_indexed_dex_pair_count", "xstock_indexed_dex_asset_count",
            "xstock_indexed_dex_volume_covered_pair_count",
            "xstock_registry_asset_count",
        )
        expected_growth_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in growth_metrics
        ]
        for growth_workbench in growth_workbenches:
            self.assertEqual(
                article_ids(
                    growth_workbench,
                    "class='validator-metric-component growth-market-component'",
                ),
                expected_growth_ids,
            )
        self.assertEqual(
            expected_growth_ids[-1],
            indexes["summary"][(
                "xstock_registry_asset_count", snapshot_at,
            )]["observation_id"],
        )

        def configure_validators(snapshot):
            row = {
                "rank": 1, "identity": "node-a", "vote_account": "vote-a",
                "state": "current", "stake_sol": 100.0, "share_pct": 100.0,
                "commission": 5, "last_vote": 100, "root_slot": 99,
            }
            snapshot["validators"] = {
                "available": True, "active_count": 1, "delinquent_count": 0,
                "delinquent_pct": 0.0, "active_stake_sol": 100.0,
                "delinquent_stake_sol": 0.0, "nakamoto_coefficient": 1,
                "top_10_share_pct": 100.0, "accounts_with_stake": 1,
                "accounts_missing_stake": 0, "ranked_validator_count": 1,
                "all_validator_count": 1,
                "commission": {
                    "available": True, "median_pct": 5.0, "mean_pct": 5.0,
                    "zero_commission_count": 0, "max_commission_count": 0,
                },
                "ranked_validators": [row], "all_validators": [row],
            }

        validator_history, _, validator_indexes = self.observation_fixture(
            configure_latest=configure_validators,
        )
        validator_at = validator_history[-1]["collected_at"]
        mobile_validators = render.render_validator_workbench(
            validator_history[-1], "mobile", validator_indexes,
        )
        commission_metrics = (
            "validator_median_commission_pct",
            "validator_mean_commission_pct", "active_count",
        )
        expected_commission_ids = [
            validator_indexes["summary"][(
                metric_id, validator_at,
            )]["observation_id"]
            for metric_id in commission_metrics
        ]
        self.assertEqual(
            article_ids(
                mobile_validators,
                "data-validator-component='validator-commission-distribution'",
            ),
            expected_commission_ids,
        )
        self.assertEqual(
            expected_commission_ids[-1],
            validator_indexes["summary"][(
                "active_count", validator_at,
            )]["observation_id"],
        )

    def test_overview_and_history_repeated_values_bind_exact_observations(self):
        def configure(snapshot):
            snapshot["activity"]["window"]["blocks_sampled"] = 3

        history, _, indexes = self.observation_fixture(
            configure_latest=configure,
        )
        snapshot_at = history[-1]["collected_at"]
        evidence_metrics = (
            "network_healthy", "activity_blocks_sampled",
            "network_wide_daily_active_addresses",
        )
        evidence_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in evidence_metrics
        ]
        desktop_evidence = render.render_overview_evidence(
            history[-1], None, indexes,
        )
        self.assertEqual(
            re.findall(r"data-observation-ids='([^']+)'", desktop_evidence),
            evidence_ids,
        )
        mobile_overview = render.render_mobile_overview(
            history[-1], None, history, "Recorded snapshot", indexes,
        )
        self.assertEqual(mobile_overview.count(desktop_evidence), 1)

        comparison = render.bind_public_comparison(
            pipeline.recheck(history), indexes,
        )
        desktop_history = render.render_history_workspace(
            history, None, comparison, indexes,
        )
        previous_at, current_at = (
            history[-2]["collected_at"], history[-1]["collected_at"],
        )
        sample_ids = [
            indexes["summary"][(
                "performance_samples_used", observed_at,
            )]["observation_id"]
            for observed_at in (previous_at, current_at)
        ]
        sample_binding = f"data-observation-ids='{' '.join(sample_ids)}'"
        self.assertEqual(desktop_history.count(sample_binding), 2)
        self.assertIn(
            "A contains 8 and B contains 8 usable recorded RPC samples",
            desktop_history,
        )
        self.assertIn(
            "A: 8 usable RPC samples  ·  B: 8 usable RPC samples",
            desktop_history,
        )

        pair_subject = f"{previous_at}->{current_at}"
        for metric_id in (
            "latest_delta_changed_count", "latest_delta_steady_count",
        ):
            observation_id = indexes["derived"][(
                metric_id, pair_subject,
            )]["observation_id"]
            self.assertEqual(desktop_history.count(observation_id), 2)

        mobile_history = render.render_mobile_history(
            history, comparison, indexes,
        )
        self.assertEqual(mobile_history.count(sample_binding), 1)
        for observed_at in (previous_at, current_at):
            tps_id = indexes["summary"][(
                "latest_tps", observed_at,
            )]["observation_id"]
            self.assertEqual(
                mobile_history.count(
                    f"<li data-observation-ids='{tps_id}'>"
                ),
                1,
            )

    def test_source_flow_binds_simd_and_scoped_official_counts(self):
        def configure(snapshot):
            snapshot["news"] = {
                "available": True, "items": [],
                "sources": {
                    "zeta_feed": {"available": False, "label": "Zeta"},
                    "simd_proposals": {
                        "available": False, "label": "SIMD",
                        "reason": "not collected",
                    },
                    "alpha_feed": {"available": True, "label": "Alpha"},
                },
            }

        history, _, indexes = self.observation_fixture(
            configure_latest=configure,
        )
        snapshot_at = history[-1]["collected_at"]
        simd_records = [
            indexes["summary"][(metric_id, snapshot_at)]
            for metric_id in ("simd_proposal_count", "simd_document_count")
        ]
        for record in simd_records:
            self.assertEqual(record["status"], "unavailable")
            self.assertIsNone(record["value"])

        official_subjects = ("alpha_feed", "zeta_feed")
        official_inputs = [
            indexes["subject"][(
                "news_source_available", source_id, None, snapshot_at,
            )]["observation_id"]
            for source_id in official_subjects
        ]
        available_count = indexes["derived"][(
            "source_flow_official_available_source_count", snapshot_at,
        )]
        source_count = indexes["derived"][(
            "source_flow_official_source_count", snapshot_at,
        )]
        self.assertEqual(available_count["value"], 1)
        self.assertEqual(source_count["value"], 2)
        self.assertEqual(available_count["input_observation_ids"], official_inputs)
        self.assertEqual(source_count["input_observation_ids"], official_inputs)
        simd_source_id = indexes["subject"][(
            "news_source_available", "simd_proposals", None, snapshot_at,
        )]["observation_id"]
        self.assertNotIn(simd_source_id, official_inputs)

        summary_metrics = (
            "network_healthy", "activity_blocks_sampled", "price_usd", "tvl_usd",
            "usd_pegged_circulating_usd", "dex_volume_24h_usd",
            "simd_proposal_count", "simd_document_count",
        )
        expected_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in summary_metrics
        ]
        expected_ids.extend(
            indexes["subject"][(
                "news_source_available", source_id, None, snapshot_at,
            )]["observation_id"]
            for source_id in ("alpha_feed", "simd_proposals", "zeta_feed")
        )
        expected_ids.extend((
            available_count["observation_id"], source_count["observation_id"],
        ))
        flow = render.render_source_flow(history[-1], indexes)
        binding = re.search(
            r"<figure class='source-flow panel' data-observation-ids='([^']+)'",
            flow,
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding.group(1).split(), expected_ids)

    def test_mobile_data_rows_bind_schema_supply_stablecoin_and_dex_details(self):
        history, _, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        mobile = render.render_mobile_data(history[-1], None, None, indexes)

        schema_id = indexes["summary"][(
            "snapshot_schema_version", snapshot_at,
        )]["observation_id"]
        snapshot_heading = re.search(
            r"<div class='mobile-data-snapshot'([^>]*)>", mobile,
        )
        self.assertIsNotNone(snapshot_heading)
        self.assertEqual(
            re.search(
                r" data-observation-ids='([^']+)'", snapshot_heading.group(1),
            ).group(1).split(),
            [schema_id],
        )

        expected_rows = {
            "Dune registered activity query": render.DUNE_CATALOG_METRICS,
            "Selected feature-account activation": (
                "feature_activation_coverage_numerator",
                "feature_activation_coverage_denominator",
                "feature_activated_count",
                "feature_rpc_context_slot",
            ),
            "Finalized token supply": (
                "xstock_supply_coverage_numerator",
                "xstock_supply_coverage_denominator",
                "xstock_supply_oldest_observation_at",
                "xstock_supply_newest_observation_at",
            ),
            "Selected four-mint stablecoin total supply": (
                "selected_stablecoin_coverage_numerator",
                "selected_stablecoin_coverage_denominator",
            ),
            "Indexed Solana DEX-pool volume": (
                "xstock_indexed_dex_volume_24h_usd",
                "xstock_indexed_dex_asset_count",
                "xstock_indexed_dex_volume_covered_pair_count",
                "xstock_indexed_dex_pair_count",
                "xstock_indexed_dex_invalid_row_count",
                "xstock_indexed_dex_conflicting_pair_count",
                "xstock_indexed_dex_batches_succeeded",
                "xstock_indexed_dex_batches_requested",
                "xstock_indexed_dex_transport_complete",
                "xstock_indexed_dex_market_coverage",
            ),
        }
        for name, metric_ids in expected_rows.items():
            with self.subTest(name=name):
                self.assertEqual(self.mobile_source_row_ids(mobile, name), [
                    indexes["summary"][(
                        metric_id, snapshot_at,
                    )]["observation_id"]
                    for metric_id in metric_ids
                ])
        self.assertIn("Current result read paused", mobile)
        self.assertIn("last-known-good execution metadata only", mobile)
        self.assertIn("selected set, not a complete upgrade inventory", mobile)

    def test_throughput_inspector_uses_snapshot_collection_time(self):
        snapshot = load_fixture()
        snapshot["performance"]["non_vote_available"] = True
        for sample in snapshot["performance"]["samples"]:
            sample["non_vote_tps"] = sample["tps"] - 1_000
            sample["vote_tps"] = 1_000.0
        instruments = render.render_network_instruments(snapshot, "desktop")
        chart = re.search(
            r"<svg class='throughput-chart'.*?</svg>", instruments, re.DOTALL,
        )
        self.assertIsNotNone(chart)
        self.assertEqual(
            chart.group(0).count("data-snapshot-collected="),
            2 * len(snapshot["performance"]["samples"]),
        )
        self.assertNotIn("data-at=", chart.group(0))
        throughput_controller = render.MOBILE_CONTROLLER.split(
            "const throughputCharts", 1,
        )[1].split("const paginationByRoot", 1)[0]
        self.assertIn(
            "snapshot collected ${point.dataset.snapshotCollected}",
            throughput_controller,
        )
        self.assertNotIn("point.dataset.at", throughput_controller)

    def test_overview_recorded_rpc_evidence_binds_health_and_fails_closed(self):
        history, _, indexes = self.observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        health_id = indexes["summary"][(
            "network_healthy", snapshot_at,
        )]["observation_id"]
        evidence = render.render_overview_evidence(history[-1], None, indexes)
        self.assertIn(
            f"<li data-observation-ids='{health_id}'>"
            "<span class='scale-dot'></span>"
            "<span class='confidence-range'>Recorded</span>"
            "<span class='confidence-level'>Measured RPC</span></li>",
            evidence,
        )

        broken = {name: dict(records) for name, records in indexes.items()}
        del broken["summary"][("network_healthy", snapshot_at)]
        with self.assertRaisesRegex(ValueError, "network_healthy"):
            render.render_overview_evidence(history[-1], None, broken)

    def test_mobile_rpc_and_release_rows_bind_complete_source_evidence(self):
        history, _, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        mobile = render.render_mobile_data(history[-1], None, None, indexes)

        rpc_metrics = (
            "network_healthy", "epoch", "network_block_time_unix",
            "total_supply_sol", "circulating_sol", "non_circulating_supply_sol",
            "active_count",
        )
        expected_rpc_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in rpc_metrics
        ]
        self.assertEqual(
            self.mobile_source_row_ids(mobile, "Public Solana JSON-RPC"),
            expected_rpc_ids,
        )

        expected_release_ids = [
            indexes["summary"][(
                "network_status_description", snapshot_at,
            )]["observation_id"],
            indexes["derived"][(
                "catalog_news_available_source_count", snapshot_at,
            )]["observation_id"],
            indexes["derived"][(
                "catalog_news_source_count", snapshot_at,
            )]["observation_id"],
        ]
        self.assertEqual(
            self.mobile_source_row_ids(mobile, "Official release feeds"),
            expected_release_ids,
        )
        release_at = mobile.index("<strong>Official release feeds</strong>")
        release_row = mobile[
            mobile.rfind("<article", 0, release_at):mobile.index("</article>", release_at)
        ]
        self.assertIn("<span class='mobile-source-status'>Partial</span>", release_row)

        for kind, key in (
            ("summary", ("network_block_time_unix", snapshot_at)),
            ("derived", ("catalog_news_available_source_count", snapshot_at)),
        ):
            broken = {name: dict(records) for name, records in indexes.items()}
            del broken[kind][key]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                render.render_mobile_data(history[-1], None, None, broken)

    def test_xstock_supply_bounds_bind_html_and_markdown_details(self):
        history, observations, indexes = self.catalog_observation_fixture()
        snapshot_at = history[-1]["collected_at"]
        bound_metrics = (
            "xstock_supply_coverage_numerator",
            "xstock_supply_coverage_denominator",
            "xstock_supply_oldest_observation_at",
            "xstock_supply_newest_observation_at",
        )
        bound_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in bound_metrics
        ]
        workbench = render.render_growth_workbench(
            history[-1], "desktop", indexes,
        )
        marker_at = workbench.index("<span>Finalized supply coverage</span>")
        row_at = workbench.rfind("<li", 0, marker_at)
        row_open = workbench[row_at:workbench.index(">", row_at) + 1]
        self.assertEqual(
            re.search(
                r" data-observation-ids='([^']+)'", row_open,
            ).group(1).split(),
            bound_ids,
        )

        markdown = render.render_markdown(
            history[-1], history=history, observations=observations,
        )
        bounds_line = next(
            line for line in markdown.splitlines()
            if line.startswith("- Supply observation bounds:")
        )
        self.assertTrue(bounds_line.endswith(
            " <!-- observation-ids:"
            + " ".join(bound_ids[-2:])
            + " -->"
        ))

    def test_current_validator_share_is_derived_from_exact_counts(self):
        def configure(snapshot):
            row = {
                "rank": 1, "identity": "node-a", "vote_account": "vote-a",
                "state": "current", "stake_sol": 100.0, "share_pct": 100.0,
                "commission": 5, "last_vote": 100, "root_slot": 99,
            }
            snapshot["validators"] = {
                "available": True, "active_count": 1, "delinquent_count": 0,
                "delinquent_pct": 0.0, "active_stake_sol": 100.0,
                "delinquent_stake_sol": 0.0, "nakamoto_coefficient": 1,
                "top_10_share_pct": 100.0, "accounts_with_stake": 1,
                "accounts_missing_stake": 0, "ranked_validator_count": 1,
                "all_validator_count": 1,
                "commission": {
                    "available": True, "median_pct": 5.0, "mean_pct": 5.0,
                    "zero_commission_count": 0, "max_commission_count": 0,
                },
                "ranked_validators": [row], "all_validators": [row],
            }

        history, _, indexes = self.observation_fixture(
            configure_latest=configure,
        )
        snapshot_at = history[-1]["collected_at"]
        self.assertNotIn(
            ("current_validator_share_pct", snapshot_at), indexes["summary"],
        )
        current_share = indexes["derived"][(
            "current_validator_share_pct", snapshot_at,
        )]
        count_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in ("active_count", "delinquent_validator_count")
        ]
        self.assertEqual(current_share["record_kind"], "derived")
        self.assertEqual(current_share["basis"], "derived")
        self.assertEqual(current_share["value"], 100.0)
        self.assertEqual(current_share["input_observation_ids"], count_ids)

        workbench = render.render_validator_workbench(
            history[-1], "mobile", indexes,
        )
        marker_at = workbench.index("data-validator-component='validator-participation'")
        card_at = workbench.rfind("<article", 0, marker_at)
        card_open = workbench[card_at:workbench.index(">", card_at) + 1]
        expected_ids = [
            indexes["summary"][(metric_id, snapshot_at)]["observation_id"]
            for metric_id in (
                "active_count", "delinquent_validator_count", "delinquent_pct",
            )
        ] + [current_share["observation_id"]]
        self.assertEqual(
            re.search(
                r" data-observation-ids='([^']+)'", card_open,
            ).group(1).split(),
            expected_ids,
        )

    def test_mobile_partial_anomaly_binds_coverage_and_fails_closed(self):
        def configure(history):
            self.configure_baseline_anomalies(history)
            for snapshot in history:
                snapshot["validators"] = {
                    "available": False, "reason": "focused partial coverage",
                }
            history[-1]["economics"]["price"] = {
                "available": False, "reason": "focused partial coverage",
            }

        history, _, analysis, _, indexes = self.anomaly_observation_fixture(
            configure,
        )
        self.assertEqual(analysis["status"], "partial_coverage")
        expected_ids = list(dict.fromkeys([
            *analysis["observation_ids"].values(),
            *(
                observation_id
                for coverage in analysis["coverage"].values()
                for observation_id in coverage["observation_ids"].values()
            ),
        ]))
        desktop = render.render_anomalies_html(analysis)
        markdown = "\n".join(render.render_anomalies_markdown(analysis))
        self.assertEqual(
            re.search(r"<h2 data-observation-ids='([^']+)'>", desktop).group(1).split(),
            expected_ids,
        )
        self.assertEqual(
            re.search(r"## Anomalies <!-- observation-ids:([^>]+) -->", markdown).group(1).split(),
            expected_ids,
        )
        mobile = render.render_mobile_data(
            history[-1], analysis, None, indexes,
        )
        self.assertEqual(
            self.mobile_source_row_ids(mobile, "Anomaly analysis"),
            expected_ids,
        )
        catalog = render.render_data_catalog(
            history[-1], analysis, None, history, indexes,
        )
        self.assertEqual(
            self.catalog_row_ids(catalog, "Anomaly analysis"),
            expected_ids,
        )
        markers = render.render_change_markers(history[-1], analysis, None)
        marker_at = markers.index("<h3 class='change-date'>Anomaly baseline</h3>")
        marker_open_at = markers.rfind("<li", 0, marker_at)
        marker_open = markers[marker_open_at:markers.index(">", marker_open_at) + 1]
        self.assertEqual(
            re.search(r" data-observation-ids='([^']+)'", marker_open).group(1).split(),
            expected_ids,
        )
        anomaly_at = mobile.index("<strong>Anomaly analysis</strong>")
        anomaly_row = mobile[
            mobile.rfind("<article", 0, anomaly_at):mobile.index("</article>", anomaly_at)
        ]
        self.assertIn(
            "<span class='mobile-source-status'>Partial coverage</span>",
            anomaly_row,
        )

        snapshot_at = history[-1]["collected_at"]
        broken = {name: dict(records) for name, records in indexes.items()}
        del broken["derived"][(
            "anomaly_coverage_sol_price_unavailable_count", snapshot_at,
        )]
        with self.assertRaises(ValueError):
            render.render_mobile_data(history[-1], analysis, None, broken)

    def test_no_change_delta_details_bind_steady_count(self):
        history, _, indexes = self.observation_fixture()
        comparison = render.bind_public_comparison(
            pipeline.recheck(history), indexes,
        )
        self.assertEqual(comparison["counts"]["changed"], 0)
        steady_id = comparison["observation_ids"]["steady_count"]

        html_output = render.render_delta_html(comparison)
        no_change = re.search(
            r"<div class='anomaly-note clear'([^>]*)>"
            r"<strong>No metric moved past its threshold</strong>",
            html_output,
        )
        self.assertIsNotNone(no_change)
        self.assertEqual(
            re.search(
                r" data-observation-ids='([^']+)'", no_change.group(1),
            ).group(1).split(),
            [steady_id],
        )

        markdown = render.render_delta_markdown(comparison)
        no_change_line = next(
            line for line in markdown
            if line.startswith("**No metric moved past its threshold**")
        )
        self.assertTrue(no_change_line.endswith(
            f" <!-- observation-ids:{steady_id} -->"
        ))

        broken = deepcopy(comparison)
        del broken["observation_ids"]["steady_count"]
        with self.assertRaises(ValueError):
            render.render_delta_html(broken)
        with self.assertRaises(ValueError):
            render.render_delta_markdown(broken)


class TestRenderStrictPublishPath(unittest.TestCase):
    """render.main fails closed before writing any output file."""

    REPO = Path(__file__).resolve().parent.parent

    def run_render(self, snapshot, out_dir, *extra, prepare=True):
        import subprocess
        if prepare:
            try:
                candidate = json.loads(Path(snapshot).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            else:
                raw = (json.dumps(candidate, indent=2, allow_nan=False) + "\n").encode()
                Path(snapshot).write_bytes(raw)
                collected_at = candidate.get("collected_at") \
                    if isinstance(candidate, dict) else None
                if Path(snapshot).name == "latest.json" and isinstance(collected_at, str):
                    immutable = Path(snapshot).parent / collect.snapshot_filename(collected_at)
                    if not immutable.exists():
                        immutable.write_bytes(raw)
        return subprocess.run(
            [sys.executable, str(self.REPO / "render.py"),
             "--snapshot", str(snapshot), "--out-dir", str(out_dir), *extra],
            capture_output=True, text=True,
        )

    @staticmethod
    def candidate(collected_at, schema_version=8):
        endpoint = "https://api.mainnet-beta.solana.com"
        return {
            "schema_version": schema_version,
            "collected_at": collected_at,
            "source": {
                "endpoint": endpoint,
                "endpoint_identity": "sha256:" + hashlib.sha256(endpoint.encode()).hexdigest(),
                "requires_api_key": False,
            },
            "provenance": {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            },
            "network": {"healthy": True, "health_raw": "ok", "slot": 500},
            "epoch": {"available": True, "epoch": 800, "progress_pct": 50.0},
            "performance": {
                "available": True,
                "samples_used": 1,
                "latest_tps": 3000.0,
                "mean_slot_time_secs": 0.4,
                "samples": [{
                    "slot": 500,
                    "tps": 3000.0,
                    "slot_time_secs": 0.4,
                    "sample_period_secs": 60,
                    "slots": 150,
                    "transactions": 180_000,
                    "non_vote_transactions": 120_000,
                }],
            },
            "supply": {"available": True, "circulating_sol": 480_000_000.0},
            "inflation": {"available": True},
            "validators": {"available": True},
            "economics": {"available": False},
            "activity": {"available": False},
            "news": {"available": False},
            "growth": {"available": False},
        }

    def test_invalid_input_writes_no_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale.json"
            stale.write_text(json.dumps(self.candidate("2026-01-01T00:00:00+00:00")))
            out_dir = Path(tmp) / "dist"
            done = self.run_render(stale, out_dir)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("publishable", done.stderr)
            self.assertFalse(out_dir.exists() or any(out_dir.iterdir())
                             if out_dir.exists() else False)

    def test_valid_input_writes_outputs_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            fresh.write_text(json.dumps(self.candidate(now)))
            out_dir = Path(tmp) / "dist"
            done = self.run_render(fresh, out_dir)
            self.assertEqual(done.returncode, 0, done.stderr)
            names = sorted(p.name for p in out_dir.iterdir())
            self.assertEqual(names, ["index.html", "report.json", "report.md"])
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            observations = report["observations"]
            latest = next(
                item for item in observations
                if item["record_kind"] == "direct"
                and item["metric_id"] == "latest_tps"
                and item["subject_id"] is None
                and item["snapshot_collected_at"] == report["collected_at"]
            )
            self.assertEqual(latest["value"], 3000.0)
            self.assertTrue(all(
                item["value"] is None
                for item in observations if item["status"] == "unavailable"
            ))
            report_ids = {item["observation_id"] for item in observations}
            html_page = (out_dir / "index.html").read_text(encoding="utf-8")
            markdown = (out_dir / "report.md").read_text(encoding="utf-8")
            self.assertEqual(
                html_page.count('http-equiv="Content-Security-Policy"'), 1,
            )
            self.assertIn(render.CONTENT_SECURITY_POLICY, html_page)
            for output in (html_page, markdown):
                emitted_ids = set(render.OBSERVATION_ID_PATTERN.findall(output))
                self.assertTrue(emitted_ids.issubset(report_ids), emitted_ids - report_ids)
                self.assertIn("3,000", output)
            self.assertRegex(
                html_page,
                rf"<li class='metric' data-observation-id='{re.escape(latest['observation_id'])}'>"
                r"<span class='metric__label'>Latest TPS</span>",
            )
            self.assertIn(
                f"| Latest TPS | 3,000 <!-- observation-ids:{latest['observation_id']} --> |",
                markdown,
            )

    def test_noncanonical_or_mismatched_latest_writes_no_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            candidate = self.candidate(now)
            canonical = (json.dumps(candidate, indent=2) + "\n").encode()
            latest = root / "latest.json"
            immutable = root / collect.snapshot_filename(now)
            immutable.write_bytes(canonical)
            out_dir = root / "dist"

            latest.write_bytes(b" " + canonical)
            done = self.run_render(latest, out_dir, prepare=False)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("not canonical collector JSON", done.stderr)
            self.assertFalse(out_dir.exists())

            latest.write_bytes(canonical.replace(b'"slot": 500', b'"slot": 501'))
            done = self.run_render(latest, out_dir, prepare=False)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("do not match the named immutable snapshot", done.stderr)
            self.assertFalse(out_dir.exists())

    def test_public_outputs_drop_unknown_top_level_snapshot_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snapshot = self.candidate(now, schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            snapshot["private_debug"] = "PRIVATE-PROJECTION-SENTINEL"
            fresh.write_text(json.dumps(snapshot))
            out_dir = Path(tmp) / "dist"

            done = self.run_render(fresh, out_dir)

            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertNotIn("private_debug", report)
            for name in ("index.html", "report.md", "report.json"):
                self.assertNotIn(
                    "PRIVATE-PROJECTION-SENTINEL",
                    (out_dir / name).read_text(encoding="utf-8"),
                )

    def test_public_outputs_drop_unknown_nested_objects_and_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snapshot = self.candidate(now, schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            sentinel = "PRIVATE-NESTED-PROJECTION-SENTINEL"
            snapshot["network"]["private_debug"] = {
                "rows": [{"secret": sentinel}],
            }
            snapshot["activity"]["private_rows"] = [{"secret": sentinel}]
            fresh.write_text(json.dumps(snapshot))
            out_dir = Path(tmp) / "dist"

            done = self.run_render(fresh, out_dir)

            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["network"]["healthy"])
            self.assertEqual(
                report["release"]["public_projection_version"],
                render.PUBLIC_PROJECTION_VERSION,
            )
            for name in ("index.html", "report.md", "report.json"):
                self.assertNotIn(sentinel, (out_dir / name).read_text(encoding="utf-8"))

    def test_xstock_mit_notice_travels_with_every_deployable_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snapshot = self.candidate(now, schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            fresh.write_text(json.dumps(snapshot))
            out_dir = Path(tmp) / "dist"

            done = self.run_render(fresh, out_dir, "--generated-at", now)

            self.assertEqual(done.returncode, 0, done.stderr)
            html_page = (out_dir / "index.html").read_text(encoding="utf-8")
            markdown = (out_dir / "report.md").read_text(encoding="utf-8")
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            notice = report["release"]["third_party_notices"][0]
            self.assertEqual(notice["text"], render.XSTOCK_REGISTRY_LICENSE_NOTICE)
            self.assertEqual(notice["license_url"], render.XSTOCK_REGISTRY_LICENSE_URL)
            self.assertIn(render.XSTOCK_REGISTRY_LICENSE_NOTICE, render.html.unescape(html_page))
            self.assertIn(render.XSTOCK_REGISTRY_LICENSE_NOTICE, markdown)
            for artifact in (html_page, markdown, json.dumps(report)):
                self.assertIn("Copyright (c) 2026 Solana Foundation", artifact)

    def test_public_outputs_disclose_legacy_history_without_losing_chart_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            current = self.candidate(now.isoformat(), schema_version=8)
            current["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            prior = self.candidate(
                (now - timedelta(hours=6)).isoformat(), schema_version=3,
            )
            prior["provenance"] = deepcopy(current["provenance"])
            prior["performance"] = {"available": False}
            prior.pop("provenance", None)
            prior.pop("inflation", None)
            prior.pop("growth", None)
            sentinel = "PRIVATE-PRIOR-HISTORY-SOURCE-SENTINEL"
            prior["source"]["private_debug"] = {
                "rows": [{"secret": sentinel}],
            }
            prior_path = root / "snapshot-prior.json"
            prior_path.write_text(json.dumps(prior))
            fresh = root / "latest.json"
            fresh.write_text(json.dumps(current))
            out_dir = root / "dist"

            done = self.run_render(
                fresh, out_dir, "--generated-at", now.isoformat(),
            )

            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["history"]["snapshots"], 2)
            self.assertIn(sentinel, prior_path.read_text(encoding="utf-8"))
            notice = "Raw history and chart calculations retain all 2 recorded snapshots"
            self.assertIn(notice, (out_dir / "index.html").read_text(encoding="utf-8"))
            self.assertIn(notice, (out_dir / "report.md").read_text(encoding="utf-8"))
            for name in ("index.html", "report.md", "report.json"):
                self.assertNotIn(sentinel, (out_dir / name).read_text(encoding="utf-8"))

    def test_wrong_prior_history_shapes_write_no_output_directory(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for field, replacement, message in (
            ("source", [], "source must be an object"),
            ("samples", {}, "performance.samples must be a list"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                current = self.candidate(now.isoformat(), schema_version=8)
                current["provenance"] = {
                    "source_revision": "a" * 40,
                    "source_tree_dirty": False,
                }
                prior = self.candidate(
                    (now - timedelta(hours=6)).isoformat(), schema_version=8,
                )
                prior["provenance"] = deepcopy(current["provenance"])
                if field == "source":
                    prior["source"] = replacement
                else:
                    prior["performance"][field] = replacement
                (root / "snapshot-prior.json").write_text(json.dumps(prior))
                fresh = root / "latest.json"
                fresh.write_text(json.dumps(current))
                out_dir = root / "dist"

                done = self.run_render(
                    fresh, out_dir, "--generated-at", now.isoformat(),
                )

                self.assertNotEqual(done.returncode, 0)
                self.assertIn(f"public projection failed: {message}", done.stderr)
                self.assertFalse(out_dir.exists())

    def test_projection_shape_and_schema_errors_write_no_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "wrong-shape.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snapshot = self.candidate(now, schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            snapshot["activity"]["rev"] = []
            fresh.write_text(json.dumps(snapshot))
            out_dir = Path(tmp) / "shape-dist"

            done = self.run_render(fresh, out_dir)

            self.assertNotEqual(done.returncode, 0)
            self.assertIn("public projection failed", done.stderr)
            self.assertFalse(out_dir.exists())

            snapshot = self.candidate(now, schema_version=4)
            fresh.write_text(json.dumps(snapshot))
            replay_out = Path(tmp) / "schema-dist"
            done = self.run_render(fresh, replay_out, "--replay")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("unsupported schema_version", done.stderr)
            self.assertFalse(replay_out.exists())

    def test_explicit_generated_at_reproduces_old_snapshot_byte_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            snapshot = self.candidate("2025-01-01T00:00:00+00:00", schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            fresh.write_text(json.dumps(snapshot))
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            timestamp = "2025-01-01T00:05:00+00:00"

            one = self.run_render(fresh, first, "--generated-at", timestamp)
            two = self.run_render(fresh, second, "--generated-at", timestamp)

            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertEqual(two.returncode, 0, two.stderr)
            for name in ("index.html", "report.md", "report.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_generated_at_requires_an_aware_iso_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "latest.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            fresh.write_text(json.dumps(self.candidate(now)))
            out_dir = Path(tmp) / "dist"

            done = self.run_render(
                fresh, out_dir, "--generated-at", "2026-08-30T20:00:00",
            )

            self.assertNotEqual(done.returncode, 0)
            self.assertIn("must include a UTC offset", done.stderr)
            self.assertFalse(out_dir.exists())

    def test_release_metadata_uses_raw_snapshot_hash_in_every_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "candidate.json"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snapshot = self.candidate(now, schema_version=8)
            snapshot["provenance"] = {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            }
            raw = (json.dumps(snapshot, indent=2) + "\n").encode("utf-8")
            fresh.write_bytes(raw)
            out_dir = Path(tmp) / "dist"

            done = self.run_render(fresh, out_dir)

            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            release = report["release"]
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(release["release_id"], digest)
            self.assertEqual(release["selected_snapshot"], {
                "path": fresh.name,
                "sha256": digest,
            })
            self.assertEqual(release["collector"], {
                "source_revision": "a" * 40,
                "source_tree_dirty": False,
            })
            self.assertRegex(release["renderer"]["source_revision"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(release["renderer"]["source_tree_dirty"], bool)
            self.assertEqual(release["schema_version"], 8)
            datetime.fromisoformat(release["generated_at"])
            markdown = (out_dir / "report.md").read_text(encoding="utf-8")
            page = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn(fresh.name, markdown)
            self.assertIn(fresh.name, page)
            self.assertNotIn(str(fresh.parent.resolve()), markdown)
            self.assertNotIn(str(fresh.parent.resolve()), page)
            for value in (digest, "a" * 40, release["renderer"]["source_revision"],
                          release["generated_at"]):
                self.assertIn(value, markdown)
                self.assertIn(value, page)


class TestReleaseMetadata(unittest.TestCase):
    def test_legacy_sample_values_without_retained_denominators_render_as_gaps(self):
        snapshot = load_fixture()
        snapshot["schema_version"] = 3
        snapshot["performance"]["samples"] = [
            {"slot": 10, "tps": 123.4, "slot_time_secs": 0.4},
        ]

        self.assertEqual(render.performance_points(snapshot), [123.4])
        self.assertEqual(
            render.performance_points(snapshot, render.public_observation_indexes([])),
            [None],
        )
        self.assertEqual(render.performance_observation_ids(snapshot, None), [None])

    def test_public_history_projection_omits_only_documented_legacy_schemas(self):
        current = {"schema_version": 8, "network": {"healthy": True}}
        legacy = {"schema_version": 3, "private_debug": "secret"}
        source = deepcopy([legacy, current])

        omissions = render.public_history_omissions(source)

        self.assertEqual(omissions, {3: 1})
        self.assertEqual(source, [legacy, current])
        self.assertIn(
            "Raw history and chart calculations retain all 2 recorded snapshots",
            render.public_history_omission_notice(omissions, 2),
        )
        with self.assertRaisesRegex(ValueError, "unsupported schema_version"):
            render.public_history_omissions([{"schema_version": 10}])
        with self.assertRaisesRegex(ValueError, "network must be an object"):
            render.public_history_omissions([
                {"schema_version": 8, "network": []},
            ])

    def test_recursive_projection_preserves_schema_7_simd_commit_records(self):
        raw = {
            "schema_version": 7,
            "news": {"sources": {"simd_proposals": {"items": [{
                "title": "Recorded commit", "link": "https://example.test/commit",
                "published": "2026-07-31T16:27:22Z", "author": "maintainer",
            }]}}},
        }

        projected = render.project_public_envelope(raw)

        self.assertEqual(
            projected["news"]["sources"]["simd_proposals"]["items"][0],
            raw["news"]["sources"]["simd_proposals"]["items"][0],
        )

    def test_activity_degraded_reason_is_public_evidence(self):
        """A degraded activity section keeps its explanation when published.

        When block sampling fails, collect emits an unavailable activity
        shape with a reason string. The reason is evidence about why the
        section is unavailable and must survive the public projection
        instead of being reported as an unknown field.
        """
        raw = {
            "schema_version": 9,
            "activity": {
                "available": False,
                "reason": "no blocks could be sampled from the endpoint",
            },
        }

        projected = render.project_public_envelope(raw)["activity"]

        self.assertEqual(projected, raw["activity"])

    def test_recursive_envelope_projection_preserves_known_records_without_mutating_input(self):
        sentinel = "PRIVATE-ENVELOPE-SENTINEL"
        raw = {
            "schema_version": 9,
            "network": {"healthy": True, "private_debug": {"value": sentinel}},
            "news": {
                "available": True,
                "partial": True,
                "requires_api_key": False,
                "featured_item_id": "story:1",
                "items": [{
                    "id": "story:1", "source_id": "network_status",
                    "publisher": "Solana Status", "category": "network",
                    "title": "Recorded status", "canonical_url": "https://status.solana.com/",
                    "published_at": None, "recorded_at": "2026-08-30T20:00:00+00:00",
                    "state": "recorded", "editorial_note": "Recorded.",
                    "art_seed": "story:1", "private_debug": sentinel,
                }],
                "private_debug": sentinel,
            },
            "release": {"release_id": "release", "private_debug": sentinel},
            "anomalies": {
                "status": "ok",
                "findings": [{
                    "severity": "info", "code": "test", "title": "Test",
                    "detail": "Known", "observed": 1, "baseline": 1,
                    "private_debug": sentinel,
                }],
                "private_debug": sentinel,
            },
            "delta": {
                "status": "ok",
                "changes": [{
                    "key": "latest_tps", "label": "Latest TPS", "unit": " TPS",
                    "basis": "measured", "previous": 1, "current": 2,
                    "change": 1, "change_pct": 100.0, "direction": "up",
                    "identifier": False, "why_it_matters": "Known",
                    "what_to_verify": "Known", "private_debug": sentinel,
                }],
                "private_debug": sentinel,
            },
            "history": {
                "snapshots": 1,
                "series": {
                    "latest_tps": {
                        "label": "Transactions per second", "unit": "TPS",
                        "basis": "measured", "charted": False,
                        "points": [{
                            "collected_at": "2026-08-30T20:00:00+00:00",
                            "value": 1, "private_debug": sentinel,
                        }],
                        "private_debug": sentinel,
                    },
                    "private_series": {"value": sentinel},
                },
                "private_debug": sentinel,
            },
            "upgrades": {"available": False, "reason": "held", "private_debug": sentinel},
            "observations": [{
                "metric_id": "network_tps", "subject_id": None, "name": "TPS",
                "type": "gauge", "value": 1, "unit": "TPS",
                "population": "RPC sample", "denominator": None, "window": "60s",
                "observed_at": "2026-08-30T20:00:00+00:00", "observed_slot": 301_234_567,
                "collected_at": "2026-08-30T20:00:00+00:00",
                "source": "getRecentPerformanceSamples", "source_url": "https://example.test/rpc",
                "source_path": "performance.latest_tps",
                "collection_method": "RPC", "calculation_method": "latest sample",
                "freshness": "current", "status": "available", "quality": "measured",
                "caveat": None, "output_path": "performance.latest_tps",
                "private_debug": sentinel,
            }],
            "private_debug": sentinel,
        }
        before = deepcopy(raw)

        projected = render.project_public_envelope(raw)

        self.assertEqual(raw, before)
        self.assertNotIn(sentinel, json.dumps(projected))
        self.assertEqual(projected["news"]["items"][0]["title"], "Recorded status")
        self.assertEqual(projected["history"]["series"]["latest_tps"]["points"][0]["value"], 1)
        self.assertEqual(projected["observations"][0]["metric_id"], "network_tps")
        self.assertEqual(projected["observations"][0]["observed_slot"], 301_234_567)
        self.assertEqual(
            projected["observations"][0]["source_url"], "https://example.test/rpc",
        )

    def test_recursive_envelope_projection_rejects_wrong_list_and_item_shapes(self):
        with self.assertRaisesRegex(ValueError, "report must be an object"):
            render.project_public_envelope([])
        with self.assertRaisesRegex(ValueError, "unsupported schema_version 8.0"):
            render.project_public_envelope({"schema_version": 8.0})
        with self.assertRaisesRegex(ValueError, "observations must be a list"):
            render.project_public_envelope({"schema_version": 9, "observations": {}})
        with self.assertRaisesRegex(ValueError, r"observations\[\] must be an object"):
            render.project_public_envelope({"schema_version": 9, "observations": ["bad"]})
        with self.assertRaisesRegex(ValueError, "network must be an object"):
            render.project_public_envelope({"schema_version": 9, "network": None})

    def test_recursive_projection_preserves_only_strict_legacy_xstock_provenance(self):
        provenance = {
            "source_method": "getAccountInfo(finalized,jsonParsed)",
            "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "program": "spl-token",
            "rpc_context_slot": 300,
            "rpc_api_version": "2.3.6",
            "private_debug": "PRIVATE-XSTOCK-ACCOUNT-SENTINEL",
        }
        asset = {
            "name": "SUI xStock",
            "slug": "xstock-sui",
            "mint": "suifhC9gU1VbJAPYPTBkHJyyyStKGLLYPVDTmPoqbvA",
            "supply": 586_513.99067914,
            "supply_account_provenance": provenance,
        }
        raw = {
            "schema_version": 9,
            "growth": {"tokenized_equities": {
                "assets": [deepcopy(asset)],
                "all_assets": [deepcopy(asset)],
            }},
        }

        projected = render.project_public_envelope(raw)

        expected = {
            "source_method": "getAccountInfo(finalized,jsonParsed)",
            "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "program": "spl-token",
            "rpc_context_slot": 300,
            "rpc_api_version": "2.3.6",
        }
        equities = projected["growth"]["tokenized_equities"]
        self.assertEqual(
            equities["assets"][0]["supply_account_provenance"], expected,
        )
        self.assertEqual(
            equities["all_assets"][0]["supply_account_provenance"], expected,
        )
        self.assertNotIn("PRIVATE-XSTOCK-ACCOUNT-SENTINEL", json.dumps(projected))

        malformed = deepcopy(raw)
        malformed["growth"]["tokenized_equities"]["all_assets"][0][
            "supply_account_provenance"
        ] = "wrong-shape"
        with self.assertRaisesRegex(
            ValueError,
            r"growth\.tokenized_equities\.all_assets\[\]\.supply_account_provenance "
            r"must be an object",
        ):
            render.project_public_envelope(malformed)

    def test_recursive_projection_preserves_dex_publication_hold(self):
        raw = {"schema_version": 9, "growth": {
            "tokenized_equities": {"volume": {
                "available": False, "reason": "Redistribution rights unresolved.",
            }},
            "sources": {"dex_volume": {
                "available": False, "partial": True, "held": True,
                "reason": "Redistribution rights unresolved.",
            }},
        }}

        projected = render.project_public_envelope(raw)

        self.assertTrue(projected["growth"]["sources"]["dex_volume"]["held"])
        self.assertEqual(
            projected["growth"]["tokenized_equities"]["volume"]["reason"],
            "Redistribution rights unresolved.",
        )

    def test_contract_is_deterministic_and_does_not_hash_its_own_output(self):
        raw = b'{"schema_version":8}'
        path = Path(render.__file__).resolve().parent / "snapshots" / "latest.json"
        snapshot = {
            "schema_version": 8,
            "collected_at": "2026-08-25T20:00:01+00:00",
            "provenance": {
                "source_revision": "b" * 40,
                "source_tree_dirty": True,
            },
        }

        release = render.build_release_metadata(
            path,
            raw,
            snapshot,
            generated_at="2026-08-25T23:59:00+00:00",
            renderer_state={"source_revision": "c" * 40, "source_tree_dirty": False},
            history=[
                {"collected_at": "2026-08-25T00:00:00+00:00"},
                {"collected_at": "2026-08-25T06:00:00+00:00"},
                {"collected_at": "2026-08-25T13:00:00+00:00"},
                snapshot,
            ],
        )

        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(release, {
            "release_id": digest,
            "selected_snapshot": {"path": "snapshots/latest.json", "sha256": digest},
            "collector": {"source_revision": "b" * 40, "source_tree_dirty": True},
            "renderer": {"source_revision": "c" * 40, "source_tree_dirty": False},
            "schema_version": 8,
            "generated_at": "2026-08-25T23:59:00+00:00",
            "public_projection_version": render.PUBLIC_PROJECTION_VERSION,
            "third_party_notices": [{
                "id": "solana-foundation-tokens-registry-mit",
                "title": "Solana Foundation Tokens registry",
                "source": "solana-foundation/tokens",
                "source_revision": "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0",
                "license": "MIT",
                "license_url": render.XSTOCK_REGISTRY_LICENSE_URL,
                "text": render.XSTOCK_REGISTRY_LICENSE_NOTICE,
            }],
            "update_status": {
                "as_of": "2026-08-25T23:59:00+00:00",
                "latest_successful_collection_at": "2026-08-25T20:00:01+00:00",
                "included_collection_attempt_at": "2026-08-25T20:00:01+00:00",
                "included_collection_attempt_outcome": "success",
                "attempt_scope": (
                    "attempt represented by this immutable artifact; later attempts "
                    "are recorded in GitHub Actions"
                ),
                "age_seconds_at_generation": 14339,
                "cadence_seconds": 3600,
                "freshness_limit_seconds": 25200,
                "next_scheduled_trigger_at": "2026-08-26T00:17:00+00:00",
                "schedule_note": (
                    "next configured GitHub Actions trigger; scheduled runs may be "
                    "delayed or dropped"
                ),
                "overdue_at_generation": False,
                "history": {
                    "snapshot_count": 4,
                    "valid_timestamp_count": 4,
                    "invalid_timestamp_count": 0,
                    "interval_count": 3,
                    "median_interval_seconds": 25200,
                    "gap_threshold_seconds": 25200,
                    "gap_count": 1,
                    "largest_gap_seconds": 25201,
                    "gaps": [{
                        "from": "2026-08-25T13:00:00+00:00",
                        "to": "2026-08-25T20:00:01+00:00",
                        "duration_seconds": 25201,
                    }],
                },
            },
        })

        fields = dict(render.release_metadata_fields({"release": release}))
        self.assertEqual(fields["Latest successful collection"],
                         "2026-08-25T20:00:01+00:00")
        self.assertEqual(fields["Included collection attempt"],
                         "2026-08-25T20:00:01+00:00")
        self.assertEqual(fields["Age at generation"], "14339 seconds")
        self.assertEqual(fields["Historical cadence gaps"], "1 over 25200 seconds")

    def test_update_status_keeps_freshness_boundary_and_invalid_history_explicit(self):
        snapshot = {"collected_at": "2026-08-25T07:00:00+00:00"}
        history = [
            {"collected_at": "2026-08-25T00:00:00+00:00"},
            {"collected_at": "not-a-time"},
            snapshot,
        ]
        boundary = render.build_update_status(
            snapshot, history, "2026-08-25T14:00:00+00:00")
        overdue = render.build_update_status(
            snapshot, history, "2026-08-25T14:00:01+00:00")

        self.assertEqual(boundary["age_seconds_at_generation"], 25200)
        self.assertFalse(boundary["overdue_at_generation"])
        self.assertTrue(overdue["overdue_at_generation"])
        self.assertEqual(boundary["history"]["invalid_timestamp_count"], 1)
        self.assertEqual(boundary["history"]["gap_count"], 0)
        self.assertEqual(boundary["history"]["largest_gap_seconds"], 25200)
        # Hourly cadence since 2026-09-03: next :17 trigger after 14:00.
        self.assertEqual(boundary["next_scheduled_trigger_at"],
                         "2026-08-25T14:17:00+00:00")


class TestPythonCompatibility(unittest.TestCase):
    def test_render_parses_with_python_3_10(self):
        import shutil
        import subprocess

        python = shutil.which("python3.10")
        if python is None:
            self.skipTest("python3.10 is not installed")
        source = str(Path(render.__file__).resolve())
        done = subprocess.run(
            [python, "-B", "-c",
             "from pathlib import Path; "
             f"compile(Path({source!r}).read_text(), {source!r}, 'exec')"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)


class TestSeptemberRendererRecovery(unittest.TestCase):
    def test_provider_detail_uses_the_same_complete_date_as_its_headline(self):
        source = {
            'available': True,
            'date': '2026-09-02',
            'provider_count': 2,
            'minimum': 100,
            'maximum': 200,
            'provider_observations': [
                {'date': '2026-09-02', 'provider': 'Allium', 'value': 100},
                {'date': '2026-09-02', 'provider': 'Dune', 'value': 200},
                {'date': '2026-09-03', 'provider': 'Allium', 'value': 900},
            ],
        }

        markup = render._pulse_provider_range_card(
            'Daily active addresses (provider range)', source, (), lambda *ids: '',
        )

        self.assertIn('Per-provider values · 2026-09-02', markup)
        self.assertIn('<span>Allium</span><b>100</b>', markup)
        self.assertIn('<span>Dune</span><b>200</b>', markup)
        self.assertNotIn('2026-09-03', markup)
        self.assertNotIn('<b>900</b>', markup)

    def test_provider_comparison_shows_source_native_overlap_and_missing_dates(self):
        snapshot = load_fixture()
        snapshot['schema_version'] = 9
        snapshot['growth'] = {'available': True, 'daily_active_addresses': {
            'available': True,
            'history_available': True,
            'date': '2026-09-03',
            'provider_count': 2,
            'minimum': 300,
            'maximum': 330,
            'oldest_date': '2026-09-01',
            'newest_date': '2026-09-03',
            'provider_observations': [
                {'date': '2026-09-01', 'provider': 'Allium', 'value': 100},
                {'date': '2026-09-02', 'provider': 'Allium', 'value': 200},
                {'date': '2026-09-03', 'provider': 'Allium', 'value': 300},
                {'date': '2026-09-01', 'provider': 'Dune', 'value': 110},
                {'date': '2026-09-03', 'provider': 'Dune', 'value': 330},
            ],
            **render._PROVIDER_BENCHMARK_CONTRACTS['daily_active_addresses'],
        }}

        markup = render.render_provider_comparison_chart(snapshot, 'desktop')

        self.assertIn("id='desktop-provider-comparison'", markup)
        self.assertIn('Stablecoin activity across providers', markup)
        self.assertIn('2 providers · 3 calendar days', markup)
        self.assertEqual(markup.count('data-provider-toggle'), 2)
        self.assertEqual(markup.count('data-provider-series='), 2)
        self.assertEqual(markup.count("data-provider-date-index='1'"), 1)
        dune_series = re.search(
            r"<g class='provider-series' data-provider-series='provider-1'.*?</g>",
            markup,
        ).group(0)
        self.assertNotIn('<path', dune_series)
        self.assertEqual(dune_series.count('data-provider-singleton'), 2)
        self.assertIn('.provider-point[data-provider-singleton] { opacity: .72; }', render.CSS)
        self.assertLess(
            render.CSS.index('.provider-point[data-provider-singleton]'),
            render.CSS.index('.provider-point[data-provider-active]'),
        )
        self.assertIn('Missing dates break lines', markup)
        self.assertIn('No average or network-wide total is asserted', markup)
        self.assertIn('Arrow keys move by date', markup)
        self.assertIn("data-provider-chart-help hidden", markup)
        self.assertIn("help?.removeAttribute('hidden');", render.MOBILE_CONTROLLER)
        self.assertIn('.provider-chart-help[hidden] { display: none; }', render.CSS)
        self.assertNotIn('consensus', markup.lower())

        desktop = render.render_growth_workbench(snapshot, 'desktop')
        mobile = render.render_growth_workbench(snapshot, 'mobile')
        self.assertIn("id='desktop-provider-comparison'", desktop)
        self.assertIn("id='mobile-provider-comparison'", mobile)

    def test_scoped_address_range_matches_both_layouts_and_observations(self):
        def configure(snapshot):
            snapshot['growth'] = {'available': True, 'daily_active_addresses': {
                'available': True, 'date': '2026-08-05', 'provider_count': 2,
                'minimum': 452031, 'maximum': 877460,
                **render._PROVIDER_BENCHMARK_CONTRACTS['daily_active_addresses'],
            }}
        history, observations, indexes = TestPublicObservationBindings.observation_fixture(
            configure_latest=configure)
        page = render.render_html(history[-1], history=history, observations=observations)
        expected_ids = [indexes['summary'][(metric, history[-1]['collected_at'])]['observation_id']
                        for metric in ('stablecoin_active_address_provider_range_min',
                                       'stablecoin_active_address_provider_range_max',
                                       'stablecoin_active_address_provider_count',
                                       'stablecoin_active_address_provider_date')]
        for opening, label in (("<li class='metric'", "<span class='metric__label'>"),
                               ("<div class='mobile-context-item'", '<span>')):
            match = re.search(re.escape(opening) + r"([^>]*)>" + re.escape(label)
                              + 'Stablecoin active addresses', page)
            self.assertIsNotNone(match)
            self.assertIn(' '.join(expected_ids), match.group(1))
        self.assertEqual(page.count('daily stablecoin activity, not network-wide DAA'), 2)
        self.assertIn('<span>452,031</span><span>–877,460</span>', page)
        self.assertIn('Network-wide daily active addresses', page)
        invalid = deepcopy(history[-1])
        invalid['growth']['daily_active_addresses']['scope'] = 'network-wide'
        self.assertNotIn('452,031', render._header_daa_value(invalid))

    def test_block_collection_and_block_evidence_ages_are_distinct(self):
        snapshot = TestCarriedForwardValueLabels.snapshot_with_stale_sources()
        snapshot['collected_at'] = '2026-09-04T16:22:59Z'
        snapshot['activity']['last_success_at'] = '2026-09-04T12:10:44Z'
        snapshot['activity']['window'] = {'last_block_time': int(datetime(
            2026, 9, 3, 23, 32, 15, tzinfo=timezone.utc).timestamp())}
        label = render.activity_evidence_label(snapshot)
        self.assertIn('last-known-good', label)
        self.assertIn('sample collected Sep 04, 2026 · 12:10 UTC', label)
        self.assertIn('last block Sep 03, 2026 · 23:32 UTC', label)
        self.assertIn('old', label)
        page = render.render_html(snapshot)
        self.assertEqual(page.count(render.html.escape(label)), 2)

    def test_ticker_exposes_one_metric_set_to_assistive_technology(self):
        from html.parser import HTMLParser
        class Reader(HTMLParser):
            def __init__(self):
                super().__init__()
                self.hidden = []
                self.labels = []
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag in ('ul', 'li'):
                    self.hidden.append(values.get('aria-hidden') == 'true')
            def handle_endtag(self, tag):
                if tag in ('ul', 'li'):
                    self.hidden.pop()
            def handle_data(self, value):
                if not any(self.hidden) and value == 'Latest TPS':
                    self.labels.append(value)
        ticker = render.render_ticker(load_fixture(), None)
        reader = Reader()
        reader.feed(ticker)
        self.assertEqual(len(reader.labels), 1)
        self.assertEqual(ticker.count('Latest TPS'), 6)

    def test_release_art_is_exact_tag_pinned_and_embedded_only_when_displayed(self):
        snapshot = editorial_fixture()
        snapshot['news']['featured_item_id'] = 'github-release:1'
        for tag, (path, digest) in render.RELEASE_ART_ASSETS.items():
            with self.subTest(tag=tag):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                snapshot['news']['items'][1]['title'] = 'Release ' + tag
                self.assertEqual(render.used_release_art_tags(snapshot), [tag])
                page = render.render_html(snapshot)
                self.assertEqual(page.count('background-image:url("data:image/png;base64,'), 3)
                for context in ('desktop', 'mobile'):
                    self.assertIn(f"data-release-art='{tag}'", render.render_project_editorial(snapshot, context))
                self.assertIn(f"data-release-art='{tag}'", render.render_community_news(snapshot, 'desktop'))
        for tag in ('v4.3.0-rc.0', 'v4.3.0-beta.10', 'v4.3.0-beta.2-extra'):
            snapshot['news']['items'][1]['title'] = 'Release ' + tag
            self.assertEqual(render.used_release_art_tags(snapshot), [])
            self.assertNotIn('data-release-art=', render.render_project_editorial(snapshot, 'desktop'))
            self.assertEqual(render.editorial_art_css(snapshot).count('data:image/png'), 0)

    def test_dune_day_label_is_based_on_recorded_execution_time(self):
        snapshot = load_fixture()
        snapshot['dune'] = {
            'available': True, 'query_id': 123,
            'execution_ended_at': '2026-09-03T23:30:00Z',
            'aggregates': {'dex_volume_total_latest_usd': 123.0},
        }
        for day, expected in (
            ('2026-09-02', 'completed UTC day'),
            ('2026-09-03', 'partial UTC day at execution'),
            ('2026-09-04', 'UTC day completeness unavailable'),
            (None, 'UTC day completeness unavailable'),
        ):
            snapshot['dune']['aggregates']['dex_volume_total_day'] = day
            pulse = render.render_ecosystem_pulse(snapshot)
            self.assertIn(expected, pulse)
            self.assertNotIn('~1 day source lag', pulse)

    def test_dune_catalog_distinguishes_daily_values_from_cached_metadata(self):
        snapshot = load_fixture()
        snapshot['dune'] = {
            'available': True,
            'query_id': '8590950',
            'execution_ended_at': '2026-09-03T01:19:20Z',
            'aggregates': {'dex_volume_total_latest_usd': 0.0},
        }
        self.assertEqual(render.dune_catalog_source(snapshot)[3], 'Recorded')

        snapshot['dune'] = {
            'available': False,
            'query_id': '8590950',
            'reason': 'current query failed',
            'last_known_good': {
                'execution_ended_at': '2026-09-03T01:19:20Z',
                'aggregates': {'xstocks_registry': {'A': 'B'}},
            },
        }
        source = render.dune_catalog_source(snapshot)
        self.assertEqual(source[3], 'Unavailable')
        self.assertIn('execution metadata only', source[1])

        snapshot['dune']['last_known_good']['aggregates'][
            'transaction_fees_latest_sol'
        ] = 0.0
        self.assertEqual(
            render.dune_catalog_source(snapshot)[3],
            'Stale · last-known-good',
        )

    def test_dune_xstock_and_transaction_fee_aggregates_project_recursively(self):
        snapshot = load_fixture()
        snapshot['schema_version'] = 9
        snapshot['dune'] = {
            'available': True, 'aggregation_contract': 'completed-utc-days-v1',
            'aggregates': {
                'xstocks_dex_volume_latest_usd': 2500.5,
                'xstocks_dex_trade_legs': 5.0,
                'xstocks_dex_priced_trade_legs': 5.0,
                'xstocks_dex_day': '2026-09-01',
                'xstocks_dex_volume_available': True,
                'xstocks_dex_volume_reason': None,
                'xstocks_registry': dict(render.dune_module.XSTOCK_REGISTRY),
                'xstocks_basis': 'covered xStocks DEX trade-leg volume; OR-matched rows counted once',
                'transaction_fees_latest_sol': 500.25,
                'transaction_fees_day': '2026-09-01',
                'transaction_fees_basis': 'all transaction fees in gas_solana.fees; not protocol REV or Jito tips',
            },
        }
        projected = render.project_public_envelope(snapshot)['dune']['aggregates']
        self.assertEqual(projected, snapshot['dune']['aggregates'])
        snapshot['dune']['aggregates']['xstocks_registry']['secret_marker'] = 'blocked'
        self.assertNotIn('secret_marker', json.dumps(render.project_public_envelope(snapshot)))

    def test_dune_xstock_and_transaction_fee_cards_keep_scope_and_coverage(self):
        snapshot = load_fixture()
        aggregates = {
            'dex_volume_total_latest_usd': 5000.0, 'dex_volume_total_day': '2026-09-01',
            'xstocks_dex_volume_latest_usd': 2500.5, 'xstocks_dex_trade_legs': 5.0,
            'xstocks_dex_priced_trade_legs': 5.0, 'xstocks_dex_day': '2026-09-01',
            'xstocks_dex_volume_available': True, 'xstocks_dex_volume_reason': None,
            'xstocks_basis': 'covered xStocks DEX trade-leg volume; OR-matched rows counted once',
            'transaction_fees_latest_sol': 500.25, 'transaction_fees_day': '2026-09-01',
            'transaction_fees_basis': 'all transaction fees in gas_solana.fees; not protocol REV or Jito tips',
        }
        snapshot['dune'] = {
            'available': True, 'query_id': 123, 'execution_ended_at': '2026-09-02T01:00:00Z',
            'aggregation_contract': 'completed-utc-days-v1', 'aggregates': aggregates,
        }
        indexes = render.public_observation_indexes(
            render.facts_module.public_observation_records(snapshot))
        pulse = render.render_ecosystem_pulse(snapshot, indexes)
        self.assertIn('Covered xStocks DEX trade-leg volume', pulse)
        self.assertIn('$2,500.50', pulse)
        self.assertIn('5 of 5 scoped trade legs priced', pulse)
        self.assertIn('not all equity or unique-user volume', pulse)
        self.assertIn('Daily all-transaction fees', pulse)
        self.assertIn('500.25 SOL', pulse)
        self.assertIn('not protocol REV or Jito tips', pulse)
        self.assertGreaterEqual(pulse.count('completed UTC day'), 3)
        for metric_id in (
            'dune_daily_dex_volume_usd', 'dune_daily_xstocks_dex_volume_usd',
            'dune_daily_xstocks_dex_trade_legs',
            'dune_daily_xstocks_dex_priced_trade_legs',
            'dune_daily_transaction_fees_sol',
        ):
            self.assertIn(
                indexes['summary'][(metric_id, snapshot['collected_at'])]['observation_id'],
                pulse,
            )
        aggregates['xstocks_dex_priced_trade_legs'] = 4.0
        pulse = render.render_ecosystem_pulse(snapshot)
        card = pulse.split('Covered xStocks DEX trade-leg volume', 1)[1].split('</div>', 3)
        self.assertIn('Unavailable', ''.join(card))
        self.assertIn('pricing covers 4 of 5 scoped trade legs', pulse)

    def test_dune_nested_lkg_aggregates_project_and_render_as_stale(self):
        snapshot = load_fixture()
        snapshot['schema_version'] = 9
        aggregates = {
            'xstocks_dex_volume_latest_usd': 100.0, 'xstocks_dex_trade_legs': 2.0,
            'xstocks_dex_priced_trade_legs': 2.0, 'xstocks_dex_day': '2026-09-01',
            'xstocks_dex_volume_available': True, 'xstocks_dex_volume_reason': None,
            'xstocks_registry': dict(render.dune_module.XSTOCK_REGISTRY),
            'transaction_fees_latest_sol': 20.0, 'transaction_fees_day': '2026-09-01',
        }
        snapshot['dune'] = {'available': False, 'reason': 'current query failed',
                            'last_known_good': {'query_id': '123',
                                'execution_ended_at': '2026-09-02T01:00:00Z',
                                'aggregation_contract': 'completed-utc-days-v1',
                                'aggregates': aggregates}}
        projected = render.project_public_envelope(snapshot)
        self.assertEqual(projected['dune']['last_known_good']['aggregates'], aggregates)
        pulse = render.render_ecosystem_pulse(projected)
        self.assertIn('$100', pulse)
        self.assertIn('last-known-good · current query unavailable', pulse)

    def test_sparse_chart_discloses_endpoints_and_sample_age(self):
        history = recorded_history_fixture(2)
        chart = render.render_overview_charts(history)
        self.assertIn('sparse recorded history', chart)
        self.assertIn('first → last · 2 observations', chart)
        self.assertIn('sample collected', chart)
        self.assertIn('last block', chart)
        self.assertIn('block evidence', chart)

    def test_editorial_titles_decode_entities_and_bound_at_word_boundaries(self):
        self.assertEqual(render.editorial_title('One &amp; two'), 'One & two')
        self.assertEqual(render.editorial_title('Ship 🤝 now 🤯'), 'Ship now')
        title = render.editorial_title('word ' * 80)
        self.assertLessEqual(len(title), 160)
        self.assertTrue(title.endswith('word…'))

    def test_x_archive_is_unavailable_labeled_and_not_promoted_to_briefing(self):
        snapshot = editorial_fixture()
        snapshot['schema_version'] = 9
        archive = {'observed_at': '2026-08-04T12:00:00Z',
                   'latest_published': '2026-08-03T12:00:00Z',
                   'items': [{'id': '123', 'title': 'Archived announcement',
                              'published': '2026-08-03T12:00:00Z',
                              'link': 'https://x.com/solana/status/123'}]}
        snapshot['news']['sources'] = {'x_announcements': {
            'available': False, 'items': [], 'last_known_good': archive}}
        projected = render.project_public_envelope(snapshot)
        self.assertEqual(projected['news']['sources']['x_announcements']['last_known_good'], archive)
        for context in ('desktop', 'mobile'):
            stream = render.render_development_stream(projected, context)
            self.assertIn("data-development-status='archived'", stream)
            self.assertIn('Archived · last successful collection Aug 04, 2026 · 12:00 UTC', stream)
            self.assertIn('Unavailable in this snapshot · archived announcements retained', stream)
            self.assertNotIn('Archived announcement', render.render_project_editorial(projected, context))
        for target in (archive, archive['items'][0]):
            target['secret_marker'] = 'blocked'
            self.assertNotIn('secret_marker', json.dumps(render.project_public_envelope(snapshot)))
            del target['secret_marker']

    def test_requirement_guide_is_collapsed_bound_and_keeps_scope_gaps(self):
        def configure(snapshot):
            snapshot['growth'] = {'available': True, 'daily_active_addresses': {
                'available': True, 'date': '2026-08-05', 'provider_count': 2,
                'minimum': 100, 'maximum': 200,
                **render._PROVIDER_BENCHMARK_CONTRACTS['daily_active_addresses'],
            }}
            snapshot['activity']['stale'] = True
            snapshot['activity']['source_state'] = 'last_known_good'
        history, observations, indexes = TestPublicObservationBindings.observation_fixture(configure_latest=configure)
        for context in ('desktop', 'mobile'):
            guide = render.render_report_coverage(history[-1], None, None, context, indexes)
            self.assertEqual(guide.count('data-requirement='), 17)
            self.assertNotIn(' open', guide.split('>', 1)[0])
            def row(identifier):
                return guide.split(f"data-requirement='{identifier}'", 1)[1].split('</tr>', 1)[0]
            self.assertIn("class='coverage-state'>Partial", row('R16'))
            self.assertIn('different populations', row('R16'))
            self.assertIn("class='coverage-state'>Unavailable", row('R15'))
            self.assertIn("class='coverage-state'>Stale", row('R14'))
            self.assertIn("class='coverage-state'>Unavailable", row('R07'))
            expected = indexes['summary'][('network_wide_daily_active_addresses', history[-1]['collected_at'])]['observation_id']
            self.assertIn(expected, row('R16'))
            self.assertIn(f"href='#{context}-people-markets'", row('R16'))
            self.assertIn('not a completion score', guide)

    def test_data_domain_rail_exposes_the_five_judge_destinations(self):
        history, observations, indexes = TestPublicObservationBindings.observation_fixture()
        for context in ("desktop", "mobile"):
            rail = render.render_data_domain_rail(history[-1], context, indexes)
            self.assertEqual(rail.count("<a href="), 5)
            self.assertNotIn("data-domain-state", rail)
            for label in ("Network", "Validators", "Economy", "Ecosystem", "Sources"):
                self.assertIn(f"<span>{label}</span>", rail)
            self.assertIn("Source catalog, coverage and downloads", rail)
            self.assertNotIn("sources available", rail)
            self.assertIn(f"href='#{context}-validator-evidence'", rail)
            self.assertIn(f"href='#{context}-people-markets'", rail)
            self.assertIn(f"href='#{context}-community-news'", rail)
        self.assertIn("href='#desktop-data-sources'", render.render_data_domain_rail(
            history[-1], "desktop", indexes,
        ))
        self.assertIn("href='#mobile-data-sources'", render.render_data_domain_rail(
            history[-1], "mobile", indexes,
        ))

    def test_feature_activation_projection_and_display_preserve_absent_vs_pending(self):
        snapshot = load_fixture()
        snapshot['schema_version'] = 9
        snapshot['feature_activation'] = {
            'available': True, 'observed_at': '2026-09-04T12:00:00Z',
            'coverage_complete': True, 'coverage_numerator': 3, 'coverage_denominator': 3,
            'activated_feature_count': 1,
            'source': {'method': 'getMultipleAccounts', 'commitment': 'finalized', 'rpc_context_slot': 150},
            'metadata': {'source_url': 'https://github.com/anza-xyz/agave/blob/pinned/feature-set/src/lib.rs'},
            'features': [
                {'key': 'active', 'title': 'Active feature', 'state': 'activated', 'address': 'abc', 'activated_at_slot': 120},
                {'key': 'pending', 'title': 'Pending feature', 'state': 'pending', 'address': 'def', 'activated_at_slot': None},
                {'key': 'absent', 'title': 'Absent feature', 'state': 'account_absent', 'address': 'ghi', 'activated_at_slot': None},
            ],
        }
        projected = render.project_public_envelope(snapshot)
        self.assertEqual(projected['feature_activation'], snapshot['feature_activation'])
        for context in ('desktop', 'mobile'):
            markup = render.render_feature_activation(projected, context)
            self.assertIn('Activation slot 120', markup)
            self.assertIn('Finalized RPC context slot 150', markup)
            self.assertIn('>Pending</strong>', markup)
            self.assertIn('>Account absent</strong>', markup)
            self.assertIn('does not establish activation history', markup)
            self.assertNotIn('not activated', markup)
        for target in (snapshot['feature_activation'], snapshot['feature_activation']['source'],
                       snapshot['feature_activation']['metadata'], snapshot['feature_activation']['features'][0]):
            target['secret_marker'] = 'omit'
            self.assertNotIn('secret_marker', json.dumps(render.project_public_envelope(snapshot)))
            del target['secret_marker']

    def test_desktop_history_selector_is_bounded_native_and_keeps_latest_pair_without_js(self):
        history, observations, indexes = TestPublicObservationBindings.observation_fixture(count=5)
        comparison = render.bind_public_comparison(pipeline.recheck(history), indexes)
        workspace = render.render_history_workspace(history, None, comparison, indexes)
        self.assertEqual(workspace.count('data-desktop-history-panel='), 10)
        controls = workspace.split('</div>', 1)[0]
        self.assertIn('<select data-desktop-history-a', controls)
        self.assertIn('<select data-desktop-history-b', controls)
        self.assertEqual(controls.count('<option'), 10)
        self.assertIn("data-desktop-history-panel='3:4'>", workspace)
        self.assertNotIn("data-desktop-history-panel='3:4' hidden", workspace)
        self.assertIn("data-desktop-history-panel='0:1' hidden", workspace)
        self.assertIn('const updateDesktopHistory', render.MOBILE_CONTROLLER)
        self.assertIn('a >= b', render.MOBILE_CONTROLLER)
        self.assertIn('option.disabled = Number(option.value) >= b', render.MOBILE_CONTROLLER)
        self.assertIn('option.disabled = Number(option.value) <= a', render.MOBILE_CONTROLLER)
        self.assertIn('Older observations remain in report.json', workspace)

    def test_chronology_years_source_date_and_utc_are_unambiguous(self):
        snapshot = editorial_fixture()
        snapshot['news']['sources'] = {'x_announcements': {
            'available': True, 'items': [
                {'title': 'Older entry', 'published': '2022-08-05T12:00:00Z'},
                {'title': 'Newer entry', 'published': '2024-08-05T12:00:00Z'},
            ]}}
        stream = render.render_development_stream(snapshot, 'mobile')
        self.assertIn('<h4>AUG 05, 2022</h4>', stream)
        self.assertIn('<h4>AUG 05, 2024</h4>', stream)
        self.assertIn('Newest entry Aug 05, 2024 · 12:00 UTC', stream)
        self.assertIn("value='xnews'", stream)
        self.assertNotIn('UTC · UTC', render.render_project_editorial(snapshot, 'desktop'))


if __name__ == "__main__":
    unittest.main()
