# Solana Ecosystem Report

An auto-updating report on the state of the Solana network. Collects on-chain
data directly from public Solana JSON-RPC **and economic indicators from public
keyless endpoints (CoinGecko, DeFiLlama)**, detects anomalies against
accumulated history, and renders a dark interactive HTML dashboard, a
human-readable Markdown report, and machine-readable JSON.

Network health, TPS and slot time, epoch progress, validator set and stake
concentration, SOL price and market cap, TVL, stablecoin supply, DEX volume,
**transaction fee distribution, Real Economic Value and address activity** —
from one command, with no credentials of any kind.

**Python standard library only.** No API keys, no accounts, no third-party
packages, no build step. `git clone` and run.

## Quick start

```bash
python3 collect.py          # fetch live data → snapshots/
python3 detect.py           # anomalies across accumulated history
python3 render.py           # snapshot → dist/index.html, report.md, report.json
open dist/index.html        # macOS — xdg-open on Linux, start on Windows
```

No install step. Requires Python 3.9+ (uses only `urllib`, `json`, `pathlib`,
`datetime`, `argparse`, `html`).

```bash
python3 collect.py --dry-run              # print without writing
python3 collect.py --endpoint <RPC_URL>   # use a different public RPC
python3 render.py --snapshot <PATH>       # render a specific snapshot
```

## Data sources

Everything comes from **public Solana JSON-RPC**, in a single batched request
plus one follow-up. No endpoint here requires authentication.

| Method | What it provides |
| --- | --- |
| `getHealth` | Node health — the only source of the healthy/unhealthy state |
| `getSlot` | Current slot |
| `getEpochInfo` | Epoch number, progress, block height, cumulative transaction count |
| `getRecentPerformanceSamples` | TPS and slot time, derived over recent samples |
| `getSupply` | Total, circulating, and non-circulating SOL |
| `getVoteAccounts` | Active vs delinquent validators, stake distribution, commissions |
| `getBlockTime` | Wall-clock time of the current slot (depends on `getSlot`) |
| `getBlock` | Per-transaction fees, signatures, account keys and balance deltas |
| `getBlocks` | Block production rate, used to scale sampled figures to a day |

Default endpoint is `https://api.mainnet-beta.solana.com`. Any public RPC works
via `--endpoint`.

### Fees, REV and address activity — sampled from block bodies

The brief names Real Economic Value, median transaction fees and daily active
addresses. None of them are available from the summary RPC methods above; they
have to come out of block bodies, which is what `blocks.py` does.

`getBlock` at `transactionDetails: "accounts"` returns each transaction's fee,
signature count, account keys and pre/post balances while dropping instruction
data — everything needed here at roughly a tenth of the payload. The public
endpoint serves blocks about 25 hours back, so the sampling window is a real
24 hours rather than a proxy for one. Sixteen blocks are read, evenly spaced
across it, in about 16 seconds.

| Metric | How it is derived |
| --- | --- |
| Median / mean / p90 / p99 fee | Distribution over **non-vote** transactions only |
| Base fees | 5,000 lamports x signature count — fixed by the protocol |
| Priority fees | Transaction fee minus the base component |
| Jito tips | Positive balance deltas on Jito's eight published tip accounts |
| REV | Base + priority + tips, the standard definition |
| Fee burn | Total fees minus the block's own `Fee` reward entry |
| Address activity | Unique non-vote fee payers and accounts touched |

Three choices here are worth stating plainly, because each is a way this
metric is commonly got wrong:

**Vote transactions are separated out.** They are the bulk of Solana's
transaction count and every one pays exactly 5,000 lamports. Include them and
the median fee is always 5,000 — a number that is technically correct and tells
you nothing about what it costs a person to use the network. They still count
toward REV, because they are real lamports paid.

**Fee burn is measured, not assumed.** Each block carries a `Fee` reward entry
recording what the leader actually received; burn is total fees minus that. No
fee-split rule is hardcoded, so the figure survives a protocol change like
SIMD-0096. It also serves as a cross-check: the independently computed
base/priority split reconciles against it to the lamport.

**Daily active addresses are reported as unavailable, not estimated.** Sums
extrapolate from a systematic sample; unique-address counts do not, because
samples overlap. The sampled count is published and labelled as a sample, and
the daily figure is left null. Multiplying it up would have been the easy way
to fill the cell and would have overstated it.

The daily REV figure *is* extrapolated — legitimately, since it is a sum — and
is published with a 95% interval on the sample mean. Repeat runs move it by
about a third, which is exactly why the interval is there rather than a bare
point estimate.

Run `python3 collect.py --no-activity` to skip block sampling, or `--samples N`
to change the depth. Sampling stops after a 90-second budget and marks the run
truncated rather than stalling a collection when the public endpoint throttles.

### Economic indicators — also keyless

The brief asks for economic data from DeFiLlama and CoinGecko, and separately
states a preference for solutions needing no API keys. Those pull in opposite
directions only if you assume those services require keys. **They do not** —
each endpoint below was verified by direct unauthenticated request:

| Source | Endpoint | Provides |
| --- | --- | --- |
| CoinGecko | `api.coingecko.com/api/v3/simple/price` | SOL price, market cap, 24h volume, 24h change |
| DeFiLlama | `api.llama.fi/v2/historicalChainTvl/Solana` | TVL + 7-day trend from 1,968 historical points |
| DeFiLlama | `stablecoins.llama.fi/stablecoinchains` | stablecoin supply on Solana |
| DeFiLlama | `api.llama.fi/overview/dexs/solana` | DEX volume 24h/7d + 1-day change |

No key, no account, no header beyond `Accept`. Run `python3 collect.py
--no-economics` to skip them entirely and collect on-chain data only.

**Each source degrades independently.** They are third-party services that can
rate-limit or fail; one outage marks that source unavailable and leaves the
others untouched. A failed source renders `—`, never `$0` — a dashboard
silently printing "$0 TVL" during an outage is worse than one admitting it
doesn't know.

Dune and Twitter are deliberately **not** included: both require credentials,
which would break the zero-dependency property the brief prefers.

### Derived metrics

Not everything is read straight off the wire — some of the more useful numbers
are computed:

- **TPS** — `numTransactions / samplePeriodSecs`, reported as latest, mean, and peak.
- **Slot time** — `samplePeriodSecs / numSlots`; the network targets 0.4s.
- **Nakamoto coefficient** — the smallest number of validators that together
  control more than one third of active stake. The standard concentration
  measure for a proof-of-stake chain, and more informative than a raw validator
  count: 692 validators means little if 18 of them can halt the chain.
- **Delinquency rate** — delinquent validators as a share of all validators.

## Architecture

```
economics.py ─┐  keyless third-party sources
blocks.py    ─┤  block sampling: fees, REV, address activity
              ▼
collect.py  ──► snapshots/snapshot-<UTC>.json   (append-only history)
                snapshots/latest.json           (copy, not a symlink)
                        │
                        ├──► detect.py          anomalies across history
                        │            │
render.py   ────────────┴────────────┴──► dist/index.html   dark dashboard
                                          dist/report.md    human-readable
                                          dist/report.json  machine-readable
```

Two design decisions carry most of the weight:

**Snapshots are the source of truth, and they are append-only.** Rendering
never touches the network. Any snapshot can be re-rendered at any time, and the
history accumulates on its own — which is what makes anomaly detection a pure
function over existing files rather than new infrastructure.

**Network access is confined to the fetch helpers.** In each module the
functions that touch the network are separated from the ones that shape data,
and everything in the second group is pure. That is why the entire test suite
runs offline, and why an RPC outage cannot produce a subtly wrong report — only
a visibly degraded one.

**Optional sections fail alone.** `economics.py` and `blocks.py` return
`available: false` on any failure and never raise into the collector, so a
CoinGecko outage or a throttled `getBlock` costs that one section and leaves
the rest of the snapshot intact. `blocks.py` is priced in SOL because that is
what the chain reports; USD is layered on at render time from the price
section, so losing CoinGecko costs the dollar columns and not the on-chain
figures.

## Automation strategy

`collect.py` is a single idempotent command with no state beyond the snapshot
directory, so scheduling it is a one-liner on any system:

```bash
# every 15 minutes
*/15 * * * * cd /path/to/solana-ecosystem-report && /usr/bin/python3 collect.py

# render after each collection
*/15 * * * * cd /path/to/solana-ecosystem-report && /usr/bin/python3 collect.py && /usr/bin/python3 render.py
```

`launchd`, `systemd` timers, or a CI schedule work equally well — there is
nothing to configure, no daemon, and no credential to rotate. Refresh interval
is entirely the operator's choice; the collector holds no interval assumption.

**Degradation is deliberate.** If a single RPC method fails, that section
reports `"available": false` and the report renders the section as
*unavailable* rather than as zero. A missing metric and a metric that is
genuinely zero must never look the same on a dashboard, and a partial outage
should not lose the metrics that did succeed.

## Anomaly detection

`detect.py` compares the newest snapshot against a median baseline drawn from
the accumulated history. Seven detectors run:

| Detector | Fires when | Severity |
| --- | --- | --- |
| `network_unhealthy` | `getHealth` does not return ok | critical |
| `tps_drop` | TPS ≥40% below baseline median | critical |
| `tps_spike` | TPS ≥60% above baseline median | info |
| `slot_stalled` | current slot is not ahead of the previous snapshot | critical |
| `delinquency_high` | ≥5% of validators delinquent | critical |
| `delinquency_jump` | delinquency up ≥2 percentage points vs baseline | warning |
| `slow_slots` | mean slot time >0.60s (target 0.400s) | warning |
| `supply_move` / `stake_move` | circulating supply ±1% / active stake ±5% | warning |

Thresholds live in one `THRESHOLDS` dict at the top of `detect.py` so an
operator can tune them without reading detector code.

Two decisions worth calling out:

**The baseline is a median, not a mean.** One anomalous snapshot in the history
would drag a mean far enough to mask the next real event. There is a test for
exactly this: a 100,000 TPS outlier in the baseline must not hide a subsequent
75% drop.

**Too little history reports `insufficient_history`, never "no anomalies".**
This is the distinction the whole tool turns on. With no baseline, an empty
findings list means *we cannot tell yet* — not *the network is fine*. The
status field says so explicitly, the report renders it in grey rather than
green, and the message reads "Absence of findings here means no baseline, not
a healthy network."

```bash
python3 detect.py                  # human-readable
python3 detect.py --json           # machine-readable
python3 detect.py --min-history 5  # demand a deeper baseline
```

`samples/report-with-anomalies.md` shows all seven firing at once, generated by
replaying a deliberately degraded snapshot against a real four-snapshot
baseline.

## Output formats

- **`dist/index.html`** — dark-theme dashboard, one self-contained file with
  inline CSS. No CDN, no external asset, no build step; opens from `file://`.
  Headline figures use compact notation (`434.4M SOL`) with the exact value
  beneath.
- **`dist/report.md`** — the human-readable record. Keeps **full precision**;
  no rounding, no abbreviation.
- **`dist/report.json`** — the snapshot verbatim, schema-versioned.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

141 tests, no network required. They cover the derived metrics (TPS, slot time,
Nakamoto coefficient, delinquency, supply shares), the JSON-RPC batch mapping
including out-of-order and error responses, the block-sampling transforms,
HTML escaping, and degraded-input handling for every section.

Four properties are pinned deliberately, because each is a way a dashboard lies:

- **A totally empty RPC response still produces a valid snapshot** — degraded,
  clearly marked unavailable, never a crash.
- **Missing renders as `—`, not `0`** — "we don't know" and "it is zero" are
  different claims.
- **Vote transactions never reach the fee statistics** — including them pins
  the median at 5,000 lamports forever and the metric quietly stops meaning
  anything.
- **Daily active addresses stay null** — the temptation to scale the sampled
  count up to a day is the specific error being tested against.

## Sample output

Committed under `samples/`, generated by the commands above — not hand-written:

| File | What it is |
| --- | --- |
| `samples/report.md` | human-readable Markdown report |
| `samples/report.json` | machine-readable snapshot + anomaly analysis |
| `samples/anomalies.json` | `detect.py --json` output |
| `samples/report-with-anomalies.md` | all seven detectors firing |

## Originality

All code here is original and written for this report. The only reuse is
between the project's own modules: `render.py` and `detect.py` both read the
snapshot format defined by `collect.py`. No third-party code is vendored — the
dependency list is the Python standard library, and nothing else.

The **Nakamoto coefficient** is the one metric not named in the brief. It is
included because a raw validator count is close to meaningless on its own: 692
active validators sounds decentralised until you find that **18 of them control
one third of stake**. That figure is the actual decentralisation story, it is
computed from `getVoteAccounts` data already being fetched, and no other
ecosystem dashboard surfaced in this space reports it alongside live network
health.

## Roadmap

Two items that started here are now built and documented above: **anomaly
detection** (seven detectors over accumulated history) and the **keyless
economic sources** (CoinGecko and DeFiLlama for price, market cap, TVL,
stablecoin supply and DEX volume). Still open:

- **Historical charts** — trends across snapshots, rendered inline with the
  same zero-dependency approach.
- **Dune ecosystem dashboards** — requires an API key, so gated behind the
  no-credential preference rather than assumed.

Deliberately not built, and why:

- **Tokenized asset volumes** — named in the brief, but every source found for
  it requires an API key, which would break the zero-credential property.
- **Ecosystem and community news** — would require scraping, with no stable
  keyless endpoint behind it.
- **A true daily active address count** — reachable only by scanning all
  ~215,700 blocks in a day. See the sampling note above; the honest partial is
  shipped and labelled instead of an estimate dressed up as the real figure.

## License

Not yet licensed. MIT is proposed; the final choice is the owner's, to be made
at the public-release gate.
