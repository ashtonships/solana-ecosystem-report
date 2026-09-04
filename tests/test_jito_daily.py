"""Synthetic checks for the held Jito daily-MEV adapter; no live requests."""

from datetime import date, datetime, timedelta, timezone
import json
import math
import unittest
from unittest import mock

import jito_daily
import transport


NOW = datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)


def row(day: date, *, count=100, jito=1.25, validators=20.5, tippers=40):
    return {
        "day": f"{day.isoformat()} 00:00:00.000 UTC",
        "count_mev_tips": count,
        "jito_tips": jito,
        "validator_tips": validators,
        "tippers": tippers,
    }


class FakeResponse:
    def test_oversized_amount_is_rejected(self):
        self.assertFalse(jito_daily._amount(10 ** 400))

    def __init__(self, body: bytes):
        self.body = body
        self.read_limit = None

    def read(self, limit=-1):
        self.read_limit = limit
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestJitoDaily(unittest.TestCase):
    def test_returns_newest_30_completed_days_and_excludes_current_utc_day(self):
        raw = [
            row(NOW.date() - timedelta(days=offset), count=offset,
                jito=offset + 0.25, validators=offset + 10.5, tippers=offset + 20)
            for offset in range(35, -1, -1)
        ]
        result = jito_daily.parse_daily_rewards(list(reversed(raw)), NOW)

        self.assertTrue(result["available"])
        self.assertEqual(result["completed_day_count"], 35)
        self.assertEqual(result["returned_day_count"], 30)
        self.assertEqual(len(result["rows"]), 30)
        self.assertEqual(
            result["rows"][0]["day"],
            f"{(NOW.date() - timedelta(days=30)).isoformat()} 00:00:00.000 UTC",
        )
        self.assertEqual(
            result["rows"][-1]["day"],
            f"{(NOW.date() - timedelta(days=1)).isoformat()} 00:00:00.000 UTC",
        )
        self.assertNotIn(NOW.date().isoformat(), {
            item["day"][:10] for item in result["rows"]
        })
        self.assertEqual(result["units"]["jito_tips"], "SOL")
        self.assertEqual(result["units"]["validator_tips"], "SOL")
        self.assertEqual(
            result["publication_status"],
            "held_pending_owner_acceptance_of_jito_tooling_terms",
        )

    def test_preserves_documented_fields_without_combining_tip_amounts(self):
        result = jito_daily.parse_daily_rewards([
            row(NOW.date() - timedelta(days=1), jito=2.5, validators=70.75),
        ], NOW)
        self.assertEqual(result["rows"][0], {
            "day": "2026-09-03 00:00:00.000 UTC",
            "count_mev_tips": 100,
            "jito_tips": 2.5,
            "validator_tips": 70.75,
            "tippers": 40,
        })
        for field in ("gross_rev", "protocol_rev", "total_tips"):
            self.assertNotIn(field, result)
            self.assertNotIn(field, result["rows"][0])
        self.assertIn("no combined tips", result["note"])

    def test_invalid_or_duplicate_days_fail_the_entire_response_closed(self):
        valid = row(NOW.date() - timedelta(days=1))
        cases = {
            "duplicate": [valid, dict(valid)],
            "invalid-date": [{**valid, "day": "2026-02-30 00:00:00.000 UTC"}],
            "not-midnight": [{**valid, "day": "2026-09-03 12:00:00.000 UTC"}],
            "missing-utc": [{**valid, "day": "2026-09-03"}],
            "future": [row(NOW.date() + timedelta(days=1))],
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                result = jito_daily.parse_daily_rewards(payload, NOW)
                self.assertFalse(result["available"])
                self.assertNotIn("rows", result)

    def test_invalid_counts_or_sol_amounts_fail_closed(self):
        day = NOW.date() - timedelta(days=1)
        cases = (
            {"count": -1}, {"count": 1.5}, {"count": True},
            {"tippers": -1}, {"tippers": 1.5}, {"tippers": False},
            {"jito": -0.1}, {"jito": math.nan}, {"jito": math.inf}, {"jito": True},
            {"validators": -0.1}, {"validators": math.nan},
            {"validators": math.inf}, {"validators": False},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                result = jito_daily.parse_daily_rewards([row(day, **changes)], NOW)
                self.assertFalse(result["available"])
                self.assertNotIn("rows", result)

    def test_malformed_top_level_rows_and_reference_time_fail_closed(self):
        valid = row(NOW.date() - timedelta(days=1))
        for raw in (None, {}, [], "bad", [None], [{"day": valid["day"]}]):
            with self.subTest(raw=raw):
                self.assertFalse(jito_daily.parse_daily_rewards(raw, NOW)["available"])
        self.assertFalse(
            jito_daily.parse_daily_rewards([valid], datetime(2026, 9, 4))["available"]
        )
        self.assertFalse(jito_daily.parse_daily_rewards([row(NOW.date())], NOW)["available"])

    def test_fetch_uses_exact_keyless_endpoint_and_shared_bounded_reader(self):
        response = FakeResponse(json.dumps([
            row(NOW.date() - timedelta(days=1)),
        ]).encode())
        with mock.patch.object(
            jito_daily.urllib.request, "urlopen", return_value=response,
        ) as urlopen:
            payload = jito_daily.fetch_daily_rewards(timeout=7)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, jito_daily.SOURCE_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(response.read_limit, transport.MAX_RESPONSE_BYTES + 1)
        self.assertEqual(payload[0]["count_mev_tips"], 100)

    def test_oversized_or_invalid_json_transport_returns_none(self):
        for body in (
            b"x" * (transport.MAX_RESPONSE_BYTES + 1),
            b"not-json",
            b"\xff",
        ):
            with self.subTest(size=len(body)), mock.patch.object(
                jito_daily.urllib.request, "urlopen", return_value=FakeResponse(body),
            ):
                self.assertIsNone(jito_daily.fetch_daily_rewards())

    def test_collect_uses_injected_clock_and_never_fetches_in_parser_tests(self):
        payload = [row(NOW.date() - timedelta(days=1))]
        with mock.patch.object(
            jito_daily, "fetch_daily_rewards", return_value=payload,
        ) as fetch:
            result = jito_daily.collect_jito_daily(NOW, timeout=9)
        fetch.assert_called_once_with(9)
        self.assertTrue(result["available"])
        self.assertEqual(result["observed_at"], "2026-09-04T18:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
