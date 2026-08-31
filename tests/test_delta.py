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
        "schema_version": 8,
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
            "stablecoins": {
                "available": True,
                "usd_pegged_circulating_usd": 15_800_000_000.0,
            },
            "dex": {"available": True, "volume_24h_usd": 1_700_000_000.0},
        },
        "activity": {
            "available": True,
            "fees": {"available": True, "median_lamports": 5_400},
            "rev": {"available": True, "sample_mean_estimate_sol": 8_000.0},
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


def with_commissions(source, rows, *, slot=100, epoch=1012, available=True):
    source["network"]["slot"] = slot
    source["epoch"]["epoch"] = epoch
    source["validators"].update({
        "available": available,
        "all_validators": [
            {
                "vote_account": vote_account,
                "identity": identity,
                "commission": commission,
                "state": "current",
            }
            for vote_account, identity, commission in rows
        ],
    })
    return source


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


class TestValidatorCommissionChanges(unittest.TestCase):
    def test_commission_changes_join_on_vote_account_and_retain_window_coverage(self):
        before = with_commissions(snapshot(), [
            ("vote-a", "node-old", 5),
            ("vote-b", "node-b", 10),
            ("vote-missing", "node-missing", 7),
        ], slot=100, epoch=1012)
        after = with_commissions(snapshot("2026-08-05T18:00:00+00:00"), [
            ("vote-a", "node-new", 8),
            ("vote-b", "node-b", 10),
            ("vote-added", "node-added", 4),
        ], slot=200, epoch=1013)

        result = delta.compare(before, after)["validator_commission"]

        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["previous_snapshot_epoch"],
                          result["current_snapshot_epoch"]),
                         (1012, 1013))
        self.assertEqual((result["previous_snapshot_slot"],
                          result["current_snapshot_slot"]),
                         (100, 200))
        self.assertEqual((result["previous_account_count"],
                          result["current_account_count"]),
                         (3, 3))
        self.assertEqual((result["matched_account_count"],
                          result["new_account_count"],
                          result["missing_account_count"],
                          result["matched_comparable_count"],
                          result["changed_count"]),
                         (2, 1, 1, 2, 1))
        self.assertEqual((result["previous_comparable_count"],
                          result["current_comparable_count"]),
                         (3, 3))
        self.assertEqual(result["changes"], [{
            "vote_account": "vote-a",
            "previous_identity": "node-old",
            "current_identity": "node-new",
            "previous_commission_pct": 5.0,
            "current_commission_pct": 8.0,
            "change_percentage_points": 3.0,
        }])

    def test_commission_changes_are_sorted_and_zero_changes_are_explicit(self):
        before = with_commissions(snapshot(), [
            ("vote-z", "node-z", 7), ("vote-a", "node-a", 5),
        ])
        after = with_commissions(snapshot("2026-08-05T18:00:00+00:00"), [
            ("vote-a", "node-a", 5), ("vote-z", "node-z", 7),
        ], slot=200)
        result = delta.compare(before, after)["validator_commission"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["changed_count"], 0)
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["matched_account_count"], 2)
        self.assertEqual(result["matched_comparable_count"], 2)

    def test_commission_comparison_refuses_missing_or_unavailable_evidence(self):
        current = with_commissions(snapshot("2026-08-05T18:00:00+00:00"), [
            ("vote-a", "node-a", 5),
        ], slot=200)
        cases = {
            "no earlier rows": snapshot(),
            "unavailable earlier rows": with_commissions(
                snapshot(), [("vote-a", "node-a", 5)], available=False),
            "no exact earlier slot": with_commissions(
                snapshot(), [("vote-a", "node-a", 5)], slot=None),
        }
        for label, before in cases.items():
            with self.subTest(label=label):
                result = delta.compare(before, current)["validator_commission"]
                self.assertEqual(result["status"], "not_comparable")
                self.assertTrue(result["reason"])
                self.assertEqual(result["changes"], [])

    def test_commission_comparison_refuses_two_disjoint_populations(self):
        before = with_commissions(snapshot(), [("vote-a", "node-a", 5)])
        after = with_commissions(snapshot("2026-08-05T18:00:00+00:00"), [
            ("vote-b", "node-b", 7),
        ], slot=200)
        result = delta.compare(before, after)["validator_commission"]
        self.assertEqual(result["status"], "not_comparable")
        self.assertIn("no shared vote accounts", result["reason"])

    def test_commission_comparison_refuses_non_forward_snapshot_context(self):
        cases = {
            "reversed slot": (200, 100, 1012, 1012),
            "equal slot": (100, 100, 1012, 1012),
            "regressed epoch": (100, 200, 1012, 1011),
        }
        for label, (before_slot, after_slot, before_epoch, after_epoch) in cases.items():
            with self.subTest(label=label):
                before = with_commissions(
                    snapshot(), [("vote-a", "node-a", 5)],
                    slot=before_slot, epoch=before_epoch,
                )
                after = with_commissions(
                    snapshot("2026-08-05T18:00:00+00:00"),
                    [("vote-a", "node-a", 7)],
                    slot=after_slot, epoch=after_epoch,
                )
                result = delta.compare(before, after)["validator_commission"]
                self.assertEqual(result["status"], "not_comparable")
                self.assertEqual(result["changes"], [])

    def test_membership_and_comparable_commission_coverage_are_separate(self):
        before = with_commissions(snapshot(), [
            ("vote-a", "node-a", 5), ("vote-b", "node-b", 7),
        ])
        after = with_commissions(snapshot("2026-08-05T18:00:00+00:00"), [
            ("vote-a", "node-a", 5), ("vote-b", "node-b", None),
        ], slot=200)
        result = delta.compare(before, after)["validator_commission"]
        self.assertEqual(result["matched_account_count"], 2)
        self.assertEqual(result["matched_comparable_count"], 1)
        self.assertEqual(result["missing_account_count"], 0)


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
        self.assertIn("economics.price marked unavailable in the newer snapshot", entry["reason"])
        self.assertIsNone(entry["previous"])
        self.assertEqual(entry["current"], None)
        # Nothing anywhere claims a -100% move.
        self.assertNotIn("-100", json.dumps(result))

    def test_current_economics_hold_removes_previous_economic_values(self):
        before = snapshot()
        after = snapshot("2026-08-05T18:00:00+00:00")
        after["economics"] = {"available": False}

        held = by_key(delta.compare(before, after)["not_comparable"])

        for key in ("price_usd", "tvl_usd", "usd_pegged_circulating_usd", "dex_volume_24h_usd"):
            self.assertIsNone(held[key]["previous"])
            self.assertIsNone(held[key]["current"])

    def test_a_metric_new_in_the_later_snapshot_is_not_a_gain_from_zero(self):
        before = snapshot(**{"activity": {"available": False}})
        after = snapshot("2026-08-05T18:00:00+00:00")
        result = delta.compare(before, after)
        entry = by_key(result["not_comparable"])["median_fee_lamports"]
        self.assertEqual(
            entry["reason"], "activity marked unavailable in the earlier snapshot")
        self.assertEqual(entry["previous"], None)

    def test_a_metric_absent_from_both_says_so_once(self):
        bare = {"collected_at": "2026-08-05T12:00:00+00:00", "schema_version": 8}
        result = delta.compare(bare, {"collected_at": "2026-08-05T18:00:00+00:00",
                                      "schema_version": 8})
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

    def test_slot_context_uses_report_policy_not_an_obsolete_protocol_target(self):
        metric = next(item for item in delta.METRICS if item["key"] == "mean_slot_time_secs")
        self.assertIn("0.60s slow-slot alert threshold", metric["verify"])
        self.assertNotIn("0.400", metric["why"] + metric["verify"])

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
                         **{"activity.rev.sample_mean_estimate_sol": 12_000.0})  # +50%
        entry = by_key(delta.compare(snapshot(), after)["changes"])["sample_mean_rev_sol"]
        self.assertEqual(entry["basis"], "sampled")

    def test_the_noisy_sampled_metric_has_a_looser_threshold_than_a_measured_one(self):
        # A 12% swing in an extrapolated REV estimate is sampling noise; the
        # same swing in measured TPS is a real event. The table must reflect it.
        rev = snapshot("2026-08-05T18:00:00+00:00",
                       **{"activity.rev.sample_mean_estimate_sol": 8_960.0})   # +12%
        tps = snapshot("2026-08-05T18:00:00+00:00",
                       **{"performance.latest_tps": 3_360.0})           # +12%
        self.assertIn("sample_mean_rev_sol",
                      by_key(delta.compare(snapshot(), rev)["steady"]))
        self.assertIn("latest_tps", by_key(delta.compare(snapshot(), tps)["changes"]))

    def test_non_finite_metrics_are_not_comparable(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                after = snapshot("2026-08-05T18:00:00+00:00",
                                 **{"performance.latest_tps": value})
                result = delta.compare(snapshot(), after)
                entry = by_key(result["not_comparable"])["latest_tps"]
                self.assertIsNone(entry["current"])
                self.assertNotIn("latest_tps", by_key(result["changes"] + result["steady"]))


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


class TestReadinessGates(unittest.TestCase):
    """A comparison is only published when the interval, schemas and source
    states on both sides are trustworthy enough to compare at all."""

    def test_stale_identical_activity_never_becomes_steady(self):
        carried = {
            "available": True,
            "source_state": "last_known_good",
            "carried_forward_at": "2026-08-05T18:00:00+00:00",
            "fees": {"available": True, "median_lamports": 5_400},
            "rev": {"available": True, "sample_mean_estimate_sol": 8_000.0},
        }
        after = snapshot("2026-08-05T18:00:00+00:00", **{"activity": carried})
        result = delta.compare(snapshot(), after)
        keys = by_key(result["not_comparable"])
        for key in ("median_fee_lamports", "sample_mean_rev_sol"):
            self.assertIn(key, keys)
            self.assertNotIn(key, by_key(result["changes"]))
            self.assertNotIn(key, by_key(result["steady"]))
            self.assertIn("carried forward", keys[key]["reason"])
        self.assertEqual(result["counts"]["changed"], 0)
        self.assertEqual(result["counts"]["steady"], 12)

    def test_a_stale_flag_without_last_known_good_also_blocks_comparison(self):
        stale = {
            "available": True, "stale": True,
            "fees": {"available": True, "median_lamports": 5_400},
            "rev": {"available": True, "sample_mean_estimate_sol": 8_000.0},
        }
        after = snapshot("2026-08-05T18:00:00+00:00", **{"activity": stale})
        result = delta.compare(snapshot(), after)
        keys = by_key(result["not_comparable"])
        self.assertIn("median_fee_lamports", keys)
        self.assertNotIn("median_fee_lamports",
                         by_key(result["changes"] + result["steady"]))
        self.assertIn("stale", keys["median_fee_lamports"]["reason"])

    def test_an_unavailable_section_is_named_rather_than_compared(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"performance.available": False})
        result = delta.compare(snapshot(), after)
        entry = by_key(result["not_comparable"])["latest_tps"]
        self.assertIn("unavailable", entry["reason"])
        self.assertNotIn("latest_tps", by_key(result["changes"] + result["steady"]))

    def test_cross_schema_snapshots_use_per_metric_compatibility(self):
        unversioned = delta.compare(snapshot(**{"schema_version": None}),
                                    snapshot("2026-08-05T18:00:00+00:00",
                                             **{"schema_version": None}))
        self.assertEqual(unversioned["status"], "incompatible_schemas")
        self.assertIn("declares no schema_version", unversioned["message"])

        mismatched = snapshot(**{"schema_version": 3})
        newer = snapshot("2026-08-05T18:00:00+00:00", **{"schema_version": 4})
        result = delta.compare(mismatched, newer)
        self.assertEqual(result["status"], "ok")
        self.assertIn("latest_tps", by_key(result["steady"]))
        self.assertIn("sample_mean_rev_sol", by_key(result["not_comparable"]))

    def test_schema_nine_preserves_schema_eight_metric_comparisons(self):
        expected = {spec["key"] for spec in delta.METRICS}
        for earlier_schema, newer_schema in ((8, 9), (9, 9)):
            with self.subTest(pair=(earlier_schema, newer_schema)):
                before = snapshot(**{"schema_version": earlier_schema})
                after = snapshot(
                    "2026-08-05T18:00:00+00:00",
                    **{"schema_version": newer_schema},
                )

                result = delta.compare(before, after)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["counts"], {
                    "changed": 0,
                    "steady": len(expected),
                    "not_comparable": 0,
                })
                self.assertEqual(set(by_key(result["steady"])), expected)

    def test_only_explicitly_supported_integer_schemas_are_compared(self):
        def refused(older_value, newer_value):
            older = snapshot(**{"schema_version": older_value})
            newer = snapshot("2026-08-05T18:00:00+00:00",
                             **{"schema_version": newer_value})
            result = delta.compare(older, newer)
            self.assertEqual(result["status"], "incompatible_schemas", (older_value, newer_value))
            self.assertEqual((result["changes"], result["steady"],
                              result["not_comparable"]), ([], [], []))
            return result

        for older_value, newer_value in (
            (None, 7),          # version missing on one side
            (7.0, 7),           # float is not an integer declaration
            (True, True),       # boolean is a flag, not a schema version
            ("7", "7"),         # string versions are not declarations
            (99, 99),           # plausible integer outside the supported set
        ):
            with self.subTest(pair=(older_value, newer_value)):
                refused(older_value, newer_value)

        matched = delta.compare(snapshot(**{"schema_version": 7}),
                                snapshot("2026-08-05T18:00:00+00:00",
                                         **{"schema_version": 7}))
        self.assertEqual(matched["status"], "ok")

    def test_a_stale_freshness_price_is_not_comparable(self):
        # The real collector contract: summarize_price keeps available True and
        # records the age verdict separately as freshness (economics.py).
        stale_price = {"available": True, "price_usd": 74.0, "freshness": "stale"}
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": stale_price})
        result = delta.compare(snapshot(), after)
        keys = by_key(result["not_comparable"])
        self.assertIn("price_usd", keys)
        self.assertNotIn("price_usd", by_key(result["changes"] + result["steady"]))
        self.assertIn("stale", keys["price_usd"]["reason"])

    def test_a_fresh_freshness_price_still_compares(self):
        fresh_price = {"available": True, "price_usd": 80.0, "freshness": "fresh"}
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": fresh_price})
        entry = by_key(delta.compare(snapshot(), after)["changes"])["price_usd"]
        self.assertEqual(entry["direction"], "up")

    def test_a_price_with_no_freshness_evidence_never_becomes_a_comparison(self):
        # Adversarial: a plausible numeric price whose age nobody recorded.
        unverified = {"available": True, "price_usd": 74.0, "freshness": "missing"}
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": unverified})
        result = delta.compare(snapshot(), after)
        keys = by_key(result["not_comparable"])
        self.assertIn("price_usd", keys)
        self.assertNotIn("price_usd", by_key(result["changes"] + result["steady"]))
        self.assertIn("no freshness evidence", keys["price_usd"]["reason"])

    def test_a_recorded_price_without_an_observed_clock_is_still_comparable(self):
        # "recorded" means the source stamped its own time but collection did
        # not; the real path supplies observation time and never emits it here,
        # so it must not be treated as a defect.
        recorded = {"available": True, "price_usd": 80.0, "freshness": "recorded"}
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": recorded})
        entry = by_key(delta.compare(snapshot(), after)["changes"])["price_usd"]
        self.assertEqual(entry["direction"], "up")

    def test_a_price_with_unavailable_freshness_is_not_comparable(self):
        unverified = {"available": True, "price_usd": 74.0,
                      "freshness": "unavailable"}
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"economics.price": unverified})
        result = delta.compare(snapshot(), after)
        keys = by_key(result["not_comparable"])
        self.assertIn("price_usd", keys)
        self.assertNotIn("price_usd", by_key(result["changes"] + result["steady"]))
        self.assertIn("freshness unavailable", keys["price_usd"]["reason"])

    def test_malformed_equal_reversed_and_ambiguous_intervals_never_return_ok(self):
        cases = {
            "malformed": ("not-a-time", "2026-08-05T18:00:00+00:00"),
            "equal": ("2026-08-05T18:00:00+00:00", "2026-08-05T18:00:00+00:00"),
            "reversed": ("2026-08-05T19:00:00+00:00", "2026-08-05T18:00:00+00:00"),
            "mixed-naive": ("2026-08-05T12:00:00", "2026-08-05T18:00:00+00:00"),
            "both-naive": ("2026-08-05T12:00:00", "2026-08-05T18:00:00"),
            "date-only-both-sides": ("2026-08-04", "2026-08-05"),
            "date-only-one-side": ("2026-08-05", "2026-08-06T12:00:00+00:00"),
            "same-instant-different-offset": (
                "2026-08-05T13:00:00-05:00", "2026-08-05T18:00:00+00:00"),
        }
        for label, (before_at, after_at) in cases.items():
            with self.subTest(case=label):
                result = delta.compare(snapshot(before_at), snapshot(after_at))
                self.assertEqual(result["status"], "invalid_interval")
                self.assertTrue(result["message"])
                self.assertEqual((result["changes"], result["steady"],
                                  result["not_comparable"]), ([], [], []))
                self.assertEqual(result["counts"]["changed"] +
                                 result["counts"]["steady"] +
                                 result["counts"]["not_comparable"], 0)

    def test_offsets_are_resolved_as_instants_not_strings(self):
        # 17:00-04:00 is 21:00Z — after 20:00+00:00 as an instant, however much
        # the string itself sorts lower. Ordering must judge the instant.
        result = delta.compare(snapshot("2026-08-05T22:00:00+00:00"),
                               snapshot("2026-08-05T17:00:00-04:00"))
        self.assertEqual(result["status"], "invalid_interval")

    def test_fresh_same_schema_data_still_compares_as_before(self):
        after = snapshot("2026-08-05T18:00:00+00:00",
                         **{"performance.latest_tps": 4000.0})
        result = delta.compare(snapshot(), after)
        self.assertEqual(result["status"], "ok")
        self.assertIn("latest_tps", by_key(result["changes"]))
        self.assertEqual(result["elapsed_seconds"], 21_600)

    def test_a_wrong_typed_value_reads_differently_from_a_missing_one(self):
        wrong_type = snapshot("2026-08-05T18:00:00+00:00",
                              **{"validators.active_count": "1000"})
        entry = by_key(delta.compare(snapshot(), wrong_type)["not_comparable"])
        self.assertIn("usable number", entry["active_count"]["reason"])
        self.assertNotIn("not present", entry["active_count"]["reason"])

        absent = snapshot("2026-08-05T18:00:00+00:00",
                          **{"validators.active_count": None})
        entry = by_key(delta.compare(snapshot(), absent)["not_comparable"])
        self.assertEqual(entry["active_count"]["reason"],
                         "not present in the newer snapshot")


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
