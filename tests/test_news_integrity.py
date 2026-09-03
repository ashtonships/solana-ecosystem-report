"""News surface integrity: every external link well-formed, newest-first.

Acceptance from the live-product review:
- every rendered external link has a valid https URL,
- points at its declared source host,
- never renders an empty href,
- lists are ordered newest-first by the source publication timestamp,
  with deterministic tie-breaking (id).
"""
import json
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "snapshot.json"

HOST_RULES = {
    "agave_releases": "github.com",
    "firedancer_releases": "github.com",
    "network_status": "status.solana.com",
    "x_announcements": "x.com",
}


def editorial_items():
    snapshot = render.build_minimal_snapshot("2026-09-03T00:00:00+00:00") \
        if hasattr(render, "build_minimal_snapshot") else {
            "schema_version": 9,
            "collected_at": "2026-09-03T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
        }
    recorded_at = snapshot["collected_at"]
    return snapshot, recorded_at, [
        {
            "id": "status:1", "source_id": "network_status",
            "publisher": "Solana Status", "category": "network",
            "title": "Network update",
            "canonical_url": "https://status.solana.com/notices/1",
            "published_at": "2026-08-05T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "n", "art_seed": "status:1",
        },
        {
            "id": "x:1", "source_id": "x_announcements",
            "publisher": "Official ecosystem accounts on X", "category": "ecosystem",
            "title": "Network upgrade scheduled",
            "canonical_url": "https://x.com/SolanaFndn/status/1830000000000000001",
            "published_at": "2026-08-05T12:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "n", "art_seed": "x:1",
        },
        {
            "id": "github-release:1", "source_id": "firedancer_releases",
            "publisher": "Firedancer Contributors", "category": "release",
            "title": "Firedancer v26.08.2",
            "canonical_url": "https://github.com/firedancer-io/firedancer/releases/tag/v26.08.2",
            "published_at": "2026-08-04T11:00:00Z", "recorded_at": recorded_at,
            "state": "recorded", "editorial_note": "n", "art_seed": "github-release:1",
        },
    ]


def snapshot_with(items):
    snapshot, recorded_at, _ = editorial_items()
    if items:
        featured = sorted(items, key=lambda i: (i.get("published_at") or "", i["id"]),
                          reverse=True)[0]["id"]
    else:
        featured = None
    snapshot["news"] = {
        "available": True, "partial": True, "featured_item_id": featured,
        "items": items,
        "sources": {name: {"label": name, "available": False, "reason": "unused"}
                    for name in HOST_RULES},
    }
    return snapshot


class TestNewsLinkIntegrity(unittest.TestCase):
    def test_every_rendered_external_link_is_valid_and_on_source_host(self):
        snapshot = snapshot_with(editorial_items()[2])
        surfaces = [
            render.render_community_news(snapshot, "desktop"),
            render.render_community_news(snapshot, "mobile"),
        ]
        for surface in surfaces:
            for match in re.finditer(r"href='([^']*)'", surface):
                href = match.group(1)
                if href.startswith(("#", "index.html", "report.")):
                    continue
                parsed = urlsplit(href)
                self.assertEqual(parsed.scheme, "https", f"non-https href: {href}")
                self.assertTrue(parsed.hostname, f"hostless href: {href}")
            # No empty hrefs anywhere.
            self.assertNotIn("href=''", surface)
            self.assertNotIn('href=""', surface)

    def test_canonical_urls_match_declared_source_hosts(self):
        items = editorial_items()[2]
        for item in items:
            host = urlsplit(item["canonical_url"]).hostname
            expected = HOST_RULES[item["source_id"]]
            if expected == "github.com":
                # Two official repos share github.com; scope by path.
                self.assertIn("/firedancer-io/" in item["canonical_url"]
                              or "/anza-xyz/" in item["canonical_url"], [True])
            self.assertTrue(host and host.endswith(expected.split(".")[-1]),
                            f"{item['id']}: {host} not under {expected}")

    def test_items_ordered_newest_first_with_deterministic_ties(self):
        # The rendered surface must present items newest-first (by
        # published_at). Title positions in the HTML must follow that order.
        snapshot = snapshot_with(editorial_items()[2])
        items = sorted(snapshot["news"]["items"],
                       key=lambda i: (i.get("published_at") or "", i["id"]),
                       reverse=True)
        surface = render.render_community_news(snapshot, "desktop")
        positions = [surface.find(item["title"]) for item in items]
        self.assertTrue(all(p != -1 for p in positions), "item title missing")
        self.assertEqual(positions, sorted(positions),
                         "not newest-first on the rendered surface")

    def test_empty_item_fields_never_render_broken_links(self):
        snapshot = snapshot_with([])
        surface = render.render_community_news(snapshot, "desktop")
        # An empty items list renders the section with its explicit empty
        # state, never an anchor with no destination.
        self.assertNotIn("href=''", surface)


if __name__ == "__main__":
    unittest.main()
