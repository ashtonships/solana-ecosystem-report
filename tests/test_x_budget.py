"""X daily cost-guard tests: charged-post counting and the budget backstop."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import xnews


def _mk_snapshot(tmp: Path, name: str, post_ids) -> None:
    payload = {
        "news": {
            "sources": {
                "x_announcements": {
                    "available": True,
                    "items": [{"id": pid} for pid in post_ids],
                }
            }
        }
    }
    (tmp / name).write_text(json.dumps(payload), encoding="utf-8")


class ChargedPostsToday(unittest.TestCase):
    def test_counts_distinct_posts_from_today_snapshots_only(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _mk_snapshot(tmp, "snapshot-20260903T210000+0000.json",
                         ["1", "2", "2", "3"])
            _mk_snapshot(tmp, "snapshot-20260903T220000+0000.json", ["2", "4"])
            # Yesterday's charges do not count toward today.
            _mk_snapshot(tmp, "snapshot-20260902T120000+0000.json", ["9"])
            # A non-snapshot file is ignored.
            (tmp / "readme.md").write_text("junk", encoding="utf-8")
            charged = xnews._charged_post_ids_today.__wrapped__ if False else None
            # Freeze "today" by asking for the 0903 day directly through the
            # real helper: monkeypatch the prefix via the datetime-free path.
            import xnews as x
            real_now = x.datetime
            class _FakeDT(real_now):
                @classmethod
                def now(cls, tz=None):
                    return real_now(2026, 9, 3, 23, 0, tzinfo=tz)
            x.datetime = _FakeDT
            try:
                charged = x._charged_post_ids_today(snapshots_dir=tmp)
            finally:
                x.datetime = real_now
            self.assertEqual(charged, {"1", "2", "3", "4"})

    def test_missing_dir_returns_empty_set(self):
        import xnews as x
        charged = x._charged_post_ids_today(
            snapshots_dir=Path("/nonexistent/nope"))
        self.assertEqual(charged, set())

    def test_budget_backstop_blocks_fetch_and_states_reason(self):
        import xnews as x
        charged_full = {str(i) for i in range(x.X_DAILY_POST_BUDGET)}
        real_counter = x._charged_post_ids_today
        x._charged_post_ids_today = lambda snapshots_dir=None: charged_full
        try:
            result = x.collect_x_announcements()
        finally:
            x._charged_post_ids_today = real_counter
        self.assertFalse(result["available"])
        self.assertIn("daily X post-read budget reached", result["reason"])
        self.assertIn("100/100", result["reason"])

    def test_under_budget_proceeds_to_fetch(self):
        import xnews as x
        real_counter = x._charged_post_ids_today
        x._charged_post_ids_today = lambda snapshots_dir=None: {"1", "2"}
        real_fetch = x.fetch_announcements
        x.fetch_announcements = lambda timeout=20: [{"id": "5", "author": "solana",
            "text": "hi", "created_at": "2026-09-03T00:00:00.000Z",
            "url": "https://x.com/solana/status/5", "like_count": 0,
            "retweet_count": 0}]
        try:
            result = x.collect_x_announcements()
        finally:
            x._charged_post_ids_today = real_counter
            x.fetch_announcements = real_fetch
        self.assertTrue(result.get("posts"))
        self.assertEqual(result["posts"][0]["id"], "5")


if __name__ == "__main__":
    unittest.main()
