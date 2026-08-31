"""Offline tests for the historical charts.

Every function under test is pure over a list of snapshot dicts, so nothing
here touches the network, the clock, or the snapshot directory.

A chart is a claim about data that a reader cannot check by squinting, so the
claims are pinned here instead:

  * a gap in collection is drawn as a gap and never interpolated,
  * a metric missing from a snapshot is never plotted at zero,
  * a sampled series is distinguishable from a measured one by more than hue,
  * the same snapshots always emit the same SVG bytes,
  * the markup stays self-contained — no script, no external asset.
"""

import copy
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import charts  # noqa: E402

TPS = next(s for s in charts.SERIES if s["key"] == "latest_tps")
SLOT_TIME = next(s for s in charts.SERIES if s["key"] == "mean_slot_time_secs")
REV = next(s for s in charts.SERIES if s["key"] == "sample_mean_rev_sol")

BASE = datetime(2026, 8, 5, tzinfo=timezone.utc)


def at(hours):
    return (BASE + timedelta(hours=hours)).isoformat(timespec="seconds")


def snap(offset_hours, tps=3000.0, **extra):
    """A snapshot `offset_hours` after the base moment, carrying only what is needed."""
    body = {
        "collected_at": at(offset_hours),
        "schema_version": 8,
        "performance": {"available": True, "latest_tps": tps},
    }
    if tps is None:
        body["performance"] = {"available": False}
    body.update(extra)
    return body


def evenly(values, start=0, step=1):
    """A run of snapshots `step` hours apart. `None` means the metric is absent."""
    return [snap(start + index * step, value) for index, value in enumerate(values)]


class TestExtraction(unittest.TestCase):
    def test_slot_time_note_names_report_policy_not_a_protocol_target(self):
        self.assertEqual(
            SLOT_TIME["note"],
            "Derived from the same performance samples. "
            "This report's slow-slot alert threshold is 0.60s.",
        )
        self.assertNotIn("network target", SLOT_TIME["note"].lower())
        self.assertNotIn("0.400", SLOT_TIME["note"])

    def test_a_missing_metric_becomes_a_null_point_not_a_zero(self):
        points = charts.extract(evenly([3000.0, None, 3200.0]), TPS)
        self.assertEqual([p["value"] for p in points], [3000.0, None, 3200.0])

    def test_an_ineligible_source_becomes_a_gap_not_current_data(self):
        # The middle snapshot retains its number but its source is a
        # carried-forward last-known-good copy: it must plot as a gap.
        history = evenly([3000.0, 3100.0, 3200.0])
        history[1]["performance"] = {
            "available": True, "stale": True,
            "source_state": "last_known_good", "latest_tps": 3100.0,
        }
        points = charts.extract(history, TPS)
        self.assertEqual([p["value"] for p in points], [3000.0, None, 3200.0])

    def test_an_explicitly_stale_freshness_source_is_never_plotted(self):
        history = evenly([3000.0, 3100.0])
        history[1]["performance"] = {
            "available": True, "freshness": "stale", "latest_tps": 3100.0,
        }
        points = charts.extract(history, TPS)
        self.assertEqual([p["value"] for p in points], [3000.0, None])

    def test_points_are_ordered_by_time_regardless_of_input_order(self):
        history = evenly([3000.0, 3100.0, 3200.0])
        points = charts.extract(list(reversed(history)), TPS)
        self.assertEqual([p["value"] for p in points], [3000.0, 3100.0, 3200.0])

    def test_a_snapshot_with_an_unparseable_timestamp_is_dropped(self):
        broken = snap(1, 3000.0)
        broken["collected_at"] = "whenever"
        points = charts.extract([broken, snap(2, 3100.0)], TPS)
        self.assertEqual(len(points), 1)

    def test_a_boolean_is_not_a_measurement(self):
        odd = snap(1)
        odd["performance"]["latest_tps"] = True
        self.assertIsNone(charts.extract([odd], TPS)[0]["value"])

    def test_non_finite_values_are_missing_observations(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                points = charts.extract(evenly([3000.0, value, 3200.0]), TPS)
                self.assertEqual([point["value"] for point in points],
                                 [3000.0, None, 3200.0])
                self.assertEqual([len(run) for run in charts.segments(points)], [1, 1])
                self.assertEqual(charts.svg_chart(TPS, points), "")


class TestGaps(unittest.TestCase):
    """Gaps are the property most easily faked by a charting library."""

    def test_evenly_spaced_points_form_one_unbroken_run(self):
        points = charts.extract(evenly([3000.0, 3100.0, 3200.0, 3300.0]), TPS)
        self.assertEqual(len(charts.segments(points)), 1)

    def test_a_long_collection_outage_breaks_the_line(self):
        # Hourly cadence, then a two-day hole. Sloping across it would be a
        # picture of data nobody collected.
        history = evenly([3000.0, 3100.0, 3200.0, 3250.0]) + [snap(48, 3300.0)]
        runs = charts.segments(charts.extract(history, TPS))
        self.assertEqual(len(runs), 2)
        self.assertEqual(len(runs[0]), 4)
        self.assertEqual(len(runs[1]), 1)

    def test_a_missing_value_breaks_the_line_too(self):
        points = charts.extract(evenly([3000.0, 3100.0, None, 3200.0, 3300.0]), TPS)
        runs = charts.segments(points)
        self.assertEqual([len(run) for run in runs], [2, 2])

    def test_no_path_is_drawn_across_a_gap(self):
        points = charts.extract(evenly([3000.0, 3100.0, None, 3200.0, 3300.0]), TPS)
        markup = charts.svg_chart(TPS, points)
        # Two runs, so exactly two line paths — never one path stitched through.
        self.assertEqual(markup.count("<path"), 2)

    def test_the_caption_counts_the_gaps_in_words(self):
        points = charts.extract(evenly([3000.0, 3100.0, None, 3200.0]), TPS)
        stats = charts.series_stats(points)
        self.assertEqual(stats["gaps"], 1)
        self.assertEqual(stats["missing"], 1)
        text = charts.caption(TPS, stats)
        self.assertIn("1 gap(s) left undrawn, never interpolated", text)
        self.assertIn("1 without this metric", text)

    def test_a_run_of_one_is_drawn_as_a_dot_not_a_line(self):
        # An isolated observation between two outages is a single reading, and
        # a line drawn from it would imply a trend that was never observed.
        history = ([snap(0, 3000.0)]
                   + evenly([3100.0, 3150.0, 3200.0, 3250.0], start=96)
                   + [snap(200, 3300.0)])
        points = charts.extract(history, TPS)
        runs = charts.segments(points)
        self.assertEqual([len(run) for run in runs], [1, 4, 1])
        markup = charts.svg_chart(TPS, points)
        self.assertEqual(markup.count("<path"), 1)   # only the four-point run


class TestChartability(unittest.TestCase):
    def test_one_point_is_not_a_trend_and_is_not_charted(self):
        points = charts.extract(evenly([3000.0]), TPS)
        self.assertFalse(charts.series_stats(points)["chartable"])
        self.assertEqual(charts.svg_chart(TPS, points), "")

    def test_a_series_absent_from_every_snapshot_is_named_not_drawn(self):
        markup = charts.render_charts_html(evenly([3000.0, 3100.0, 3200.0]))
        self.assertIn("Not charted yet, and not drawn as zero", markup)
        self.assertIn("REV over observed window (sample mean)", markup)
        # Nothing invents a zero baseline for the absent series.
        self.assertNotIn("0 usable point(s) across 3 snapshot(s); two are needed"
                         " before a trend exists</li><li>Transactions", markup)

    def test_two_points_are_enough(self):
        self.assertTrue(
            charts.series_stats(charts.extract(evenly([3000.0, 3100.0]), TPS))["chartable"])

    def test_an_empty_history_says_so(self):
        self.assertIn("No snapshots on disk yet", charts.render_charts_html([]))

    def test_markdown_calls_history_recorded_not_committed(self):
        rendered = "\n".join(charts.render_charts_markdown(evenly([1.0, 2.0])))
        self.assertIn("recorded snapshot", rendered)
        self.assertNotIn("committed snapshot", rendered)


class TestScaling(unittest.TestCase):
    """The chart must read the same at 3 snapshots and at 200."""

    def test_few_points_are_individually_marked(self):
        markup = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3100.0, 3200.0]), TPS))
        self.assertEqual(markup.count("<circle"), 4)   # 3 markers + the endpoint

    def test_many_points_drop_per_point_markers_and_keep_the_endpoint(self):
        many = [3000.0 + index for index in range(60)]
        markup = charts.svg_chart(TPS, charts.extract(evenly(many), TPS))
        self.assertEqual(markup.count("<circle"), 1)   # endpoint only
        self.assertEqual(markup.count("<path"), 1)

    def test_a_long_history_still_produces_one_hover_band_per_snapshot(self):
        many = [3000.0 + index for index in range(60)]
        markup = charts.svg_chart(TPS, charts.extract(evenly(many), TPS))
        self.assertEqual(markup.count("<rect"), 60)

    def test_the_endpoint_label_stays_compact_for_large_values(self):
        self.assertEqual(charts.fmt_endlabel(4_788_941_147.0, 0), "4.79B")
        self.assertEqual(charts.fmt_endlabel(3_221.57, 2), "3,221.57")


class TestAxis(unittest.TestCase):
    def test_a_constant_series_gets_a_band_not_a_zero_height_axis(self):
        low, high, ticks, step = charts.nice_axis(1.0, 1.0)
        self.assertLess(low, 1.0)
        self.assertGreater(high, 1.0)
        self.assertGreater(step, 0)

    def test_a_constant_series_renders_a_flat_line_without_dividing_by_zero(self):
        points = charts.extract(evenly([1.0, 1.0, 1.0]), TPS)
        markup = charts.svg_chart(TPS, points)
        self.assertIn("<path", markup)

    def test_ticks_cover_the_data(self):
        low, high, ticks, _ = charts.nice_axis(2935.0, 4021.0)
        self.assertLessEqual(low, 2935.0)
        self.assertGreaterEqual(high, 4021.0)
        self.assertGreaterEqual(len(ticks), 2)

    def test_a_narrow_band_still_gets_distinguishable_tick_labels(self):
        # TVL moving 0.2% across a day: rounding every tick to "4.8B" would
        # leave the axis carrying no information at all.
        low, high, ticks, step = charts.nice_axis(4_788_941_147.0, 4_797_107_540.0)
        labels = charts.tick_labels(ticks, step)
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(all(label.endswith("B") for label in labels))

    def test_wide_ranges_use_whole_numbers(self):
        low, high, ticks, step = charts.nice_axis(0.0, 5000.0)
        self.assertEqual(charts.tick_labels(ticks, step)[0], "0")

    def test_zero_and_negative_inputs_do_not_raise(self):
        for pair in ((0.0, 0.0), (-5.0, 5.0), (-10.0, -2.0)):
            low, high, ticks, step = charts.nice_axis(*pair)
            self.assertTrue(ticks)


class TestBasisDistinction(unittest.TestCase):
    """Sampled must never be mistakable for measured."""

    def test_a_sampled_series_is_dashed_and_a_measured_one_is_not(self):
        rev_history = [
            {"schema_version": 8,
             "collected_at": f"2026-08-05T0{hour}:00:00+00:00",
             "activity": {"available": True,
                          "rev": {"sample_mean_estimate_sol": 8000.0 + hour}}}
            for hour in range(1, 4)
        ]
        sampled = charts.svg_chart(REV, charts.extract(rev_history, REV))
        measured = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3100.0, 3200.0]), TPS))
        self.assertIn("stroke-dasharray", sampled)
        self.assertNotIn("stroke-dasharray", measured)

    def test_the_two_bases_use_different_hues(self):
        rev_history = [
            {"schema_version": 8,
             "collected_at": f"2026-08-05T0{hour}:00:00+00:00",
             "activity": {"available": True,
                          "rev": {"sample_mean_estimate_sol": 8000.0 + hour}}}
            for hour in range(1, 4)
        ]
        sampled = charts.svg_chart(REV, charts.extract(rev_history, REV))
        measured = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3100.0]), TPS))
        self.assertIn(charts.COLOR_SAMPLED, sampled)
        self.assertNotIn(charts.COLOR_MEASURED, sampled)
        self.assertIn(charts.COLOR_MEASURED, measured)

    def test_every_chart_carries_a_written_basis_badge(self):
        history = [
            {"schema_version": 8,
             "collected_at": f"2026-08-05T0{hour}:00:00+00:00",
             "performance": {"latest_tps": 3000.0 + hour},
             "activity": {"available": True,
                          "rev": {"sample_mean_estimate_sol": 8000.0 + hour}}}
            for hour in range(1, 4)
        ]
        markup = charts.render_charts_html(history)
        self.assertIn("basis-badge sampled", markup)
        self.assertIn("basis-badge measured", markup)

    def test_the_series_table_declares_a_basis_for_every_series(self):
        for spec in charts.SERIES:
            self.assertIn(spec["basis"], ("measured", "sampled"))
            self.assertTrue(spec["note"].strip())

    def test_series_keys_are_unique(self):
        keys = [spec["key"] for spec in charts.SERIES]
        self.assertEqual(len(keys), len(set(keys)))


class TestSelfContainment(unittest.TestCase):
    def test_the_svg_references_nothing_external(self):
        markup = charts.render_charts_html(evenly([3000.0, 3100.0, 3200.0]))
        # Not even a namespace URL: the inline SVG needs none inside an HTML
        # document, and its absence makes "no external reference" grep-checkable.
        self.assertNotIn("http://", markup)
        self.assertNotIn("https://", markup)
        self.assertNotIn("<script", markup)
        self.assertNotIn("<image", markup)
        self.assertNotIn("url(", markup)

    def test_hover_detail_is_native_svg_with_no_script(self):
        markup = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3100.0]), TPS))
        self.assertIn("<title>", markup)
        self.assertNotIn("onmouseover", markup)

    def test_a_missing_point_says_so_on_hover_rather_than_reading_zero(self):
        points = charts.extract(evenly([3000.0, 3100.0, None, 3200.0]), TPS)
        markup = charts.svg_chart(TPS, points)
        self.assertIn("no value in this snapshot", markup)

    def test_the_chart_is_labelled_for_a_screen_reader(self):
        markup = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3100.0]), TPS))
        self.assertIn("role='img'", markup)
        self.assertIn("aria-label=", markup)

    def test_hostile_content_cannot_escape_the_svg(self):
        history = evenly([3000.0, 3100.0])
        history[0]["collected_at"] = "2026-08-05T00:00:00+00:00<script>alert(1)</script>"
        markup = charts.svg_chart(TPS, charts.extract(history, TPS))
        self.assertNotIn("<script>", markup)


class TestDeterminism(unittest.TestCase):
    def test_the_same_snapshots_emit_the_same_bytes(self):
        history = evenly([3000.0, 3100.0, None, 3200.0])
        first = charts.render_charts_html(history)
        second = charts.render_charts_html(copy.deepcopy(history))
        self.assertEqual(first, second)

    def test_rendering_does_not_mutate_the_history(self):
        history = evenly([3000.0, 3100.0, 3200.0])
        original = copy.deepcopy(history)
        charts.render_charts_html(history)
        self.assertEqual(history, original)

    def test_coordinates_are_rounded_so_output_is_stable(self):
        markup = charts.svg_chart(TPS, charts.extract(evenly([3000.0, 3123.456]), TPS))
        for number in re.findall(r"[-\d]+\.(\d+)", markup):
            self.assertLessEqual(len(number), 2)


class TestOtherOutputs(unittest.TestCase):
    def test_markdown_reports_counts_for_every_series_including_empty_ones(self):
        lines = charts.render_charts_markdown(evenly([3000.0, 3100.0, 3200.0]))
        text = "\n".join(lines)
        self.assertIn("## History", text)
        for spec in charts.SERIES:
            self.assertIn(spec["label"], text)
        # A series with no data prints a dash, never a zero.
        self.assertIn("| REV over observed window (sample mean) (SOL) | 0 | 3 | 0 | — | — | — | sampled |",
                      text)

    def test_markdown_history_bindings_are_complete_and_fail_closed(self):
        history = evenly([3000.0, None, 3200.0])
        sequence = iter(range(1, 1000))

        def observation_id():
            return "obs-v1:" + f"{next(sequence):064x}"

        point_ids = {
            (spec["key"], point["at"]): observation_id()
            for spec in charts.SERIES
            for point in charts.extract(history, spec)
        }
        stat_ids = {}
        for spec in charts.SERIES:
            stats = charts.series_stats(charts.extract(history, spec))
            fields = ["points", "missing", "gaps"]
            if stats["points"]:
                fields.extend(("min", "max", "latest"))
            for field in fields:
                stat_ids[(spec["key"], field)] = observation_id()
        snapshot_count_id = observation_id()

        text = "\n".join(charts.render_charts_markdown(
            history,
            observation_ids=point_ids,
            stat_observation_ids=stat_ids,
            snapshot_count_observation_id=snapshot_count_id,
        ))
        self.assertIn(snapshot_count_id, text)
        tps_row = next(
            line for line in text.splitlines()
            if line.startswith("| Transactions per second (TPS) |")
        )
        self.assertIn("| 2 | 1 | 1 | 3,000 | 3,200 | 3,200 |", tps_row)
        self.assertTrue(all(identifier in text for identifier in point_ids.values()))
        self.assertTrue(all(identifier in text for identifier in stat_ids.values()))

        for missing_key in point_ids:
            with self.subTest(binding="point", missing_key=missing_key):
                incomplete = dict(point_ids)
                del incomplete[missing_key]
                with self.assertRaises(KeyError):
                    charts.render_charts_markdown(
                        history,
                        observation_ids=incomplete,
                        stat_observation_ids=stat_ids,
                        snapshot_count_observation_id=snapshot_count_id,
                    )
        for missing_key in stat_ids:
            with self.subTest(binding="stat", missing_key=missing_key):
                incomplete = dict(stat_ids)
                del incomplete[missing_key]
                with self.assertRaises(KeyError):
                    charts.render_charts_markdown(
                        history,
                        observation_ids=point_ids,
                        stat_observation_ids=incomplete,
                        snapshot_count_observation_id=snapshot_count_id,
                    )
        with self.subTest(binding="stat map"):
            with self.assertRaises(KeyError):
                charts.render_charts_markdown(
                    history,
                    observation_ids=point_ids,
                    stat_observation_ids=None,
                    snapshot_count_observation_id=snapshot_count_id,
                )
        with self.subTest(binding="snapshot count"):
            with self.assertRaises(KeyError):
                charts.render_charts_markdown(
                    history,
                    observation_ids=point_ids,
                    stat_observation_ids=stat_ids,
                    snapshot_count_observation_id=None,
                )

    def test_json_keeps_missing_values_null(self):
        payload = charts.history_json(evenly([3000.0, None, 3200.0]))
        series = payload["series"]["latest_tps"]
        self.assertEqual([p["value"] for p in series["points"]], [3000.0, None, 3200.0])
        self.assertFalse(series["charted"])
        self.assertFalse(payload["series"]["sample_mean_rev_sol"]["charted"])

    def test_json_records_the_basis_of_every_series(self):
        payload = charts.history_json(evenly([3000.0, 3100.0]))
        self.assertEqual(payload["series"]["sample_mean_rev_sol"]["basis"], "sampled")
        self.assertEqual(payload["series"]["latest_tps"]["basis"], "measured")


if __name__ == "__main__":
    unittest.main()
