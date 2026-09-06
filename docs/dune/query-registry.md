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

The separate result-read ledger requires confirmed remaining credits. The
2026-09-06 allowance permits two reads total and expires the next UTC midnight.
Its exact fields are `version`, `starts_on`, `expires_on`,
`query_id`, `total_read_limit`, `daily_read_limit`, `max_rows_per_read`, and
`reservations`.
The code ceiling is 500 rows per read. Dates are UTC with exclusive expiration.
The workflow reserves two reads when it also reserves an execution, otherwise
one. It commits both ledgers before exposing either receipt. Missing credentials,
files, allowance or receipt keep `DUNE_PAID_READS_ENABLED=false` and cause zero
Dune HTTP requests. Previously published last-known-good evidence is read from
the committed snapshot and retains its original execution date and contract.

## Registered-query convergence gate

On 2026-09-06, the owner signed in to Dune as `@clearout`. Query 8590950 was
saved through the editor and reopened in a fresh page. Full editor clipboard
readback matched `docs/dune/solana-activity.sql` exactly (SHA256
`9ca466907db200ebdc697b08316a8fb07a8b9648977e975057937ff2bf0520da`).
The prior SQL was retained privately for rollback. This establishes saved-query
convergence, not successful execution or measured coverage.

Fresh account settings showed Plus trial, 2,496.643 included credits remaining,
extra spending locked at $0 with no payment method, and the saved per-execution
ceiling at 25 credits. The owner authorized one execution and at most two result
reads of 500 rows each on 2026-09-06; no subscription change or extra spending.
Result-read credits are separate from the execution ceiling.

The completed trial used the explicit `dune_refresh_once` manual workflow input, keeping both
repository enable flags false. This input requests a durable allowance and can
bypass the ordinary collection cadence only with matching current-run receipts.
Expired, spent or invalid accounting prevents paid requests. Do not refund a
reservation or execute again after a failed trial.

Execution results must still be checked for the registered metric families,
completed UTC dates, 107-mint provenance and consistent xStock counts. The USD
family is correctly absent when pricing is incomplete. An account cost cap is
a provider stop condition; the 120-second client deadline does not prove that
server-side computation stopped. Inspect the retained execution ID and account
usage after success or failure before reporting the trial's final outcome.

## Verified trial outcome — 2026-09-06

[Run 34010172983](https://github.com/ashtonships/solana-ecosystem-report/actions/runs/34010172983)
completed one execution, `01M1TDTS0G45E5EY0SEDDGV33G`, at
2026-09-06T03:57:58.943975Z. The production audit reported
**4.627115385 execution credits**, below the saved 25-credit cap. The account
usage page showed $0 additional spending. The finite two-read allowance is
exhausted; this success does not authorize another run or continuing Dune refreshes.

The [published snapshot](https://github.com/ashtonships/solana-ecosystem-report/blob/b58fbb08ff7f8fd0fdcbe70f790b36f5ddcf57da/snapshots/latest.json)
records 194 returned rows and the following September 5 completed-day aggregates:

| Metric | Recorded result |
| --- | ---: |
| Non-vote fee payers | 2,030,935 |
| DEX trade-leg volume | $1,077,657,706.86 |
| All-transaction fees | 2,759.23552078 SOL |
| Scoped xStock trade legs | 15,296 |
| Priced scoped xStock trade legs | 15,000 |
| Complete scoped xStock USD volume | Unavailable |

The 296 unpriced trade legs prevent a complete xStock USD total. The USD family
is withheld rather than assigning zero or extrapolating prices. Fee payers are
not a network-wide unique-user count, and transaction fees are not total REV.
The source SQL has seven families; this result does not establish seven available
latest values. Published aggregates and execution provenance are retained; raw
result rows are not part of the public snapshot.

In the HTML report, open **Data → Full recorded data appendix** for the DEX,
xStock price-coverage, and transaction-fee cards. Full precision and provenance
remain in JSON. Carried evidence retains its original observation date; ordinary
core updates do not authorize additional paid Dune reads.

## Offline verification

`python3 -B -m unittest tests.test_dune tests.test_dune_lkg tests.test_pipeline -q`

The tests mock every Dune request, clock and ledger. They exercise documented
URLs, execution races, daily reservation/replay, HTTP classification, deadlines,
whole-row validation, incomplete days, source isolation and retained public
semantics. No live or paid Dune request is needed to run them.
