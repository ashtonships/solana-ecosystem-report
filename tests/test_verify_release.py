"""Offline adversarial checks for the public release boundary."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect  # noqa: E402
import facts  # noqa: E402
import render  # noqa: E402
import verify_release  # noqa: E402
from tests.test_facts import xstock_fact_snapshot  # noqa: E402


VERIFY_NOW = datetime.fromisoformat("2026-08-31T10:00:02+00:00")


def candidate(collected_at: str) -> dict:
    endpoint = "https://api.mainnet.solana.com"
    return {
        "schema_version": collect.SCHEMA_VERSION,
        "collected_at": collected_at,
        "provenance": {
            "source_revision": "a" * 40,
            "source_tree_dirty": False,
        },
        "source": {
            "endpoint": endpoint,
            "endpoint_identity": "sha256:" + hashlib.sha256(endpoint.encode()).hexdigest(),
            "method": "Solana JSON-RPC batch",
            "requires_api_key": False,
        },
        "network": {
            "healthy": True,
            "health_raw": "ok",
            "health_method": "getHealth",
            "health_scope": "rpc_endpoint",
            "slot": 500,
        },
        "epoch": {"available": True, "epoch": 800, "progress_pct": 50.0},
        "performance": {
            "available": True,
            "samples_used": 1,
            "sample_period_seconds": 60,
            "latest_tps": 3000.0,
            "mean_tps": 3000.0,
            "peak_tps": 3000.0,
            "mean_slot_time_secs": 0.4,
            "samples": [{
                "slot": 500,
                "transactions": 180_000,
                "non_vote_transactions": 120_000,
                "sample_period_secs": 60,
                "slots": 150,
                "tps": 3000.0,
                "non_vote_tps": 2000.0,
                "vote_tps": 1000.0,
                "vote_share_pct": 33.3333333333,
                "slot_time_secs": 0.4,
            }],
        },
        "supply": {"available": True, "circulating_sol": 480_000_000.0},
        "inflation": {"available": True},
        "validators": {"available": True},
        "economics": {"available": False},
        "activity": {"available": False},
        "news": {
            "available": False,
            "featured_item_id": None,
            "items": [],
            "sources": {},
        },
        "growth": {"available": False},
    }


def write_snapshot(root: Path, snapshot: dict) -> None:
    raw = verify_release.canonical_json(snapshot)
    snapshot_dir = root / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "latest.json").write_bytes(raw)
    (snapshot_dir / collect.snapshot_filename(snapshot["collected_at"])).write_bytes(raw)


def rewrite_artifact_release_path(artifacts: Path) -> None:
    report_path = artifacts / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["release"]["selected_snapshot"]["path"] = "snapshots/latest.json"
    report_path.write_bytes(verify_release.canonical_json(report))
    replacements = {
        "index.html": (
            "<dt>Selected snapshot</dt><dd><code>latest.json</code></dd>",
            "<dt>Selected snapshot</dt><dd><code>snapshots/latest.json</code></dd>",
        ),
        "report.md": (
            "| Selected snapshot | latest.json |",
            "| Selected snapshot | snapshots/latest.json |",
        ),
    }
    for name, (old, new) in replacements.items():
        path = artifacts / name
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"missing selected snapshot release row in {name}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class TestReleaseVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_handle = tempfile.TemporaryDirectory(prefix="release-verifier-template-")
        cls.template = Path(cls.template_handle.name)
        snapshot = candidate("2026-08-31T10:00:00+00:00")
        write_snapshot(cls.template, snapshot)
        facts_path = cls.template / "history" / "facts.jsonl"
        facts.append_jsonl(facts_path, facts.snapshot_facts(snapshot))
        samples = cls.template / "samples"
        with patch.object(
            render.collect_module, "source_code_state",
            return_value={"source_revision": "b" * 40, "source_tree_dirty": False},
        ), patch.object(sys, "argv", [
            "render.py", "--snapshot", str(cls.template / "snapshots" / "latest.json"),
            "--out-dir", str(samples), "--generated-at", "2026-08-31T10:00:01+00:00",
        ]):
            code = render.main()
        if code != 0:
            raise RuntimeError("could not build release-verifier test package")
        rewrite_artifact_release_path(samples)
        data = verify_release.verify_public_data(root=cls.template, now=VERIFY_NOW)
        report, artifact_bytes = verify_release.verify_artifacts(samples, data)
        manifest = verify_release.build_manifest(
            cls.template, samples, data, report, artifact_bytes,
        )
        (cls.template / "release-manifest.json").write_bytes(
            verify_release.canonical_json(manifest, sort_keys=True),
        )

    @classmethod
    def tearDownClass(cls):
        cls.template_handle.cleanup()

    def setUp(self):
        self.handle = tempfile.TemporaryDirectory(prefix="release-verifier-test-")
        self.root = Path(self.handle.name) / "package"
        shutil.copytree(self.template, self.root)

    def tearDown(self):
        self.handle.cleanup()

    def verify(self):
        return verify_release.verify_package(
            self.root, self.root / "samples",
            manifest_path=self.root / "release-manifest.json", check_git=False,
            now=VERIFY_NOW,
        )

    def mutate_snapshot(self, mutation) -> None:
        path = self.root / "snapshots" / "latest.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        mutation(snapshot)
        write_snapshot(self.root, snapshot)

    def test_clean_manifest_binds_every_public_byte(self):
        result = self.verify()

        self.assertRegex(result["release_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(set(result["artifact_sha256"]), verify_release.ARTIFACT_NAMES)
        self.assertEqual(result["manifest"]["source_revision"], "a" * 40)
        self.assertEqual(result["manifest"]["data_revision"], "b" * 40)

    def test_every_immutable_history_snapshot_is_strictly_verified(self):
        selected_path = self.root / "snapshots" / "latest.json"
        selected_raw, selected, immutable = verify_release.verify_snapshot(
            selected_path, now=VERIFY_NOW,
        )

        def legacy_candidate(timestamp):
            value = candidate(timestamp)
            value["schema_version"] = 3
            value.pop("provenance", None)
            value.pop("inflation", None)
            value.pop("growth", None)
            value["news"].pop("featured_item_id", None)
            value["news"].pop("items", None)
            return value

        legacy = legacy_candidate("2026-08-31T09:00:00+00:00")
        legacy_path = self.root / "snapshots" / collect.snapshot_filename(
            legacy["collected_at"]
        )
        legacy_path.write_bytes(verify_release.canonical_json(legacy))
        records = verify_release.verify_snapshot_history(
            selected_path.parent, selected_raw, selected, immutable,
        )
        self.assertEqual([item["snapshot"]["schema_version"] for item in records], [3, 9])

        cases = []
        cases.append((
            "noncanonical",
            lambda root: (root / "snapshots" / collect.snapshot_filename(
                "2026-08-31T09:00:00+00:00"
            )).write_bytes(b" " + verify_release.canonical_json(legacy)),
            "not canonical collector JSON",
        ))
        cases.append((
            "filename",
            lambda root: (root / "snapshots" / "snapshot-wrong.json").write_bytes(
                verify_release.canonical_json(legacy)
            ),
            "filename does not match",
        ))

        def duplicate_instant(root):
            value = legacy_candidate("2026-08-31T05:00:00-05:00")
            (root / "snapshots" / collect.snapshot_filename(value["collected_at"])).write_bytes(
                verify_release.canonical_json(value)
            )

        cases.append(("duplicate", duplicate_instant, "duplicate one UTC collection instant"))

        def decreasing(root):
            for timestamp in ("2026-08-31T04:30:00-05:00", "2026-08-31T09:00:00+00:00"):
                value = legacy_candidate(timestamp)
                (root / "snapshots" / collect.snapshot_filename(timestamp)).write_bytes(
                    verify_release.canonical_json(value)
                )

        cases.append(("decreasing", decreasing, "not strictly increasing"))

        def future(root):
            value = legacy_candidate("2026-08-31T10:00:01+00:00")
            (root / "snapshots" / collect.snapshot_filename(value["collected_at"])).write_bytes(
                verify_release.canonical_json(value)
            )

        cases.append(("future", future, "after the selected snapshot"))

        def private_legacy(root):
            value = legacy_candidate("2026-08-31T09:00:00+00:00")
            value["source"]["endpoint"] = "/Users/ashton/private-receipt.json"
            (root / "snapshots" / collect.snapshot_filename(value["collected_at"])).write_bytes(
                verify_release.canonical_json(value)
            )

        cases.append(("private", private_legacy, "prohibited private"))

        def supported_unknown(root):
            value = candidate("2026-08-31T09:00:00+00:00")
            value["private_debug"] = "secret"
            (root / "snapshots" / collect.snapshot_filename(value["collected_at"])).write_bytes(
                verify_release.canonical_json(value)
            )

        cases.append(("supported unknown", supported_unknown, "outside the recursive public schema"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "package"
            shutil.copytree(self.template, root)
            value = candidate("2026-08-31T09:00:00+00:00")
            value["private_debug"] = {"nested": [{"deep": 1}]}
            projected = render.project_public_envelope(deepcopy(value))
            extras = verify_release._first_projection_extras(value, projected)
            self.assertIn("$.private_debug", extras)
            self.assertTrue(all(isinstance(item, str) for item in extras))

        def unsupported(root):
            value = candidate("2026-08-31T09:00:00+00:00")
            value["schema_version"] = 10
            (root / "snapshots" / collect.snapshot_filename(value["collected_at"])).write_bytes(
                verify_release.canonical_json(value)
            )

        cases.append(("unsupported", unsupported, "schema is unsupported"))

        for label, mutate, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                mutate(root)
                raw, current, current_immutable = verify_release.verify_snapshot(
                    root / "snapshots" / "latest.json", now=VERIFY_NOW,
                )
                with self.assertRaisesRegex(verify_release.ReleaseVerificationError, message):
                    verify_release.verify_snapshot_history(
                        root / "snapshots", raw, current, current_immutable,
                    )

    def test_artifact_set_runtime_privacy_and_freshness_fail_closed(self):
        stale_now = datetime.fromisoformat("2026-09-01T10:00:00+00:00")
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "publication gate",
        ):
            verify_release.verify_public_data(root=self.root, now=stale_now)

        cases = (
            ("index.html", "\n<script src='//example.invalid/leak.js'></script>\n",
             "external runtime resource"),
            ("index.html", "\n<img src=https://example.invalid/leak.png>\n",
             "external runtime resource"),
            ("index.html", "\n<style>@import \"https://example.invalid/leak.css\";</style>\n",
             "external runtime resource"),
            ("index.html", "\n<style>.x{background:url(//example.invalid/leak)}</style>\n",
             "external runtime resource"),
            ("index.html", "\n<meta http-equiv=refresh content='0;url=https://example.invalid'>\n",
             "external runtime resource"),
            ("index.html", "\n<svg><image href=https://example.invalid/leak.svg></image></svg>\n",
             "external runtime resource"),
            ("report.md", "\n/private/tmp/private-receipt.json\n",
             "prohibited private"),
            ("report.md", "\nhttp://192.168.1.42:8781\n",
             "prohibited non-public IP"),
            ("report.md", "\nhttp://[::1]:8781\n",
             "prohibited non-public IP"),
            ("report.md", "\nhttp://2130706433:8781\n",
             "prohibited non-public IP"),
            ("report.md", "\nhttp://127.1:8781\n",
             "prohibited non-public IP"),
            ("report.md", "\nhttp://0x7f000001:8781\n",
             "prohibited non-public IP"),
            ("report.md", "\nhttp://ashton-mac.local:8781\n",
             "prohibited local-network"),
            ("report.md", "\nhttp://router.lan:8781\n",
             "prohibited local-network"),
            ("report.md", "\nhttp://nas:8781\n",
             "prohibited local-network"),
            ("report.md", "\nhttp://home.arpa:8781\n",
             "prohibited local-network"),
            ("report.md", "\nhttp://localdomain:8781\n",
             "prohibited local-network"),
            ("report.md", "\nhttps://alice:supersecret@example.com\n",
             "prohibited credential-bearing URL"),
            ("report.md", "\nhttps://example.com/?access_token=abcdefghijklmnop\n",
             "prohibited credential query parameter"),
            ("report.md", "\nhttps://example.com/#refresh_token=abcdefghijklmnop\n",
             "prohibited credential query parameter"),
            ("report.md", "\nhttps://example.com/?X-Amz-Credential=AKIAIOSFODNN7EXAMPLE\n",
             "prohibited credential query parameter"),
            ("report.md", "\nhttps://example.com/?foo=bar;access_token=abcdefghijklmnop\n",
             "prohibited credential query parameter"),
            ("report.md", "\nhttps://example.com/releases/ghp_12345678901234567890\n",
             "credential-shaped text"),
            ("report.md", "\nhttps://example.com/releases/Bearer%20abcdefghijklmnop\n",
             "credential-shaped text"),
            ("report.md", "\nhttps://example.com/private%2Ftmp%2Fprivate-receipt.json\n",
             "prohibited private"),
            ("report.md", "\nhttps%3A%2F%2Fexample.com%2F%3Fapi_key%3Dabcdefghijklmnop\n",
             "prohibited credential query parameter"),
            ("index.html", "\n<img src=\"https&#58;//example.invalid/leak.png\">\n",
             "external runtime resource"),
            ("report.md", "\nBearer ghp_12345678901234567890\n",
             "credential-shaped text"),
        )
        for name, suffix, message in cases:
            with self.subTest(name=name, suffix=suffix), tempfile.TemporaryDirectory(
                prefix="release-scan-adversary-",
            ) as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                path = root / "samples" / name
                path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
                data = verify_release.verify_public_data(root=root, now=VERIFY_NOW)
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError, message,
                ):
                    verify_release.verify_artifacts(root / "samples", data)

        (self.root / "samples" / "leak").symlink_to(self.root, target_is_directory=True)
        data = verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "artifact set mismatch",
        ):
            verify_release.verify_artifacts(self.root / "samples", data)

    def test_percent_encoded_privacy_scan_fails_closed_after_five_rounds(self):
        for seed in (
            "https://example.com/releases/ghp_12345678901234567890",
            "https://example.com/private/tmp/private-receipt.json",
        ):
            encoded = seed
            for _ in range(6):
                encoded = urllib.parse.quote(encoded, safe="")
            with self.subTest(seed=seed):
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError,
                    "excessively nested percent-encoded",
                ):
                    verify_release._scan_public_text(encoded, "report.md")

    def test_benign_percent_encoded_public_url_is_accepted(self):
        verify_release._scan_public_text(
            "https%3A%2F%2Fexample.com%2Fdocs%2F%E2%9C%93%3Flang%3Den",
            "report.md",
        )

    def test_snapshot_fact_and_json_adversaries_fail_before_publication(self):
        mutations = (
            lambda value: value.__setitem__("private_debug", "secret"),
            lambda value: value["network"].__setitem__("private_debug", "secret"),
            lambda value: value["performance"]["samples"][0].__setitem__(
                "private_debug", "secret",
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory(prefix="release-snapshot-adversary-") as tmp:
                    root = Path(tmp) / "package"
                    shutil.copytree(self.template, root)
                    path = root / "snapshots" / "latest.json"
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    mutation(snapshot)
                    write_snapshot(root, snapshot)
                    with self.assertRaisesRegex(
                        verify_release.ReleaseVerificationError,
                        "recursive public schema",
                    ):
                        verify_release.verify_public_data(root=root, now=VERIFY_NOW)

        facts_path = self.root / "history" / "facts.jsonl"
        rows = [json.loads(line) for line in facts_path.read_text().splitlines()]
        rows[0]["private_debug"] = "secret"
        facts_path.write_bytes(b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        ))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "fields mismatch",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        facts_path = self.root / "history" / "facts.jsonl"
        rows = [json.loads(line) for line in facts_path.read_text().splitlines()]
        sample = next(row for row in rows if row["metric_id"] == "performance_sample_tps")
        sample["coverage"]["private_debug"] = "secret"
        facts_path.write_bytes(b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        ))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "coverage fields mismatch",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        facts_path = self.root / "history" / "facts.jsonl"
        rows = [json.loads(line) for line in facts_path.read_text().splitlines()]
        rows[0]["quality"] = "/Users/ashton/private-receipt.json"
        facts_path.write_bytes(b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        ))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "prohibited private",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        facts_path = self.root / "history" / "facts.jsonl"
        rows = [json.loads(line) for line in facts_path.read_text().splitlines()]
        rows[0]["collected_at"] = "2035-01-01T00:00:00+00:00"
        rows = facts.dedupe_facts(rows)
        facts_path.write_bytes(b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        ))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "after the selected snapshot",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        facts_path = self.root / "history" / "facts.jsonl"
        rows = [json.loads(line) for line in facts_path.read_text().splitlines()]
        row = rows[0]
        row["event_time"] = "2035-01-01T00:00:00+00:00"
        rows = facts.dedupe_facts(rows)
        facts_path.write_bytes(b"".join(
            (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for item in rows
        ))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "event_time is after collection",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        latest = self.root / "snapshots" / "latest.json"
        raw = latest.read_bytes()
        latest.write_bytes(b'{"schema_version":9,' + raw[1:])
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "duplicate JSON key",
        ):
            verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)

    def test_xstock_fact_and_state_nested_fields_are_exact(self):
        snapshot = xstock_fact_snapshot(observed=1)
        fact = next(
            item for item in facts.snapshot_facts(snapshot)
            if item["metric_id"] == facts.XSTOCK_METRIC_ID
        )
        verify_release.verify_fact_record(fact, 1)
        for field, value, message in (
            ("source_schema", "nine", "source_schema"),
            ("source_revision", "nonsense", "source_revision"),
        ):
            malformed = deepcopy(fact)
            malformed[field] = value
            with self.assertRaisesRegex(verify_release.ReleaseVerificationError, message):
                verify_release.verify_fact_record(malformed, 1)
        private_fact = deepcopy(fact)
        private_fact["coverage"]["multiplier_provenance"]["state"][
            "private_debug"
        ] = "secret"
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "state fields mismatch",
        ):
            verify_release.verify_fact_record(private_fact, 1)

        endpoint = "https://api.mainnet.solana.com"
        snapshot["source"] = {
            "endpoint_identity": "sha256:" + hashlib.sha256(endpoint.encode()).hexdigest(),
        }
        observations = verify_release._snapshot_state_observations(snapshot)
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "supply state is required",
        ):
            verify_release.verify_state(self.root / "state" / "missing.json", snapshot)
        state = {
            "version": verify_release.growth.SUPPLY_STATE_VERSION,
            "rpc_endpoint_identity": snapshot["source"]["endpoint_identity"],
            "cursor_mint": next(iter(observations)),
            "updated_at": snapshot["collected_at"],
            "observations": deepcopy(observations),
        }
        state_path = self.root / "state" / "xstocks-supply.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_bytes(verify_release.canonical_json(state, sort_keys=True))
        verify_release.verify_state(state_path, snapshot)

        unavailable = deepcopy(snapshot)
        unavailable["growth"]["tokenized_equities"]["all_assets"] = []
        state["cursor_mint"] = "attempted-mint-without-an-observation"
        state_path.write_bytes(verify_release.canonical_json(state, sort_keys=True))
        verify_release.verify_state(state_path, unavailable)

        state["observations"][next(iter(observations))]["multiplier_provenance"][
            "private_debug"
        ] = "secret"
        state_path.write_bytes(verify_release.canonical_json(state, sort_keys=True))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "fields mismatch",
        ):
            verify_release.verify_state(state_path, snapshot)

    def test_dirty_stale_and_tampered_packages_are_rejected(self):
        report_path = self.root / "samples" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["release"]["renderer"]["source_tree_dirty"] = True
        report_path.write_bytes(verify_release.canonical_json(report))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "source trees must both be clean",
        ):
            self.verify()

        for name, old, new in (
            ("index.html", "Solana Ecosystem Report", "Altered Ecosystem Report"),
            ("report.md", "# Solana Ecosystem Report", "# Altered Ecosystem Report"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix="release-artifact-adversary-",
            ) as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                path = root / "samples" / name
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text)
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError,
                    "does not match deterministic renderer output",
                ):
                    verify_release.verify_package(
                        root, root / "samples",
                        manifest_path=root / "release-manifest.json", check_git=False,
                        now=VERIFY_NOW,
                    )

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        manifest_path = self.root / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["samples/index.html"]["sha256"] = "0" * 64
        manifest_path.write_bytes(verify_release.canonical_json(manifest, sort_keys=True))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "manifest does not match",
        ):
            self.verify()

        shutil.copytree(self.template, self.root, dirs_exist_ok=True)
        self.mutate_snapshot(lambda value: value["network"].__setitem__("slot", 501))
        with self.assertRaises(verify_release.ReleaseVerificationError):
            self.verify()

    def test_derived_envelope_is_rebuilt_from_validated_history(self):
        for mutation in ("upgrades", "history", "history numeric type", "paired anomaly"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="release-derived-envelope-adversary-",
            ) as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                samples = root / "samples"
                report_path = samples / "report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if mutation == "upgrades":
                    report["upgrades"]["label"] = "Fabricated upgrade feed"
                elif mutation == "history":
                    report["history"]["series"]["latest_tps"]["label"] = (
                        "Fabricated throughput history"
                    )
                elif mutation == "history numeric type":
                    report["history"]["snapshots"] = float(
                        report["history"]["snapshots"]
                    )
                else:
                    report["anomalies"]["message"] = "Fabricated anomaly analysis"
                report_path.write_bytes(verify_release.canonical_json(report))

                data = verify_release.verify_public_data(root=root, now=VERIFY_NOW)
                if mutation == "paired anomaly":
                    snapshot = data["snapshot"]
                    artifact_snapshot = render.project_public_envelope({
                        **snapshot,
                        "release": report["release"],
                    })
                    charted = facts.publication_history(
                        render.history_for(snapshot, data["history"]), selected=snapshot,
                    )
                    notice = render.public_history_omission_notice(
                        render.public_history_omissions(charted), len(charted),
                    )
                    (samples / "report.md").write_text(
                        render.render_markdown(
                            artifact_snapshot, report["anomalies"], report["delta"],
                            charted, observations=report["observations"],
                            history_projection_notice=notice,
                        ),
                        encoding="utf-8",
                    )
                    (samples / "index.html").write_text(
                        render.publish(
                            artifact_snapshot, report["anomalies"], report["delta"],
                            charted, "Recorded snapshot",
                            observations=report["observations"],
                            history_projection_notice=notice,
                        ),
                        encoding="utf-8",
                    )

                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError,
                    "deterministic renderer envelope",
                ):
                    verify_release.verify_artifacts(samples, data)

    def test_required_routes_carousel_rasters_and_csp_cannot_disappear(self):
        cases = (
            ("id='project'", "id='removed'", "project route anchor"),
            ('<meta http-equiv="Content-Security-Policy"', '<meta data-removed="csp"',
             "exact CSP once"),
            ("data-pulse-track", "data-removed-pulse-track", "Overview chart carousel"),
            ("data:image/png;base64,", "data:image/gif;base64,",
             "Project raster illustrations"),
            ("data:image/webp;base64,", "data:image/gif;base64,",
             "Project editorial imagery"),
        )
        for old, new, message in cases:
            with self.subTest(marker=old), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                path = root / "samples" / "index.html"
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text)
                path.write_text(text.replace(old, new), encoding="utf-8")
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError, message,
                ):
                    verify_release.verify_package(
                        root, root / "samples",
                        manifest_path=root / "release-manifest.json", check_git=False,
                        now=VERIFY_NOW,
                    )

    def test_pending_fact_append_is_exact_and_observation_digest_is_bound(self):
        data = verify_release.verify_public_data(root=self.root, now=VERIFY_NOW)
        self.assertEqual(
            verify_release.expected_pending_facts(b"", data["snapshot"]),
            data["facts_raw"],
        )

        report_path = self.root / "samples" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["release"]["observation_contract_sha256"] = "0" * 64
        report_path.write_bytes(verify_release.canonical_json(report))
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError, "observation contract digest",
        ):
            self.verify()

    def test_generated_at_must_follow_collection_and_precede_verification(self):
        for generated_at in (
            "2026-08-31T09:59:59+00:00",
            "2035-01-01T00:00:00+00:00",
        ):
            with self.subTest(generated_at=generated_at), tempfile.TemporaryDirectory(
                prefix="release-generated-at-adversary-",
            ) as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                report_path = root / "samples" / "report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                original = report["release"]["generated_at"]
                report["release"]["generated_at"] = generated_at
                report["release"]["update_status"]["as_of"] = generated_at
                report_path.write_bytes(verify_release.canonical_json(report))
                for name in ("index.html", "report.md"):
                    path = root / "samples" / name
                    path.write_text(
                        path.read_text(encoding="utf-8").replace(original, generated_at),
                        encoding="utf-8",
                    )
                data = verify_release.verify_public_data(root=root, now=VERIFY_NOW)
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError,
                    "generated_at must be between collection and verification time",
                ):
                    verify_release.verify_artifacts(root / "samples", data)

    def test_observation_and_derived_root_shapes_are_complete(self):
        cases = ("observation", "history")
        for mutation in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="release-contract-adversary-",
            ) as tmp:
                root = Path(tmp) / "package"
                shutil.copytree(self.template, root)
                report_path = root / "samples" / "report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if mutation == "observation":
                    del report["observations"][0]["metric_id"]
                    message = "report observation 0 fields mismatch"
                else:
                    del report["history"]
                    message = "report.json root fields mismatch"
                report_path.write_bytes(verify_release.canonical_json(report))
                data = verify_release.verify_public_data(root=root, now=VERIFY_NOW)
                with self.assertRaisesRegex(
                    verify_release.ReleaseVerificationError, message,
                ):
                    verify_release.verify_artifacts(root / "samples", data)

    def test_report_observation_accepts_bare_source_dates(self):
        """Provider-row source dates stay bare dates in report observations.

        The facts contract records provider observations as dates and never
        invents a time of day; report verification interprets such
        observed_at values as the start of that day in UTC, matching
        fact-level ordering semantics.
        """
        record = {
            "observation_id": facts.public_observation_id(
                record_kind="direct",
                metric_id="stablecoin_active_address_provider_range",
                subject_id="Allium",
                snapshot_collected_at="2026-09-01T21:59:02+00:00",
                observed_at="2026-08-31",
                observed_slot=None,
                source="solana-data-provider-benchmark",
            ),
            "record_kind": "direct",
            "metric_id": "stablecoin_active_address_provider_range",
            "subject_id": "Allium",
            "name": "Stablecoin active-address provider observation",
            "value": 12345.0,
            "type": "numeric",
            "unit": "addresses",
            "population": "provider observation",
            "denominator": "not applicable",
            "window": "provider observation date",
            "observed_at": "2026-08-31",
            "observed_slot": None,
            "collected_at": "2026-09-01T21:59:02+00:00",
            "snapshot_collected_at": "2026-09-01T21:59:02+00:00",
            "source": "solana-data-provider-benchmark",
            "source_url": "https://solana.com/data",
            "source_path": "growth.daily_active_addresses.provider_observations",
            "collection_method": "retain source-labelled provider rows",
            "calculation_method": "recorded provider observation",
            "freshness": "fresh",
            "status": "current",
            "basis": "recorded",
            "quality": "not applicable",
            "caveat": "Provider methodologies differ.",
            "input_observation_ids": [],
            "output_path": "observations[observation_id=%s]" % json.dumps(
                facts.public_observation_id(
                    record_kind="direct",
                    metric_id="stablecoin_active_address_provider_range",
                    subject_id="Allium",
                    snapshot_collected_at="2026-09-01T21:59:02+00:00",
                    observed_at="2026-08-31",
                    observed_slot=None,
                    source="solana-data-provider-benchmark",
                )
            ),
        }
        snapshot = {"collected_at": "2026-09-01T21:59:02+00:00"}
        now = verify_release.reference_time("2026-09-01T22:00:00+00:00")
        known = verify_release.verify_observations([record], snapshot, now)
        self.assertEqual(len(known), 1)
        stale = dict(record, observed_at="2026-09-02")
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError,
            r"report observation 0\.observed_at is after",
        ):
            verify_release.verify_observations([stale], snapshot, now)
        invalid = dict(record, observed_at="2026-08-05T12:00")
        with self.assertRaisesRegex(
            verify_release.ReleaseVerificationError,
            r"report observation 0\.observed_at",
        ):
            verify_release.verify_observations([invalid], snapshot, now)


class TestCleanInitialGitHistory(unittest.TestCase):
    def _build_repo(
        self, *, source_contains_data=False, with_package=True,
        extra_data=False, executable_data=False, extra_package=False,
    ):
        tmp = tempfile.TemporaryDirectory(prefix="release-clean-history-")
        root = Path(tmp.name) / "repo"
        root.mkdir()
        verify_release._git(root, "init", "-q")
        verify_release._git(root, "config", "user.name", "Release Test")
        verify_release._git(root, "config", "user.email", "release@example.invalid")

        source_snapshot = candidate("2026-08-31T09:00:00+00:00")
        if source_contains_data:
            write_snapshot(root, source_snapshot)
            facts.append_jsonl(root / "history" / "facts.jsonl",
                               facts.snapshot_facts(source_snapshot))
        (root / "source.txt").write_text("source\n", encoding="utf-8")
        source_paths = ["source.txt"]
        if source_contains_data:
            source_paths.extend([
                "snapshots/latest.json",
                "snapshots/snapshot-20260831T090000+0000.json",
                "history/facts.jsonl",
            ])
        verify_release._git(root, "add", "--", *source_paths)
        verify_release._git(root, "commit", "-q", "-m", "source")
        source_revision = verify_release._git(
            root, "rev-parse", "HEAD",
        ).stdout.decode().strip()

        snapshot = candidate("2026-08-31T10:00:00+00:00")
        snapshot["provenance"]["source_revision"] = source_revision
        write_snapshot(root, snapshot)
        facts_path = root / "history" / "facts.jsonl"
        facts.append_jsonl(facts_path, facts.snapshot_facts(snapshot))
        immutable = root / "snapshots" / collect.snapshot_filename(snapshot["collected_at"])
        data_paths = [
            immutable.relative_to(root).as_posix(),
            "snapshots/latest.json",
            "history/facts.jsonl",
        ]
        if extra_data:
            (root / "snapshots" / "unapproved.json").write_text(
                "unexpected\n", encoding="utf-8",
            )
            data_paths.append("snapshots/unapproved.json")
        if executable_data:
            (root / "snapshots" / "latest.json").chmod(0o755)
        verify_release._git(root, "add", "--", *data_paths)
        verify_release._git(root, "commit", "-q", "-m", "data")
        data_revision = verify_release._git(
            root, "rev-parse", "HEAD",
        ).stdout.decode().strip()
        data = {
            "immutable": immutable,
            "snapshot": snapshot,
            "facts_raw": facts_path.read_bytes(),
            "state_raw": None,
        }
        report = {"release": {
            "collector": {"source_revision": source_revision},
            "renderer": {"source_revision": data_revision},
        }}
        manifest = None
        if with_package:
            artifacts = root / "samples"
            artifacts.mkdir()
            artifact_records = {}
            for name in verify_release.ARTIFACT_NAMES:
                raw = f"{name}\n".encode("utf-8")
                (artifacts / name).write_bytes(raw)
                artifact_records[f"samples/{name}"] = {
                    "sha256": verify_release.sha256(raw), "bytes": len(raw),
                }
            manifest = {"schema_version": 1, "artifacts": artifact_records}
            (root / "release-manifest.json").write_bytes(
                verify_release.canonical_json(manifest, sort_keys=True)
            )
            if extra_package:
                (artifacts / "unapproved.txt").write_text(
                    "unexpected\n", encoding="utf-8",
                )
            verify_release._git(
                root, "add", "--", "release-manifest.json", "samples",
            )
            verify_release._git(root, "commit", "-q", "-m", "package")
        return tmp, root, data, report, manifest

    def test_clean_initial_history_binds_root_data_package_and_head(self):
        tmp, root, data, report, manifest = self._build_repo()
        try:
            verify_release.verify_git_revisions(root, data, report, manifest)

            (root / "source.txt").write_text("source\nreviewed\n", encoding="utf-8")
            verify_release._git(root, "add", "--", "source.txt")
            verify_release._git(root, "commit", "-q", "-m", "source review")
            verify_release.verify_git_revisions(root, data, report, manifest)

            (root / "samples" / "report.md").write_text("changed\n", encoding="utf-8")
            verify_release._git(root, "add", "--", "samples/report.md")
            verify_release._git(root, "commit", "-q", "-m", "tamper package")
            with self.assertRaisesRegex(
                verify_release.ReleaseVerificationError,
                "package revision must be the single direct child",
            ):
                verify_release.verify_git_revisions(root, data, report, manifest)
        finally:
            tmp.cleanup()

    def test_initial_source_commit_cannot_contain_public_data(self):
        tmp, root, data, report, manifest = self._build_repo(source_contains_data=True)
        try:
            with self.assertRaisesRegex(
                verify_release.ReleaseVerificationError, "source root commit",
            ):
                verify_release.verify_git_revisions(root, data, report, manifest)
        finally:
            tmp.cleanup()

    def test_initial_data_and_package_inventories_are_exact(self):
        cases = (
            ({"extra_data": True}, "source-to-data path set"),
            ({"executable_data": True}, "data commit protected path inventory"),
            ({"extra_package": True}, "data-to-package path set"),
        )
        for options, message in cases:
            with self.subTest(options=options):
                tmp, root, data, report, manifest = self._build_repo(**options)
                try:
                    with self.assertRaisesRegex(
                        verify_release.ReleaseVerificationError, message,
                    ):
                        verify_release.verify_git_revisions(root, data, report, manifest)
                finally:
                    tmp.cleanup()

    def test_manifestless_update_path_remains_incremental(self):
        tmp, root, data, report, manifest = self._build_repo(with_package=False)
        try:
            self.assertIsNone(manifest)
            verify_release.verify_git_revisions(root, data, report, None)
        finally:
            tmp.cleanup()


class TestGitReleaseTransitions(unittest.TestCase):
    def test_only_data_then_package_paths_may_change(self):
        cases = (
            (None, None, "exact", "2026-08-31T10:00:00+00:00", False, False, None),
            ("unrelated-data.txt", None, "exact", "2026-08-31T10:00:00+00:00", False, False,
             "source-to-data path set"),
            (None, None, "rewrite", "2026-08-31T10:00:00+00:00", False, False,
             "committed facts are not the exact append"),
            (None, None, "exact", "2026-08-31T08:00:00+00:00", False, False,
             "candidate snapshot is not newer"),
            (None, None, "exact", "2026-08-31T10:00:00+00:00", True, False,
             "trusted base-to-source range changes public data"),
            (None, None, "exact", "2026-08-31T10:00:00+00:00", False, True,
             "data revision-to-HEAD range changes public data"),
        )
        for (
            extra_data, extra_package, history_mode, collected_at,
            rewrite_source, leak_after_data, failure,
        ) in cases:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(
                prefix="release-git-transition-",
            ) as tmp:
                root = Path(tmp) / "repo"
                root.mkdir()
                verify_release._git(root, "init", "-q")
                verify_release._git(root, "config", "user.name", "Release Test")
                verify_release._git(root, "config", "user.email", "release@example.invalid")
                (root / "source.txt").write_text("source\n", encoding="utf-8")
                previous = candidate("2026-08-31T09:00:00+00:00")
                previous["network"]["slot"] = 400
                previous["performance"]["samples"][0]["slot"] = 400
                write_snapshot(root, previous)
                facts_path = root / "history" / "facts.jsonl"
                facts.append_jsonl(facts_path, facts.snapshot_facts(previous))
                verify_release._git(
                    root, "add", "--", "source.txt", "snapshots", "history/facts.jsonl",
                )
                verify_release._git(root, "commit", "-q", "-m", "source")
                source_revision = verify_release._git(
                    root, "rev-parse", "HEAD",
                ).stdout.decode().strip()
                base_revision = source_revision
                if rewrite_source:
                    rewritten = candidate("2026-08-31T09:30:00+00:00")
                    rewritten["network"]["slot"] = 450
                    rewritten["performance"]["samples"][0]["slot"] = 450
                    write_snapshot(root, rewritten)
                    facts_path.write_bytes(
                        verify_release.expected_pending_facts(b"", rewritten)
                    )
                    verify_release._git(
                        root, "add", "--", "snapshots", "history/facts.jsonl",
                    )
                    verify_release._git(root, "commit", "-q", "-m", "rewrite source")
                    source_revision = verify_release._git(
                        root, "rev-parse", "HEAD",
                    ).stdout.decode().strip()

                snapshot = candidate(collected_at)
                snapshot["provenance"]["source_revision"] = source_revision
                write_snapshot(root, snapshot)
                immutable = root / "snapshots" / collect.snapshot_filename(collected_at)
                latest = root / "snapshots" / "latest.json"
                if history_mode == "rewrite":
                    facts_path.write_bytes(verify_release.expected_pending_facts(b"", snapshot))
                else:
                    facts.append_jsonl(facts_path, facts.snapshot_facts(snapshot))
                data_paths = [
                    immutable.relative_to(root).as_posix(),
                    latest.relative_to(root).as_posix(),
                    facts_path.relative_to(root).as_posix(),
                ]
                if extra_data:
                    (root / extra_data).write_text("unexpected\n", encoding="utf-8")
                    data_paths.append(extra_data)
                verify_release._git(root, "add", "--", *data_paths)
                verify_release._git(root, "commit", "-q", "-m", "data")
                data_revision = verify_release._git(
                    root, "rev-parse", "HEAD",
                ).stdout.decode().strip()

                package_paths = [
                    "release-manifest.json", "samples/index.html",
                    "samples/report.md", "samples/report.json",
                ]
                for relative in package_paths:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"{relative}\n", encoding="utf-8")
                if extra_package:
                    (root / extra_package).write_text("unexpected\n", encoding="utf-8")
                    package_paths.append(extra_package)
                verify_release._git(root, "add", "--", *package_paths)
                verify_release._git(root, "commit", "-q", "-m", "package")

                base_branch = verify_release._git(
                    root, "branch", "--show-current",
                ).stdout.decode().strip()
                verify_release._git(root, "switch", "-q", "-c", "code-change")
                if leak_after_data:
                    leaked = root / "snapshots" / "private.json"
                    leaked.write_text("private\n", encoding="utf-8")
                    verify_release._git(root, "add", "--", "snapshots/private.json")
                    verify_release._git(root, "commit", "-q", "-m", "add private data")
                    leaked.unlink()
                    verify_release._git(root, "add", "-u", "--", "snapshots/private.json")
                    verify_release._git(root, "commit", "-q", "-m", "remove private data")
                (root / "source.txt").write_text("source\ncode change\n", encoding="utf-8")
                verify_release._git(root, "add", "--", "source.txt")
                verify_release._git(root, "commit", "-q", "-m", "code change")
                verify_release._git(root, "switch", "-q", base_branch)
                verify_release._git(
                    root, "merge", "-q", "--no-ff", "-m", "merge code change", "code-change",
                )
                self.assertEqual(
                    len(verify_release.commit_parents(root, "HEAD")), 2,
                    "fixture must exercise a synthetic merge-shaped HEAD",
                )

                data = {
                    "immutable": immutable,
                    "snapshot": snapshot,
                    "facts_raw": facts_path.read_bytes(),
                    "state_raw": None,
                }
                report = {"release": {
                    "collector": {"source_revision": source_revision},
                    "renderer": {"source_revision": data_revision},
                }}
                manifest = {"artifacts": {
                    f"samples/{name}": {} for name in verify_release.ARTIFACT_NAMES
                }}
                committed_failure = failure if failure and (
                    "source-to-data" in failure
                    or "committed facts" in failure
                    or "candidate snapshot" in failure
                    or "trusted base-to-source" in failure
                    or "data revision-to-HEAD" in failure
                ) else None
                if committed_failure is None:
                    verify_release.verify_committed_data(root, data, base_revision)
                else:
                    with self.assertRaisesRegex(
                        verify_release.ReleaseVerificationError, committed_failure,
                    ):
                        verify_release.verify_committed_data(root, data, base_revision)


class TestPythonCompatibility(unittest.TestCase):
    def test_verifier_parses_with_python_3_10(self):
        import subprocess

        python = shutil.which("python3.10")
        if python is None:
            self.skipTest("python3.10 is not installed")
        completed = subprocess.run(
            [python, "-m", "py_compile", str(verify_release.__file__)],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
