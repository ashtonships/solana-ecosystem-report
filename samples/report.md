# Solana Ecosystem Report

**Collected:** 2026-08-05T10:36:01+00:00  
**Source:** `https://api.mainnet-beta.solana.com` (public JSON-RPC, no API key)  
**Network health:** 🟢 healthy

## Anomalies

🟢 **None detected** across 6 snapshots (baseline of 5).

## Network

| Metric | Value |
| --- | --- |
| Current slot | 437,363,069 |
| Block time | 2026-08-05T10:35:40+00:00 |
| Block height | 415,417,589 |
| Total transactions | 535,301,001,929 |

## Epoch

| Metric | Value |
| --- | --- |
| Epoch | 1012 |
| Progress | 41.45% |
| Slot in epoch | 179,069 of 432,000 |

## Performance

| Metric | Value |
| --- | --- |
| Latest TPS | 3,006.85 |
| Mean TPS (8 samples) | 3,066.77 |
| Peak TPS | 3,204.77 |
| Mean slot time | 0.42s |

## Economic indicators

| Metric | Value | Source |
| --- | --- | --- |
| SOL price | $73.97 (+1.20% 24h) | CoinGecko |
| Market cap | $42,999,647,416.55 | CoinGecko |
| 24h trading volume | $1,523,134,795.92 | CoinGecko |
| Total value locked | $4,797,107,540 (+0.40% 7d) | DeFiLlama |
| Stablecoin supply | $15,820,740,037.79 | DeFiLlama |
| DEX volume 24h | $1,738,979,711.93 (+1.54% 1d) | DeFiLlama |

_All economic sources are public and keyless — no API key or account required._

## Supply

| Metric | Value |
| --- | --- |
| Total supply | 631,630,021.99 SOL |
| Circulating | 581,307,073.31 SOL |
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
