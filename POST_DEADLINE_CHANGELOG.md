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

## 2026-09-02 (historical plan)

This list records the plan at that date, not the current backlog. Stability
hardening and Ecosystem Pulse subsequently merged in PRs #7, #11, and #12;
rights-safe news and upgrade metadata followed in PRs #17–19. The Dune adapter
and its later corrections are implemented, but current paid reads remain
held. Solana News content remains held; the empty WIP PR #13 is not a release
dependency. See the later release entries for deployed evidence.
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

### Later September 4 releases (PRs #48–53)

- **PR #48** (`11f3d59`): total/non-vote TPS overlays, source-native provider
  comparison and inspection, desktop/mobile growth and History parity, source
  search, and bounded human-readable Markdown with complete JSON evidence.
- **PR #49** (`9253cfb`): restored the shared value/unit spacing in charts.
- **PR #50** (`fb19ba7`): tiered collection with fast RPC/price refreshes,
  hourly heavy sources, six-hour news/provider refreshes, and daily token/Dune
  lanes. Reused evidence keeps its original source clocks; paid-source budgets
  and publication holds still apply.
- **PR #51** (`bb98dbf`): observed cluster-node software versions with an
  explicit denominator, bounded recovery of the last complete production
  observation, and failed-refresh disclosure.
- **PR #52** (`e89caf6`): aligned publication metadata with the configured
  900-second cadence and next scheduled trigger. This configuration is not a
  guarantee of fifteen-minute delivery by GitHub Actions.
- **PR #53** (`9e71658`): linked protocol proposals and date-grouped recorded
  activity, source/date/type filters, Timeline/Grid views, and native evidence
  disclosures on desktop and mobile. The current catalog contains 22 datasets.

[Production run 33931392861](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33931392861)
passed 1,080 tests (two Python 3.10 availability skips on that runner), collection,
data/package verification, Pages deployment, and the live release-ID smoke check.
Its clean renderer is `83a9f533466acb0a1149f970ea232d3ef8670987`, generated at
`2026-09-05T00:03:36+00:00`. All three hosted files were byte-matched to that
release; the reviewer verification also reproduced those bytes locally and
passed all 1,080 tests without skips.

The release ID is
`7e07d1f20f0c1b2ea2ee0888df73a433cf377e04356e9b56cc406e1d161877ee`.
This is manual deployment evidence. As checked on September 5 at 00:48 UTC,
three successful scheduled cycles after PR #53 remain unproved, and the local
cycle monitor is paused. Dune/X paid reads, a permitted date-aligned daily Jito
source, and native iOS interaction verification remain open. The original
submitted revision and submission receipt are unchanged by these releases.
