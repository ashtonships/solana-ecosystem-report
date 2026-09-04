"""Archived X evidence stays old, bounded and separate from current reads."""
import copy
from datetime import datetime, timezone
import unittest

import pipeline


class XArchiveSemantics(unittest.TestCase):
    def source(self):
        return {
            "available": False, "reason": "budget unavailable", "items": [], "item_count": 0,
            "last_known_good": {
                "observed_at": "2026-09-03T12:00:00Z",
                "latest_published": "2026-09-03T10:00:00Z",
                "items": [{
                    "id": "123", "author": "solana", "title": "Release update",
                    "text": "Release update", "published": "2026-09-03T10:00:00Z",
                    "link": "https://x.com/solana/status/123", "like_count": 1,
                    "retweet_count": None,
                }],
            },
        }

    def errors(self, source):
        return pipeline._x_archive_semantic_failures(
            source, datetime(2026, 9, 4, tzinfo=timezone.utc),
        )

    def test_archive_and_legacy_without_archive_remain_valid(self):
        self.assertEqual(self.errors(self.source()), [])
        self.assertEqual(self.errors({"available": True, "items": []}), [])

    def test_current_rows_reuse_the_strict_archive_contract(self):
        items = self.source()["last_known_good"]["items"]
        observed = datetime(2026, 9, 4, tzinfo=timezone.utc)
        self.assertEqual(
            pipeline._x_post_semantic_failures(items, observed, allow_empty=True)[0], [],
        )
        items[0].update(author="evil", id="abc", link="https://evil.example/post",
                        text="x" * 500, like_count=-9, retweet_count=True)
        errors = pipeline._x_post_semantic_failures(items, observed, allow_empty=True)[0]
        for detail in ("id", "author", "link", "text", "like_count", "retweet_count"):
            self.assertTrue(any(detail in error for error in errors), detail)

    def test_current_promotion_dates_scope_and_unknown_fields_are_rejected(self):
        mutations = [
            lambda s: s.update(available=True),
            lambda s: s.update(items=s["last_known_good"]["items"], item_count=1),
            lambda s: s["last_known_good"].update(observed_at="2026-09-05T00:00:00Z"),
            lambda s: s["last_known_good"].update(observed_at="2026-09-03T09:00:00Z"),
            lambda s: s["last_known_good"].update(latest_published="2026-09-03T11:00:00Z"),
            lambda s: s["last_known_good"].update(uncontracted=True),
            lambda s: s["last_known_good"]["items"].extend(copy.deepcopy(s["last_known_good"]["items"]) * 20),
            lambda s: s["last_known_good"]["items"][0].update(author="unlisted"),
            lambda s: s["last_known_good"]["items"][0].update(link="https://example.com/123"),
            lambda s: s["last_known_good"]["items"][0].update(like_count=True),
            lambda s: s["last_known_good"]["items"][0].update(text="a" * 281),
            lambda s: s["last_known_good"]["items"][0].update(uncontracted=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source = self.source()
                mutate(source)
                self.assertTrue(self.errors(source))


if __name__ == "__main__":
    unittest.main()
