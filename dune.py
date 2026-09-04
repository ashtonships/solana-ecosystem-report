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
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import transport

API_BASE = "https://api.dune.com/api/v1/query"
PUBLIC_QUERY_URL_BASE = "https://dune.com/queries/"
SOURCE_URL = "https://dune.com/"

DEFAULT_REFRESH_HOURS = 24.0
EXECUTE_DEADLINE_SECONDS = 120.0
EXECUTE_POLL_INTERVAL_SECONDS = 5.0
MAX_ATTEMPTS = 3  # 1 initial + 2 bounded retries on 429/5xx
RETRY_BACKOFF_SECONDS = 2.0  # exponential: 2s, 4s

EXPECTED_COLUMNS = ("metric_id", "day", "dimension", "value", "unit", "sample_count")
AGGREGATION_CONTRACT = "completed-utc-days-v1"
METRIC_UNITS = {
    "daily_non_vote_fee_payers": "fee_payers",
    "daily_dex_volume_total": "usd",
    "daily_dex_volume_by_project": "usd",
    "daily_xstocks_dex_volume": "usd",
    "daily_xstocks_dex_trade_legs": "trade_legs",
    "daily_xstocks_dex_priced_trade_legs": "trade_legs",
    "daily_transaction_fees_sol": "sol",
}
XSTOCK_DIMENSION = "pinned_107_xstocks"
MAX_COUNT = 2 ** 63 - 1
XSTOCK_REGISTRY = {
    "source_key": "solana-foundation/tokens:xstock-variant-groups",
    "source_revision": "661a6f0ca466ccf74ea967dae7e3abbcdc088bc0",
    "source_path": "packages/asset-registry/src/data/xstock-variant-groups.ts",
    "license": "MIT",
    "selection": "address label exactly 'xStock'",
    "expected_unique_mint_count": 107,
}
MAX_RESULT_ROWS = 500


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
    timeout: float = 30,
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(transport.read_bounded(response).decode("utf-8"))
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
    deadline: float | None = None,
    max_attempts: int | None = None,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Retry transient reads within the deadline; never replay a paid POST."""
    pause = time.sleep if sleep is None else sleep
    backoff = RETRY_BACKOFF_SECONDS
    last_error: str | None = None
    attempts = max_attempts if max_attempts is not None else (MAX_ATTEMPTS if method == "GET" else 1)
    status = 0
    for attempt in range(attempts):
        remaining = deadline - time.monotonic() if deadline is not None else 30
        if remaining <= 0:
            return 0, None, "request deadline exhausted"
        try:
            status, payload = _request(
                url, api_key, method=method, body=body, timeout=min(30, remaining),
            )
            if deadline is not None and time.monotonic() >= deadline:
                return 0, None, "request deadline exhausted"
            if 200 <= status < 300:
                return status, payload, None
            last_error = f"HTTP {status}"
            if not _is_retryable(status):
                return status, None, last_error
        except urllib.error.HTTPError as error:
            status = error.code
            last_error = f"HTTP {status}"
            error.close()
            if not _is_retryable(status):
                return status, None, last_error
        except (urllib.error.URLError, OSError):
            last_error = "transport request failed"
            status = 0
        except ValueError:
            return status, None, "invalid or oversized JSON response"
        if attempt < attempts - 1:
            remaining = deadline - time.monotonic() if deadline is not None else float("inf")
            if remaining <= backoff:
                return status, None, "request deadline exhausted"
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
    rows = result.get("rows")
    if isinstance(rows, list):
        return len(rows) if key == "row_count" else sum(len(row) for row in rows if isinstance(row, dict))
    return None


def _rows_sha256(rows: Any) -> str | None:
    if not isinstance(rows, list):
        return None
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def _day(value: Any) -> date | None:
    """Accept the query's DATE or older midnight-UTC DATE_TRUNC representation."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").removesuffix(" UTC") +
                                        ("+00:00" if value.endswith(" UTC") else ""))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date() if parsed.time().isoformat() == "00:00:00" else None


def _validate_result(
    payload: dict[str, Any], reference: datetime, query_id: str,
) -> str | None:
    """Validate every source row before deriving or retaining result metadata."""
    if payload.get("state") != "QUERY_STATE_COMPLETED":
        return "result is not a complete successful execution"
    if payload.get("query_id") is not None and str(payload["query_id"]) != query_id:
        return "result query identity mismatch"
    if _response_execution_id(payload) is None:
        return "result execution identity is missing"
    started = _parse_utc(_payload_field(payload, "execution_started_at"))
    ended = _parse_utc(_payload_field(payload, "execution_ended_at"))
    if started is None or ended is None or not started <= ended <= reference:
        return "execution timestamps are missing, reversed or in the future"
    result = payload.get("result")
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        return "result has no nonempty rows list"
    metadata = result.get("metadata")
    for source in (payload, result, metadata):
        if not isinstance(source, dict):
            continue
        if source.get("next_uri") is not None or source.get("next_offset") is not None:
            return "paginated result is incomplete; narrow the registered query"
        for key in ("row_count", "total_row_count"):
            if key in source and (type(source[key]) is not int or source[key] != len(rows)):
                return "result row count does not match complete rows"
        if "datapoint_count" in source and (
            type(source["datapoint_count"]) is not int or source["datapoint_count"] < 0
        ):
            return "invalid result datapoint count"
    seen = set()
    normalized: dict[tuple[str, date], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != set(EXPECTED_COLUMNS):
            return f"schema drift: row {index} must contain exactly the expected columns"
        metric = row["metric_id"]
        if not isinstance(metric, str) or metric not in METRIC_UNITS:
            return f"row {index} has an unknown metric family"
        if row["unit"] != METRIC_UNITS[metric]:
            return f"row {index} has the wrong unit"
        day = _day(row["day"])
        if day is None or day > ended.astimezone(timezone.utc).date():
            return f"row {index} has an invalid or future UTC day"
        value = row["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not 0 <= value <= sys.float_info.max):
            return f"row {index} must have a finite nonnegative value"
        if (metric == "daily_non_vote_fee_payers" or metric.endswith("_trade_legs")) and (
            value != int(value) or value > MAX_COUNT
        ):
            return f"row {index} fee payers must be an integer count"
        if (type(row["sample_count"]) is not int or row["sample_count"] < 0
                or row["sample_count"] > MAX_COUNT):
            return f"row {index} has an invalid sample count"
        dimension = row["dimension"]
        if metric == "daily_dex_volume_by_project":
            if not isinstance(dimension, str) or not dimension.strip() or len(dimension) > 200:
                return f"row {index} requires a project dimension"
        elif metric.startswith("daily_xstocks_"):
            if dimension != XSTOCK_DIMENSION:
                return f"row {index} must declare the pinned xStock scope"
        elif dimension is not None:
            return f"row {index} total must not have a project dimension"
        identity = (metric, day, dimension)
        if identity in seen:
            return f"row {index} repeats a source metric/day/dimension"
        seen.add(identity)
        normalized[(metric, day)] = row
    xstock_days = {day for metric, day in normalized if metric.startswith("daily_xstocks_")}
    for day in xstock_days:
        all_legs = normalized.get(("daily_xstocks_dex_trade_legs", day))
        priced = normalized.get(("daily_xstocks_dex_priced_trade_legs", day))
        volume = normalized.get(("daily_xstocks_dex_volume", day))
        if all_legs is None or priced is None:
            return f"xStock coverage rows are incomplete for {day.isoformat()}"
        if (all_legs["value"] <= 0 or all_legs["value"] != all_legs["sample_count"]
                or priced["sample_count"] != all_legs["sample_count"]
                or priced["value"] > all_legs["value"]):
            return f"xStock coverage counts are contradictory for {day.isoformat()}"
        complete = priced["value"] == all_legs["value"]
        if volume is not None and (not complete or volume["sample_count"] != priced["value"]):
            return f"xStock USD volume lacks complete pricing coverage for {day.isoformat()}"
        if complete and volume is None:
            return f"xStock fully priced coverage is missing USD volume for {day.isoformat()}"
    return None


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
    contract = last_known_good.get("aggregation_contract") if last_known_good else AGGREGATION_CONTRACT
    if contract is not None:
        state["aggregation_contract"] = contract
    if query_id:
        state["query_id"] = query_id
        state["query_url"] = f"{PUBLIC_QUERY_URL_BASE}{query_id}"
    if last_known_good is not None:
        state["last_known_good"] = last_known_good
    return state


def _read_result_ledger(path: Path) -> dict[str, Any]:
    """Read a finite owner-set allowance for result rows, which consume credits."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or set(ledger) != {
        "version", "starts_on", "expires_on", "total_read_limit",
        "daily_read_limit", "max_rows_per_read", "query_id", "reservations",
    } or type(ledger["version"]) is not int or ledger["version"] != 1:
        raise ValueError("invalid Dune result-read ledger")
    start, end = date.fromisoformat(ledger["starts_on"]), date.fromisoformat(ledger["expires_on"])
    if start >= end:
        raise ValueError("invalid Dune result-read window")
    if not isinstance(ledger["query_id"], str) or re.fullmatch(r"[1-9][0-9]*", ledger["query_id"]) is None:
        raise ValueError("invalid Dune result-read query identity")
    for key in ("total_read_limit", "daily_read_limit", "max_rows_per_read"):
        if type(ledger[key]) is not int or ledger[key] < 1:
            raise ValueError("invalid Dune result-read limit")
    if ledger["daily_read_limit"] > ledger["total_read_limit"] or ledger["max_rows_per_read"] > MAX_RESULT_ROWS:
        raise ValueError("Dune result-read limit exceeds the code ceiling")
    if not isinstance(ledger["reservations"], dict):
        raise ValueError("invalid Dune result-read reservations")
    used = 0
    for token, receipt in ledger["reservations"].items():
        if (not isinstance(token, str) or not token or not isinstance(receipt, dict)
                or set(receipt) != {"run_token", "utc_date", "reserved_at", "reads", "max_rows_per_read", "query_id"}
                or receipt["run_token"] != token or type(receipt["reads"]) is not int
                or not 1 <= receipt["reads"] <= 2
                or receipt["query_id"] != ledger["query_id"]
                or receipt["max_rows_per_read"] != ledger["max_rows_per_read"]):
            raise ValueError("invalid Dune result-read receipt")
        stamp = _parse_utc(receipt["reserved_at"])
        if stamp is None or receipt["utc_date"] != stamp.astimezone(timezone.utc).date().isoformat():
            raise ValueError("invalid Dune result-read receipt time")
        receipt_day = date.fromisoformat(receipt["utc_date"])
        if not start <= receipt_day < end:
            raise ValueError("Dune result-read receipt outside policy window")
        used += receipt["reads"]
    # Per-day totals are checked independently of the reader's wall clock.
    by_day: dict[str, int] = {}
    for receipt in ledger["reservations"].values():
        by_day[receipt["utc_date"]] = by_day.get(receipt["utc_date"], 0) + receipt["reads"]
    if used > ledger["total_read_limit"] or any(value > ledger["daily_read_limit"] for value in by_day.values()):
        raise ValueError("Dune result-read ledger exceeds its allowance")
    return ledger


def reserve_result_reads(path: Path, query_id: str, run_token: str, reads: int,
                         now: datetime | None = None) -> dict[str, Any]:
    """Atomically reserve one cached read, plus one result read when executing."""
    reference = now or datetime.now(timezone.utc)
    if (reference.utcoffset() is None or not isinstance(run_token, str) or not run_token
            or not isinstance(query_id, str) or re.fullmatch(r"[1-9][0-9]*", query_id) is None
            or reads not in (1, 2)):
        raise ValueError("invalid Dune result-read reservation input")
    path = Path(path)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temporary = None
    try:
        os.close(fd)
        ledger = _read_result_ledger(path)
        if ledger["query_id"] != query_id:
            raise ValueError("Dune result-read query identity mismatch")
        day = reference.astimezone(timezone.utc).date().isoformat()
        if not ledger["starts_on"] <= day < ledger["expires_on"] or run_token in ledger["reservations"]:
            raise ValueError("Dune result-read policy is inactive or run already reserved")
        used = sum(row["reads"] for row in ledger["reservations"].values())
        today = sum(row["reads"] for row in ledger["reservations"].values() if row["utc_date"] == day)
        if used + reads > ledger["total_read_limit"] or today + reads > ledger["daily_read_limit"]:
            raise ValueError("Dune result-read allowance is exhausted")
        receipt = {"run_token": run_token, "query_id": query_id, "utc_date": day,
                   "reserved_at": reference.isoformat(timespec="seconds"), "reads": reads,
                   "max_rows_per_read": ledger["max_rows_per_read"]}
        ledger["reservations"][run_token] = receipt
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(ledger, output, sort_keys=True, indent=2, allow_nan=False)
            output.write("\n"); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _consume_result_read_reservation(environment: dict[str, str], query_id: str,
                                     reference: datetime) -> dict[str, int]:
    if environment.get("DUNE_PAID_READS_ENABLED") != "true":
        raise ValueError("Dune result reads require explicit finite-budget authorization")
    path = Path(environment["DUNE_RESULT_READ_LEDGER"])
    if environment.get("GITHUB_ACTIONS") == "true":
        receipt_path = Path(environment["DUNE_RESULT_READ_RECEIPT"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        ledger = _read_result_ledger(path)
        token = f"{environment['GITHUB_RUN_ID']}:{environment['GITHUB_RUN_ATTEMPT']}"
        expected_reads = 2 if environment.get("DUNE_EXECUTION_ENABLED") == "true" else 1
        if (receipt != ledger["reservations"].get(token) or receipt.get("query_id") != query_id
                or receipt.get("reads") != expected_reads
                or receipt.get("utc_date") != reference.astimezone(timezone.utc).date().isoformat()
                or _parse_utc(receipt.get("reserved_at")) is None
                or _parse_utc(receipt["reserved_at"]) > reference):
            raise ValueError("Dune result-read receipt does not match this run")
        marker = receipt_path.with_name(receipt_path.name + ".consumed")
        with marker.open("x") as handle:
            handle.write(token + "\n"); handle.flush(); os.fsync(handle.fileno())
        directory = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    else:
        reads = 2 if environment.get("DUNE_EXECUTION_ENABLED") == "true" else 1
        receipt = reserve_result_reads(
            path, query_id, f"local:{os.getpid()}:{uuid.uuid4()}", reads, reference,
        )
    return {"remaining": receipt["reads"], "max_rows": receipt["max_rows_per_read"]}


def _previous_last_known_good(environment: dict[str, str], query_id: str,
                              reference: datetime) -> dict[str, Any] | None:
    """Retain only already-published Dune evidence when a paid read is skipped."""
    try:
        path = Path(environment.get("DUNE_PREVIOUS_SNAPSHOT") or Path(__file__).parent / "snapshots/latest.json")
        section = json.loads(path.read_text(encoding="utf-8")).get("dune")
        if not isinstance(section, dict) or section.get("query_id") != query_id:
            return None
        source = section.get("last_known_good") if isinstance(section.get("last_known_good"), dict) else section
        if source.get("query_id") != query_id:
            return None
        ended = _parse_utc(source.get("execution_ended_at"))
        if ended is None or ended > reference:
            return None
        fields = {"aggregation_contract", "query_id", "query_url", "source_url", "execution_id",
                  "execution_started_at", "execution_ended_at", "row_count", "datapoint_count",
                  "result_sha256", "execution_started_at_parsed", "execution_ended_at_parsed", "aggregates"}
        retained = {key: source.get(key) for key in fields if key in source}
        retained["age_seconds"] = round((reference - ended).total_seconds())
        return retained
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _result_read(url: str, api_key: str, guard: dict[str, int], *, sleep: Any,
                 deadline: float) -> tuple[int, dict[str, Any] | None, str | None]:
    if guard["remaining"] <= 0:
        return 0, None, "Dune result-read reservation is exhausted"
    guard["remaining"] -= 1  # An ambiguous entered request remains spent.
    query = urllib.parse.urlencode({"limit": guard["max_rows"], "columns": ",".join(EXPECTED_COLUMNS)})
    return _request_with_retry(
        f"{url}?{query}", api_key, sleep=sleep, deadline=deadline, max_attempts=1,
    )


def _read_execution_ledger(path: Path) -> dict[str, Any]:
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(ledger, dict) or set(ledger) != {"version", "attempts"}
            or type(ledger["version"]) is not int or ledger["version"] != 1
            or not isinstance(ledger["attempts"], dict)):
        raise ValueError("invalid Dune execution ledger")
    for query, days in ledger["attempts"].items():
        if not isinstance(query, str) or not re.fullmatch(r"[1-9][0-9]*", query) or not isinstance(days, dict):
            raise ValueError("invalid Dune execution ledger identity")
        for day, receipt in days.items():
            if (not isinstance(receipt, dict)
                    or set(receipt) != {"version", "query_id", "utc_date", "run_token", "reserved_at"}
                    or type(receipt["version"]) is not int or receipt["version"] != 1
                    or receipt["query_id"] != query or receipt["utc_date"] != day
                    or _day(day) is None or _day(day).isoformat() != day
                    or not isinstance(receipt["run_token"], str) or not receipt["run_token"]
                    or _parse_utc(receipt["reserved_at"]) is None
                    or _parse_utc(receipt["reserved_at"]).astimezone(timezone.utc).date().isoformat() != day):
                raise ValueError("invalid Dune execution reservation")
    return ledger


def reserve_execution_attempt(
    path: Path, query_id: str, run_token: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Durably reserve before spend. CI must commit/push this before collection.

    Missing/corrupt ledgers and abandoned locks fail closed. Never auto-reset
    accounting after a crash; a spent attempt remains spent even on failure.
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    if (reference.utcoffset() is None or not re.fullmatch(r"[1-9][0-9]*", query_id)
            or not isinstance(run_token, str) or not run_token):
        raise ValueError("invalid Dune reservation input")
    path = Path(path)
    day = reference.astimezone(timezone.utc).date().isoformat()
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temp_path = None
    try:
        os.close(fd)
        ledger = _read_execution_ledger(path)
        days = ledger["attempts"].setdefault(query_id, {})
        if any(recorded_day >= day for recorded_day in days):
            raise ValueError("Dune execution attempt already reserved today or in the future")
        receipt = {
            "version": 1, "query_id": query_id, "utc_date": day,
            "run_token": run_token, "reserved_at": reference.isoformat(timespec="seconds"),
        }
        days[day] = receipt
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(ledger, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return receipt
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        lock.unlink()


def _reserve_execution_attempt(
    environment: dict[str, str], query_id: str, reference: datetime,
) -> str | None:
    if environment.get("DUNE_EXECUTION_ENABLED") != "true":
        return "Dune refresh requires explicit execution-budget authorization; cached result retained"
    ledger_path = environment.get("DUNE_EXECUTION_LEDGER")
    if not ledger_path:
        return "Dune execution ledger is missing; cached result retained"
    try:
        if environment.get("GITHUB_ACTIONS") == "true":
            receipt_path = Path(environment["DUNE_EXECUTION_RECEIPT"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            ledger = _read_execution_ledger(Path(ledger_path))
            day = reference.astimezone(timezone.utc).date().isoformat()
            recorded = ledger["attempts"].get(query_id, {}).get(day)
            expected_token = f"{environment['GITHUB_RUN_ID']}:{environment['GITHUB_RUN_ATTEMPT']}"
            if (receipt != recorded or not isinstance(receipt, dict)
                    or receipt.get("run_token") != expected_token
                    or _parse_utc(receipt.get("reserved_at")) > reference):
                raise ValueError("Dune receipt does not match the committed reservation and run")
            # Outside the checkout, so consuming a receipt does not dirty source.
            with receipt_path.with_name(receipt_path.name + ".consumed").open("x") as handle:
                handle.write(expected_token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            directory_fd = os.open(receipt_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        else:
            reserve_execution_attempt(
                Path(ledger_path), query_id, f"local:{os.getpid()}", reference,
            )
    except (OSError, ValueError, KeyError, TypeError):
        return "Dune execution budget is unknown, unavailable or already spent; cached result retained"
    return None


def execution_refresh_due(
    section: dict[str, Any], now: datetime, refresh_hours: float = DEFAULT_REFRESH_HOURS,
) -> bool:
    """Conservative offline preflight for CI's durable reservation step."""
    if not isinstance(section, dict):
        return False
    previous = section.get("last_known_good")
    ended = _parse_utc(section.get("execution_ended_at"))
    if ended is None and isinstance(previous, dict):
        ended = _parse_utc(previous.get("execution_ended_at"))
    return (
        now.utcoffset() is not None and math.isfinite(refresh_hours) and refresh_hours > 0
        and ended is not None and now - ended > timedelta(hours=refresh_hours)
    )


def collect_dune(
    env: dict[str, str] | None = None,
    now: datetime | None = None,
    sleep: Any = None,
) -> dict[str, Any]:
    """Optional-source boundary: invalid data/configuration cannot stop collection."""
    environment = os.environ if env is None else env
    api_key = environment.get("DUNE_API_KEY")
    query_id = environment.get("DUNE_QUERY_ID")
    if not api_key or not query_id:
        return _unconfigured()
    if not isinstance(query_id, str) or re.fullmatch(r"[1-9][0-9]*", query_id) is None:
        return _build_unavailable("invalid Dune query id")
    reference = now if now is not None else datetime.now(timezone.utc)
    try:
        if reference.utcoffset() is None:
            return _build_unavailable("Dune reference time must include a UTC offset", query_id)
        refresh_hours = float(environment.get("DUNE_REFRESH_HOURS", DEFAULT_REFRESH_HOURS))
        if not math.isfinite(refresh_hours) or refresh_hours <= 0:
            return _build_unavailable("invalid Dune refresh window", query_id)
        return _collect_dune(environment, query_id, reference, refresh_hours, sleep)
    except Exception:
        # Do not retain arbitrary exception text: it can contain provider URLs/keys.
        return _build_unavailable("Dune collection failed validation or transport", query_id)


def _collect_dune(
    environment: dict[str, str], query_id: str, reference: datetime,
    refresh_hours: float, sleep: Any,
) -> dict[str, Any]:
    start = time.monotonic()
    deadline = start + EXECUTE_DEADLINE_SECONDS
    api_key = environment["DUNE_API_KEY"]
    query_url = f"{PUBLIC_QUERY_URL_BASE}{query_id}"
    results_url = f"{API_BASE}/{query_id}/results"
    previous = _previous_last_known_good(environment, query_id, reference)
    try:
        read_guard = _consume_result_read_reservation(environment, query_id, reference)
    except (OSError, ValueError, KeyError, TypeError):
        return _build_unavailable(
            "Dune result read skipped: finite owner-approved credit allowance is missing or spent",
            query_id, previous,
        )
    _, payload, error = _result_read(
        results_url, api_key, read_guard, sleep=sleep, deadline=deadline,
    )
    if payload is None:
        if error != "HTTP 404":
            return _build_unavailable(f"dune latest-result request failed: {error}", query_id, previous)
        # A saved query edit intentionally invalidates its old latest result.
        # Execute only with the independently precommitted attempt receipt and
        # second result-read allowance; a bare 404 never authorizes spending.
        reservation_error = _reserve_execution_attempt(environment, query_id, reference)
        if reservation_error:
            return _build_unavailable(reservation_error, query_id, previous)
        execution = _execute_and_poll(query_id, api_key, deadline, read_guard, sleep=sleep)
        if not execution["available"]:
            return _build_unavailable(execution["reason"], query_id, previous)
        updated = execution["payload"]
        effective_now = reference + timedelta(seconds=time.monotonic() - start)
        validation_error = _validate_result(updated, effective_now, query_id)
        if validation_error:
            return _build_unavailable(validation_error, query_id, previous)
        section = _success_section(query_id, query_url, SOURCE_URL, updated, "fresh", effective_now)
        return section if section.get("available") is True else _build_unavailable(
            section["reason"], query_id, previous,
        )
    effective_now = reference + timedelta(seconds=time.monotonic() - start)
    error = _validate_result(payload, effective_now, query_id)
    if error:
        return _build_unavailable(error, query_id, previous)
    ended_at = _parse_utc(_payload_field(payload, "execution_ended_at"))
    if effective_now - ended_at <= timedelta(hours=refresh_hours):
        section = _success_section(query_id, query_url, SOURCE_URL, payload, "fresh", effective_now)
        if section.get("available") is not True and previous is not None:
            return _build_unavailable(section["reason"], query_id, previous)
        return section

    # Preserve only the already validated cache, never a malformed new result.
    lkg = _last_known_good(
        query_id, query_url, results_url, payload,
        round((effective_now - ended_at).total_seconds()),
    )
    if lkg.get("aggregates") is None:
        lkg = previous
    error = _reserve_execution_attempt(environment, query_id, effective_now)
    if error:
        return _build_unavailable(error, query_id, lkg)
    execution = _execute_and_poll(query_id, api_key, deadline, read_guard, sleep=sleep)
    if not execution["available"]:
        return _build_unavailable(execution["reason"], query_id, lkg)
    updated = execution["payload"]
    effective_now = reference + timedelta(seconds=time.monotonic() - start)
    error = _validate_result(updated, effective_now, query_id)
    if error:
        return _build_unavailable(error, query_id, lkg)
    if _parse_utc(_payload_field(updated, "execution_ended_at")) <= ended_at:
        return _build_unavailable("refreshed execution is not newer than the cached result", query_id, lkg)
    section = _success_section(query_id, query_url, SOURCE_URL, updated, "fresh", effective_now)
    return section if section.get("available") is True else _build_unavailable(section["reason"], query_id, lkg)


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
    aggregates = None
    if ended_at is not None:
        aggregates = _derive_aggregates([
            row for row in result.get("rows", [])
            if isinstance(row, dict) and _day(row.get("day")) is not None
            and _day(row.get("day")) < ended_at.astimezone(timezone.utc).date()
        ])
    return {
        "aggregation_contract": AGGREGATION_CONTRACT,
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
        "aggregates": aggregates,
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
    xstock_rows: dict[str, dict[str, float]] = {}
    transaction_fees: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric_id = row.get("metric_id")
        parsed_day = _day(row.get("day"))
        day = parsed_day.isoformat() if parsed_day else None
        value = row.get("value")
        if not isinstance(day, str) or day == "" or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if metric_id == "daily_non_vote_fee_payers":
            fee_payers[day] = value
        elif metric_id == "daily_dex_volume_total":
            dex_total[day] = value
        elif metric_id == "daily_dex_volume_by_project":
            dimension = row.get("dimension")
            if not isinstance(dimension, str) or not dimension:
                continue
            per_day = dex_by_project.setdefault(day, {})
            per_day[dimension] = per_day.get(dimension, 0.0) + value
        elif metric_id.startswith("daily_xstocks_"):
            xstock_rows.setdefault(day, {})[metric_id] = value
        elif metric_id == "daily_transaction_fees_sol":
            transaction_fees[day] = value
    if not fee_payers and not dex_total and not xstock_rows and not transaction_fees:
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
    if dex_latest and dex_latest[0] in dex_by_project:
        project_day = dex_latest[0]
        top = sorted(dex_by_project[project_day].items(), key=lambda kv: kv[1], reverse=True)[:5]
        by_project_top = [{"dimension": name, "value": value} for name, value in top]
    xstock_day = max(xstock_rows) if xstock_rows else None
    xstock = xstock_rows.get(xstock_day, {})
    xstock_all = xstock.get("daily_xstocks_dex_trade_legs")
    xstock_priced = xstock.get("daily_xstocks_dex_priced_trade_legs")
    xstock_volume = xstock.get("daily_xstocks_dex_volume")
    if xstock_volume is not None:
        xstock_reason = None
    elif xstock_day is None:
        xstock_reason = "registered query result did not contain pinned xStock coverage rows"
    else:
        xstock_reason = "USD volume withheld because one or more scoped trade legs lacked valid pricing"
    fees_latest = latest(transaction_fees)
    candidates = [item[0] for item in (fee_latest, dex_latest, fees_latest) if item is not None]
    if xstock_day is not None:
        candidates.append(xstock_day)
    latest_day = max(candidates) if candidates else None
    return {
        "latest_day": latest_day,
        "fee_payers_latest": fee_latest[1] if fee_latest else None,
        "fee_payers_day": fee_latest[0] if fee_latest else None,
        "dex_volume_total_latest_usd": dex_latest[1] if dex_latest else None,
        "dex_volume_total_day": dex_latest[0] if dex_latest else None,
        "dex_volume_by_project_top": by_project_top,
        "dex_volume_by_project_day": project_day,
        "xstocks_dex_volume_latest_usd": xstock_volume,
        "xstocks_dex_trade_legs": xstock_all,
        "xstocks_dex_priced_trade_legs": xstock_priced,
        "xstocks_dex_day": xstock_day,
        "xstocks_dex_volume_available": xstock_volume is not None,
        "xstocks_dex_volume_reason": xstock_reason,
        "xstocks_registry": dict(XSTOCK_REGISTRY),
        "xstocks_basis": "covered xStocks DEX trade-leg volume; OR-matched rows counted once",
        "transaction_fees_latest_sol": fees_latest[1] if fees_latest else None,
        "transaction_fees_day": fees_latest[0] if fees_latest else None,
        "transaction_fees_basis": "all transaction fees in gas_solana.fees; not protocol REV or Jito tips",
        "basis": "provider-reported (Dune); trade-leg volume, not unique-user volume",
        "scope": "completed UTC days before execution; fee payers exclude vote transactions; DEX volume sums swap legs",
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
    section = {
        "available": freshness == "fresh",
        "aggregation_contract": AGGREGATION_CONTRACT,
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
    # Use the execution date, not collection date: a cached partial day never
    # becomes a complete day just because the report is collected tomorrow.
    aggregates = _derive_aggregates([
        row for row in result.get("rows", [])
        if _day(row.get("day")) < ended_at.astimezone(timezone.utc).date()
    ])
    if aggregates is None:
        return _build_unavailable("no completed UTC-day metric totals in the result", query_id)
    section["aggregates"] = aggregates
    return section


def _execute_and_poll(
    query_id: str, api_key: str, deadline: float, read_guard: dict[str, int], sleep: Any = None,
) -> dict[str, Any]:
    """Execute once, then poll only that execution within the elapsed budget."""
    pause = time.sleep if sleep is None else sleep
    _, payload, error = _request_with_retry(
        f"{API_BASE}/{query_id}/execute", api_key, method="POST", body={},
        sleep=pause, deadline=deadline,
    )
    if payload is None:
        return {"available": False, "reason": f"dune execute failed: {error}; attempt remains spent"}
    execution_id = _response_execution_id(payload)
    if not execution_id or re.fullmatch(r"[A-Za-z0-9_-]+", execution_id) is None:
        return {"available": False, "reason": "dune execute returned an invalid execution id"}
    execution_url = f"https://api.dune.com/api/v1/execution/{execution_id}"
    while time.monotonic() < deadline:
        _, status, error = _request_with_retry(
            f"{execution_url}/status", api_key, sleep=pause, deadline=deadline,
        )
        if status is None:
            return {"available": False, "reason": f"dune status failed: {error}"}
        if (_response_execution_id(status) != execution_id
                or str(status.get("query_id")) != query_id):
            return {"available": False, "reason": "dune status execution/query identity mismatch"}
        state = status.get("state")
        if state == "QUERY_STATE_COMPLETED":
            _, result, error = _result_read(
                f"{execution_url}/results", api_key, read_guard, sleep=pause, deadline=deadline,
            )
            if result is None:
                return {"available": False, "reason": f"dune execution results failed: {error}"}
            if _response_execution_id(result) != execution_id:
                return {"available": False, "reason": "dune result execution identity mismatch"}
            return {"available": True, "payload": result}
        if state not in {"QUERY_STATE_PENDING", "QUERY_STATE_EXECUTING"}:
            return {"available": False, "reason": f"dune execution did not complete ({state})"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        pause(min(EXECUTE_POLL_INTERVAL_SECONDS, remaining))
    return {"available": False, "reason": "dune execution timed out within the elapsed request budget"}
