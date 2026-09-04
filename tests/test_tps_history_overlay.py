"""Focused regressions for the Overview total/non-vote TPS history overlay."""

import json
import re
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render  # noqa: E402


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample-snapshot.json"


def history_with_tps(
    total_values: list[float], non_vote_values: list[float | None],
) -> list[dict]:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    history = []
    for index, (total, non_vote) in enumerate(zip(total_values, non_vote_values)):
        snapshot = deepcopy(base)
        snapshot["schema_version"] = 8
        snapshot["collected_at"] = (
            datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=6 * index)
        ).isoformat()
        snapshot["performance"].update({
            "available": True,
            "latest_tps": total,
            "non_vote_available": non_vote is not None,
            "latest_non_vote_tps": non_vote,
            "mean_slot_time_secs": None,
        })
        snapshot["validators"]["delinquent_pct"] = None
        history.append(snapshot)
    return history


def indexes_for(history: list[dict]) -> dict:
    summary = {}
    derived = {}
    first_at = history[0]["collected_at"]
    last_at = history[-1]["collected_at"]
    window = f"{first_at}->{last_at}"
    for index, snapshot in enumerate(history):
        at = snapshot["collected_at"]
        summary[("latest_tps", at)] = {"observation_id": f"total-{index}"}
        summary[("latest_non_vote_tps", at)] = {
            "observation_id": f"non-vote-{index}"
        }
    for key in ("latest_tps", "latest_non_vote_tps"):
        derived[(f"history_{key}_points_count", window)] = {
            "observation_id": f"{key}-count"
        }
        derived[(f"history_{key}_first_to_last_change_pct", window)] = {
            "observation_id": f"{key}-change"
        }
    return {"summary": summary, "subject": {}, "derived": derived}


class TestTpsHistoryOverlay(unittest.TestCase):
    def test_one_card_uses_one_axis_and_breaks_each_series_independently(self):
        history = history_with_tps(
            [100.0, 110.0, 120.0, 130.0, 140.0],
            [60.0, 65.0, None, 75.0, 80.0],
        )

        markup = render.render_overview_charts(history)
        card = markup.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]

        self.assertNotIn("data-pulse-key='latest_non_vote_tps'", markup)
        self.assertIn("data-overview-tps-overlay", card)
        self.assertEqual(card.count("<svg "), 1)
        self.assertEqual(card.count("chart-series--total"), 1)
        self.assertEqual(card.count("chart-series--non-vote"), 1)
        total = card.split("chart-series--total", 1)[1].split("</g>", 1)[0]
        non_vote = card.split("chart-series--non-vote", 1)[1].split("</g>", 1)[0]
        self.assertEqual(total.count("<polyline"), 1)
        self.assertEqual(non_vote.count("<polyline"), 2)
        self.assertIn("<span class='axis-label'>150</span>", card)
        self.assertIn("<span class='axis-label'>100</span>", card)
        self.assertIn("<span class='axis-label'>50</span>", card)

    def test_date_targets_name_both_values_and_explicit_missing_observations(self):
        history = history_with_tps(
            [100.0, 110.0, 120.0, 130.0],
            [60.0, None, 70.0, 75.0],
        )
        markup = render.render_overview_charts(history, indexes_for(history))
        card = markup.split("data-pulse-key='latest_tps'", 1)[1].split("</figure>", 1)[0]

        self.assertEqual(card.count("data-overview-chart-point"), 4)
        self.assertIn("data-total-value='110'", card)
        self.assertIn("data-non-vote-value='Unavailable'", card)
        for index in range(4):
            self.assertIn(f"total-{index}", card)
            self.assertIn(f"non-vote-{index}", card)
        for binding in (
            "latest_tps-count", "latest_non_vote_tps-count",
            "latest_tps-change", "latest_non_vote_tps-change",
        ):
            self.assertIn(binding, card)

        self.assertIn(
            "chart.hasAttribute('data-overview-tps-overlay')", render.MOBILE_CONTROLLER,
        )
        self.assertIn("point.dataset.nonVoteValue", render.MOBILE_CONTROLLER)
        self.assertIn("event.key === 'ArrowLeft'", render.MOBILE_CONTROLLER)
        self.assertIn("event.key === 'ArrowRight'", render.MOBILE_CONTROLLER)

    def test_non_vote_only_missing_history_does_not_invent_an_unavailable_card(self):
        history = history_with_tps([100.0, 110.0], [None, None])
        for index, snapshot in enumerate(history):
            snapshot["performance"]["mean_slot_time_secs"] = 0.4 + index * 0.01
            snapshot["validators"]["delinquent_pct"] = 3.0 + index
            snapshot["economics"] = {
                "available": True,
                "price": {"available": True, "price_usd": 100.0 + index},
                "tvl": {"available": True, "tvl_usd": 8_000_000_000.0 + index},
            }
            snapshot["activity"] = {
                "available": True,
                "window": {"last_block_time": int(datetime.fromisoformat(
                    snapshot["collected_at"]
                ).timestamp())},
                "fees": {"available": True, "median_lamports": 5_000 + index},
                "rev": {"available": True, "sample_mean_estimate_sol": 10.0 + index},
            }

        direct = render.facts_module.public_observation_records(history[-1], history=history)
        derived = render.build_derived_observation_records(history, direct)
        unavailable = next(
            record for record in derived
            if record["metric_id"] == "overview_unavailable_history_chart_count"
        )

        self.assertEqual(unavailable["value"], 0)
        self.assertEqual(unavailable["denominator"], "7 configured Overview history cards")
        markup = render.render_overview_charts(history)
        self.assertNotIn("chart-disclosure--availability", markup)
        self.assertIn("Non-vote TPS unavailable in recorded history", markup)


if __name__ == "__main__":
    unittest.main()
