"""Idempotency of the publication path under repeated identical collections.

The release contract requires that a rerun of the same collection cannot
duplicate ledger rows or change committed bytes: facts.jsonl is append-only
and byte-exact across reruns.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts  # noqa: E402


def _provider_source():
    """A snapshot exercising the sliding-window provider benchmark source."""
    return {
        "schema_version": 9,
        "collected_at": "2026-09-02T06:00:00+00:00",
        "growth": {
            "available": True,
            "daily_active_addresses": {
                "available": True,
                "history_available": True,
                "semantic_metric_id": "stablecoin_active_address_provider_range",
                "provider_observations": [
                    {"date": "2026-08-21", "provider": "A", "value": 100},
                    {"date": "2026-08-21", "provider": "B", "value": 140},
                ],
            },
        },
    }


class TestFactsLedgerIdempotency(unittest.TestCase):
    def test_double_append_of_identical_collection_adds_nothing(self):
        with tempfile.TemporaryDirectory(prefix="facts-idempotency-") as tmp:
            path = Path(tmp) / "facts.jsonl"
            pack = facts.snapshot_facts(_provider_source())
            first = facts.append_jsonl(path, pack)
            second = facts.append_jsonl(path, pack)
            self.assertGreater(first, 0)
            self.assertEqual(second, 0)
            serialized = path.read_bytes()
            self.assertTrue(serialized.endswith(b"\n"))
            rows = [json.loads(line) for line in serialized.splitlines()]
            self.assertEqual(len(rows), len(facts.dedupe_facts(rows)))

    def test_additions_report_is_stable_across_reruns(self):
        with tempfile.TemporaryDirectory(prefix="facts-additions-") as tmp:
            path = Path(tmp) / "facts.jsonl"
            pack = facts.snapshot_facts(_provider_source())
            facts.append_jsonl(path, pack)
            self.assertEqual(facts.jsonl_additions(path, pack), [])

    def test_changed_provider_value_appends_exactly_one_revision(self):
        with tempfile.TemporaryDirectory(prefix="facts-revision-") as tmp:
            path = Path(tmp) / "facts.jsonl"
            pack = facts.snapshot_facts(_provider_source())
            facts.append_jsonl(path, pack)
            before = path.read_bytes()
            revised_source = _provider_source()
            revised_source["growth"]["daily_active_addresses"][
                "provider_observations"
            ][0]["value"] = 111
            added = facts.append_jsonl(
                path, facts.snapshot_facts(revised_source)
            )
            self.assertEqual(added, 1)
            self.assertNotEqual(before, path.read_bytes())
            # Old revision retained verbatim as a prefix row set.
            old_values = [
                json.loads(line)
                for line in before.splitlines()
            ]
            kept = [
                json.loads(line)
                for line in path.read_bytes().splitlines()
            ]
            for row in old_values:
                self.assertIn(row, kept)


if __name__ == "__main__":
    unittest.main()
