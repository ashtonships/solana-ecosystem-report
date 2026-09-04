"""Offline proofs for RPC-observed cluster software version evidence."""

import copy
import unittest
from unittest.mock import patch

import cluster_software


BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def pubkey(index: int) -> str:
    return "1" * 31 + BASE58[index]


def node(index: int, version="2.3.8"):
    return {
        "pubkey": pubkey(index),
        "version": version,
        "gossip": f"192.0.2.{index}:8001",
        "rpc": f"https://rpc-{index}.example/private",
        "tpu": f"192.0.2.{index}:8003",
    }


class ClusterSoftwareTests(unittest.TestCase):
    def parse(self, raw, endpoint="https://api.mainnet.solana.com",
              observed_at="2026-09-04T20:00:00Z"):
        return cluster_software.normalize_cluster_software(
            raw, endpoint, observed_at,
        )

    def test_exact_versions_and_unknown_share_one_unweighted_denominator(self):
        result = self.parse([
            node(0, "2.3.8"), node(1, None), node(2, "2.3.8"),
            node(3, "2.2.20"), node(4, None),
        ])

        self.assertTrue(result["available"])
        self.assertEqual(result["observed_node_count"], 5)
        self.assertEqual(result["version_reported_node_count"], 3)
        self.assertEqual(result["unknown_version_node_count"], 2)
        self.assertEqual(result["version_coverage_pct"], 60.0)
        self.assertEqual(result["distinct_reported_version_count"], 2)
        self.assertEqual(result["versions"], [
            {"version": "2.3.8", "node_count": 2,
             "share_of_observed_nodes_pct": 40.0},
            {"version": "2.2.20", "node_count": 1,
             "share_of_observed_nodes_pct": 20.0},
        ])
        self.assertEqual(result["source"]["weighting"], "unweighted node count")
        self.assertNotIn("client", result["source"])

    def test_output_is_bounded_and_retains_an_explicit_other_count(self):
        raw = [node(index, f"2.{index}.0") for index in range(22)]
        result = self.parse(raw)

        self.assertTrue(result["available"])
        self.assertEqual(len(result["versions"]), cluster_software.MAX_VERSION_GROUPS)
        self.assertEqual(result["published_version_group_count"], 20)
        self.assertEqual(result["distinct_reported_version_count"], 22)
        self.assertEqual(result["other_reported_version_node_count"], 2)
        self.assertEqual(
            sum(row["node_count"] for row in result["versions"])
            + result["other_reported_version_node_count"]
            + result["unknown_version_node_count"],
            result["observed_node_count"],
        )

    def test_semantic_validator_accepts_normalized_available_and_unavailable(self):
        endpoint = "https://api.mainnet.solana.com"
        reference = cluster_software.growth.rpc_endpoint_reference(endpoint)
        available = self.parse([node(0), node(1, None)])
        unavailable = self.parse(None)

        self.assertEqual(cluster_software.validate_cluster_software(
            available, reference, "2026-09-04T20:00:01Z",
        ), [])
        self.assertEqual(cluster_software.validate_cluster_software(
            unavailable, reference, "2026-09-04T20:00:01Z",
        ), [])

    def test_semantic_validator_rejects_time_source_count_and_row_drift(self):
        endpoint = "https://api.mainnet.solana.com"
        reference = cluster_software.growth.rpc_endpoint_reference(endpoint)
        valid = self.parse([node(0, "2.3.8"), node(1, "2.2.20")])
        mutations = []
        future = copy.deepcopy(valid)
        future["observed_at"] = "2026-09-04T20:00:02Z"
        mutations.append(future)
        wrong_source = copy.deepcopy(valid)
        wrong_source["source"]["endpoint_identity"] = "sha256:wrong"
        mutations.append(wrong_source)
        bad_total = copy.deepcopy(valid)
        bad_total["unknown_version_node_count"] = 1
        mutations.append(bad_total)
        bad_share = copy.deepcopy(valid)
        bad_share["versions"][0]["share_of_observed_nodes_pct"] = 49.0
        mutations.append(bad_share)
        unsorted = copy.deepcopy(valid)
        unsorted["versions"].reverse()
        mutations.append(unsorted)
        duplicate = copy.deepcopy(valid)
        duplicate["versions"][1]["version"] = duplicate["versions"][0]["version"]
        mutations.append(duplicate)
        uncontracted = copy.deepcopy(valid)
        uncontracted["private_nodes"] = ["must not publish"]
        mutations.append(uncontracted)

        for candidate in mutations:
            with self.subTest(candidate=candidate):
                self.assertTrue(cluster_software.validate_cluster_software(
                    candidate, reference, "2026-09-04T20:00:01Z",
                ))

    def test_semantic_validator_requires_null_unavailable_metrics(self):
        endpoint = "https://api.mainnet.solana.com"
        reference = cluster_software.growth.rpc_endpoint_reference(endpoint)
        unavailable = self.parse(None)
        unavailable["observed_node_count"] = 0
        unavailable["reason"] = ""

        errors = cluster_software.validate_cluster_software(
            unavailable, reference, "2026-09-04T20:00:01Z",
        )

        self.assertTrue(errors)
        self.assertTrue(any("counts must be null" in error for error in errors))
        self.assertTrue(any("requires a reason" in error for error in errors))

    def test_malformed_or_duplicate_node_records_fail_closed(self):
        malformed = [
            None,
            [],
            [None],
            [node(0), node(0, "2.2.20")],
            [{"pubkey": "not-a-pubkey", "version": "2.3.8"}],
            [node(0, "")],
            [node(0, " 2.3.8")],
            [node(0, 238)],
            [node(0, "v" * (cluster_software.MAX_VERSION_LENGTH + 1))],
        ]
        for raw in malformed:
            with self.subTest(raw=raw):
                result = self.parse(raw)
                self.assertFalse(result["available"])
                self.assertIsNone(result["observed_node_count"])
                self.assertEqual(result["versions"], [])
                self.assertIsInstance(result["reason"], str)

    def test_invalid_observation_time_or_endpoint_cannot_look_available(self):
        for endpoint, observed_at in (
            ("https://api.mainnet.solana.com", "not-a-time"),
            ("https://api.mainnet.solana.com", "2026-09-04T20:00:00"),
            ("", "2026-09-04T20:00:00Z"),
        ):
            with self.subTest(endpoint=endpoint, observed_at=observed_at):
                result = self.parse([node(0)], endpoint, observed_at)
                self.assertFalse(result["available"])

    def test_custom_endpoint_and_raw_node_coordinates_never_enter_output(self):
        endpoint = "https://rpc.example/private?api-key=secret"
        raw = [node(0, "0.1106.40201")]
        original = copy.deepcopy(raw)
        result = self.parse(raw, endpoint)
        rendered = str(result)

        self.assertTrue(result["available"])
        self.assertEqual(result["source"]["endpoint"], "custom RPC endpoint")
        self.assertTrue(result["source"]["endpoint_identity"].startswith("sha256:"))
        for secret in (endpoint, raw[0]["pubkey"], raw[0]["gossip"],
                       raw[0]["rpc"], raw[0]["tpu"]):
            self.assertNotIn(secret, rendered)
        self.assertEqual(raw, original)


if __name__ == "__main__":
    unittest.main()
