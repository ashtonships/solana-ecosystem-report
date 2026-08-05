#!/usr/bin/env python3
"""Ecosystem releases and announcements, from keyless public feeds.

Standard library only — `urllib` to fetch, `xml.etree` to parse. No API key,
no account, no token, no third-party feed reader. Each candidate feed below
was verified by direct unauthenticated request before any code was written
against it:

    Agave releases    github.com/anza-xyz/agave/releases.atom          200, 10 entries
    SIMD proposals    …/solana-improvement-documents/commits/main.atom 200, 20 entries
    Network status    status.solana.com/history.atom                   200, 25 entries

One candidate was dropped: the SIMD repository's *releases* feed
(`…/solana-improvement-documents/releases.atom`) answers 200 but publishes
zero entries — that repository tags no releases. Shipping it would have meant
an always-empty panel that looks like a data problem. Its commit feed carries
the actual proposal activity, and that is what is used instead.

Same architecture as `economics.py`, for the same reasons:

**Network access is confined to `fetch`.** Every transform is pure, so the
whole parser is tested offline against committed fixture feeds and never
against the live network.

**Each source degrades alone.** A timeout, an HTTP error, a body that is not
XML, or an XML document that is not a feed all resolve to
`{"available": False}` for that source and leave the others untouched. A
source that fails renders as *unavailable* — never as "no releases", which
would be a claim about the ecosystem rather than about the fetch.

**Items are recorded into the snapshot.** Rendering never re-fetches, so a
committed snapshot re-renders to the same page offline, months later.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from typing import Any

ATOM = "{http://www.w3.org/2005/Atom}"

# Items kept per source. Enough to show activity, few enough to stay a summary.
MAX_ITEMS = 5

# Titles longer than this are cut — a commit subject can run to a paragraph.
MAX_TITLE = 140

SOURCES: dict[str, dict[str, str]] = {
    "agave_releases": {
        "url": "https://github.com/anza-xyz/agave/releases.atom",
        "label": "Agave validator releases",
        "publisher": "anza-xyz/agave (GitHub)",
        "why": "Agave is the validator client most of the network runs. A release "
               "here is the software operators are about to be asked to run.",
    },
    "simd_proposals": {
        "url": "https://github.com/solana-foundation/solana-improvement-documents"
               "/commits/main.atom",
        "label": "SIMD proposal activity",
        "publisher": "solana-foundation/solana-improvement-documents (GitHub)",
        "why": "Protocol changes are proposed and amended here before they ship. "
               "This is the commit feed, so it shows drafting activity, not "
               "acceptance — a commit is not a merged-and-agreed upgrade.",
    },
    "network_status": {
        "url": "https://status.solana.com/history.atom",
        "label": "Network status history",
        "publisher": "status.solana.com (official status page)",
        "why": "The operator's own incident record. Entries are historical: an old "
               "newest-entry date means no incident has been posted since then, "
               "which is information in itself.",
    },
}


# ── network boundary ─────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 20) -> bytes | None:
    """GET a feed. Returns None on any failure — never raises, never retries."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml, application/rss+xml, application/xml",
            "User-Agent": "solana-ecosystem-report/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def fetch_all(timeout: int = 20) -> dict[str, bytes | None]:
    """Fetch every feed independently so one outage cannot take out the rest."""
    return {name: fetch(source["url"], timeout) for name, source in SOURCES.items()}


# ── pure transforms ──────────────────────────────────────────────────────────

def _text(node: Any) -> str:
    """Collapse an element's text to a single tidy line.

    GitHub commit titles arrive wrapped in newlines and indentation; a feed
    title pasted verbatim into a table breaks the row.
    """
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def parse_atom(body: bytes | None) -> list[dict[str, Any]] | None:
    """Atom feed → a list of entries. None means "this is not a usable feed".

    The None/[] distinction is load-bearing: None is a broken or unparseable
    source, [] is a feed that genuinely published nothing. They render
    differently, because they mean different things.
    """
    if not body:
        return None
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    if root.tag != f"{ATOM}feed":
        return None

    items: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        title = _text(entry.find(f"{ATOM}title"))
        if not title:
            continue
        if len(title) > MAX_TITLE:
            title = title[:MAX_TITLE - 1].rstrip() + "…"
        link_node = entry.find(f"{ATOM}link")
        link = link_node.get("href") if link_node is not None else None
        items.append({
            "title": title,
            "link": link if isinstance(link, str) and link.startswith("https://") else None,
            "published": _text(entry.find(f"{ATOM}updated")) or None,
            "author": _text(entry.find(f"{ATOM}author/{ATOM}name")) or None,
        })

    # Newest first. Timestamps are ISO-8601 Zulu here, so a string sort is a
    # chronological sort; entries without one sort last rather than vanishing.
    items.sort(key=lambda item: item["published"] or "", reverse=True)
    return items[:MAX_ITEMS]


def summarize_source(name: str, body: bytes | None) -> dict[str, Any]:
    """One feed's section. Every failure mode is a named state, not an empty list."""
    source = SOURCES[name]
    base = {
        "label": source["label"],
        "publisher": source["publisher"],
        "why": source["why"],
        "url": source["url"],
        "requires_api_key": False,
    }
    items = parse_atom(body)
    if items is None:
        return {**base, "available": False,
                "reason": "feed unreachable or not a parseable Atom document"}
    if not items:
        return {**base, "available": True, "items": [], "item_count": 0,
                "reason": "the feed parsed and published no entries"}
    return {**base, "available": True, "items": items, "item_count": len(items),
            "latest_published": items[0]["published"]}


def build_news(raw: dict[str, bytes | None]) -> dict[str, Any]:
    """Assemble the news section. Pure — no network, no clock."""
    sources = {name: summarize_source(name, raw.get(name)) for name in SOURCES}
    return {
        # True if any feed produced a usable answer, the same rule economics uses.
        "available": any(source["available"] for source in sources.values()),
        "requires_api_key": False,
        "sources": sources,
        "note": (
            "Official first-party feeds, fetched without credentials and recorded "
            "into this snapshot, so the section re-renders offline unchanged. Feed "
            "contents are third-party statements reproduced verbatim, not claims "
            "made by this report."
        ),
    }


def collect_news(timeout: int = 20) -> dict[str, Any]:
    return build_news(fetch_all(timeout))


if __name__ == "__main__":
    print(json.dumps(collect_news(), indent=2))
