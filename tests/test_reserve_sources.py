"""Reservations stay spent when publication fails; source gates are independent."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import reserve_sources


class ReservationWorkflowTests(unittest.TestCase):
    def test_failure_after_reservation_cannot_reuse_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github').mkdir()
            (root / 'snapshots').mkdir()
            (root / 'snapshots/latest.json').write_text(json.dumps({
                'dune': {'execution_ended_at': '2026-09-02T00:00:00Z'},
                'collection_schedule': {
                    'dune': {
                        'last_attempt_at': '2026-09-02T00:00:00+00:00',
                        'last_success_at': '2026-09-02T00:00:00+00:00',
                        'interval_seconds': 86400,
                        'state': 'fresh',
                    },
                    'news': {
                        'last_attempt_at': '2026-09-04T10:00:00+00:00',
                        'last_success_at': '2026-09-04T10:00:00+00:00',
                        'interval_seconds': 21600,
                        'state': 'fresh',
                    },
                },
            }))
            (root / reserve_sources.LEDGERS[0]).write_text('{"version":1,"attempts":{}}')
            (root / reserve_sources.LEDGERS[1]).write_text(json.dumps({
                'version': 1, 'starts_on': '2026-09-03', 'expires_on': '2026-09-17',
                'query_id': '8590950',
                'total_read_limit': 2, 'daily_read_limit': 2,
                'max_rows_per_read': 500, 'reservations': {},
            }))
            (root / reserve_sources.LEDGERS[2]).write_text(json.dumps({
                'version': 1, 'starts_on': '2026-09-03', 'expires_on': '2026-09-17',
                'total_post_limit': 20, 'daily_post_limit': 20, 'reservations': {},
            }))
            env = {'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
                   'DUNE_QUERY_ID': '8590950', 'DUNE_EXECUTION_ENABLED': 'true',
                   'DUNE_PAID_READS_ENABLED': 'true', 'DUNE_API_KEY_PRESENT': 'true',
                   'X_PAID_READS_ENABLED': 'true',
                   'X_BEARER_TOKEN_PRESENT': 'true'}
            now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
            settings = reserve_sources.prepare(root, root / 'tmp', env, now)
            self.assertEqual(settings['DUNE_EXECUTION_ENABLED'], 'true')
            self.assertEqual(settings['DUNE_PAID_READS_ENABLED'], 'true')
            self.assertEqual(settings['X_PAID_READS_ENABLED'], 'true')
            # Simulate death before collection or a failed artifact upload. The
            # next runner gets the committed ledger and a different attempt id.
            with mock.patch('builtins.print'):
                settings = reserve_sources.prepare(root, root / 'tmp2',
                                                   {**env, 'GITHUB_RUN_ATTEMPT': '2'}, now)
            self.assertEqual(settings['DUNE_EXECUTION_ENABLED'], 'false')
            self.assertEqual(settings['DUNE_PAID_READS_ENABLED'], 'false')
            self.assertEqual(settings['X_PAID_READS_ENABLED'], 'false')

    def test_explicit_first_run_and_elapsed_failure_are_due(self):
        now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
        schedules = []
        first_run = reserve_sources.cadence.initial_schedule()
        schedules.append(('first-run', first_run))
        elapsed_failure = reserve_sources.cadence.initial_schedule()
        for source_key, interval in (('dune', 86400), ('news', 21600)):
            stamp = (now - timedelta(seconds=interval)).isoformat()
            elapsed_failure[source_key].update(
                last_attempt_at=stamp,
                last_success_at=None,
                state='failed',
            )
        schedules.append(('elapsed-failure', elapsed_failure))

        for label, schedule in schedules:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'snapshots').mkdir()
                (root / 'snapshots/latest.json').write_text(json.dumps({
                    'dune': {}, 'collection_schedule': schedule,
                }))
                env = {
                    'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
                    'DUNE_QUERY_ID': '8590950', 'DUNE_EXECUTION_ENABLED': 'false',
                    'DUNE_PAID_READS_ENABLED': 'true', 'DUNE_API_KEY_PRESENT': 'true',
                    'X_PAID_READS_ENABLED': 'true', 'X_BEARER_TOKEN_PRESENT': 'true',
                }
                with mock.patch.object(
                    reserve_sources.dune, 'reserve_result_reads', return_value={'reads': 1},
                ) as dune_read, mock.patch.object(
                    reserve_sources.xnews, 'reserve_post_reads', return_value={'posts': 20},
                ) as x_read:
                    settings = reserve_sources.prepare(root, root / 'tmp', env, now)
                dune_read.assert_called_once()
                x_read.assert_called_once()
                self.assertEqual(settings['DUNE_PAID_READS_ENABLED'], 'true')
                self.assertEqual(settings['X_PAID_READS_ENABLED'], 'true')

    def test_paid_reservations_fail_closed_until_valid_cadence_is_due(self):
        now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
        recent_failure = reserve_sources.cadence.initial_schedule()
        for source_key in ('dune', 'news'):
            recent_failure[source_key].update(
                last_attempt_at=(now - timedelta(minutes=15)).isoformat(),
                last_success_at=None,
                state='failed',
            )
        malformed = reserve_sources.cadence.initial_schedule()
        malformed['dune']['last_attempt_at'] = 'not-a-timestamp'
        malformed['news']['interval_seconds'] = 900
        future = reserve_sources.cadence.initial_schedule()
        for source_key in ('dune', 'news'):
            future[source_key].update(
                last_attempt_at=(now + timedelta(minutes=1)).isoformat(),
                last_success_at=None,
                state='failed',
            )

        for label, schedule in (
            ('not-due-after-failure', recent_failure),
            ('malformed', malformed),
            ('future', future),
            ('missing', None),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'snapshots').mkdir()
                snapshot = {'dune': {}}
                if schedule is not None:
                    snapshot['collection_schedule'] = schedule
                (root / 'snapshots/latest.json').write_text(json.dumps(snapshot))
                env = {
                    'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
                    'DUNE_QUERY_ID': '8590950', 'DUNE_EXECUTION_ENABLED': 'true',
                    'DUNE_PAID_READS_ENABLED': 'true', 'DUNE_API_KEY_PRESENT': 'true',
                    'X_PAID_READS_ENABLED': 'true', 'X_BEARER_TOKEN_PRESENT': 'true',
                }
                with mock.patch.object(
                    reserve_sources.dune, 'reserve_execution_attempt',
                ) as dune_execution, mock.patch.object(
                    reserve_sources.dune, 'reserve_result_reads',
                ) as dune_read, mock.patch.object(
                    reserve_sources.xnews, 'reserve_post_reads',
                ) as x_read, mock.patch('builtins.print'):
                    settings = reserve_sources.prepare(root, root / 'tmp', env, now)
                dune_execution.assert_not_called()
                dune_read.assert_not_called()
                x_read.assert_not_called()
                self.assertEqual(settings, {
                    'DUNE_EXECUTION_ENABLED': 'false',
                    'DUNE_PAID_READS_ENABLED': 'false',
                    'X_PAID_READS_ENABLED': 'false',
                })
                self.assertEqual(
                    [path.name for path in (root / 'tmp').iterdir()],
                    ['environment'],
                )

    def test_dune_and_news_cadence_gates_are_independent(self):
        now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
        for due_source in ('dune', 'news'):
            with self.subTest(due_source=due_source), tempfile.TemporaryDirectory() as directory:
                schedule = reserve_sources.cadence.initial_schedule()
                held_source = 'news' if due_source == 'dune' else 'dune'
                schedule[held_source].update(
                    last_attempt_at=(now - timedelta(minutes=15)).isoformat(),
                    last_success_at=(now - timedelta(minutes=15)).isoformat(),
                    state='fresh',
                )
                root = Path(directory)
                (root / 'snapshots').mkdir()
                (root / 'snapshots/latest.json').write_text(json.dumps({
                    'dune': {}, 'collection_schedule': schedule,
                }))
                env = {
                    'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
                    'DUNE_QUERY_ID': '8590950', 'DUNE_EXECUTION_ENABLED': 'false',
                    'DUNE_PAID_READS_ENABLED': 'true', 'DUNE_API_KEY_PRESENT': 'true',
                    'X_PAID_READS_ENABLED': 'true', 'X_BEARER_TOKEN_PRESENT': 'true',
                }
                with mock.patch.object(
                    reserve_sources.dune, 'reserve_result_reads', return_value={'reads': 1},
                ) as dune_read, mock.patch.object(
                    reserve_sources.xnews, 'reserve_post_reads', return_value={'posts': 20},
                ) as x_read, mock.patch('builtins.print'):
                    settings = reserve_sources.prepare(root, root / 'tmp', env, now)
                self.assertEqual(dune_read.call_count, int(due_source == 'dune'))
                self.assertEqual(x_read.call_count, int(due_source == 'news'))
                self.assertEqual(
                    settings['DUNE_PAID_READS_ENABLED'],
                    'true' if due_source == 'dune' else 'false',
                )
                self.assertEqual(
                    settings['X_PAID_READS_ENABLED'],
                    'true' if due_source == 'news' else 'false',
                )

    def test_no_enablement_creates_no_accounting_or_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = reserve_sources.prepare(root, root / 'tmp', {
                'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
            })
            self.assertEqual(settings, {
                'DUNE_EXECUTION_ENABLED': 'false', 'DUNE_PAID_READS_ENABLED': 'false',
                'X_PAID_READS_ENABLED': 'false',
            })
            self.assertEqual([p.name for p in (root / 'tmp').iterdir()], ['environment'])

    def test_missing_credentials_never_reserve_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github').mkdir()
            (root / 'snapshots').mkdir()
            (root / 'snapshots/latest.json').write_text('{"dune": {}}')
            dune_ledger = root / reserve_sources.LEDGERS[0]
            read_ledger = root / reserve_sources.LEDGERS[1]
            x_ledger = root / reserve_sources.LEDGERS[2]
            dune_ledger.write_text('{"version":1,"attempts":{}}')
            read_ledger.write_text(json.dumps({
                'version': 1, 'starts_on': '2026-09-03', 'expires_on': '2026-09-17',
                'query_id': '8590950',
                'total_read_limit': 2, 'daily_read_limit': 2,
                'max_rows_per_read': 500, 'reservations': {},
            }))
            x_ledger.write_text(json.dumps({
                'version': 1, 'starts_on': '2026-09-03', 'expires_on': '2026-09-17',
                'total_post_limit': 20, 'daily_post_limit': 20, 'reservations': {},
            }))
            before = (dune_ledger.read_bytes(), read_ledger.read_bytes(), x_ledger.read_bytes())
            settings = reserve_sources.prepare(root, root / 'tmp', {
                'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1',
                'DUNE_QUERY_ID': '8590950', 'DUNE_EXECUTION_ENABLED': 'true',
                'DUNE_PAID_READS_ENABLED': 'true', 'X_PAID_READS_ENABLED': 'true',
            }, datetime(2026, 9, 4, 18, tzinfo=timezone.utc))
            self.assertEqual(settings, {
                'DUNE_EXECUTION_ENABLED': 'false', 'DUNE_PAID_READS_ENABLED': 'false',
                'X_PAID_READS_ENABLED': 'false',
            })
            self.assertEqual((dune_ledger.read_bytes(), read_ledger.read_bytes(), x_ledger.read_bytes()), before)

    def test_workflow_persists_before_enabling_collection(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/update.yml').read_text()
        start = workflow.index('- name: Reserve paid sources before collection')
        end = workflow.index('- name: Collect live snapshot')
        block = workflow[start:end]
        self.assertLess(block.index('python3 reserve_sources.py'), block.index('git push'))
        self.assertLess(block.index('git push'), block.index('>> "$GITHUB_ENV"'))
        self.assertIn("DUNE_API_KEY_PRESENT: ${{ secrets.DUNE_API_KEY != '' }}", block)
        self.assertIn("DUNE_PAID_READS_ENABLED: ${{ vars.DUNE_PAID_READS_ENABLED || 'false' }}", block)
        self.assertIn('.github/dune-result-read-budget.json', block)
        self.assertIn("X_BEARER_TOKEN_PRESENT: ${{ secrets.X_BEARER_TOKEN != '' }}", block)
        self.assertIn('GITHUB_SHA= python3 collect.py', workflow)


if __name__ == '__main__':
    unittest.main()
