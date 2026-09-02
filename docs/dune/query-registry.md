# Dune query registry

Every Dune query the report collects from is registered here before its
`DUNE_QUERY_ID` is wired into the environment. A query is not collectable until
it has a row in this registry with a real `query_id` created by Ashton in the
Dune UI.

## Registry schema

| Field | Meaning |
| --- | --- |
| `query_id` | Numeric Dune query id; becomes `DUNE_QUERY_ID` at collection time |
| `owner` | GitHub/Dune account that owns and edits the query (Ashton) |
| `canonical_url` | `https://dune.com/queries/<query_id>` |
| `metric_ids` | Exact `metric_id` values the query must emit |
| `expected_columns` | Exact column names and DuneSQL types of the result |
| `max_acceptable_age` | Oldest `execution_ended_at` treated as fresh (`DUNE_REFRESH_HOURS`, default 24h) |
| `scope` | What the metrics cover — chain, window, definition |
| `exclusions` | What is deliberately NOT counted (vote txs, non-trade legs, …) |
| `credit_policy` | Execution budget. This deployment: **max 1 execution/day, $0 extra spend** — the adapter reads the latest cached result and re-executes only when it is older than `max_acceptable_age` |

## Queries

| query_id | owner | canonical_url | metric_ids | expected_columns | max_acceptable_age | scope | exclusions | credit_policy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| *(pending Ashton-created query)* | ashton | — | `daily_non_vote_fee_payers`, `daily_dex_volume_total`, `daily_dex_volume_by_project` | `metric_id VARCHAR, day DATE, dimension VARCHAR, value DOUBLE, unit VARCHAR, sample_count BIGINT` | 24h | Solana mainnet: non-vote fee payers (last 2 days); DEX trade-leg volume total and by project (last 8 days) | vote transactions; non-trade legs; volume is TRADE-LEG volume, never unique-user volume | max 1 execution/day; $0 extra spend; latest-result-first, execute only when stale |

## SQL source

The canonical query text lives at [`solana-activity.sql`](./solana-activity.sql).
Ashton creates the query in the Dune UI from that file, then records the
resulting `query_id` in the table above and sets the `DUNE_QUERY_ID` Actions
variable. Until then the adapter reports `available: false` with reason
`dune query not configured` — cleanly unavailable, not an error.
