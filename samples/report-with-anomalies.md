# Solana Ecosystem Report

**Collected:** 2026-08-05T12:45:00+00:00  
**Source:** `https://api.mainnet-beta.solana.com` (public JSON-RPC, no API key)  
**Network health:** 🔴 unhealthy

## Anomalies

| | Finding | Observed | Baseline |
| --- | --- | --- | --- |
| 🔴 critical | **Network reports unhealthy** — getHealth did not return ok. It was healthy at the previous snapshot. | error: node is behind | — |
| 🔴 critical | **Transaction throughput dropped sharply** — TPS is 75.0% below the 4-snapshot median. | 735.71 | 2942.83 |
| 🔴 critical | **Validator delinquency is elevated** — 8.40% of validators are delinquent, at or above the 5.0% threshold. | 8.4 | — |
| 🔴 critical | **Slot did not advance** — The current slot is not ahead of the previous snapshot — the chain or the collector is stuck. | 437361107 | 437361112 |
| 🟡 warning | **Slot times are slower than target** — Mean slot time 0.950s exceeds the 0.6s threshold (network targets 0.400s). | 0.95 | 0.4 |
| 🟡 warning | **Validator delinquency jumped** — Up 7.40 percentage points against the 4-snapshot median. | 8.4 | 1.0 |
| 🟡 warning | **Active stake moved** — Down 20.00% against the 4-snapshot median (SOL). | 347537325.89 | 434421657.36 |

## Network

| Metric | Value |
| --- | --- |
| Current slot | 437,361,107 |
| Block time | 2026-08-05T10:21:55+00:00 |
| Block height | 415,415,636 |
| Total transactions | 535,298,472,591 |

## Epoch

| Metric | Value |
| --- | --- |
| Epoch | 1012 |
| Progress | 41% |
| Slot in epoch | 177,112 of 432,000 |

## Performance

| Metric | Value |
| --- | --- |
| Latest TPS | 735.71 |
| Mean TPS (8 samples) | 3,021.18 |
| Peak TPS | 3,184.72 |
| Mean slot time | 0.95s |

## Fees, REV and activity

_Block sampling unavailable in this snapshot._

## Economic indicators

_Economic sources unavailable in this snapshot._

## Supply

| Metric | Value |
| --- | --- |
| Total supply | 631,630,028.58 SOL |
| Circulating | 581,307,079.90 SOL |
| Circulating share | 92.03% |

## Validators

| Metric | Value |
| --- | --- |
| Active validators | 692 |
| Delinquent validators | 7 (8.40%) |
| Active stake | 347,537,325.89 SOL |
| Nakamoto coefficient | 18 |
| Median commission | —% (mean —%, — at 0%) |

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
