# Post-Deadline Changelog

Everything below lands AFTER the submitted revision (`submission-2026-09-01` = `0e42bb8`).
The submission itself is unchanged; this file itemizes later work for judges and the sponsor.

## 2026-09-01 (post-deadline, same day)

### Reliability
- **PR #1** (`e0df70d`): render keyed provider observations one-per-subject-per-snapshot,
  which broke when the Solana Data feed began retaining multiple observation dates per
  provider. Fixed by keying on source-native event time; added regression tests.
- **PR #2** (`051aa52f`): report observation verifier now accepts bare source dates
  (UTC-midnight semantics), matching the facts contract from `1b7f8ba`; ordering checks
  unchanged.
- **PR #3** (`a5556280`): degraded-activity `reason` field added to the public schema with
  regression test — availability explanations are evidence and now survive projection.
- Two consecutive green unattended update+deploy cycles verified; hosted release-ID parity
  across HTML/Markdown/JSON confirmed.

### Data breadth
- **PR #4** (`d0426c04`): enabled the rights-cleared CoinGecko Demo SOL price transport
  (key via header only, per-source publication holds intact, release gate updated to accept
  the keyed transport). SOL price card, charts, anomaly detection, and USD fee layering live.
- Provider-range observations over a full 365-day Solana Data window now publish
  (2,191 observations with per-provider provenance and ranges).

## 2026-09-02 (planned/in-flight)
- Provider observation revision model (revised provider rows append a new revision with
  `supersedes` semantics instead of conflicting; charts select latest valid revision;
  raw evidence retained) — fixes the 2026-09-02 04:40 UTC scheduled-run failure.
- Stability hardening: deduplicated failure-issue automation, post-deploy hosted smoke
  check, facts idempotency tests, bounded-retry consolidation.
- Official solana.com news source and first-class Upgrades overview.
- Ecosystem Pulse overview block and additional Solana Data provider metrics.
- Dune adapter (bounded, credit-capped, one execution/day max) — pending query creation.
- Release verification: a push whose trusted event base is a newer bot "[skip ci]"
  snapshot commit (PR merged between bot commits) is now judged by public-content
  equality instead of a false-positive history-walk check; all previous rejection
  semantics are preserved and regression-tested.

## 2026-09-04

### Reliability and evidence contracts

- **PR #46** (`075069b`, merged as `67b856c6`): added durable, fail-closed
  pre-transport reservations for optional X and Dune reads. Credentials alone do
  not authorize a paid request; the owner must also commit a finite allowance.
- Repaired Dune execution-specific polling, completed-UTC-day aggregation,
  whole-row validation, bounded timeouts, and last-known-good retention.
- Corrected current X API field selection and engagement mapping, tightened the
  allowlist/link/time contract, and kept failed reads as a dated archive rather
  than relabelling them current.
- Tightened RPC retry/freshness semantics and source isolation. Optional source
  failure no longer blocks valid core evidence.

### Coverage and presentation

- Added finalized on-chain state for ten selected Agave feature accounts, with
  explicit activated versus account-absent states and exact 10/10 coverage.
- Added completed-day Dune contracts for scoped xStock DEX trade-leg volume,
  price coverage, and all-transaction fees. The local SQL pins 107 exact xStock
  mints. The registered query has not been changed or executed by this release.
- Added a 17-item requirement disclosure, a 19-dataset catalog, bounded desktop
  History A/B selection, corrected activity/stablecoin scope, and the approved
  release artwork.
- Reduced the generated HTML parse surface by rendering each desktop/mobile
  representation only in its own workbench and limiting each production table
  to the 100 highest leader-slot identities; all 675 exact rows remain in JSON.

### Verification and production state

- The complete offline suite passed **1,015 tests**. Independent Dune-to-UI and
  budget/LKG reviews found no remaining P0/P1 issue in their owned surfaces.
- [Production run 33909339958](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33909339958)
  passed collection, semantic validation, anomaly detection, exact changed-path
  verification, data commit, rendering, package verification, Pages deployment,
  and the exact live release-ID smoke check.
- Production data commit `5bd13c8` published release
  `ee61e7dadcc9c2a0b0a698982ad4a6b3a898ed77f848dc470a8e15efff3e3e7e`.
  Hosted HTML, Markdown, and JSON were independently byte-matched to the verified
  Pages artifact.
- Dune and X paid reads remain disabled because no finite remaining allowance is
  committed. The live report shows those states and preserves dated evidence;
  this release did not make a paid request.
