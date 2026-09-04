"""Paid reads reserve their full possible cost before transport or publication."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import xnews

NOW = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)


class XBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name) / 'budget.json'
        self.receipt = Path(self.directory.name) / 'receipt.json'
        self.policy = {
            'version': 1, 'starts_on': '2026-09-03', 'expires_on': '2026-09-17',
            'total_post_limit': 200, 'daily_post_limit': 100, 'reservations': {},
        }
        self.write_policy()
        self.env = {'X_PAID_READS_ENABLED': 'true', 'X_BEARER_TOKEN': 'test-only',
                    'X_READ_LEDGER': str(self.ledger), 'X_READ_RECEIPT': str(self.receipt),
                    'GITHUB_ACTIONS': 'true', 'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1'}

    def write_policy(self):
        self.ledger.write_text(json.dumps(self.policy))

    def reserve(self, token='42:1', now=NOW):
        receipt = xnews.reserve_post_reads(self.ledger, token, now)
        self.receipt.write_text(json.dumps(receipt))
        return receipt

    def test_99_reserved_posts_cannot_trigger_a_twenty_post_request(self):
        self.policy['daily_post_limit'] = 99
        self.write_policy()
        for i in range(5):
            self.reserve(str(i))
        self.policy = json.loads(self.ledger.read_text())
        self.policy['daily_post_limit'] = 100
        self.write_policy()
        with self.assertRaisesRegex(xnews.XSourceUnavailable, '10-post search minimum'):
            self.reserve('sixth')
        self.assertEqual(sum(r['posts'] for r in self.policy['reservations'].values()), 99)

    def test_total_limit_does_not_reset_the_next_day(self):
        self.policy['total_post_limit'] = 30
        self.write_policy()
        self.assertEqual(self.reserve('first')['posts'], 20)
        tomorrow = NOW.replace(day=5)
        self.assertEqual(self.reserve('second', tomorrow)['posts'], 10)
        with self.assertRaises(xnews.XSourceUnavailable):
            self.reserve('third', NOW.replace(day=6))

    def test_missing_corrupt_or_unapproved_ledger_fails_closed(self):
        for payload in (None, 'invalid json', '{}'):
            with self.subTest(payload=payload):
                if payload is None:
                    self.ledger.unlink(missing_ok=True)
                else:
                    self.ledger.write_text(payload)
                with self.assertRaises(xnews.XSourceUnavailable):
                    self.reserve()
        self.write_policy()
        with mock.patch.dict(os.environ, {**self.env, 'X_PAID_READS_ENABLED': ''}), \
                mock.patch('xnews._request') as request:
            result = xnews.collect_x_announcements()
        self.assertFalse(result['available'])
        request.assert_not_called()

    def test_invalid_limits_and_receipts_fail_closed(self):
        for field, value in (('version', True), ('total_post_limit', -1), ('daily_post_limit', 101),
                             ('total_post_limit', True), ('reservations', {'42:1': {}})):
            with self.subTest(field=field, value=value):
                original = self.policy[field]
                self.policy[field] = value
                self.write_policy()
                with self.assertRaises(xnews.XSourceUnavailable):
                    self.reserve()
                self.policy[field] = original

    def test_expired_policy_and_replayed_reservation_are_rejected(self):
        self.reserve()
        with self.assertRaisesRegex(xnews.XSourceUnavailable, 'already reserved'):
            self.reserve()
        with self.assertRaisesRegex(xnews.XSourceUnavailable, 'inactive'):
            self.reserve('later', NOW.replace(day=17))

    def test_failed_request_consumes_receipt_and_keeps_durable_debit(self):
        self.reserve()
        with mock.patch.dict(os.environ, self.env), \
                mock.patch('xnews._request', side_effect=xnews.XSourceUnavailable('timeout')) as request:
            with self.assertRaisesRegex(xnews.XSourceUnavailable, 'timeout'):
                xnews.fetch_announcements(now_unix=int(NOW.timestamp()))
            with self.assertRaisesRegex(xnews.XSourceUnavailable, 'unused, committed'):
                xnews.fetch_announcements(now_unix=int(NOW.timestamp()))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(json.loads(self.ledger.read_text())['reservations']['42:1']['posts'], 20)

    def test_wrong_run_or_day_never_reaches_transport(self):
        self.reserve()
        for env, now in (({**self.env, 'GITHUB_RUN_ATTEMPT': '2'}, NOW),
                         (self.env, NOW.replace(day=5))):
            with mock.patch.dict(os.environ, env), mock.patch('xnews._request') as request:
                with self.assertRaises(xnews.XSourceUnavailable):
                    xnews.fetch_announcements(now_unix=int(now.timestamp()))
            request.assert_not_called()

    def test_local_lock_prevents_concurrent_or_ambiguous_debits(self):
        self.ledger.with_name('budget.json.lock').touch()
        with self.assertRaisesRegex(xnews.XSourceUnavailable, 'another process'):
            self.reserve()
        self.assertEqual(json.loads(self.ledger.read_text())['reservations'], {})

    def test_requested_count_respects_remaining_allowance_before_row_filtering(self):
        self.policy['total_post_limit'] = 13
        self.write_policy()
        self.reserve()
        body = {'data': [
            {'id': '1', 'author_id': '9', 'text': 'Good &amp; clear 🤝', 'created_at': '2026-09-04T17:00:00Z',
             'public_metrics': {'like_count': -1, 'repost_count': True}},
            {'id': '../../bad', 'author_id': '9', 'text': 'bad', 'created_at': '2026-09-04T17:00:00Z'},
            {'id': '3', 'author_id': '9', 'text': 'future', 'created_at': '2026-09-05T17:00:00Z'},
            {'id': '4', 'author_id': [], 'text': 'bad author', 'created_at': '2026-09-04T17:00:00Z'},
        ], 'includes': {'users': [{'id': '9', 'username': 'SOLANA'}]}}
        with mock.patch.dict(os.environ, self.env), mock.patch('xnews._request', return_value=body) as request:
            posts = xnews.fetch_announcements(now_unix=int(NOW.timestamp()))
        query = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertEqual(query['max_results'], ['13'])
        self.assertEqual(query['post.fields'], [xnews.POST_FIELDS])
        self.assertNotIn('tweet.fields', query)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['text'], 'Good & clear')
        self.assertIsNone(posts[0]['like_count'])
        self.assertIsNone(posts[0]['retweet_count'])
        self.assertEqual(json.loads(self.ledger.read_text())['reservations']['42:1']['posts'], 13)

    def test_current_provider_repost_count_maps_to_compatible_report_field(self):
        self.reserve()
        body = {'data': [{
            'id': '1234567890123456789', 'author_id': '9', 'text': 'Update',
            'created_at': '2026-09-04T17:00:00Z',
            'public_metrics': {'like_count': 7, 'repost_count': 4},
        }], 'includes': {'users': [{'id': '9', 'username': 'solana'}]}}
        with mock.patch.dict(os.environ, self.env), mock.patch('xnews._request', return_value=body):
            posts = xnews.fetch_announcements(now_unix=int(NOW.timestamp()))
        self.assertEqual(posts[0]['retweet_count'], 4)

    def test_post_ids_are_bounded_to_documented_19_digits(self):
        self.reserve()
        body = {'data': [{
            'id': '1' * 20, 'author_id': '9', 'text': 'Invalid id',
            'created_at': '2026-09-04T17:00:00Z', 'public_metrics': {},
        }], 'includes': {'users': [{'id': '9', 'username': 'solana'}]}}
        with mock.patch.dict(os.environ, self.env), mock.patch('xnews._request', return_value=body):
            self.assertEqual(xnews.fetch_announcements(now_unix=int(NOW.timestamp())), [])


if __name__ == '__main__':
    unittest.main()
