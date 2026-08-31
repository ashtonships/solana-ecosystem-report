"""Focused contracts for the scheduler-ready release artwork wrapper."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import generate_weekly_release_art as release_art


class TestWeeklyReleaseArt(unittest.TestCase):
    def test_selects_newest_releases_and_builds_exact_prompt(self):
        snapshot = {"news": {"sources": {"agave_releases": {"items": [
            {"tag": "v4.3.0-beta.1", "published": "2026-08-21T12:47:32Z"},
            {"tag": "not-a-release", "published": "2026-08-22T00:00:00Z"},
            {"tag": "v4.3.0-beta.2", "published": "2026-08-21T14:34:51Z"},
        ]}}}}
        self.assertEqual(
            [row["tag"] for row in release_art.recorded_releases(snapshot)],
            ["v4.3.0-beta.2", "v4.3.0-beta.1"],
        )
        prompt = release_art.release_prompt("v4.3.0-beta.1")
        self.assertIn('Text (verbatim): "BETA 01" and "ANZA / v4.3.0"', prompt)
        self.assertIn("3840x2160", prompt)

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(Path(release_art.__file__)), "--snapshot", str(snapshot_path),
                "--out-dir", str(Path(temp_dir) / "art"), "--dry-run",
            ], check=True, capture_output=True, text=True)
        self.assertIn("BETA 02", result.stdout)


if __name__ == "__main__":
    unittest.main()
