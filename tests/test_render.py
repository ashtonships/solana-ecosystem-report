"""Offline tests for the renderers.

Both renderers are pure functions over a snapshot dict, so these run with no
network and no filesystem beyond reading the fixture.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample-snapshot.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestMarkdown(unittest.TestCase):
    def test_renders_every_section(self):
        markdown = render.render_markdown(load_fixture())
        for heading in ("# Solana Ecosystem Report", "## Network", "## Epoch",
                        "## Performance", "## Supply", "## Validators"):
            self.assertIn(heading, markdown)

    def test_includes_real_values_from_the_snapshot(self):
        markdown = render.render_markdown(load_fixture())
        self.assertIn("4,213", markdown)     # TPS, thousands-separated
        self.assertIn("1,024", markdown)     # active validator count
        self.assertIn("no API key", markdown)

    def test_degraded_snapshot_says_unavailable_rather_than_zero(self):
        degraded = {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": False},
            "epoch": {"available": False},
            "performance": {"available": False},
            "supply": {"available": False},
            "validators": {"available": False},
            "economics": {"available": False},
        }
        markdown = render.render_markdown(degraded)
        # Every data section must name itself unavailable rather than print 0.
        for section in ("Epoch", "Performance", "Supply", "Validator", "Economic"):
            self.assertRegex(markdown, rf"_{section}[^_]*unavailable[^_]*_")
        self.assertIn("🔴 unhealthy", markdown)
        self.assertNotIn("| 0 |", markdown)


class TestHtml(unittest.TestCase):
    def test_is_a_self_contained_document(self):
        page = render.render_html(load_fixture())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)
        self.assertIn("<style>", page)
        # No build step, no CDN, no external asset of any kind.
        self.assertNotIn("<script src=", page)
        self.assertNotIn("https://cdn", page)

    def test_the_page_fetches_no_subresource_of_any_kind(self):
        """Charts, badges and feeds must not have introduced an external asset.

        Anchors to primary sources are fine and wanted — they are citations a
        reader follows deliberately. What must never appear is a tag the
        browser fetches on its own: without the network, or from a file:// URL,
        the page has to draw itself completely.
        """
        page = render.render_html(load_fixture())
        for tag in ("<script", "<link", "<img", "<iframe", "<object", "<embed",
                    "<use", "<image"):
            self.assertNotIn(tag, page)
        self.assertNotIn("@import", page)
        self.assertNotIn("url(", page)          # no CSS-fetched font or image
        self.assertNotIn("srcset", page)

    def test_dark_theme_is_applied(self):
        page = render.render_html(load_fixture())
        self.assertIn("--bg: #0b0d10", page)

    def test_escapes_values_that_could_break_the_document(self):
        hostile = load_fixture()
        hostile["validators"]["top_validators"][0]["identity"] = "<script>alert(1)</script>"
        page = render.render_html(hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_health_pill_reflects_actual_state(self):
        healthy = load_fixture()
        self.assertIn('class="pill ok"', render.render_html(healthy))

        unhealthy = load_fixture()
        unhealthy["network"]["healthy"] = False
        self.assertIn('class="pill bad"', render.render_html(unhealthy))

    def test_degraded_snapshot_renders_without_raising(self):
        page = render.render_html({
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": False},
            "epoch": {"available": False},
            "performance": {"available": False},
            "supply": {"available": False},
            "validators": {"available": False},
            "activity": {"available": False},
        })
        # Each degraded section says so; none of them renders a zero.
        self.assertEqual(page.count("unavailable in this snapshot"), 5)
        self.assertNotIn(">$0<", page)

    def test_an_absent_activity_section_degrades_like_any_other(self):
        # A v1 snapshot predates block sampling entirely and has no such key.
        page = render.render_html({
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
        })
        self.assertIn("Block sampling unavailable in this snapshot", page)

    def test_epoch_progress_bar_cannot_overflow(self):
        odd = load_fixture()
        odd["epoch"]["progress_pct"] = 140.0
        self.assertIn("width:100%", render.render_html(odd))


class TestActivityRendering(unittest.TestCase):
    """The activity section renders SOL from the chain and USD from CoinGecko.

    Those two sources fail independently, so the tests below pin the behaviour
    when the price is gone: SOL figures survive, dollar figures become dashes,
    and nothing anywhere turns into "$0.00".
    """

    def snapshot(self, price=True):
        body = {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
            "activity": {
                "available": True,
                "window": {"blocks_sampled": 16, "observed_seconds": 91_395},
                "fees": {
                    "available": True, "median_lamports": 5_391, "mean_lamports": 34_311,
                    "p90_lamports": 18_000, "p99_lamports": 555_000, "median_sol": 5.391e-06,
                    "vote_share_pct": 43.7, "failure_rate_pct": 44.88,
                    "nonvote_transactions_sampled": 14_176,
                },
                "rev": {
                    "available": True, "definition": "base fees + priority fees + Jito tips",
                    "sampled_sol": {"base": 0.13146, "priority": 0.41041,
                                    "jito_tips": 0.10789, "total": 0.64976},
                    "per_block_sol": {"mean": 0.04061, "min": 0.016146, "max": 0.113629},
                    "estimated_24h_sol": 8760.35, "estimated": True,
                    "estimated_24h_sol_low": 5900.0, "estimated_24h_sol_high": 11620.7,
                    "confidence": "95% interval on the sample mean",
                    "blocks_in_window": 215_719, "method": "mean REV per sampled block",
                },
                "addresses": {
                    "available": True, "unique_fee_payers_sampled": 4_019,
                    "unique_accounts_sampled": 32_646, "mean_fee_payers_per_block": 377.2,
                    "blocks_sampled": 16, "daily_active_addresses": None,
                    "daily_active_available": False,
                    "note": "Unique non-vote fee payers seen in the sampled blocks only, "
                            "not a daily total.",
                },
                "fee_split": {
                    "available": True, "blocks_reconciled": 16, "fees_sol": 0.54187,
                    "validator_reward_sol": 0.476097, "burned_sol": 0.065772, "burned_pct": 12.14,
                },
            },
        }
        if price:
            body["economics"] = {"available": True,
                                 "price": {"available": True, "price_usd": 73.97}}
        return body

    def test_markdown_states_the_fee_basis_next_to_the_number(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("5,391 lamports", markdown)
        self.assertIn("non-vote transactions", markdown)
        self.assertIn("43.70%", markdown)

    def test_daily_active_is_rendered_as_withheld_never_as_a_number(self):
        for text in (render.render_markdown(self.snapshot()),
                     render.render_html(self.snapshot())):
            self.assertIn("not a daily total", text)
            self.assertNotIn("Daily active addresses</div><div class='value'>0", text)
        self.assertIn("not derivable", render.render_markdown(self.snapshot()))
        self.assertIn("not derivable", render.render_html(self.snapshot()))

    def test_rev_components_and_daily_estimate_appear(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("8,760.35 SOL", markdown)
        self.assertIn("Jito tips", markdown)
        self.assertIn("$648,003", markdown)  # 8760.35 x 73.97

    def test_the_daily_rev_figure_is_labelled_as_extrapolated(self):
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("Extrapolated, not measured", markdown)
        self.assertIn("order-of-magnitude estimate", markdown)

    def test_the_daily_estimate_is_printed_with_its_interval(self):
        for text in (render.render_markdown(self.snapshot()),
                     render.render_html(self.snapshot())):
            self.assertIn("5,900", text)
            self.assertIn("11,620.7", text)

    def test_an_estimate_without_an_interval_omits_it_rather_than_faking_one(self):
        narrow = self.snapshot()
        for key in ("estimated_24h_sol_low", "estimated_24h_sol_high", "confidence"):
            narrow["activity"]["rev"][key] = None
        markdown = render.render_markdown(narrow)
        self.assertIn("8,760.35 SOL", markdown)
        self.assertNotIn("95% interval", markdown)

    def test_a_truncated_run_says_so_on_the_page(self):
        cut = self.snapshot()
        cut["activity"]["window"].update({"truncated": True, "blocks_sampled": 4,
                                          "blocks_requested": 16})
        for text in (render.render_markdown(cut), render.render_html(cut)):
            self.assertIn("Sampling stopped early", text)
        self.assertNotIn("Sampling stopped early", render.render_markdown(self.snapshot()))

    def test_burn_is_labelled_as_measured_rather_than_assumed(self):
        self.assertIn("no burn rate is assumed", render.render_markdown(self.snapshot()))

    def test_a_sub_cent_fee_does_not_round_away_to_zero(self):
        # A median fee is a fraction of a cent; "$0.00" would be a false claim
        # that using Solana is free.
        markdown = render.render_markdown(self.snapshot())
        self.assertIn("$0.000399", markdown)  # 5.391e-06 SOL x $73.97
        self.assertNotIn("| $0.00 |", markdown)

    def test_a_missing_price_source_dashes_the_usd_not_zeroes_it(self):
        markdown = render.render_markdown(self.snapshot(price=False))
        self.assertIn("8,760.35 SOL", markdown)   # chain figures unaffected
        self.assertNotIn("$0", markdown)
        self.assertIn("| 5,391 lamports | — |", markdown)

    def test_html_renders_the_section_without_raising(self):
        page = render.render_html(self.snapshot())
        self.assertIn("Fees, REV and activity", page)
        self.assertIn("Address activity", page)
        self.assertIn("Transaction fee distribution", page)

    def test_unavailable_subsections_are_skipped_not_zeroed(self):
        partial = self.snapshot()
        partial["activity"]["rev"] = {"available": False}
        partial["activity"]["fee_split"] = {"available": False}
        markdown = render.render_markdown(partial)
        self.assertNotIn("Real economic value", markdown)
        self.assertIn("5,391 lamports", markdown)  # fees still render


class TestDeltaRendering(unittest.TestCase):
    """The delta panel in Markdown and HTML.

    The rendering rules mirror the module's: a not-comparable metric never
    appears as a change, a sampled metric never looks measured, and an absent
    comparison degrades to a stated "not yet comparable" rather than silence.
    """

    @staticmethod
    def comparison(**overrides):
        body = {
            "status": "ok",
            "previous_collected_at": "2026-08-05T11:00:00+00:00",
            "current_collected_at": "2026-08-05T17:00:00+00:00",
            "elapsed_seconds": 21_600,
            "changes": [{
                "key": "latest_tps", "label": "Latest TPS", "unit": " TPS",
                "basis": "measured", "previous": 3000.0, "current": 4000.0,
                "change": 1000.0, "change_pct": 33.33, "direction": "up",
                "identifier": False,
                "why_it_matters": "Throughput is the headline liveness signal.",
                "what_to_verify": "getRecentPerformanceSamples on the same endpoint.",
            }],
            "steady": [{
                "key": "delinquent_pct", "label": "Validator delinquency", "unit": "%",
                "basis": "measured", "previous": 1.0, "current": 1.0, "change": 0.0,
                "change_pct": 0.0, "direction": "flat", "identifier": False,
            }],
            "not_comparable": [{
                "key": "price_usd", "label": "SOL price",
                "reason": "not present in the newer snapshot",
                "previous": 74.0, "current": None,
            }],
            "counts": {"changed": 1, "steady": 1, "not_comparable": 1},
        }
        body.update(overrides)
        return body

    def test_markdown_prints_the_moved_metric_with_both_context_lines(self):
        markdown = render.render_markdown(load_fixture(), None, self.comparison())
        self.assertIn("## What changed since the last snapshot", markdown)
        self.assertIn("| Latest TPS | 3,000.0 TPS | 4,000.0 TPS |", markdown)
        self.assertIn("+1,000.0 TPS (+33.33%)", markdown)
        self.assertIn("headline liveness signal", markdown)
        self.assertIn("_Verify:_", markdown)

    def test_a_not_comparable_metric_is_named_not_shown_as_a_change(self):
        for text in (render.render_markdown(load_fixture(), None, self.comparison()),
                     render.render_html(load_fixture(), None, self.comparison())):
            self.assertIn("not present in the newer snapshot", text)
        markdown = render.render_markdown(load_fixture(), None, self.comparison())
        # It appears under the not-comparable list, never in the change table.
        self.assertNotIn("| SOL price |", markdown)

    def test_html_marks_a_sampled_metric_distinctly_from_a_measured_one(self):
        sampled = self.comparison()
        sampled["changes"][0]["basis"] = "sampled"
        page = render.render_html(load_fixture(), None, sampled)
        self.assertIn("basis-badge sampled", page)
        self.assertNotIn("basis-badge sampled",
                         render.render_html(load_fixture(), None, self.comparison()))

    def test_markdown_names_the_basis_of_every_moved_metric(self):
        self.assertIn("| measured |",
                      render.render_markdown(load_fixture(), None, self.comparison()))
        sampled = self.comparison()
        sampled["changes"][0]["basis"] = "sampled"
        self.assertIn("| sampled/extrapolated |",
                      render.render_markdown(load_fixture(), None, sampled))

    def test_no_movement_reads_as_no_movement_not_as_no_data(self):
        quiet = self.comparison(changes=[], counts={"changed": 0, "steady": 12,
                                                    "not_comparable": 0},
                                not_comparable=[])
        markdown = render.render_markdown(load_fixture(), None, quiet)
        self.assertIn("No metric moved past its threshold", markdown)
        self.assertIn("12 compared metric(s)", markdown)
        self.assertIn("anomaly-note clear",
                      render.render_html(load_fixture(), None, quiet))

    def test_insufficient_history_is_stated_and_styled_as_pending(self):
        pending = {"status": "insufficient_history",
                   "message": "1 snapshot(s) on disk; two are needed.",
                   "changes": [], "steady": [], "not_comparable": [],
                   "counts": {"changed": 0, "steady": 0, "not_comparable": 0}}
        markdown = render.render_markdown(load_fixture(), None, pending)
        self.assertIn("Not yet comparable", markdown)
        page = render.render_html(load_fixture(), None, pending)
        # Grey, not green — "cannot compare" must not read as "nothing moved".
        self.assertIn("anomaly-note pending", page)

    def test_the_section_is_absent_entirely_when_no_comparison_is_supplied(self):
        markdown = render.render_markdown(load_fixture())
        self.assertNotIn("What changed since the last snapshot", markdown)
        self.assertNotIn("What changed since the last snapshot",
                         render.render_html(load_fixture()))

    def test_a_percentage_against_zero_is_declared_rather_than_invented(self):
        from_zero = self.comparison()
        from_zero["changes"][0].update({"previous": 0.0, "current": 3.0,
                                        "change": 3.0, "change_pct": None})
        markdown = render.render_markdown(load_fixture(), None, from_zero)
        self.assertIn("% n/a from zero", markdown)
        self.assertNotIn("+0.00%", markdown)

    def test_hostile_text_in_a_comparison_cannot_break_the_page(self):
        hostile = self.comparison()
        hostile["changes"][0]["label"] = "<script>alert(1)</script>"
        page = render.render_html(load_fixture(), None, hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class TestNewsRendering(unittest.TestCase):
    """The releases panel keeps three states apart, in both output formats."""

    @staticmethod
    def snapshot(**sources):
        return {
            "collected_at": "2026-08-05T00:00:00+00:00",
            "source": {"endpoint": "x"},
            "network": {"healthy": True},
            "news": {
                "available": any(s.get("available") for s in sources.values()),
                "requires_api_key": False,
                "note": "Official first-party feeds, fetched without credentials.",
                "sources": sources,
            },
        }

    @staticmethod
    def source(available=True, items=None, reason="", label="Agave validator releases"):
        body = {
            "label": label, "publisher": "anza-xyz/agave (GitHub)",
            "why": "Agave is the validator client most of the network runs.",
            "url": "https://github.com/anza-xyz/agave/releases.atom",
            "requires_api_key": False, "available": available,
        }
        if reason:
            body["reason"] = reason
        if items is not None:
            body["items"] = items
            body["item_count"] = len(items)
        return body

    ITEM = {"title": "Release v4.2.0-rc.1",
            "link": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0-rc.1",
            "published": "2026-08-03T12:41:49Z", "author": "github-actions[bot]"}

    def test_entries_render_with_their_dates_and_links(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[self.ITEM]))
        markdown = render.render_markdown(snapshot)
        self.assertIn("## Releases and announcements", markdown)
        self.assertIn("Release v4.2.0-rc.1", markdown)
        self.assertIn("2026-08-03T12:41:49Z", markdown)
        page = render.render_html(snapshot)
        self.assertIn("releases/tag/v4.2.0-rc.1", page)

    def test_a_failed_feed_says_unavailable_and_never_implies_quiet(self):
        # One feed up, one down — the down one must name its failure rather
        # than render as a source with nothing to report.
        snapshot = self.snapshot(
            agave_releases=self.source(items=[self.ITEM]),
            network_status=self.source(
                available=False, label="Network status history",
                reason="feed unreachable or not a parseable Atom document"),
        )
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("navailable", text)
            self.assertIn("feed unreachable", text)
            self.assertNotIn("no releases", text.lower())

    def test_a_feed_that_published_nothing_reads_differently_from_one_that_failed(self):
        empty = self.snapshot(agave_releases=self.source(items=[], reason="the feed parsed and published no entries"))
        broken = self.snapshot(agave_releases=self.source(available=False, reason="feed unreachable"))
        self.assertNotEqual(render.render_html(empty), render.render_html(broken))
        self.assertIn("published no entries", render.render_html(empty))

    def test_one_broken_feed_does_not_hide_a_working_one(self):
        snapshot = self.snapshot(
            agave_releases=self.source(items=[self.ITEM]),
            network_status=self.source(available=False, reason="feed unreachable",
                                       label="Network status history"),
        )
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("Release v4.2.0-rc.1", text)
            self.assertIn("Network status history", text)

    def test_every_feed_failing_states_it_is_about_the_fetch(self):
        snapshot = self.snapshot(agave_releases=self.source(
            available=False, reason="feed unreachable"))
        snapshot["news"]["available"] = False
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("statement about the fetch, not about the ecosystem", text)

    def test_a_snapshot_predating_the_feature_says_so_rather_than_reporting_a_failure(self):
        bare = {"collected_at": "2026-08-05T00:00:00+00:00",
                "source": {"endpoint": "x"}, "network": {"healthy": True}}
        for text in (render.render_markdown(bare), render.render_html(bare)):
            self.assertIn("predates the releases section", text)

    def test_feed_content_is_escaped_because_it_is_third_party_input(self):
        hostile = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "title": "<script>alert(1)</script>"}]))
        page = render.render_html(hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_an_entry_without_a_link_still_renders_its_title(self):
        snapshot = self.snapshot(agave_releases=self.source(items=[{
            **self.ITEM, "link": None}]))
        for text in (render.render_markdown(snapshot), render.render_html(snapshot)):
            self.assertIn("Release v4.2.0-rc.1", text)
        self.assertNotIn("](None)", render.render_markdown(snapshot))


class TestFormatting(unittest.TestCase):
    def test_none_renders_as_a_dash_not_zero(self):
        # "unknown" and "zero" must not look the same on a dashboard.
        self.assertEqual(render.fmt(None), "—")
        self.assertEqual(render.fmt(0), "0")

    def test_thousands_separators(self):
        self.assertEqual(render.fmt(1234567), "1,234,567")

    def test_block_time_converts_unix_to_iso(self):
        snapshot = {"network": {"block_time_unix": 1786000000}}
        self.assertTrue(render.block_time_iso(snapshot).startswith("2026-"))

    def test_missing_block_time_is_a_dash(self):
        self.assertEqual(render.block_time_iso({"network": {}}), "—")

    def test_identifiers_print_without_thousands_separators(self):
        # Epoch 1012 is a name, not a count — "1,012" reads as a quantity.
        self.assertEqual(render.fmt_id(1012), "1012")
        self.assertEqual(render.fmt_id(None), "—")

    def test_sol_amounts_are_compact_in_cards(self):
        # A 9-digit SOL figure wraps the card and cannot be scanned.
        self.assertEqual(render.fmt_sol(434_421_657.36), "434.4M SOL")
        self.assertEqual(render.fmt_sol(12_500.0), "12.5K SOL")
        self.assertEqual(render.fmt_sol(42.5), "42.50 SOL")
        self.assertEqual(render.fmt_sol(None), "—")

    def test_billion_scale_figures_get_their_own_tier(self):
        # Market cap and TVL are USD billions; "$42,919.6M" is the amateur read.
        self.assertEqual(render.fmt_sol(42_919_600_000), "42.92B SOL")
        self.assertEqual(render.fmt_sol(999_999_999), "1,000.0M SOL")

    def test_html_keeps_the_exact_figure_alongside_the_compact_one(self):
        page = render.render_html(load_fixture())
        self.assertIn("600.0M SOL", page)      # compact headline
        self.assertIn("600,000,000", page)     # exact value still present

    def test_markdown_keeps_full_precision(self):
        # The Markdown report is the human-readable record — no rounding there.
        markdown = render.render_markdown(load_fixture())
        self.assertIn("600,000,000", markdown)
        self.assertNotIn("600.0M", markdown)

    def test_epoch_is_not_comma_formatted_in_output(self):
        snapshot = load_fixture()
        snapshot["epoch"]["epoch"] = 1012
        self.assertIn("| Epoch | 1012 |", render.render_markdown(snapshot))
        self.assertIn("Epoch 1012", render.render_html(snapshot))


class TestAnalysisSelection(unittest.TestCase):
    """The anomaly panel must describe the snapshot being rendered.

    Rendering an older snapshot with the newest snapshot's verdict is the
    known_zero-class failure: a confident panel about the wrong moment.
    """

    @staticmethod
    def snap(hour, slot):
        return {
            "collected_at": f"2026-08-05T0{hour}:00:00+00:00",
            "network": {"healthy": True, "slot": slot},
        }

    def test_history_after_the_target_snapshot_is_excluded(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        target = history[3]
        analysis = render.analysis_for(target, history)
        self.assertEqual(analysis["collected_at"], target["collected_at"])
        self.assertEqual(analysis["snapshots_analysed"], 4)

    def test_newest_snapshot_keeps_the_full_history(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        analysis = render.analysis_for(history[-1], history)
        self.assertEqual(analysis["collected_at"], history[-1]["collected_at"])
        self.assertEqual(analysis["snapshots_analysed"], 5)

    def test_snapshot_without_timestamp_falls_back_to_full_history(self):
        history = [self.snap(hour, 100 + hour) for hour in range(5)]
        analysis = render.analysis_for({"network": {}}, history)
        self.assertEqual(analysis["snapshots_analysed"], 5)


if __name__ == "__main__":
    unittest.main()
