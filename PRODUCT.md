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
- The rights-safe path is configured every six hours but remains disabled until the repository variable `REPORT_AUTOMATION_ENABLED=true`; it stops before push/deploy when collection, publication validation, or rendering fails.

## Capabilities and Constraints

- Python 3.10+ standard library only; no framework, package install, API key, account, or build step for the default path.
- Public Solana JSON-RPC supplies the rights-safe core. The bounded growth path may add a fixed MIT-licensed xStock mint registry, finalized per-mint supply, and selected four-mint stablecoin supply. DEX Screener, economic, activity-provider, xStocks API, Solana News, curated-upgrade, and SIMD sources remain disabled or held unless their source decision permits public retention and redistribution.
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
- Public deployment, submission, accounts, KYC, payout, and credential handling are outside the local product build.

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
