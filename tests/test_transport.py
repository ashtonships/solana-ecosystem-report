"""Offline checks for shared network-response bounds."""

import sys
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transport  # noqa: E402


class TestReadBounded(unittest.TestCase):
    def test_accepts_limit_and_rejects_one_byte_over(self):
        self.assertEqual(transport.read_bounded(BytesIO(b"abc"), limit=3), b"abc")
        with self.assertRaisesRegex(ValueError, "response exceeds 3 bytes"):
            transport.read_bounded(BytesIO(b"abcd"), limit=3)


if __name__ == "__main__":
    unittest.main()
