"""Tests for the Ecosystem Pulse block (render-time only)."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample-snapshot.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def pulse_snapshot():
    """Live-shaped fixture: schema 9 with all pulse inputs available."""
    snapshot = load_fixture()
    snapshot["schema_version"] = 9
    snapshot["economics"] = {
        "available": True,
        "price": {
            "available": True, "price_usd": 99.28,
            "last_updated_at_unix": 1788374660, "freshness": "fresh",
        },
        "sources": {"coingecko": {"available": True}},
    }
    snapshot["activity"] = {
        "available": True,
        "rev": {"available": True, "sample_mean_estimate_sol": 4257.68},
    }
    snapshot["growth"] = {
        "available": True,
        "daily_active_addresses": {
            "available": True, "minimum": 394098, "maximum": 774939,
            "provider_count": 7, "date": "2026-08-30",
            "semantic_metric_id": "stablecoin_active_address_provider_range",
            "display_name": "Stablecoin active-address provider range",
            "source_label": "Active Addresses",
            "scope": "provider observations for Solana stablecoin activity, "
                     "not network-wide DAA or unique humans",
        },
        "daily_fee_payers": {
            "available": True, "minimum": 1200000, "maximum": 2400000,
            "provider_count": 6, "date": "2026-08-30",
            "semantic_metric_id": "transaction_initiator_provider_range",
            "display_name": "Transaction-initiator provider range",
            "source_label": "Fee Payers",
            "scope": "provider observations of transaction initiators, not unique humans",
        },
        "tokenized_equities": {
            "available": True, "registry_asset_count": 2,
            "all_assets": [
                {"symbol": None, "name": "Walmart", "supply": 1_500_000.5,
                 "supply_collected_at": "2026-09-01T11:00:00+00:00"},
                {"symbol": None, "name": "NVIDIA", "supply": 321577.7386022,
                 "supply_collected_at": "2026-09-02T11:00:00+00:00"},
            ],
            "supply_coverage": {"coverage_denominator": 2},
        },
    }
    return snapshot


class EcosystemPulseTests(unittest.TestCase):
    def test_available_inputs_render_all_six_cards_with_real_values(self):
        html = render.render_ecosystem_pulse(pulse_snapshot())
        # The block is titled and every card carries a value, source, basis,
        # data date and (where provider data) the ~1 day source lag.
        self.assertIn("Ecosystem Pulse", html)
        self.assertIn("$99.28", html)
        self.assertIn("CoinGecko · provider-reported", html)
        self.assertIn("394,098–774,939", html)
        self.assertIn("Solana Data · provider-reported · 2026-08-30 · 7 providers", html)
        self.assertIn("1,200,000–2,400,000", html)
        self.assertIn("6 providers", html)
        self.assertIn("~1 day source lag", html)
        self.assertIn("4,257.68 SOL", html)
        self.assertIn("on-chain block sampling · measured", html)
        self.assertIn("1.8M units", html)
        self.assertIn("RPC getTokenSupply(finalized) · measured", html)
        # Application revenue has no Solana Data source yet, so even the
        # all-available fixture shows its explicit pending state.
        self.assertIn("Application revenue", html)
        self.assertIn("provider range pending Solana Data adoption", html)
        # The Pulse's Dune card is excluded: the fixture has no dune section,
        # and the sixth card here is the tokenized-equities supply card.
        self.assertNotIn(">Unavailable</div>", html.split("DEX volume")[0])
        # Never a zero value and never a bare dash.
        self.assertNotIn("value\">0</div>", html)
        self.assertNotIn("value\">—</div>", html)

    def test_unavailable_sections_render_explicit_states_without_raising(self):
        snapshot = pulse_snapshot()
        snapshot["economics"] = {
            "available": False,
            "price": {"available": False, "reason": "CoinGecko request failed."},
            "sources": {},
        }
        snapshot["activity"] = {"available": False, "reason": "RPC unreachable."}
        snapshot["growth"] = {
            "available": True,
            "daily_active_addresses": {
                "available": False,
                "reason": "Solana Data provider activity rows were not collected this run.",
            },
            "daily_fee_payers": {
                "available": False,
                "reason": "Fee Payers provider-row republication is held pending "
                          "explicit source-rights acceptance.",
            },
            "tokenized_equities": {"available": False},
        }
        html = render.render_ecosystem_pulse(snapshot)  # must not raise
        self.assertIn("Ecosystem Pulse", html)
        self.assertIn("CoinGecko request failed.", html)
        self.assertIn("RPC unreachable.", html)
        self.assertIn("not collected this run", html)
        self.assertIn("held pending explicit source-rights acceptance", html)
        # Application revenue keeps its explicit pending state, not a zero or dash.
        self.assertIn("provider range pending Solana Data adoption", html)
        self.assertIn("Finalized token supplies were not observed this run.", html)
        self.assertNotIn("value\">0</div>", html)
        self.assertNotIn("value\">—</div>", html)
        # And the assembled overview still places the block above economics.
        overview = render.render_html(snapshot) \
            if hasattr(render, "render_html") else None
        if overview is not None:
            pulse_at = overview.find("Ecosystem Pulse")
            economics_at = overview.rfind("Economic indicators")
            self.assertGreaterEqual(pulse_at, 0)
            self.assertLess(pulse_at, economics_at)

    def test_rendering_is_projection_pure_snapshot_bytes_unchanged(self):
        snapshot = pulse_snapshot()
        before = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        render.render_ecosystem_pulse(snapshot)
        after = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        self.assertEqual(before, after)
        # Also holds when the block is placed inside the full overview assembly.
        snapshot_b = pulse_snapshot()
        before_b = json.dumps(snapshot_b, sort_keys=True, separators=(",", ":"))
        if hasattr(render, "render_html"):
            render.render_html(snapshot_b)
            after_b = json.dumps(snapshot_b, sort_keys=True, separators=(",", ":"))
            self.assertEqual(before_b, after_b)


if __name__ == "__main__":
    unittest.main()
