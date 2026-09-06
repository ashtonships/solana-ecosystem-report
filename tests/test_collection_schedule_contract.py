"""Collection reuse must not manufacture new observations or hide source age."""
import copy
import json
from pathlib import Path
import unittest

import cadence
import charts
import detect
import facts
import pipeline
import render


class CollectionScheduleContractTests(unittest.TestCase):
    def snapshot(self):
        snapshot = json.loads((Path(__file__).parents[1] / 'snapshots/latest.json').read_text())
        stamp = snapshot['collected_at']
        snapshot['collection_schedule'] = cadence.initial_schedule()
        for entry in snapshot['collection_schedule'].values():
            entry.update(last_attempt_at=stamp, last_success_at=stamp, state='fresh')
        return snapshot

    def test_reused_sample_keeps_fact_identity_and_baseline_size(self):
        original = self.snapshot()
        reused = copy.deepcopy(original)
        from datetime import datetime, timedelta
        reused['collected_at'] = (datetime.fromisoformat(original['collected_at']) + timedelta(minutes=15)).isoformat()
        reused['collection_schedule']['activity']['state'] = 'reused'
        before = facts.fact_from_snapshot(original, 'median_fee_lamports')
        after = facts.fact_from_snapshot(reused, 'median_fee_lamports')
        self.assertEqual(before, after)
        self.assertEqual(len(facts.dedupe_facts([before, after, after])), 1)
        spec = next(row for row in charts.SERIES if row['key'] == 'median_fee_lamports')
        points = charts.extract([original, reused], spec)
        self.assertIsNotNone(points[0]['value'])
        self.assertIsNone(points[1]['value'])
        refreshed = copy.deepcopy(reused)
        refreshed['collection_schedule']['activity'].update(
            state='fresh', last_attempt_at=reused['collected_at'], last_success_at=reused['collected_at'])
        self.assertNotEqual(facts.fact_identity(before), facts.fact_identity(
            facts.fact_from_snapshot(refreshed, 'median_fee_lamports')))

    def test_public_reuse_retains_source_time_and_current_publication(self):
        snapshot = self.snapshot()
        old = snapshot['collected_at']
        from datetime import datetime, timedelta
        snapshot['collected_at'] = (datetime.fromisoformat(old) + timedelta(minutes=15)).isoformat()
        snapshot['collection_schedule']['activity']['state'] = 'reused'
        record = next(row for row in facts.public_observation_records(snapshot)
                      if row['metric_id'] == 'median_fee_lamports')
        self.assertEqual(record['collected_at'], old)
        self.assertEqual(record['snapshot_collected_at'], snapshot['collected_at'])
        self.assertEqual(record['freshness'], 'reused at original source time')
        self.assertIn('Scheduled reuse', record['caveat'])
        source_records = [row for row in facts.public_observation_records(snapshot)
                          if row['metric_id'] == 'news_source_available']
        self.assertTrue(source_records)
        self.assertTrue(all(row['observed_at'] is None for row in source_records))

    def test_failed_current_value_is_excluded_without_mutating_old_fact(self):
        snapshot = self.snapshot()
        original = facts.fact_from_snapshot(snapshot, 'median_fee_lamports')
        snapshot['collection_schedule']['activity']['state'] = 'failed'
        self.assertFalse(detect._eligible_value(snapshot, ('activity', 'fees', 'median_lamports')))
        self.assertEqual(original, facts.fact_from_snapshot(snapshot, 'median_fee_lamports'))

    def test_schedule_validation_and_recursive_projection(self):
        snapshot = self.snapshot()
        self.assertFalse([e for e in pipeline.semantic_failures(snapshot)
                          if e['detail'].startswith('collection_schedule')])
        projected = render.project_public_envelope(snapshot)
        self.assertEqual(projected['collection_schedule'], snapshot['collection_schedule'])
        for field, value in [('private_token', 'never-publish'), ('last_success_at', '2999-01-01T00:00:00Z'), ('interval_seconds', 42)]:
            candidate = copy.deepcopy(snapshot)
            candidate['collection_schedule']['news'][field] = value
            self.assertTrue([e for e in pipeline.semantic_failures(candidate)
                             if e['detail'].startswith('collection_schedule')])
        snapshot['collection_schedule']['news']['private_token'] = 'never-publish'
        self.assertNotIn('never-publish', json.dumps(render.project_public_envelope(snapshot)))

    def test_historical_daily_token_schedule_remains_valid_and_unchanged(self):
        snapshot = self.snapshot()
        snapshot['collection_schedule']['growth_tokens']['interval_seconds'] = 86_400
        original = copy.deepcopy(snapshot)
        self.assertFalse([e for e in pipeline.semantic_failures(snapshot)
                          if e['detail'].startswith('collection_schedule')])
        projected = render.project_public_envelope(snapshot)
        self.assertEqual(projected['collection_schedule'], snapshot['collection_schedule'])
        self.assertEqual(snapshot, original)

    def test_methods_show_same_schedule_on_mobile_and_desktop(self):
        snapshot = self.snapshot()
        for mobile in (False, True):
            content = render.render_collection_schedule(snapshot, mobile=mobile)
            self.assertIn('every fifteen minutes', content)
            self.assertIn('original collection times', content)
            self.assertIn('Every six hours', content)
            self.assertIn('Paid sources', content)
            self.assertEqual(content.count('<dt>'), len(cadence.INTERVALS))

    def test_schedule_explains_reuse_and_next_eligible_time(self):
        snapshot = self.snapshot()
        snapshot['collected_at'] = '2026-09-06T08:02:03+00:00'
        snapshot['collection_schedule']['activity'].update(
            state='reused', last_attempt_at='2026-09-06T07:31:46+00:00',
            last_success_at='2026-09-06T07:31:46+00:00')
        row = render.collection_schedule_rows(snapshot)[0]
        self.assertEqual(row[2], 'Scheduled reuse')
        self.assertIn('at snapshot', row[3])
        self.assertIn('08:31 UTC', row[4])
        snapshot['collected_at'] = '2026-09-06T08:32:00+00:00'
        row = render.collection_schedule_rows(snapshot)[0]
        self.assertEqual(row[2], 'Refresh due')
        self.assertIn('awaiting an eligible run', row[4])

    def test_first_failure_is_not_hidden_by_missing_success(self):
        snapshot = self.snapshot()
        snapshot['collection_schedule']['activity'].update(
            state='failed', last_success_at=None)
        row = render.collection_schedule_rows(snapshot)[0]
        self.assertEqual(row[2], 'Refresh unavailable')
        self.assertIn('No successful collection', row[3])
        self.assertIn('Last attempt', row[4])
        content = render.render_collection_schedule(snapshot)
        self.assertIn('These recorded statuses do not update', content)

    def test_legacy_token_schedule_displays_its_recorded_interval(self):
        snapshot = self.snapshot()
        snapshot['collection_schedule']['growth_tokens']['interval_seconds'] = 86400
        row = next(row for row in render.collection_schedule_rows(snapshot)
                   if row[0] == 'Selected token supplies')
        self.assertEqual(row[1], 'Daily')

    def test_validator_coverage_declares_count_denominator(self):
        row = next(row for row in render.REPORT_COVERAGE_REQUIREMENTS if row[0] == 'R08')
        self.assertNotIn('Stake-weighted', str(row))
        self.assertIn('Vote-account delinquency', str(row))

    def test_coverage_does_not_retimestamp_aggregate_sources(self):
        snapshot = self.snapshot()
        indexes = render.public_observation_indexes(facts.public_observation_records(snapshot))
        content = render.render_report_coverage(snapshot, None, None, 'desktop', indexes)
        for key in ('R11', 'R16'):
            row = content.split("data-requirement='" + key + "'", 1)[1].split('</tr>', 1)[0]
            self.assertNotIn('Observed ', row)
            self.assertIn('Source observation times vary', row)


if __name__ == '__main__':
    unittest.main()
