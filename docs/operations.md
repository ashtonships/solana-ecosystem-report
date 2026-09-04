# Operating the report

The production workflow requests runs at minutes 7, 22, 37 and 52 of each UTC hour. GitHub can queue or delay it. Record the actual `collected_at`, release ID and each source's event time when evaluating freshness. A successful deployment can contain explicitly unavailable optional sources.

## Source refresh tiers

Core network RPC and approved price collection run on every update. Block activity,
completed-epoch production and selected feature accounts refresh hourly. Provider
activity and news refresh every six hours. Selected token supply and Dune refresh
daily. Endpoint changes force new on-chain collection rather than reusing another
endpoint's observations.

Each published `collection_schedule` entry retains its last attempt, last success,
interval and refreshed/reused/failed state. A reused source keeps its original
observation time; another publication does not create a new independent baseline
point. A failed refresh does not advance the last-success clock. The Methods page,
Markdown and JSON expose these source times separately from publication time.
Token cursor state is not rewritten when the token lane is skipped.

The paid reservation step checks the same source schedule **before** reserving any
Dune or X allowance. Missing or malformed paid-source schedule entries fail closed.
A due source still requires every existing account and ledger authorization.

## Paid sources

No key is a spending authorization. Query execution and X search have separate, fail-closed controls; ordinary keyless news and core RPC collection remain independent.

Dune result retrieval consumes credits, so `--with-dune`, a key and query ID are
not sufficient. Reads additionally require `DUNE_PAID_READS_ENABLED=true` and an
owner-created `.github/dune-result-read-budget.json`; that file is absent while
allowance is unknown. A durable receipt bounds total/daily reads and at most 500
rows across the six contracted columns. Paid reads have no automatic retry. To
allow a refresh, the workflow also needs `DUNE_EXECUTION_ENABLED=true` and the
tracked `.github/dune-execution-ledger.json`. That ledger records at most one
attempted execution per query and UTC day. A transport failure or killed run
consumes the read/attempt reservation. Confirm credits and hard account caps
before enabling; the code does not purchase credits or raise a cap. See the
[query contract](dune/query-registry.md).

X needs `X_BEARER_TOKEN`, `X_PAID_READS_ENABLED=true` and an approved `.github/x-read-budget.json`. Missing, corrupt, expired or exhausted accounting skips paid search. No file is initialized from snapshot counts: a response may have cost money even if it was filtered, never published or lost with a runner.

The owner must reconcile current console usage and prices, then approve a remaining **post allowance** within the existing monetary ceiling. The following is an inactive example, not an allowance to spend:

```json
{
  "version": 1,
  "starts_on": "2026-09-04",
  "expires_on": "2026-09-18",
  "total_post_limit": 0,
  "daily_post_limit": 20,
  "reservations": {}
}
```

Dates are UTC and expiration is exclusive. The total allowance never resets at midnight; the daily ceiling cannot exceed 100. One request reserves at most 20 posts, reduced to remaining allowance. Below the API's 10-post minimum, no request is made. Reservations conservatively count the maximum response size, including potentially filtered posts. There is no automatic refund, deduplication discount or retry of an ambiguous paid request. Account-level limits remain necessary for spending outside this workflow and pricing changes. Current pricing is shown in the [X Developer Console](https://console.x.com/); see [usage and billing](https://docs.x.com/x-api/fundamentals/post-cap) and [recent-search limits](https://docs.x.com/x-api/posts/search-recent-posts).

The workflow order is:

1. Tests and a clean-checkout check.
2. Atomically reserve eligible requests and write temporary receipts.
3. Commit and push only the changed budget ledgers. Failure stops here.
4. Expose receipts to collection. Each receipt matches the GitHub run and attempt and is consumed before HTTP.
5. Validate and commit append-only report evidence, render, verify, push, package and deploy.

The reservation commit may exist without a later snapshot commit. That is intentional: an interrupted attempt must survive a new runner. Do not delete a ledger, remove reservations or reset a stale lock to resume spending without reconciling the account and any ambiguous request. Receipts contain run identities, not API keys; they live under `RUNNER_TEMP`, outside report artifacts. Local paid requests also require explicit enablement and durable accounting.

## Failure and recovery

A failed optional adapter records its reason. Dune may expose a dated last-known-good result. A failed X read stays unavailable; previously recorded posts may appear only in the archived chronology with their original observation and publication times. A current timestamp must never be assigned to carried evidence.

RPC transport retries only eligible transient failures within its existing time budget. Logs contain the method and status class, never a custom endpoint, request body or provider error message. Check method failures, actual sample block times and coverage before treating a green run as recovered data.

If publication fails, use the workflow's failing step and run URL. Confirm the previous hosted release is still intact. Repair and test the source or gate, then run `update`; do not weaken validation, rewrite old snapshots or substitute zeros. A rollback publishes the previously verified package and preserves its original release ID/timestamp; it must not be presented as a fresh collection.

An independent monitor is needed to detect a scheduler that stops running entirely; a check inside the same workflow cannot prove scheduler liveness. The release evidence should include three successful **scheduled** runs after the change and their actual intervals, plus source freshness and coverage. Manual runs are useful diagnosis but do not satisfy that scheduled-run evidence.
