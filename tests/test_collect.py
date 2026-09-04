"""Offline tests for the collection transforms.

No network. Every test feeds raw JSON-RPC-shaped input to a pure function, so
the whole suite runs on a plane, in CI, or with the RPC endpoint down.
"""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect  # noqa: E402


class TestRpcCollection(unittest.TestCase):
    def test_source_code_state_uses_exact_revision_and_dirty_flag(self):
        status = collect.subprocess.CompletedProcess(
            ["git", "status", "--porcelain"], 0, stdout=" M collect.py\n", stderr="",
        )
        with patch.dict(collect.os.environ, {"GITHUB_SHA": "b" * 40}), patch.object(
            collect.subprocess, "run", return_value=status,
        ) as run:
            self.assertEqual(collect.source_code_state(), {
                "source_revision": "b" * 40,
                "source_tree_dirty": True,
            })
        run.assert_called_once()

    def test_source_code_state_excludes_data_and_generated_outputs(self):
        status = collect.subprocess.CompletedProcess(
            ["git", "status", "--porcelain"], 0, stdout="", stderr="",
        )
        with patch.dict(collect.os.environ, {"GITHUB_SHA": "b" * 40}), patch.object(
            collect.subprocess, "run", return_value=status,
        ) as run:
            collect.source_code_state()

        command = run.call_args.args[0]
        for path in ("snapshots/**", "history/**", "state/**", "preview/**", "dist/**"):
            self.assertIn(f":(exclude){path}", command)

    def test_requests_methods_individually_and_keeps_partial_results(self):
        replies: list[object] = [
            {"jsonrpc": "2.0", "id": index, "result": "ok" if index == 0 else index}
            for index in range(len(collect.RPC_CALLS))
        ]
        replies[4] = collect.CollectionError("supply timed out")

        with patch.object(collect, "fetch_rpc_call", side_effect=replies) as fetch:
            batch = collect.fetch_rpc("https://rpc.example")

        self.assertEqual(fetch.call_count, len(collect.RPC_CALLS))
        self.assertEqual(batch[0]["result"], "ok")
        self.assertEqual(batch[4]["error"]["message"], "supply timed out")
        self.assertEqual(batch[5]["result"], 5)

    def test_raises_only_when_every_rpc_method_fails(self):
        with patch.object(
            collect,
            "fetch_rpc_call",
            side_effect=collect.CollectionError("endpoint unavailable"),
        ):
            with self.assertRaisesRegex(collect.CollectionError, "all RPC methods failed"):
                collect.fetch_rpc("https://rpc.example")


class TestIndexResults(unittest.TestCase):
    def test_maps_batch_positions_back_to_method_names(self):
        batch = [
            {"jsonrpc": "2.0", "id": 0, "result": "ok"},
            {"jsonrpc": "2.0", "id": 1, "result": 999},
        ]
        indexed = collect.index_results(batch)
        self.assertEqual(indexed["getHealth"], "ok")
        self.assertEqual(indexed["getSlot"], 999)

    def test_out_of_order_responses_still_map_correctly(self):
        # JSON-RPC does not guarantee response order; ids are the contract.
        batch = [
            {"jsonrpc": "2.0", "id": 1, "result": 42},
            {"jsonrpc": "2.0", "id": 0, "result": "ok"},
        ]
        indexed = collect.index_results(batch)
        self.assertEqual(indexed["getSlot"], 42)
        self.assertEqual(indexed["getHealth"], "ok")

    def test_error_entries_are_kept_not_dropped(self):
        batch = [{"jsonrpc": "2.0", "id": 0, "error": {"code": -32005, "message": "node unhealthy"}}]
        indexed = collect.index_results(batch)
        self.assertEqual(indexed["getHealth"]["code"], -32005)

    def test_ignores_junk_entries_without_raising(self):
        batch = ["not a dict", {"id": 99, "result": "out of range"}, {"no_id": True}]
        self.assertEqual(collect.index_results(batch), {})

    def test_negative_and_out_of_range_ids_cannot_poison_another_method(self):
        indexed = collect.index_results([
            {"jsonrpc": "2.0", "id": -1, "result": {"poison": True}},
            {"jsonrpc": "2.0", "id": 99, "result": {"poison": True}},
            {"jsonrpc": "2.0", "id": 0, "result": "ok"},
        ])
        self.assertEqual(indexed, {"getHealth": "ok"})


class TestPerformance(unittest.TestCase):
    def test_derives_tps_and_slot_time(self):
        summary = collect.summarize_performance([
            {"slot": 100, "numTransactions": 12000, "numNonVoteTransactions": 9000,
             "numSlots": 150, "samplePeriodSecs": 60},
            {"slot": 90, "numTransactions": 6000, "numNonVoteTransactions": 3000,
             "numSlots": 150, "samplePeriodSecs": 60},
        ])
        self.assertTrue(summary["available"])
        self.assertEqual(summary["latest_tps"], 200.0)
        self.assertEqual(summary["mean_tps"], 150.0)
        self.assertEqual(summary["peak_tps"], 200.0)
        self.assertEqual(summary["mean_slot_time_secs"], 0.4)
        self.assertTrue(summary["non_vote_available"])
        self.assertEqual(summary["latest_non_vote_tps"], 150.0)
        self.assertEqual(summary["mean_non_vote_tps"], 100.0)
        self.assertEqual(summary["peak_non_vote_tps"], 150.0)
        self.assertEqual(summary["mean_vote_share_pct"], 33.33)
        self.assertEqual(summary["samples"][0]["non_vote_tps"], 150.0)
        self.assertEqual(summary["samples"][0]["vote_tps"], 50.0)

    def test_missing_non_vote_counts_preserve_total_tps_without_inventing_split(self):
        summary = collect.summarize_performance([
            {"slot": 100, "numTransactions": 12000, "numSlots": 150, "samplePeriodSecs": 60},
        ])
        self.assertTrue(summary["available"])
        self.assertFalse(summary["non_vote_available"])
        self.assertIsNone(summary["latest_non_vote_tps"])
        self.assertIsNone(summary["samples"][0]["non_vote_tps"])

    def test_multi_sample_rollups_weight_transaction_counts_by_observed_seconds(self):
        summary = collect.summarize_performance([
            {"slot": 101, "numTransactions": 6000, "numNonVoteTransactions": 3000,
             "numSlots": 150, "samplePeriodSecs": 60},
            {"slot": 100, "numTransactions": 3000, "numNonVoteTransactions": 1200,
             "numSlots": 50, "samplePeriodSecs": 30},
        ])
        self.assertEqual(summary["mean_tps"], 100.0)
        self.assertEqual(summary["mean_non_vote_tps"], 46.67)
        self.assertEqual(summary["mean_vote_share_pct"], 53.33)
        self.assertEqual(summary["sample_period_seconds"], 90)
        self.assertEqual(summary["samples"][0]["transactions"], 6000)
        self.assertEqual(summary["samples"][0]["non_vote_transactions"], 3000)
        self.assertEqual(summary["samples"][0]["sample_period_secs"], 60)

    def test_missing_samples_report_unavailable_rather_than_zero(self):
        # Zero TPS and "we don't know" are different claims.
        for bad in ([], None, "nope", {}):
            self.assertFalse(collect.summarize_performance(bad)["available"])

    def test_malformed_or_nonfinite_samples_degrade_instead_of_crashing(self):
        bad_samples = [
            {"numTransactions": "6000", "numSlots": 150, "samplePeriodSecs": 60},
            {"numTransactions": 6000, "numSlots": 150, "samplePeriodSecs": -60},
            {"numTransactions": float("nan"), "numSlots": 150, "samplePeriodSecs": 60},
            {"numTransactions": 6000, "numSlots": float("inf"), "samplePeriodSecs": 60},
        ]
        self.assertFalse(collect.summarize_performance(bad_samples)["available"])

    def test_skips_samples_that_would_divide_by_zero(self):
        summary = collect.summarize_performance([
            {"slot": 1, "numTransactions": 100, "numSlots": 0, "samplePeriodSecs": 60},
            {"slot": 2, "numTransactions": 100, "numSlots": 10, "samplePeriodSecs": 0},
            {"slot": 3, "numTransactions": 6000, "numSlots": 150, "samplePeriodSecs": 60},
        ])
        self.assertEqual(summary["samples_used"], 1)
        self.assertEqual(summary["latest_tps"], 100.0)


class TestValidators(unittest.TestCase):
    def sample(self):
        return {
            "current": [
                {"nodePubkey": "A", "activatedStake": 5_000_000_000, "commission": 5},
                {"nodePubkey": "B", "activatedStake": 3_000_000_000, "commission": 10},
                {"nodePubkey": "C", "activatedStake": 2_000_000_000, "commission": 0},
            ],
            "delinquent": [
                {"nodePubkey": "D", "activatedStake": 1_000_000_000, "commission": 100},
            ],
        }

    def test_counts_and_converts_lamports_to_sol(self):
        summary = collect.summarize_validators(self.sample())
        self.assertEqual(summary["active_count"], 3)
        self.assertEqual(summary["delinquent_count"], 1)
        self.assertEqual(summary["active_stake_sol"], 10.0)
        self.assertEqual(summary["delinquent_stake_sol"], 1.0)
        self.assertEqual(summary["delinquent_pct"], 25.0)

    def test_ranks_top_validators_by_stake(self):
        summary = collect.summarize_validators(self.sample())
        self.assertEqual([v["identity"] for v in summary["top_validators"]], ["A", "B", "C"])
        self.assertEqual(summary["top_validators"][0]["share_pct"], 50.0)

    def test_validator_rows_preserve_vote_account_state_and_vote_freshness(self):
        raw = self.sample()
        raw["current"][0].update({"votePubkey": "vote-A", "lastVote": 123, "rootSlot": 120})
        raw["delinquent"][0].update({"votePubkey": "vote-D", "lastVote": 99, "rootSlot": 95})
        summary = collect.summarize_validators(raw, top_n=30)
        current = next(row for row in summary["ranked_validators"] if row["identity"] == "A")
        delinquent = next(row for row in summary["ranked_validators"] if row["identity"] == "D")
        self.assertEqual(current["vote_account"], "vote-A")
        self.assertEqual(current["state"], "current")
        self.assertEqual(current["last_vote"], 123)
        self.assertEqual(current["root_slot"], 120)
        self.assertEqual(delinquent["vote_account"], "vote-D")
        self.assertEqual(delinquent["state"], "delinquent")
        self.assertEqual(delinquent["last_vote"], 99)
        self.assertEqual(summary["ranked_validator_limit"], 30)
        self.assertEqual(len(summary["top_delinquent"]), 1)

    def test_missing_validator_stake_remains_unavailable_and_sorts_last(self):
        raw = {
            "current": [
                {"nodePubkey": "B", "votePubkey": "vote-B", "activatedStake": None, "commission": 5},
                {"nodePubkey": "A", "votePubkey": "vote-A", "activatedStake": 2_000_000_000, "commission": 5},
                {"nodePubkey": "C", "votePubkey": "vote-C", "commission": 5},
            ],
            "delinquent": [],
        }
        summary = collect.summarize_validators(raw)
        self.assertEqual([row["identity"] for row in summary["ranked_validators"]], ["A", "B", "C"])
        self.assertEqual(summary["accounts_missing_stake"], 2)
        self.assertIsNone(summary["ranked_validators"][1]["stake_lamports"])
        self.assertIsNone(summary["ranked_validators"][1]["stake_sol"])
        self.assertIsNone(summary["ranked_validators"][1]["share_pct"])

    def test_nakamoto_coefficient_counts_validators_past_one_third(self):
        # A alone holds 50% of 10 SOL, which already exceeds 33.3%.
        self.assertEqual(collect.summarize_validators(self.sample())["nakamoto_coefficient"], 1)

    def test_nakamoto_coefficient_with_even_distribution(self):
        even = {"current": [{"nodePubkey": str(i), "activatedStake": 10**9} for i in range(9)],
                "delinquent": []}
        # Nine equal validators: four are needed to pass one third.
        self.assertEqual(collect.summarize_validators(even)["nakamoto_coefficient"], 4)

    def test_top_n_is_respected(self):
        many = {"current": [{"nodePubkey": str(i), "activatedStake": (100 - i) * 10**9} for i in range(50)],
                "delinquent": []}
        summary = collect.summarize_validators(many, top_n=10)
        self.assertEqual(len(summary["top_validators"]), 10)
        self.assertEqual(len(summary["ranked_validators"]), 10)
        self.assertEqual(len(summary["all_validators"]), 50)
        self.assertEqual(summary["all_validator_count"], 50)

    def test_empty_validator_set_does_not_divide_by_zero(self):
        summary = collect.summarize_validators({"current": [], "delinquent": []})
        self.assertTrue(summary["available"])
        self.assertEqual(summary["active_stake_sol"], 0)
        self.assertEqual(summary["delinquent_pct"], 0.0)
        self.assertEqual(summary["nakamoto_coefficient"], 0)

    def test_malformed_input_reports_unavailable(self):
        for bad in (None, [], "nope", {"current": "not a list", "delinquent": []}):
            self.assertFalse(collect.summarize_validators(bad)["available"])

    def test_missing_vote_account_arrays_are_unavailable_not_an_empty_network(self):
        for bad in ({}, {"current": []}, {"delinquent": []}):
            self.assertFalse(collect.summarize_validators(bad)["available"])

    def test_commission_stats_cover_the_whole_active_set(self):
        # Not just the top ten — the brief asks for commission tracking.
        many = {"current": [{"nodePubkey": str(i), "activatedStake": 10**9,
                             "commission": c}
                            for i, c in enumerate([0, 0, 5, 5, 10, 100])],
                "delinquent": []}
        stats = collect.summarize_validators(many)["commission"]
        self.assertTrue(stats["available"])
        self.assertEqual(stats["median_pct"], 5)
        self.assertEqual(stats["zero_commission_count"], 2)
        self.assertEqual(stats["max_commission_count"], 1)

    def test_commission_unavailable_when_no_validator_reports_one(self):
        bare = {"current": [{"nodePubkey": "a", "activatedStake": 10**9}], "delinquent": []}
        self.assertFalse(collect.summarize_validators(bare)["commission"]["available"])


class TestEpochAndSupply(unittest.TestCase):
    def test_epoch_progress_percentage(self):
        summary = collect.summarize_epoch({
            "epoch": 700, "slotIndex": 216_000, "slotsInEpoch": 432_000,
            "absoluteSlot": 302_400_000, "blockHeight": 280_000_000,
        })
        self.assertEqual(summary["epoch"], 700)
        self.assertEqual(summary["progress_pct"], 50.0)

    def test_epoch_without_slot_counts_reports_none_progress(self):
        summary = collect.summarize_epoch({"epoch": 700})
        self.assertTrue(summary["available"])
        self.assertIsNone(summary["progress_pct"])

    def test_snapshot_estimates_epoch_remaining_from_recorded_slot_cadence(self):
        indexed = {
            "getEpochInfo": {
                "epoch": 700, "slotIndex": 216_000, "slotsInEpoch": 432_000,
                "absoluteSlot": 302_400_000, "blockHeight": 280_000_000,
            },
            "getRecentPerformanceSamples": [
                {"slot": 100, "numTransactions": 6000, "numNonVoteTransactions": 3000,
                 "numSlots": 150, "samplePeriodSecs": 60},
            ],
        }
        snapshot = collect.build_snapshot(
            indexed, "2026-08-05T00:00:00+00:00", "endpoint",
        )
        self.assertEqual(snapshot["epoch"]["estimated_remaining_seconds"], 86_400)
        self.assertEqual(snapshot["epoch"]["estimated_end_at"], "2026-08-06T00:00:00+00:00")
        self.assertEqual(snapshot["epoch"]["eta_basis"], "median recent slot time")
        self.assertEqual(snapshot["epoch"]["slot_time_statistic"], "median")

    def test_epoch_eta_uses_median_slot_cadence_not_outlier_skewed_mean(self):
        indexed = {
            "getEpochInfo": {"epoch": 700, "slotIndex": 431_900, "slotsInEpoch": 432_000},
            "getRecentPerformanceSamples": [
                {"slot": 103, "numTransactions": 6000, "numNonVoteTransactions": 3000,
                 "numSlots": 150, "samplePeriodSecs": 60},
                {"slot": 102, "numTransactions": 6000, "numNonVoteTransactions": 3000,
                 "numSlots": 150, "samplePeriodSecs": 60},
                {"slot": 101, "numTransactions": 6000, "numNonVoteTransactions": 3000,
                 "numSlots": 30, "samplePeriodSecs": 60},
            ],
        }
        snapshot = collect.build_snapshot(indexed, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertEqual(snapshot["epoch"]["recent_median_slot_time_secs"], 0.4)
        self.assertEqual(snapshot["epoch"]["estimated_remaining_seconds"], 40)

    def test_supply_converts_and_computes_share(self):
        summary = collect.summarize_supply({"value": {
            "total": 600_000_000 * 10**9,
            "circulating": 480_000_000 * 10**9,
            "nonCirculating": 120_000_000 * 10**9,
        }})
        self.assertEqual(summary["total_sol"], 600_000_000.0)
        self.assertEqual(summary["circulating_pct"], 80.0)

    def test_supply_missing_value_reports_unavailable(self):
        for bad in (None, {}, {"value": "nope"}, {"value": {}}, {"value": {"total": 1}}):
            self.assertFalse(collect.summarize_supply(bad)["available"])


class TestBuildSnapshot(unittest.TestCase):
    def test_custom_rpc_credentials_are_replaced_by_opaque_public_provenance(self):
        endpoint = "https://user:password@rpc.example/v2/secret?api-key=SUPERSECRET"
        activity = {
            "available": True,
            "requires_api_key": False,
            "source": {
                "endpoint": endpoint,
                "method": "getBlock (transactionDetails=accounts)",
            },
        }

        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", endpoint, activity=activity,
        )

        self.assertEqual(snapshot["source"]["endpoint"], "custom RPC endpoint")
        self.assertEqual(
            snapshot["source"]["endpoint_identity"],
            collect.growth_module.rpc_endpoint_identity(endpoint),
        )
        self.assertIsNone(snapshot["source"]["requires_api_key"])
        self.assertEqual(snapshot["activity"]["source"], {
            "endpoint": "custom RPC endpoint",
            "endpoint_identity": collect.growth_module.rpc_endpoint_identity(endpoint),
            "method": "getBlock (transactionDetails=accounts)",
        })
        self.assertIsNone(snapshot["activity"]["requires_api_key"])
        self.assertEqual(activity["source"]["endpoint"], endpoint)
        self.assertNotIn("SUPERSECRET", json.dumps(snapshot))
        self.assertNotIn("password", json.dumps(snapshot))

    def test_default_public_rpc_endpoint_label_is_preserved(self):
        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", collect.DEFAULT_ENDPOINT,
        )

        self.assertEqual(snapshot["source"]["endpoint"], collect.DEFAULT_ENDPOINT)
        self.assertEqual(
            snapshot["source"]["endpoint_identity"],
            collect.growth_module.rpc_endpoint_identity(collect.DEFAULT_ENDPOINT),
        )
        self.assertFalse(snapshot["source"]["requires_api_key"])

    def test_selected_stablecoin_evidence_survives_snapshot_assembly_unchanged(self):
        selected = {
            "metric_id": "selected_usd_stablecoin_total_supply",
            "state": "partial",
            "coverage_numerator": 1,
            "coverage_denominator": 4,
            "assets": [{
                "symbol": "USDC",
                "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "raw_amount": "100",
                "decimals": 2,
                "rpc_ui_amount_string": "1",
            }],
        }
        growth = {"available": True, "selected_usd_stablecoins": selected}

        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", "https://rpc.example",
            growth=growth,
        )

        self.assertEqual(snapshot["schema_version"], 9)
        self.assertIs(snapshot["growth"], growth)
        self.assertEqual(snapshot["growth"]["selected_usd_stablecoins"], selected)

    def test_preserves_collection_source_revision_and_dirty_state(self):
        provenance = {"source_revision": "c" * 40, "source_tree_dirty": True}
        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", "endpoint", provenance=provenance,
        )
        self.assertEqual(snapshot["provenance"], provenance)

    def test_completed_epoch_production_is_attached_to_validators(self):
        production = {"available": True, "epoch": 799}
        snapshot = collect.build_snapshot(
            {"getVoteAccounts": {"current": [], "delinquent": []}},
            "2026-08-05T00:00:00+00:00", "endpoint",
            block_production=production,
        )
        self.assertEqual(snapshot["validators"]["block_production"], production)

    def test_inflation_rate_and_governor_are_recorded_from_native_rpc(self):
        snapshot = collect.build_snapshot({
            "getInflationRate": {
                "total": 0.0412, "validator": 0.0412, "foundation": 0.0, "epoch": 1021,
            },
            "getInflationGovernor": {
                "initial": 0.08, "terminal": 0.015, "taper": 0.15,
                "foundation": 0.05, "foundationTerm": 7.0,
            },
        }, "2026-08-05T00:00:00+00:00", "endpoint")
        inflation = snapshot["inflation"]
        self.assertTrue(inflation["available"])
        self.assertEqual(inflation["current_total_pct"], 4.12)
        self.assertEqual(inflation["current_validator_pct"], 4.12)
        self.assertEqual(inflation["epoch"], 1021)
        self.assertEqual(inflation["initial_pct"], 8.0)
        self.assertEqual(inflation["terminal_pct"], 1.5)
        self.assertEqual(inflation["taper_pct"], 15.0)
        self.assertEqual(inflation["foundation_term_years"], 7.0)

    def test_inflation_degrades_without_converting_missing_fields_to_zero(self):
        summary = collect.summarize_inflation({"total": "bad"}, None)
        self.assertFalse(summary["available"])
        self.assertIsNone(summary["current_total_pct"])
        self.assertIsNone(summary["terminal_pct"])

    def test_healthy_only_when_rpc_says_ok(self):
        healthy = collect.build_snapshot({"getHealth": "ok", "getSlot": 5}, "2026-08-05T00:00:00+00:00", "u")
        self.assertTrue(healthy["network"]["healthy"])
        self.assertEqual(healthy["network"]["health_scope"], "rpc_endpoint")
        self.assertEqual(healthy["network"]["health_method"], "getHealth")

        # An error object must never be read as healthy.
        unhealthy = collect.build_snapshot(
            {"getHealth": {"code": -32005}, "getSlot": 5}, "2026-08-05T00:00:00+00:00", "u",
        )
        self.assertFalse(unhealthy["network"]["healthy"])
        self.assertEqual(unhealthy["network"]["health_raw"], "unhealthy")

    def test_missing_or_transport_failed_health_is_unavailable_not_unhealthy(self):
        missing = collect.build_snapshot({}, "2026-08-05T00:00:00+00:00", "u")
        timed_out = collect.build_snapshot(
            {"getHealth": {"code": -1, "message": "timed out"}},
            "2026-08-05T00:00:00+00:00",
            "u",
        )
        self.assertIsNone(missing["network"]["healthy"])
        self.assertEqual(missing["network"]["health_raw"], "unavailable")
        self.assertEqual(missing["network"]["health_scope"], "rpc_endpoint")
        self.assertEqual(missing["network"]["health_method"], "getHealth")
        self.assertIsNone(timed_out["network"]["healthy"])
        self.assertEqual(timed_out["network"]["health_raw"], "unavailable")

    def test_snapshot_is_json_serializable_and_stamped(self):
        import json
        snapshot = collect.build_snapshot({"getHealth": "ok", "getSlot": 1}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertEqual(snapshot["schema_version"], collect.SCHEMA_VERSION)
        self.assertFalse(snapshot["source"]["requires_api_key"])
        json.dumps(snapshot)  # must not raise

    def test_totally_empty_rpc_response_still_produces_a_valid_snapshot(self):
        # A degraded snapshot beats a crash — the report should say "unavailable".
        snapshot = collect.build_snapshot({}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertIsNone(snapshot["network"]["healthy"])
        self.assertIsNone(snapshot["network"]["slot"])
        for section in ("epoch", "performance", "supply", "validators"):
            self.assertFalse(snapshot[section]["available"])

    def test_activity_last_known_good_is_carried_forward_with_explicit_staleness(self):
        current = {"collected_at": "2026-08-05T01:00:00+00:00", "activity": {"available": False}}
        previous = {"collected_at": "2026-08-05T00:00:00+00:00",
                    "activity": {"available": True, "window": {"blocks_sampled": 8}}}
        result = collect.apply_activity_last_known_good(current, previous)
        self.assertTrue(result["activity"]["available"])
        self.assertTrue(result["activity"]["stale"])
        self.assertEqual(result["activity"]["source_state"], "last_known_good")
        self.assertEqual(result["activity"]["last_success_at"], previous["collected_at"])
        self.assertEqual(result["activity"]["age_seconds"], 3_600)

    def test_activity_last_known_good_stops_at_the_publication_freshness_limit(self):
        previous = {"collected_at": "2026-08-05T00:00:00+00:00",
                    "activity": {"available": True, "window": {"blocks_sampled": 8}}}
        boundary = {"collected_at": "2026-08-05T07:00:00+00:00",
                    "activity": {"available": False}}
        expired = {"collected_at": "2026-08-05T07:00:01+00:00",
                   "activity": {"available": False}}

        carried = collect.apply_activity_last_known_good(boundary, previous)
        withheld = collect.apply_activity_last_known_good(expired, previous)

        self.assertEqual(collect.PUBLICATION_FRESHNESS_SECONDS, 25_200)
        self.assertTrue(carried["activity"]["available"])
        self.assertEqual(carried["activity"]["age_seconds"], 25_200)
        self.assertEqual(withheld["activity"], {"available": False})

    def test_fresh_activity_is_never_replaced_by_previous_data(self):
        current = {"collected_at": "2026-08-05T01:00:00+00:00",
                   "activity": {"available": True, "window": {"blocks_sampled": 4}}}
        previous = {"collected_at": "2026-08-05T00:00:00+00:00",
                    "activity": {"available": True, "window": {"blocks_sampled": 8}}}
        result = collect.apply_activity_last_known_good(current, previous)
        self.assertEqual(result["activity"]["window"]["blocks_sampled"], 4)
        self.assertFalse(result["activity"]["stale"])
        self.assertEqual(result["activity"]["source_state"], "fresh")

    def test_successful_collection_keeps_source_event_age(self):
        current = {"collected_at": "2026-08-05T00:00:00+00:00",
                   "activity": {"available": True,
                                "window": {"last_block_time": 1_785_801_600}}}
        result = collect.apply_activity_last_known_good(current, None)
        self.assertEqual(result["activity"]["age_seconds"], 86_400)
        self.assertTrue(result["activity"]["stale"])
        self.assertEqual(result["activity"]["source_state"], "stale")
        self.assertEqual(result["activity"]["last_success_at"], current["collected_at"])

    def test_recent_retrieval_does_not_extend_expired_block_evidence(self):
        previous = {"collected_at": "2026-08-05T07:00:00+00:00",
                    "activity": {"available": True,
                                 "window": {"last_block_time": 1_785_888_000}}}
        current = {"collected_at": "2026-08-05T08:00:00+00:00", "activity": {"available": False}}
        result = collect.apply_activity_last_known_good(current, previous)
        self.assertEqual(result["activity"], {"available": False})

    def test_carried_evidence_age_and_retrieval_time_remain_distinct(self):
        previous = {"collected_at": "2026-08-05T01:00:00+00:00",
                    "activity": {"available": True,
                                 "window": {"last_block_time": 1_785_888_000}}}
        current = {"collected_at": "2026-08-05T05:00:00+00:00", "activity": {"available": False}}
        result = collect.apply_activity_last_known_good(current, previous)
        self.assertEqual(result["activity"]["age_seconds"], 18_000)
        self.assertEqual(result["activity"]["last_success_at"], previous["collected_at"])
        self.assertEqual(result["activity"]["source_state"], "last_known_good")


class TestNewsSection(unittest.TestCase):
    """The feeds are recorded into the snapshot, on the same terms as economics."""

    def test_a_snapshot_without_feeds_records_the_section_as_unavailable(self):
        snapshot = collect.build_snapshot({}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertFalse(snapshot["news"]["available"])

    def test_supplied_feeds_are_recorded_verbatim_for_offline_re_rendering(self):
        feeds = {"available": True, "sources": {"agave_releases": {"available": True,
                                                                   "items": [{"title": "Release v4.2.0"}]}}}
        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", "endpoint", news=feeds)
        self.assertEqual(snapshot["news"], feeds)

    def test_the_schema_version_moved_for_the_added_section(self):
        # Additive only: an older snapshot must still render.
        self.assertEqual(collect.SCHEMA_VERSION, 9)

    def test_editorial_items_are_stamped_with_the_snapshot_time(self):
        feeds = {
            "available": True,
            "featured_item_id": "github-release:1",
            "items": [{
                "id": "github-release:1",
                "source_id": "agave_releases",
                "publisher": "Anza",
                "category": "release",
                "title": "v4.2.0",
                "canonical_url": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0",
                "published_at": "2026-08-04T00:00:00Z",
                "recorded_at": None,
                "state": "recorded",
                "editorial_note": "Recorded release.",
                "art_seed": "github-release:1",
            }],
        }
        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", "endpoint", news=feeds,
        )
        self.assertEqual(
            snapshot["news"]["items"][0]["recorded_at"],
            "2026-08-05T00:00:00+00:00",
        )
        self.assertIsNone(feeds["items"][0]["recorded_at"])


class TestCompletedEpochProductionCollection(unittest.TestCase):
    @patch.object(collect.blocks, "normalize_block_production")
    @patch.object(collect.blocks, "fetch_block_production")
    @patch.object(collect.blocks, "completed_epoch_range")
    @patch.object(collect, "fetch_block_time", return_value=None)
    @patch.object(collect, "index_results")
    @patch.object(collect, "fetch_rpc", return_value=[])
    def test_sources_fetches_and_normalizes_the_previous_completed_epoch(
        self, _fetch_rpc, index_results, _block_time, completed_range,
        fetch_production, normalize_production,
    ):
        indexed = {
            "getSlot": 20,
            "getEpochInfo": {"epoch": 800},
            "getEpochSchedule": {"slotsPerEpoch": 432_000},
            "getVoteAccounts": {"current": [], "delinquent": []},
        }
        epoch_range = {"available": True, "epoch": 799}
        raw_production = {"value": {}}
        normalized = {"available": True, "epoch": 799}
        index_results.return_value = indexed
        completed_range.return_value = epoch_range
        fetch_production.return_value = raw_production
        normalize_production.return_value = normalized

        indexed["getClusterNodes"] = [{"pubkey": "1" * 32, "version": "3.0.0"}]
        feature_evidence = {"available": False}
        with patch("collect.feature_accounts.collect_feature_accounts", return_value=feature_evidence):
            raw = collect.sources(
                "rpc", with_economics=False, with_activity=False,
                with_news=False, with_growth=False,
            )
        self.assertEqual(raw["feature_activation"], feature_evidence)
        self.assertEqual(raw["cluster_software"]["observed_node_count"], 1)
        self.assertEqual(raw["cluster_software"]["versions"][0]["version"], "3.0.0")
        self.assertEqual(collect.RPC_CALLS[-1], ("getClusterNodes", []))

        completed_range.assert_called_once_with(
            indexed["getEpochInfo"], indexed["getEpochSchedule"],
        )
        fetch_production.assert_called_once_with(epoch_range, "rpc")
        self.assertEqual(normalize_production.call_args.args[:3], (
            raw_production, indexed["getVoteAccounts"], epoch_range,
        ))
        self.assertEqual(raw["block_production"], normalized)


class TestGrowthSection(unittest.TestCase):
    def test_feature_activation_is_additive_and_preserved_by_normalization(self):
        evidence = {"available": True, "coverage_numerator": 10}
        raw = {"indexed": {}, "collected_at": "2026-09-04T12:00:00+00:00", "endpoint": "rpc",
               "feature_activation": evidence}
        self.assertEqual(collect.normalize(raw)["feature_activation"], evidence)
        del raw["feature_activation"]
        self.assertNotIn("feature_activation", collect.normalize(raw))

    def test_growth_section_is_additive_and_defaults_to_unavailable(self):
        snapshot = collect.build_snapshot({}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertEqual(snapshot["growth"], {"available": False})

    def test_growth_section_preserves_tokenized_equity_and_daily_address_truth(self):
        growth = {
            "available": True,
            "tokenized_equities": {"available": True, "registry_asset_count": 2},
            "daily_active_addresses": {"available": False, "reason": "source not proven"},
        }
        snapshot = collect.build_snapshot(
            {}, "2026-08-05T00:00:00+00:00", "endpoint", growth=growth,
        )
        self.assertEqual(snapshot["growth"], growth)


class TestGrowthSupplyStateLifecycle(unittest.TestCase):
    def snapshot(self):
        return {
            "schema_version": collect.SCHEMA_VERSION,
            "collected_at": "2026-08-25T12:00:00+00:00",
            "activity": {"available": False},
            "growth": {"available": True},
        }

    def state(self):
        return {
            "version": 1,
            "cursor_mint": "mint-a",
            "updated_at": "2026-08-25T12:00:00+00:00",
            "observations": {},
        }

    def test_python_collection_apis_default_to_rights_safe_economics_off(self):
        for function in (collect.sources, collect.collect, collect.collect_with_state):
            self.assertFalse(inspect.signature(function).parameters[
                "with_economics"
            ].default)

    def test_collect_with_state_keeps_cursor_out_of_the_snapshot(self):
        raw = {
            "indexed": {}, "collected_at": "2026-08-25T12:00:00+00:00",
            "endpoint": "rpc", "growth": {"available": True},
            "_growth_supply_state": self.state(),
        }
        with patch("collect.sources", return_value=raw), \
             patch("collect.normalize", return_value=self.snapshot()), \
             patch("collect.pipeline.validate", side_effect=lambda value: value):
            snapshot, state = collect.collect_with_state("rpc", supply_state={})
        self.assertEqual(state, self.state())
        self.assertNotIn("_growth_supply_state", snapshot)

    def test_dry_run_reads_but_never_writes_supply_state_or_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            facts_path = Path(directory) / "history" / "facts.jsonl"
            with patch.object(sys, "argv", [
                "collect.py", "--dry-run", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ) as load, patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ) as collector, patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch.object(
                collect.facts, "append_jsonl",
            ) as append_facts, patch("builtins.print"):
                self.assertEqual(collect.main(), 0)
            load.assert_called_once_with()
            self.assertFalse(collector.call_args.kwargs["with_economics"])
            save.assert_not_called()
            append_facts.assert_not_called()
            self.assertFalse(out_dir.exists())
            self.assertFalse(facts_path.exists())

    def test_cli_growth_state_path_is_used_for_load_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            facts_path = Path(directory) / "history" / "facts.jsonl"
            state_path = Path(directory) / "state" / "growth.json"
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
                "--growth-state-path", str(state_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ) as load, patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 0)
            load.assert_called_once_with(state_path)
            save.assert_called_once_with(self.state(), state_path)

    def test_held_economics_requires_explicit_cli_opt_in(self):
        with patch.object(sys, "argv", [
            "collect.py", "--dry-run", "--no-growth", "--with-economics",
        ]), patch.object(
            collect, "collect_with_state", return_value=(self.snapshot(), None),
        ) as collector, patch.object(
            collect.pipeline, "check_publishable",
            return_value={"publishable": True, "failures": []},
        ), patch.object(
            collect.facts, "snapshot_facts", return_value=[],
        ), patch.object(
            collect.facts, "jsonl_additions", return_value=[],
        ), patch("builtins.print"):
            self.assertEqual(collect.main(), 0)
        self.assertTrue(collector.call_args.kwargs["with_economics"])

    def test_held_economics_cli_opt_in_cannot_write_canonical_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            facts_path = Path(directory) / "history" / "facts.jsonl"
            with patch.object(sys, "argv", [
                "collect.py", "--with-economics", "--no-growth",
                "--out-dir", str(out_dir), "--facts-path", str(facts_path),
            ]), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), None),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "snapshot_facts", return_value=[],
            ), patch.object(
                collect.facts, "jsonl_additions", return_value=[],
            ), patch.object(
                collect.facts, "append_jsonl", return_value=0,
            ), patch("builtins.print"):
                self.assertEqual(collect.main(), 2)
            self.assertFalse(out_dir.exists())
            self.assertFalse(facts_path.exists())

    def test_success_publishes_latest_only_after_facts_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            facts_path = Path(directory) / "history" / "facts.jsonl"
            saved = []

            def save_before_latest(state):
                self.assertFalse((out_dir / "latest.json").exists())
                self.assertTrue((out_dir / collect.snapshot_filename(
                    self.snapshot()["collected_at"])).exists())
                self.assertTrue(facts_path.exists())
                saved.append(state)

            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.growth_module, "save_supply_state", side_effect=save_before_latest,
            ), patch("builtins.print"):
                self.assertEqual(collect.main(), 0)
            self.assertEqual(saved, [self.state()])
            self.assertTrue((out_dir / "latest.json").exists())
            self.assertTrue(facts_path.exists())

    def test_fact_write_failure_leaves_existing_latest_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            out_dir.mkdir(parents=True)
            latest = out_dir / "latest.json"
            before = b'{"collected_at":"2026-08-25T06:00:00+00:00"}\n'
            latest.write_bytes(before)
            facts_path = Path(directory) / "history" / "facts.jsonl"
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "append_jsonl", side_effect=OSError("disk full"),
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertEqual(latest.read_bytes(), before)
            save.assert_not_called()
            self.assertTrue((out_dir / collect.snapshot_filename(
                self.snapshot()["collected_at"])).exists())

    def test_state_write_failure_leaves_existing_latest_byte_identical(self):
        for error in (OSError("disk full"), ValueError("invalid state")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                out_dir = Path(directory) / "snapshots"
                out_dir.mkdir(parents=True)
                latest = out_dir / "latest.json"
                before = b'{"collected_at":"2026-08-25T06:00:00+00:00"}\n'
                latest.write_bytes(before)
                facts_path = Path(directory) / "history" / "facts.jsonl"
                with patch.object(sys, "argv", [
                    "collect.py", "--out-dir", str(out_dir),
                    "--facts-path", str(facts_path),
                ]), patch.object(
                    collect.growth_module, "load_supply_state", return_value=self.state(),
                ), patch.object(
                    collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
                ), patch.object(
                    collect.pipeline, "check_publishable",
                    return_value={"publishable": True, "failures": []},
                ), patch.object(
                    collect.facts, "append_jsonl", return_value=1,
                ), patch.object(
                    collect.growth_module, "save_supply_state", side_effect=error,
                ), patch("builtins.print"):
                    self.assertEqual(collect.main(), 1)
                self.assertEqual(latest.read_bytes(), before)
                self.assertTrue((out_dir / collect.snapshot_filename(
                    self.snapshot()["collected_at"])).exists())

    def test_immutable_snapshot_collision_fails_before_fact_or_state_write(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            out_dir.mkdir(parents=True)
            path = out_dir / collect.snapshot_filename(self.snapshot()["collected_at"])
            path.write_text("different immutable evidence\n", encoding="utf-8")
            latest = out_dir / "latest.json"
            before = b'{"collected_at":"2026-08-25T06:00:00+00:00"}\n'
            latest.write_bytes(before)
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(Path(directory) / "history" / "facts.jsonl"),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "append_jsonl",
            ) as append, patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "different immutable evidence\n")
            self.assertEqual(latest.read_bytes(), before)
            append.assert_not_called()
            save.assert_not_called()

    def test_immutable_snapshot_write_failure_prevents_auxiliary_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            out_dir.mkdir(parents=True)
            latest = out_dir / "latest.json"
            before = b'{"collected_at":"2026-08-25T06:00:00+00:00"}\n'
            latest.write_bytes(before)
            path = out_dir / collect.snapshot_filename(self.snapshot()["collected_at"])
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(Path(directory) / "history" / "facts.jsonl"),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect, "_write_immutable_text", side_effect=OSError("disk full"),
            ), patch.object(
                collect.facts, "append_jsonl",
            ) as append, patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertFalse(path.exists())
            self.assertEqual(latest.read_bytes(), before)
            self.assertFalse((Path(directory) / "history" / "facts.jsonl").exists())
            append.assert_not_called()
            save.assert_not_called()

    def test_exact_immutable_snapshot_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            out_dir.mkdir(parents=True)
            serialized = json.dumps(self.snapshot(), indent=2, allow_nan=False) + "\n"
            path = out_dir / collect.snapshot_filename(self.snapshot()["collected_at"])
            path.write_text(serialized, encoding="utf-8")
            facts_path = Path(directory) / "history" / "facts.jsonl"
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "append_jsonl", return_value=1,
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ), patch("builtins.print"):
                self.assertEqual(collect.main(), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), serialized)
            self.assertEqual((out_dir / "latest.json").read_text(encoding="utf-8"), serialized)

    def test_latest_atomic_replace_failure_preserves_previous_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            out_dir.mkdir(parents=True)
            latest = out_dir / "latest.json"
            before = b'{"collected_at":"2026-08-25T06:00:00+00:00"}\n'
            latest.write_bytes(before)
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(Path(directory) / "history" / "facts.jsonl"),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "append_jsonl", return_value=1,
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ), patch.object(
                collect, "_atomic_replace_text", side_effect=OSError("replace failed"),
                create=True,
            ), patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertEqual(latest.read_bytes(), before)

    def test_fact_conflict_fails_before_snapshot_or_state_write(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(Path(directory) / "history" / "facts.jsonl"),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.facts, "jsonl_additions",
                side_effect=collect.facts.FactConflictError("conflict"),
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertFalse(out_dir.exists())
            save.assert_not_called()

    def test_no_growth_neither_loads_nor_saves_supply_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "argv", [
                "collect.py", "--dry-run", "--no-growth", "--out-dir", directory,
            ]), patch.object(
                collect.growth_module, "load_supply_state",
            ) as load, patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), None),
            ) as run, patch.object(
                collect.pipeline, "check_publishable",
                return_value={"publishable": True, "failures": []},
            ), patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 0)
            load.assert_not_called()
            save.assert_not_called()
            self.assertIsNone(run.call_args.kwargs["supply_state"])

    def test_publication_gate_fails_before_snapshot_fact_or_state_write(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "snapshots"
            facts_path = Path(directory) / "history" / "facts.jsonl"
            with patch.object(sys, "argv", [
                "collect.py", "--out-dir", str(out_dir),
                "--facts-path", str(facts_path),
            ]), patch.object(
                collect.growth_module, "load_supply_state", return_value=self.state(),
            ), patch.object(
                collect, "collect_with_state", return_value=(self.snapshot(), self.state()),
            ), patch.object(
                collect.pipeline, "check_publishable", return_value={
                    "publishable": False,
                    "failures": [{"check": "semantic", "detail": "carried activity invalid"}],
                },
            ), patch.object(
                collect.facts, "append_jsonl",
            ) as append_facts, patch.object(
                collect.growth_module, "save_supply_state",
            ) as save, patch("builtins.print"):
                self.assertEqual(collect.main(), 1)
            self.assertFalse(out_dir.exists())
            self.assertFalse(facts_path.exists())
            append_facts.assert_not_called()
            save.assert_not_called()


class TestSnapshotFilename(unittest.TestCase):
    def test_filename_is_filesystem_safe_and_sorts_chronologically(self):
        earlier = collect.snapshot_filename("2026-08-05T09:00:00+00:00")
        later = collect.snapshot_filename("2026-08-05T10:00:00+00:00")
        self.assertNotIn(":", earlier)
        self.assertLess(earlier, later)


if __name__ == "__main__":
    unittest.main()
