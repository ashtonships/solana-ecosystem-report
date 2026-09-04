"""Pure scheduling helpers for bounded source refresh and exact reuse."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


INTERVALS = {
    "activity": 3_600,
    "block_production": 3_600,
    "feature_activation": 3_600,
    "news": 21_600,
    "growth_providers": 21_600,
    "growth_tokens": 86_400,
    "dune": 86_400,
}
STATES = frozenset({"fresh", "reused", "failed"})


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _valid_entry(entry: Any, source_key: str, now: datetime) -> bool:
    if not isinstance(entry, dict) or set(entry) != {
        "last_attempt_at", "last_success_at", "interval_seconds", "state",
    }:
        return False
    if (type(entry.get("interval_seconds")) is not int
            or entry.get("interval_seconds") != INTERVALS.get(source_key)):
        return False
    state = entry.get("state")
    if not isinstance(state, str) or state not in STATES:
        return False
    attempt = entry.get("last_attempt_at")
    success = entry.get("last_success_at")
    attempt_at = _utc_timestamp(attempt) if attempt is not None else None
    success_at = _utc_timestamp(success) if success is not None else None
    if (attempt is not None and attempt_at is None) or (
        success is not None and success_at is None
    ):
        return False
    if attempt_at is not None and attempt_at > now:
        return False
    if success_at is not None and success_at > now:
        return False
    if attempt_at is None and success_at is not None:
        return False
    if attempt_at is not None and success_at is not None and success_at > attempt_at:
        return False
    if state == "fresh" and (
        attempt_at is None or success_at != attempt_at
    ):
        return False
    if state == "failed" and attempt_at is None:
        return False
    return True


def initial_schedule() -> dict[str, dict[str, Any]]:
    """Return explicit first-run entries; null anchors make free sources due."""
    return {
        source_key: {
            "last_attempt_at": None,
            "last_success_at": None,
            "interval_seconds": interval_seconds,
            "state": "reused",
        }
        for source_key, interval_seconds in INTERVALS.items()
    }


def collection_schedule(
    value: Any, now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Keep valid prior entries or bootstrap an absent legacy schedule."""
    schedule = initial_schedule()
    if value is None:
        return schedule
    if not isinstance(value, dict) or set(value) != set(INTERVALS):
        raise ValueError("collection schedule must contain the exact source keys")
    reference = now or datetime.now(timezone.utc)
    for source_key in INTERVALS:
        entry = value.get(source_key)
        if not _valid_entry(entry, source_key, reference):
            raise ValueError(f"invalid collection schedule entry: {source_key}")
        schedule[source_key] = dict(entry)
    return schedule


def source_due(schedule: Any, source_key: str, now: datetime) -> bool:
    """Return whether an explicitly valid source entry has reached its interval."""
    if (source_key not in INTERVALS or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(now)
            or not isinstance(schedule, dict)):
        return False
    entry = schedule.get(source_key)
    if not _valid_entry(entry, source_key, now):
        return False
    anchor = entry.get("last_attempt_at") or entry.get("last_success_at")
    if anchor is None:
        return True
    elapsed = (now - _utc_timestamp(anchor)).total_seconds()
    return elapsed >= entry["interval_seconds"]


def update_source(
    schedule: dict[str, dict[str, Any]], source_key: str, now: datetime,
    *, attempted: bool, succeeded: bool,
) -> None:
    """Record one refresh decision without advancing a reused source clock."""
    if source_key not in INTERVALS or not _valid_entry(
        schedule.get(source_key), source_key, now,
    ):
        raise ValueError(f"invalid cadence entry for {source_key}")
    if succeeded and not attempted:
        raise ValueError("a source cannot succeed without an attempt")
    entry = schedule[source_key]
    if not attempted:
        failed_without_new_success = (
            entry["state"] == "failed"
            or (
                entry["last_attempt_at"] is not None
                and (
                    entry["last_success_at"] is None
                    or _utc_timestamp(entry["last_attempt_at"])
                    > _utc_timestamp(entry["last_success_at"])
                )
            )
        )
        entry["state"] = "failed" if failed_without_new_success else "reused"
        return
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    entry["last_attempt_at"] = stamp
    if succeeded:
        entry["last_success_at"] = stamp
        entry["state"] = "fresh"
    else:
        entry["state"] = "failed"
