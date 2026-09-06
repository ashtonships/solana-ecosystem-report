# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- People evaluating the current condition and direction of the Solana ecosystem.
- Researchers, builders, and automated agents that need source-traceable network, validator, economic, activity, and ecosystem evidence.
- Superteam reviewers assessing comprehensiveness, automation, maintainability, presentation, originality, and technical quality.

## Product Purpose

Turn public Solana data into a current, inspectable report that works in three forms: an interactive HTML dashboard, a human-readable Markdown report, and schema-versioned JSON. Success means a reader can understand what changed, inspect the source and measurement basis, and distinguish recorded facts from samples, estimates, missing data, and interpretation.

## Positioning

The report is generated from append-only snapshots and preserves evidence limits throughout the interface. Missing is never rendered as zero, sampled values remain labelled, historical gaps remain gaps, and every default artifact works offline without API keys or a build step.

## Operating Context

- `collect.py` records the keyless Solana RPC core into append-only `snapshots/`; optional source adapters remain subject to the canonical terms decisions.
- `pipeline.py` gates schema, semantic relationships, freshness, source state, coverage, and collection provenance before accepted writes or publication.
- `facts.py` maintains the versioned per-metric compatibility contract used by `detect.py`, `delta.py`, and `charts.py`; compact facts live in `history/facts.jsonl`.
- `state/xstocks-supply.json` is an optional resumable cursor, not source-of-truth history, and is written only when the gated growth path is enabled.
- `render.py` produces `index.html`, `report.md`, and `report.json` from one selected snapshot and compatible history, with shared release provenance.
- The five report views are Overview, Data, Methods, History, and Project.
- Normal output uses recorded data. Deterministic loading, empty, and error previews exist only for UI testing and are labelled as test states.
- The production workflow requests updates at minutes 7, 22, 37 and 52 of each UTC hour. Automation is enabled as verified on September 6, 2026; GitHub can delay runs, so actual collection and source timestamps define freshness. Source refresh tiers are documented in [operations](docs/operations.md). Failed collection, publication validation or rendering stops that candidate before deployment.

## Capabilities and Constraints

- Python 3.10+ standard library only; no framework, package install, API key, account, or build step for the default path.
- Public Solana JSON-RPC supplies the keyless core. The adopted growth path adds a fixed MIT-licensed xStock mint registry, finalized per-mint supply, selected four-mint stablecoin supply and scoped Solana Data Active Addresses provider rows. Agave and Firedancer releases, Solana Status and watched SIMD frontmatter provide factual metadata and links. Approved CoinGecko Demo price and the registered Dune query are separate keyed adapters. DEX Screener, DeFiLlama, xStocks API, Solana News, curated-upgrade archives and uncleared provider rows remain held under the [source decisions](README.md#optional-ecosystem-sources-and-release-holds).
- The approved Dune window uses included credits only, with a finite allowance through September 14 UTC and no extra spending. Account caps, source cadence and durable reservations remain separate controls; expiry prevents further requests. See [operations](docs/operations.md#paid-sources) for the exact limits and retained trial accounting.
- Optional sources fail independently and render as unavailable rather than becoming zero or blocking unrelated evidence.
- The HTML artifact is self-contained and performs no runtime network request.
- Mobile web is designed first; desktop must remain equally complete and coherent.
- Machine-readable JSON retains full precision, evidence basis, anomalies, deltas, and chart history.
- The activity sample records exact first/last slots and block times; the report uses the observed duration rather than false “24h” wording.
- A fee payer is the first account key. Fee decomposition is a message-signature base-fee lower bound plus an unclassified residual, never a fabricated exact priority-fee split.
- REV is transaction fees plus detected Jito tips over the sampled slot window, with sample-mean dispersion and temporal/endpoint bias stated explicitly.
- Network-wide daily active addresses and exact cross-venue tokenized-asset volume remain unavailable unless a truthful, permitted method is added.
- Selected stablecoin total supply is published only with exact N/4 coverage and is never presented as circulating supply, value, liquidity, depth, or ecosystem-wide composition.
- First visit defaults to System; complete theme choices are Light, Dark, and System.
- Public deployment and the submitted repository URL have separate verification records in [the submission record](SUBMISSION_FREEZE.md) and [post-deadline changelog](POST_DEADLINE_CHANGELOG.md). Local build success does not establish portal timing, acceptance of later changes, an award, KYC or payout.

## Brand Commitments

- Product name: Solana Ecosystem Report.
- Superteam-aligned identity: white and zinc surfaces with focused violet `#5522e0`, Archivo Semi Expanded, and crisp editorial data graphics.
- Voice is precise, calm, evidence-led, and explicit about limitations.
- Mobile and desktop are re-composed for their form factors rather than mechanically resized.

## Evidence on Hand

- Recorded snapshots under `snapshots/`.
- Deterministic UI fixture under `fixtures/sample-snapshot.json`.
- Private QA previews and the release-package HTML, Markdown, and JSON samples remain distinct; their presence alone is not proof of hosted parity.
- Official requirements: Superteam Canada’s “Develop Solana Ecosystem Auto-Updating Report & Interactive Dashboard” listing.
- Primary product reference: `https://solana.com/data`.
- Current implementation and truth-preserving tests in this repository.
- Public source decisions and licences: [README.md](README.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Product Principles

1. Evidence before interpretation.
2. Missing is never zero.
3. Samples and estimates remain visibly labelled.
4. Real collection and deterministic UI fixtures stay separate.
5. Every important state works for people, keyboards, screen readers, and automated agents.

## Release Boundary

A release requires one independent review of the local release candidate and,
after an explicitly approved deployment, a separate independent review of the
exact hosted revision. Local implementation, generated artifacts, commits,
deployment, hosted verification, and portal submission remain distinct states.

## Accessibility & Inclusion

Use semantic HTML, visible keyboard focus, useful accessible names, 44px touch targets where practical, reduced-motion support, explicit text labels beyond color, and layouts without horizontal overflow from 320px mobile through wide desktop viewports.
