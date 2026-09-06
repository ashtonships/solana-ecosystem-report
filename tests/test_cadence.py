"""Deterministic contracts for source-tier refresh decisions."""

import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cadence  # noqa: E402


NOW = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)


class CadenceTests(unittest.TestCase):
    def test_fixed_source_intervals_match_the_public_tiers(self):
        self.assertEqual(cadence.INTERVALS, {
            "activity": 3_600,
            "block_production": 3_600,
            "feature_activation": 3_600,
            "news": 21_600,
            "growth_providers": 21_600,
            "growth_tokens": 21_600,
            "dune": 86_400,
        })

    def test_only_an_explicit_valid_entry_can_be_due(self):
        schedule = cadence.initial_schedule()
        self.assertTrue(cadence.source_due(schedule, "activity", NOW))
        self.assertFalse(cadence.source_due({}, "activity", NOW))
        self.assertFalse(cadence.source_due(schedule, "unknown", NOW))

        malformed = deepcopy(schedule)
        malformed["activity"]["interval_seconds"] = 1
        self.assertFalse(cadence.source_due(malformed, "activity", NOW))

        future = deepcopy(schedule)
        future["activity"]["last_attempt_at"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
        self.assertFalse(cadence.source_due(future, "activity", NOW))

    def test_legacy_token_tier_migrates_without_resetting_source_clocks(self):
        prior = cadence.initial_schedule()
        stamp = (NOW - timedelta(hours=10)).isoformat()
        prior["growth_tokens"].update(
            interval_seconds=86_400, last_attempt_at=stamp,
            last_success_at=stamp, state="reused",
        )
        original = deepcopy(prior)

        current = cadence.collection_schedule(prior, NOW)

        self.assertEqual(prior, original)
        self.assertEqual(current["growth_tokens"], {
            **prior["growth_tokens"], "interval_seconds": 21_600,
        })
        self.assertTrue(cadence.source_due(current, "growth_tokens", NOW))
        self.assertEqual(current["dune"]["interval_seconds"], 86_400)

    def test_token_refresh_matches_supply_freshness_boundary(self):
        import growth

        self.assertEqual(cadence.INTERVALS["growth_tokens"], growth.SUPPLY_FRESH_SECONDS)
        schedule = cadence.initial_schedule()
        cadence.update_source(schedule, "growth_tokens", NOW,
                              attempted=True, succeeded=True)
        self.assertFalse(cadence.source_due(
            schedule, "growth_tokens", NOW + timedelta(hours=6, seconds=-1)))
        self.assertTrue(cadence.source_due(
            schedule, "growth_tokens", NOW + timedelta(hours=6)))

    def test_legacy_interval_exception_does_not_weaken_other_tiers(self):
        self.assertTrue(cadence.valid_interval("growth_tokens", 86_400))
        for key, value in (("news", 86_400), ("dune", 21_600),
                           ("growth_tokens", 3_600), ("growth_tokens", True),
                           ("unknown", 86_400)):
            self.assertFalse(cadence.valid_interval(key, value))

    def test_failed_attempt_is_the_retry_anchor_and_does_not_erase_success(self):
        schedule = cadence.initial_schedule()
        previous_success = (NOW - timedelta(hours=2)).isoformat()
        schedule["activity"].update({
            "last_attempt_at": previous_success,
            "last_success_at": previous_success,
            "state": "fresh",
        })

        cadence.update_source(
            schedule, "activity", NOW, attempted=True, succeeded=False,
        )

        self.assertEqual(schedule["activity"]["last_success_at"], previous_success)
        self.assertEqual(schedule["activity"]["state"], "failed")
        self.assertFalse(cadence.source_due(
            schedule, "activity", NOW + timedelta(minutes=59),
        ))
        self.assertTrue(cadence.source_due(
            schedule, "activity", NOW + timedelta(hours=1),
        ))

    def test_reuse_changes_only_state(self):
        schedule = cadence.initial_schedule()
        stamp = (NOW - timedelta(minutes=10)).isoformat()
        schedule["news"].update({
            "last_attempt_at": stamp, "last_success_at": stamp, "state": "fresh",
        })

        cadence.update_source(
            schedule, "news", NOW, attempted=False, succeeded=False,
        )

        self.assertEqual(schedule["news"], {
            "last_attempt_at": stamp,
            "last_success_at": stamp,
            "interval_seconds": 21_600,
            "state": "reused",
        })

    def test_failed_source_stays_failed_until_an_actual_success(self):
        schedule = cadence.initial_schedule()
        attempt = (NOW - timedelta(minutes=10)).isoformat()
        success = (NOW - timedelta(hours=2)).isoformat()
        schedule["activity"].update({
            "last_attempt_at": attempt,
            "last_success_at": success,
            "state": "failed",
        })

        cadence.update_source(
            schedule, "activity", NOW, attempted=False, succeeded=False,
        )

        self.assertEqual(schedule["activity"]["state"], "failed")
        self.assertEqual(schedule["activity"]["last_attempt_at"], attempt)
        self.assertEqual(schedule["activity"]["last_success_at"], success)


if __name__ == "__main__":
    unittest.main()
