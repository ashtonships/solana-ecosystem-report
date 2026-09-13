"""Exercise the actual workflow notification shell with an offline gh double."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class FailureNotificationTests(unittest.TestCase):
    def run_notification(self, existing_body=None):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/update.yml').read_text()
        job = workflow.split('  notify-failure:', 1)[1]
        script = textwrap.dedent(job.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gh'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['GH_CALLS'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
if args[:2] == ['run', 'view']:
    print('Push committed snapshot' if '.steps' in args[-1] else 'update')
elif args[:2] == ['issue', 'list']:
    print('71' if os.environ['ISSUE_EXISTS'] == '1' else '')
elif args[:2] == ['issue', 'view']:
    print(os.environ['EXISTING_BODY'])
''')
            fake.chmod(0o755)
            calls_path = root / 'calls.jsonl'
            environment = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                               GH_CALLS=str(calls_path),
                               ISSUE_EXISTS='1' if existing_body is not None else '0',
                               EXISTING_BODY=existing_body or '',
                               GITHUB_REPOSITORY='example/report', GITHUB_RUN_ID='123',
                               RUN_URL='https://github.com/example/report/actions/runs/123',
                               TITLE='[solana-ecosystem-report] scheduled update failed')
            result = subprocess.run(['bash', '-c', script], env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return [json.loads(line) for line in calls_path.read_text().splitlines()]

    def test_first_failure_creates_issue(self):
        calls = self.run_notification()
        self.assertEqual(sum(c[:2] == ['issue', 'create'] for c in calls), 1)

    def test_unchanged_failure_does_not_write_or_comment(self):
        calls = self.run_notification(
            'Old timestamp and run\n- **Failing jobs:** update\n'
            '- **Failing steps:** Push committed snapshot\n')
        self.assertFalse(any(c[:2] in [['issue', 'create'], ['issue', 'edit'],
                                     ['issue', 'comment']] for c in calls))

    def test_changed_failure_updates_existing_issue(self):
        calls = self.run_notification(
            '- **Failing jobs:** update\n- **Failing steps:** Collect live snapshot\n')
        self.assertEqual(sum(c[:2] == ['issue', 'edit'] for c in calls), 1)
        self.assertFalse(any(c[:2] == ['issue', 'comment'] for c in calls))


class HistoricalVerificationTests(unittest.TestCase):
    def test_only_code_checks_use_snapshot_clock(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/update.yml').read_text()
        verify, publication = workflow.split('  update:', 1)
        self.assertEqual(verify.count('--now "$COMMITTED_SNAPSHOT_TIMESTAMP"'), 2)
        self.assertNotIn('COMMITTED_SNAPSHOT_TIMESTAMP', publication)
        self.assertIn('--max-age-seconds 25200 --now "$RUN_TIMESTAMP"', publication)
        self.assertIn('--now "$RUN_TIMESTAMP"', publication)
