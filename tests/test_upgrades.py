"""Offline contracts for future-gated licensed Solana content transforms."""

import io
import hashlib
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import upgrades  # noqa: E402


LICENSE = """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007
"""
TEST_LICENSE_SHA256 = hashlib.sha256(LICENSE.encode()).hexdigest()

POST = """---
title: "A published update"
status: published
author: solana-foundation
publishedAt: 2026-08-24T12:00:00.000Z
description: This must not be retained.
heroImage: /private/image.webp
---
Body that must not be retained.
"""

UPGRADE = """---
title: Reduced Slot Times
status: published
author: solana-foundation
publishedAt: 2026-08-24T11:00:00.000Z
stage: pending_activation
release: agave-4-2
description: This must not be retained.
---
[SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/pull/525)
[SIMD-0437](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0437-incremental-rent-reduction.md)
"""


def archive(files, source_commit, include_license=True):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as bundle:
        rows = ([('LICENSE', LICENSE)] if include_license else []) + list(files)
        for path, text in rows:
            payload = text if isinstance(text, bytes) else text.encode()
            info = tarfile.TarInfo(f"solana-com-{source_commit}/{path}")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    return data.getvalue()


class TestLicensedContentArchive(unittest.TestCase):
    COMMIT = "a" * 40

    def parse(self, files, collected_at="2026-08-25T00:00:00Z", **kwargs):
        with mock.patch.object(upgrades, "SOLANA_COM_LICENSE_SHA256", TEST_LICENSE_SHA256):
            return upgrades.parse_solana_content_archive(
                archive(files, self.COMMIT, **kwargs), self.COMMIT, collected_at,
            )

    def parse_body(self, body, collected_at="2026-08-25T00:00:00Z"):
        with mock.patch.object(upgrades, "SOLANA_COM_LICENSE_SHA256", TEST_LICENSE_SHA256):
            return upgrades.parse_solana_content_archive(body, self.COMMIT, collected_at)

    def test_news_keeps_only_published_as_of_metadata_with_pinned_provenance(self):
        draft = POST.replace("status: published", "status: draft")
        future = POST.replace("2026-08-24T12:00:00.000Z", "2026-08-26T12:00:00.000Z")
        content = self.parse([
            ("apps/media/content/posts/published-update.mdx", POST),
            ("apps/media/content/posts/draft-update.mdx", draft),
            ("apps/media/content/posts/future-update.mdx", future),
        ])

        self.assertEqual(content["license"], "GPL-3.0")
        self.assertIn(content["source_commit"], content["license_url"])
        self.assertEqual(content["posts"]["record_count"], 1)
        row = content["posts"]["records"][0]
        self.assertEqual(row["id"], "solana-news:published-update")
        self.assertEqual(row["slug"], "published-update")
        self.assertEqual(row["title"], "A published update")
        self.assertEqual(row["author_id"], "solana-foundation")
        self.assertEqual(row["published"], "2026-08-24T12:00:00Z")
        self.assertEqual(row["link"], "https://solana.com/news/published-update")
        self.assertEqual(row["source_path"], "apps/media/content/posts/published-update.mdx")
        self.assertEqual(row["source_commit"], self.COMMIT)
        self.assertEqual(row["license"], "GPL-3.0")
        for forbidden in ("body", "description", "heroImage", "summary", "image"):
            self.assertNotIn(forbidden, row)

    def test_upgrade_pages_are_editorial_lifecycle_not_simd_status(self):
        second = UPGRADE.replace("Reduced Slot Times", "Rent Reduction").replace(
            "pending_activation", "live",
        )
        content = self.parse([
            ("apps/media/content/upgrades/reduced-slot-times.mdx", UPGRADE),
            ("apps/media/content/upgrades/rent-reduction.mdx", second),
        ])

        rows = content["upgrades"]["records"]
        self.assertEqual({row["stage"] for row in rows}, {"pending_activation", "live"})
        first = next(row for row in rows if row["slug"] == "reduced-slot-times")
        self.assertEqual(first["release_slug"], "agave-4-2")
        self.assertEqual(first["link"], "https://solana.com/upgrades/reduced-slot-times")
        self.assertEqual(
            [reference["identifier"] for reference in first["simd_references"]],
            ["SIMD-0437", "SIMD-0525"],
        )
        for reference in first["simd_references"]:
            self.assertNotIn("stage", reference)
            self.assertNotIn("status", reference)
        self.assertEqual(content["upgrades"]["lifecycle_basis"], "official curated upgrade lifecycle")
        self.assertIn("editorial", content["upgrades"]["note"])
        self.assertIn("not", content["upgrades"]["note"])

    def test_all_explicit_stages_are_accepted_and_unknown_stage_is_partial(self):
        files = [(
            f"apps/media/content/upgrades/{stage.replace('_', '-')}.mdx",
            UPGRADE.replace("pending_activation", stage),
        ) for stage in sorted(upgrades.UPGRADE_STAGES)]
        files.append((
            "apps/media/content/upgrades/unknown.mdx",
            UPGRADE.replace("pending_activation", "rumoured"),
        ))
        content = self.parse(files)

        self.assertEqual(
            {row["stage"] for row in content["upgrades"]["records"]},
            upgrades.UPGRADE_STAGES,
        )
        self.assertTrue(content["upgrades"]["partial"])
        self.assertEqual(
            content["upgrades"]["unparsed_paths"],
            ["apps/media/content/upgrades/unknown.mdx"],
        )

    def test_unknown_or_missing_publication_status_is_partial_not_excluded(self):
        unknown = POST.replace("status: published", "status: publshed")
        missing = POST.replace("status: published\n", "")
        draft = POST.replace("status: published", "status: draft")
        content = self.parse([
            ("apps/media/content/posts/unknown.mdx", unknown),
            ("apps/media/content/posts/missing.mdx", missing),
            ("apps/media/content/posts/draft.mdx", draft),
        ])

        self.assertTrue(content["posts"]["partial"])
        self.assertFalse(content["posts"]["coverage_complete"])
        self.assertEqual(content["posts"]["excluded_count"], 1)
        self.assertEqual(content["posts"]["invalid_item_count"], 2)
        self.assertEqual(content["posts"]["unparsed_paths"], [
            "apps/media/content/posts/missing.mdx",
            "apps/media/content/posts/unknown.mdx",
        ])

    def test_unscheduled_upgrade_is_valid_with_a_nullable_release(self):
        content = self.parse([
            ("apps/media/content/upgrades/unscheduled.mdx",
             UPGRADE.replace("release: agave-4-2\n", "")),
        ])

        self.assertFalse(content["upgrades"]["partial"])
        self.assertIsNone(content["upgrades"]["records"][0]["release_slug"])

    def test_invalid_document_degrades_only_its_section(self):
        conflict = UPGRADE.replace("/pull/525", "/pull/526")
        content = self.parse([
            ("apps/media/content/posts/update.mdx", POST),
            ("apps/media/content/upgrades/conflict.mdx", conflict),
        ])

        self.assertEqual(content["posts"]["record_count"], 1)
        self.assertFalse(content["posts"]["partial"])
        self.assertEqual(content["upgrades"]["record_count"], 0)
        self.assertTrue(content["upgrades"]["partial"])
        self.assertEqual(content["upgrades"]["unparsed_paths"], [
            "apps/media/content/upgrades/conflict.mdx",
        ])

    def test_duplicate_frontmatter_key_degrades_only_its_record(self):
        invalid = POST.replace("status: published", "status: published\nstatus: draft")
        content = self.parse([
            ("apps/media/content/posts/invalid.mdx", invalid),
            ("apps/media/content/upgrades/valid.mdx", UPGRADE),
        ])

        self.assertEqual(content["posts"]["record_count"], 0)
        self.assertTrue(content["posts"]["partial"])
        self.assertEqual(content["posts"]["unparsed_paths"], [
            "apps/media/content/posts/invalid.mdx",
        ])
        self.assertEqual(content["upgrades"]["record_count"], 1)
        self.assertFalse(content["upgrades"]["partial"])

    def test_invalid_utf8_document_is_partial_not_archive_failure(self):
        content = self.parse([
            ("apps/media/content/posts/update.mdx", POST),
            ("apps/media/content/upgrades/invalid.mdx", b"\xff\xfe"),
        ])

        self.assertEqual(content["posts"]["record_count"], 1)
        self.assertTrue(content["upgrades"]["partial"])
        self.assertEqual(content["upgrades"]["unparsed_paths"], [
            "apps/media/content/upgrades/invalid.mdx",
        ])

    def test_missing_or_wrong_license_fails_closed(self):
        files = [("apps/media/content/posts/update.mdx", POST)]
        self.assertIsNone(self.parse_body(
            archive(files, self.COMMIT, include_license=False),
        ))
        wrong = archive([("LICENSE", "MIT License"), *files], self.COMMIT,
                        include_license=False)
        self.assertIsNone(self.parse_body(wrong))

    def test_audited_license_hash_is_pinned(self):
        self.assertEqual(
            upgrades.SOLANA_COM_LICENSE_SHA256,
            "f817886ee6bb65ed3098a7987b1e9781653d15c71f85589ce0d2af663c66d373",
        )

    def test_duplicate_archive_path_fails_closed(self):
        duplicate = archive([
            ("apps/media/content/posts/update.mdx", POST),
            ("apps/media/content/posts/update.mdx", POST),
        ], self.COMMIT)
        self.assertIsNone(self.parse_body(duplicate))

    def test_archive_root_and_collection_time_are_pinned(self):
        wrong_root = archive(
            [("apps/media/content/posts/update.mdx", POST)], "b" * 40,
        )
        self.assertIsNone(self.parse_body(wrong_root))
        with self.assertRaises(ValueError):
            upgrades.solana_com_archive_url("main")


if __name__ == "__main__":
    unittest.main()
