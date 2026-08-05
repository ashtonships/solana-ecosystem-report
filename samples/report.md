# Solana Ecosystem Report

**Collected:** 2026-08-05T11:29:07+00:00  
**Source:** `https://api.mainnet-beta.solana.com` (public JSON-RPC, no API key)  
**Network health:** 🟢 healthy

## Anomalies

🟢 **None detected** across 8 snapshots (baseline of 7).

## Network

| Metric | Value |
| --- | --- |
| Current slot | 437,370,578 |
| Block time | 2026-08-05T11:28:23+00:00 |
| Block height | 415,425,093 |
| Total transactions | 535,310,817,788 |

## Epoch

| Metric | Value |
| --- | --- |
| Epoch | 1012 |
| Progress | 43.19% |
| Slot in epoch | 186,578 of 432,000 |

## Performance

| Metric | Value |
| --- | --- |
| Latest TPS | 3,221.57 |
| Mean TPS (8 samples) | 3,156.32 |
| Peak TPS | 3,284.87 |
| Mean slot time | 0.42s |

## Fees, REV and activity

> Sampled from **16 blocks** evenly spaced across 25.4h of chain history (~215,806 blocks produced in that window). Public JSON-RPC `getBlock`, no API key.

| Transaction fee | Value | USD |
| --- | --- | --- |
| Median | 5,527 lamports | $0.000408 |
| Mean | 39,231 lamports | $0.002896 |
| 90th percentile | 19,918 lamports | $0.001471 |
| 99th percentile | 635,912 lamports | $0.05 |

Measured over **12,280 non-vote transactions**. Vote transactions were 46.81% of all sampled traffic and are excluded — every vote pays exactly 5,000 lamports, so including them pins the median there and the figure stops describing what it costs anyone to use the network. 40.78% of non-vote transactions failed on-chain.

### Real economic value

REV is **base fees + priority fees + Jito tips**.

| Component | Sampled (SOL) | Share |
| --- | --- | --- |
| Base fees | 0.12 | 19.4% |
| Priority fees | 0.41 | 65.4% |
| Jito tips | 0.10 | 15.1% |
| **Total sampled** | **0.63** | 100% |

**Estimated 24h REV: 8,522.16 SOL** ($629,191), 95% interval on the sample mean 2,492.52–14,551.79 SOL ($184,023–$1,074,359).

Extrapolated, not measured: mean REV per sampled block x blocks produced in the window; block count from a measured production rate, not an assumed slot time. Per-block REV across the sample ranged 0.01 to 0.25 SOL (mean 0.04), so treat the daily figure as an order-of-magnitude estimate from a small sample, not a settled total.

Of the fees in the 16 reconciled blocks, **11.47% was burned** (0.06 SOL) and 0.47 SOL went to block leaders. Measured from each block's own fee reward entry — no burn rate is assumed, so this stays correct across a change to the fee split.

### Address activity

| Metric | Value |
| --- | --- |
| Unique fee payers (sampled) | 4,188 |
| Unique accounts touched (sampled) | 31,904 |
| Mean fee payers per block | 375.40 |
| **Daily active addresses** | **not derivable — see below** |

⚠️ Unique non-vote fee payers seen in the sampled blocks only, not a daily total. A true daily active address count is the union over every block in 24 hours; unique counts do not scale from a sample the way sums do, because samples overlap. Extrapolating this number would overstate it, so the daily figure is reported as unavailable instead.

## Economic indicators

| Metric | Value | Source |
| --- | --- | --- |
| SOL price | $73.83 (+0.34% 24h) | CoinGecko |
| Market cap | $42,919,634,277.96 | CoinGecko |
| 24h trading volume | $1,513,687,190.86 | CoinGecko |
| Total value locked | $4,794,880,239 (+0.35% 7d) | DeFiLlama |
| Stablecoin supply | $15,614,638,702.36 | DeFiLlama |
| DEX volume 24h | $1,746,976,990.93 (+2.01% 1d) | DeFiLlama |

_All economic sources are public and keyless — no API key or account required._

## Supply

| Metric | Value |
| --- | --- |
| Total supply | 631,629,996.39 SOL |
| Circulating | 581,307,047.72 SOL |
| Circulating share | 92.03% |

## Validators

| Metric | Value |
| --- | --- |
| Active validators | 692 |
| Delinquent validators | 7 (1%) |
| Active stake | 434,421,657.36 SOL |
| Nakamoto coefficient | 18 |
| Median commission | 5% (mean 12.07%, 261 at 0%) |

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
