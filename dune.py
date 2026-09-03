"""Dune query adapter — optional, never blocks the snapshot path.

Latest-result-first: the collector reads the newest cached result of an
Ashton-owned Dune query and only triggers a paid re-execution when that result
is older than ``DUNE_REFRESH_HOURS`` (default 24). Every failure mode —
missing configuration, transport errors, rate limits, schema drift, failed
executions — degrades to ``available: false``; nothing here raises into the
snapshot path. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

API_BASE = "https://api.dune.com/api/v1/query"
PUBLIC_QUERY_URL_BASE = "https://dune.com/queries/"
SOURCE_URL = "https://dune.com/"

DEFAULT_REFRESH_HOURS = 24.0
EXECUTE_DEADLINE_SECONDS = 120.0
EXECUTE_POLL_INTERVAL_SECONDS = 5.0
MAX_ATTEMPTS = 3  # 1 initial + 2 bounded retries on 429/5xx
RETRY_BACKOFF_SECONDS = 2.0  # exponential: 2s, 4s

EXPECTED_COLUMNS = ("metric_id", "day", "dimension", "value", "unit", "sample_count")


def is_configured(env: dict[str, str] | None = None) -> bool:
    """True only when both the API key and a query id are present."""
    environment = os.environ if env is None else env
    return bool(environment.get("DUNE_API_KEY")) and bool(environment.get("DUNE_QUERY_ID"))


def _unconfigured() -> dict[str, Any]:
    return {"available": False, "reason": "dune query not configured"}


def _request(
    url: str,
    api_key: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """One Dune API call. Returns (status, parsed-json). Raises on transport error."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-DUNE-API-KEY": api_key,
            "Accept": "application/json",
        },
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    status = getattr(response, "status", 200)
    return status, payload if isinstance(payload, dict) else {}


def _is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _request_with_retry(
    url: str,
    api_key: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    sleep: Any = None,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Bounded retry on 429/5xx. Returns (status, payload, error)."""
    pause = time.sleep if sleep is None else sleep
    backoff = RETRY_BACKOFF_SECONDS
    last_error: str | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            status, payload = _request(url, api_key, method=method, body=body)
            if status < 400:
                return status, payload, None
            last_error = f"HTTP {status}"
            if not _is_retryable(status):
                return status, None, last_error
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = f"transport error: {error}"
            status = 0
        if attempt < MAX_ATTEMPTS - 1:
            pause(backoff)
            backoff *= 2
    return status if status else 0, None, last_error


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None



def _payload_field(payload: dict[str, Any], key: str) -> Any:
    """Dune keeps execution_* at the response top level; older docs put
    them on result. Prefer top level, fall back to result."""
    if key in payload and payload[key] is not None:
        return payload[key]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    return result.get(key)


def _result_count(payload: dict[str, Any], key: str) -> int | None:
    """Row/datapoint counts live in result.metadata; top level is the
    fallback some response versions use."""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    for source in (metadata, result, payload):
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _rows_sha256(rows: Any) -> str | None:
    if not isinstance(rows, list):
        return None
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def _validate_columns(result: dict[str, Any]) -> tuple[bool, str | None]:
    """Reject schema drift: every expected column must exist in the result metadata."""
    metadata = result.get("result", {})
    if not isinstance(metadata, dict):
        return False, "result payload missing result metadata"
    rows = metadata.get("rows")
    if not isinstance(rows, list):
        return False, "result payload has no rows list"
    if not rows:
        return False, "result has zero rows"
    first = rows[0]
    if not isinstance(first, dict):
        return False, "result rows are not objects"
    # Metadata column names when present; otherwise the first row's keys.
    column_names: list[str] = []
    raw_metadata = metadata.get("metadata") or result.get("metadata")
    if isinstance(raw_metadata, list):
        for entry in raw_metadata:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                column_names.append(entry["name"])
    if not column_names:
        column_names = list(first.keys())
    missing = [name for name in EXPECTED_COLUMNS if name not in column_names]
    if missing:
        return False, "schema drift: missing columns " + ", ".join(missing)
    return True, None


def _build_unavailable(
    reason: str,
    query_id: str | None = None,
    last_known_good: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "available": False,
        "requires_api_key": True,
        "reason": reason,
        "source_url": SOURCE_URL,
        "columns": list(EXPECTED_COLUMNS),
    }
    if query_id:
        state["query_id"] = query_id
        state["query_url"] = f"{PUBLIC_QUERY_URL_BASE}{query_id}"
    if last_known_good is not None:
        state["last_known_good"] = last_known_good
    return state


def collect_dune(
    env: dict[str, str] | None = None,
    now: datetime | None = None,
    sleep: Any = None,
) -> dict[str, Any]:
    """Collect the latest Dune query result, executing only when stale.

    Never raises. All failures degrade to ``available: false``.
    """
    environment = os.environ if env is None else env
    api_key = environment.get("DUNE_API_KEY")
    query_id = environment.get("DUNE_QUERY_ID")
    if not api_key or not query_id:
        return _unconfigured()
    # Resolve at call time so tests (and embedders) can patch the clock.
    pause = time.sleep if sleep is None else sleep

    reference = now if now is not None else datetime.now(timezone.utc)
    try:
        refresh_hours = float(environment.get("DUNE_REFRESH_HOURS", DEFAULT_REFRESH_HOURS))
        if refresh_hours <= 0:
            refresh_hours = DEFAULT_REFRESH_HOURS
    except (TypeError, ValueError):
        refresh_hours = DEFAULT_REFRESH_HOURS

    query_url = f"{PUBLIC_QUERY_URL_BASE}{query_id}"
    results_url = f"{API_BASE}/{query_id}/results"

    try:
        status, payload, error = _request_with_retry(results_url, api_key, sleep=sleep)
    except Exception as error:  # pragma: no cover - defensive, never propagate
        return _build_unavailable(f"dune request failed: {error}", query_id)
    if payload is None:
        return _build_unavailable(
            f"dune latest-result request failed after retries: {error}", query_id,
        )

    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    # Dune returns execution_id at the response top level; the result body
    # carries the timing and row data.
    execution_id = payload.get("execution_id") if isinstance(payload.get("execution_id"), str) else result.get("execution_id")
    ended_at = _parse_utc(_payload_field(payload, "execution_ended_at"))

    columns_ok, columns_error = _validate_columns(payload)
    if not columns_ok:
        return _build_unavailable(columns_error or "schema drift", query_id)

    rows = result.get("rows")
    freshness: str
    execution: dict[str, Any] | None = None
    if ended_at is None:
        freshness = "stale"
    elif (reference - ended_at) <= timedelta(hours=refresh_hours):
        freshness = "fresh"
    else:
        freshness = "stale"
        # Paid re-execution only when the cached result is older than the
        # refresh window. Credit policy: at most one execution per day.
        execution = _execute_and_poll(results_url, api_key, reference, sleep=pause)

    if execution is not None and not execution.get("available", True):
        # Execution failed or timed out: keep last-known-good with explicit age.
        age_seconds = (
            round((reference - ended_at).total_seconds()) if ended_at is not None else None
        )
        degraded = _build_unavailable(
            execution.get("reason") or "dune execution failed",
            query_id,
        )
        degraded["last_known_good"] = _last_known_good(
            query_id, query_url, results_url, payload, age_seconds,
        )
        return degraded

    if execution is not None:
        payload = execution["payload"]
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        ended_at = _parse_utc(_payload_field(payload, "execution_ended_at"))
        columns_ok, columns_error = _validate_columns(payload)
        if not columns_ok:
            degraded = _build_unavailable(columns_error or "schema drift", query_id)
            degraded["last_known_good"] = _last_known_good(
                query_id, query_url, results_url, payload, None,
            )
            return degraded
        if ended_at is None:
            freshness = "stale"
        else:
            freshness = (
                "fresh"
                if (reference - ended_at) <= timedelta(hours=refresh_hours)
                else "stale"
            )

    return _success_section(query_id, query_url, SOURCE_URL, payload, freshness, reference)


def _response_execution_id(payload: dict[str, Any]) -> str | None:
    """Dune puts execution_id at the response top level; result may mirror it."""
    value = payload.get("execution_id")
    if isinstance(value, str) and value:
        return value
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    value = result.get("execution_id")
    return value if isinstance(value, str) and value else None


def _last_known_good(
    query_id: str,
    query_url: str,
    source_url: str,
    payload: dict[str, Any],
    age_seconds: int | None,
) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    started_at = _parse_utc(_payload_field(payload, "execution_started_at"))
    ended_at = _parse_utc(_payload_field(payload, "execution_ended_at"))
    return {
        "query_id": query_id,
        "query_url": query_url,
        "source_url": source_url,
        "execution_id": _response_execution_id(payload),
        "execution_started_at": _payload_field(payload, "execution_started_at"),
        "execution_ended_at": _payload_field(payload, "execution_ended_at"),
        "age_seconds": age_seconds,
        "row_count": _result_count(payload, "row_count"),
        "datapoint_count": (
            _result_count(payload, "datapoint_count")
        ),
        "result_sha256": _rows_sha256(result.get("rows")),
        "execution_started_at_parsed": started_at.isoformat(timespec="seconds") if started_at else None,
        "execution_ended_at_parsed": ended_at.isoformat(timespec="seconds") if ended_at else None,
    }


def _derive_aggregates(rows: Any) -> dict[str, Any] | None:
    """Compact public aggregates over the query's rows, derived at
    collection time. Raw rows never enter the snapshot (the committed
    snapshot must equal its public projection), so the renderer-visible
    data is these aggregates plus full provenance.

    Metric families from the report's saved query:
      daily_non_vote_fee_payers   -> fee_payers_latest
      daily_dex_volume_total      -> dex_volume_total_latest
      daily_dex_volume_by_project -> dex_volume_by_project_top (max 5)
    """
    if not isinstance(rows, list) or not rows:
        return None
    fee_payers: dict[str, float] = {}
    dex_total: dict[str, float] = {}
    dex_by_project: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric_id = row.get("metric_id")
        day = row.get("day")
        value = row.get("value")
        if not isinstance(day, str) or day == "" or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if metric_id == "daily_non_vote_fee_payers":
            fee_payers[day] = max(fee_payers.get(day, 0.0), value)
        elif metric_id == "daily_dex_volume_total":
            dex_total[day] = max(dex_total.get(day, 0.0), value)
        elif metric_id == "daily_dex_volume_by_project":
            dimension = row.get("dimension")
            if not isinstance(dimension, str) or not dimension:
                continue
            per_day = dex_by_project.setdefault(day, {})
            per_day[dimension] = per_day.get(dimension, 0.0) + value
    if not fee_payers and not dex_total:
        return None

    def latest(daily: dict[str, float]) -> tuple[str, float] | None:
        if not daily:
            return None
        day = max(daily)
        return day, daily[day]

    fee_latest = latest(fee_payers)
    dex_latest = latest(dex_total)
    by_project_top: list[dict[str, Any]] = []
    project_day: str | None = None
    if dex_by_project:
        project_day = max(dex_by_project)
        top = sorted(dex_by_project[project_day].items(), key=lambda kv: kv[1], reverse=True)[:5]
        by_project_top = [{"dimension": name, "value": value} for name, value in top]
    candidates = [day for day, _ in (fee_latest, dex_latest) if day]
    latest_day = max(candidates) if candidates else None
    return {
        "latest_day": latest_day,
        "fee_payers_latest": fee_latest[1] if fee_latest else None,
        "fee_payers_day": fee_latest[0] if fee_latest else None,
        "dex_volume_total_latest_usd": dex_latest[1] if dex_latest else None,
        "dex_volume_total_day": dex_latest[0] if dex_latest else None,
        "dex_volume_by_project_top": by_project_top,
        "dex_volume_by_project_day": project_day,
        "basis": "provider-reported (Dune); trade-leg volume, not unique-user volume",
        "scope": "fee payers exclude vote transactions; DEX volume sums swap legs",
    }


def _success_section(
    query_id: str,
    query_url: str,
    source_url: str,
    payload: dict[str, Any],
    freshness: str,
    reference: datetime,
) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    started_at = _parse_utc(_payload_field(payload, "execution_started_at"))
    ended_at = _parse_utc(_payload_field(payload, "execution_ended_at"))
    age_seconds = (
        round((reference - ended_at).total_seconds()) if ended_at is not None else None
    )
    return {
        "available": freshness == "fresh",
        "requires_api_key": True,
        "query_id": query_id,
        "query_url": query_url,
        "execution_id": _response_execution_id(payload),
        "execution_started_at": _payload_field(payload, "execution_started_at"),
        "execution_ended_at": _payload_field(payload, "execution_ended_at"),
        "result_age_seconds": age_seconds,
        "row_count": _result_count(payload, "row_count"),
        "datapoint_count": (
            _result_count(payload, "datapoint_count")
        ),
        "result_sha256": _rows_sha256(result.get("rows")),
        "source_url": source_url,
        "freshness": freshness,
        "columns": list(EXPECTED_COLUMNS),
        "state": "fresh" if freshness == "fresh" else "stale",
        "reason": None if freshness == "fresh" else "latest result exceeded the refresh window",
    }
    aggregates = _derive_aggregates(result.get("rows"))
    if aggregates is not None:
        section["aggregates"] = aggregates
    return section


def _execute_and_poll(
    results_url: str,
    api_key: str,
    reference: datetime,
    sleep: Any = time.sleep,
) -> dict[str, Any] | None:
    """POST /execute then poll the same URL within a fixed 120s deadline.

    Returns the refreshed payload dict on success, or an
    ``{"available": False, "reason": ...}`` dict on failure/timeout. Never raises.
    """
    status, payload, error = _request_with_retry(
        f"{results_url}/execute", api_key, method="POST", body={}, sleep=sleep,
    )
    if payload is None:
        return {"available": False, "reason": f"dune execute failed: {error}"}

    # Polling is bounded by iteration count (interval x deadline), not wall
    # clock, so behavior is deterministic under an injected clock and the
    # real-world ceiling is equivalent (120s / 5s = 24 polls).
    max_polls = int(EXECUTE_DEADLINE_SECONDS / EXECUTE_POLL_INTERVAL_SECONDS)
    for _ in range(max_polls):
        sleep(EXECUTE_POLL_INTERVAL_SECONDS)
        status, payload, error = _request_with_retry(results_url, api_key, sleep=sleep)
        if payload is not None:
            state = payload.get("state")
            if state == "QUERY_STATE_COMPLETED":
                return {"available": True, "payload": payload}
            if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
                return {
                    "available": False,
                    "reason": f"dune execution ended with state {state!r}",
                }
    return {"available": False, "reason": "dune execution poll timed out after 120s"}
