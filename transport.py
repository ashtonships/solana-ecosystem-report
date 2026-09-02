"""Shared bounded reads and bounded retries for stdlib HTTP collectors."""

from __future__ import annotations

import time
import urllib.error
from typing import Any, Callable, TypeVar

MAX_RESPONSE_BYTES = 16 * 1024 * 1024

# Bounded retry for transient fetch failures: 429 and 5xx (plus network-level
# URLError/timeout) get a short backoff; 4xx client errors fail fast because a
# retry cannot fix a bad request or a rights-held source.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_ATTEMPTS = 3
RETRY_INITIAL_DELAY_SECONDS = 0.5
RETRY_BACKOFF_MULTIPLIER = 2.0

T = TypeVar("T")

# Module-level indirection so tests can patch the clock without real delays.
_sleep = time.sleep


def read_bounded(response: Any, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most one byte beyond the limit so oversized bodies fail closed."""
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"response exceeds {limit} bytes")
    return body


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_STATUS_CODES
    if isinstance(error, urllib.error.URLError):
        return True  # DNS, refused, TLS handshake: transient network-level
    if isinstance(error, TimeoutError):
        return True
    return False


def fetch_with_retry(
    open_request: Callable[[], Any],
    *,
    attempts: int = RETRY_ATTEMPTS,
    initial_delay: float = RETRY_INITIAL_DELAY_SECONDS,
    backoff: float = RETRY_BACKOFF_MULTIPLIER,
    sleep: Callable[[float], None] | None = None,
    retryable: Callable[[BaseException], bool] = _is_retryable,
) -> Any:
    """Run ``open_request()`` with bounded retry on transient failures.

    ``open_request`` must open the request and return the response context
    manager (or raise). Success paths are untouched: the first successful call
    wins and no sleep ever runs. Non-retryable errors (4xx except 429, JSON
    errors surfaced after the open) propagate immediately.
    """
    pause = sleep if sleep is not None else _sleep
    delay = initial_delay
    for attempt in range(attempts):
        try:
            return open_request()
        except BaseException as error:  # noqa: BLE001 - classified below
            if attempt + 1 >= attempts or not retryable(error):
                raise
            pause(delay)
            delay *= backoff
    raise AssertionError("unreachable")
