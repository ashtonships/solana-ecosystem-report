"""Offline tests for active release/status sources and held content sources.

Parsers are exercised against locally authored synthetic feed/API/archive
responses, never the network. Production-fetch tests mock the network boundary
and prove that future-gated licensed content is represented as unavailable
without requesting its full-repository archive.

The property under test throughout is the three-way distinction a feed panel
has to keep straight:

    unreachable / unparseable  ->  available: false, with a reason
    parsed, zero entries       ->  available: true, items: []
    parsed, entries            ->  available: true, items: [...]

Collapsing the first into the second turns a failed HTTP request into a claim
that the ecosystem has been quiet.
"""

import sys
import os
import unittest
import json
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news  # noqa: E402
import xnews  # noqa: E402

FEEDS = Path(__file__).resolve().parent.parent / "fixtures" / "feeds"


def fixture(name):
    return (FEEDS / name).read_bytes()


EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>tag:github.com,2008:https://github.com/example/repo/releases</id>
  <title>Example release notes</title>
</feed>"""


class TestParsingFeedShapes(unittest.TestCase):
    def test_agave_stable_classification_requires_all_three_conditions(self):
        rows = [
            {"id": 1, "tag_name": "v4.1.0", "name": "v4.1.0", "draft": False,
             "prerelease": False, "published_at": "2026-08-03T12:41:49Z",
             "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0"},
            {"id": 2, "tag_name": "v4.2.0-rc.1", "draft": False,
             "prerelease": False, "published_at": "2026-08-02T12:41:49Z",
             "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0-rc.1"},
            {"id": 3, "tag_name": "v4.3.0", "draft": False,
             "prerelease": True, "published_at": "2026-08-01T12:41:49Z",
             "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.3.0"},
            {"id": 4, "tag_name": "v4.0.0", "draft": True,
             "prerelease": False, "published_at": "2026-07-31T12:41:49Z",
             "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.0.0"},
        ]
        items = news.parse_agave_releases(
            json.dumps(rows).encode(), {"v4.1.0": "c" * 40},
        )
        self.assertEqual([item["stable"] for item in items], [True, False, False, False])
        self.assertEqual([item["id"] for item in items], ["github-release:1", "github-release:2", "github-release:3", "github-release:4"])
        self.assertEqual(items[0]["tag_commit_sha"], "c" * 40)

    def test_alpha_beta_and_rc_tag_suffixes_are_never_stable(self):
        rows = [{"id": index, "tag_name": tag, "draft": False, "prerelease": False,
                 "published_at": f"2026-08-{10 - index:02d}T00:00:00Z",
                 "html_url": f"https://github.com/anza-xyz/agave/releases/tag/{tag}"}
                for index, tag in enumerate(("v4.0.0-alpha.2", "v4.0.0-beta1",
                                              "v4.0.0rc1", "v4.0.0-rc.1+build.5"), 1)]
        self.assertTrue(all(not item["stable"]
                            for item in news.parse_agave_releases(json.dumps(rows).encode())))

    def test_latest_stable_survives_a_prerelease_burst(self):
        rows = [
            {
                "id": index,
                "tag_name": f"v5.0.0-rc.{index}",
                "draft": False,
                "prerelease": True,
                "published_at": f"2026-08-{20 - index:02d}T00:00:00Z",
                "html_url": f"https://github.com/anza-xyz/agave/releases/tag/v5.0.0-rc.{index}",
            }
            for index in range(1, 7)
        ]
        rows.append({
            "id": 99,
            "tag_name": "v4.2.1",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-08-01T00:00:00Z",
            "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.2.1",
        })

        items = news.parse_agave_releases(json.dumps(rows).encode())
        summary = news.summarize_source("agave_releases", json.dumps(rows).encode())

        self.assertEqual(len(items), news.MAX_ITEMS + 1)
        self.assertEqual([item["tag"] for item in items if item["stable"]], ["v4.2.1"])
        self.assertEqual(summary["latest_stable"]["tag"], "v4.2.1")

    def test_generic_rss_is_metadata_only_and_deduplicated(self):
        body = b"""<?xml version='1.0'?><rss><channel>
          <item><title>Update</title><link>https://example.com/update</link>
          <guid>update</guid><pubDate>Mon, 24 Aug 2026 14:19:00 GMT</pubDate>
          <description>Body that must not be retained.</description></item>
          <item><title>Duplicate</title><link>https://example.com/update</link>
          <guid>update</guid><pubDate>Mon, 24 Aug 2026 14:19:00 GMT</pubDate></item>
        </channel></rss>"""
        items = news.parse_feed(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "update")
        self.assertEqual(items[0]["published"], "2026-08-24T14:19:00Z")
        self.assertNotIn("description", items[0])

    def test_simd_commit_titles_are_flattened_to_one_line(self):
        # Commit titles can arrive wrapped in newlines and indentation; a raw
        # one breaks a Markdown table row.
        items = news.parse_atom(fixture("simd-commits.atom"))
        self.assertTrue(items)
        for item in items:
            self.assertNotIn("\n", item["title"])
            self.assertEqual(item["title"], item["title"].strip())

    def test_status_history_parses_and_keeps_its_dates(self):
        items = news.parse_feed(fixture("status-history.atom"))
        self.assertTrue(items)
        # These are historical incidents; the date is the point of the entry.
        self.assertTrue(all(item["published"] for item in items))
        self.assertTrue(all(item["id"] for item in items))

    def test_entries_come_back_newest_first(self):
        items = news.parse_feed(fixture("agave-releases.atom"))
        published = [item["published"] for item in items]
        self.assertEqual(published, sorted(published, reverse=True))

    def test_no_more_than_the_item_cap_is_kept(self):
        many = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">""" + b"".join(
            f"<entry><title>Release {index}</title>"
            f"<link href='https://example.com/{index}'/>"
            f"<updated>2026-08-0{index}T00:00:00Z</updated></entry>".encode()
            for index in range(1, 9)
        ) + b"</feed>"
        self.assertEqual(len(news.parse_feed(many)), news.MAX_ITEMS)


class TestDegradation(unittest.TestCase):
    def test_current_status_and_unresolved_incidents_are_normalized(self):
        summary = {
            "page": {"updated_at": "2026-08-23T20:05:00Z"},
            "status": {"indicator": "minor", "description": "Minor Service Outage"},
        }
        incidents = {"page": {"updated_at": "2026-08-23T20:06:00Z"}, "incidents": [{
            "id": "abc",
            "name": "RPC degradation", "status": "investigating", "impact": "minor",
            "shortlink": "https://status.solana.com/incidents/abc", "created_at": "2026-08-23T19:00:00Z",
            "updated_at": "2026-08-23T20:00:00Z", "resolved_at": None,
        }, {
            "id": "old", "name": "Old incident", "status": "resolved", "impact": "major",
            "shortlink": "https://status.solana.com/incidents/old", "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-02T00:00:00Z", "resolved_at": "2023-01-02T00:00:00Z",
        }, {
            "id": "old", "name": "Duplicate", "status": "resolved", "impact": "major",
            "shortlink": "https://status.solana.com/incidents/old", "updated_at": "2023-01-02T00:00:00Z",
        }]}
        current = news.summarize_current_status(summary, incidents, 1_700_000_000)
        self.assertTrue(current["available"])
        self.assertEqual(current["indicator"], "minor")
        self.assertEqual(current["active_incident_count"], 1)
        self.assertEqual(current["incidents"][0]["name"], "RPC degradation")
        self.assertEqual(len(current["incident_history"]), 2)
        self.assertEqual(current["incident_history"][0]["id"], "abc")
        self.assertFalse(current["history_is_freshness_signal"])
        self.assertEqual(current["observed_at_unix"], 1_700_000_000)
        self.assertEqual(current["summary_source_updated_at"], "2026-08-23T20:05:00Z")
        self.assertEqual(current["incidents_source_updated_at"], "2026-08-23T20:06:00Z")

    def test_current_status_failure_is_independent_and_not_empty_success(self):
        current = news.summarize_current_status(None, None)
        self.assertFalse(current["available"])
        self.assertIsNone(current["active_incident_count"])

    def test_partial_status_does_not_turn_missing_incidents_into_zero(self):
        current = news.summarize_current_status(
            {"status": {"indicator": "none", "description": "All Systems Operational"}},
            None,
        )
        self.assertFalse(current["available"])
        self.assertTrue(current["partial"])
        self.assertIsNone(current["active_incident_count"])

    def test_malformed_incident_rows_do_not_become_zero_active(self):
        current = news.summarize_current_status(
            {"status": {"indicator": "none", "description": "All Systems Operational"}},
            {"incidents": [{"id": "broken", "name": "Missing status and dates"}]},
        )
        self.assertFalse(current["available"])
        self.assertTrue(current["partial"])
        self.assertIsNone(current["active_incident_count"])
        self.assertEqual(current["invalid_incident_count"], 1)

    def test_a_missing_body_is_unavailable_not_empty(self):
        self.assertIsNone(news.parse_feed(None))
        summary = news.summarize_source("agave_releases", None)
        self.assertFalse(summary["available"])
        self.assertIn("unreachable", summary["reason"])
        self.assertNotIn("items", summary)

    def test_malformed_xml_is_unavailable_rather_than_a_crash(self):
        for body in (b"", b"not xml at all", b"<feed><unclosed>"):
            self.assertIsNone(news.parse_feed(body))
        summary = news.summarize_source("network_status", b"<html>404</html>")
        self.assertFalse(summary["available"])

    def test_valid_xml_that_is_not_a_feed_is_rejected(self):
        self.assertIsNone(news.parse_feed(b"<?xml version='1.0'?><html/>"))

    def test_a_feed_with_no_entries_is_available_and_empty_not_failed(self):
        # The distinction that matters: this source answered, and had nothing.
        summary = news.summarize_source("network_status", EMPTY_FEED)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["items"], [])
        self.assertEqual(summary["item_count"], 0)
        self.assertIn("published no entries", summary["reason"])

    def test_one_broken_feed_does_not_take_out_the_others(self):
        section = news.build_news({
            "agave_releases": None,
            "solana_news": {
                "available": True, "partial": False,
                "records": [{
                    "id": "solana-news:update", "title": "Update",
                    "published": "2026-08-24T00:00:00Z",
                    "link": "https://solana.com/news/update",
                }],
            },
            "simd_proposals": None,
            "network_status": b"<garbage",
        })
        self.assertTrue(section["available"])
        self.assertTrue(section["partial"])
        self.assertTrue(section["sources"]["solana_news"]["available"])
        self.assertFalse(section["sources"]["agave_releases"]["available"])
        self.assertFalse(section["sources"]["simd_proposals"]["available"])
        self.assertFalse(section["sources"]["network_status"]["available"])

    def test_normalized_editorial_items_are_stable_and_choose_the_newest_hero(self):
        section = news.build_news({
            "agave_releases": {
                "releases": json.dumps([{
                    "id": 1,
                    "tag_name": "v4.1.0",
                    "name": "Agave v4.1.0",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-08-03T12:41:49Z",
                    "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0",
                }]).encode(),
                "tag_commits": {"v4.1.0": "b" * 40},
            },
            "network_status": (
                b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
                b"<entry><id>status-1</id><title>Network update</title>"
                b"<link href='https://status.solana.com/notices/1'/>"
                b"<updated>2026-08-04T12:00:00Z</updated></entry></feed>"
            ),
        }, held_sources=news.HELD_LICENSED_CONTENT_SOURCES)
        self.assertEqual(
            [item["source_id"] for item in section["items"]],
            ["network_status", "agave_releases"],
        )
        self.assertEqual(section["featured_item_id"], section["items"][0]["id"])
        self.assertEqual(section["items"][0]["category"], "network")
        self.assertEqual(section["items"][1]["category"], "release")
        self.assertIsNone(section["items"][0]["recorded_at"])

    def test_editorial_normalization_drops_drafts_and_off_host_links(self):
        sources = {
            "agave_releases": {
                "available": True,
                "items": [
                    {
                        "id": "github-release:draft", "title": "Draft",
                        "link": "https://github.com/anza-xyz/agave/releases/tag/draft",
                        "published": "2026-08-04T12:00:00Z", "draft": True,
                        "release_channel": "draft",
                    },
                    {
                        "id": "github-release:preview", "title": "Agave preview",
                        "link": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0-beta.1",
                        "published": "2026-08-03T12:00:00Z", "draft": False,
                        "release_channel": "prerelease",
                    },
                    {
                        "id": "github-release:off-host", "title": "Spoofed release",
                        "link": "https://attacker.example/anza-xyz/agave/releases/tag/v9",
                        "published": "2026-08-05T12:00:00Z", "draft": False,
                        "release_channel": "stable",
                    },
                ],
            },
            "network_status": {"available": True, "items": []},
        }
        current = {
            "status_available": True, "indicator": "none",
            "description": "All Systems Operational", "active_incident_count": 0,
            "observed_at_unix": 1_800_000_000,
        }

        items = news.normalize_editorial_items(sources, current)

        self.assertEqual(
            [item["id"] for item in items],
            ["status-summary:1800000000", "github-release:preview"],
        )
        self.assertEqual(news.featured_editorial_item_id(items), "github-release:preview")
        self.assertIn("Prerelease", items[1]["editorial_note"])

    def test_partial_current_status_keeps_the_section_publishable(self):
        current = news.summarize_current_status(
            {"status": {"indicator": "none", "description": "All Systems Operational"}},
            None,
        )
        section = news.build_news({name: None for name in news.SOURCES}, current)
        self.assertTrue(section["available"])
        self.assertTrue(section["partial"])
        self.assertIsNone(section["current_status"]["active_incident_count"])

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
                b"<link href='https://example.com/real'/>"
                b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        items = news.parse_feed(body)
        self.assertEqual([item["title"] for item in items], ["Real one"])
        summary = news.summarize_source("network_status", body)
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["invalid_item_count"], 1)

    def test_a_very_long_title_is_truncated_visibly(self):
        long_title = "x" * 400
        body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>"""
                + long_title.encode()
                + b"</title><link href='https://example.com/long'/>"
                b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        title = news.parse_feed(body)[0]["title"]
        self.assertLessEqual(len(title), news.MAX_TITLE)
        self.assertTrue(title.endswith("…"))

    def test_a_non_https_link_is_dropped_rather_than_rendered(self):
        # A feed is third-party input; a javascript: or http: href does not get
        # to become an anchor on the dashboard.
        for href in (b"javascript:alert(1)", b"http://example.com/x", b"/relative"):
            body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">"""
                    b"<entry><title>Entry</title><link href='" + href + b"'/>"
                    b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
            self.assertIsNone(news.parse_feed(body))

    def test_an_entry_with_no_timestamp_sorts_last_rather_than_disappearing(self):
        body = (b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">"""
                b"<entry><title>Undated</title><link href='https://example.com/undated'/></entry>"
                b"<entry><title>Dated</title>"
                b"<link href='https://example.com/dated'/>"
                b"<updated>2026-08-02T00:00:00Z</updated></entry></feed>")
        items = news.parse_feed(body)
        self.assertEqual([item["title"] for item in items], ["Dated"])


class TestSourceTable(unittest.TestCase):
    def test_source_credentials_are_declared_and_explained(self):
        section = news.build_news({name: None for name in news.SOURCES})
        self.assertTrue(section["requires_api_key"])
        for name, source in section["sources"].items():
            self.assertEqual(source["requires_api_key"], name == "x_announcements")
            self.assertTrue(source["why"].strip())
            self.assertTrue(source["label"].strip())
            self.assertTrue(source["publisher"].strip())

    def test_every_source_url_is_https_and_carries_no_credential(self):
        for source in news.SOURCES.values():
            self.assertTrue(source["url"].startswith("https://"))
            for secret in ("token", "api_key", "apikey", "?key=", "access_token"):
                self.assertNotIn(secret, source["url"].lower())

    def test_primary_source_contracts_are_explicit(self):
        self.assertEqual(news.SOURCES["agave_releases"]["format"], "github_releases")
        self.assertEqual(news.SOURCES["solana_news"]["format"], "solana_content_posts")
        self.assertEqual(news.SOURCES["simd_proposals"]["format"], "solana_content_upgrades")
        self.assertEqual(news.SOURCES["solana_news"]["url"], news.upgrades.SOLANA_COM_HEAD_URL)
        self.assertEqual(news.SOURCES["simd_proposals"]["url"], news.upgrades.SOLANA_COM_HEAD_URL)
        self.assertTrue(news.CURRENT_STATUS_URLS["incidents"].endswith("/incidents.json"))

    def test_invalid_tag_commit_provenance_remains_partial(self):
        body = json.dumps([{
            "id": 1, "tag_name": "v4.1.0", "name": "v4.1.0", "draft": False,
            "prerelease": False, "published_at": "2026-08-03T12:41:49Z",
            "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0",
        }]).encode()
        summary = news.summarize_source(
            "agave_releases", {"releases": body, "tag_commits": {"v4.1.0": "invalid"}},
        )
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["tag_commit_covered_count"], 0)

    def test_malformed_agave_rows_make_a_mixed_source_partial(self):
        rows = [{
            "id": 1, "tag_name": "v4.1.0", "draft": False, "prerelease": False,
            "published_at": "2026-08-03T12:41:49Z",
            "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.1.0",
        }, {
            "id": 2, "tag_name": "v4.2.0", "draft": False, "prerelease": False,
            "published_at": "bad",
            "html_url": "https://github.com/anza-xyz/agave/releases/tag/v4.2.0",
        }]
        summary = news.summarize_source(
            "agave_releases",
            {"releases": json.dumps(rows).encode(), "tag_commits": {"v4.1.0": "a" * 40}},
        )
        self.assertTrue(summary["available"])
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["invalid_item_count"], 1)

    @mock.patch.object(news, "fetch_json", return_value=None)
    @mock.patch.object(news, "fetch", return_value=None)
    def test_default_collection_never_fetches_direct_rss_or_proposal_repo(
        self, fetch, fetch_json,
    ):
        section = news.collect_news(7)

        fetched = {call.args[0] for call in fetch.call_args_list}
        fetched_json = {call.args[0] for call in fetch_json.call_args_list}
        self.assertNotIn("https://solana.com/news/rss.xml", fetched)
        self.assertNotIn(
            "https://api.github.com/repos/solana-foundation/solana-improvement-documents/commits/main",
            fetched_json,
        )
        self.assertNotIn(news.upgrades.SOLANA_COM_HEAD_URL, fetched_json)
        self.assertFalse(any("codeload.github.com" in url for url in fetched))
        self.assertIn(news.SOURCES["agave_releases"]["url"], fetched)
        self.assertIn(news.SOURCES["network_status"]["url"], fetched)
        for name in ("solana_news", "simd_proposals"):
            source = section["sources"][name]
            self.assertFalse(source["available"])
            self.assertIn("transport", source["reason"])
            self.assertIn("acceptance", source["reason"])
            self.assertNotIn("records", source)
            self.assertNotIn("lifecycle_basis", source)

    @mock.patch.object(news, "fetch_agave_releases", return_value={"releases": None})
    @mock.patch.object(news, "fetch", return_value=b"status")
    def test_release_fetch_excludes_held_content_sources(self, fetch, _agave):
        raw = news.fetch_release_sources(7)

        # Rights-safe sources are fetched; the held licensed-content sources
        # (GPL solana-com archive transforms) must stay out of production fetch.
        self.assertEqual(
            set(raw),
            {"agave_releases", "network_status",
             "firedancer_releases", "simd_proposal_metadata",
             "x_announcements"},
        )
        self.assertNotIn("solana_news", raw)
        self.assertNotIn("simd_proposals", raw)
        # network_status is one of the five fetch calls (agave, firedancer,
        # SIMD metadata, X announcements, status).
        fetched_urls = [call.args[0] for call in fetch.call_args_list]
        self.assertIn(news.SOURCES["network_status"]["url"], fetched_urls)
        self.assertEqual(len(fetched_urls), 4)

    @mock.patch.object(news, "fetch_json")
    def test_annotated_agave_tag_is_dereferenced_to_commit(self, fetch_json):
        fetch_json.side_effect = [
            {"object": {"type": "tag", "sha": "a" * 40}},
            {"object": {"type": "commit", "sha": "b" * 40}},
        ]
        self.assertEqual(news.resolve_agave_tag_commit("v4.1.0", 7), "b" * 40)
        self.assertIn("/git/ref/tags/v4.1.0", fetch_json.call_args_list[0].args[0])

    def test_upgrade_summary_retains_editorial_stage_without_simd_status(self):
        upgrade = {
            "id": "solana-upgrade:alpenglow", "slug": "alpenglow", "title": "Alpenglow",
            "stage": "in_development", "published": "2026-06-24T00:00:00Z",
            "link": "https://solana.com/upgrades/alpenglow", "source_commit": "a" * 40,
            "simd_references": [{"identifier": "SIMD-0326", "link": "https://example.com/326"}],
        }
        summary = news.summarize_source("simd_proposals", {
            "available": True, "records": [upgrade], "record_count": 1,
            "document_count": 1, "partial": False, "unparsed_paths": [],
            "source_commit": "a" * 40,
            "source": f"https://github.com/solana-foundation/solana-com/tree/{'a' * 40}/apps/media/content/upgrades",
            "license": "GPL-3.0", "license_url": "https://example.com/license",
            "attribution": "Solana Foundation / solana-foundation/solana-com",
            "note": "official curated upgrade lifecycle; editorial evidence, not on-chain proof",
        })
        self.assertEqual(summary["source_commit"], "a" * 40)
        self.assertEqual(summary["items"][0]["stage"], "in_development")
        self.assertNotIn("status", summary["items"][0]["simd_references"][0])
        self.assertNotIn("proposals", summary)

    def test_the_section_states_that_feed_contents_are_third_party(self):
        section = news.build_news({})
        self.assertIn("not claims", section["note"])

class TestRightsSafeAdditions(unittest.TestCase):
    """Firedancer releases and SIMD watch metadata: parse, degrade, project."""

    FIXTURES = Path(__file__).resolve().parent / "fixtures"

    def test_firedancer_releases_parse_from_saved_fixture(self):
        body = (self.FIXTURES / "firedancer-releases.json").read_bytes()
        items = news.parse_firedancer_releases(body)
        self.assertIsInstance(items, list)
        self.assertTrue(items)
        self.assertIn("tag", items[0])
        self.assertTrue(all(item["published"] for item in items))
        stable = [item for item in items if item["stable"]]
        self.assertTrue(stable)

    def test_firedancer_malformed_body_is_unavailable_not_empty(self):
        self.assertIsNone(news.parse_firedancer_releases(b"not json"))
        self.assertIsNone(news.parse_firedancer_releases(b'{"object": true}'))
        self.assertIsNone(news.parse_firedancer_releases(None))

    def test_simd_metadata_parse_from_saved_fixtures(self):
        documents = {
            "0326-alpenglow": (self.FIXTURES / "simd-0326-alpenglow.md").read_bytes(),
            "0525-reduce-slot-times": (
                self.FIXTURES / "simd-0525-reduce-slot-times.md"
            ).read_bytes(),
        }
        items = news.parse_simd_proposal_metadata(documents)
        by_id = {item["identifier"]: item for item in items}
        self.assertTrue(by_id["0326-alpenglow"]["available"])
        self.assertEqual(by_id["0326-alpenglow"]["status"], "Review")
        self.assertEqual(by_id["0525-reduce-slot-times"]["status"], "Draft")
        self.assertTrue(by_id["0326-alpenglow"]["link"].startswith("https://"))

    def test_simd_metadata_degrades_per_proposal_without_titles(self):
        documents = {"0326-alpenglow": None, "0525-reduce-slot-times": b"no frontmatter"}
        items = news.parse_simd_proposal_metadata(documents)
        self.assertFalse(any(item["available"] for item in items))
        self.assertTrue(all(item["reason"] for item in items))
        # And the source summary degrades to unavailable with a reason —
        # an optional-source outage never publishes contract-violating items.
        summary = news.summarize_source(
            "simd_proposal_metadata", documents,
        )
        self.assertFalse(summary["available"])
        self.assertIn("reason", summary)

    def test_build_news_keeps_new_sources_and_the_section_independent(self):
        firedancer = news.parse_firedancer_releases(
            (self.FIXTURES / "firedancer-releases.json").read_bytes(),
        )
        simd = [
            item for item in news.parse_simd_proposal_metadata({
                "0326-alpenglow": (self.FIXTURES / "simd-0326-alpenglow.md").read_bytes(),
                "0525-reduce-slot-times": (
                    self.FIXTURES / "simd-0525-reduce-slot-times.md").read_bytes(),
            })
            if item.get("available")
        ]
        raw = {
            "agave_releases": None, "network_status": None,
            "firedancer_releases": (self.FIXTURES / "firedancer-releases.json").read_bytes(),
            "simd_proposal_metadata": {
                "0326-alpenglow": (self.FIXTURES / "simd-0326-alpenglow.md").read_bytes(),
                "0525-reduce-slot-times": (
                    self.FIXTURES / "simd-0525-reduce-slot-times.md").read_bytes(),
            },
        }
        built = news.build_news(raw, None, news.HELD_LICENSED_CONTENT_SOURCES)
        # Optional sources succeed; section available; core sources failed so
        # the section honestly reports partial.
        self.assertTrue(built["available"])
        self.assertTrue(built["partial"])
        self.assertIn("firedancer_releases", built["sources"])
        self.assertIn("simd_proposal_metadata", built["sources"])
        editorial_sources = {item["source_id"] for item in built["items"]}
        self.assertIn("firedancer_releases", editorial_sources)

    def test_optional_source_failure_does_not_flip_section_partial_alone(self):
        # Everything core unavailable (as held/failing), only optional sources
        # failing too: available stays False, and no exception escapes.
        built = news.build_news({}, None, news.HELD_LICENSED_CONTENT_SOURCES)
        self.assertFalse(built["available"])


class TestXAnnouncements(unittest.TestCase):
    """Allowlisted, capped, dedup-friendly X announcements (pay-per-use)."""

    FIXTURES = Path(__file__).resolve().parent / "fixtures"

    def _timeline_fixture(self):
        return json.loads((self.FIXTURES / "x-user-timeline.json").read_bytes())

    def test_parse_shapes_and_bounding(self):
        posts = self._timeline_fixture()["data"]
        items = xnews.parse_announcements([
            {**posts[0], "author": "SolanaFndn",
             "url": "https://x.com/SolanaFndn/status/" + posts[0]["id"]},
            "junk",
            {"id": None},
        ])
        # Only well-formed posts survive; junk rows are dropped.
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["author"], "SolanaFndn")
        # parse_announcements is a pure shaper: URL enforcement happens at
        # the editorial layer, so raw passthrough rows may carry link=None.
        shaped = [item for item in items if item["link"]]
        self.assertTrue(all(item["link"].startswith("https://x.com/") for item in shaped))

    def test_missing_token_degrades_with_reason(self):
        with mock.patch.dict(os.environ, {xnews.X_BEARER_TOKEN_ENV: ""}):
            with self.assertRaises(xnews.XSourceUnavailable):
                xnews.fetch_announcements(now_unix=1_000_000)

    def test_cap_never_exceeded(self):
        # MAX_POSTS is the cost ceiling; the fetcher must stop at it.
        self.assertLessEqual(xnews.MAX_POSTS, 20)
        self.assertLessEqual(len(xnews.X_ACCOUNT_ALLOWLIST), 6)

    def test_summarize_reports_named_unavailable_states(self):
        summary = news.summarize_source(
            "x_announcements", {"available": False,
                                "reason": "X API rate limit or credit cap reached"},
        )
        self.assertFalse(summary["available"])
        self.assertIn("credit", summary["reason"])
        # Contract fields required of every available source, present here too:
        ok = news.summarize_source("x_announcements", {"posts": [
            {"id": "1", "author": "solana", "text": "hello",
             "created_at": "2026-09-02T18:00:00.000Z",
             "url": "https://x.com/solana/status/1",
             "like_count": 1, "retweet_count": 0},
        ]})
        self.assertTrue(ok["available"])
        self.assertFalse(ok["partial"])
        self.assertEqual(ok["invalid_item_count"], 0)
        empty = news.summarize_source("x_announcements", {"posts": []})
        self.assertTrue(empty["available"])
        self.assertEqual(empty["items"], [])
        self.assertIsNone(empty["latest_published"])
        previous = news.build_news({"x_announcements": {"posts": [{
            "id": "9", "author": "solana", "text": "Earlier",
            "created_at": "2026-09-02T18:00:00Z",
            "url": "https://x.com/solana/status/9",
        }]}})
        current = news.build_news({"x_announcements": {"posts": []}})
        news.apply_last_known_good(current, previous, "2026-09-03T01:00:00Z")
        self.assertNotIn("last_known_good", current["sources"]["x_announcements"])

    def test_failed_x_reads_archive_original_observation_without_promoting_it(self):
        post = {"id": "9", "author": "solana", "text": "Recorded announcement",
                "created_at": "2026-09-02T18:00:00Z", "url": "https://x.com/solana/status/9"}
        previous = news.build_news({"x_announcements": {"posts": [post]}})
        failed = news.build_news({"x_announcements": {"available": False, "reason": "budget paused"}})
        archived = news.apply_last_known_good(failed, previous, "2026-09-03T01:00:00Z")
        source = archived["sources"]["x_announcements"]
        self.assertFalse(source["available"])
        self.assertEqual(source["items"], [])
        self.assertEqual(source["last_known_good"]["observed_at"], "2026-09-03T01:00:00Z")
        self.assertFalse(any(item["source_id"] == "x_announcements" for item in archived["items"]))
        later = news.build_news({})
        news.apply_last_known_good(later, archived, "2026-09-04T01:00:00Z")
        self.assertEqual(later["sources"]["x_announcements"]["last_known_good"], source["last_known_good"])
        self.assertNotIn("last_known_good", previous["sources"]["x_announcements"])

    def test_x_titles_keep_words_entities_and_link_only_citations(self):
        title = news._x_title("A &amp; B " + "complete " * 30, "solana")
        self.assertNotIn("&amp", title)
        self.assertLessEqual(len(title), news.MAX_TITLE)
        self.assertTrue(title.endswith("complete…"))
        self.assertEqual(news._x_title("Ship 🤝 now", "solana"), "Ship now")
        self.assertEqual(news._x_title("https://t.co/abc", "solana"),
                         "Announcement from @solana")

    def test_editorial_items_derive_title_from_post_text(self):
        raw = {
            "agave_releases": None, "network_status": None,
            "firedancer_releases": None, "simd_proposal_metadata": {},
            "x_announcements": {"posts": [
                {"id": "9", "author": "SolanaFndn",
                 "text": "  Network upgrade   scheduled  ",
                 "created_at": "2026-09-02T18:00:00.000Z",
                 "url": "https://x.com/SolanaFndn/status/9",
                 "like_count": 3, "retweet_count": 1},
            ]},
        }
        built = news.build_news(raw, None, news.HELD_LICENSED_CONTENT_SOURCES)
        x_items = [i for i in built["items"] if i["source_id"] == "x_announcements"]
        self.assertEqual(len(x_items), 1)
        self.assertEqual(x_items[0]["title"], "Network upgrade scheduled")
        self.assertTrue(x_items[0]["canonical_url"].startswith("https://x.com/"))


if __name__ == "__main__":
    unittest.main()
