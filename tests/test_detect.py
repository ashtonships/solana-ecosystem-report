"""Offline tests for anomaly detection.

Synthetic histories, no network, no filesystem. Each test constructs the exact
history shape it needs so a detector's trigger condition is unambiguous.
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import detect  # noqa: E402

# Fixed reference time for every analysis. Freshness is mandatory, so tests
# inject a deterministic `now` instead of ever depending on today's date.
NOW = "2026-08-05T10:30:00+00:00"


def analyse(snapshots, **overrides):
    """detect.analyse with an injected reference time; overrides still win."""
    overrides.setdefault("now", NOW)
    return detect.analyse(snapshots, **overrides)


def snapshot(
    *, at="2026-08-05T09:00:00+00:00", healthy=True, slot=100, tps=3000.0,
    slot_time=0.40, delinquent_pct=1.0, circulating=480_000_000.0,
    active_stake=390_000_000.0, price_usd=150.0, tvl_usd=10_000_000_000.0,
):
    return {
        "collected_at": at,
        "schema_version": 9,
        "network": {"healthy": healthy, "health_raw": "ok" if healthy else "unhealthy", "slot": slot},
        "performance": {"available": True, "latest_tps": tps, "mean_slot_time_secs": slot_time},
        "supply": {"available": True, "circulating_sol": circulating},
        "validators": {"available": True, "delinquent_pct": delinquent_pct,
                       "active_stake_sol": active_stake},
        "economics": {
            "available": True,
            "price": {"available": True, "price_usd": price_usd},
            "tvl": {"available": True, "tvl_usd": tvl_usd},
        },
    }


def history(n=4, **overrides):
    """A stable six-hour baseline of n snapshots, slots advancing."""
    end = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    return [
        snapshot(
            at=(end - timedelta(hours=6 * (n - index - 1))).isoformat(timespec="seconds"),
            slot=100 + index, **overrides,
        )
        for index in range(n)
    ]


def codes(result):
    return {f["code"] for f in result["findings"]}


class TestInsufficientHistory(unittest.TestCase):
    def test_no_snapshots_is_no_data(self):
        result = analyse([])
        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["findings"], [])

    def test_single_snapshot_cannot_judge_anything(self):
        result = analyse([snapshot()])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["baseline_size"], 0)

    def test_insufficient_history_says_so_rather_than_all_clear(self):
        # The whole point: empty findings here must not read as "healthy".
        result = analyse([snapshot(), snapshot()])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIn("not a healthy network", result["message"])

    def test_current_state_findings_survive_insufficient_history(self):
        previous = snapshot(at="2026-08-05T03:00:00+00:00", slot=100)
        latest = snapshot(
            at="2026-08-05T09:00:00+00:00", slot=100, healthy=False,
            slot_time=0.9, delinquent_pct=9.0,
        )

        result = analyse([previous, latest])

        self.assertEqual(result["status"], "insufficient_history")
        self.assertTrue({"network_unhealthy", "slow_slots", "delinquency_high", "slot_stalled"} <= codes(result))

    def test_status_flips_to_ok_once_baseline_is_deep_enough(self):
        result = analyse(history(3) + [snapshot(slot=200)], min_history=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["baseline_size"], 3)

    def test_min_history_is_configurable(self):
        snapshots = history(2) + [snapshot(slot=200)]
        self.assertEqual(analyse(snapshots, min_history=2)["status"], "ok")
        self.assertEqual(analyse(snapshots, min_history=5)["status"], "insufficient_history")

    def test_current_economics_hold_removes_old_price_and_tvl_baselines(self):
        latest = snapshot(slot=200)
        latest["economics"] = {"available": False}

        result = analyse(history(3) + [latest])

        self.assertEqual(result["coverage"]["sol_price"]["raw_eligible"], 0)
        self.assertEqual(result["coverage"]["tvl"]["raw_eligible"], 0)


class TestHealth(unittest.TestCase):
    def test_unhealthy_rpc_endpoint_is_critical(self):
        result = analyse(history(3) + [snapshot(slot=200, healthy=False)])
        self.assertIn("network_unhealthy", codes(result))
        finding = result["findings"][0]
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["title"], "RPC endpoint reports unhealthy")
        self.assertIn("recorded RPC endpoint", finding["detail"])

    def test_notes_the_rpc_endpoint_transition_from_healthy(self):
        result = analyse(history(3) + [snapshot(slot=200, healthy=False)])
        detail = next(f for f in result["findings"] if f["code"] == "network_unhealthy")["detail"]
        self.assertIn("endpoint was healthy at the previous snapshot", detail)

    def test_healthy_network_produces_nothing(self):
        result = analyse(history(3) + [snapshot(slot=200)])
        self.assertNotIn("network_unhealthy", codes(result))

    def test_unavailable_health_warns_without_claiming_unhealthy(self):
        latest = snapshot(slot=200)
        latest["network"] = {"healthy": None, "health_raw": "unavailable", "slot": 200}
        result = analyse(history(3) + [latest])
        self.assertIn("network_health_unavailable", codes(result))
        self.assertNotIn("network_unhealthy", codes(result))
        health = next(f for f in result["findings"] if f["code"] == "network_health_unavailable")
        self.assertEqual(health["severity"], "warning")
        self.assertEqual(health["title"], "RPC endpoint health is unavailable")
        self.assertIn("does not establish network-wide health or an outage", health["detail"])

    def test_incompatible_health_schema_is_unavailable(self):
        latest = snapshot(slot=200)
        latest["schema_version"] = 7

        result = analyse(history(3) + [latest])

        self.assertIn("network_health_unavailable", codes(result))
        self.assertFalse(result["coverage"]["network_health"]["current_eligible"])

    def test_previous_health_transition_requires_eligible_evidence(self):
        previous = snapshot(at="2026-08-05T03:00:00+00:00", healthy=True)
        previous["network"]["stale"] = True
        findings = detect.detect_health(
            snapshot(at="2026-08-05T09:00:00+00:00", healthy=False), [previous],
        )

        self.assertNotIn("previous snapshot", findings[0]["detail"])


class TestThroughput(unittest.TestCase):
    def test_large_tps_drop_is_critical(self):
        result = analyse(history(3) + [snapshot(slot=200, tps=1000.0)])
        self.assertIn("tps_drop", codes(result))
        drop = next(f for f in result["findings"] if f["code"] == "tps_drop")
        self.assertEqual(drop["severity"], "critical")
        self.assertEqual(drop["baseline"], 3000.0)

    def test_tps_spike_is_informational_not_critical(self):
        # A throughput spike is not an outage; it must not cry wolf.
        result = analyse(history(3) + [snapshot(slot=200, tps=9000.0)])
        spike = next(f for f in result["findings"] if f["code"] == "tps_spike")
        self.assertEqual(spike["severity"], "info")

    def test_small_fluctuation_is_not_an_anomaly(self):
        result = analyse(history(3) + [snapshot(slot=200, tps=2800.0)])
        self.assertNotIn("tps_drop", codes(result))
        self.assertNotIn("tps_spike", codes(result))

    def test_baseline_uses_median_so_one_outlier_does_not_hide_a_drop(self):
        # A single absurd spike in history would drag a mean far enough to mask this.
        noisy = history(3)
        noisy[1]["performance"]["latest_tps"] = 100_000.0
        result = analyse(noisy + [snapshot(slot=200, tps=1000.0)])
        self.assertIn("tps_drop", codes(result))

    def test_missing_tps_produces_no_false_finding(self):
        blind = snapshot(slot=200)
        blind["performance"] = {"available": False}
        result = analyse(history(3) + [blind])
        self.assertNotIn("tps_drop", codes(result))


class TestSlotTime(unittest.TestCase):
    def test_slow_slots_warn(self):
        result = analyse(history(3) + [snapshot(slot=200, slot_time=0.9)])
        self.assertIn("slow_slots", codes(result))
        finding = next(f for f in result["findings"] if f["code"] == "slow_slots")
        self.assertEqual(finding["title"], "Mean slot time exceeded this report's alert threshold")
        self.assertIn("this report's 0.60s slow-slot alert threshold", finding["detail"])
        self.assertEqual(finding["baseline"], 0.60)
        self.assertNotIn("0.400", finding["detail"])

    def test_normal_slot_time_is_silent(self):
        result = analyse(history(3) + [snapshot(slot=200, slot_time=0.41)])
        self.assertNotIn("slow_slots", codes(result))


class TestDelinquency(unittest.TestCase):
    def test_high_absolute_delinquency_is_critical(self):
        result = analyse(history(3) + [snapshot(slot=200, delinquent_pct=7.0)])
        self.assertIn("delinquency_high", codes(result))

    def test_jump_against_baseline_warns_even_when_absolute_is_low(self):
        result = analyse(history(3) + [snapshot(slot=200, delinquent_pct=3.5)])
        self.assertIn("delinquency_jump", codes(result))
        self.assertNotIn("delinquency_high", codes(result))

    def test_stable_delinquency_is_silent(self):
        result = analyse(history(3) + [snapshot(slot=200, delinquent_pct=1.2)])
        self.assertEqual(codes(result) & {"delinquency_high", "delinquency_jump"}, set())


class TestSupplyAndStake(unittest.TestCase):
    def test_large_supply_move_warns(self):
        result = analyse(history(3) + [snapshot(slot=200, circulating=500_000_000.0)])
        self.assertIn("supply_move", codes(result))

    def test_large_stake_move_warns(self):
        result = analyse(history(3) + [snapshot(slot=200, active_stake=300_000_000.0)])
        self.assertIn("stake_move", codes(result))

    def test_direction_is_stated(self):
        result = analyse(history(3) + [snapshot(slot=200, active_stake=300_000_000.0)])
        self.assertIn("Down", next(f for f in result["findings"] if f["code"] == "stake_move")["detail"])


class TestEconomicMoves(unittest.TestCase):
    def test_large_sol_price_move_warns(self):
        result = analyse(history(3) + [snapshot(slot=200, price_usd=190.0)])
        self.assertIn("sol_price_move", codes(result))

    def test_large_tvl_move_warns(self):
        result = analyse(history(3) + [snapshot(slot=200, tvl_usd=7_500_000_000.0)])
        self.assertIn("tvl_move", codes(result))

    def test_normal_market_movement_is_silent(self):
        result = analyse(history(3) + [snapshot(slot=200, price_usd=158.0, tvl_usd=10_800_000_000.0)])
        self.assertNotIn("sol_price_move", codes(result))
        self.assertNotIn("tvl_move", codes(result))

    def test_missing_economic_source_never_becomes_a_false_move(self):
        latest = snapshot(slot=200)
        latest["economics"]["price"] = {"available": False}
        result = analyse(history(3) + [latest])
        self.assertNotIn("sol_price_move", codes(result))


class TestFreshnessGate(unittest.TestCase):
    """A stale latest snapshot must never read as a clean bill of health."""

    FRESH_NOW = "2026-08-05T10:30:00+00:00"  # 90 minutes after the fixture snapshots

    def test_stale_latest_snapshot_is_not_ok(self):
        result = analyse(
            history(3) + [snapshot(slot=200)], now=self.FRESH_NOW, max_age_seconds=3600,
        )
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertEqual(result["findings"], [])
        self.assertIn("stale", result["message"])

    def test_fresh_latest_snapshot_with_injected_reference_time_is_ok(self):
        result = analyse(history(3) + [snapshot(slot=200)], now=self.FRESH_NOW)
        self.assertEqual(result["status"], "ok")

    def test_reference_time_accepts_a_datetime_object(self):
        now = datetime(2026, 8, 5, 10, 30, tzinfo=timezone.utc)
        result = analyse(history(3) + [snapshot(slot=200)], now=now)
        self.assertEqual(result["status"], "ok")

    def test_age_exactly_at_the_limit_is_still_eligible(self):
        result = analyse(
            history(3) + [snapshot(slot=200)], now=self.FRESH_NOW, max_age_seconds=5400,
        )
        self.assertEqual(result["status"], "ok")

    def test_unreadable_collected_at_cannot_be_judged_fresh(self):
        latest = snapshot(slot=200)
        latest.pop("collected_at")
        result = analyse(history(3) + [latest], now=self.FRESH_NOW)
        self.assertNotEqual(result["status"], "ok")
        self.assertEqual(result["findings"], [])

    def test_stale_and_insufficient_history_are_reported_together(self):
        result = analyse(
            [snapshot(at="2026-08-04T00:00:00+00:00"),
             snapshot(at="2026-08-04T06:00:00+00:00", slot=200)],
            now=self.FRESH_NOW, max_age_seconds=3600,
        )
        self.assertEqual(result["status"], "stale_snapshot")
        self.assertEqual(len(result["conditions"]), 2)
        self.assertIn("freshness limit", result["message"])
        self.assertIn("time-eligible prior", result["message"])


class TestBaselineEligibility(unittest.TestCase):
    """Baselines use equivalent metric facts that are finite and available."""

    def test_equivalent_metric_semantics_survive_a_schema_change(self):
        snaps = history(3) + [snapshot(slot=200)]
        for item in snaps:
            item["schema_version"] = 9
        intruder = snapshot(at="2026-08-05T08:30:00+00:00", slot=99, tps=100.0)
        intruder["schema_version"] = 8
        result = analyse([intruder] + snaps)
        self.assertEqual(result["baseline_size"], 4)
        self.assertEqual(result["status"], "ok")

    def test_equivalent_old_schema_fact_can_complete_the_baseline(self):
        snaps = history(2) + [snapshot(slot=200)]
        for item in snaps:
            item["schema_version"] = 9
        intruder = snapshot(at="2026-08-05T08:30:00+00:00", slot=99)
        intruder["schema_version"] = 8
        result = analyse([intruder] + snaps)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coverage"]["tps"]["eligible_priors"], 3)

    def test_rapid_snapshots_do_not_become_five_hour_spaced_priors(self):
        rapid = [
            snapshot(at=f"2026-08-05T0{hour}:00:00+00:00", slot=100 + hour)
            for hour in range(3)
        ]
        result = analyse(rapid + [snapshot(slot=200)])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["coverage"]["tps"]["raw_eligible"], 3)
        self.assertEqual(result["coverage"]["tps"]["eligible_priors"], 1)
        self.assertEqual(result["coverage"]["tps"]["cadence_excluded"], 2)

    def test_metric_evidence_uses_exact_18000_second_boundary(self):
        base = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
        raw = [
            snapshot(at=(base + timedelta(seconds=seconds)).isoformat(), slot=100 + index)
            for index, seconds in enumerate((0, 17_999, 18_000))
        ]

        evidence = detect.metric_evidence(raw, "latest_tps")

        self.assertEqual(set(evidence), {"raw", "eligible", "cadence"})
        self.assertEqual(len(evidence["raw"]), 3)
        self.assertEqual(len(evidence["eligible"]), 3)
        self.assertEqual(
            [item["collected_at"] for item in evidence["cadence"]],
            [raw[0]["collected_at"], raw[2]["collected_at"]],
        )

    def test_one_value_metric_baseline_cannot_trigger_a_finding(self):
        # Three history snapshots but TPS eligible in only one of them: a median
        # of one is not a baseline, so no tps_drop may fire.
        snaps = history(4) + [snapshot(slot=200, tps=100.0)]
        for item in snaps[1:4]:
            item["performance"] = {"available": False}
        result = analyse(snaps)
        self.assertNotIn("tps_drop", codes(result))
        tps = result["coverage"]["tps"]
        self.assertEqual(tps["evaluated"], 1)
        self.assertTrue(tps["insufficient_baseline"])
        self.assertNotEqual(result["status"], "ok")

    def test_non_finite_history_values_are_ineligible(self):
        snaps = history(4) + [snapshot(slot=200, tps=1000.0)]
        snaps[0]["performance"]["latest_tps"] = float("nan")
        snaps[1]["performance"]["latest_tps"] = float("inf")
        result = analyse(snaps)
        tps = result["coverage"]["tps"]
        self.assertEqual(tps["evaluated"], 2)
        self.assertNotIn("tps_drop", codes(result))

    def test_non_finite_current_value_never_triggers_or_clears(self):
        snaps = history(3) + [snapshot(slot=200)]
        snaps[-1]["performance"]["latest_tps"] = float("nan")
        result = analyse(snaps)
        self.assertNotIn("tps_drop", codes(result))
        self.assertFalse(result["coverage"]["tps"]["current_eligible"])
        self.assertNotEqual(result["status"], "ok")


class TestAvailabilityGating(unittest.TestCase):
    """Retained numbers behind unavailable or carried-forward state are inert."""

    def test_unavailable_block_with_retained_value_cannot_trigger(self):
        latest = snapshot(slot=200)
        latest["validators"] = {"available": False, "delinquent_pct": 50.0,
                                "active_stake_sol": 10_000_000.0}
        result = analyse(history(3) + [latest])
        self.assertNotIn("delinquency_high", codes(result))
        self.assertNotIn("delinquency_jump", codes(result))
        self.assertNotIn("stake_move", codes(result))
        self.assertFalse(result["coverage"]["delinquency"]["current_eligible"])
        self.assertNotEqual(result["status"], "ok")

    def test_carried_forward_value_cannot_trigger_an_anomaly(self):
        latest = snapshot(slot=200)
        latest["economics"]["price"] = {
            "available": True, "stale": True, "source_state": "last_known_good",
            "carried_forward_at": latest["collected_at"], "price_usd": 1000.0,
        }
        result = analyse(history(3) + [latest])
        self.assertNotIn("sol_price_move", codes(result))
        self.assertFalse(result["coverage"]["sol_price"]["current_eligible"])

    def test_carried_forward_history_values_do_not_shape_the_baseline(self):
        stale = snapshot(at="2026-08-05T08:30:00+00:00", price_usd=1000.0)
        stale["economics"]["price"]["source_state"] = "last_known_good"
        result = analyse([stale] + history(3) + [snapshot(slot=200)])
        self.assertEqual(result["coverage"]["sol_price"]["evaluated"], 3)
        self.assertNotIn("sol_price_move", codes(result))


class TestFreshSameSchemaHappyPath(unittest.TestCase):
    def test_full_eligibility_reads_as_ok_with_complete_coverage(self):
        snaps = history(3) + [snapshot(slot=200)]
        for item in snaps:
            item["schema_version"] = 9
        result = analyse(snaps, now="2026-08-05T10:30:00+00:00")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["findings"], [])
        for code in ("tps", "slot_time", "delinquency", "supply", "stake",
                     "sol_price", "tvl"):
            entry = result["coverage"][code]
            self.assertEqual(entry["evaluated"], 3, code)
            self.assertEqual(entry["unavailable"], 0, code)
            self.assertTrue(entry["current_eligible"], code)
            self.assertFalse(entry["insufficient_baseline"], code)


class TestStalledSlot(unittest.TestCase):
    def test_slot_not_advancing_is_critical(self):
        past = history(3)
        result = analyse(past + [snapshot(slot=past[-1]["network"]["slot"])])
        self.assertIn("slot_stalled", codes(result))

    def test_rapid_equal_slot_is_not_a_stall(self):
        past = history(3)
        latest = snapshot(
            at="2026-08-05T03:01:00+00:00",
            slot=past[-1]["network"]["slot"],
        )
        result = analyse(past + [latest], now="2026-08-05T03:02:00+00:00")
        self.assertNotIn("slot_stalled", codes(result))

    def test_advancing_slot_is_silent(self):
        result = analyse(history(3) + [snapshot(slot=999)])
        self.assertNotIn("slot_stalled", codes(result))

    def test_equal_slot_requires_exact_18000_second_spacing(self):
        previous = snapshot(at="2026-08-05T04:00:00+00:00", slot=100)
        too_close = snapshot(at="2026-08-05T08:59:59+00:00", slot=100)
        at_boundary = snapshot(at="2026-08-05T09:00:00+00:00", slot=100)

        self.assertEqual(detect.detect_stalled_epoch(too_close, [previous]), [])
        self.assertEqual(codes({"findings": detect.detect_stalled_epoch(at_boundary, [previous])}),
                         {"slot_stalled"})

    def test_unavailable_or_stale_slot_facts_are_inert(self):
        previous = snapshot(at="2026-08-05T03:00:00+00:00", slot=100)
        unavailable = snapshot(at="2026-08-05T09:00:00+00:00", slot=100)
        unavailable["network"]["available"] = False
        stale_previous = snapshot(at="2026-08-05T03:00:00+00:00", slot=100)
        stale_previous["network"]["stale"] = True

        self.assertEqual(detect.detect_stalled_epoch(unavailable, [previous]), [])
        self.assertEqual(detect.detect_stalled_epoch(snapshot(slot=100), [stale_previous]), [])

    def test_boolean_slots_are_never_integers_for_stall_detection(self):
        for previous_slot, current_slot in ((True, 1), (1, True), (False, False)):
            with self.subTest(previous_slot=previous_slot, current_slot=current_slot):
                previous = snapshot(at="2026-08-05T03:00:00+00:00", slot=previous_slot)
                current = snapshot(at="2026-08-05T09:00:00+00:00", slot=current_slot)
                self.assertEqual(detect.detect_stalled_epoch(current, [previous]), [])

    def test_incompatible_slot_schema_is_inert(self):
        previous = snapshot(at="2026-08-05T03:00:00+00:00", slot=100)
        previous["schema_version"] = 7
        current = snapshot(at="2026-08-05T09:00:00+00:00", slot=100)

        self.assertEqual(detect.detect_stalled_epoch(current, [previous]), [])


class TestOutputContract(unittest.TestCase):
    def test_non_no_data_results_publish_actual_min_baseline(self):
        partial = snapshot(slot=200)
        partial["performance"] = {"available": False}
        outputs = [
            analyse(history(3) + [snapshot(slot=200)], min_history=2, min_baseline=2),
            analyse(history(1) + [snapshot(slot=200)], min_history=3, min_baseline=2),
            analyse(history(3) + [partial], min_history=2, min_baseline=2),
            analyse(
                history(3) + [snapshot(slot=200)], min_history=2, min_baseline=2,
                now="2026-08-06T09:00:00+00:00",
            ),
        ]

        self.assertEqual({item["status"] for item in outputs},
                         {"ok", "insufficient_history", "partial_coverage", "stale_snapshot"})
        self.assertTrue(all(item["min_baseline"] == 2 for item in outputs))


class TestReviewAdversarialCases(unittest.TestCase):
    """Regression cases from the backend-readiness review."""

    def test_unknown_schema_baseline_cannot_join_known_schema_latest(self):
        # Raw snapshot count is enough; exact schema identity is not.
        snaps = history(2) + [snapshot(slot=200)]
        for item in snaps:
            item["schema_version"] = 4
        snaps[0:0] = [
            snapshot(at=f"2026-08-05T0{hour}:00:00+00:00", slot=hour)
            for hour in range(3)
        ]
        for item in snaps[:3]:
            item["schema_version"] = 3   # declared foreign schema
        result = analyse(snaps)
        self.assertEqual(result["snapshots_analysed"], 6)   # raw count is enough
        self.assertEqual(result["baseline_size"], 2)        # only exact-schema joins
        self.assertEqual(result["status"], "insufficient_history")

    def test_retained_healthy_flag_behind_ineligible_state_cannot_clear(self):
        for block in (
            {"available": False, "healthy": True, "health_raw": "ok", "slot": 200},
            {"available": True, "stale": True, "source_state": "last_known_good",
             "carried_forward_at": "2026-08-05T09:00:00+00:00",
             "healthy": True, "health_raw": "ok", "slot": 200},
        ):
            latest = snapshot(slot=200)
            latest["network"] = block
            result = analyse(history(3) + [latest])
            found = codes(result)
            self.assertNotIn("network_unhealthy", found, block)
            self.assertIn("network_health_unavailable", found, block)
            self.assertFalse(
                result["coverage"]["network_health"]["current_eligible"], block)
            self.assertEqual(result["status"], "partial_coverage", block)

    def test_unversioned_snapshots_never_become_an_assessable_baseline(self):
        snaps = history(4) + [snapshot(slot=200)]
        for item in snaps:
            item.pop("schema_version")
        result = analyse(snaps)
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["baseline_size"], 0)

    def test_non_integer_schema_declarations_never_match_integer_identity(self):
        for bogus in (4.0, True, "4"):
            snaps = history(3) + [snapshot(slot=200)]
            for item in snaps[:-1]:
                item["schema_version"] = bogus
            result = analyse(snaps)
            self.assertEqual(result["baseline_size"], 0, bogus)
            self.assertEqual(result["status"], "insufficient_history", bogus)

    def test_coingecko_stale_price_is_ineligible_everywhere(self):
        # Real CoinGecko shape: a retained number with an explicit freshness state.
        for state in ("stale", "missing", "unavailable"):
            stale_history = snapshot(at="2026-08-05T08:30:00+00:00")
            stale_history["economics"]["price"] = {
                "available": True, "price_usd": 1000.0, "freshness": state,
                "updated_at_unix": 1754000000,
            }
            result = analyse([stale_history] + history(3) + [snapshot(slot=200)])
            self.assertNotIn("sol_price_move", codes(result), state)
            sol = result["coverage"]["sol_price"]
            self.assertEqual(sol["evaluated"], 3, state)   # carried block excluded
            self.assertEqual(sol["unavailable"], 1, state)

    def test_current_coingecko_stale_price_cannot_trigger_or_clear(self):
        latest = snapshot(slot=200)
        latest["economics"]["price"] = {
            "available": True, "price_usd": 1000.0, "freshness": "stale",
            "updated_at_unix": 1754000000,
        }
        result = analyse(history(3) + [latest])
        self.assertNotIn("sol_price_move", codes(result))
        self.assertFalse(result["coverage"]["sol_price"]["current_eligible"])
        self.assertEqual(result["status"], "partial_coverage")


class TestOrderingAndCounts(unittest.TestCase):
    def test_findings_are_sorted_most_severe_first(self):
        result = analyse(history(3) + [
            snapshot(slot=200, tps=1000.0, slot_time=0.9, delinquent_pct=7.0),
        ])
        order = [f["severity"] for f in result["findings"]]
        self.assertEqual(order, sorted(order, key=detect.SEVERITIES.index))
        self.assertEqual(order[0], "critical")

    def test_counts_match_findings(self):
        result = analyse(history(3) + [snapshot(slot=200, tps=1000.0, slot_time=0.9)])
        self.assertEqual(result["counts"]["critical"], 1)
        self.assertEqual(result["counts"]["warning"], 1)


class TestLoadHistory(unittest.TestCase):
    def test_missing_directory_returns_empty_not_error(self):
        self.assertEqual(detect.load_history(Path("/nonexistent/xyz")), [])


class TestCliOutput(unittest.TestCase):
    def test_limited_coverage_prints_eligible_findings_after_the_limitation(self):
        for status in ("partial_coverage", "insufficient_history"):
            with self.subTest(status=status):
                result = {
                    "status": status,
                    "message": "Baseline evidence is limited.",
                    "findings": [{
                        "severity": "critical",
                        "title": "RPC endpoint reports unhealthy",
                        "detail": "Current endpoint evidence is assessable.",
                        "observed": "unhealthy",
                        "baseline": None,
                    }],
                    "snapshots_analysed": 2,
                    "baseline_size": 1,
                }
                output = io.StringIO()
                with (
                    mock.patch.object(sys, "argv", ["detect.py"]),
                    mock.patch.object(detect, "load_history", return_value=[]),
                    mock.patch.object(detect, "analyse", return_value=result),
                    redirect_stdout(output),
                ):
                    self.assertEqual(detect.main(), 0)

                self.assertIn(result["message"], output.getvalue())
                self.assertIn(result["findings"][0]["title"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
