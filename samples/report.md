# Solana Ecosystem Report

**Collected:** 2026-08-05T18:10:38+00:00  
**Source:** `https://api.mainnet-beta.solana.com` (public JSON-RPC, no API key)  
**Network health:** 🟢 healthy

## Anomalies

🟢 **None detected** across 10 snapshots (baseline of 9).

## What changed since the last snapshot

> `2026-08-05T13:36:49+00:00` → `2026-08-05T18:10:38+00:00` (4.6h apart). 0 metric(s) moved past threshold, 14 steady, 0 not comparable.

🟢 **No metric moved past its threshold** across 14 compared metric(s).

## Network

| Metric | Value |
| --- | --- |
| Current slot | 437,427,507 |
| Block time | 2026-08-05T18:09:47+00:00 |
| Block height | 415,481,975 |
| Total transactions | 535,397,176,314 |

## Epoch

| Metric | Value |
| --- | --- |
| Epoch | 1012 |
| Progress | 56.37% |
| Slot in epoch | 243,507 of 432,000 |

## Performance

| Metric | Value |
| --- | --- |
| Latest TPS | 4,422.58 |
| Mean TPS (8 samples) | 3,920.56 |
| Peak TPS | 4,422.58 |
| Mean slot time | 0.42s |

## History

Across 10 committed snapshot(s). Ranges are over real observations only — snapshots missing a metric are counted as missing, never as zero.

| Series | Points | Missing | Gaps | Min | Max | Latest | Basis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Transactions per second (TPS) | 10 | 0 | 4 | 2,935 | 4,423 | 4,423 | measured |
| Mean slot time (s) | 10 | 0 | 4 | 0.420 | 0.433 | 0.422 | measured |
| Validator delinquency (%) | 10 | 0 | 4 | 1.00 | 1.00 | 1.00 | measured |
| SOL price (USD) | 6 | 4 | 2 | 73.37 | 74.42 | 74.42 | measured |
| Total value locked (USD) | 6 | 4 | 2 | 4,788,941,147 | 4,800,643,250 | 4,800,643,250 | measured |
| Median fee (non-vote) (lamports) | 4 | 6 | 0 | 5,469 | 5,608 | 5,514 | sampled |
| REV 24h (estimated) (SOL) | 4 | 6 | 0 | 6,677 | 9,463 | 9,463 | sampled |

A series with fewer than two points is charted nowhere and reported here as the count it actually has.

## Fees, REV and activity

> Sampled from **16 blocks** evenly spaced across 25.4h of chain history (~215,741 blocks produced in that window). Public JSON-RPC `getBlock`, no API key.

| Transaction fee | Value | USD |
| --- | --- | --- |
| Median | 5,514 lamports | $0.00041 |
| Mean | 42,773 lamports | $0.003183 |
| 90th percentile | 20,374 lamports | $0.001516 |
| 99th percentile | 992,000 lamports | $0.07 |

Measured over **12,204 non-vote transactions**. Vote transactions were 47.38% of all sampled traffic and are excluded — every vote pays exactly 5,000 lamports, so including them pins the median there and the figure stops describing what it costs anyone to use the network. 44.40% of non-vote transactions failed on-chain.

### Real economic value

REV is **base fees + priority fees + Jito tips**.

| Component | Sampled (SOL) | Share |
| --- | --- | --- |
| Base fees | 0.12 | 17.3% |
| Priority fees | 0.46 | 65.0% |
| Jito tips | 0.12 | 17.7% |
| **Total sampled** | **0.70** | 100% |

**Estimated 24h REV: 9,463.20 SOL** ($704,251), 95% interval on the sample mean 6,939.44–11,986.96 SOL ($516,433–$892,070).

Extrapolated, not measured: mean REV per sampled block x blocks produced in the window; block count from a measured production rate, not an assumed slot time. Per-block REV across the sample ranged 0.01 to 0.11 SOL (mean 0.04), so treat the daily figure as an order-of-magnitude estimate from a small sample, not a settled total.

Of the fees in the 16 reconciled blocks, **10.51% was burned** (0.06 SOL) and 0.52 SOL went to block leaders. Measured from each block's own fee reward entry — no burn rate is assumed, so this stays correct across a change to the fee split.

### Address activity

| Metric | Value |
| --- | --- |
| Unique fee payers (sampled) | 3,784 |
| Unique accounts touched (sampled) | 29,530 |
| Mean fee payers per block | 343.60 |
| **Daily active addresses** | **not derivable — see below** |

⚠️ Unique non-vote fee payers seen in the sampled blocks only, not a daily total. A true daily active address count is the union over every block in 24 hours; unique counts do not scale from a sample the way sums do, because samples overlap. Extrapolating this number would overstate it, so the daily figure is reported as unavailable instead.

## Economic indicators

| Metric | Value | Source |
| --- | --- | --- |
| SOL price | $74.42 (+0.63% 24h) | CoinGecko |
| Market cap | $43,263,136,838.12 | CoinGecko |
| 24h trading volume | $1,692,014,761.35 | CoinGecko |
| Total value locked | $4,800,643,250 (+0.47% 7d) | DeFiLlama |
| Stablecoin supply | $15,661,470,128.03 | DeFiLlama |
| DEX volume 24h | $1,747,556,147.93 (+2.04% 1d) | DeFiLlama |

_All economic sources are public and keyless — no API key or account required._

## Supply

| Metric | Value |
| --- | --- |
| Total supply | 631,629,703.86 SOL |
| Circulating | 581,306,755.15 SOL |
| Circulating share | 92.03% |

## Validators

| Metric | Value |
| --- | --- |
| Active validators | 692 |
| Delinquent validators | 7 (1%) |
| Active stake | 434,421,657.36 SOL |
| Nakamoto coefficient | 18 |
| Median commission | 5% (mean 12.36%, 259 at 0%) |

### Top validators by stake

| # | Identity | Stake (SOL) | Share | Commission |
| --- | --- | --- | --- | --- |
| 1 | `Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk` | 16,808,219.81 | 3.87% | 7% |
| 2 | `HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU` | 16,003,204.52 | 3.68% | 0% |
| 3 | `JUPiTERrZqgf1jUyR7dSkhMx4Kn2qJyekWsg3LT1h4b` | 12,472,697.16 | 2.87% | 5% |
| 4 | `DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy` | 12,265,636.09 | 2.82% | 0% |
| 5 | `C8Bey3LKVJHVqN6xPTeW8WJfUgFQAeGNBpT4Rp99JP1k` | 9,189,333.01 | 2.12% | 7% |
| 6 | `CAo1dCGYrB6NhHh5xb1cGjUiu86iyCfMTENxgHumSve4` | 8,837,285.49 | 2.03% | 10% |
| 7 | `E1r4Psq84tHfQ6aPTvvDka4U3u8zPVD7gEUrH25RdxHL` | 8,157,243.65 | 1.88% | 0% |
| 8 | `EvnRmnMrd69kFdbLMxWkTn1icZ7DCceRhvmb2SJXqDo4` | 7,899,431.53 | 1.82% | 7% |
| 9 | `9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY` | 7,479,271.15 | 1.72% | 5% |
| 10 | `Awes4Tr6TX8JDzEhCZY2QVNimT6iD1zWHzf1vNyGvpLM` | 6,653,304.21 | 1.53% | 0% |

## Releases and announcements

> Official first-party feeds, fetched without credentials and recorded into this snapshot, so the section re-renders offline unchanged. Feed contents are third-party statements reproduced verbatim, not claims made by this report.

### Agave validator releases

_Agave is the validator client most of the network runs. A release here is the software operators are about to be asked to run._

| Published (UTC) | Entry |
| --- | --- |
| 2026-08-03T12:41:49Z | [Release v4.2.0-rc.1](https://github.com/anza-xyz/agave/releases/tag/v4.2.0-rc.1) |
| 2026-07-27T15:24:59Z | [Release v4.2.0-rc.0](https://github.com/anza-xyz/agave/releases/tag/v4.2.0-rc.0) |
| 2026-07-24T14:31:34Z | [Release v4.3.0-alpha.1](https://github.com/anza-xyz/agave/releases/tag/v4.3.0-alpha.1) |
| 2026-07-24T14:31:17Z | [Release v4.3.0-alpha.2](https://github.com/anza-xyz/agave/releases/tag/v4.3.0-alpha.2) |
| 2026-07-20T12:14:51Z | [Release v4.2.0-beta.2](https://github.com/anza-xyz/agave/releases/tag/v4.2.0-beta.2) |

From `https://github.com/anza-xyz/agave/releases.atom` — anza-xyz/agave (GitHub), public and keyless, recorded at collection time.

### SIMD proposal activity

_Protocol changes are proposed and amended here before they ship. This is the commit feed, so it shows drafting activity, not acceptance — a commit is not a merged-and-agreed upgrade._

| Published (UTC) | Entry |
| --- | --- |
| 2026-07-31T16:27:22Z | [re-amend SIMD-0340: additional inter- and intra- validation (#551)](https://github.com/solana-foundation/solana-improvement-documents/commit/fc519fb3d1ef0f7624b6232bda958438feba09ce) |
| 2026-07-31T08:06:24Z | [SIMD-0433: Loader V3: Set Program Data to ELF Length (#433)](https://github.com/solana-foundation/solana-improvement-documents/commit/06bd4bd6b0b835d110bf4ccb0bc7c759ae88e997) |
| 2026-07-23T01:49:14Z | [SIMD-0550: Double disinflation (#550)](https://github.com/solana-foundation/solana-improvement-documents/commit/b13be70e7454144becbe9c474b296d737d72df98) |
| 2026-07-20T18:46:49Z | [SIMD-0553: Resource and Inclusion Fee (#553)](https://github.com/solana-foundation/solana-improvement-documents/commit/2e6243a5010255ed5b1592f42ebfc890a2c21d05) |
| 2026-07-16T22:18:10Z | [SIMD-0392: Clarify included stake accounts and calculations (#572)](https://github.com/solana-foundation/solana-improvement-documents/commit/0c3548e91bc6553774607181446dd09b00e4a649) |

From `https://github.com/solana-foundation/solana-improvement-documents/commits/main.atom` — solana-foundation/solana-improvement-documents (GitHub), public and keyless, recorded at collection time.

### Network status history

_The operator's own incident record. Entries are historical: an old newest-entry date means no incident has been posted since then, which is information in itself._

| Published (UTC) | Entry |
| --- | --- |
| 2024-02-06T15:09:24Z | [mb-020624](https://status.solana.com/incidents/n5kcgs8dl9pj) |
| 2023-02-26T15:48:39Z | [Cluster Instability](https://status.solana.com/incidents/ymr0gyj9xqyz) |
| 2023-01-08T07:01:17Z | [Public Endpoints and Explorer offline](https://status.solana.com/incidents/mf9plxrkjhnk) |
| 2022-10-01T07:06:06Z | [Degraded Performance](https://status.solana.com/incidents/kxsv0xcz9dn3) |
| 2022-06-06T16:32:07Z | [Mainnet Beta Clock Drift](https://status.solana.com/incidents/f68wm876ph9m) |

From `https://status.solana.com/history.atom` — status.solana.com (official status page), public and keyless, recorded at collection time.

## Upcoming upgrades

> **Static reference data**, last checked 2026-08-05. Not a live feed — verify against the linked SIMD repository.

### Alpenglow (Votor) — Review

Changes the core consensus protocol from Proof of History and TowerBFT to Alpenglow — specifically the Votor parts. Current consensus finality time is 12.8 seconds. Rotor, the data-dissemination half of the Alpenglow white paper, is explicitly excluded from this SIMD: Turbine remains the dissemination protocol and Rotor will get its own proposal later.

_Finality is the property most visible to users and applications, and this is the largest change to Solana consensus since launch. Alpenglow is a family rather than a single proposal — see also SIMD-0357 (validator admission ticket) and SIMD-0384 (migration)._

Source: https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md

### Reduce Slot Times — Draft

Reduces the target slot time from 400ms to 200ms in four feature-gated steps — 350ms, 300ms, 250ms, 200ms. Each step holds ticks_per_slot at 64 and leader windows at 4 slots, scaling per-slot work limits so wall-clock throughput is unchanged. Extends SIMD-0357.

_This report already measures mean slot time against the current 400ms target. If this lands, that target halves in four observable steps — so the measurement above becomes the way to watch this roadmap item arrive._

Source: https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md

---

Generated from a single snapshot by `render.py`. On-chain data comes from public
Solana JSON-RPC; economic data from public keyless endpoints. No third-party
Python packages, no API keys, and no account is required for any source.
