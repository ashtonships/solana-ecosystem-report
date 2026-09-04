#!/usr/bin/env python3
"""Official X announcements from four allowlisted ecosystem accounts.

One recent-search request returns at most 20 posts. Every paid request needs
an advance reservation against a finite, owner-set post allowance. Charges
are conservatively reserved even if transport, parsing or publication fails;
published snapshots cannot establish the account's paid usage.

Missing credentials or budget evidence degrades this source alone. Current
prices and account spending limits must be checked in the X Developer Console.
"""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import textwrap
import time
import uuid
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import transport

X_API_BASE = "https://api.x.com/2"
X_BEARER_TOKEN_ENV = "X_BEARER_TOKEN"

# Fixed allowlist: official ecosystem accounts only.
X_ACCOUNT_ALLOWLIST: tuple[str, ...] = (
    "SolanaFndn",
    "solana",
    "anza_tech",
    "firedancer_io",
)

# Cost ceiling: hard cap on post reads recorded per collection run.
MAX_POSTS = 20

# An upper bound, not a dollar-price claim. An approved ledger may be stricter.
X_DAILY_POST_BUDGET = 100
X_MIN_SEARCH_POSTS = 10
X_READ_LEDGER = Path(__file__).resolve().parent / ".github" / "x-read-budget.json"

# Post text is quoted material, stored at excerpt length with attribution
# and a canonical link back to the source post.
MAX_TEXT_CHARS = 280

# Timeline fields requested. Keep the list minimal — everything requested
# is stored and shown.
POST_FIELDS = "id,author_id,text,created_at,public_metrics"


class XSourceUnavailable(Exception):
    """Raised when the source cannot be read; carries the reason."""


_URL_SCHEME = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)


def _plain_text(text: str) -> str:
    """Keep source excerpts readable and plain in generated report formats."""
    return "".join(character for character in html.unescape(text)
                   if unicodedata.category(character) not in {"So", "Cs"}
                   and character not in {"\ufe0f", "\u200d", "\u20e3"}).strip()


def _sanitize_excerpt(text: str) -> str:
    """Neutralize URL schemes in quoted post text.

    Post text is third-party quoted evidence. Official accounts may mention
    any URL — including local-network or private addresses — and a raw
    scheme-bearing token in a stored excerpt would fail the snapshot's
    public-URL scan (and rightly so: it is a live link token). Replacing
    the scheme keeps the citation readable and evidence-preserving while
    making the token inert text rather than a URL.
    """
    return _URL_SCHEME.sub(lambda m: m.group(0).split("://", 1)[1], _plain_text(text))


def _token() -> str:
    token = os.environ.get(X_BEARER_TOKEN_ENV, "").strip()
    if not token:
        raise XSourceUnavailable(
            "X_BEARER_TOKEN is not set; official announcements from X are disabled"
        )
    return token


def _read_budget(path: Path) -> dict[str, Any]:
    """Reject missing/corrupt accounting rather than infer spend from snapshots."""
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(ledger, dict) or set(ledger) != {
            "version", "starts_on", "expires_on", "total_post_limit",
            "daily_post_limit", "reservations",
        } or type(ledger["version"]) is not int or ledger["version"] != 1:
            raise ValueError("schema")
        start = datetime.strptime(ledger["starts_on"], "%Y-%m-%d").date()
        end = datetime.strptime(ledger["expires_on"], "%Y-%m-%d").date()
        if start >= end:
            raise ValueError("window")
        for key in ("total_post_limit", "daily_post_limit"):
            if type(ledger[key]) is not int or ledger[key] < 0:
                raise ValueError("limit")
        if ledger["daily_post_limit"] > X_DAILY_POST_BUDGET:
            raise ValueError("daily ceiling")
        reservations = ledger["reservations"]
        if not isinstance(reservations, dict):
            raise ValueError("reservations")
        daily: dict[str, int] = {}
        for run_token, receipt in reservations.items():
            if not isinstance(run_token, str) or not run_token or not isinstance(receipt, dict):
                raise ValueError("receipt")
            if set(receipt) != {"run_token", "utc_date", "reserved_at", "posts"}:
                raise ValueError("receipt fields")
            stamp = datetime.fromisoformat(receipt["reserved_at"])
            if (stamp.tzinfo is None or stamp.utcoffset() is None
                    or receipt["run_token"] != run_token
                    or receipt["utc_date"] != stamp.astimezone(timezone.utc).date().isoformat()
                    or not start <= stamp.astimezone(timezone.utc).date() < end
                    or type(receipt["posts"]) is not int
                    or not X_MIN_SEARCH_POSTS <= receipt["posts"] <= MAX_POSTS):
                raise ValueError("receipt values")
            day = receipt["utc_date"]
            daily[day] = daily.get(day, 0) + receipt["posts"]
        if (sum(daily.values()) > ledger["total_post_limit"]
                or any(count > ledger["daily_post_limit"] for count in daily.values())):
            raise ValueError("overspent ledger")
        return ledger
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        raise XSourceUnavailable(
            "X paid reads paused: approved remaining-budget ledger is missing or invalid"
        ) from error


def reserve_post_reads(path: Path, run_token: str,
                       now: datetime | None = None) -> dict[str, Any]:
    """Durably debit the worst-case request before any network request.

    The workflow serializes runs and pushes this ledger before collection.
    The exclusive local lock also prevents two local collectors overspending.
    A killed reservation leaves a lock and requires accounting reconciliation.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not isinstance(run_token, str) or not run_token:
        raise XSourceUnavailable("X budget reservation requires UTC time and a run identity")
    now = now.astimezone(timezone.utc)
    lock = path.with_name(path.name + ".lock")
    try:
        claim = lock.open("x", encoding="utf-8")
    except OSError as error:
        raise XSourceUnavailable("X budget ledger unavailable or reserved by another process") from error
    temporary = None
    try:
        with claim:
            ledger = _read_budget(path)
            day = now.date().isoformat()
            if not ledger["starts_on"] <= day < ledger["expires_on"]:
                raise XSourceUnavailable("X paid reads paused: approved budget window is inactive")
            if run_token in ledger["reservations"]:
                raise XSourceUnavailable("X run already reserved; ambiguous attempts are never refunded")
            receipts = ledger["reservations"].values()
            used = sum(row["posts"] for row in receipts)
            today = sum(row["posts"] for row in receipts if row["utc_date"] == day)
            posts = min(MAX_POSTS, ledger["total_post_limit"] - used,
                        ledger["daily_post_limit"] - today)
            if posts < X_MIN_SEARCH_POSTS:
                raise XSourceUnavailable(
                    "X paid reads paused: remaining total/daily allowance is below the 10-post search minimum"
                )
            receipt = {"run_token": run_token, "utc_date": day,
                       "reserved_at": now.isoformat(), "posts": posts}
            ledger["reservations"][run_token] = receipt
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(ledger, output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return receipt
    except OSError as error:
        raise XSourceUnavailable("X budget reservation could not be saved; paid read skipped") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _consume_reservation(now: datetime) -> int:
    if os.environ.get("X_PAID_READS_ENABLED") != "true":
        raise XSourceUnavailable("X paid reads paused until the remaining account allowance is approved")
    ledger_path = Path(os.environ.get("X_READ_LEDGER") or X_READ_LEDGER)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return reserve_post_reads(ledger_path, str(uuid.uuid4()), now)["posts"]
    receipt_path = Path(os.environ.get("X_READ_RECEIPT") or "")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        ledger = _read_budget(ledger_path)
        run_token = f"{os.environ['GITHUB_RUN_ID']}:{os.environ['GITHUB_RUN_ATTEMPT']}"
        day = now.astimezone(timezone.utc).date().isoformat()
        if (receipt != ledger["reservations"].get(run_token)
                or receipt["utc_date"] != day
                or not ledger["starts_on"] <= day < ledger["expires_on"]
                or datetime.fromisoformat(receipt["reserved_at"]) > now):
            raise ValueError("mismatched receipt")
        # Created before HTTP. Retrying a process on this runner cannot replay it.
        with receipt_path.with_name(receipt_path.name + ".consumed").open("x") as marker:
            marker.write(run_token)
            marker.flush()
            os.fsync(marker.fileno())
        directory = os.open(receipt_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt["posts"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise XSourceUnavailable("X paid read requires an unused, committed reservation for this run") from error


def _request(url: str, token: str, timeout: int) -> dict[str, Any]:
    if not token:
        raise XSourceUnavailable(
            "X_BEARER_TOKEN is not set; official announcements from X are disabled"
        )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "solana-ecosystem-report/0.1",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(transport.read_bounded(response).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            detail = {
                401: "invalid or revoked token (401)",
                403: "token lacks Pay-Per-Use access to this endpoint (403); "
                     "the token must come from an app inside the "
                     "solana-ecosystem-report Pay-Per-Use project",
            }.get(error.code, f"HTTP {error.code}")
            raise XSourceUnavailable(f"X API rejected the credentials: {detail}") from error
        if error.code == 429:
            raise XSourceUnavailable("X API rate limit or credit cap reached") from error
        raise XSourceUnavailable(f"X API returned HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise XSourceUnavailable("X API request failed or timed out") from error
    if not isinstance(body, dict):
        raise XSourceUnavailable("X API response is not a JSON object")
    return body


def fetch_announcements(
    now_unix: int | None = None, timeout: int = 20,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """One bounded recent search; no pagination or automatic paid retries."""
    bearer = token if token is not None else _token()
    now = now_unix if now_unix is not None else int(time.time())
    maximum = _consume_reservation(datetime.fromtimestamp(now, timezone.utc))
    from_clause = " OR ".join(f"from:{name}" for name in X_ACCOUNT_ALLOWLIST)
    query = urllib.parse.urlencode({
        "query": f"({from_clause}) -is:retweet -is:reply",
        "max_results": maximum,
        "post.fields": POST_FIELDS,
        # Author expansion identifies the allowlisted publisher without lookups.
        "expansions": "author_id",
    })
    body = _request(f"{X_API_BASE}/tweets/search/recent?{query}", bearer, timeout)
    if "title" in body and not body.get("data"):
        # X returns client errors inside HTTP 200 for some conditions.
        raise XSourceUnavailable(
            f"X API error response: {body.get('title') or 'unknown'}")
    rows = body.get("data")
    if rows is None:
        # A search with no matches returns no data key: not an error.
        return []
    if not isinstance(rows, list) or len(rows) > maximum:
        raise XSourceUnavailable("X API response data is not a list")
    users_by_id = {}
    includes = body.get("includes")
    if isinstance(includes, dict) and isinstance(includes.get("users"), list):
        for user in includes["users"]:
            if isinstance(user, dict) and isinstance(user.get("id"), str):
                users_by_id[user["id"]] = user.get("username")
    posts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        if len(posts) >= MAX_POSTS:
            break
        if not isinstance(row, dict):
            continue
        post_id = row.get("id")
        text = row.get("text")
        created = row.get("created_at")
        if (not isinstance(post_id, str) or re.fullmatch(r"[0-9]{1,19}", post_id) is None
                or post_id in seen_ids):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(created, str) or not created:
            continue
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if published.tzinfo is None or published.timestamp() > now:
                continue
        except (ValueError, OverflowError):
            continue
        author_id = row.get("author_id")
        author = users_by_id.get(author_id) if isinstance(author_id, str) else None
        # Author must be allowlisted; skip anything else the search returns.
        canonical = {name.lower(): name for name in X_ACCOUNT_ALLOWLIST}
        author = canonical.get(author.lower()) if isinstance(author, str) else None
        if author is None:
            continue
        metrics = row.get("public_metrics")
        def count(name):
            value = metrics.get(name) if isinstance(metrics, dict) else None
            return value if type(value) is int and value >= 0 else None
        posts.append({
            "id": post_id,
            "author": author,
            "text": textwrap.shorten(_sanitize_excerpt(text),
                                     width=MAX_TEXT_CHARS, placeholder="…"),
            "created_at": created,
            "url": f"https://x.com/{author}/status/{post_id}",
            "like_count": count("like_count"),
            # Keep the report's established field name while adapting X's
            # current provider response at the transport boundary.
            "retweet_count": count("repost_count"),
        })
        seen_ids.add(post_id)
    posts.sort(key=lambda post: (post["created_at"], post["id"]), reverse=True)
    return posts


def parse_announcements(
    posts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Contract-shape the fetched posts (already bounded by the fetcher)."""
    if posts is None:
        return []
    items = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        text = post.get("text")
        excerpt = (textwrap.shorten(_sanitize_excerpt(text), width=MAX_TEXT_CHARS, placeholder="…")
                   if isinstance(text, str) else None)
        items.append({
            "id": post.get("id"),
            "author": post.get("author"),
            "text": excerpt,
            "published": post.get("created_at"),
            "link": post.get("url"),
            "like_count": post.get("like_count"),
            "retweet_count": post.get("retweet_count"),
        })
    return items


def collect_x_announcements(timeout: int = 20) -> dict[str, Any]:
    """Fetch or report a named source failure; budget is guarded at HTTP entry."""
    try:
        posts = fetch_announcements(timeout=timeout)
    except XSourceUnavailable as error:
        return {"available": False, "reason": str(error)}
    return {"posts": posts}


if __name__ == "__main__":
    print(json.dumps(collect_x_announcements(), indent=2))
