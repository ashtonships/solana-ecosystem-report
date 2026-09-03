#!/usr/bin/env python3
"""Ecosystem releases and status, from keyless public sources.

Standard library only — `urllib` and small metadata parsers. No API key,
account, token, or third-party feed reader:

    Agave releases    api.github.com/repos/anza-xyz/agave/releases
    Network status    status.solana.com/api/v2/summary.json
                      status.solana.com/api/v2/incidents.json
                      status.solana.com/history.atom

Same architecture as `economics.py`, for the same reasons:

**Network access is confined to `fetch` and `fetch_json`.** Every transform is
pure, so the parsers are tested offline against fixtures and synthetic source
responses rather than the live network.

**Each source degrades alone.** A timeout, an HTTP error, or a body that does
not match its declared format all resolve to
`{"available": False}` for that source and leave the others untouched. A
source that fails renders as *unavailable* — never as "no releases", which
would be a claim about the ecosystem rather than about the fetch.

**Items are recorded into the snapshot.** Rendering never re-fetches, so a
committed snapshot re-renders to the same page offline, months later.

Solana News and curated upgrade transforms remain available for future use,
but their sections are held: production collection does not request the large
repository archive until its transport and public-reuse acceptance are safe.
The unlicensed proposal corpus remains unfetched.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request

import xnews
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ElementTree

import upgrades
import transport

ATOM = "{http://www.w3.org/2005/Atom}"

# Recent items kept per source. Agave may retain one additional latest-stable
# row when a prerelease burst would otherwise hide it.
MAX_ITEMS = 5

# Titles longer than this are cut — a commit subject can run to a paragraph.
MAX_TITLE = 140

# Rights-safe keyless additions. Firedancer publishes Apache-2.0 release
# metadata through the same GitHub transport already used for Agave. The SIMD
# repository publishes proposal documents without a license file, so this
# source records frontmatter metadata (title, status, canonical link) only —
# never document text. Lifecycle state comes from proposal frontmatter, never
# from a commit, PR, or merge event.
FIREDANCER_RELEASES_URL = (
    "https://api.github.com/repos/firedancer-io/firedancer/releases?per_page=5"
)
FIREDANCER_REPOSITORY = "https://github.com/firedancer-io/firedancer"
FIREDANCER_LICENSE = "Apache-2.0"
SIMD_REPO_RAW_BASE = (
    "https://raw.githubusercontent.com/solana-foundation/"
    "solana-improvement-documents/main/proposals"
)
SIMD_PROPOSAL_WATCH = {
    "0326-alpenglow": "https://github.com/solana-foundation/"
                      "solana-improvement-documents/blob/main/proposals/0326-alpenglow.md",
    "0525-reduce-slot-times": "https://github.com/solana-foundation/"
                              "solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md",
}

EDITORIAL_CATEGORIES = frozenset({"release", "network", "governance", "event", "ecosystem"})
EDITORIAL_ITEM_LIMIT = 24
STATUS_PUBLIC_URL = "https://status.solana.com/"
EDITORIAL_SOURCE_CONFIG = {
    "agave_releases": {
        "category": "release",
        "publisher": "Anza",
        "note": "Validator client release recorded from the official Agave repository.",
    },
    "firedancer_releases": {
        "category": "release",
        "publisher": "Firedancer Contributors",
        "note": "Independent validator client release recorded from the official "
                "Firedancer repository (Apache-2.0).",
    },
    "x_announcements": {
        "category": "ecosystem",
        "publisher": "Official ecosystem accounts on X",
        "note": "Post recorded from an allowlisted official ecosystem account on X. "
                "Linked text is third-party content; engagement counts are "
                "provider-reported at collection time.",
    },
    "network_status": {
        "category": "network",
        "publisher": "Solana Status",
        "note": "Network update recorded from Solana's official status history.",
    },
}

# Publisher artwork is deliberately not fetched or hotlinked. Automated image
# collection needs a per-source rights decision, host allowlist, byte/type cap,
# content hash, attribution record, and a tested fallback before it can replace
# the pinned generated presentation art embedded by render.py.

SOURCES: dict[str, dict[str, str]] = {
    "agave_releases": {
        "url": "https://api.github.com/repos/anza-xyz/agave/releases",
        "format": "github_releases",
        "label": "Agave validator releases",
        "publisher": "anza-xyz/agave (official GitHub repository)",
        "why": "Agave is the validator client most of the network runs. A release "
               "here is the software operators are about to be asked to run.",
    },
    "solana_news": {
        "url": upgrades.SOLANA_COM_HEAD_URL,
        "format": "solana_content_posts",
        "label": "Solana News",
        "publisher": "Solana Foundation / solana-foundation/solana-com",
        "why": "Future-gated first-party post metadata and canonical links from a "
               "pinned, exactly licence-verified repository archive.",
    },
    "simd_proposals": {
        "url": upgrades.SOLANA_COM_HEAD_URL,
        "format": "solana_content_upgrades",
        "label": "Solana upgrade metadata (held)",
        "publisher": "Solana Foundation / solana-foundation/solana-com",
        "why": "Future-gated editorial upgrade metadata from a pinned, exactly "
               "licence-verified archive. No lifecycle records are active while this "
               "source is held; linked SIMDs remain references only.",
    },
    "network_status": {
        "url": "https://status.solana.com/history.atom",
        "format": "feed",
        "label": "Network status history",
        "publisher": "status.solana.com (official status page)",
        "why": "The operator's own incident record. Entries are historical: an old "
               "newest-entry date means no incident has been posted since then, "
               "which is information in itself.",
    },
    "firedancer_releases": {
        "url": FIREDANCER_RELEASES_URL,
        "format": "github_releases",
        "label": "Firedancer releases",
        "publisher": "firedancer-io/firedancer (official GitHub repository, Apache-2.0)",
        "why": "Firedancer is the independent validator client in staged rollout. "
               "A release here records how far the second client has come.",
    },
    "simd_proposal_metadata": {
        "url": SIMD_REPO_RAW_BASE,
        "format": "simd_metadata",
        "label": "SIMD upgrade watch (metadata only)",
        "publisher": "solana-foundation/solana-improvement-documents (official)",
        "why": "Lifecycle state for watched upgrades comes from proposal frontmatter "
               "in the official SIMD repository. Document text is not republished.",
    },
    "x_announcements": {
        "url": "https://x.com/SolanaFndn",
        "format": "x_timeline",
        "label": "Official X announcements",
        "publisher": "Allowlisted official ecosystem accounts on X",
        "why": "First-party announcements from the official Solana, Anza, and "
               "Firedancer accounts. Pay-per-use transport, capped per run; "
               "posts are recorded with attribution and canonical links. No "
               "aggregate sentiment score is computed.",
    },
}

CURRENT_STATUS_URLS = {
    "summary": "https://status.solana.com/api/v2/summary.json",
    "incidents": "https://status.solana.com/api/v2/incidents.json",
}

AGAVE_REPO_API = "https://api.github.com/repos/anza-xyz/agave"
HELD_LICENSED_CONTENT_SOURCES = frozenset({"solana_news", "simd_proposals"})
HELD_RELEASE_REASON = (
    "not collected: licensed metadata transport is not release-safe; "
    "full-repository archive retrieval is disabled and public reuse remains "
    "source-rights acceptance-gated"
)


# ── network boundary ─────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 20) -> bytes | None:
    """GET one source. Returns None on any failure — never raises, never retries."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json, application/atom+xml, application/rss+xml, application/gzip",
            "User-Agent": "solana-ecosystem-report/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return transport.read_bounded(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def fetch_release_sources(timeout: int = 20) -> dict[str, Any]:
    """Fetch only sources currently cleared for the public report."""
    return {
        "agave_releases": fetch_agave_releases(timeout),
        "firedancer_releases": fetch_firedancer_releases(timeout),
        "simd_proposal_metadata": fetch_simd_proposal_metadata(timeout),
        "x_announcements": xnews.collect_x_announcements(timeout),
        "network_status": fetch(SOURCES["network_status"]["url"], timeout),
    }


def fetch_json(url: str, timeout: int = 20) -> Any | None:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "solana-ecosystem-report/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(transport.read_bounded(response).decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError):
        return None


def resolve_agave_tag_commit(tag: str, timeout: int = 20) -> str | None:
    if not isinstance(tag, str) or not tag:
        return None
    payload = fetch_json(f"{AGAVE_REPO_API}/git/ref/tags/{quote(tag, safe='')}", timeout)
    target = payload.get("object") if isinstance(payload, dict) else None
    seen = set()
    while isinstance(target, dict):
        kind, sha = target.get("type"), target.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or sha in seen:
            return None
        if kind == "commit":
            return sha
        if kind != "tag":
            return None
        seen.add(sha)
        payload = fetch_json(f"{AGAVE_REPO_API}/git/tags/{sha}", timeout)
        target = payload.get("object") if isinstance(payload, dict) else None
    return None


def fetch_agave_releases(timeout: int = 20) -> dict[str, Any]:
    body = fetch(SOURCES["agave_releases"]["url"], timeout)
    items = parse_agave_releases(body)
    commits = ({item["tag"]: resolve_agave_tag_commit(item["tag"], timeout)
                for item in items} if items is not None else {})
    return {"releases": body, "tag_commits": commits}


def fetch_firedancer_releases(timeout: int = 20) -> bytes | None:
    return fetch(FIREDANCER_RELEASES_URL, timeout)


def fetch_simd_proposal_metadata(timeout: int = 20) -> dict[str, bytes | None]:
    """Fetch only the watched proposals' raw documents, for frontmatter metadata."""
    return {
        slug: fetch(f"{SIMD_REPO_RAW_BASE}/{slug}.md", timeout)
        for slug in sorted(SIMD_PROPOSAL_WATCH)
    }


_SIMD_FRONTMATTER_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_SIMD_FRONTMATTER_STATUS = re.compile(r"^status:\s*(.+?)\s*$", re.MULTILINE)


def parse_firedancer_releases(body: bytes | None) -> list[dict[str, Any]] | None:
    """Same release-item shape as Agave, minus Agave-specific tag resolution.

    tag_commit_sha is deliberately absent: this source does not dereference
    tags, so no null placeholder is emitted.
    """
    parsed = _parse_agave_releases(body)
    if parsed[0] is None:
        return None
    items = []
    for item in parsed[0]:
        items.append({
            "id": item["id"],
            "title": item["title"],
            "link": item["link"],
            "published": item["published"],
            "author": item.get("author"),
            "tag": item["tag"],
            "draft": item["draft"],
            "prerelease": item["prerelease"],
            "stable": item["stable"],
            "release_channel": item["release_channel"],
        })
    return items


def parse_simd_proposal_metadata(documents: dict[str, bytes | None]) -> list[dict[str, Any]]:
    """Frontmatter metadata for watched SIMDs. No document text is retained."""
    items: list[dict[str, Any]] = []
    for slug in sorted(SIMD_PROPOSAL_WATCH):
        document = documents.get(slug)
        link = SIMD_PROPOSAL_WATCH[slug]
        if not document:
            items.append({
                "id": f"simd-watch:{slug}",
                "identifier": slug,
                "title": None,
                "status": None,
                "link": link,
                "published": None,
                "available": False,
                "reason": "proposal document unreachable or empty",
            })
            continue
        try:
            text = document.decode("utf-8")
        except UnicodeDecodeError:
            items.append({
                "id": f"simd-watch:{slug}", "identifier": slug, "title": None,
                "status": None, "link": link, "published": None,
                "available": False, "reason": "proposal document is not valid UTF-8",
            })
            continue
        title_match = _SIMD_FRONTMATTER_TITLE.search(text)
        status_match = _SIMD_FRONTMATTER_STATUS.search(text)
        title = title_match.group(1).strip() if title_match else None
        status = status_match.group(1).strip() if status_match else None
        if not title or not status:
            items.append({
                "id": f"simd-watch:{slug}", "identifier": slug, "title": title,
                "status": status, "link": link, "published": None,
                "available": False,
                "reason": "proposal frontmatter is missing title or status",
            })
            continue
        items.append({
            "id": f"simd-watch:{slug}",
            "identifier": slug,
            "title": _title(title),
            "status": status,
            "link": link,
            "published": None,
            "available": True,
        })
    return items


def summarize_current_status(summary: Any, incidents: Any,
                            observed_at_unix: int | None = None) -> dict[str, Any]:
    status = summary.get("status") if isinstance(summary, dict) else None
    summary_page = summary.get("page") if isinstance(summary, dict) else None
    incidents_page = incidents.get("page") if isinstance(incidents, dict) else None
    indicator = status.get("indicator") if isinstance(status, dict) else None
    description = status.get("description") if isinstance(status, dict) else None
    incident_rows = incidents.get("incidents") if isinstance(incidents, dict) else None
    incident_response_available = isinstance(incident_rows, list)
    history = []
    invalid_incident_count = 0
    valid_statuses = {"investigating", "identified", "monitoring", "resolved", "postmortem"}
    for incident in incident_rows if incident_response_available else []:
        if not isinstance(incident, dict):
            invalid_incident_count += 1
            continue
        identity = incident.get("id") or incident.get("shortlink")
        incident_status = incident.get("status")
        created_at = incident.get("created_at") if _is_timestamp(incident.get("created_at")) else None
        updated_at = incident.get("updated_at") if _is_timestamp(incident.get("updated_at")) else None
        if (not isinstance(identity, (str, int)) or isinstance(identity, bool)
                or incident_status not in valid_statuses or not (created_at or updated_at)):
            invalid_incident_count += 1
            continue
        row = {
            "id": str(identity),
            "name": incident.get("name") if isinstance(incident.get("name"), str) else "Unnamed incident",
            "status": incident_status,
            "impact": incident.get("impact") if isinstance(incident.get("impact"), str) else None,
            "url": _safe_url(incident.get("shortlink")),
            "created_at": created_at,
            "updated_at": updated_at,
            "monitoring_at": incident.get("monitoring_at") if _is_timestamp(incident.get("monitoring_at")) else None,
            "resolved_at": incident.get("resolved_at") if _is_timestamp(incident.get("resolved_at")) else None,
        }
        history.append(row)
    history.sort(key=lambda row: row["updated_at"] or row["created_at"] or "", reverse=True)
    unique_history = []
    seen = set()
    for row in history:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique_history.append(row)
    history = unique_history
    active = [row for row in history if row["status"] in {"investigating", "identified", "monitoring"}]
    status_available = isinstance(indicator, str) and isinstance(description, str)
    incidents_available = incident_response_available and invalid_incident_count == 0
    return {
        "available": status_available and incidents_available,
        "partial": not (status_available and incidents_available)
                   and (status_available or incident_response_available),
        "status_available": status_available,
        "incidents_available": incidents_available,
        "incident_response_available": incident_response_available,
        "invalid_incident_count": invalid_incident_count,
        "indicator": indicator if status_available else None,
        "description": description if status_available else None,
        "incidents": active,
        "active_incident_count": len(active) if incidents_available else None,
        "incident_history": history,
        "history_is_freshness_signal": False,
        "incident_history_note": (
            "Incident dates describe incident history; an old newest incident is not a stale status response."
        ),
        "observed_at_unix": observed_at_unix,
        "summary_source_updated_at": (
            summary_page.get("updated_at")
            if isinstance(summary_page, dict) and _is_timestamp(summary_page.get("updated_at"))
            else None
        ),
        "incidents_source_updated_at": (
            incidents_page.get("updated_at")
            if isinstance(incidents_page, dict) and _is_timestamp(incidents_page.get("updated_at"))
            else None
        ),
        "sources": CURRENT_STATUS_URLS.copy(),
        "requires_api_key": False,
    }


# ── pure transforms ──────────────────────────────────────────────────────────

def _text(node: Any) -> str:
    """Collapse an element's text to a single tidy line.

    GitHub commit titles arrive wrapped in newlines and indentation; a feed
    title pasted verbatim into a table breaks the row.
    """
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def _safe_url(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _editorial_safe_url(source_id: str, value: Any) -> str | None:
    """Keep normalized story links on the accepted source's canonical hosts."""
    safe = _safe_url(value)
    if safe is None:
        return None
    try:
        parsed = urlsplit(safe)
        port = parsed.port
    except ValueError:
        return None
    if port not in {None, 443} or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if source_id == "agave_releases":
        return safe if host == "github.com" and parsed.path.startswith("/anza-xyz/agave/releases/") else None
    if source_id == "firedancer_releases":
        return safe if host == "github.com" and parsed.path.startswith("/firedancer-io/firedancer/releases/") else None
    if source_id == "x_announcements":
        return safe if host == "x.com" and re.fullmatch(
            r"/[A-Za-z0-9_]{1,15}/status/[0-9]+", parsed.path) else None
    if source_id == "network_status":
        return safe if host in {"status.solana.com", "stspg.io"} else None
    return None


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _title(value: str) -> str:
    value = " ".join(value.split())
    return value if len(value) <= MAX_TITLE else value[:MAX_TITLE - 1].rstrip() + "…"


def _identity(explicit: str, link: str | None, title: str, published: str | None) -> str:
    if explicit:
        return explicit
    if link:
        return link
    payload = f"{title}\0{published or ''}".encode()
    return f"feed:{hashlib.sha256(payload).hexdigest()}"


def _dedupe(items: list[dict[str, Any]], limit: int | None = MAX_ITEMS) -> list[dict[str, Any]]:
    items.sort(key=lambda item: item["published"] or "", reverse=True)
    unique = []
    seen = set()
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique if limit is None else unique[:limit]


def _rss_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_feed(body: bytes | None) -> tuple[list[dict[str, Any]] | None, int]:
    """Atom/RSS feed → metadata entries plus rejected-row count.

    The None/[] distinction is load-bearing: None is a broken or unparseable
    source, [] is a feed that genuinely published nothing. They render
    differently, because they mean different things.
    """
    if not body:
        return None, 0
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None, 0
    items: list[dict[str, Any]] = []
    invalid_count = 0
    if root.tag == f"{ATOM}feed":
        entries = root.findall(f"{ATOM}entry")
        for entry in entries:
            title = _title(_text(entry.find(f"{ATOM}title")))
            if not title:
                invalid_count += 1
                continue
            links = entry.findall(f"{ATOM}link")
            link_node = next((node for node in links if node.get("rel") in (None, "alternate")), None)
            link = _safe_url(link_node.get("href") if link_node is not None else None)
            published = (_text(entry.find(f"{ATOM}published"))
                         or _text(entry.find(f"{ATOM}updated")) or None)
            if not link or not _is_timestamp(published):
                invalid_count += 1
                continue
            identity = _identity(_text(entry.find(f"{ATOM}id")), link, title, published)
            items.append({"id": identity, "title": title, "link": link,
                          "published": published,
                          "author": _text(entry.find(f"{ATOM}author/{ATOM}name")) or None})
    elif root.tag == "rss" and root.find("channel") is not None:
        entries = root.findall("channel/item")
        for entry in entries:
            title = _title(_text(entry.find("title")))
            if not title:
                invalid_count += 1
                continue
            link = _safe_url(_text(entry.find("link")))
            published = _rss_date(_text(entry.find("pubDate")))
            if not link or not published:
                invalid_count += 1
                continue
            identity = _identity(_text(entry.find("guid")), link, title, published)
            items.append({"id": identity, "title": title, "link": link,
                          "published": published, "author": None})
    else:
        return None, 0
    if entries and not items:
        return None, invalid_count
    return _dedupe(items), invalid_count


def parse_feed(body: bytes | None) -> list[dict[str, Any]] | None:
    """Backward-compatible item-only view of the feed parser."""
    return _parse_feed(body)[0]


def parse_atom(body: bytes | None) -> list[dict[str, Any]] | None:
    """Backward-compatible name for the feed parser."""
    return parse_feed(body)


_PRERELEASE_SUFFIX = re.compile(r"(?:alpha|beta|rc)(?:[-._]?\d+)*$", re.IGNORECASE)


def _parse_agave_releases(
    body: bytes | None,
    tag_commits: dict[str, str | None] | None = None,
) -> tuple[list[dict[str, Any]] | None, int]:
    if not body:
        return None, 0
    try:
        releases = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, 0
    if not isinstance(releases, list):
        return None, 0
    items = []
    invalid_count = 0
    for release in releases:
        if not isinstance(release, dict):
            invalid_count += 1
            continue
        tag = release.get("tag_name")
        release_id = release.get("id")
        if (not isinstance(tag, str) or not tag or not isinstance(release_id, (str, int))
                or isinstance(release_id, bool)):
            invalid_count += 1
            continue
        title = _title(release.get("name")) if isinstance(release.get("name"), str) else ""
        title = title or tag
        link = _safe_url(release.get("html_url"))
        published = release.get("published_at") if _is_timestamp(release.get("published_at")) else None
        if not link or not published:
            invalid_count += 1
            continue
        prerelease_tag = _PRERELEASE_SUFFIX.search(tag.split("+", 1)[0]) is not None
        stable = (release.get("draft") is False and release.get("prerelease") is False
                  and not prerelease_tag)
        if release.get("draft") is True:
            channel = "draft"
        elif release.get("prerelease") is True or prerelease_tag:
            channel = "prerelease"
        else:
            channel = "stable" if stable else "unknown"
        author = release.get("author")
        tag_commit = tag_commits.get(tag) if isinstance(tag_commits, dict) else None
        if not isinstance(tag_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", tag_commit):
            tag_commit = None
        items.append({
            "id": f"github-release:{release_id}",
            "title": title,
            "link": link,
            "published": published,
            "author": author.get("login") if isinstance(author, dict) and isinstance(author.get("login"), str) else None,
            "tag": tag,
            "draft": release.get("draft") is True,
            "prerelease": release.get("prerelease") is True,
            "stable": stable,
            "release_channel": channel,
            "tag_commit_sha": tag_commit,
        })
    unique = _dedupe(items, limit=None)
    recent = unique[:MAX_ITEMS]
    latest_stable = next((item for item in unique if item["stable"]), None)
    if latest_stable is not None and latest_stable not in recent:
        recent.append(latest_stable)
    return (None, invalid_count) if releases and not items else (recent, invalid_count)


def parse_agave_releases(body: bytes | None,
                         tag_commits: dict[str, str | None] | None = None) -> list[dict[str, Any]] | None:
    """Backward-compatible item-only view of Agave release metadata."""
    return _parse_agave_releases(body, tag_commits)[0]


def source_metadata(name: str) -> dict[str, Any]:
    source = SOURCES[name]
    return {
        "label": source["label"],
        "publisher": source["publisher"],
        "why": source["why"],
        "url": source["url"],
        "requires_api_key": False,
    }


def normalize_editorial_items(
    sources: dict[str, Any], current_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project source lanes into a small, stable editorial presentation contract."""
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_links: set[str] = set()
    for source_id, config in EDITORIAL_SOURCE_CONFIG.items():
        source = sources.get(source_id)
        if not isinstance(source, dict) or source.get("available") is not True:
            continue
        source_items = source.get("items")
        if not isinstance(source_items, list):
            continue
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            if source_id == "agave_releases" and source_item.get("draft") is True:
                continue
            if source_id == "x_announcements":
                # X posts have no title; the post excerpt carries the story.
                # Enforce the excerpt shape here so the editorial item keeps
                # a non-empty title derived from the recorded text.
                text = source_item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                excerpt = " ".join(text.strip().split())
                title = excerpt if len(excerpt) <= MAX_TITLE else excerpt[:MAX_TITLE - 1].rstrip() + "…"
            else:
                title = source_item.get("title")
            item_id = source_item.get("id")
            link = _editorial_safe_url(source_id, source_item.get("link"))
            published = source_item.get("published")
            if (not isinstance(item_id, str) or not item_id
                    or not isinstance(title, str) or not title.strip()
                    or link is None or item_id in seen_ids or link in seen_links):
                continue
            if published is not None and not _is_timestamp(published):
                published = None
            seen_ids.add(item_id)
            seen_links.add(link)
            note = config["note"]
            if source_id == "agave_releases" and source_item.get("release_channel") == "prerelease":
                note = "Prerelease validator client build recorded from the official Agave repository."
            items.append({
                "id": item_id,
                "source_id": source_id,
                "publisher": config["publisher"],
                "category": config["category"],
                "title": title,
                "canonical_url": link,
                "published_at": published,
                "recorded_at": None,
                "state": "recorded",
                "editorial_note": note,
                "art_seed": item_id,
                "_sort_at": published or "",
            })

    if isinstance(current_status, dict) and current_status.get("status_available") is True:
        observed_at = current_status.get("observed_at_unix")
        description = current_status.get("description")
        if (isinstance(observed_at, int) and not isinstance(observed_at, bool)
                and observed_at >= 0 and isinstance(description, str) and description.strip()):
            observed = datetime.fromtimestamp(observed_at, timezone.utc).isoformat()
            active_incidents = current_status.get("active_incident_count")
            note = (
                f"Official status response recorded with {active_incidents} active incident(s)."
                if isinstance(active_incidents, int) and not isinstance(active_incidents, bool)
                else "Official status response recorded with this snapshot."
            )
            item_id = f"status-summary:{observed_at}"
            items.append({
                "id": item_id,
                "source_id": "network_status",
                "publisher": "Solana Status",
                "category": "network",
                "title": (
                    "Network status remains operational"
                    if current_status.get("indicator") == "none"
                    else f"Network status: {description.strip()}"
                ),
                "canonical_url": STATUS_PUBLIC_URL,
                "published_at": None,
                "recorded_at": None,
                "state": "recorded",
                "editorial_note": note,
                "art_seed": item_id,
                "_sort_at": observed,
            })

    items.sort(key=lambda item: (item["_sort_at"], item["id"]), reverse=True)
    for item in items:
        item.pop("_sort_at", None)
    return items[:EDITORIAL_ITEM_LIMIT]


def featured_editorial_item_id(items: list[dict[str, Any]]) -> str | None:
    """Choose a published development, not the recurring status-summary card."""
    featured = next(
        (item for item in items if item.get("published_at") is not None),
        items[0] if items else None,
    )
    item_id = featured.get("id") if isinstance(featured, dict) else None
    return item_id if isinstance(item_id, str) else None


def summarize_source(name: str, body: Any) -> dict[str, Any]:
    """One feed's section. Every failure mode is a named state, not an empty list."""
    source = SOURCES[name]
    base = source_metadata(name)
    if source.get("format") == "github_releases":
        if name == "firedancer_releases":
            # Firedancer uses its own item shape: no tag-commit resolution,
            # no provenance counts. Unavailable propagates cleanly.
            items = parse_firedancer_releases(
                body.get("releases") if isinstance(body, dict) else body,
            )
            if items is None:
                return {**base, "available": False,
                        "reason": "source unreachable or not parseable in its declared format"}
            if not items:
                return {**base, "available": True, "items": [], "item_count": 0,
                        "reason": "the feed parsed and published no entries"}
            return {**base, "available": True, "items": items,
                    "item_count": len(items), "partial": False,
                    "invalid_item_count": 0,
                    "latest_published": items[0]["published"]}
        release_body = body.get("releases") if isinstance(body, dict) else body
        tag_commits = body.get("tag_commits") if isinstance(body, dict) else None
        items, invalid_count = _parse_agave_releases(release_body, tag_commits)
        if items is not None:
            covered = sum(item["tag_commit_sha"] is not None for item in items)
            base.update({
                "latest_stable": next((item for item in items if item["stable"]), None),
                "tag_commit_covered_count": covered,
                "tag_commit_missing_count": len(items) - covered,
                "invalid_item_count": invalid_count,
                "partial": covered != len(items) or invalid_count > 0,
            })
    elif source.get("format") == "simd_metadata":
        # fetch_simd_proposal_metadata returns {slug: bytes} keyed by proposal
        # slug; summarize consumes exactly that mapping.
        rows = body if isinstance(body, dict) else None
        if not isinstance(rows, dict):
            return {**base, "available": False,
                    "reason": "SIMD proposal metadata response is invalid",
                    "items": [], "item_count": 0}
        parsed = parse_simd_proposal_metadata(rows)
        # Pipeline contract: an available source's items each need a non-empty
        # title, an HTTPS link, and (when present) an offset-aware timestamp.
        # Watch rows that lack readable frontmatter degrade to the source's
        # reason instead of publishing contract-violating items.
        items = [item for item in parsed
                 if item.get("available") is True
                 and isinstance(item.get("title"), str) and item["title"].strip()
                 and isinstance(item.get("link"), str) and item["link"].startswith("https://")]
        return {
            **base,
            "available": bool(items),
            "reason": (
                None if items
                else "no watched proposal produced readable frontmatter metadata"
            ),
            "items": items,
            "item_count": len(items),
            "watched_proposal_count": len(SIMD_PROPOSAL_WATCH),
            "metadata_item_count": len(items),
            "partial": False,
            "basis": "proposal frontmatter metadata only; document text is not republished",
        }
    elif source.get("format") == "x_timeline":
        rows = body.get("posts") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return {**base, "available": False,
                    "reason": (body.get("reason") if isinstance(body, dict)
                               and isinstance(body.get("reason"), str)
                               else "X announcements response is invalid"),
                    "items": [], "item_count": 0}
        items = xnews.parse_announcements(rows)
        return {
            **base,
            "available": bool(items),
            "reason": (
                None if items
                else "no allowlisted posts in the lookback window"
            ),
            "items": items,
            "item_count": len(items),
            "account_allowlist": list(xnews.X_ACCOUNT_ALLOWLIST),
            "max_posts_per_run": xnews.MAX_POSTS,
            "partial": False,
            "invalid_item_count": 0,
            "basis": "provider-reported post metadata; no sentiment score computed",
        }
    elif source.get("format") in {"solana_content_posts", "solana_content_upgrades"}:
        if not isinstance(body, dict) or body.get("available") is not True:
            reason = body.get("reason") if isinstance(body, dict) else None
            return {**base, **(body if isinstance(body, dict) else {}),
                    "available": False,
                    "reason": reason or "pinned GPL-3.0 repository archive unavailable or invalid"}
        records = body.get("records")
        if not isinstance(records, list):
            return {**base, "available": False,
                    "reason": "pinned GPL-3.0 repository archive metadata is invalid"}
        items = records[:MAX_ITEMS]
        return {
            **base,
            **body,
            "url": body.get("source") or base["url"],
            "items": items,
            "item_count": len(items),
            "latest_published": items[0]["published"] if items else None,
        }
    else:
        items, invalid_count = _parse_feed(body)
        base.update({"invalid_item_count": invalid_count,
                     "partial": invalid_count > 0})
    if items is None:
        return {**base, "available": False,
                "reason": "source unreachable or not parseable in its declared format"}
    if not items:
        return {**base, "available": True, "items": [], "item_count": 0,
                "reason": "the feed parsed and published no entries"}
    return {**base, "available": True, "items": items, "item_count": len(items),
            "latest_published": items[0]["published"]}


def build_news(
    raw: dict[str, Any],
    current_status: dict[str, Any] | None = None,
    held_sources: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Assemble the news section. Pure — no network, no clock."""
    sources = {
        name: (
            {**source_metadata(name), "available": False, "reason": HELD_RELEASE_REASON}
            if name in held_sources else summarize_source(name, raw.get(name))
        )
        for name in SOURCES
    }
    current = current_status or {"available": False, "partial": False,
                                 "requires_api_key": False}
    available = (any(source["available"] for source in sources.values())
                 or current.get("available") is True or current.get("partial") is True)
    # `complete` follows the pipeline's four-source contract. Optional sources
    # degrade visibly per-source (freshness rows, section states) but must
    # never flip the section-level partial flag, because that would turn an
    # optional-source outage into a publication-gate failure.
    core_sources = ("agave_releases", "solana_news", "simd_proposals", "network_status")
    complete = (all(sources[name]["available"] and not sources[name].get("partial")
                    for name in core_sources)
                and current.get("available") is True and current.get("partial") is not True)
    editorial_items = normalize_editorial_items(sources, current)
    return {
        "available": available,
        "partial": available and not complete,
        "requires_api_key": False,
        "featured_item_id": featured_editorial_item_id(editorial_items),
        "items": editorial_items,
        "sources": sources,
        "current_status": current,
        "note": (
            "Accepted first-party sources are fetched without credentials and recorded "
            "into this snapshot, so the section re-renders offline unchanged. Licensed "
            "Solana post and curated-upgrade transforms remain future-gated: held sources "
            "are not requested until a release-safe transport and public-reuse acceptance "
            "exist. Only source metadata and links are retained; source statements are not "
            "claims made by this report."
        ),
    }


def collect_news(timeout: int = 20) -> dict[str, Any]:
    current = summarize_current_status(
        fetch_json(CURRENT_STATUS_URLS["summary"], timeout),
        fetch_json(CURRENT_STATUS_URLS["incidents"], timeout),
        int(time.time()),
    )
    return build_news(
        fetch_release_sources(timeout), current, HELD_LICENSED_CONTENT_SOURCES,
    )


if __name__ == "__main__":
    print(json.dumps(collect_news(), indent=2))
