"""Offline tests for the snapshot-to-snapshot delta.

`delta.compare` is a pure function over two dicts, so every test here is a
literal input and a literal expectation — no network, no fixtures on disk, no
clock. The properties pinned below are the ways a delta section lies:

  * an unavailable metric rendered as a -100% collapse,
  * a percentage invented against a previous value of zero,
  * an extrapolated figure moving as loudly as a measured one,
  * output that differs between two runs over the same two snapshots.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import delta  # noqa: E402


def snapshot(collected_at="2026-08-05T12:00:00+00:00", **overrides):
    body = {
        "collected_at": collected_at,
        "network": {"healthy": True, "slot": 100},
        "epoch": {"available": True, "epoch": 1012},
        "performance": {
            "available": True, "latest_tps": 3000.0, "mean_tps": 2900.0,
            "mean_slot_time_secs": 0.420,
        },
        "supply": {"available": True, "circulating_sol": 600_000_000.0},
        "validators": {
            "available": True, "active_count": 1000, "delinquent_pct": 1.0,
            "nakamoto_coefficient": 18, "active_stake_sol": 434_000_000.0,
        },
        "economics": {
            "available": True,
            "price": {"available": True, "price_usd": 74.0},
            "tvl": {"available": True, "tvl_usd": 4_800_000_000.0},
            "stablecoins": {"available": True, "stablecoin_usd": 15_800_000_000.0},
            "dex": {"available": True, "volume_24h_usd": 1_700_000_000.0},
        },
        "activity": {
            "available": True,
            "fees": {"available": True, "median_lamports": 5_400},
            "rev": {"available": True, "estimated_24h_sol": 8_000.0},
        },
    }
    for path, value in overrides.items():
        node = body
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return body


def by_key(entries):
    return {item["key"]: item for item in entries}


class TestDeterminism(unittest.TestCase):
    def test_the_same_two_snapshots_produce_identical_output(self):
        before, after = snapshot(), snapshot("2026-08-05T18:00:00+00:00",
                                             **{"performance.latest_tps": 4000.0})
        first = json.dumps(delta.compare(before, after), sort_keys=True)
        second = json.dumps(delta.compare(copy.deepcopy(before), copy.deepcopy(after)),
                            sort_keys=True)
        self.assertEqual(first, second)

    def test_comparison_does_not_mutate_its_inputs(self):
        before, after = snapshot(), snapshot("2026-08-05T18:00:00+00:00")
        original = copy.deepcopy((before, after))
        delta.compare(before, after)
        self.assertEqual((before, after), original)


class TestMissingValues(unittest.TestCase):
    """The central rule: absent is not zero and absent is not a change."""

    def test_a_metric_missing_on_one_side_is_not_comparable_not_a_collapse(self):
        before = snapshot()
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": {"available": False}})
        result = delta.compare(before, after)

        self.assertNotIn("price_usd", by_key(result["changes"]))
        self.assertNotIn("price_usd", by_key(result["steady"]))
        entry = by_key(result["not_comparable"])["price_usd"]
        self.assertEqual(entry["reason"], "not present in the newer snapshot")
        self.assertEqual(entry["current"], None)
        # Nothing anywhere claims a -100% move.
        self.assertNotIn("-100", json.dumps(result))

    def test_a_metric_new_in_the_later_snapshot_is_not_a_gain_from_zero(self):
        before = snapshot(**{"activity": {"available": False}})
        after = snapshot("2026-08-05T18:00:00+00:00")
        result = delta.compare(before, after)
        entry = by_key(result["not_comparable"])["median_fee_lamports"]
        self.assertEqual(entry["reason"], "not present in the earlier snapshot")
        self.assertEqual(entry["previous"], None)

    def test_a_metric_absent_from_both_says_so_once(self):
        bare = {"collected_at": "2026-08-05T12:00:00+00:00"}
        result = delta.compare(bare, {"collected_at": "2026-08-05T18:00:00+00:00"})
        self.assertEqual(result["counts"]["changed"], 0)
        self.assertEqual(result["counts"]["steady"], 0)
        self.assertEqual(result["counts"]["not_comparable"], len(delta.METRICS))
        for item in result["not_comparable"]:
            self.assertEqual(item["reason"], "not present in either snapshot")

    def test_a_null_value_is_treated_as_missing_not_as_zero(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"performance.latest_tps": None})
        result = delta.compare(snapshot(), after)
        self.assertIn("latest_tps", by_key(result["not_comparable"]))

    def test_a_boolean_never_counts_as_a_numeric_value(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"validators.active_count": True})
        result = delta.compare(snapshot(), after)
        self.assertIn("active_count", by_key(result["not_comparable"]))


class TestThresholds(unittest.TestCase):
    def test_a_move_below_threshold_is_steady_not_a_change(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"performance.latest_tps": 3100.0})  # +3.3%, below 10%
        result = delta.compare(snapshot(), after)
        self.assertIn("latest_tps", by_key(result["steady"]))
        self.assertNotIn("latest_tps", by_key(result["changes"]))

    def test_a_move_past_threshold_carries_its_context_lines(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"performance.latest_tps": 4000.0})  # +33%
        entry = by_key(delta.compare(snapshot(), after)["changes"])["latest_tps"]
        self.assertEqual(entry["direction"], "up")
        self.assertAlmostEqual(entry["change_pct"], 33.33, places=2)
        self.assertTrue(entry["why_it_matters"])
        self.assertTrue(entry["what_to_verify"])

    def test_steady_metrics_carry_no_narrative(self):
        # Context is only attached to something that actually moved.
        result = delta.compare(snapshot(), snapshot("2026-08-05T18:00:00+00:00"))
        for item in result["steady"]:
            self.assertNotIn("why_it_matters", item)

    def test_an_absolute_threshold_governs_counts(self):
        small = snapshot("2026-08-05T18:00:00+00:00",
                         **{"validators.active_count": 1002})   # +2, floor is 5
        large = snapshot("2026-08-05T18:00:00+00:00",
                         **{"validators.active_count": 1008})   # +8
        self.assertIn("active_count", by_key(delta.compare(snapshot(), small)["steady"]))
        self.assertIn("active_count", by_key(delta.compare(snapshot(), large)["changes"]))

    def test_an_epoch_rollover_is_reported_as_a_change(self):
        after = snapshot("2026-08-05T18:00:00+00:00", **{"epoch.epoch": 1013})
        entry = by_key(delta.compare(snapshot(), after)["changes"])["epoch"]
        self.assertTrue(entry["identifier"])
        self.assertEqual(entry["change"], 1)

    def test_sampled_metrics_are_labelled_as_such(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"activity.rev.estimated_24h_sol": 12_000.0})  # +50%
        entry = by_key(delta.compare(snapshot(), after)["changes"])["estimated_24h_rev_sol"]
        self.assertEqual(entry["basis"], "sampled")

    def test_the_noisy_sampled_metric_has_a_looser_threshold_than_a_measured_one(self):
        # A 12% swing in an extrapolated REV estimate is sampling noise; the
        # same swing in measured TPS is a real event. The table must reflect it.
        rev = snapshot("2026-08-05T18:00:00+00:00",
                       **{"activity.rev.estimated_24h_sol": 8_960.0})   # +12%
        tps = snapshot("2026-08-05T18:00:00+00:00",
                       **{"performance.latest_tps": 3_360.0})           # +12%
        self.assertIn("estimated_24h_rev_sol",
                      by_key(delta.compare(snapshot(), rev)["steady"]))
        self.assertIn("latest_tps", by_key(delta.compare(snapshot(), tps)["changes"]))


class TestPercentages(unittest.TestCase):
    def test_a_move_away_from_zero_reports_no_percentage_rather_than_infinity(self):
        before = snapshot(**{"validators.delinquent_pct": 0.0})
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"validators.delinquent_pct": 3.0})
        entry = by_key(delta.compare(before, after)["changes"])["delinquent_pct"]
        self.assertIsNone(entry["change_pct"])
        self.assertEqual(entry["change"], 3.0)

    def test_zero_to_zero_is_steady(self):
        before = snapshot(**{"validators.delinquent_pct": 0.0})
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"validators.delinquent_pct": 0.0})
        self.assertIn("delinquent_pct", by_key(delta.compare(before, after)["steady"]))

    def test_a_downward_move_is_signed(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price.price_usd": 66.0})   # -10.8%
        entry = by_key(delta.compare(snapshot(), after)["changes"])["price_usd"]
        self.assertEqual(entry["direction"], "down")
        self.assertLess(entry["change_pct"], 0)


class TestElapsed(unittest.TestCase):
    def test_elapsed_seconds_come_from_the_snapshots_not_the_clock(self):
        result = delta.compare(snapshot("2026-08-05T12:00:00+00:00"),
                               snapshot("2026-08-05T18:00:00+00:00"))
        self.assertEqual(result["elapsed_seconds"], 21_600)

    def test_an_unparseable_timestamp_leaves_the_interval_unknown(self):
        result = delta.compare(snapshot("not-a-time"), snapshot())
        self.assertIsNone(result["elapsed_seconds"])
        self.assertEqual(delta.format_elapsed(None), "an unknown interval")

    def test_elapsed_formatting_scales(self):
        self.assertEqual(delta.format_elapsed(45), "45s")
        self.assertEqual(delta.format_elapsed(900), "15m")
        self.assertEqual(delta.format_elapsed(21_600), "6.0h")


class TestAnalyse(unittest.TestCase):
    def test_one_snapshot_is_insufficient_never_no_change(self):
        result = delta.analyse([snapshot()])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIn("not a statement that nothing changed", result["message"])
        self.assertEqual(result["changes"], [])

    def test_empty_history_is_insufficient_too(self):
        self.assertEqual(delta.analyse([])["status"], "insufficient_history")

    def test_the_two_newest_snapshots_are_the_ones_compared(self):
        history = [snapshot(f"2026-08-05T0{hour}:00:00+00:00") for hour in range(1, 5)]
        result = delta.analyse(history)
        self.assertEqual(result["previous_collected_at"], "2026-08-05T03:00:00+00:00")
        self.assertEqual(result["current_collected_at"], "2026-08-05T04:00:00+00:00")

    def test_rendering_an_older_snapshot_does_not_borrow_a_newer_comparison(self):
        history = [snapshot(f"2026-08-05T0{hour}:00:00+00:00") for hour in range(1, 5)]
        result = delta.delta_for(history[1], history)
        self.assertEqual(result["current_collected_at"], "2026-08-05T02:00:00+00:00")
        self.assertEqual(result["previous_collected_at"], "2026-08-05T01:00:00+00:00")

    def test_the_oldest_snapshot_has_nothing_to_compare_against(self):
        history = [snapshot(f"2026-08-05T0{hour}:00:00+00:00") for hour in range(1, 5)]
        self.assertEqual(delta.delta_for(history[0], history)["status"],
                         "insufficient_history")


class TestMetricTable(unittest.TestCase):
    def test_every_metric_declares_a_threshold_and_both_context_lines(self):
        for spec in delta.METRICS:
            self.assertTrue(spec.get("move_pct") or spec.get("move_abs"), spec["key"])
            self.assertIn(spec["basis"], ("measured", "sampled"))
            self.assertTrue(spec["why"].strip())
            self.assertTrue(spec["verify"].strip())

    def test_metric_keys_are_unique(self):
        keys = [spec["key"] for spec in delta.METRICS]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
