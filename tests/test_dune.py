"""Dune adapter + integration tests. Mock/fixture only — no network, no secrets."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import collect
import dune
import render

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)

QUERY_ID = "4242"

FRESH_ROWS = [
    {
        "metric_id": "daily_non_vote_fee_payers",
        "day": "2026-09-01",
        "dimension": None,
        "value": 1_234_567.0,
        "unit": "fee_payers",
        "sample_count": 5_000_000,
    },
    {
        "metric_id": "daily_dex_volume_total",
        "day": "2026-09-01",
        "dimension": None,
        "value": 987_654_321.0,
        "unit": "usd",
        "sample_count": 1_000_000,
    },
]


def _result_payload(ended_at: datetime, rows: list[dict] | None = None) -> dict:
    return {
        "state": "QUERY_STATE_COMPLETED",
        "execution_id": "exec-1",
        "result": {
            "execution_started_at": (ended_at - timedelta(seconds=42)).isoformat(),
            "execution_ended_at": ended_at.isoformat(),
            "row_count": len(rows if rows is not None else FRESH_ROWS),
            "datapoint_count": 12,
            "rows": copy.deepcopy(rows if rows is not None else FRESH_ROWS),
        },
    }


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _no_sleep(_seconds):
    return None


class IsConfiguredTests(unittest.TestCase):
    def test_unconfigured_when_either_env_missing(self):
        self.assertFalse(dune.is_configured(env={}))
        self.assertFalse(dune.is_configured(env={"DUNE_API_KEY": "k"}))
        self.assertFalse(dune.is_configured(env={"DUNE_QUERY_ID": "1"}))
        self.assertTrue(dune.is_configured(env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": "1"}))

    def test_collect_dune_unconfigured_returns_unavailable_not_error(self):
        self.assertEqual(
            dune.collect_dune(env={}, now=NOW),
            {"available": False, "reason": "dune query not configured"},
        )
        self.assertEqual(
            dune.collect_dune(env={"DUNE_API_KEY": "k"}, now=NOW),
            {"available": False, "reason": "dune query not configured"},
        )


class CollectDuneTests(unittest.TestCase):
    def test_configured_fresh(self):
        payload = _result_payload(NOW - timedelta(minutes=10))
        with mock.patch.object(
            dune, "_request", return_value=(200, payload),
        ):
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID}, now=NOW,
            )
        self.assertTrue(section["available"])
        self.assertEqual(section["freshness"], "fresh")
        self.assertEqual(section["state"], "fresh")
        self.assertEqual(section["query_id"], QUERY_ID)
        self.assertEqual(section["query_url"], f"https://dune.com/queries/{QUERY_ID}")
        self.assertEqual(section["source_url"], "https://dune.com/")
        self.assertEqual(section["execution_id"], "exec-1")
        self.assertEqual(section["row_count"], 2)
        self.assertEqual(section["datapoint_count"], 12)
        self.assertIsInstance(section["result_sha256"], str)
        self.assertEqual(len(section["result_sha256"]), 64)
        self.assertEqual(section["columns"], list(dune.EXPECTED_COLUMNS))
        self.assertIsNone(section["last_known_good"])

    def test_stale_triggers_execute_and_poll_then_fresh(self):
        stale_payload = _result_payload(NOW - timedelta(hours=48))
        fresh_payload = _result_payload(NOW - timedelta(minutes=1))
        calls = []

        def fake_request(url, api_key, method="GET", body=None):
            calls.append((method, url))
            if method == "POST":
                self.assertTrue(url.endswith(f"/query/{QUERY_ID}/results/execute"))
                return 200, {"execution_id": "exec-2"}
            return 200, fresh_payload

        with mock.patch.object(dune, "_request", side_effect=fake_request):
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID},
                now=NOW,
                sleep=_no_sleep,
            )
        methods = [m for m, _ in calls]
        self.assertIn("POST", methods)
        self.assertTrue(section["available"])
        self.assertEqual(section["freshness"], "fresh")
        self.assertEqual(section["execution_id"], "exec-1")

    def test_schema_drift_rejected_as_unavailable(self):
        drifted = [
            {"metric_id": "x", "day": "2026-09-01", "value": 1.0},  # missing 3 columns
        ]
        payload = _result_payload(NOW - timedelta(minutes=5), rows=drifted)
        with mock.patch.object(dune, "_request", return_value=(200, payload)):
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID}, now=NOW,
            )
        self.assertFalse(section["available"])
        self.assertIn("schema drift", section["reason"])

    def test_execution_failure_keeps_last_known_good_with_age(self):
        stale_payload = _result_payload(NOW - timedelta(hours=48))

        def fake_request(url, api_key, method="GET", body=None):
            if method == "POST":
                return 200, {"execution_id": "exec-2"}
            # Poll never completes; the fixed 120s deadline expires.
            return 200, {"state": "QUERY_STATE_PENDING"}

        with mock.patch.object(dune, "_request", side_effect=fake_request):
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID},
                now=NOW,
                sleep=_no_sleep,
            )
        self.assertFalse(section["available"])
        self.assertIn("timed out", section["reason"])
        lkg = section.get("last_known_good")
        self.assertIsInstance(lkg, dict)
        self.assertEqual(lkg["query_id"], QUERY_ID)
        self.assertEqual(lkg["age_seconds"], 48 * 3600)
        self.assertIsInstance(lkg["result_sha256"], str)

    def test_retry_on_429_then_success(self):
        payload = _result_payload(NOW - timedelta(minutes=10))
        responses = [(429, {"error": "rate limited"}), (200, payload)]
        with mock.patch.object(dune, "_request", side_effect=lambda *a, **k: responses.pop(0)) as rm, \
                mock.patch.object(dune.time, "sleep") as sleep_mock:
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID}, now=NOW,
            )
        self.assertEqual(rm.call_count, 2)
        sleep_mock.assert_called_once_with(2.0)
        self.assertTrue(section["available"])

    def test_all_failures_degrade_without_raising(self):
        # Transport exception at every layer still returns a dict.
        with mock.patch.object(
            dune, "_request", side_effect=OSError("boom"),
        ), mock.patch.object(dune.time, "sleep"):
            section = dune.collect_dune(
                env={"DUNE_API_KEY": "k", "DUNE_QUERY_ID": QUERY_ID}, now=NOW,
            )
        self.assertFalse(section["available"])
        self.assertIn("reason", section)


class WiringTests(unittest.TestCase):
    def test_sources_stores_dune_only_when_with_dune(self):
        made = {}

        def fake_build_snapshot(*args, **kwargs):
            made["dune_kwarg"] = kwargs.get("dune")
            # Return the kwargs minimally; validate() is not under test here.
            return {"schema_version": collect.SCHEMA_VERSION}

        with mock.patch.object(collect, "fetch_rpc", return_value={}), \
                mock.patch.object(collect, "index_results", return_value={}), \
                mock.patch.object(collect, "fetch_block_time", return_value=None), \
                mock.patch.object(collect.blocks, "completed_epoch_range", return_value=None), \
                mock.patch.object(collect.blocks, "fetch_block_production", return_value=[]), \
                mock.patch.object(collect.blocks, "normalize_block_production", return_value={}), \
                mock.patch.object(collect.blocks, "collect_activity", return_value=None), \
                mock.patch.object(collect.news_module, "collect_news", return_value=None), \
                mock.patch.object(collect.growth_module, "collect_growth", return_value=({}, None)), \
                mock.patch.object(collect.dune_module, "collect_dune", return_value={"available": True}), \
                mock.patch.object(collect, "build_snapshot", side_effect=fake_build_snapshot), \
                mock.patch.object(collect.pipeline, "validate", side_effect=lambda s: s):
            collect.collect_with_state("http://endpoint.invalid", with_dune=True)
            self.assertEqual(made["dune_kwarg"], {"available": True})
            collect.collect_with_state("http://endpoint.invalid")
            self.assertIsNone(made["dune_kwarg"])

    def test_build_snapshot_omits_dune_key_when_not_collected(self):
        snapshot = collect.build_snapshot({}, "2026-09-02T00:00:00+00:00", "https://api.mainnet-beta.solana.com")
        self.assertNotIn("dune", snapshot)
        snapshot_with = collect.build_snapshot(
            {}, "2026-09-02T00:00:00+00:00", "https://api.mainnet-beta.solana.com",
            dune={"available": False, "reason": "dune query not configured"},
        )
        self.assertIn("dune", snapshot_with)


class ProjectionInvariantTests(unittest.TestCase):
    def _dune_section(self) -> dict:
        return {
            "available": True,
            "requires_api_key": True,
            "query_id": QUERY_ID,
            "query_url": f"https://dune.com/queries/{QUERY_ID}",
            "execution_id": "exec-1",
            "execution_started_at": "2026-09-02T11:59:18+00:00",
            "execution_ended_at": "2026-09-02T12:00:00+00:00",
            "result_age_seconds": 0,
            "row_count": 2,
            "datapoint_count": 12,
            "result_sha256": "a" * 64,
            "source_url": "https://dune.com/",
            "freshness": "fresh",
            "columns": list(dune.EXPECTED_COLUMNS),
            "state": "fresh",
            "last_known_good": {
                "query_id": QUERY_ID,
                "query_url": f"https://dune.com/queries/{QUERY_ID}",
                "source_url": f"https://api.dune.com/api/v1/query/{QUERY_ID}/results",
                "execution_id": "exec-0",
                "execution_started_at": "2026-09-01T11:00:00+00:00",
                "execution_ended_at": "2026-09-01T11:00:10+00:00",
                "age_seconds": 90000,
                "row_count": 2,
                "datapoint_count": 12,
                "result_sha256": "b" * 64,
                "execution_started_at_parsed": "2026-09-01T11:00:00+00:00",
                "execution_ended_at_parsed": "2026-09-01T11:00:10+00:00",
            },
            "reason": None,
            "raw_rows": [{"metric_id": "daily_dex_volume_total"}],  # must be projected away
        }

    def test_dune_shaped_snapshot_projects_to_allowed_fields_only(self):
        snapshot = collect.build_snapshot(
            {}, "2026-09-02T12:00:00+00:00", "https://api.mainnet-beta.solana.com",
            dune=self._dune_section(),
        )
        projected = render.project_public_envelope(snapshot)
        self.assertIn("dune", projected)
        self.assertIn("raw_rows", self._dune_section())
        self.assertNotIn("raw_rows", projected["dune"])
        allowed = render.PUBLIC_SCHEMA_OVERRIDES[9]["dune"]
        self.assertEqual(set(projected["dune"].keys()), allowed)
        lkg_allowed = render.PUBLIC_SCHEMA_OVERRIDES[9]["dune.last_known_good"]
        self.assertEqual(set(projected["dune"]["last_known_good"].keys()), lkg_allowed)
        # Round-trip: projecting the projection changes nothing.
        self.assertEqual(render.project_public_envelope(projected), projected)

    def test_snapshot_without_dune_projects_byte_identically(self):
        baseline = collect.build_snapshot(
            {}, "2026-09-02T12:00:00+00:00", "https://api.mainnet-beta.solana.com",
        )
        without = copy.deepcopy(baseline)
        projected_before = render.project_public_envelope(baseline)
        # Simulate an older render.py without the dune contract: same snapshot
        # must project identically whether or not the dune override exists.
        with mock.patch.dict(
            render.PUBLIC_SCHEMA_OVERRIDES[9], {"dune": None}, clear=False,
        ):
            # Remove the override entirely to emulate the pre-change contract.
            saved = render.PUBLIC_SCHEMA_OVERRIDES[9].pop("dune", None)
            saved_lkg = render.PUBLIC_SCHEMA_OVERRIDES[9].pop("dune.last_known_good", None)
            try:
                projected_old = render.project_public_envelope(baseline)
            finally:
                if saved is not None:
                    render.PUBLIC_SCHEMA_OVERRIDES[9]["dune"] = saved
                if saved_lkg is not None:
                    render.PUBLIC_SCHEMA_OVERRIDES[9]["dune.last_known_good"] = saved_lkg
        projected_new = render.project_public_envelope(without)
        self.assertEqual(
            json.dumps(projected_before, sort_keys=True),
            json.dumps(projected_old, sort_keys=True),
        )
        self.assertEqual(
            json.dumps(projected_before, sort_keys=True),
            json.dumps(projected_new, sort_keys=True),
        )
        self.assertNotIn("dune", projected_old)


if __name__ == "__main__":
    unittest.main()
