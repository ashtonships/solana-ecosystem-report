import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import facts


def snapshot(at="2026-08-25T00:00:00+00:00", schema=8, tps=100.0, slot=123):
    return {
        "schema_version": schema,
        "collected_at": at,
        "performance": {"available": True, "latest_tps": tps,
                        "samples": [{"slot": slot}]},
        "activity": {"available": True, "window": {"last_block_time": 1_777_070_400},
                     "rev": {"available": True, "sample_mean_estimate_sol": 12.5}},
    }


def canonical_snapshot(schema=8):
    source = snapshot(schema=schema)
    source["performance"].update({
        "mean_slot_time_secs": 0.4,
        "samples": [{
            "slot": 123, "tps": 100.0, "sample_period_secs": 60,
            "slots": 150, "transactions": 6000, "non_vote_transactions": 2400,
        }],
    })
    source.update({
        "network": {"available": True, "healthy": True, "slot": 999},
        "epoch": {"available": True, "epoch": 800},
        "supply": {"available": True, "circulating_sol": 500_000_000.0},
        "validators": {
            "available": True,
            "active_count": 900,
            "delinquent_pct": 2.5,
            "nakamoto_coefficient": 19,
            "active_stake_sol": 400_000_000.0,
            "all_validators": [],
        },
        "economics": {
            "available": True,
            "price": {"available": True, "price_usd": 105.0,
                      "last_updated_at_unix": 1_777_070_400, "freshness": "fresh"},
            "tvl": {"available": True, "tvl_usd": 5_900_000_000.0},
            "stablecoins": {
                "available": True,
                "usd_pegged_circulating_usd": 15_900_000_000.0,
            },
            "dex": {"available": True, "volume_24h_usd": 1_400_000_000.0},
        },
    })
    source["activity"]["fees"] = {"available": True, "median_lamports": 5000}
    return source


class TestPublicationHistory(unittest.TestCase):
    def test_unavailable_current_economics_withholds_old_values_without_mutation(self):
        earlier = snapshot()
        earlier["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 123.45},
        }
        current = snapshot("2026-08-25T06:00:00+00:00")
        current["economics"] = {"available": False}

        published = facts.publication_history([earlier, current])

        self.assertEqual(earlier["economics"]["price"]["price_usd"], 123.45)
        self.assertEqual(published[0]["economics"]["publication_state"], "withheld")
        self.assertIsNone(facts.fact_from_snapshot(published[0], "price_usd")["value"])


def selected_stablecoin_snapshot(observed=4, first_raw="100"):
    source = snapshot()
    identities = (
        ("USDC", "Circle", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
        ("USDT", "Tether", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
        ("PYUSD", "PayPal USD issued by Paxos",
         "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"),
        ("USDG", "Paxos Digital Singapore",
         "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"),
    )
    assets = []
    for index, (symbol, issuer, mint) in enumerate(identities, 1):
        asset = {"symbol": symbol, "issuer": issuer, "mint": mint,
                 "available": index <= observed}
        if asset["available"]:
            raw = first_raw if index == 1 else str(index * 100)
            decimals = 0 if index == 1 and first_raw != "100" else 2
            total = raw if decimals == 0 else f"{index}.00"
            asset.update({
                "total_supply_decimal": total,
                "raw_amount": raw,
                "decimals": decimals,
                "rpc_ui_amount_string": total,
                "rpc_context_slot": 320 + index,
                "rpc_api_version": "2.3.7",
                "event_time": None,
                "collected_at": f"2026-08-25T00:00:0{index}+00:00",
                "basis": "finalized on-chain total token supply",
                "account_provenance": {
                    "source_method": "getAccountInfo(finalized,jsonParsed)",
                    "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "program": "spl-token",
                    "rpc_context_slot": 300 + index,
                    "rpc_api_version": "2.3.6",
                },
            })
        else:
            asset["reason"] = "Finalized validated mint supply is unavailable."
        assets.append(asset)
    summary = {
        "metric_id": "selected_usd_stablecoin_total_supply",
        "available": observed == 4,
        "state": "current" if observed == 4 else ("partial" if observed else "unavailable"),
        "coverage_numerator": observed,
        "coverage_denominator": 4,
        "coverage_label": f"{observed}/4",
        "universe_coverage": "unknown",
        "unit": "selected stablecoin token units",
        "basis": "finalized on-chain total token supply",
        "assets": assets,
        "registry_source": {
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
        },
    }
    if observed == 4:
        selected_total = sum(Decimal(asset["total_supply_decimal"]) for asset in assets)
        summary["selected_total_supply_decimal"] = format(selected_total, "f")
        for asset in assets:
            asset["share_of_selected_total"] = format(
                Decimal(asset["total_supply_decimal"]) / selected_total, "f"
            )
    source["growth"] = {"selected_usd_stablecoins": summary}
    return source


def xstock_fact_snapshot(observed=2):
    source = snapshot(at="2026-08-24T12:00:00+00:00")
    revision = "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0"
    path = "packages/asset-registry/src/data/xstock-variant-groups.ts"
    registry_url = (
        "https://raw.githubusercontent.com/solana-foundation/tokens/"
        f"{revision}/{path}"
    )
    assets = []
    for index in range(107):
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
        if index < observed:
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
                "supply_collected_at": (
                    "2026-08-24T11:59:00+00:00" if index == 0
                    else "2026-08-24T05:00:00+00:00"
                ),
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
        assets.append(asset)
    coverage = {
        "registry_asset_count": 107,
        "registry_complete": True,
        "eligible_asset_count": 107,
        "queried_this_run_asset_count": observed,
        "successful_this_run_asset_count": min(observed, 1),
        "failed_this_run_asset_count": max(0, observed - 1),
        "attempt_scope": "current collection run",
        "observed_asset_count": observed,
        "fresh_asset_count": 1 if observed else 0,
        "valued_asset_count": 0,
        "coverage_numerator": observed,
        "coverage_denominator": 107,
        "fresh_max_age_seconds": 21_600,
        "sweep_max_age_seconds": 259_200,
        "oldest_observation_at": (
            "2026-08-24T05:00:00+00:00" if observed > 1
            else ("2026-08-24T11:59:00+00:00" if observed else None)
        ),
        "newest_observation_at": "2026-08-24T11:59:00+00:00" if observed else None,
        "observation_span_seconds": 25_140 if observed > 1 else (0 if observed else None),
        "sweep_complete": observed == 107,
        "scope": "registry-wide" if observed == 107 else "observed subset",
        "coverage_basis": (
            "eligible assets with a valid supply observation no older than 72 hours"
        ),
    }
    registry = {
        "url": registry_url,
        "kind": "pinned official token registry",
        "available": True,
        "coverage_complete": True,
        "asset_count": 107,
        "source_key": "solana-foundation/tokens:xstock-variant-groups",
        "source_revision": revision,
        "source_license": "MIT",
        "provenance": {
            "repository": "https://github.com/solana-foundation/tokens",
            "path": path,
            "revision": revision,
            "license": "MIT",
            "selection": "address label exactly 'xStock'",
            "expected_unique_group_count": 107,
            "expected_unique_mint_count": 107,
        },
        "reason": None,
    }
    source["source"] = {"endpoint": "https://api.mainnet.solana.com"}
    source["growth"] = {
        "available": True,
        "tokenized_equities": {
            "available": bool(observed),
            "observed_at_unix": 1_787_572_800,
            "all_assets": assets,
            "supply_coverage": coverage,
        },
        "sources": {"registry": registry},
    }
    return source


def use_legacy_xstock_provenance(asset):
    asset.pop("supply_multiplier_provenance", None)
    asset["supply_account_provenance"] = {
        "source_method": "getAccountInfo(finalized,jsonParsed)",
        "program_id": facts.XSTOCK_TOKEN_PROGRAM_ID,
        "program": "spl-token",
        "rpc_context_slot": 300,
        "rpc_api_version": "2.3.6",
    }
    return asset


class TestFactContract(unittest.TestCase):
    def test_schema_nine_preserves_every_schema_eight_summary_fact_semantic(self):
        schema_eight = canonical_snapshot(8)
        schema_nine = canonical_snapshot(9)
        value_differences = []

        for metric_id in facts.PUBLIC_METRICS:
            with self.subTest(metric_id=metric_id):
                before = facts.fact_from_snapshot(schema_eight, metric_id)
                after = facts.fact_from_snapshot(schema_nine, metric_id)
                self.assertEqual(after["metric_id"], before["metric_id"])
                if after["value"] != before["value"]:
                    value_differences.append(metric_id)
                if metric_id == "snapshot_schema_version":
                    self.assertEqual(before["value"], 8.0)
                    self.assertEqual(after["value"], 9.0)
                    self.assertEqual(before["state"], "current")
                    self.assertEqual(after["state"], "current")
                else:
                    self.assertEqual(after["value"], before["value"])
                self.assertEqual(after["unit"], before["unit"])
                self.assertEqual(after["basis"], before["basis"])
                self.assertEqual(after["state"], before["state"])
                self.assertEqual(after["source"], before["source"])
                self.assertEqual(facts.eligible(after), facts.eligible(before))
                self.assertEqual(after["source_schema"], 9)
        self.assertEqual(value_differences, ["snapshot_schema_version"])

    def test_schema_nine_preserves_schema_eight_specialized_fact_semantics(self):
        for producer, source in (
            (facts.selected_usd_stablecoin_supply_facts,
             selected_stablecoin_snapshot(2)),
            (facts.xstock_labelled_mint_supply_facts, xstock_fact_snapshot(1)),
        ):
            with self.subTest(producer=producer.__name__):
                before = producer(source)
                source["schema_version"] = 9
                after = producer(source)
                self.assertEqual(len(after), len(before))
                self.assertEqual(
                    [{key: value for key, value in item.items() if key != "source_schema"}
                     for item in after],
                    [{key: value for key, value in item.items() if key != "source_schema"}
                     for item in before],
                )
                self.assertTrue(all(item["source_schema"] == 9 for item in after))
                self.assertEqual(
                    [facts.eligible(item) for item in after],
                    [facts.eligible(item) for item in before],
                )

    def test_derived_observation_identity_tracks_method_parameters_and_ordered_inputs(self):
        inputs = ["obs-v1:" + "a" * 64, "obs-v1:" + "b" * 64]
        common = {
            "metric_id": "derived_test_metric",
            "subject_id": "subject-a",
            "name": "Derived test metric",
            "value": 1,
            "unit": "count",
            "population": "test inputs",
            "denominator": "two ordered inputs",
            "window": "test window",
            "snapshot_collected_at": "2026-08-25T00:00:00+00:00",
            "source_path": "derived.test_metric",
            "calculation_method": "sum inputs; threshold >= 10",
            "input_observation_ids": inputs,
        }
        original = facts.derived_public_observation_record(**common)
        repeated = facts.derived_public_observation_record(**common)
        self.assertEqual(original["observation_id"], repeated["observation_id"])

        variants = {
            "formula": {"calculation_method": "median inputs; threshold >= 10"},
            "threshold": {"calculation_method": "sum inputs; threshold >= 11"},
            "input order": {"input_observation_ids": list(reversed(inputs))},
            "input identity": {
                "input_observation_ids": [inputs[0], "obs-v1:" + "c" * 64],
            },
        }
        for label, replacement in variants.items():
            with self.subTest(label=label):
                changed = facts.derived_public_observation_record(
                    **{**common, **replacement},
                )
                self.assertNotEqual(original["observation_id"], changed["observation_id"])

    def test_public_observations_cover_every_summary_metric_and_keep_missing_null(self):
        source = canonical_snapshot(9)
        records = facts.public_observation_records(source)
        summaries = {
            item["metric_id"]: item for item in records if item["subject_id"] is None
            and item["metric_id"] in facts.PUBLIC_METRICS
        }
        summaries.update({
            item["metric_id"]: item for item in records
            if item["record_kind"] == "derived"
            and item["subject_id"] == source["collected_at"]
            and item["metric_id"] in facts.DERIVED_REPORT_METRIC_INPUTS
        })
        self.assertEqual(set(summaries), set(facts.PUBLIC_METRICS))
        required = {
            "observation_id", "record_kind", "metric_id", "subject_id", "name", "value", "type",
            "source_path", "unit", "population", "denominator", "window",
            "observed_at", "observed_slot", "collected_at", "snapshot_collected_at",
            "source", "collection_method",
            "source_url", "calculation_method", "freshness", "status", "quality", "caveat",
            "basis", "input_observation_ids", "output_path",
        }
        self.assertTrue(all(set(item) == required for item in summaries.values()))
        self.assertEqual(summaries["latest_tps"]["value"], 100.0)
        self.assertEqual(summaries["latest_tps"]["status"], "current")
        self.assertIn("performance.latest_tps", summaries["latest_tps"]["output_path"])
        self.assertTrue(all(item["observation_id"].startswith("obs-v1:") for item in summaries.values()))
        self.assertTrue(all(
            item["record_kind"] == (
                "derived" if metric_id in facts.DERIVED_REPORT_METRIC_INPUTS else "direct"
            )
            for metric_id, item in summaries.items()
        ))
        direct_ids = {
            item["metric_id"]: item["observation_id"] for item in records
            if item["subject_id"] is None
        }
        for metric_id, input_metric_ids in facts.DERIVED_REPORT_METRIC_INPUTS.items():
            self.assertEqual(summaries[metric_id]["basis"], "derived")
            self.assertEqual(
                summaries[metric_id]["input_observation_ids"],
                [direct_ids[item] for item in input_metric_ids],
            )
        self.assertTrue(all(
            item["source_url"].startswith("https://")
            for item in summaries.values() if item["status"] != "unavailable"
        ))
        self.assertTrue(all(
            item["source_url"] is None
            for item in summaries.values() if item["status"] == "unavailable"
        ))

    def test_public_observations_cover_renderer_visible_aggregate_paths(self):
        source = canonical_snapshot(9)
        source["network"]["slot"] = 442_000_001
        source["epoch"].update({
            "progress_pct": 40.5,
            "block_height": 421_000_001,
            "transaction_count": 543_000_000_001,
            "estimated_remaining_seconds": 81_000,
        })
        source["performance"].update({
            "mean_tps": 4_100.0,
            "peak_tps": 4_500.0,
            "latest_non_vote_tps": 2_300.0,
            "mean_non_vote_tps": 2_000.0,
            "mean_vote_share_pct": 51.0,
        })
        source["activity"].update({
            "fees": {
                "available": True,
                "median_lamports": 5_000,
                "mean_lamports": 20_000,
                "p90_lamports": 25_000,
                "p99_lamports": 400_000,
                "failure_rate_pct": 12.5,
            },
            "addresses": {
                "available": True,
                "unique_fee_payers_sampled": 2_000,
            },
        })
        source["supply"].update({"total_sol": 633_000_000.0, "circulating_pct": 92.0})
        source["inflation"] = {"available": True, "current_total_pct": 3.5}
        source["validators"].update({
            "commission": {"available": True, "median_pct": 5.0},
        })

        records = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["subject_id"] is None
        }
        expected = {
            "network_healthy": ("network.healthy", True),
            "network_slot": ("network.slot", 442_000_001.0),
            "epoch_progress_pct": ("epoch.progress_pct", 40.5),
            "epoch_block_height": ("epoch.block_height", 421_000_001.0),
            "epoch_transaction_count": ("epoch.transaction_count", 543_000_000_001.0),
            "epoch_estimated_remaining_seconds": ("epoch.estimated_remaining_seconds", 81_000.0),
            "mean_tps": ("performance.mean_tps", 4_100.0),
            "peak_tps": ("performance.peak_tps", 4_500.0),
            "latest_non_vote_tps": ("performance.latest_non_vote_tps", 2_300.0),
            "mean_non_vote_tps": ("performance.mean_non_vote_tps", 2_000.0),
            "mean_vote_share_pct": ("performance.mean_vote_share_pct", 51.0),
            "mean_fee_lamports": ("activity.fees.mean_lamports", 20_000.0),
            "p90_fee_lamports": ("activity.fees.p90_lamports", 25_000.0),
            "p99_fee_lamports": ("activity.fees.p99_lamports", 400_000.0),
            "activity_failure_rate_pct": ("activity.fees.failure_rate_pct", 12.5),
            "unique_fee_payers_sampled": ("activity.addresses.unique_fee_payers_sampled", 2_000.0),
            "total_supply_sol": ("supply.total_sol", 633_000_000.0),
            "circulating_supply_pct": ("supply.circulating_pct", 92.0),
            "inflation_current_total_pct": ("inflation.current_total_pct", 3.5),
            "validator_median_commission_pct": ("validators.commission.median_pct", 5.0),
        }
        for metric_id, (path, value) in expected.items():
            with self.subTest(metric_id=metric_id):
                self.assertEqual(records[metric_id]["source_path"], path)
                self.assertEqual(records[metric_id]["value"], value)
                self.assertEqual(records[metric_id]["status"], "current")
                self.assertTrue(records[metric_id]["source_url"].startswith("https://"))
        self.assertEqual(records["network_healthy"]["type"], "boolean")

    def test_public_observations_cover_selected_total_shares_and_block_production(self):
        source = selected_stablecoin_snapshot(4)
        source["schema_version"] = 9
        source["validators"] = {
            "available": True,
            "block_production": {
                "available": True,
                "epoch": 799,
                "first_slot": 10,
                "last_slot": 19,
                "leader_slots": 10,
                "blocks_produced": 8,
                "skipped_slots": 2,
                "skip_rate": 0.2,
                "validators": [
                    {"identity": "node-a", "vote_identity_matched": True},
                    {"identity": "node-b", "vote_identity_matched": False},
                ],
            },
        }

        records = facts.public_observation_records(source)
        summaries = {
            item["metric_id"]: item for item in records if item["subject_id"] is None
        }
        self.assertEqual(summaries["selected_stablecoin_total_supply"]["value"], "10.00")
        self.assertEqual(summaries["selected_stablecoin_total_supply"]["type"], "decimal-string")
        self.assertIn(
            "four-mint",
            summaries["selected_stablecoin_coverage_numerator"]["collection_method"],
        )
        self.assertNotIn(
            "xStock",
            summaries["selected_stablecoin_coverage_numerator"]["collection_method"],
        )
        self.assertEqual(summaries["block_production_epoch"]["value"], 799.0)
        self.assertEqual(summaries["block_production_leader_slots"]["value"], 10.0)
        self.assertEqual(summaries["block_production_blocks_produced"]["value"], 8.0)
        self.assertEqual(summaries["block_production_skipped_slots"]["value"], 2.0)
        self.assertEqual(summaries["block_production_skip_rate"]["value"], 0.2)
        self.assertEqual(summaries["block_production_identity_count"]["value"], 2.0)
        self.assertEqual(summaries["block_production_unmatched_identity_count"]["value"], 1.0)
        self.assertEqual(summaries["block_production_vote_join_coverage_pct"]["value"], 50.0)
        shares = [
            item for item in records
            if item["metric_id"] == facts.SELECTED_STABLECOIN_SHARE_METRIC_ID
        ]
        self.assertEqual([item["value"] for item in shares], ["0.1", "0.2", "0.3", "0.4"])
        self.assertTrue(all(item["record_kind"] == "derived" for item in shares))
        self.assertTrue(all(item["type"] == "decimal-string" for item in shares))
        self.assertTrue(all(
            item["source_path"]
            == "growth.selected_usd_stablecoins.assets[].share_of_selected_total"
            for item in shares
        ))
        ordered_mints = [mint for _, _, mint in facts.SELECTED_STABLECOIN_IDENTITIES]
        supply_records = {
            item["subject_id"]: item for item in records
            if item["metric_id"] == facts.SELECTED_STABLECOIN_METRIC_ID
            and item["subject_id"] is not None
        }
        ordered_supply_ids = [supply_records[mint]["observation_id"] for mint in ordered_mints]
        self.assertTrue(all(
            item["input_observation_ids"] == ordered_supply_ids for item in shares
        ))

        mutated = selected_stablecoin_snapshot(4)
        mutated["schema_version"] = 9
        selected = mutated["growth"]["selected_usd_stablecoins"]
        usdt = next(asset for asset in selected["assets"] if asset["symbol"] == "USDT")
        usdt.update({
            "total_supply_decimal": "5.00",
            "raw_amount": "500",
            "rpc_ui_amount_string": "5.00",
            "rpc_context_slot": usdt["rpc_context_slot"] + 1,
        })
        mutated_total = sum(
            Decimal(asset["total_supply_decimal"]) for asset in selected["assets"]
        )
        selected["selected_total_supply_decimal"] = format(mutated_total, "f")
        for asset in selected["assets"]:
            asset["share_of_selected_total"] = format(
                Decimal(asset["total_supply_decimal"]) / mutated_total, "f"
            )

        mutated_records = facts.public_observation_records(mutated)
        mutated_supplies = {
            item["subject_id"]: item for item in mutated_records
            if item["metric_id"] == facts.SELECTED_STABLECOIN_METRIC_ID
            and item["subject_id"] is not None
        }
        mutated_supply_ids = [
            mutated_supplies[mint]["observation_id"] for mint in ordered_mints
        ]
        mutated_shares = {
            item["subject_id"]: item for item in mutated_records
            if item["metric_id"] == facts.SELECTED_STABLECOIN_SHARE_METRIC_ID
        }
        self.assertEqual(
            {mint: mutated_shares[mint]["value"] for mint in ordered_mints},
            {
                asset["mint"]: asset["share_of_selected_total"]
                for asset in selected["assets"]
            },
        )
        self.assertTrue(all(
            mutated_shares[mint]["record_kind"] == "derived"
            and mutated_shares[mint]["type"] == "decimal-string"
            and mutated_shares[mint]["input_observation_ids"] == mutated_supply_ids
            for mint in ordered_mints
        ))
        original_share_ids = {item["subject_id"]: item["observation_id"] for item in shares}
        self.assertTrue(all(
            mutated_shares[mint]["observation_id"] != original_share_ids[mint]
            for mint in ordered_mints
        ))

        unavailable = canonical_snapshot(9)
        unavailable["economics"]["price"] = {
            "available": False,
            "price_usd": 999.0,
            "freshness": "unavailable",
            "reason": "Price source is release-held.",
        }
        price = next(
            item for item in facts.public_observation_records(unavailable)
            if item["metric_id"] == "price_usd"
        )
        self.assertIsNone(price["value"])
        self.assertEqual(price["status"], "unavailable")
        self.assertEqual(price["freshness"], "unavailable")
        self.assertIsNone(price["source_url"])
        self.assertIn("Price source is release-held.", price["caveat"])

    def test_public_projection_shape_retains_economics_publication_hold(self):
        source = canonical_snapshot(9)
        # Schema 9 intentionally projects held economics to this minimal shape.
        source["economics"] = {"available": False}
        economic_ids = {
            metric_id for metric_id, spec in facts.PUBLIC_METRICS.items()
            if spec["path"][:1] == ("economics",)
        }
        records = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["subject_id"] is None and item["metric_id"] in economic_ids
        }

        self.assertEqual(len(economic_ids), 9)
        self.assertEqual(set(records), economic_ids)
        for record in records.values():
            self.assertIsNone(record["value"])
            self.assertEqual(record["status"], "unavailable")
            self.assertEqual(record["freshness"], "unavailable")
            self.assertIsNone(record["source_url"])
            self.assertIn(facts.ECONOMICS_PUBLICATION_HOLD, record["caveat"])
            self.assertNotIn("no source reason was recorded", record["caveat"])

    def test_recorded_catalog_sources_have_current_boolean_observations(self):
        source = canonical_snapshot(9)
        source["economics"]["sources"] = {
            "price": {
                "available": True,
                "label": "SOL price",
                "source_url": "https://example.com/price",
            },
            "tvl": {
                "available": False,
                "label": "TVL",
                "source_url": "https://example.com/tvl",
                "reason": "Release-held source.",
            },
        }
        source["news"] = {
            "available": False,
            "sources": {
                "status": {
                    "available": True,
                    "label": "Solana Status",
                    "source_url": "https://status.solana.com/",
                },
                "simd": {
                    "available": False,
                    "label": "SIMD proposals",
                    "source_url": "https://github.com/solana-foundation/solana-improvement-documents",
                    "reason": "Feed unavailable.",
                },
            },
        }

        records = {
            (item["metric_id"], item["subject_id"]): item
            for item in facts.public_observation_records(source)
            if item["metric_id"] in ("economic_source_available", "news_source_available")
        }
        expected = {
            ("economic_source_available", "price"): True,
            ("economic_source_available", "tvl"): False,
            ("news_source_available", "status"): True,
            ("news_source_available", "simd"): False,
        }

        self.assertEqual(set(records), set(expected))
        self.assertEqual(len(records), 4)
        for identity, expected_value in expected.items():
            with self.subTest(identity=identity):
                record = records[identity]
                self.assertEqual(record["value"], expected_value)
                self.assertEqual(record["type"], "boolean")
                self.assertEqual(record["unit"], "boolean")
                self.assertEqual(record["status"], "current")
                self.assertEqual(record["record_kind"], "direct")

    def test_recorded_categorical_context_survives_unavailable_parent_as_partial(self):
        source = canonical_snapshot(9)
        source["news"] = {
            "available": False,
            "current_status": {
                "available": False,
                "description": "Partial outage under investigation",
            },
        }
        source["growth"] = {
            "available": False,
            "tokenized_equities": {
                "available": False,
                "supply_coverage": {
                    "available": False,
                    "oldest_observation_at": "2026-08-24T00:00:00+00:00",
                    "newest_observation_at": "2026-08-25T00:00:00+00:00",
                },
            },
            "selected_usd_stablecoins": {
                "available": False,
                "newest_observation_at": "2026-08-25T00:00:01+00:00",
            },
            "daily_active_addresses": {
                "available": False,
                "date": "2026-08-24",
            },
            "daily_fee_payers": {
                "available": False,
                "date": "2026-08-23",
            },
        }
        expected = {
            "network_status_description": "Partial outage under investigation",
            "xstock_supply_oldest_observation_at": "2026-08-24T00:00:00+00:00",
            "xstock_supply_newest_observation_at": "2026-08-25T00:00:00+00:00",
            "selected_stablecoin_newest_observation_at": "2026-08-25T00:00:01+00:00",
            "stablecoin_active_address_provider_date": "2026-08-24",
            "transaction_initiator_provider_date": "2026-08-23",
        }

        records = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["subject_id"] is None and item["metric_id"] in expected
        }
        self.assertEqual(set(records), set(expected))
        for metric_id, expected_value in expected.items():
            with self.subTest(metric_id=metric_id):
                self.assertEqual(records[metric_id]["value"], expected_value)
                self.assertEqual(records[metric_id]["type"], "categorical")
                self.assertEqual(records[metric_id]["status"], "partial")

    def test_coingecko_provider_timestamp_becomes_observation_time(self):
        source = canonical_snapshot(9)
        source["economics"]["price"]["last_updated_at_unix"] = 1_700_000_000
        record = next(
            item for item in facts.public_observation_records(source)
            if item["metric_id"] == "price_usd"
        )

        self.assertEqual(record["observed_at"], "2023-11-14T22:13:20+00:00")

    def test_public_observations_include_user_visible_subject_facts(self):
        core = canonical_snapshot(9)
        core["source"] = {
            "endpoint": "https://user:private@example.test/?api-key=private",
        }
        core["performance"]["samples"][0].update({
            "non_vote_tps": 40.0,
            "vote_tps": 60.0,
            "vote_share_pct": 60.0,
            "slot_time_secs": 0.4,
        })
        core["validators"]["all_validators"] = [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5, "stake_sol": 100.0, "share_pct": 2.5,
            "last_vote": 122, "root_slot": 121,
        }]
        core_records = facts.public_observation_records(core)
        sample = next(item for item in core_records
                      if item["metric_id"] == "performance_sample_tps")
        commission = next(item for item in core_records
                          if item["metric_id"] == "validator_commission_pct")
        sample_non_vote = next(item for item in core_records
                               if item["metric_id"] == "performance_sample_non_vote_tps")
        validator_stake = next(item for item in core_records
                               if item["metric_id"] == "validator_stake_sol")
        self.assertIsNone(sample["observed_at"])
        self.assertEqual(sample["observed_slot"], 123)
        self.assertEqual(sample_non_vote["subject_id"], "slot:123")
        self.assertEqual(sample_non_vote["value"], 40.0)
        self.assertIn("performance.samples[].non_vote_tps", sample_non_vote["source_path"])
        self.assertEqual(commission["subject_id"], "vote-a")
        self.assertEqual(validator_stake["subject_id"], "vote-a")
        self.assertEqual(validator_stake["value"], 100.0)
        self.assertIn("validators.all_validators[].stake_sol", validator_stake["source_path"])
        current_urls = [item["source_url"] for item in core_records
                        if item["source_url"] is not None]
        self.assertTrue(all(item.startswith("https://") for item in current_urls))
        self.assertNotIn("private", " ".join(current_urls))

        simd_source = snapshot(schema=9)
        simd_source["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True, "partial": False, "source_commit": "a" * 40,
            "proposals": [{
                "identifier": "SIMD-0326", "name": "Alpenglow", "status": "Review",
                "created": "2025-07-25", "source": "https://example.com/0326",
                "source_path": "proposals/0326.md", "source_commit": "a" * 40,
                "basis": "recorded",
            }],
        }}}
        persisted_simd = facts.simd_lifecycle_facts(simd_source)[0]
        public_simd = next(
            item for item in facts.public_observation_records(simd_source)
            if item["metric_id"] == "simd_lifecycle_status"
        )
        self.assertIsNone(persisted_simd["value"])
        self.assertFalse(facts.eligible(persisted_simd))
        self.assertEqual(public_simd["value"], "Review")
        self.assertEqual(public_simd["type"], "categorical")
        self.assertEqual(public_simd["source_path"], "coverage.status")
        self.assertEqual(public_simd["source_url"], "https://example.com/0326")

        selected = selected_stablecoin_snapshot(1)
        selected["schema_version"] = 9
        selected_row = next(
            item for item in facts.public_observation_records(selected)
            if item["metric_id"] == facts.SELECTED_STABLECOIN_METRIC_ID
        )
        self.assertEqual(selected_row["subject_id"], facts.SELECTED_STABLECOIN_IDENTITIES[0][2])
        self.assertEqual(selected_row["status"], "current")
        self.assertEqual(selected_row["source_url"], facts.GET_TOKEN_SUPPLY_URL)

        xstock = xstock_fact_snapshot(1)
        xstock["schema_version"] = 9
        xstock_row = next(
            item for item in facts.public_observation_records(xstock)
            if item["metric_id"] == facts.XSTOCK_METRIC_ID
        )
        self.assertEqual(xstock_row["subject_id"], "mint-000")
        self.assertEqual(xstock_row["status"], "current")
        self.assertEqual(xstock_row["source_url"], facts.GET_TOKEN_SUPPLY_URL)

    def test_public_observation_with_incomplete_required_metadata_is_unavailable(self):
        source = snapshot(schema=9)
        source["growth"] = {
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                ],
            },
        }
        row = next(
            item for item in facts.public_observation_records(source)
            if item["metric_id"] == "stablecoin_active_address_provider_range"
        )
        self.assertIsNone(row["value"])
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["freshness"], "unavailable")
        self.assertIn("name", row["caveat"])
        self.assertIn("population", row["caveat"])
        self.assertIn("source_url", row["caveat"])

    def test_snapshot_fact_pack_includes_metrics_samples_and_commissions(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["performance"]["samples"] = [{
            "slot": 123, "tps": 100.0, "sample_period_secs": 60,
            "slots": 150, "transactions": 6000, "non_vote_transactions": 2400,
        }]
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        packed = facts.snapshot_facts(source)
        self.assertIn("latest_tps", {item["metric_id"] for item in packed})
        self.assertIn("performance_sample_tps", {item["metric_id"] for item in packed})
        self.assertIn("validator_commission_pct", {item["metric_id"] for item in packed})

    def test_fact_retains_provenance_without_inventing_event_time(self):
        item = facts.fact_from_snapshot(snapshot(), "latest_tps")
        self.assertEqual(item["metric_id"], "latest_tps")
        self.assertEqual(item["event_slot"], 123)
        self.assertIsNone(item["event_time"])
        self.assertEqual(item["collected_at"], "2026-08-25T00:00:00+00:00")
        self.assertEqual(item["source_schema"], 8)
        self.assertEqual(item["basis"], "measured")

    def test_corrected_rev_is_schema_eight_only(self):
        old = snapshot(schema=7)
        old["activity"]["rev"] = {"estimated_24h_sol": 99.0}
        self.assertEqual(facts.fact_from_snapshot(old, "sample_mean_rev_sol")["state"],
                         "unavailable")
        self.assertIsNone(facts.fact_from_snapshot(old, "sample_mean_rev_sol")["value"])
        self.assertEqual(facts.fact_from_snapshot(snapshot(), "sample_mean_rev_sol")["value"], 12.5)

    def test_usd_pegged_supply_refuses_legacy_generic_stablecoin_facts(self):
        legacy = snapshot(schema=7)
        legacy["economics"] = {
            "available": True,
            "stablecoins": {"available": True, "stablecoin_usd": 12.0},
        }
        current = snapshot()
        current["economics"] = {
            "available": True,
            "stablecoins": {
                "available": True,
                "usd_pegged_circulating_usd": 13.0,
            },
        }
        self.assertEqual(
            facts.fact_from_snapshot(legacy, "usd_pegged_circulating_usd")["state"],
            "unavailable",
        )
        self.assertEqual(
            facts.fact_from_snapshot(current, "usd_pegged_circulating_usd")["value"],
            13.0,
        )

    def test_selected_stablecoin_facts_are_per_mint_measured_slot_facts(self):
        source = selected_stablecoin_snapshot()
        packed = facts.selected_usd_stablecoin_supply_facts(source)
        self.assertEqual(len(packed), 4)
        self.assertEqual(
            [item["subject_id"] for item in packed],
            [identity[2] for identity in (
                ("USDC", "Circle", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
                ("USDT", "Tether", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
                ("PYUSD", "PayPal USD issued by Paxos",
                 "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"),
                ("USDG", "Paxos Digital Singapore",
                 "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"),
            )],
        )
        self.assertEqual([item["event_slot"] for item in packed], [321, 322, 323, 324])
        self.assertTrue(all(item["event_time"] is None for item in packed))
        self.assertTrue(all(item["basis"] == "measured" for item in packed))
        self.assertTrue(all(item["state"] == "current" for item in packed))
        self.assertEqual([item["value"] for item in packed], [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(packed[0]["coverage"]["raw_amount"], "100")
        self.assertEqual(packed[0]["coverage"]["decimals"], 2)
        self.assertEqual(packed[0]["coverage"]["total_supply_decimal"], "1.00")
        self.assertEqual(packed[0]["coverage"]["account_rpc_context_slot"], 301)
        self.assertEqual(packed[0]["coverage"]["account_rpc_api_version"], "2.3.6")
        self.assertEqual(packed[0]["coverage"]["coverage_numerator"], 1)
        self.assertEqual(packed[0]["coverage"]["coverage_denominator"], 4)
        self.assertEqual(packed[0]["coverage"]["coverage_label"], "1/4")
        self.assertNotIn("selected_mint_count", packed[0]["coverage"])
        self.assertEqual(
            packed[0]["coverage"]["fact_contract"],
            "selected-stablecoin-supply-v2",
        )
        self.assertIsNone(packed[0]["source_revision"])
        self.assertIn(packed[0], facts.snapshot_facts(source))

    def test_selected_stablecoin_partial_section_retains_only_observed_mints(self):
        source = selected_stablecoin_snapshot(3)
        packed = facts.selected_usd_stablecoin_supply_facts(source)
        self.assertEqual(len(packed), 3)
        self.assertTrue(all(item["state"] == "current" for item in packed))
        self.assertTrue(all(item["coverage"]["coverage_numerator"] == 1
                            for item in packed))
        self.assertTrue(all(item["coverage"]["coverage_denominator"] == 4
                            for item in packed))
        self.assertTrue(all("selected_mint_count" not in item["coverage"]
                            for item in packed))
        self.assertTrue(all(item["coverage"]["universe_coverage"] == "unknown"
                            for item in packed))

    def test_selected_stablecoin_observation_is_invariant_across_set_coverage(self):
        complete = facts.selected_usd_stablecoin_supply_facts(
            selected_stablecoin_snapshot(4),
        )[0]
        partial = facts.selected_usd_stablecoin_supply_facts(
            selected_stablecoin_snapshot(3),
        )[0]

        self.assertEqual(facts.fact_identity(complete), facts.fact_identity(partial))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.append_jsonl(path, [complete]), 1)
            self.assertEqual(facts.jsonl_additions(path, [partial]), [])
        self.assertEqual(partial, complete)
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([complete, {**partial, "value": 9.0}])

    def test_legacy_selected_stablecoin_fact_adapts_without_ledger_rewrite(self):
        canonical = facts.selected_usd_stablecoin_supply_facts(
            selected_stablecoin_snapshot(4),
        )[0]
        legacy_coverage = dict(canonical["coverage"])
        legacy_coverage.pop("coverage_label")
        legacy_coverage.pop("coverage_basis")
        legacy_coverage.update({
            "selected_mint_count": 4,
            "coverage_numerator": 4,
            "fact_contract": "selected-stablecoin-supply-v1",
        })
        legacy = {**canonical, "coverage": legacy_coverage}

        self.assertEqual(facts.dedupe_facts([legacy, canonical]), [canonical])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(facts.jsonl_additions(path, [canonical]), [])
            self.assertEqual(path.read_bytes(), before)

    def test_selected_stablecoin_fact_keeps_exact_value_beyond_float_precision(self):
        raw = "9007199254740993"
        item = facts.selected_usd_stablecoin_supply_facts(
            selected_stablecoin_snapshot(1, first_raw=raw),
        )[0]
        self.assertEqual(item["value"], float(raw))
        self.assertEqual(item["coverage"]["raw_amount"], raw)
        self.assertEqual(item["coverage"]["total_supply_decimal"], raw)
        self.assertIn("exact value", item["coverage"]["value_contract"])

    def test_selected_stablecoin_fact_contract_rejects_mutated_exact_evidence(self):
        item = facts.selected_usd_stablecoin_supply_facts(
            selected_stablecoin_snapshot(1),
        )[0]
        mutations = (
            {"event_time": "2026-08-25T00:00:01+00:00"},
            {"source_revision": facts.SELECTED_STABLECOIN_SOURCE_REVISION},
            {"coverage": {**item["coverage"], "raw_amount": "101"}},
            {"coverage": {**item["coverage"], "decimals": 3}},
            {"coverage": {**item["coverage"], "total_supply_decimal": "1.01"}},
            {"coverage": {**item["coverage"], "account_rpc_context_slot": -1}},
            {"coverage": {**item["coverage"], "account_rpc_api_version": ""}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(facts.FactConflictError):
                    facts.dedupe_facts([{**item, **mutation}])

    def test_xstock_facts_are_per_mint_scaled_ui_slot_observations(self):
        source = xstock_fact_snapshot()
        packed = facts.xstock_labelled_mint_supply_facts(source)
        self.assertEqual(len(packed), 2)
        self.assertEqual([item["metric_id"] for item in packed], [
            "xstock_labelled_mint_total_supply",
            "xstock_labelled_mint_total_supply",
        ])
        by_mint = {item["subject_id"]: item for item in packed}
        self.assertEqual(set(by_mint), {"mint-000", "mint-001"})
        self.assertEqual(by_mint["mint-000"]["event_slot"], 321)
        self.assertEqual(by_mint["mint-001"]["event_slot"], 322)
        self.assertTrue(all(item["event_time"] is None for item in packed))
        self.assertTrue(all(item["state"] == "current" for item in packed))
        self.assertTrue(all(item["basis"] == "measured" for item in packed))
        self.assertTrue(all(item["source_revision"] is None for item in packed))
        coverage = by_mint["mint-000"]["coverage"]
        self.assertEqual(coverage["raw_amount"], "100000000")
        self.assertEqual(coverage["decimals"], 8)
        self.assertEqual(coverage["rpc_ui_amount_string"], "1.25")
        self.assertEqual(coverage["coverage_numerator"], 1)
        self.assertEqual(coverage["coverage_denominator"], 107)
        self.assertEqual(coverage["coverage_label"], "1/107")
        self.assertEqual(coverage["fact_contract"],
                         "xstock-labelled-mint-supply-v2")
        self.assertEqual(
            coverage["coverage_basis"],
            "one observed labelled mint in the pinned 107-mint registry",
        )
        self.assertFalse({
            "supply_age_seconds", "fresh_max_age_seconds", "observed_at_unix",
        } & coverage.keys())
        self.assertEqual(coverage["registry_source_revision"],
                         "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0")
        self.assertEqual(coverage["multiplier_provenance"]["extension"],
                         "scaledUiAmountConfig")
        self.assertIn(by_mint["mint-000"], facts.snapshot_facts(source))
        self.assertNotIn("xstock_labelled_mint_total_supply", facts.METRICS)

    def test_xstock_107_of_107_keeps_strict_legacy_and_scaled_observations(self):
        source = xstock_fact_snapshot(107)
        use_legacy_xstock_provenance(
            source["growth"]["tokenized_equities"]["all_assets"][0],
        )

        packed = facts.xstock_labelled_mint_supply_facts(source)
        self.assertEqual(len(packed), 107)
        legacy = next(item for item in packed if item["subject_id"] == "mint-000")
        self.assertEqual(legacy["source"], facts.XSTOCK_LEGACY_SOURCE)
        self.assertEqual(legacy["quality"], facts.XSTOCK_LEGACY_FACT_QUALITY)
        self.assertIn("account_provenance", legacy["coverage"])
        self.assertNotIn("multiplier_provenance", legacy["coverage"])

        observations = [
            item for item in facts.public_observation_records(source)
            if item["metric_id"] == facts.XSTOCK_METRIC_ID
        ]
        self.assertEqual(len(observations), 107)
        self.assertTrue(all(item["status"] == "current" for item in observations))
        legacy_observation = next(
            item for item in observations if item["subject_id"] == "mint-000"
        )
        legacy_metadata = " ".join(
            legacy_observation[key]
            for key in ("window", "collection_method", "calculation_method")
        )
        self.assertIn("legacy SPL Token", legacy_metadata)
        self.assertNotIn("Token-2022", legacy_metadata)
        self.assertNotIn("multiplier", legacy_metadata)

    def test_public_observations_distinguish_unavailable_growth_populations(self):
        source = canonical_snapshot(9)
        source["growth"] = {
            "daily_active_addresses": {
                "available": False,
                "reason": "Network-wide daily active addresses remain unavailable.",
            },
            "selected_usd_stablecoins": {
                "available": False,
                "state": "unavailable",
                "reason": (
                    "Complete selected-mint evidence is unavailable; "
                    "no cleared complete-universe source."
                ),
            },
            "tokenized_equities": {
                "available": True,
                "valuation": {"available": False, "reason": "No cleared valuation source."},
                "volume": {"available": False, "reason": "No cleared indexed-pool source."},
            },
        }

        records = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["subject_id"] is None
        }
        expected = {
            "network_wide_daily_active_addresses": "Network-wide daily active addresses",
            "network_wide_circulating_stablecoin_composition": "complete-universe",
            "xstock_usd_valuation": "valuation source",
            "xstock_indexed_dex_volume_24h_usd": "indexed-pool source",
        }
        for metric_id, reason in expected.items():
            with self.subTest(metric_id=metric_id):
                self.assertIsNone(records[metric_id]["value"])
                self.assertEqual(records[metric_id]["status"], "unavailable")
                self.assertIn(reason, records[metric_id]["caveat"])
        self.assertNotEqual(
            records["network_wide_daily_active_addresses"]["population"],
            records["network_wide_circulating_stablecoin_composition"]["population"],
        )

    def test_public_history_observations_include_prior_metrics_and_samples_without_aliases(self):
        earlier = canonical_snapshot(9)
        earlier["collected_at"] = "2026-08-24T18:00:00+00:00"
        earlier["performance"]["samples_used"] = 1
        earlier["performance"]["samples"][0]["slot"] = 100
        later = canonical_snapshot(9)
        later["collected_at"] = "2026-08-25T00:00:00+00:00"
        later["performance"]["samples_used"] = 1
        later["performance"]["samples"][0]["slot"] = 200

        records = facts.public_observation_records(later, history=[earlier, later])
        latest_tps = [item for item in records if item["metric_id"] == "latest_tps"]
        sample_counts = [
            item for item in records if item["metric_id"] == "performance_samples_used"
        ]
        samples = [item for item in records if item["metric_id"] == "performance_sample_tps"]
        self.assertEqual(len(latest_tps), 2)
        self.assertEqual(len(sample_counts), 2)
        self.assertEqual([item["value"] for item in sample_counts], [1.0, 1.0])
        self.assertEqual(len(samples), 2)
        self.assertEqual(len({item["observation_id"] for item in records}), len(records))
        self.assertEqual(
            {item["snapshot_collected_at"] for item in latest_tps},
            {earlier["collected_at"], later["collected_at"]},
        )
        self.assertTrue(all(
            f'observation_id={json.dumps(item["observation_id"])}' in item["output_path"]
            for item in latest_tps
        ))

    def test_simd_summary_counts_are_direct_and_fail_closed_when_held(self):
        source = canonical_snapshot(9)
        source["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True,
            "proposal_count": 326,
            "document_count": 330,
            "source_commit": "a" * 40,
        }}}
        records = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["metric_id"] in {"simd_proposal_count", "simd_document_count"}
        }
        self.assertEqual(records["simd_proposal_count"]["value"], 326.0)
        self.assertEqual(records["simd_document_count"]["value"], 330.0)
        self.assertTrue(all(item["record_kind"] == "direct" for item in records.values()))
        self.assertEqual(len({item["observation_id"] for item in records.values()}), 2)

        source["news"]["sources"]["simd_proposals"].update({
            "available": False,
            "reason": "source-rights acceptance gate",
        })
        held = {
            item["metric_id"]: item for item in facts.public_observation_records(source)
            if item["metric_id"] in records
        }
        for record in held.values():
            self.assertIsNone(record["value"])
            self.assertEqual(record["status"], "unavailable")
            self.assertIn("source-rights acceptance gate", record["caveat"])

    def test_public_history_retains_network_health_and_slot_for_every_schema(self):
        incompatible = canonical_snapshot(7)
        incompatible["collected_at"] = "2026-08-24T12:00:00+00:00"
        schema_eight = canonical_snapshot(8)
        schema_eight["collected_at"] = "2026-08-24T18:00:00+00:00"
        schema_nine = canonical_snapshot(9)
        schema_nine["collected_at"] = "2026-08-25T00:00:00+00:00"
        snapshots = [incompatible, schema_eight, schema_nine]

        records = facts.public_observation_records(schema_nine, history=snapshots)
        expected_times = {item["collected_at"] for item in snapshots}
        for metric_id, expected_value in (
            ("network_healthy", True),
            ("network_slot", 999.0),
        ):
            with self.subTest(metric_id=metric_id):
                metric_records = [
                    item for item in records if item["metric_id"] == metric_id
                ]
                self.assertEqual(len(metric_records), len(snapshots))
                self.assertEqual(
                    {item["snapshot_collected_at"] for item in metric_records},
                    expected_times,
                )
                self.assertTrue(all(
                    item["record_kind"] == "direct" for item in metric_records
                ))
                by_time = {
                    item["snapshot_collected_at"]: item for item in metric_records
                }
                incompatible_record = by_time[incompatible["collected_at"]]
                self.assertIsNone(incompatible_record["value"])
                self.assertEqual(incompatible_record["status"], "unavailable")
                self.assertIn("incompatible source schema", incompatible_record["caveat"])
                for compatible in (schema_eight, schema_nine):
                    record = by_time[compatible["collected_at"]]
                    self.assertEqual(record["value"], expected_value)
                    self.assertEqual(record["status"], "current")

    def test_xstock_legacy_provenance_fails_closed_when_malformed_or_mixed(self):
        mutations = (
            ("program id", "program_id", "11111111111111111111111111111111"),
            ("program", "program", "spl-token-2022"),
            ("method", "source_method", "getAccountInfo"),
            ("slot", "rpc_context_slot", 999),
            ("api version", "rpc_api_version", ""),
        )
        for label, key, value in mutations:
            with self.subTest(label=label):
                source = xstock_fact_snapshot(2)
                asset = use_legacy_xstock_provenance(
                    source["growth"]["tokenized_equities"]["all_assets"][0],
                )
                asset["supply_account_provenance"][key] = value
                self.assertEqual(facts.xstock_labelled_mint_supply_facts(source), [])

        mixed = xstock_fact_snapshot(2)
        asset = mixed["growth"]["tokenized_equities"]["all_assets"][0]
        multiplier = asset["supply_multiplier_provenance"]
        use_legacy_xstock_provenance(asset)
        asset["supply_multiplier_provenance"] = multiplier
        self.assertEqual(facts.xstock_labelled_mint_supply_facts(mixed), [])

    def test_cached_xstock_observations_are_invariant_jsonl_duplicates(self):
        first_snapshot = xstock_fact_snapshot(2)
        later_snapshot = xstock_fact_snapshot(2)
        later_snapshot["collected_at"] = "2026-08-27T20:00:00+00:00"
        equities = later_snapshot["growth"]["tokenized_equities"]
        equities["observed_at_unix"] = 1_787_860_800
        equities["all_assets"][0]["supply_age_seconds"] = 288_060
        equities["all_assets"][0]["supply_freshness"] = "stale"
        equities["all_assets"][1]["supply_age_seconds"] = 313_200
        equities["all_assets"][1]["supply_freshness"] = "stale"
        equities["supply_coverage"]["fresh_asset_count"] = 0
        equities["supply_coverage"]["coverage_numerator"] = 0

        first = facts.xstock_labelled_mint_supply_facts(first_snapshot)
        repeated = facts.xstock_labelled_mint_supply_facts(later_snapshot)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.append_jsonl(path, first), 2)
            self.assertEqual(facts.jsonl_additions(path, repeated), [])
        self.assertEqual(repeated, first)

    def test_xstock_scaled_ui_fact_does_not_invent_raw_decimal_equality(self):
        source = xstock_fact_snapshot(1)
        source["growth"]["tokenized_equities"]["all_assets"][0][
            "supply_raw_amount"
        ] = "100000001"
        packed = facts.xstock_labelled_mint_supply_facts(source)
        self.assertEqual(len(packed), 1)
        self.assertEqual(packed[0]["value"], 1.25)
        self.assertEqual(packed[0]["coverage"]["raw_amount"], "100000001")
        self.assertNotIn("aggregate", packed[0]["metric_id"])

    def test_xstock_registry_only_snapshot_emits_no_supply_fact(self):
        source = xstock_fact_snapshot(0)
        self.assertEqual(facts.xstock_labelled_mint_supply_facts(source), [])
        self.assertFalse(any(
            item["metric_id"] == "xstock_labelled_mint_total_supply"
            for item in facts.snapshot_facts(source)
        ))

    def test_xstock_fact_contract_rejects_mutated_trust_evidence(self):
        item = facts.xstock_labelled_mint_supply_facts(xstock_fact_snapshot(1))[0]
        mutations = (
            {"event_time": "2026-08-24T11:59:00+00:00"},
            {"event_slot": 999},
            {"source_revision": "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0"},
            {"state": "stale"},
            {"value": 9.0},
            {"coverage": {**item["coverage"], "raw_amount": "1.0"}},
            {"coverage": {**item["coverage"], "rpc_ui_amount_string": "9"}},
            {"coverage": {**item["coverage"], "coverage_numerator": 2}},
            {"coverage": {**item["coverage"], "coverage_denominator": 106}},
            {"coverage": {**item["coverage"], "supply_age_seconds": 0}},
            {"coverage": {**item["coverage"], "registry_source_revision": "b" * 40}},
            {"coverage": {
                **item["coverage"],
                "multiplier_provenance": {
                    **item["coverage"]["multiplier_provenance"],
                    "extension": "unknown",
                },
            }},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(facts.FactConflictError):
                    facts.dedupe_facts([{**item, **mutation}])

        malformed = xstock_fact_snapshot(1)
        malformed["growth"]["tokenized_equities"]["all_assets"][0][
            "supply_multiplier_provenance"
        ]["state"]["multiplier"] = "0"
        self.assertEqual(facts.xstock_labelled_mint_supply_facts(malformed), [])

    def test_same_source_native_identity_deduplicates(self):
        first = facts.fact_from_snapshot(snapshot(), "latest_tps")
        duplicate = dict(first, collected_at="2026-08-25T01:00:00+00:00")
        self.assertEqual(facts.dedupe_facts([first, duplicate]), [first])

    def test_conflicting_source_native_identity_fails_closed(self):
        first = facts.fact_from_snapshot(snapshot(), "latest_tps")
        conflict = dict(first, value=101.0)
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([first, conflict])

    def test_invalid_persisted_fact_fails_closed(self):
        invalid = facts.fact_from_snapshot(snapshot(), "latest_tps")
        invalid["collected_at"] = "not-a-time"
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([invalid])

    def test_rapid_observations_do_not_count_as_six_hour_priors(self):
        items = [
            facts.fact_from_snapshot(snapshot(at=f"2026-08-25T0{hour}:00:00+00:00",
                                              tps=100 + hour, slot=123 + hour), "latest_tps")
            for hour in range(4)
        ]
        self.assertEqual(len(facts.cadence_eligible(items)), 1)

    def test_five_hour_jitter_tolerance_keeps_scheduled_priors(self):
        items = [
            facts.fact_from_snapshot(snapshot(at=at, tps=100 + index,
                                              slot=123 + index), "latest_tps")
            for index, at in enumerate((
                "2026-08-25T00:00:00+00:00", "2026-08-25T05:30:00+00:00",
                "2026-08-25T11:00:00+00:00",
            ))
        ]
        self.assertEqual(len(facts.cadence_eligible(items)), 3)

    def test_jsonl_append_is_idempotent_and_conflicts_before_writing(self):
        item = facts.fact_from_snapshot(snapshot(), "latest_tps")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.append_jsonl(path, [item]), 1)
            before = path.read_bytes()
            self.assertEqual(facts.append_jsonl(path, [item]), 0)
            with self.assertRaises(facts.FactConflictError):
                facts.append_jsonl(path, [dict(item, value=999.0)])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads(path.read_text()), item)

    def test_jsonl_append_rewrites_back_dated_rows_into_canonical_order(self):
        newer = facts.fact_from_snapshot(
            snapshot(at="2026-08-25T02:00:00+00:00", tps=102.0, slot=125), "latest_tps",
        )
        older = facts.fact_from_snapshot(
            snapshot(at="2026-08-25T01:00:00+00:00", tps=101.0, slot=124), "latest_tps",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.append_jsonl(path, [newer]), 1)
            self.assertEqual(facts.append_jsonl(path, [older]), 1)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["collected_at"] for row in rows], [
                "2026-08-25T01:00:00+00:00", "2026-08-25T02:00:00+00:00",
            ])

    def test_jsonl_additions_preflight_does_not_write(self):
        item = facts.fact_from_snapshot(snapshot(), "latest_tps")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.jsonl_additions(path, [item]), [item])
            self.assertFalse(path.exists())

    def test_jsonl_replace_failure_preserves_original_bytes(self):
        first = facts.fact_from_snapshot(snapshot(), "latest_tps")
        second = facts.fact_from_snapshot(
            snapshot(at="2026-08-25T01:00:00+00:00", tps=101.0), "latest_tps",
        )
        second["event_slot"] = 124
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            self.assertEqual(facts.append_jsonl(path, [first]), 1)
            before = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    facts.append_jsonl(path, [second])
            self.assertEqual(path.read_bytes(), before)

    def test_performance_fact_retains_exact_slot_period_and_counts(self):
        source = snapshot()
        source["performance"]["samples"] = [{
            "slot": 123, "tps": 100.0, "sample_period_secs": 60,
            "slots": 150, "transactions": 6000, "non_vote_transactions": 2400,
        }]
        item = facts.performance_sample_facts(source)[0]
        self.assertEqual(item["event_slot"], 123)
        self.assertIsNone(item["event_time"])
        self.assertEqual(item["coverage"]["sample_period_seconds"], 60)
        self.assertEqual(item["coverage"]["non_vote_transactions"], 2400)

    def test_commission_facts_are_vote_account_keyed_and_schema_seven_plus(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        item = facts.validator_commission_facts(source)[0]
        self.assertEqual(item["subject_id"], "vote-a")
        self.assertIsNone(item["event_slot"])
        self.assertEqual(item["coverage"]["snapshot_slot"], 999)
        self.assertIn("no source-native observation slot", item["quality"])
        self.assertEqual(item["coverage"]["identity"], "node-a")
        source["schema_version"] = 6
        self.assertEqual(facts.validator_commission_facts(source), [])

    def test_legacy_commission_slots_adapt_without_rewrite_or_duplicate(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        corrected = facts.validator_commission_facts(source)[0]
        legacy = dict(corrected, event_slot=999, quality=None,
                      coverage={"identity": "node-a", "vote_state": "current"})
        original = json.dumps(legacy, sort_keys=True)
        adapted = facts.adapt_fact(legacy)
        self.assertEqual(json.dumps(legacy, sort_keys=True), original)
        self.assertIsNone(adapted["event_slot"])
        self.assertEqual(adapted["coverage"]["snapshot_slot"], 999)
        self.assertEqual(adapted["coverage"]["fact_contract"],
                         facts.VALIDATOR_COMMISSION_FACT_CONTRACT)
        self.assertEqual(adapted["quality"], facts.VALIDATOR_COMMISSION_QUALITY)
        self.assertEqual(facts.fact_identity(adapted), facts.fact_identity(corrected))
        self.assertEqual(facts.dedupe_facts([legacy, corrected]), [corrected])
        self.assertTrue(facts.eligible(corrected))
        self.assertTrue(facts.eligible(legacy))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(facts.jsonl_additions(path, [corrected]), [])
            self.assertEqual(path.read_bytes(), before)

    def test_legacy_commission_slot_conflict_fails_closed(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        legacy = facts.validator_commission_facts(source)[0]
        legacy = dict(legacy, event_slot=999,
                      coverage={**legacy["coverage"], "snapshot_slot": 998})
        with self.assertRaises(facts.FactConflictError):
            facts.adapt_fact(legacy)

    def test_commission_adapter_refuses_unknown_or_invalid_legacy_semantics(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        corrected = facts.validator_commission_facts(source)[0]
        legacy = dict(corrected, event_slot=999, quality=None,
                      coverage={"identity": "node-a", "vote_state": "current"})

        provider = dict(legacy, source="provider-with-native-commission-slot",
                        quality="source-native slot")
        self.assertEqual(facts.adapt_fact(provider), provider)
        self.assertFalse(facts.eligible(provider))

        qualified = dict(legacy, quality="partial validator response")
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([legacy, qualified])

        negative = dict(legacy, event_slot=-1)
        self.assertFalse(facts.eligible(negative))
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([negative])

        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([dict(corrected, quality="partial validator response")])

    def test_commission_identity_and_cadence_return_canonical_facts(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        corrected = facts.validator_commission_facts(source)[0]
        legacy = dict(corrected, event_slot=999, quality=None,
                      coverage={"identity": "node-a", "vote_state": "current"})
        self.assertEqual(facts.fact_identity(legacy), facts.fact_identity(corrected))
        self.assertEqual(facts.cadence_eligible([legacy], minimum_seconds=0), [corrected])

    def test_canonical_commission_semantics_fail_closed(self):
        source = snapshot()
        source["network"] = {"slot": 999}
        source["validators"] = {"available": True, "all_validators": [{
            "identity": "node-a", "vote_account": "vote-a", "state": "current",
            "commission": 5,
        }]}
        corrected = facts.validator_commission_facts(source)[0]
        for field, value in (
            ("unit", "SOL"), ("basis", "estimated"), ("state", "partial"),
            ("value", -1.0), ("value", 101.0),
        ):
            with self.subTest(field=field, value=value):
                invalid = dict(corrected, **{field: value})
                self.assertFalse(facts.eligible(invalid))
                with self.assertRaises(facts.FactConflictError):
                    facts.dedupe_facts([invalid])

    def test_simd_lifecycle_facts_retain_explicit_status_and_pinned_revision(self):
        source = snapshot()
        source["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True, "partial": False, "source_commit": "a" * 40,
            "lifecycle_note": "explicit frontmatter only",
            "proposals": [{
                "identifier": "SIMD-0326", "name": "Alpenglow", "status": "Review",
                "created": "2025-07-25", "source": "https://example.com/0326",
                "source_path": "proposals/0326.md", "source_commit": "a" * 40,
                "basis": "recorded",
            }],
        }}}
        item = facts.simd_lifecycle_facts(source)[0]
        self.assertEqual(item["metric_id"], "simd_lifecycle_status")
        self.assertEqual(item["subject_id"], "SIMD-0326")
        self.assertEqual(item["event_time"], "2025-07-25")
        self.assertEqual(item["source_revision"], "a" * 40)
        self.assertEqual(item["coverage"]["status"], "Review")
        self.assertIsNone(item["value"])
        self.assertIn(item, facts.snapshot_facts(source))

    def test_provider_activity_facts_retain_each_provider_and_source_date(self):
        source = snapshot()
        source["growth"] = {
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "display_name": "Stablecoin active-address provider range",
                "source_label": "Active Addresses",
                "scope": "provider observations, not network-wide DAA",
                "source_url": "https://solana.com/data",
                "source_generated_at": "2026-08-23T11:50:17Z",
                "partial": False,
                "note": "Provider methodologies differ.",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                    {"date": "2026-08-21", "provider": "B", "value": 200},
                ],
            },
            "daily_fee_payers": {
                "available": False,
                "history_available": False,
                "semantic_metric_id": "transaction_initiator_provider_range",
                "provider_observations": [],
            },
        }
        packed = facts.provider_activity_facts(source)
        self.assertEqual([item["subject_id"] for item in packed], ["A", "B"])
        self.assertTrue(all(item["event_time"] == "2026-08-21" for item in packed))
        self.assertTrue(all(item["basis"] == "recorded" for item in packed))
        self.assertTrue(all(item["metric_id"] == "stablecoin_active_address_provider_range"
                            for item in packed))
        public_rows = [
            item for item in facts.public_observation_records(source)
            if item["metric_id"] == "stablecoin_active_address_provider_range"
        ]
        self.assertTrue(all(item["source_url"] == "https://solana.com/data"
                            for item in public_rows))
        self.assertTrue(all(item in facts.snapshot_facts(source) for item in packed))

    def test_provider_activity_revisions_append_instead_of_conflicting(self):
        """A revised provider row appends a distinct revision; the old value is retained."""
        source = snapshot()
        source["growth"] = {
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                ],
            },
        }
        first = facts.provider_activity_facts(source)[0]
        revised = facts.dedupe_facts([first, dict(first, value=101.0)])
        self.assertEqual(len(revised), 2)
        self.assertEqual(sorted(row["value"] for row in revised), [100.0, 101.0])

    def test_exact_provider_rerun_is_idempotent(self):
        source = snapshot()
        source["growth"] = {
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                ],
            },
        }
        first = facts.provider_activity_facts(source)[0]
        self.assertEqual(len(facts.dedupe_facts([first, dict(first)])), 1)
        # A later re-collection of the same observation stays one fact.
        self.assertEqual(len(facts.dedupe_facts([
            first, dict(first, collected_at="2026-08-25T23:00:00+00:00"),
        ])), 1)

    def test_provider_revisions_order_deterministically_by_collection(self):
        source = snapshot()
        source["growth"] = {
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                ],
            },
        }
        first = facts.provider_activity_facts(source)[0]
        older = dict(first, value=101.0, collected_at="2026-08-25T20:00:00+00:00")
        newer = dict(first, value=102.0, collected_at="2026-08-25T22:00:00+00:00")
        ordered = facts.dedupe_facts([newer, first, older])
        self.assertEqual([row["value"] for row in ordered], [100.0, 101.0, 102.0])
        again = facts.dedupe_facts([older, newer, first])
        self.assertEqual([row["value"] for row in again], [100.0, 101.0, 102.0])

    def test_non_revisionable_chain_fact_still_conflicts(self):
        source = snapshot()
        source["economics"] = {
            "available": True,
            "price": {"available": True, "price_usd": 105.0,
                      "last_updated_at_unix": 1_777_070_400, "freshness": "fresh"},
            "tvl": {"available": True, "tvl_usd": 5_900_000_000.0},
            "stablecoins": {
                "available": True,
                "usd_pegged_circulating_usd": 15_900_000_000.0,
            },
            "dex": {"available": True, "volume_24h_usd": 1_400_000_000.0},
        }
        price = next(
            row for row in facts.snapshot_facts(source)
            if row["metric_id"] == "price_usd"
        )
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([price, dict(price, value=106.0)])

    def test_provider_activity_facts_require_explicit_history_availability(self):
        source = snapshot()
        source["growth"] = {"available": False, "daily_active_addresses": {
            "semantic_metric_id": "stablecoin_active_address_provider_range",
            "provider_observations": [
                {"date": "2026-08-21", "provider": "A", "value": 123},
            ],
        }}

        self.assertEqual(facts.provider_activity_facts(source), [])

    def test_simd_status_transition_requires_a_new_pinned_revision(self):
        source = snapshot()
        proposal = {
            "identifier": "SIMD-0326", "name": "Alpenglow", "status": "Review",
            "created": None, "source": "https://example.com/0326",
            "source_path": "proposals/0326.md", "source_commit": "a" * 40,
            "basis": "recorded",
        }
        source["news"] = {"available": True, "sources": {"simd_proposals": {
            "available": True, "partial": False, "source_commit": "a" * 40,
            "proposals": [proposal],
        }}}
        first = facts.simd_lifecycle_facts(source)[0]
        repeated = dict(first, collected_at="2026-08-25T06:00:00+00:00")
        self.assertEqual(facts.dedupe_facts([first, repeated]), [first])
        with self.assertRaises(facts.FactConflictError):
            facts.dedupe_facts([first, dict(first, coverage={**first["coverage"], "status": "Accepted"})])
        changed = dict(
            repeated, source_revision="b" * 40,
            coverage={**first["coverage"], "status": "Accepted", "source_commit": "b" * 40},
        )
        self.assertEqual(len(facts.dedupe_facts([first, changed])), 2)


if __name__ == "__main__":
    unittest.main()
