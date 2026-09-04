"""Feature account coverage and provenance must justify every published claim."""
import base64
import copy
from datetime import datetime, timezone
import unittest

import feature_accounts
import growth
import pipeline

ENDPOINT = "https://api.mainnet-beta.solana.com"
NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def account(slot):
    return {"owner": feature_accounts.FEATURE_PROGRAM_ID, "executable": False,
            "data": [base64.b64encode(bytes([1]) + slot.to_bytes(8, "little")).decode(), "base64"]}


class FeatureActivationContract(unittest.TestCase):
    def section(self):
        return feature_accounts.parse_feature_accounts(
            {"context": {"slot": 100, "apiVersion": "3.1.0"},
             "value": [account(80)] + [None] * 9}, ENDPOINT, NOW.isoformat(),
        )

    def errors(self, section):
        return pipeline._feature_activation_semantic_failures(
            section, NOW, growth.rpc_endpoint_reference(ENDPOINT),
        )

    def test_full_partial_and_unavailable_observations_are_truthful(self):
        self.assertEqual(self.errors(self.section()), [])
        partial = self.section()
        partial["features"][1].update(
            state="unavailable", reason="Feature account encoding is invalid.",
        )
        partial.update(coverage_numerator=9, coverage_complete=False)
        self.assertEqual(self.errors(partial), [])
        failed = feature_accounts.parse_feature_accounts(None, ENDPOINT, NOW.isoformat())
        self.assertEqual(self.errors(failed), [])
        self.assertEqual(failed["activated_feature_count"], 0)
        self.assertFalse(failed["available"])

    def test_scope_counts_states_dates_and_provenance_cannot_be_fabricated(self):
        mutations = [
            lambda s: s.update(uncontracted=True),
            lambda s: s["metadata"].update(source_revision="a" * 40),
            lambda s: s.update(note="All upgrades active"),
            lambda s: s.update(coverage_numerator=11),
            lambda s: s.update(coverage_denominator=True),
            lambda s: s.update(activated_feature_count=2),
            lambda s: s.update(available=False),
            lambda s: s.update(coverage_complete=False),
            lambda s: s.update(observed_at="2026-09-05T00:00:00Z"),
            lambda s: s.update(observed_at="2026-09-04T07:00:00-05:00"),
            lambda s: s.update(observed_at=None),
            lambda s: s["source"].update(rpc_context_slot=None),
            lambda s: s["source"].update(rpc_context_slot=True),
            lambda s: s["source"].update(rpc_api_version={}),
            lambda s: s["source"].update(commitment="processed"),
            lambda s: s["source"].update(endpoint="https://private.invalid/?key=hidden"),
            lambda s: s["source"].update(endpoint_identity="sha256:" + "a" * 64),
            lambda s: s["source"].update(uncontracted=True),
            lambda s: s["features"][0].update(activated_at_slot=101),
            lambda s: s["features"][0].update(activated_at_slot=True),
            lambda s: s["features"][1].update(state="inactive"),
            lambda s: s["features"][1].update(activated_at_slot=0),
            lambda s: s["features"][1].update(reason=None),
            lambda s: s["features"][1].update(reason="Feature is inactive"),
            lambda s: s["features"][0].update(title="Invented gate"),
            lambda s: s["features"][1].update(address=s["features"][0]["address"]),
            lambda s: s["features"].pop(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                section = self.section()
                mutate(section)
                self.assertTrue(self.errors(section))

    def test_publication_gate_calls_feature_contract_but_legacy_absence_is_optional(self):
        snapshot = {"schema_version": 9, "collected_at": NOW.isoformat(),
                    "source": growth.rpc_endpoint_reference(ENDPOINT),
                    "feature_activation": self.section()}
        self.assertFalse(any(row["detail"].startswith("feature_activation:")
                             for row in pipeline.semantic_failures(snapshot)))
        snapshot["feature_activation"]["activated_feature_count"] = 10
        self.assertTrue(any(row["detail"].startswith("feature_activation:")
                            for row in pipeline.semantic_failures(snapshot)))
        snapshot.pop("feature_activation")
        self.assertFalse(any(row["detail"].startswith("feature_activation:")
                             for row in pipeline.semantic_failures(snapshot)))


if __name__ == "__main__":
    unittest.main()
