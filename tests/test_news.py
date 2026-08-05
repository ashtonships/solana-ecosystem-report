"""Offline tests for the releases and announcements feeds.

The parser is exercised against **committed fixtures captured from the real
feeds**, never against the network — the same rule the rest of the suite
follows. `fixtures/feeds/` holds three entries from each live feed, trimmed of
their bodies, so a shape change upstream shows up as a failing test rather
than as a quietly empty panel in production.

The property under test throughout is the three-way distinction a feed panel
has to keep straight:

    unreachable / unparseable  ->  available: false, with a reason
    parsed, zero entries       ->  available: true, items: []
    parsed, entries            ->  available: true, items: [...]

Collapsing the first into the second turns a failed HTTP request into a claim
that the ecosystem has been quiet.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news  # noqa: E402

FEEDS = Path(__file__).resolve().parent.parent / "fixtures" / "feeds"


def fixture(name):
    return (FEEDS / name).read_bytes()


EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>tag:github.com,2008:https://github.com/example/repo/releases</id>
  <title>Example release notes</title>
</feed>"""


class TestParsingRealFeedShapes(unittest.TestCase):
    def test_agave_releases_parse(self):
        items = news.parse_atom(fixture("agave-releases.atom"))
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertTrue(item["title"])
            self.assertTrue(item["link"].startswith("https://github.com/anza-xyz/agave"))
            self.assertTrue(item["published"].endswith("Z"))

    def test_simd_commit_titles_are_flattened_to_one_line(self):
        # GitHub commit titles arrive wrapped in newlines and indentation; a
        # raw one breaks a Markdown table row.
        items = news.parse_atom(fixture("simd-commits.atom"))
        self.assertTrue(items)
        for item in items:
            self.assertNotIn("\n", item["title"])
            self.assertEqual(item["title"], item["title"].strip())

    def test_status_history_parses_and_keeps_its_dates(self):
        items = news.parse_atom(fixture("status-history.atom"))
        self.assertTrue(items)
        # These are historical incidents; the date is the point of the entry.
        self.assertTrue(all(item["published"] for item in items))

    def test_entries_come_back_newest_first(self):
        items = news.parse_atom(fixture("agave-releases.atom"))
        published = [item["published"] for item in items]
        self.assertEqual(published, sorted(published, reverse=True))

    def test_no_more_than_the_item_cap_is_kept(self):
        many = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">""" + b"".join(
            f"<entry><title>Release {index}</title>"
            f"<updated>2026-08-0{index}T00:00:00Z</updated></entry>".encode()
            for index in range(1, 9)
        ) + b"</feed>"
        self.assertEqual(len(news.parse_atom(many)), news.MAX_ITEMS)


class TestDegradation(unittest.TestCase):
    def test_a_missing_body_is_unavailable_not_empty(self):
        self.assertIsNone(news.parse_atom(None))
        summary = news.summarize_source("agave_releases", None)
        self.assertFalse(summary["available"])
        self.assertIn("unreachable", summary["reason"])
        self.assertNotIn("items", summary)

    def test_malformed_xml_is_unavailable_rather_than_a_crash(self):
        for body in (b"", b"not xml at all", b"<feed><unclosed>"):
            self.assertIsNone(news.parse_atom(body))
        summary = news.summarize_source("network_status", b"<html>404</html>")
        self.assertFalse(summary["available"])

    def test_valid_xml_that_is_not_a_feed_is_rejected(self):
        self.assertIsNone(news.parse_atom(b"<?xml version='1.0'?><rss><channel/></rss>"))

    def test_a_feed_with_no_entries_is_available_and_empty_not_failed(self):
        # The distinction that matters: this source answered, and had nothing.
        summary = news.summarize_source("simd_proposals", EMPTY_FEED)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["items"], [])
        self.assertEqual(summary["item_count"], 0)
        self.assertIn("published no entries", summary["reason"])

    def test_one_broken_feed_does_not_take_out_the_others(self):
        section = news.build_news({
            "agave_releases": fixture("agave-releases.atom"),
            "simd_proposals": None,
            "network_status": b"<garbage",
        })
        self.assertTrue(section["available"])
        self.assertTrue(section["sources"]["agave_releases"]["available"])
        self.assertFalse(section["sources"]["simd_proposals"]["available"])
        self.assertFalse(section["sources"]["network_status"]["available"])

    def test_every_source_failing_marks_the_whole_section_unavailable(self):
        section = news.build_news({name: None for name in news.SOURCES})
        self.assertFalse(section["available"])
        for source in section["sources"].values():
            self.assertFalse(source["available"])

    def test_a_collector_that_skipped_news_still_builds_a_section(self):
        section = news.build_news({})
        self.assertFalse(section["available"])
        self.assertEqual(len(section["sources"]), len(news.SOURCES))


class TestEntrySanitising(unittest.TestCase):
    def test_an_untitled_entry_is_dropped_rather_than_shown_blank(self):
        body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">"""
                b"<entry><updated>2026-08-01T00:00:00Z</updated></entry>"
                b"<entry><title>Real one</title>"
                b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        items = news.parse_atom(body)
        self.assertEqual([item["title"] for item in items], ["Real one"])

    def test_a_very_long_title_is_truncated_visibly(self):
        long_title = "x" * 400
        body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>"""
                + long_title.encode()
                + b"</title><updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        title = news.parse_atom(body)[0]["title"]
        self.assertLessEqual(len(title), news.MAX_TITLE)
        self.assertTrue(title.endswith("…"))

    def test_a_non_https_link_is_dropped_rather_than_rendered(self):
        # A feed is third-party input; a javascript: or http: href does not get
        # to become an anchor on the dashboard.
        for href in (b"javascript:alert(1)", b"http://example.com/x", b"/relative"):
            body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">"""
                    b"<entry><title>Entry</title><link href='" + href + b"'/>"
                    b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
            self.assertIsNone(news.parse_atom(body)[0]["link"])

    def test_an_entry_with_no_timestamp_sorts_last_rather_than_disappearing(self):
        body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">"""
                b"<entry><title>Undated</title></entry>"
                b"<entry><title>Dated</title>"
                b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        items = news.parse_atom(body)
        self.assertEqual([item["title"] for item in items], ["Dated", "Undated"])
        self.assertIsNone(items[1]["published"])


class TestSourceTable(unittest.TestCase):
    def test_every_source_is_declared_keyless_and_explained(self):
        section = news.build_news({name: None for name in news.SOURCES})
        self.assertFalse(section["requires_api_key"])
        for source in section["sources"].values():
            self.assertFalse(source["requires_api_key"])
            self.assertTrue(source["why"].strip())
            self.assertTrue(source["label"].strip())
            self.assertTrue(source["publisher"].strip())

    def test_every_source_url_is_https_and_carries_no_credential(self):
        for source in news.SOURCES.values():
            self.assertTrue(source["url"].startswith("https://"))
            for secret in ("token", "api_key", "apikey", "?key=", "access_token"):
                self.assertNotIn(secret, source["url"].lower())

    def test_the_section_states_that_feed_contents_are_third_party(self):
        section = news.build_news({})
        self.assertIn("not claims", section["note"])


if __name__ == "__main__":
    unittest.main()
