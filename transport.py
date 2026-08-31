"""Shared bounded reads for stdlib HTTP collectors."""

from __future__ import annotations

from typing import Any

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def read_bounded(response: Any, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most one byte beyond the limit so oversized bodies fail closed."""
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"response exceeds {limit} bytes")
    return body
