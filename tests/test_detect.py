"""Offline tests for anomaly detection.

Synthetic histories, no network, no filesystem. Each test constructs the exact
history shape it needs so a detector's trigger condition is unambiguous.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import detect  # noqa: E402


def snapshot(
    *, at="2026-08-05T09:00:00+00:00", healthy=True, slot=100, tps=3000.0,
    slot_time=0.40, delinquent_pct=1.0, circulating=480_000_000.0,
    active_stake=390_000_000.0,
):
    return {
        "collected_at": at,
        "network": {"healthy": healthy, "health_raw": "ok" if healthy else "unhealthy", "slot": slot},
        "performance": {"available": True, "latest_tps": tps, "mean_slot_time_secs": slot_time},
        "supply": {"available": True, "circulating_sol": circulating},
        "validators": {"available": True, "delinquent_pct": delinquent_pct,
                       "active_stake_sol": active_stake},
    }


def history(n=4, **overrides):
    """A stable baseline of n snapshots, slots advancing."""
    return [snapshot(at=f"2026-08-05T0{i}:00:00+00:00", slot=100 + i, **overrides) for i in range(n)]


def codes(result):
    return {f["code"] for f in result["findings"]}


class TestInsufficientHistory(unittest.TestCase):
    def test_no_snapshots_is_no_data(self):
        result = detect.analyse([])
        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["findings"], [])

    def test_single_snapshot_cannot_judge_anything(self):
        result = detect.analyse([snapshot()])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["baseline_size"], 0)

    def test_insufficient_history_says_so_rather_than_all_clear(self):
        # The whole point: empty findings here must not read as "healthy".
        result = detect.analyse([snapshot(), snapshot()])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIn("not a healthy network", result["message"])

    def test_status_flips_to_ok_once_baseline_is_deep_enough(self):
        result = detect.analyse(history(3) + [snapshot(slot=200)], min_history=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["baseline_size"], 3)

    def test_min_history_is_configurable(self):
        snapshots = history(2) + [snapshot(slot=200)]
        self.assertEqual(detect.analyse(snapshots, min_history=2)["status"], "ok")
        self.assertEqual(detect.analyse(snapshots, min_history=5)["status"], "insufficient_history")


class TestHealth(unittest.TestCase):
    def test_unhealthy_network_is_critical(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, healthy=False)])
        self.assertIn("network_unhealthy", codes(result))
        self.assertEqual(result["findings"][0]["severity"], "critical")

    def test_notes_the_transition_from_healthy(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, healthy=False)])
        detail = next(f for f in result["findings"] if f["code"] == "network_unhealthy")["detail"]
        self.assertIn("healthy at the previous snapshot", detail)

    def test_healthy_network_produces_nothing(self):
        result = detect.analyse(history(3) + [snapshot(slot=200)])
        self.assertNotIn("network_unhealthy", codes(result))


class TestThroughput(unittest.TestCase):
    def test_large_tps_drop_is_critical(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, tps=1000.0)])
        self.assertIn("tps_drop", codes(result))
        drop = next(f for f in result["findings"] if f["code"] == "tps_drop")
        self.assertEqual(drop["severity"], "critical")
        self.assertEqual(drop["baseline"], 3000.0)

    def test_tps_spike_is_informational_not_critical(self):
        # A throughput spike is not an outage; it must not cry wolf.
        result = detect.analyse(history(3) + [snapshot(slot=200, tps=9000.0)])
        spike = next(f for f in result["findings"] if f["code"] == "tps_spike")
        self.assertEqual(spike["severity"], "info")

    def test_small_fluctuation_is_not_an_anomaly(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, tps=2800.0)])
        self.assertNotIn("tps_drop", codes(result))
        self.assertNotIn("tps_spike", codes(result))

    def test_baseline_uses_median_so_one_outlier_does_not_hide_a_drop(self):
        # A single absurd spike in history would drag a mean far enough to mask this.
        noisy = history(3)
        noisy[1]["performance"]["latest_tps"] = 100_000.0
        result = detect.analyse(noisy + [snapshot(slot=200, tps=1000.0)])
        self.assertIn("tps_drop", codes(result))

    def test_missing_tps_produces_no_false_finding(self):
        blind = snapshot(slot=200)
        blind["performance"] = {"available": False}
        result = detect.analyse(history(3) + [blind])
        self.assertNotIn("tps_drop", codes(result))


class TestSlotTime(unittest.TestCase):
    def test_slow_slots_warn(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, slot_time=0.9)])
        self.assertIn("slow_slots", codes(result))

    def test_normal_slot_time_is_silent(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, slot_time=0.41)])
        self.assertNotIn("slow_slots", codes(result))


class TestDelinquency(unittest.TestCase):
    def test_high_absolute_delinquency_is_critical(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, delinquent_pct=7.0)])
        self.assertIn("delinquency_high", codes(result))

    def test_jump_against_baseline_warns_even_when_absolute_is_low(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, delinquent_pct=3.5)])
        self.assertIn("delinquency_jump", codes(result))
        self.assertNotIn("delinquency_high", codes(result))

    def test_stable_delinquency_is_silent(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, delinquent_pct=1.2)])
        self.assertEqual(codes(result) & {"delinquency_high", "delinquency_jump"}, set())


class TestSupplyAndStake(unittest.TestCase):
    def test_large_supply_move_warns(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, circulating=500_000_000.0)])
        self.assertIn("supply_move", codes(result))

    def test_large_stake_move_warns(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, active_stake=300_000_000.0)])
        self.assertIn("stake_move", codes(result))

    def test_direction_is_stated(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, active_stake=300_000_000.0)])
        self.assertIn("Down", next(f for f in result["findings"] if f["code"] == "stake_move")["detail"])


class TestStalledSlot(unittest.TestCase):
    def test_slot_not_advancing_is_critical(self):
        past = history(3)
        result = detect.analyse(past + [snapshot(slot=past[-1]["network"]["slot"])])
        self.assertIn("slot_stalled", codes(result))

    def test_advancing_slot_is_silent(self):
        result = detect.analyse(history(3) + [snapshot(slot=999)])
        self.assertNotIn("slot_stalled", codes(result))


class TestOrderingAndCounts(unittest.TestCase):
    def test_findings_are_sorted_most_severe_first(self):
        result = detect.analyse(history(3) + [
            snapshot(slot=200, tps=1000.0, slot_time=0.9, delinquent_pct=7.0),
        ])
        order = [f["severity"] for f in result["findings"]]
        self.assertEqual(order, sorted(order, key=detect.SEVERITIES.index))
        self.assertEqual(order[0], "critical")

    def test_counts_match_findings(self):
        result = detect.analyse(history(3) + [snapshot(slot=200, tps=1000.0, slot_time=0.9)])
        self.assertEqual(result["counts"]["critical"], 1)
        self.assertEqual(result["counts"]["warning"], 1)


class TestLoadHistory(unittest.TestCase):
    def test_missing_directory_returns_empty_not_error(self):
        self.assertEqual(detect.load_history(Path("/nonexistent/xyz")), [])


if __name__ == "__main__":
    unittest.main()
