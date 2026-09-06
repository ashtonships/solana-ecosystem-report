# Post-Deadline Changelog

Everything below lands after the preserved pre-deadline revision
(`submission-2026-09-01` = `0e42bb8`). This file itemizes later work.
The tag is unchanged. The submitted repository URL is now verified; the exact
portal timestamp/revision and judging treatment of later changes remain unknown.
See `SUBMISSION_FREEZE.md`.

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
source, and native iOS interaction verification remain open. The preserved
pre-deadline revision is unchanged. No portal action was performed or verified
by these release checks.


## 2026-09-05 — app-wide interaction polish

- Kept chart sample keyboard input separate from carousel navigation; restored
  distinct desktop total/non-vote TPS series styling.
- Kept all matching mobile source groups open during filtering, including
  empty-search recovery.
- Added full-sample History keyboard inspection, transformed SVG pointer
  coordinates, and a keyboard-reachable snapshot picker footer. Preserved
  selected A/B snapshots across mobile/desktop transitions.
- Added 12-event progressive activity batches with accurate counts and readable
  focus order. Project filters, Timeline/Grid and expanded batches now share
  state across responsive layouts. Every activity record remains in static HTML.
- Removed the collapsed Methods disclosure's 296px blank area and separated
  the source-flow return label from its line.
- Added executable browser interaction checks and full-page light/dark captures
  across all five routes. See [UI verification](docs/ui-qa/2026-09-05/README.md).


## 2026-09-05 — mobile structure and metric inspection

- Replaced the Data introduction's boxed navigation cards with a compact contents list and matching coverage disclosures.
- Joined mobile History selectors and chart in one padded comparison component.
- Added explanations and recorded-reading inspection to all seven validator and five growth cards.
- Separated inline chart tap inspection from carousel swipe navigation; added an expanded chart dialog for drag inspection with focus and route/resize recovery.
- Wrapped the shared narrow-screen footer before its name and timestamp overlap.
- Added touch/keyboard checks and full-page captures across five routes and four widths. See [design verification](docs/ui-qa/2026-09-05-mobile-design/README.md).


## September 6 reconciliation of release evidence

PR #54 (`ec4f96304f6462f6aaa997bd73583cfed7dd4faa`) bound release smoke
checks to verified artifacts. PR #55
(`68d82c65b79301c5c26ef4dc71aafc87296a8f68`) made offline reviewer setup
immediate and added private source preflight. PR #56 completed validator
distribution, stake-history, and commission charts.

The earlier PR #53 three-cycle statement is historical. PR #56 merged as
`600b7d3684bf85c59d57442487e1548eccae2e74`; its scheduled runs
[33947613513](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33947613513),
[33957636385](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33957636385), and
[33967118741](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33967118741)
were verified in the September 5 review. Actual intervals were approximately
2h44m, 3h43m, and 3h31m. This establishes those unattended publications, not
reliable fifteen-minute delivery or three cycles for subsequent UI releases.

- **PR #57**, merged as `c024fb69f3d2b3d20eb0000072763e5cdd6e2ead`:
  the app-wide interaction work above. Production run
  [33999323715](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/33999323715)
  succeeded; clean renderer `93cd39199506cb7c06857a35c247b5ebfe1a069f`
  produced HTML, Markdown, and JSON independently matched to its hosted artifact.
- **PR #58**, merged as `5f067db450698b6aaed3e85b0a011fdb1debaa77`:
  the mobile structure and inspection work above. Production run
  [34000976440](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/34000976440)
  succeeded; clean renderer `f1a4bdfe213692b832dbae121ab95a0b35efed16`
  produced independently matched hosted HTML, Markdown, and JSON.
- The September 6 live audit found no open PRs and a further successful scheduled
  run [34003517430](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/34003517430).
  Its renderer is `50fc5d483a0f56c02b1491b0e6db269639f6dc27`. Local port 3000
  still served the preceding snapshot; it already contained PR #58 UI changes.

These code and deployment records do not establish a submission receipt,
post-deadline judging eligibility, or an award.

## 2026-09-06 — final integration and bounded Dune trial

- **PR #59**, merged as `9f6b758feb8fb111df8c1b19d57701d308dde0dc`:
  completed the alternate desktop History comparison ledger, fixed clipped chart
  inspection buttons and tablet wordmark/navigation overlap, and corrected
  release-history evidence. The original dirty redesign checkout was preserved.
- **PR #60**, merged as `ee32a1c26e378ffc3c907fb58dadb7caa078f082`:
  added a manual-only Dune refresh bound to persisted read/execution receipts,
  with UTC expiry and safe execution-ID/credit audit records. Scheduled jobs
  cannot consume the trial while repository-wide spending flags remain disabled.
- Query 8590950 was saved through the owner's signed-in editor and independently
  reopened; its full SQL matched `docs/dune/solana-activity.sql` exactly.
- [Production run 34010172983](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/34010172983)
  passed 1,116 tests, collected one successful Dune execution, verified the
  publication package, deployed it, and passed the hosted release smoke check.
  An independent review also passed 137 focused tests.
- The execution used 4.627115385 credits, with $0 extra spending observed in the
  account. Its allowance is spent. Continuing paid refreshes requires a new
  bounded allowance; this entry is not ongoing spending authorization.
- September 5 DEX trade-leg volume is now recorded. Complete scoped xStock USD
  volume remains unavailable because 296 of 15,296 trade legs lacked valid
  pricing. See the [query outcome](docs/dune/query-registry.md#verified-trial-outcome--2026-09-06).
- Clean renderer `b58fbb08ff7f8fd0fdcbe70f790b36f5ddcf57da` generated release
  `470d88b83d5a9a12c5033c0c0c534289273e65145fda2de700eeca3a7f83df90`
  at 2026-09-06T03:58:14Z. Hosted HTML, Markdown and JSON were byte-matched to
  the Pages artifact; port 3000 and the existing phone URL matched those files.
- The signed-in Superteam profile confirms this bounty's submission links to
  the canonical GitHub repository. Exact portal time, frozen commit, acceptance
  of later changes, and an award remain unverified.
