#!/usr/bin/env python3
"""Generate missing 4K release artwork from the latest recorded snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+(?:-beta\.\d+)?")


def recorded_releases(snapshot: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """Return the newest recorded Agave releases that can name an artwork file."""
    news = snapshot.get("news") if isinstance(snapshot.get("news"), dict) else {}
    sources = news.get("sources") if isinstance(news.get("sources"), dict) else {}
    source = sources.get("agave_releases") if isinstance(sources.get("agave_releases"), dict) else {}
    rows = source.get("items") if isinstance(source.get("items"), list) else []
    releases = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tag = str(row.get("tag") or "")
        if TAG_PATTERN.fullmatch(tag):
            releases.append({"tag": tag, "published": str(row.get("published") or "")})
    return sorted(releases, key=lambda row: row["published"], reverse=True)[:limit]


def release_prompt(tag: str) -> str:
    """Build the approved dot-field prompt with exact release typography."""
    match = re.fullmatch(r"(v\d+\.\d+\.\d+)(?:-beta\.(\d+))?", tag)
    if match is None:
        raise ValueError(f"unsupported release tag: {tag}")
    version, beta = match.groups()
    number = f"{int(beta):02d}" if beta is not None else version.rsplit(".", 1)[-1].zfill(2)
    phase = int(beta or 2)
    pattern = (
        "a sparse field beginning to form"
        if phase == 0
        else "an ascending connected field suggesting measured progress"
        if phase == 1
        else "an established stepped field suggesting measured stability"
    )
    label = "BETA" if beta is not None else "RELEASE"
    return f"""Use case: ads-marketing
Asset type: 16:9 editorial release artwork for the Solana Ecosystem Report
Primary request: Create a native 3840x2160 editorial graphic for Anza Agave {tag}; exact geometric construction, not pixel art.
Scene/backdrop: one perfectly flat chroma-key green #00FF00 background, with no green in the artwork
Subject: {pattern}, built only from crisp perfectly circular violet dots with subtle depth
Style/medium: mature Swiss editorial design; precise vector-like edges; restrained Apple/Stripe-level finish
Composition/framing: strict rule of thirds; typography in the lower-left third; dot field across the middle and right thirds; generous negative space; text visibly layered above the dots
Text (verbatim): "{label} {number}" and "ANZA / {version}"
Typography: clean grotesk sans serif; exact spelling; sharp at 4K; small uppercase label, large restrained number, small tracking-heavy signature
Color palette: violet #8B6CF6 to #B9A5FF with restrained near-black detail
Constraints: exact text; clean circular grid; no extra copy; no logo; no icon; no hardware; no watermark
Avoid: blurry text, chunky halos, jagged edges, pixel art, 8-bit style, photographic imagery, sci-fi circuitry, decorative clutter"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "snapshots" / "latest.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "assets" / "editorial" / "releases")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    releases = recorded_releases(snapshot, max(args.limit, 0))
    pending = [row for row in releases if args.force or not (args.out_dir / f"{row['tag']}.png").exists()]
    if args.dry_run:
        print(json.dumps([{"tag": row["tag"], "prompt": release_prompt(row["tag"])} for row in pending], indent=2))
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required")

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    image_cli = Path(os.environ.get("IMAGE_GEN_CLI", codex_home / "skills/.system/imagegen/scripts/image_gen.py"))
    chroma_cli = image_cli.with_name("remove_chroma_key.py")
    if not image_cli.is_file() or not chroma_cli.is_file():
        parser.error("set IMAGE_GEN_CLI to the bundled image_gen.py path")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="solana-release-art-") as temp_dir:
        for row in pending:
            target = args.out_dir / f"{row['tag']}.png"
            chroma = Path(temp_dir) / target.name
            subprocess.run([
                sys.executable, str(image_cli), "generate", "--model", "gpt-image-2",
                "--prompt", release_prompt(row["tag"]), "--size", "3840x2160",
                "--quality", "high", "--output-format", "png", "--no-augment",
                "--out", str(chroma), "--force",
            ], check=True)
            subprocess.run([
                sys.executable, str(chroma_cli), "--input", str(chroma), "--out", str(target),
                "--key-color", "#00ff00", "--soft-matte", "--spill-cleanup", "--force",
            ], check=True)
            print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
