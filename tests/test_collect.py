"""Offline tests for the collection transforms.

No network. Every test feeds raw JSON-RPC-shaped input to a pure function, so
the whole suite runs on a plane, in CI, or with the RPC endpoint down.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect  # noqa: E402


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


class TestPerformance(unittest.TestCase):
    def test_derives_tps_and_slot_time(self):
        summary = collect.summarize_performance([
            {"slot": 100, "numTransactions": 12000, "numSlots": 150, "samplePeriodSecs": 60},
            {"slot": 90, "numTransactions": 6000, "numSlots": 150, "samplePeriodSecs": 60},
        ])
        self.assertTrue(summary["available"])
        self.assertEqual(summary["latest_tps"], 200.0)
        self.assertEqual(summary["mean_tps"], 150.0)
        self.assertEqual(summary["peak_tps"], 200.0)
        self.assertEqual(summary["mean_slot_time_secs"], 0.4)

    def test_missing_samples_report_unavailable_rather_than_zero(self):
        # Zero TPS and "we don't know" are different claims.
        for bad in ([], None, "nope", {}):
            self.assertFalse(collect.summarize_performance(bad)["available"])

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
        self.assertEqual(len(collect.summarize_validators(many, top_n=10)["top_validators"]), 10)

    def test_empty_validator_set_does_not_divide_by_zero(self):
        summary = collect.summarize_validators({"current": [], "delinquent": []})
        self.assertTrue(summary["available"])
        self.assertEqual(summary["active_stake_sol"], 0)
        self.assertEqual(summary["delinquent_pct"], 0.0)
        self.assertEqual(summary["nakamoto_coefficient"], 0)

    def test_malformed_input_reports_unavailable(self):
        for bad in (None, [], "nope", {"current": "not a list", "delinquent": []}):
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

    def test_supply_converts_and_computes_share(self):
        summary = collect.summarize_supply({"value": {
            "total": 600_000_000 * 10**9,
            "circulating": 480_000_000 * 10**9,
            "nonCirculating": 120_000_000 * 10**9,
        }})
        self.assertEqual(summary["total_sol"], 600_000_000.0)
        self.assertEqual(summary["circulating_pct"], 80.0)

    def test_supply_missing_value_reports_unavailable(self):
        for bad in (None, {}, {"value": "nope"}):
            self.assertFalse(collect.summarize_supply(bad)["available"])


class TestBuildSnapshot(unittest.TestCase):
    def test_healthy_only_when_rpc_says_ok(self):
        healthy = collect.build_snapshot({"getHealth": "ok", "getSlot": 5}, "2026-08-05T00:00:00+00:00", "u")
        self.assertTrue(healthy["network"]["healthy"])

        # An error object must never be read as healthy.
        unhealthy = collect.build_snapshot(
            {"getHealth": {"code": -32005}, "getSlot": 5}, "2026-08-05T00:00:00+00:00", "u",
        )
        self.assertFalse(unhealthy["network"]["healthy"])
        self.assertEqual(unhealthy["network"]["health_raw"], "unhealthy")

    def test_snapshot_is_json_serializable_and_stamped(self):
        import json
        snapshot = collect.build_snapshot({"getHealth": "ok", "getSlot": 1}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertEqual(snapshot["schema_version"], collect.SCHEMA_VERSION)
        self.assertFalse(snapshot["source"]["requires_api_key"])
        json.dumps(snapshot)  # must not raise

    def test_totally_empty_rpc_response_still_produces_a_valid_snapshot(self):
        # A degraded snapshot beats a crash — the report should say "unavailable".
        snapshot = collect.build_snapshot({}, "2026-08-05T00:00:00+00:00", "endpoint")
        self.assertFalse(snapshot["network"]["healthy"])
        self.assertIsNone(snapshot["network"]["slot"])
        for section in ("epoch", "performance", "supply", "validators"):
            self.assertFalse(snapshot[section]["available"])


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
        self.assertGreaterEqual(collect.SCHEMA_VERSION, 3)


class TestSnapshotFilename(unittest.TestCase):
    def test_filename_is_filesystem_safe_and_sorts_chronologically(self):
        earlier = collect.snapshot_filename("2026-08-05T09:00:00+00:00")
        later = collect.snapshot_filename("2026-08-05T10:00:00+00:00")
        self.assertNotIn(":", earlier)
        self.assertLess(earlier, later)


if __name__ == "__main__":
    unittest.main()
