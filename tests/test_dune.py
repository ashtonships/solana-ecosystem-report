"""Dune adapter + integration tests. Mock/fixture only — no network, no secrets."""

from __future__ import annotations

import copy
import json
import io
import tempfile
import urllib.error
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import collect
import dune
import render
import pipeline

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
    if rows is None:
        rows = copy.deepcopy(FRESH_ROWS)
        for row in rows:
            row["day"] = (ended_at.date() - timedelta(days=1)).isoformat()
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

    def read(self, limit=-1):
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


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class CollectDuneTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(dune.time, "monotonic", side_effect=self.clock.monotonic).start()
        mock.patch.object(dune.time, "sleep", side_effect=self.clock.sleep).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "budget.json"
        self.ledger.write_text('{"version":1,"attempts":{}}')
        self.read_ledger = Path(self.tmp.name) / "reads.json"
        self.read_policy = {
            "version": 1, "starts_on": "2026-09-01", "expires_on": "2026-09-10",
            "query_id": QUERY_ID,
            "total_read_limit": 100, "daily_read_limit": 100,
            "max_rows_per_read": 500, "reservations": {},
        }
        self.read_ledger.write_text(json.dumps(self.read_policy))
        self.env = {"DUNE_API_KEY": "mock-key", "DUNE_QUERY_ID": QUERY_ID,
                    "DUNE_EXECUTION_ENABLED": "true", "DUNE_EXECUTION_LEDGER": str(self.ledger),
                    "DUNE_PAID_READS_ENABLED": "true", "DUNE_RESULT_READ_LEDGER": str(self.read_ledger)}

    def collect(self, payload, **kwargs):
        self.read_ledger.write_text(json.dumps(self.read_policy))
        with mock.patch.object(dune, "_request", return_value=(200, payload)) as request:
            result = dune.collect_dune(env=self.env, now=NOW, **kwargs)
        return result, request

    def test_fresh_result_is_complete_dated_and_never_executes(self):
        result, request = self.collect(_result_payload(NOW - timedelta(minutes=10)))
        self.assertTrue(result["available"])
        self.assertEqual(result["result_age_seconds"], 600)
        self.assertEqual(result["aggregates"]["latest_day"], "2026-09-01")
        self.assertEqual(result["aggregation_contract"], "completed-utc-days-v1")
        self.assertNotIn("last_known_good", result)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(json.loads(self.ledger.read_text())["attempts"], {})

    def execution_responses(self, *, pending=False, result_override=None):
        stale = _result_payload(NOW - timedelta(hours=48))
        fresh = _result_payload(NOW + timedelta(seconds=2))
        fresh["execution_id"] = "exec-2"
        calls = []

        def request(url, key, method="GET", body=None, timeout=30):
            calls.append((method, url, timeout))
            if method == "POST":
                self.assertEqual(url, f"{dune.API_BASE}/{QUERY_ID}/execute")
                self.assertTrue(json.loads(self.ledger.read_text())["attempts"])
                self.clock.sleep(2)
                return 200, {"execution_id": "exec-2"}
            if url.endswith("/status"):
                return 200, {"execution_id": "exec-2", "query_id": int(QUERY_ID),
                             "state": "QUERY_STATE_PENDING" if pending else "QUERY_STATE_COMPLETED"}
            if "/execution/" in url:
                return 200, result_override if result_override is not None else fresh
            return 200, stale
        return request, calls

    def test_refresh_binds_status_and_results_to_new_execution(self):
        request, calls = self.execution_responses()
        with mock.patch.object(dune, "_request", side_effect=request):
            result = dune.collect_dune(env=self.env, now=NOW)
        self.assertTrue(result["available"], result)
        self.assertEqual(result["execution_id"], "exec-2")
        self.assertEqual([url for _, url, _ in calls], [
            f"{dune.API_BASE}/{QUERY_ID}/results?limit=500&columns=metric_id%2Cday%2Cdimension%2Cvalue%2Cunit%2Csample_count",
            f"{dune.API_BASE}/{QUERY_ID}/execute",
            "https://api.dune.com/api/v1/execution/exec-2/status",
            "https://api.dune.com/api/v1/execution/exec-2/results?limit=500&columns=metric_id%2Cday%2Cdimension%2Cvalue%2Cunit%2Csample_count",
        ])

    def test_saved_query_edit_404_executes_only_with_both_precommitted_allowances(self):
        request, calls = self.execution_responses()
        original = request
        def no_current_result(url, key, method="GET", body=None, timeout=30):
            if url.startswith(f"{dune.API_BASE}/{QUERY_ID}/results?"):
                calls.append((method, url, timeout))
                return 404, {"error": "No execution for current SQL"}
            return original(url, key, method=method, body=body, timeout=timeout)
        with mock.patch.object(dune, "_request", side_effect=no_current_result):
            result = dune.collect_dune(env=self.env, now=NOW)
        self.assertTrue(result["available"], result)
        self.assertEqual(result["execution_id"], "exec-2")
        self.assertEqual(len(calls), 4)
        self.assertEqual(json.loads(self.read_ledger.read_text())["reservations"].popitem()[1]["reads"], 2)

    def test_disabled_or_missing_result_budget_makes_no_paid_read_and_retains_previous(self):
        prior = dune._success_section(QUERY_ID, f"https://dune.com/queries/{QUERY_ID}",
                                      dune.SOURCE_URL, _result_payload(NOW - timedelta(hours=2)),
                                      "fresh", NOW)
        snapshot = Path(self.tmp.name) / "previous.json"
        snapshot.write_text(json.dumps({"dune": prior}))
        self.env["DUNE_PREVIOUS_SNAPSHOT"] = str(snapshot)
        before = self.read_ledger.read_bytes()
        for change in ({"DUNE_PAID_READS_ENABLED": "false"},
                       {"DUNE_RESULT_READ_LEDGER": str(Path(self.tmp.name) / "missing.json")}):
            with self.subTest(change=change), mock.patch.dict(self.env, change), \
                    mock.patch.object(dune, "_request") as request:
                result = dune.collect_dune(env=self.env, now=NOW)
            request.assert_not_called()
            self.assertFalse(result["available"])
            self.assertEqual(result["last_known_good"]["aggregates"], prior["aggregates"])
            self.assertEqual(self.read_ledger.read_bytes(), before)

    def test_committed_result_read_receipt_is_consumed_once_before_transport(self):
        receipt = dune.reserve_result_reads(self.read_ledger, QUERY_ID, "123:1", 1, NOW)
        receipt_path = Path(self.tmp.name) / "read-receipt.json"
        receipt_path.write_text(json.dumps(receipt))
        ci = {**self.env, "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123",
              "GITHUB_RUN_ATTEMPT": "1", "DUNE_RESULT_READ_RECEIPT": str(receipt_path),
              "DUNE_EXECUTION_ENABLED": "false"}
        for environment, reference, query_id in (
            ({**ci, "GITHUB_RUN_ATTEMPT": "2"}, NOW, QUERY_ID),
            ({**ci, "DUNE_EXECUTION_ENABLED": "true"}, NOW, QUERY_ID),
            (ci, NOW + timedelta(days=1), QUERY_ID),
            (ci, NOW, "9999"),
        ):
            with self.assertRaises(ValueError):
                dune._consume_result_read_reservation(environment, query_id, reference)
        self.assertFalse(receipt_path.with_name(receipt_path.name + ".consumed").exists())
        with mock.patch.object(dune, "_request", side_effect=OSError("ambiguous")) as request:
            first = dune.collect_dune(env=ci, now=NOW, sleep=_no_sleep)
            second = dune.collect_dune(env=ci, now=NOW, sleep=_no_sleep)
        self.assertFalse(first["available"])
        self.assertFalse(second["available"])
        self.assertEqual(request.call_count, 1)
        self.assertIn("123:1", json.loads(self.read_ledger.read_text())["reservations"])
        self.assertTrue(receipt_path.with_name(receipt_path.name + ".consumed").exists())

    def test_elapsed_timeout_preserves_original_lkg(self):
        request, calls = self.execution_responses(pending=True)
        with mock.patch.object(dune, "_request", side_effect=request):
            result = dune.collect_dune(env=self.env, now=NOW)
        self.assertFalse(result["available"])
        self.assertIn("timed out", result["reason"])
        self.assertEqual(self.clock.elapsed, dune.EXECUTE_DEADLINE_SECONDS)
        self.assertEqual(result["last_known_good"]["execution_id"], "exec-1")
        self.assertEqual(result["last_known_good"]["aggregates"]["dex_volume_total_latest_usd"], 987654321.0)
        self.assertEqual(pipeline._dune_semantic_failures(result, NOW), [])
        invalid = copy.deepcopy(result)
        invalid["last_known_good"]["aggregates"]["dex_volume_total_latest_usd"] = -1
        self.assertTrue(pipeline._dune_semantic_failures(invalid, NOW))
        self.assertEqual(result["aggregation_contract"], dune.AGGREGATION_CONTRACT)
        self.assertEqual(result["last_known_good"]["aggregation_contract"], dune.AGGREGATION_CONTRACT)
        self.assertEqual(result["last_known_good"]["age_seconds"], 48 * 3600)
        self.assertLessEqual(calls[-1][2], 5)

    def test_new_invalid_or_old_result_never_replaces_lkg(self):
        for updated in [{"execution_id": "exec-2", "state": "QUERY_STATE_COMPLETED"},
                        _result_payload(NOW - timedelta(hours=48))]:
            with self.subTest(updated=updated):
                self.ledger.write_text('{"version":1,"attempts":{}}')
                request, _ = self.execution_responses(result_override=updated)
                with mock.patch.object(dune, "_request", side_effect=request):
                    result = dune.collect_dune(env=self.env, now=NOW)
                self.assertFalse(result["available"])
                self.assertEqual(result["last_known_good"]["execution_id"], "exec-1")

    def test_stale_partial_day_result_cannot_drop_published_lkg(self):
        prior = dune._success_section(
            QUERY_ID, f"https://dune.com/queries/{QUERY_ID}", dune.SOURCE_URL,
            _result_payload(NOW - timedelta(hours=24)), "fresh", NOW,
        )
        snapshot = Path(self.tmp.name) / "previous.json"
        snapshot.write_text(json.dumps({"dune": prior}))
        ended = NOW - timedelta(hours=48)
        partial_rows = [{**row, "day": ended.date().isoformat()} for row in FRESH_ROWS]
        self.env.update({
            "DUNE_PREVIOUS_SNAPSHOT": str(snapshot),
            "DUNE_EXECUTION_ENABLED": "false",
        })
        result, _ = self.collect(_result_payload(ended, partial_rows))
        self.assertFalse(result["available"])
        self.assertEqual(result["last_known_good"]["execution_id"], prior["execution_id"])
        self.assertEqual(result["last_known_good"]["aggregates"], prior["aggregates"])

    def test_missing_family_and_partial_day_do_not_crash_or_invent_totals(self):
        rows = copy.deepcopy(FRESH_ROWS[:1])
        rows += [{**FRESH_ROWS[1], "day": "2026-09-02", "value": 123.0}]
        result, _ = self.collect(_result_payload(NOW, rows))
        self.assertTrue(result["available"])
        self.assertIsNone(result["aggregates"]["dex_volume_total_latest_usd"])
        self.assertEqual(result["aggregates"]["fee_payers_day"], "2026-09-01")
        only_partial = [{**row, "day": "2026-09-02"} for row in FRESH_ROWS]
        result, _ = self.collect(_result_payload(NOW, only_partial))
        self.assertFalse(result["available"])
        self.assertIn("completed UTC-day", result["reason"])

    def test_legacy_dune_utc_day_format_retains_only_complete_days(self):
        rows = copy.deepcopy(FRESH_ROWS)
        rows[0]["day"] = "2026-09-02 00:00:00.000 UTC"
        rows[1]["day"] = "2026-09-01 00:00:00.000 UTC"
        result, _ = self.collect(_result_payload(NOW, rows))
        self.assertTrue(result["available"], result)
        self.assertIsNone(result["aggregates"]["fee_payers_latest"])
        self.assertEqual(result["aggregates"]["dex_volume_total_day"], "2026-09-01")

    def test_xstock_volume_requires_complete_pricing_and_single_counted_scope(self):
        def rows(priced, include_volume):
            output = copy.deepcopy(FRESH_ROWS)
            output.extend([
                {"metric_id": "daily_xstocks_dex_trade_legs", "day": "2026-09-01",
                 "dimension": dune.XSTOCK_DIMENSION, "value": 5, "unit": "trade_legs", "sample_count": 5},
                {"metric_id": "daily_xstocks_dex_priced_trade_legs", "day": "2026-09-01",
                 "dimension": dune.XSTOCK_DIMENSION, "value": priced, "unit": "trade_legs", "sample_count": 5},
                {"metric_id": "daily_transaction_fees_sol", "day": "2026-09-01",
                 "dimension": None, "value": 500.25, "unit": "sol", "sample_count": 1000},
            ])
            if include_volume:
                output.append({"metric_id": "daily_xstocks_dex_volume", "day": "2026-09-01",
                               "dimension": dune.XSTOCK_DIMENSION, "value": 2500.5,
                               "unit": "usd", "sample_count": priced})
            return output
        complete, _ = self.collect(_result_payload(NOW, rows(5, True)))
        self.assertTrue(complete["available"], complete)
        aggregates = complete["aggregates"]
        self.assertEqual(aggregates["xstocks_dex_volume_latest_usd"], 2500.5)
        self.assertEqual(aggregates["xstocks_dex_trade_legs"], 5.0)
        self.assertTrue(aggregates["xstocks_dex_volume_available"])
        self.assertEqual(aggregates["transaction_fees_latest_sol"], 500.25)
        self.assertIn("not protocol REV", aggregates["transaction_fees_basis"])
        self.assertEqual(pipeline._dune_semantic_failures(complete, NOW), [])

        partial, _ = self.collect(_result_payload(NOW, rows(4, False)))
        self.assertTrue(partial["available"], partial)
        self.assertIsNone(partial["aggregates"]["xstocks_dex_volume_latest_usd"])
        self.assertEqual(partial["aggregates"]["xstocks_dex_priced_trade_legs"], 4.0)
        self.assertIn("withheld", partial["aggregates"]["xstocks_dex_volume_reason"])
        self.assertEqual(pipeline._dune_semantic_failures(partial, NOW), [])

    def test_xstock_rows_reject_incomplete_or_contradictory_coverage(self):
        base = copy.deepcopy(FRESH_ROWS)
        all_row = {"metric_id": "daily_xstocks_dex_trade_legs", "day": "2026-09-01",
                   "dimension": dune.XSTOCK_DIMENSION, "value": 5, "unit": "trade_legs", "sample_count": 5}
        priced = {**all_row, "metric_id": "daily_xstocks_dex_priced_trade_legs", "value": 4}
        volume = {**all_row, "metric_id": "daily_xstocks_dex_volume", "value": 100.0, "unit": "usd", "sample_count": 4}
        mutations = [
            base + [all_row],
            base + [all_row, priced, volume],
            base + [all_row, {**priced, "value": 6}],
            base + [{**all_row, "dimension": "all_xstocks"}, priced],
            base + [{**all_row, "value": 4}, priced],
        ]
        for rows in mutations:
            with self.subTest(rows=rows[-2:]):
                result, _ = self.collect(_result_payload(NOW, rows))
                self.assertFalse(result["available"])

    def test_new_metric_families_stand_alone_and_drive_latest_day(self):
        fees = [{"metric_id": "daily_transaction_fees_sol", "day": "2026-09-01",
                 "dimension": None, "value": 400.5, "unit": "sol", "sample_count": 100}]
        result, _ = self.collect(_result_payload(NOW, fees))
        self.assertTrue(result["available"], result)
        self.assertEqual(result["aggregates"]["latest_day"], "2026-09-01")
        self.assertEqual(result["aggregates"]["transaction_fees_latest_sol"], 400.5)
        self.assertEqual(pipeline._dune_semantic_failures(result, NOW), [])

        counts = [
            {"metric_id": "daily_xstocks_dex_trade_legs", "day": "2026-09-01",
             "dimension": dune.XSTOCK_DIMENSION, "value": 2, "unit": "trade_legs", "sample_count": 2},
            {"metric_id": "daily_xstocks_dex_priced_trade_legs", "day": "2026-09-01",
             "dimension": dune.XSTOCK_DIMENSION, "value": 1, "unit": "trade_legs", "sample_count": 2},
        ]
        result, _ = self.collect(_result_payload(NOW, counts))
        self.assertTrue(result["available"], result)
        self.assertEqual(result["aggregates"]["latest_day"], "2026-09-01")
        self.assertEqual(result["aggregates"]["xstocks_dex_trade_legs"], 2.0)
        self.assertEqual(pipeline._dune_semantic_failures(result, NOW), [])

    def test_historical_contract_preserves_partial_and_preaggregate_evidence(self):
        section, _ = self.collect(_result_payload(NOW))
        section.pop("aggregation_contract")
        section["aggregates"].update(
            latest_day="2026-09-02 00:00:00.000 UTC",
            fee_payers_day="2026-09-02 00:00:00.000 UTC",
        )
        self.assertEqual(pipeline._dune_semantic_failures(section, NOW), [])
        historical = copy.deepcopy(section)
        historical.pop("aggregates")
        self.assertEqual(pipeline._dune_semantic_failures(historical, NOW), [])
        for change in (
            lambda s: s.update(aggregation_contract=dune.AGGREGATION_CONTRACT),
            lambda s: s.update(aggregation_contract="unknown"),
            lambda s: s["aggregates"].update(fee_payers_latest=-1),
            lambda s: s["aggregates"].update(fee_payers_day="2026-09-03"),
        ):
            invalid = copy.deepcopy(section)
            change(invalid)
            self.assertTrue(pipeline._dune_semantic_failures(invalid, NOW))
        legacy_lkg = {
            "available": False, "requires_api_key": True, "reason": "old failure",
            "query_id": QUERY_ID, "query_url": f"https://dune.com/queries/{QUERY_ID}",
            "source_url": dune.SOURCE_URL, "columns": list(dune.EXPECTED_COLUMNS),
            "last_known_good": {
                "query_id": QUERY_ID, "query_url": f"https://dune.com/queries/{QUERY_ID}",
                "source_url": dune.SOURCE_URL, "execution_id": "legacy-exec",
                "execution_started_at": "2026-09-01T00:00:00Z",
                "execution_ended_at": "2026-09-01T00:01:00Z", "age_seconds": 172740,
                "row_count": 1, "datapoint_count": 6, "result_sha256": "a" * 64,
                "execution_started_at_parsed": "2026-09-01T00:00:00+00:00",
                "execution_ended_at_parsed": "2026-09-01T00:01:00+00:00",
            },
        }
        self.assertEqual(pipeline._dune_semantic_failures(legacy_lkg, NOW), [])

    def test_every_row_is_validated_and_bad_values_never_become_zero(self):
        edits = [{"value": -1}, {"value": float("nan")}, {"value": True},
                 {"value": 10 ** 1000}, {"unit": "sol"}, {"day": "2026-09-03"},
                 {"day": "not-a-date"}, {"sample_count": True}, {"dimension": "unexpected"},
                 {"metric_id": "unknown"}]
        for edit in edits:
            with self.subTest(edit=edit):
                rows = copy.deepcopy(FRESH_ROWS)
                rows[1].update(edit)
                result, _ = self.collect(_result_payload(NOW, rows))
                self.assertFalse(result["available"])
                self.assertNotIn("aggregates", result)
        result, _ = self.collect(_result_payload(NOW, FRESH_ROWS + FRESH_ROWS[:1]))
        self.assertFalse(result["available"])
        self.assertIn("repeats", result["reason"])

    def test_future_execution_pagination_and_partial_state_are_rejected(self):
        for payload in [_result_payload(NOW + timedelta(days=1)),
                        {**_result_payload(NOW), "next_uri": "https://api.dune.com/next"},
                        {**_result_payload(NOW), "state": "QUERY_STATE_COMPLETED_PARTIAL"}]:
            result, _ = self.collect(payload)
            self.assertFalse(result["available"])

    def test_retry_classifies_http_errors_and_never_replays_paid_post(self):
        for code, expected in [(404, 1), (403, 1), (429, 3), (503, 3)]:
            with self.subTest(code=code):
                def failure(*args, **kwargs):
                    raise urllib.error.HTTPError("https://api.dune.com/", code, "error", {}, io.BytesIO())
                with mock.patch.object(dune, "_request", side_effect=failure) as request:
                    _, payload, _ = dune._request_with_retry("https://api.dune.com/", "mock")
                self.assertIsNone(payload)
                self.assertEqual(request.call_count, expected)
        for error in [OSError("ambiguous"), urllib.error.HTTPError("https://api.dune.com/", 503, "error", {}, io.BytesIO())]:
            with mock.patch.object(dune, "_request", side_effect=error) as request:
                _, payload, _ = dune._request_with_retry("https://api.dune.com/", "mock", method="POST")
            self.assertIsNone(payload)
            self.assertEqual(request.call_count, 1)

    def test_request_timeout_uses_remaining_elapsed_budget(self):
        def slow(url, key, **kwargs):
            self.assertEqual(kwargs["timeout"], 4)
            self.clock.sleep(4)
            raise OSError("timeout")
        with mock.patch.object(dune, "_request", side_effect=slow) as request:
            _, payload, error = dune._request_with_retry("https://api.dune.com/", "mock", deadline=4)
        self.assertIsNone(payload)
        self.assertEqual(request.call_count, 1)
        self.assertIn("deadline", error)

    def test_unexpected_optional_error_does_not_escape_or_retain_secret(self):
        with mock.patch.object(dune, "_derive_aggregates", side_effect=TypeError("mock-key")):
            result, _ = self.collect(_result_payload(NOW))
        self.assertFalse(result["available"])
        self.assertNotIn("mock-key", result["reason"])

    def test_missing_authorization_and_corrupt_ledger_prevent_post(self):
        for change in [{"DUNE_EXECUTION_ENABLED": "false"}, {"DUNE_EXECUTION_LEDGER": ""}]:
            with mock.patch.dict(self.env, change):
                result, request = self.collect(_result_payload(NOW - timedelta(hours=48)))
            self.assertFalse(result["available"])
            self.assertEqual(request.call_count, 1)
        self.ledger.write_text('{broken')
        result, request = self.collect(_result_payload(NOW - timedelta(hours=48)))
        self.assertFalse(result["available"])
        self.assertEqual(request.call_count, 1)

    def test_daily_reservation_survives_failed_call_and_next_day_can_reserve(self):
        receipt = dune.reserve_execution_attempt(self.ledger, QUERY_ID, "run-1", NOW)
        self.assertEqual(receipt["utc_date"], "2026-09-02")
        with self.assertRaises(ValueError):
            dune.reserve_execution_attempt(self.ledger, QUERY_ID, "run-2", NOW)
        dune.reserve_execution_attempt(self.ledger, QUERY_ID, "run-3", NOW + timedelta(days=1))
        self.assertEqual(len(json.loads(self.ledger.read_text())["attempts"][QUERY_ID]), 2)

    def test_ci_receipt_is_bound_to_run_day_query_and_consumed_once(self):
        receipt = dune.reserve_execution_attempt(self.ledger, QUERY_ID, "123:1", NOW)
        path = Path(self.tmp.name) / "receipt.json"
        path.write_text(json.dumps(receipt))
        self.env.update({"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                         "DUNE_EXECUTION_RECEIPT": str(path)})
        with mock.patch.dict(self.env, {"GITHUB_RUN_ATTEMPT": "2"}):
            self.assertIsNotNone(dune._reserve_execution_attempt(self.env, QUERY_ID, NOW))
        self.assertIsNone(dune._reserve_execution_attempt(self.env, QUERY_ID, NOW))
        self.assertIsNotNone(dune._reserve_execution_attempt(self.env, QUERY_ID, NOW))
        self.assertTrue(path.with_name(path.name + ".consumed").exists())

    def test_pipeline_validates_dune_including_missing_family_and_lkg(self):
        result, _ = self.collect(_result_payload(NOW, FRESH_ROWS[:1]))
        self.assertEqual(pipeline._dune_semantic_failures(result, NOW), [])
        result["available"] = "bad"
        self.assertTrue(pipeline._dune_semantic_failures(result, NOW))
        result["available"] = True
        result["aggregates"]["fee_payers_latest"] = -1
        self.assertTrue(pipeline._dune_semantic_failures(result, NOW))


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
                mock.patch.object(collect.feature_accounts, "collect_feature_accounts", return_value={"available": False}), \
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
        # aggregates is absent from this fixture (no rows stored), so the
        # projected key set is the contract minus that optional block.
        self.assertLessEqual(set(projected["dune"].keys()), allowed)
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
