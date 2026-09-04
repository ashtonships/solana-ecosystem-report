# Decision: Project release artwork is the pinned generated PNG stamps

Date: 2026-08-31
Decided by: Ashton (explicit choice, ending a three-flip revert loop)
Scope: the Release card/hero artwork on the Project editorial surface only.

## Canonical artwork

The three generated "release stamp" images (layered-depth 4K renders) are the
approved, canonical release artwork:

| Tag | File | SHA-256 (prefix) |
|---|---|---|
| v4.3.0-beta.0 | `assets/editorial/releases/v4.3.0-beta.0.png` | `1eeb9cfd…` |
| v4.3.0-beta.1 | `assets/editorial/releases/v4.3.0-beta.1.png` | `6a09e43d…` |
| v4.3.0-beta.2 | `assets/editorial/releases/v4.3.0-beta.2.png` | `e9a93aa9…` |

They are pinned by full hash in `render.py::RELEASE_EDITORIAL_ART_ASSETS`,
embedded as `data:image/png;base64` by `editorial_art_css()`, and routed by
`project_editorial_art_markup()` when a release item's title contains one of
the pinned tags (CSS key form: `v4-3-0-beta-<n>`).

## Non-canonical (do not restore over the PNGs)

- The CSS typographic dot-field cards (`project-editorial__release-art`,
  "BETA 00/01/02" stamps) remain in the codebase as the **fallback** for
  release items whose tag is not in `RELEASE_EDITORIAL_ART_ASSETS`. They are
  fallback rendering, not the approved design. Do not "restore" them over the
  pinned PNGs for pinned tags.
- The four `.webp` editorial illustrations (hero-release, agave-release,
  network-status, community-builders) are unchanged and separate; the
  network-status illustration stays untouched by this decision.
- Original agave/server PNGs from the first design pass are superseded.

## Why this doc exists

This artwork was reverted three times on 2026-08-31 by post-compaction agent
sessions that re-guessed "the approved design" after context compaction. If a
future session believes the release PNGs are a regression or substitution:
they are not. This decision, the pinned hashes, and
`test_pinned_release_art_is_pinned_embedded_and_routed` in
`tests/test_render.py` are the source of truth. Changing the release artwork
requires Ashton's explicit approval, not inference from older session context.

## Verification baked in

- `tests/test_render.py`: routing test (pinned tags → PNG art keys;
  unlisted tags → CSS cards) plus pinned-hash/embed/routing test.
- `tests/test_render.py::test_about_snapshot_art_is_pinned_embedded_and_available_to_both_layouts`:
  PNG data-URI count is `2 + len(RELEASE_EDITORIAL_ART_ASSETS)`.
- `verify_release.py`: same counted requirement.
- Render: `python3 -B render.py --snapshot snapshots/latest.json --out-dir preview --replay`
  embeds all three (grep `project-editorial__art--v4-3-0-beta-` in `preview/index.html`).

## Recovery into the September 4 candidate

The approved PNG bytes and hashes above are preserved unchanged. The renderer selects a version stamp only when a displayed release title names that exact tag, and embeds each used stamp once for all desktop/mobile Project and Community News placements. Unused version stamps are not embedded, so snapshots about newer releases do not carry unrelated artwork bytes. Other releases retain the reviewed category artwork; a beta stamp must never label an RC or a different beta.

Current implementation names are `RELEASE_ART_ASSETS`, `used_release_art_tags(snapshot)`, and `project_editorial_art_markup()`. The conditional PNG count is `2 + len(used_release_art_tags(snapshot))`. The historical implementation details above document the original decision; these recovery details describe the current candidate. Regression coverage is `TestSeptemberRendererRecovery.test_release_art_is_exact_tag_pinned_and_embedded_only_when_displayed`.
