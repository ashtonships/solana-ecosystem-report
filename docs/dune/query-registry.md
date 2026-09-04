# Dune query registry and execution contract

| Query | Owner / recorded evidence | Metrics | Window |
| --- | --- | --- | --- |
| [8590950](https://dune.com/queries/8590950) | Ashton; query/execution identity recorded in the published snapshots | Fee-payer counts; transaction fees; DEX trade-leg USD volume total/by project; xStocks volume and price coverage | SQL candidate: two completed UTC days for fees/payers; eight completed UTC days for DEX trades |

The reviewed execution ended on September 3, 2026 at 01:19:20 UTC. Its query ID
is already configured; the old “pending query” description was outdated.
The revised [`solana-activity.sql`](./solana-activity.sql) is a local candidate.
The remotely saved query has **not** been changed or executed during this work;
apply and verify that SQL separately under the existing account authorization.

The six result columns remain `metric_id`, `day`, `dimension`, `value`, `unit`,
and `sample_count`. Every row is validated: family, unit, finite nonnegative
value, integer sample count, date, dimension, and unique metric/day/dimension.
Partial/paginated results and mismatched execution/query identities are withheld.
A missing metric family stays null instead of crashing or fabricating a total.
Only dates before the execution's UTC date enter aggregates; re-reading a cached
partial day tomorrow does not turn it into a completed day. DEX project rows
must describe the same day as the displayed DEX total. Trade legs are intentional;
this is not route volume, unique-user volume, or tokenized-equity volume.

The xStocks extension pins exactly 107 mints labeled `xStock` in
`solana-foundation/tokens` revision
`661a6f0ca466ccf74ea967dae7e3abbcdc088bc0`. Bought-or-sold membership is one
OR condition, so a leg with xStocks on both sides is counted once. It emits all
scoped trade-leg count and valid-price count. USD volume is omitted unless every
scoped row on that day has a finite nonnegative `amount_usd`; the adapter keeps
the value null and gives the coverage reason. Missing days are never zero-filled.
The metric is covered xStocks DEX trade-leg volume. It excludes other tokenized
equities, unique swaps/users, and centralized or off-chain trading.

The transaction-fee family uses
[`gas_solana.fees`](https://docs.dune.com/data-catalog/curated/gas-fees/solana_fees),
which documents both vote and
normal transactions and `tx_fee` in SOL. A day is emitted only if every row has
a finite nonnegative fee. This is an all-transaction fee total, not protocol REV;
the report's separate bounded block-sample REV estimate includes detected Jito
tips and must retain its own scope and sampling label.

New configured sections and their retained provenance declare
`aggregation_contract: "completed-utc-days-v1"`. The publication gate requires
strictly completed days under that contract and rejects unknown markers.
Immutable historical sections without the marker retain their older semantics:
dated values may include the execution's partial UTC day, and the first three
available snapshots contain no aggregates. They remain replayable with explicit
legacy/partial presentation; no value or completed-day claim is synthesized.
Finite values, units, identity, chronology and nonnegative counts stay required.

## Documented transport

- Cached result: `GET /api/v1/query/{query_id}/results`.
- One paid execution: `POST /api/v1/query/{query_id}/execute`.
- Status: `GET /api/v1/execution/{execution_id}/status`.
- Completed result: `GET /api/v1/execution/{execution_id}/results`.

[Execute query](https://docs.dune.com/api-reference/executions/endpoint/execute-query),
[execution status](https://docs.dune.com/api-reference/executions/endpoint/get-execution-status),
and [execution results](https://docs.dune.com/api-reference/executions/endpoint/get-execution-result)
were checked against official documentation on September 4, 2026.
A latest-result response is never used to poll a newly submitted execution.
The returned execution ID must match every status/result response.

All requests share one 120-second monotonic budget, including network timeout,
backoff and polling. Individual request timeouts use the remaining budget.
Responses use the existing 16 MiB bounded reader. Permanent HTTP errors fail
immediately. Transient reads have at most three attempts. Paid POST has exactly
one attempt: ambiguous failure keeps its reservation spent. Status failure,
partial completion, malformed results or timeout never replace validated
last-known-good metadata with a failed new response.

## Paid execution authorization and durability

`DUNE_API_KEY` and `DUNE_QUERY_ID` do not authorize a cached-result read: Dune
documents that result retrieval consumes credits based on result size. Every
result GET additionally requires `DUNE_PAID_READS_ENABLED=true`, an existing
owner-set `DUNE_RESULT_READ_LEDGER`, and a precommitted receipt.
`DUNE_REFRESH_HOURS` defaults to 24 and
must be finite and positive. Execution additionally requires
`DUNE_EXECUTION_ENABLED=true` and an existing valid `DUNE_EXECUTION_LEDGER`.
Unknown, missing or corrupt accounting prevents all Dune result reads.
The counter limits **attempts per query per UTC day**, not credits or currency.
Account allowance and query cost must be separately authorized; neither an
API key nor a recorded snapshot proves that allowance. The adapter caps each
result at 500 rows and requests only the six contracted columns. A result-read
ledger bounds one or two reads per run, total/daily reads, and rows per read.
This caps returned datapoints at `reads * max_rows_per_read * 6`, but does not
infer provider pricing or current account allowance. Paid result GETs have no
retry; an ambiguous entered read remains spent.

The execution ledger starts as `{"version": 1, "attempts": {}}` only when its accounting
start is established. `reserve_execution_attempt(path, query_id, run_token,
now=None)` writes one dated receipt before any POST using exclusive locking,
atomic replacement and fsync. It never clears earlier attempts. Missing files,
abandoned locks, prior same-day attempts and future-dated entries block spending.

For GitHub Actions, a runner-local file alone is insufficient. The workflow must:

1. Check `execution_refresh_due(previous_section, now, refresh_hours)`; unknown
   prior execution time returns false rather than consuming a speculative attempt.
2. Call `reserve_execution_attempt` on `.github/dune-execution-ledger.json`
   with run token `{GITHUB_RUN_ID}:{GITHUB_RUN_ATTEMPT}`.
3. Commit and push that reservation before collecting. Failure prevents collection.
4. Save the returned receipt JSON outside the checkout and pass
   `DUNE_EXECUTION_LEDGER` and `DUNE_EXECUTION_RECEIPT` with the explicit enable flag.

Receipt fields are `version`, `query_id`, `utc_date`, `run_token`, `reserved_at`.
The adapter requires exact agreement with the ledger, current day and current
GitHub run/attempt. It exclusively creates `<receipt>.consumed` and fsyncs before
the POST, so a second invocation cannot reuse the receipt. A rerun has a new
attempt token and cannot reuse the committed reservation. Failed collection,
failed publication and killed runners do not refund the durable attempt.
A local authorized run reserves directly against an existing persistent ledger.

The separate result-read ledger is deliberately absent until the owner confirms
remaining credits. Its exact fields are `version`, `starts_on`, `expires_on`,
`query_id`, `total_read_limit`, `daily_read_limit`, `max_rows_per_read`, and
`reservations`.
The code ceiling is 500 rows per read. Dates are UTC with exclusive expiration.
The workflow reserves two reads when it also reserves an execution, otherwise
one. It commits both ledgers before exposing either receipt. Missing credentials,
files, allowance or receipt keep `DUNE_PAID_READS_ENABLED=false` and cause zero
Dune HTTP requests. Previously published last-known-good evidence is read from
the committed snapshot and retains its original execution date and contract.

## Registered-query convergence gate

The local SQL is functional candidate code; it is not proof that query 8590950
contains these seven families. Convergence requires an API key created by that
query's owner, `Read/Write` scope, and an Analyst plan or higher for
[`PATCH /api/v1/query/{queryId}`](https://docs.dune.com/api-reference/queries/endpoint/update).
Before the owner-authorized mutation, read and retain the current query response
from [`GET /api/v1/query/{queryId}`](https://docs.dune.com/api-reference/queries/endpoint/read),
then PATCH only `query_id` and `query_sql`. Read it back and compare the returned
SQL byte-for-byte with `docs/dune/solana-activity.sql`; do not report convergence
from the PATCH status alone.

Updating SQL does not authorize execution. A first result still needs separately
confirmed account credit allowance and the existing durable execution receipt
flow. Enable `DUNE_EXECUTION_ENABLED` only after that allowance is known; let the
workflow reserve/commit/push the attempt before its one POST. Then verify the
execution-specific result contains all seven registered families, completed UTC
dates, 107-mint coverage provenance, and internally consistent xStock counts.
Keep the old query response and SQL for rollback. Current account allowance,
write scope, and billed credits are `UNKNOWN` from repository evidence.

## Offline verification

`python3 -B -m unittest tests.test_dune tests.test_dune_lkg tests.test_pipeline -q`

The tests mock every Dune request, clock and ledger. They exercise documented
URLs, execution races, daily reservation/replay, HTTP classification, deadlines,
whole-row validation, incomplete days, source isolation and retained public
semantics. No live or paid Dune request is needed to run them.
