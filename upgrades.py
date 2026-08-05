#!/usr/bin/env python3
"""Upcoming Solana protocol upgrades.

**This is hand-maintained static reference data, not a live feed.** There is no
keyless API that publishes the protocol roadmap, so rather than scrape a page
that will change shape, or imply liveness the artifact does not have, each entry
carries the date it was last checked and a primary-source link a reader can
verify independently.

The report labels this section as static wherever it renders. A dashboard that
presents hand-typed text with the same authority as a live RPC read is the
dishonest version of this feature.

Update by editing this file: change `status`, bump `last_verified`, and keep the
source link pointing at something a reader can check.
"""

from __future__ import annotations

from typing import Any

LAST_VERIFIED = "2026-08-05"

SIMD_BASE = (
    "https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals"
)

UPGRADES: list[dict[str, Any]] = [
    {
        "name": "Alpenglow (Votor)",
        "identifier": "SIMD-0326",
        "status": "Review",
        "summary": (
            "Changes the core consensus protocol from Proof of History and TowerBFT to "
            "Alpenglow — specifically the Votor parts. Current consensus finality time "
            "is 12.8 seconds. Rotor, the data-dissemination half of the Alpenglow white "
            "paper, is explicitly excluded from this SIMD: Turbine remains the "
            "dissemination protocol and Rotor will get its own proposal later."
        ),
        "why_it_matters": (
            "Finality is the property most visible to users and applications, and this "
            "is the largest change to Solana consensus since launch. Alpenglow is a "
            "family rather than a single proposal — see also SIMD-0357 (validator "
            "admission ticket) and SIMD-0384 (migration)."
        ),
        "source": f"{SIMD_BASE}/0326-alpenglow.md",
    },
    {
        "name": "Reduce Slot Times",
        "identifier": "SIMD-0525",
        "status": "Draft",
        "summary": (
            "Reduces the target slot time from 400ms to 200ms in four feature-gated "
            "steps — 350ms, 300ms, 250ms, 200ms. Each step holds ticks_per_slot at 64 "
            "and leader windows at 4 slots, scaling per-slot work limits so wall-clock "
            "throughput is unchanged. Extends SIMD-0357."
        ),
        "why_it_matters": (
            "This report already measures mean slot time against the current 400ms "
            "target. If this lands, that target halves in four observable steps — so "
            "the measurement above becomes the way to watch this roadmap item arrive."
        ),
        "source": f"{SIMD_BASE}/0525-reduce-slot-times.md",
    },
]


def upgrade_section() -> dict[str, Any]:
    """Return the static roadmap, explicitly marked as hand-maintained."""
    return {
        "available": bool(UPGRADES),
        "is_static": True,
        "last_verified": LAST_VERIFIED,
        "note": (
            "Hand-maintained reference data, not a live feed. Verify status "
            "against the linked SIMD repository before relying on it."
        ),
        "upgrades": UPGRADES,
    }
