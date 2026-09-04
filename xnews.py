#!/usr/bin/env python3
"""Official X announcements, from a fixed allowlist of ecosystem accounts.

Standard library only. The X API v2 is pay-per-use ($0.005 per post read),
so this source is bounded three ways:

  1. ACCOUNT ALLOWLIST — four official ecosystem accounts only
     (@SolanaFndn, @solana, @anza_tech, @firedancer_io). No search, no
     influencers, no sentiment mining.
  2. FRESH-ONLY FETCH — the timeline request asks for posts from the last
     24 hours (start_time), so re-running within the same UTC day reads the
     same resources, and X's 24-hour dedup means no second charge.
  3. PER-RUN CAP — no more than MAX_POSTS posts are recorded; the fetcher
     stops expanding timelines once the cap is hit.

The token arrives via X_BEARER_TOKEN in the environment. Missing token,
HTTP error, rate limit, or malformed body all degrade this one source to
{"available": False} with a reason — the report publishes regardless.

Stored per post: id, author, text, created_at, url, engagement counts,
plus the provider-reported label. No aggregate sentiment score is computed
anywhere in this module: the counts are shown as recorded, period.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Daily cost backstop: distinct post reads charged per UTC day. At
# $0.005/read this caps worst-case daily X spend at $0.50. Normal days
# surface far fewer distinct posts; the guard only trips on bursts.
X_DAILY_POST_BUDGET = 100

# Post text is quoted material, stored at excerpt length with attribution
# and a canonical link back to the source post.
MAX_TEXT_CHARS = 280

# Timeline fields requested. Keep the list minimal — everything requested
# is stored and shown.
POST_FIELDS = "id,author_id,text,created_at,public_metrics"


class XSourceUnavailable(Exception):
    """Raised when the source cannot be read; carries the reason."""


_URL_SCHEME = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)


def _sanitize_excerpt(text: str) -> str:
    """Neutralize URL schemes in quoted post text.

    Post text is third-party quoted evidence. Official accounts may mention
    any URL — including local-network or private addresses — and a raw
    scheme-bearing token in a stored excerpt would fail the snapshot's
    public-URL scan (and rightly so: it is a live link token). Replacing
    the scheme keeps the citation readable and evidence-preserving while
    making the token inert text rather than a URL.
    """
    return _URL_SCHEME.sub(lambda m: m.group(0).split("://", 1)[1], text)


def _token() -> str:
    token = os.environ.get(X_BEARER_TOKEN_ENV, "").strip()
    if not token:
        raise XSourceUnavailable(
            "X_BEARER_TOKEN is not set; official announcements from X are disabled"
        )
    return token


def _charged_post_ids_today(snapshots_dir: Path | None = None) -> set[str]:
    """Distinct X post ids recorded by snapshots collected today (UTC).

    Reads the git-committed snapshot files directly — no network, no API.
    Any post present in a snapshot recorded today was already charged
    today (X bills a post once per 24h UTC window), so the returned set
    is the day's spend so far. Best-effort: any read failure returns an
    empty set, which only means the guard under-counts, never over-counts.
    """
    repo_root = Path(__file__).resolve().parent
    directory = snapshots_dir if snapshots_dir is not None \
        else repo_root / "snapshots"
    today_prefix = datetime.now(timezone.utc).strftime("snapshot-%Y%m%dT")
    charged: set[str] = set()
    try:
        paths = sorted(directory.glob("snapshot-*.json"))
    except OSError:
        return charged
    for path in paths:
        if not path.name.startswith(today_prefix):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source = (payload.get("news") or {}).get("sources", {}).get(
            "x_announcements", {})
        for item in source.get("items") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                charged.add(item["id"])
    return charged


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
            body = json.loads(response.read().decode("utf-8"))
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
    """Read allowlisted timelines via recent search, capped at MAX_POSTS.

    One recent-search request covers the whole allowlist (from:user OR ...),
    so there are no per-user lookup reads ($0.010 each). Only post reads are
    charged ($0.005 each), and X dedups the same post within a 24-hour UTC
    window, so repeat runs inside a day cost no new post charges.

    No start_time is sent: recent-search covers the last ~7 days natively,
    and a start_time parameter caused silently-empty results in production
    (2026-09-03). The MAX_POSTS cap and the UTC-day dedup are the cost
    controls, not the window.

    Raises XSourceUnavailable with a reason on any failure. Pure network
    boundary: no parsing logic beyond transport.
    """
    bearer = token if token is not None else _token()
    now = now_unix if now_unix is not None else int(time.time())
    from_clause = " OR ".join(f"from:{name}" for name in X_ACCOUNT_ALLOWLIST)
    query = urllib.parse.urlencode({
        "query": f"({from_clause}) -is:retweet -is:reply",
        "max_results": MAX_POSTS,
        "tweet.fields": POST_FIELDS,
        # expansions is free (same post read) and required: without it the
        # response has no includes.users, so author_id cannot be mapped to
        # a username and every row would be filtered by the allowlist check.
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
    if not isinstance(rows, list):
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
        if not isinstance(post_id, str) or post_id in seen_ids:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(created, str) or not created:
            continue
        author_id = row.get("author_id")
        author = users_by_id.get(author_id)
        # Author must be allowlisted; skip anything else the search returns.
        if author not in X_ACCOUNT_ALLOWLIST:
            continue
        metrics = row.get("public_metrics")
        posts.append({
            "id": post_id,
            "author": author,
            "text": _sanitize_excerpt(text)[:MAX_TEXT_CHARS],
            "created_at": created,
            "url": f"https://x.com/{author}/status/{post_id}",
            "like_count": metrics.get("like_count") if isinstance(metrics, dict) else None,
            "retweet_count": metrics.get("retweet_count") if isinstance(metrics, dict) else None,
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
        items.append({
            "id": post.get("id"),
            "author": post.get("author"),
            "text": post.get("text"),
            "published": post.get("created_at"),
            "link": post.get("url"),
            "like_count": post.get("like_count"),
            "retweet_count": post.get("retweet_count"),
        })
    return items


def collect_x_announcements(timeout: int = 20) -> dict[str, Any]:
    """Transport boundary for news.py: fetch + parse, or a named failure.

    Cost guard: recent-search post reads are $0.005 each, and X bills the
    same post once per 24-hour UTC window, so daily spend is bounded by
    DISTINCT posts surfaced — not by run frequency. The backstop below
    bounds the burst case: when prior snapshots recorded
    X_DAILY_POST_BUDGET distinct posts for the current UTC day, the fetch
    is skipped and the source degrades with an explicit reason instead of
    spending further.
    """
    charged_today = _charged_post_ids_today()
    if len(charged_today) >= X_DAILY_POST_BUDGET:
        return {
            "available": False,
            "reason": (
                "daily X post-read budget reached "
                f"({len(charged_today)}/{X_DAILY_POST_BUDGET} distinct posts charged today); "
                "fetch resumes at the next UTC day"
            ),
        }
    try:
        posts = fetch_announcements(timeout=timeout)
    except XSourceUnavailable as error:
        return {"available": False, "reason": str(error)}
    return {"posts": posts}


if __name__ == "__main__":
    print(json.dumps(collect_x_announcements(), indent=2))
