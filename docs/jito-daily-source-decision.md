# Prepared Jito daily source: approval pending

`jito_daily.py` and its offline tests are prepared. The adapter is not imported by
collection, rendering or the workflow, and no public snapshot is populated from
it. The existing project source-decision gate requires owner acceptance of the
applicable Jito terms before public adoption.

The [official daily MEV endpoint documentation](https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/stake-pool-api/#6-daily-mev-rewards)
defines `jito_tips` as SOL paid to Jito and `validator_tips` as SOL distributed to
validators. It also provides the UTC day, MEV tip transaction count and unique
submitting-account count. The adapter preserves those fields separately. It does
not infer gross tips, protocol REV, account-level network activity or a fee split.

The prepared request is one bounded keyless GET of
`https://kobe.mainnet.jito.network/api/v1/daily_mev_rewards`. The response is
validated before use, current UTC day excluded, and at most the latest 30
completed days retained. Duplicate/future dates and malformed or non-finite
values fail closed. Transport uses existing bounded response and retry helpers.

Before enabling public collection, reconcile acceptance for this specific API
with its linked [Foundation Terms](https://www.jito.network/docs/jitosol/resources/terms-of-use/)
and the existing README Jito gate. If accepted, integrate on a daily source
cadence with recorded source timestamps, explicit source-native names and failure
states. This approval would not establish a complete daily REV formula: the
separate date-aligned total transaction-fee component remains required. Do not
combine current-day, sampled, post-burn reward or mismatched-date components.

Validation:

```sh
python3 -B -m unittest tests.test_jito_daily tests.test_transport -q
```

All tests use synthetic data or mocked transport; no live retrieval is required.
