"""Regression: the degraded Dune path (stale result, paid re-execution
fails) attaches last_known_good, and the snapshot must still pass the
public projection/verifier. PR #44's run failed because
'last_known_good' was missing from the parent dune contract while
'dune.last_known_good' had a shape — projection stripped the key and
verify_release flagged it as a first offender."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import render
import verify_release


class DuneLastKnownGoodContract(unittest.TestCase):
    def _stale_snapshot_with_lkg(self) -> dict:
        latest = json.loads(subprocess.run(
            ["git", "show", "origin/main:snapshots/latest.json"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        ).stdout)
        latest["dune"] = {
            "available": False,
            "requires_api_key": True,
            "reason": "dune execution failed",
            "query_id": "8590950",
            "query_url": "https://dune.com/queries/8590950",
            "source_url": "https://api.dune.com/api/v1/query/8590950/results",
            "columns": [],
            "state": "stale",
            "last_known_good": {
                "query_id": "8590950",
                "query_url": "https://dune.com/queries/8590950",
                "source_url": "https://api.dune.com/api/v1/query/8590950/results",
                "execution_id": "01ABC",
                "execution_started_at": "2026-09-03T01:18:55.954939Z",
                "execution_ended_at": "2026-09-03T01:20:01.000000Z",
                "age_seconds": 90000,
                "row_count": 175,
                "datapoint_count": 175,
                "result_sha256": "a" * 64,
                "execution_started_at_parsed": "2026-09-03T01:18:55+00:00",
                "execution_ended_at_parsed": "2026-09-03T01:20:01+00:00",
            },
        }
        return latest

    def test_projection_keeps_contracted_last_known_good(self):
        snapshot = self._stale_snapshot_with_lkg()
        projected = render.project_public_envelope(snapshot)
        self.assertIn("last_known_good", projected["dune"])
        self.assertEqual(projected["dune"]["last_known_good"]["row_count"], 175)
        self.assertEqual(projected, snapshot)

    def test_lkg_extra_key_is_still_rejected(self):
        snapshot = self._stale_snapshot_with_lkg()
        snapshot["dune"]["last_known_good"]["sneaky"] = True
        projected = render.project_public_envelope(snapshot)
        self.assertNotIn("sneaky", projected["dune"]["last_known_good"])
        self.assertNotEqual(projected, snapshot)

    def test_lkg_dict_keys_match_contract_exactly(self):
        import dune
        payload = {
            "execution_id": "01ABC",
            "execution_started_at": "2026-09-03T01:18:55.954939Z",
            "execution_ended_at": "2026-09-03T01:20:01.000000Z",
            "result": {"metadata": {"row_count": 175, "datapoint_count": 175},
                       "rows": [{"a": 1}]},
        }
        lkg = dune._last_known_good("8590950", "https://dune.com/queries/8590950",
                                    "https://api.dune.com/api/v1/query/8590950/results",
                                    payload, 90000)
        contracted = render.PUBLIC_SCHEMA_OVERRIDES[9]["dune.last_known_good"]
        self.assertEqual(frozenset(lkg.keys()), contracted)


if __name__ == "__main__":
    unittest.main()
