"""Cluster version output keeps its node denominator and public evidence bindings."""
import copy
import json
from pathlib import Path
import unittest

import cluster_software
import facts
import pipeline
import render


class ClusterPublicTests(unittest.TestCase):
    def snapshot(self):
        snapshot = json.loads((Path(__file__).parents[1] / 'snapshots/latest.json').read_text())
        section = cluster_software.normalize_cluster_software([
            {'pubkey': '1' * 32, 'version': '3.0.0', 'gossip': 'private-not-published'},
            {'pubkey': '2' * 32, 'version': '3.0.0'},
            {'pubkey': '3' * 32, 'version': None},
        ], 'https://api.mainnet.solana.com', snapshot['collected_at'])
        section['source'].update({key: snapshot['source'][key] for key in ('endpoint', 'endpoint_identity')})
        snapshot['cluster_software'] = section
        return snapshot

    def test_bound_version_counts_on_both_layouts(self):
        snapshot = self.snapshot()
        observations = facts.public_observation_records(snapshot)
        indexes = render.public_observation_indexes(observations)
        row = next(item for item in observations if item['metric_id'] == 'cluster_software_version_nodes')
        self.assertEqual(row['value'], 2)
        self.assertEqual(row['subject_id'], '3.0.0')
        for context in ('desktop', 'mobile'):
            output = render.render_cluster_software(snapshot, context, indexes)
            self.assertIn(row['observation_id'], output)
            self.assertIn("max='3' value='2'", output)
            self.assertIn('Unknown version', output)
            self.assertIn('not validator-client or stake adoption', output)
            self.assertNotIn('private-not-published', output)

    def test_semantics_and_public_projection_reject_bad_count_and_drop_addresses(self):
        snapshot = self.snapshot()
        self.assertFalse([error for error in pipeline.semantic_failures(snapshot)
                          if error['detail'].startswith('cluster_software')])
        snapshot['cluster_software']['unknown_version_node_count'] = 4
        self.assertTrue([error for error in pipeline.semantic_failures(snapshot)
                         if error['detail'].startswith('cluster_software')])
        snapshot = self.snapshot()
        snapshot['cluster_software']['versions'][0]['gossip'] = 'private-not-published'
        self.assertNotIn('private-not-published', json.dumps(render.project_public_envelope(snapshot)))

    def test_oversized_percentages_return_semantic_failures(self):
        for path in ('coverage', 'share', 'node_count', 'total'):
            snapshot = self.snapshot()
            section = snapshot['cluster_software']
            if path == 'coverage':
                section['version_coverage_pct'] = 10 ** 400
            elif path == 'share':
                section['versions'][0]['share_of_observed_nodes_pct'] = 10 ** 400
            elif path == 'node_count':
                section['versions'][0]['node_count'] = 10 ** 400
            else:
                section['observed_node_count'] = 10 ** 400
            self.assertTrue(cluster_software.validate_cluster_software(
                section, snapshot['source'], snapshot['collected_at']))

    def test_missing_source_is_unavailable_and_has_no_version_rows(self):
        snapshot = self.snapshot()
        snapshot['cluster_software'] = cluster_software.normalize_cluster_software(
            None, 'https://api.mainnet.solana.com', snapshot['collected_at'])
        self.assertEqual(facts.cluster_software_detail_facts(snapshot), [])
        self.assertIn('unavailable', render.render_cluster_software(snapshot, 'mobile'))


if __name__ == '__main__':
    unittest.main()
