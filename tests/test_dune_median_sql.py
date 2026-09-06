"""Exercise the production median CTEs with SQLite's equivalent rank arithmetic.

Only the Dune timestamp expressions are adapted. This checks exact aggregation
on synthetic rows; it does not establish Dune execution cost or live completeness.
"""

from contextlib import closing
from pathlib import Path
import random
import sqlite3
import statistics
import unittest


SQL_PATH = Path(__file__).resolve().parents[1] / "docs/dune/solana-activity.sql"


class TestDailyMedianSQL(unittest.TestCase):
    def query(self, rows):
        source = SQL_PATH.read_text()
        ctes = source[source.index("non_vote_fee_histogram AS ("):
                      source.index("transaction_fee_days AS (")].rstrip().rstrip(",")
        ctes = ctes.replace("CAST(DATE_TRUNC('day', block_time) AS DATE)",
                            "substr(block_time, 1, 10)")
        date = "CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE)"
        ctes = ctes.replace(date + " - INTERVAL '2' DAY", "'2026-09-04'")
        ctes = ctes.replace(date, "'2026-09-06'")
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("ATTACH DATABASE ':memory:' AS solana")
            db.execute("CREATE TABLE solana.transactions (block_time TEXT, fee INTEGER, success INTEGER)")
            db.executemany("INSERT INTO solana.transactions VALUES (?, ?, ?)", rows)
            return db.execute("WITH " + ctes + " SELECT day, value, sample_count FROM non_vote_fee_medians ORDER BY day").fetchall()

    def test_exact_even_odd_duplicate_zero_and_precision_boundary_populations(self):
        rng = random.Random(20260906)
        populations = [[0], [0, 1], [5000, 5001], [1, 1, 1, 20],
                       [1, 2, 3, 4, 5], [2**52 - 2, 2**52 - 1]]
        populations += [[rng.randrange(20) for _ in range(n)] for n in range(1, 60)]
        for fees in populations:
            with self.subTest(fees=fees):
                rows = [("2026-09-05T12:00:00", fee, index % 2)
                        for index, fee in enumerate(fees)]
                self.assertEqual(self.query(rows), [("2026-09-05", statistics.median(fees), len(fees))])

    def test_one_invalid_fee_withholds_whole_day_without_hiding_valid_other_day(self):
        for bad in (None, -1, 2**52):
            with self.subTest(bad=bad):
                rows = [("2026-09-04T12:00:00", 7, 0),
                        ("2026-09-05T12:00:00", 5000, 1),
                        ("2026-09-05T12:00:01", bad, 0)]
                self.assertEqual(self.query(rows), [("2026-09-04", 7, 1)])

    def test_only_completed_two_day_window_and_both_transaction_outcomes(self):
        rows = [("2026-09-03T23:59:59", 99, 1),
                ("2026-09-04T00:00:00", 0, 0),
                ("2026-09-05T23:59:58", 5000, 1),
                ("2026-09-05T23:59:59", 5001, 0),
                ("2026-09-06T00:00:00", 99, 1)]
        self.assertEqual(self.query(rows), [("2026-09-04", 0, 1), ("2026-09-05", 5000.5, 2)])
        self.assertEqual(self.query([]), [])


if __name__ == "__main__":
    unittest.main()
