"""Offline proofs for pinned feature identities and finalized account states."""

import base64
import copy
import unittest
from unittest.mock import patch

import feature_accounts as features


def account(slot=None):
    data = bytes([slot is not None]) + (slot or 0).to_bytes(8, "little")
    return {"owner": features.FEATURE_PROGRAM_ID, "executable": False, "space": 9,
            "data": [base64.b64encode(data).decode(), "base64"]}


def response():
    return {"context": {"slot": 1_000, "apiVersion": "4.2.2"},
            "value": [account(900), account(), *([None] * 8)]}


class TestFeatureAccounts(unittest.TestCase):
    def parse(self, raw=None, endpoint="https://api.mainnet-beta.solana.com"):
        return features.parse_feature_accounts(
            response() if raw is None else raw, endpoint, "2026-09-04T12:00:00Z",
        )

    def test_active_pending_and_absent_have_distinct_meanings(self):
        result = self.parse()
        self.assertEqual(result["coverage_numerator"], 10)
        self.assertEqual(result["coverage_denominator"], 10)
        self.assertEqual(result["activated_feature_count"], 1)
        self.assertTrue(result["available"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["features"][0]["state"], "activated")
        self.assertEqual(result["features"][0]["activated_at_slot"], 900)
        self.assertEqual(result["features"][1]["state"], "pending")
        self.assertIsNone(result["features"][1]["activated_at_slot"])
        self.assertEqual(result["features"][2]["state"], "account_absent")
        self.assertIsNone(result["features"][2]["activated_at_slot"])
        self.assertNotIn("inactive", {row["state"] for row in result["features"]})

    def test_absent_accounts_are_observed_without_an_activation_claim(self):
        raw = response()
        raw["value"] = [None] * 10
        result = self.parse(raw)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["activated_feature_count"], 0)
        self.assertTrue(all(row["state"] == "account_absent" for row in result["features"]))

    def test_invalid_owner_size_encoding_or_activation_cannot_be_counted(self):
        invalid = [
            {**account(900), "owner": "not-the-feature-program"},
            {**account(900), "executable": True},
            {**account(900), "space": 8},
            {**account(900), "data": ["not base64!!", "base64"]},
            {**account(900), "data": [base64.b64encode(bytes([2]) + bytes(8)).decode(), "base64"]},
            {**account(900), "data": [base64.b64encode(bytes(8)).decode(), "base64"]},
            account(1_001),
        ]
        for bad in invalid:
            with self.subTest(account=bad):
                raw = response()
                raw["value"][0] = bad
                result = self.parse(raw)
                self.assertEqual(result["coverage_numerator"], 9)
                self.assertFalse(result["coverage_complete"])
                self.assertEqual(result["activated_feature_count"], 0)
                self.assertEqual(result["features"][0]["state"], "unavailable")
                self.assertIsNone(result["features"][0]["activated_at_slot"])

    def test_missing_or_misaligned_response_is_unavailable_not_all_absent(self):
        for raw in ({}, {"context": {"slot": 100}, "value": []},
                    {"context": {"slot": True}, "value": [None] * 10}):
            with self.subTest(raw=raw):
                result = self.parse(raw)
                self.assertFalse(result["available"])
                self.assertFalse(result["coverage_complete"])
                self.assertEqual(result["coverage_numerator"], 0)
                self.assertIsNone(result["source"]["rpc_context_slot"])
                self.assertTrue(all(row["state"] == "unavailable" for row in result["features"]))

    def test_invalid_observation_time_cannot_look_fresh(self):
        for stamp in ("not-a-date", "2026-09-04T00:00:00", None):
            result = features.parse_feature_accounts(response(), "rpc", stamp)
            self.assertFalse(result["available"])
            self.assertIsNone(result["observed_at"])

    def test_custom_endpoint_is_redacted_and_identity_stays_opaque(self):
        endpoint = "https://rpc.example/private-token?api-key=secret"
        result = self.parse(endpoint=endpoint)
        self.assertEqual(result["source"]["endpoint"], "custom RPC endpoint")
        self.assertTrue(result["source"]["endpoint_identity"].startswith("sha256:"))
        self.assertNotIn("private-token", str(result))
        self.assertNotIn("api-key", str(result))

    def test_duplicate_pinned_addresses_fail_closed(self):
        duplicated = (*features.FEATURES[:-1], features.FEATURES[0])
        with patch.object(features, "FEATURES", duplicated):
            result = self.parse()
        self.assertFalse(result["available"])
        self.assertEqual(result["coverage_numerator"], 0)

    def test_collection_uses_one_bounded_finalized_request(self):
        with patch("feature_accounts.blocks.call", return_value=response()) as rpc:
            result = features.collect_feature_accounts("https://rpc.example", timeout=7)
        rpc.assert_called_once_with("getMultipleAccounts", [
            [row[3] for row in features.FEATURES],
            {"commitment": "finalized", "encoding": "base64"},
        ], "https://rpc.example", 7)
        self.assertTrue(result["available"])

    def test_source_and_gate_metadata_are_pinned_without_payload_passthrough(self):
        raw = response()
        original = copy.deepcopy(raw)
        raw["private"] = "must not survive"
        raw["value"][0]["private"] = "must not survive"
        result = self.parse(raw)
        self.assertEqual(result["metadata"], features.METADATA)
        self.assertEqual(result["metadata"]["license"], "Apache-2.0")
        self.assertEqual(len(result["metadata"]["source_revision"]), 40)
        self.assertNotIn("must not survive", str(result))
        self.assertEqual(raw["context"], original["context"])
        self.assertEqual(result["features"][0]["simd"], "SIMD-0326")
        self.assertEqual(sum(row["simd"] == "SIMD-0525" for row in result["features"]), 4)


if __name__ == "__main__":
    unittest.main()
