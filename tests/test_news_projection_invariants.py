"""Projection invariants for the rights-safe news additions.

The decisive invariant from the plan:
    project_public_envelope(maximal_generated_snapshot) == maximal_generated_snapshot

A snapshot built by today's collector (with the new sources populated) must
project to itself — no field may exist that the public contract omits.
Conversely, a pre-existing snapshot without the new sources must project to
itself unchanged (contract growth is purely additive).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news  # noqa: E402
import render  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def snapshot_with_new_sources():
    firedancer_body = (FIXTURES / "firedancer-releases.json").read_bytes()
    simd_documents = {
        "0326-alpenglow": (FIXTURES / "simd-0326-alpenglow.md").read_bytes(),
        "0525-reduce-slot-times": (
            FIXTURES / "simd-0525-reduce-slot-times.md").read_bytes(),
    }
    raw = {
        "agave_releases": None,
        "firedancer_releases": firedancer_body,
        "simd_proposal_metadata": simd_documents,
        "network_status": None,
    }
    built = news.build_news(raw, None, news.HELD_LICENSED_CONTENT_SOURCES)
    return {
        "schema_version": 9,
        "collected_at": "2026-09-03T01:00:00+00:00",
        "source": {"endpoint": "https://api.mainnet-beta.solana.com"},
        "network": {"healthy": True},
        "news": built,
    }


def legacy_snapshot_without_new_sources():
    return {
        "schema_version": 9,
        "collected_at": "2026-09-01T00:00:00+00:00",
        "source": {"endpoint": "https://api.mainnet-beta.solana.com"},
        "network": {"healthy": True},
        "news": {
            "available": False,
            "partial": False,
            "requires_api_key": False,
            "items": [],
            "featured_item_id": None,
            "sources": {
                "agave_releases": {
                    "label": "Agave validator releases",
                    "publisher": "anza-xyz/agave (official GitHub repository)",
                    "why": "why", "url": "https://api.github.com/x",
                    "requires_api_key": False, "available": False,
                    "reason": "source unreachable",
                },
                "solana_news": {
                    "label": "Solana News", "publisher": "p", "why": "why",
                    "url": "https://x", "requires_api_key": False,
                    "available": False, "reason": "held",
                },
                "simd_proposals": {
                    "label": "l", "publisher": "p", "why": "why",
                    "url": "https://x", "requires_api_key": False,
                    "available": False, "reason": "held",
                },
                "network_status": {
                    "label": "Network status history", "publisher": "p",
                    "why": "why", "url": "https://x", "requires_api_key": False,
                    "available": False, "reason": "source unreachable",
                },
            },
            "current_status": {"available": False, "partial": False,
                               "requires_api_key": False},
            "note": "note",
        },
    }


class TestProjectionInvariants(unittest.TestCase):
    def test_maximal_snapshot_with_new_sources_projects_to_itself(self):
        snapshot = snapshot_with_new_sources()
        self.assertEqual(render.project_public_envelope(snapshot), snapshot)

    def test_legacy_snapshot_without_new_sources_projects_to_itself(self):
        snapshot = legacy_snapshot_without_new_sources()
        self.assertEqual(render.project_public_envelope(snapshot), snapshot)


if __name__ == "__main__":
    unittest.main()
