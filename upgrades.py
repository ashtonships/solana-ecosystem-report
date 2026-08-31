#!/usr/bin/env python3
"""Pure, future-gated transforms for one pinned licensed Solana archive.

This module performs no network access. Production collection currently holds
these sources until a release-safe transport and public-reuse acceptance exist.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

SOLANA_COM_SOURCE_COMMIT = "46091c373d7681a469e4130155187503def93387"
SOLANA_COM_HEAD_URL = (
    "https://api.github.com/repos/solana-foundation/solana-com/commits/main"
)
SOLANA_COM_REPOSITORY = "https://github.com/solana-foundation/solana-com"
SOLANA_COM_ATTRIBUTION = "Solana Foundation / solana-foundation/solana-com"
SOLANA_COM_LICENSE = "GPL-3.0"
SOLANA_COM_LICENSE_SHA256 = (
    "f817886ee6bb65ed3098a7987b1e9781653d15c71f85589ce0d2af663c66d373"
)
POSTS_PREFIX = "apps/media/content/posts/"
UPGRADES_PREFIX = "apps/media/content/upgrades/"
UPGRADE_STAGES = frozenset({"planned", "in_development", "pending_activation", "live"})
MAX_CONTENT_FILE_BYTES = 2_000_000

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
_SIMD_LINK = re.compile(
    r"\[[^\]]*?\bSIMD-(?P<number>\d{1,4})\b[^\]]*\]"
    r"\((?P<link>https://github\.com/solana-foundation/"
    r"solana-improvement-documents/[^)\s]+)\)",
    re.IGNORECASE,
)


def solana_com_archive_url(source_commit: str) -> str:
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("solana-com source commit must be a full lowercase SHA")
    return f"https://codeload.github.com/solana-foundation/solana-com/tar.gz/{source_commit}"


def _frontmatter(document: str) -> dict[str, Any] | None:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], 1)
                   if line.strip() == "---")
    except StopIteration:
        return None

    metadata: dict[str, Any] = {}
    list_key: str | None = None
    for line in lines[1:end]:
        if line.startswith((" ", "\t")):
            item = line.strip()
            if list_key and item.startswith("- "):
                metadata[list_key].append(_scalar(item[2:]))
            continue
        if ":" not in line:
            list_key = None
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if not key:
            continue
        if key in metadata:
            raise ValueError(f"duplicate frontmatter key: {key}")
        if value:
            metadata[key] = _scalar(value)
            list_key = None
        else:
            metadata[key] = []
            list_key = key
    return metadata


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value.strip()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _timestamp_text(value: Any) -> str | None:
    parsed = _timestamp(value)
    return (parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
            if parsed is not None else None)


def _slug(path: str, prefix: str) -> str | None:
    if not path.startswith(prefix) or not path.endswith(".mdx"):
        return None
    relative = path[len(prefix):-4]
    return relative if "/" not in relative and _SLUG.fullmatch(relative) else None


def _license_url(source_commit: str) -> str:
    return f"{SOLANA_COM_REPOSITORY}/blob/{source_commit}/LICENSE"


def _source_url(path: str, source_commit: str) -> str:
    return f"{SOLANA_COM_REPOSITORY}/blob/{source_commit}/{quote(path, safe='/-_.')}"


def _provenance(path: str, source_commit: str) -> dict[str, Any]:
    return {
        "source_path": path,
        "source_commit": source_commit,
        "source": _source_url(path, source_commit),
        "attribution": SOLANA_COM_ATTRIBUTION,
        "license": SOLANA_COM_LICENSE,
        "license_url": _license_url(source_commit),
        "basis": "recorded",
    }


def _simd_references(document: str) -> list[dict[str, str]]:
    references: dict[str, str] = {}
    for match in _SIMD_LINK.finditer(document):
        number = match.group("number")
        link = match.group("link")
        linked_number = re.search(r"/(?:proposals|pull)/(\d{1,4})(?:[-/.]|$)", link)
        if linked_number is None or int(linked_number.group(1)) != int(number):
            raise ValueError(f"conflicting SIMD reference: {number} -> {link}")
        identifier = f"SIMD-{number.zfill(4)}"
        if identifier in references and references[identifier] != link:
            raise ValueError(f"conflicting SIMD links: {identifier}")
        references[identifier] = link
    return [{"identifier": identifier, "link": references[identifier]}
            for identifier in sorted(references)]


def _section(path_prefix: str, records: list[dict[str, Any]], document_count: int,
             excluded_count: int, unparsed_paths: list[str], source_commit: str,
             collected_at: str) -> dict[str, Any]:
    available = document_count > 0
    section = {
        "available": available,
        "partial": available and bool(unparsed_paths),
        "records": records,
        "record_count": len(records),
        "document_count": document_count,
        "excluded_count": excluded_count,
        "invalid_item_count": len(unparsed_paths),
        "coverage_complete": available and not unparsed_paths,
        "unparsed_paths": sorted(unparsed_paths),
        "source_commit": source_commit,
        "source": f"{SOLANA_COM_REPOSITORY}/tree/{source_commit}/{path_prefix.rstrip('/')}",
        "head_url": SOLANA_COM_HEAD_URL,
        "collected_at": collected_at,
        "attribution": SOLANA_COM_ATTRIBUTION,
        "license": SOLANA_COM_LICENSE,
        "license_url": _license_url(source_commit),
        "basis": "recorded",
    }
    if not available:
        section["reason"] = f"pinned archive contained no {path_prefix.rstrip('/')} documents"
    return section


def _build_catalog(documents: dict[str, str | None], source_commit: str,
                   collected_at: Any) -> dict[str, Any] | None:
    as_of = _timestamp(collected_at)
    collected_at_text = _timestamp_text(collected_at)
    if as_of is None or collected_at_text is None:
        return None

    post_records: list[dict[str, Any]] = []
    upgrade_records: list[dict[str, Any]] = []
    post_unparsed: list[str] = []
    upgrade_unparsed: list[str] = []
    post_documents = upgrade_documents = post_excluded = upgrade_excluded = 0
    post_ids: set[str] = set()
    upgrade_ids: set[str] = set()

    for path, document in sorted(documents.items()):
        prefix = POSTS_PREFIX if path.startswith(POSTS_PREFIX) else UPGRADES_PREFIX
        if prefix == POSTS_PREFIX:
            post_documents += 1
            unparsed, identities = post_unparsed, post_ids
        else:
            upgrade_documents += 1
            unparsed, identities = upgrade_unparsed, upgrade_ids
        if not isinstance(document, str):
            unparsed.append(path)
            continue
        slug = _slug(path, prefix)
        try:
            metadata = _frontmatter(document)
        except ValueError:
            unparsed.append(path)
            continue
        if slug is None or not metadata:
            unparsed.append(path)
            continue
        status = metadata.get("status")
        if status == "draft":
            if prefix == POSTS_PREFIX:
                post_excluded += 1
            else:
                upgrade_excluded += 1
            continue
        if status != "published":
            unparsed.append(path)
            continue
        published = _timestamp(metadata.get("publishedAt"))
        if published is None:
            unparsed.append(path)
            continue
        if published > as_of:
            if prefix == POSTS_PREFIX:
                post_excluded += 1
            else:
                upgrade_excluded += 1
            continue
        title, author_id = metadata.get("title"), metadata.get("author")
        if (not isinstance(title, str) or not title.strip()
                or not isinstance(author_id, str) or not author_id.strip()):
            unparsed.append(path)
            continue
        kind = "solana-news" if prefix == POSTS_PREFIX else "solana-upgrade"
        identity = f"{kind}:{slug}"
        if identity in identities:
            raise ValueError(f"duplicate content identity: {identity}")
        identities.add(identity)
        canonical = f"https://solana.com/{'news' if prefix == POSTS_PREFIX else 'upgrades'}/{slug}"
        record = {
            "id": identity,
            "slug": slug,
            "title": " ".join(title.split()),
            "author_id": author_id,
            "published": published.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status": "published",
            "link": canonical,
            **_provenance(path, source_commit),
        }
        if prefix == POSTS_PREFIX:
            post_records.append(record)
            continue
        stage, release = metadata.get("stage"), metadata.get("release")
        invalid_release = (
            release is not None
            and (not isinstance(release, str) or _SLUG.fullmatch(release) is None)
        )
        if stage not in UPGRADE_STAGES or invalid_release:
            identities.remove(identity)
            upgrade_unparsed.append(path)
            continue
        try:
            simd_references = _simd_references(document)
        except ValueError:
            identities.remove(identity)
            upgrade_unparsed.append(path)
            continue
        record.update({
            "stage": stage,
            "release_slug": release,
            "simd_references": simd_references,
        })
        upgrade_records.append(record)

    for records in (post_records, upgrade_records):
        records.sort(key=lambda row: (row["published"], row["id"]), reverse=True)
    posts = _section(POSTS_PREFIX, post_records, post_documents, post_excluded,
                     post_unparsed, source_commit, collected_at_text)
    upgrades = _section(UPGRADES_PREFIX, upgrade_records, upgrade_documents,
                        upgrade_excluded, upgrade_unparsed, source_commit,
                        collected_at_text)
    upgrades.update({
        "lifecycle_basis": "official curated upgrade lifecycle",
        "note": (
            "Official curated upgrade lifecycle recorded as editorial evidence; "
            "it is not proposal-frontmatter or on-chain activation proof. Linked "
            "SIMDs are references only and never inherit a page stage."
        ),
    })
    return {"posts": posts, "upgrades": upgrades}


def parse_solana_content_archive(body: bytes | None, source_commit: str,
                                 collected_at: Any) -> dict[str, Any] | None:
    """Verify one pinned GPL-3.0 archive and retain metadata-only content rows."""
    if not body:
        return None
    try:
        archive_url = solana_com_archive_url(source_commit)
        roots: set[str] = set()
        documents: dict[str, str | None] = {}
        license_verified = False
        seen_paths: set[str] = set()
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive:
                if not member.isfile() or "/" not in member.name:
                    continue
                root, path = member.name.split("/", 1)
                roots.add(root)
                if path != "LICENSE" and not path.startswith((POSTS_PREFIX, UPGRADES_PREFIX)):
                    continue
                if path in seen_paths:
                    raise ValueError(f"duplicate archive path: {path}")
                seen_paths.add(path)
                handle = archive.extractfile(member)
                if path == "LICENSE":
                    if handle is None or member.size > MAX_CONTENT_FILE_BYTES:
                        raise ValueError("archive license unavailable or oversized")
                    license_verified = (
                        hashlib.sha256(handle.read()).hexdigest()
                        == SOLANA_COM_LICENSE_SHA256
                    )
                elif path.endswith(".mdx"):
                    if handle is None or member.size > MAX_CONTENT_FILE_BYTES:
                        documents[path] = None
                        continue
                    try:
                        documents[path] = handle.read().decode("utf-8")
                    except UnicodeDecodeError:
                        documents[path] = None
        if (roots != {f"solana-com-{source_commit}"}
                or not license_verified):
            return None
        catalog = _build_catalog(documents, source_commit, collected_at)
        if catalog is None:
            return None
        return {
            "source_commit": source_commit,
            "source": archive_url,
            "head_url": SOLANA_COM_HEAD_URL,
            "attribution": SOLANA_COM_ATTRIBUTION,
            "license": SOLANA_COM_LICENSE,
            "license_url": _license_url(source_commit),
            **catalog,
        }
    except (OSError, tarfile.TarError, TypeError, ValueError):
        return None
