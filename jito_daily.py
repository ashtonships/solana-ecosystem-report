#!/usr/bin/env python3
"""Held Jito daily-MEV adapter; production adoption requires source approval.

The Jito Foundation documents these as separate provider-labelled daily fields.
This module preserves them separately and never derives protocol REV or a
combined tip total. It is intentionally not wired into collection or output.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
import re
from typing import Any
import urllib.error
import urllib.request

import transport


SOURCE_URL = "https://kobe.mainnet.jito.network/api/v1/daily_mev_rewards"
DOCUMENTATION_URL = (
    "https://www.jito.network/docs/jitosol/jitosol-liquid-staking/"
    "for-developers/stake-pool-api/#6-daily-mev-rewards"
)
PUBLICATION_STATUS = "held_pending_owner_acceptance_of_jito_tooling_terms"
MAX_COMPLETED_DAYS = 30
EXPECTED_FIELDS = frozenset({
    "day", "count_mev_tips", "jito_tips", "validator_tips", "tippers",
})
UNITS = {
    "day": "UTC calendar day",
    "count_mev_tips": "MEV tip transactions",
    "jito_tips": "SOL",
    "validator_tips": "SOL",
    "tippers": "unique accounts",
}
FIELD_DEFINITIONS = {
    "count_mev_tips": "individual MEV tip transactions that occurred on the day",
    "jito_tips": "MEV tips paid to Jito on the day",
    "validator_tips": "MEV tips distributed to validators on the day",
    "tippers": "unique accounts that submitted MEV tips on the day",
}
DAY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}) 00:00:00\.000 UTC$")


def _reference_time(value: Any) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    match = DAY_PATTERN.fullmatch(value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _amount(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except OverflowError:
        return False


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "source_url": SOURCE_URL,
        "documentation_url": DOCUMENTATION_URL,
        "publication_status": PUBLICATION_STATUS,
    }


def parse_daily_rewards(raw: Any, now: datetime) -> dict[str, Any]:
    """Validate all source rows, exclude the current UTC day, and retain 30."""
    reference = _reference_time(now)
    if reference is None:
        return _unavailable("reference time must be an offset-aware datetime")
    if not isinstance(raw, list) or not raw:
        return _unavailable("daily MEV response must be a nonempty list")

    rows: list[tuple[date, dict[str, Any]]] = []
    seen_days: set[date] = set()
    for item in raw:
        if not isinstance(item, dict) or not EXPECTED_FIELDS.issubset(item):
            return _unavailable("daily MEV row is missing a documented field")
        day = _day(item.get("day"))
        if day is None or day > reference.date():
            return _unavailable("daily MEV row has an invalid or future UTC day")
        if day in seen_days:
            return _unavailable("daily MEV response contains a duplicate UTC day")
        seen_days.add(day)
        if not _count(item.get("count_mev_tips")) or not _count(item.get("tippers")):
            return _unavailable("daily MEV count fields must be nonnegative integers")
        if not _amount(item.get("jito_tips")) or not _amount(item.get("validator_tips")):
            return _unavailable("daily MEV SOL fields must be finite and nonnegative")
        if day == reference.date():
            continue
        rows.append((day, {
            "day": item["day"],
            "count_mev_tips": item["count_mev_tips"],
            "jito_tips": item["jito_tips"],
            "validator_tips": item["validator_tips"],
            "tippers": item["tippers"],
        }))

    rows.sort(key=lambda item: item[0])
    completed_count = len(rows)
    selected = [item for _, item in rows[-MAX_COMPLETED_DAYS:]]
    if not selected:
        return _unavailable("daily MEV response has no completed UTC days")
    return {
        "available": True,
        "observed_at": reference.isoformat(timespec="seconds"),
        "source_url": SOURCE_URL,
        "documentation_url": DOCUMENTATION_URL,
        "publication_status": PUBLICATION_STATUS,
        "scope": "Jito Foundation provider-labelled daily MEV tip fields",
        "aggregation_contract": "completed-utc-days-v1",
        "current_utc_day_excluded": True,
        "completed_day_count": completed_count,
        "returned_day_count": len(selected),
        "returned_day_limit": MAX_COMPLETED_DAYS,
        "units": dict(UNITS),
        "field_definitions": dict(FIELD_DEFINITIONS),
        "rows": selected,
        "note": (
            "Provider-labelled fields are retained separately; no combined tips, "
            "gross revenue, protocol REV, or relationship between fields is derived."
        ),
    }


def fetch_daily_rewards(timeout: int = 20) -> Any | None:
    """Fetch one bounded JSON response; this held adapter is not called by production."""
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "solana-ecosystem-report/0.1",
        },
    )
    try:
        with transport.fetch_with_retry(
            lambda: urllib.request.urlopen(request, timeout=timeout),
        ) as response:
            return json.loads(transport.read_bounded(response).decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def collect_jito_daily(
    now: datetime | None = None, timeout: int = 20,
) -> dict[str, Any]:
    """Fetch and parse for private review; callers must enforce the owner gate."""
    reference = now or datetime.now(timezone.utc)
    return parse_daily_rewards(fetch_daily_rewards(timeout), reference)
