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
