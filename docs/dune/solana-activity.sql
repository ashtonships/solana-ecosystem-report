-- Solana ecosystem activity metrics for the report's Dune adapter.
--
-- Produces exactly three metric families with the adapter's expected columns:
--   daily_non_vote_fee_payers   — fee payers (vote transactions excluded)
--   daily_dex_volume_total      — TRADE-LEG DEX volume, all projects summed
--   daily_dex_volume_by_project — TRADE-LEG DEX volume per project
--
-- IMPORTANT BASIS NOTES:
--   1. solana.transactions contains NO vote transactions at all (votes live in
--      solana.vote_transactions), so no vote filter is needed — or possible.
--   2. dex_solana.trades records swap legs. The DEX volume metrics here are
--      TRADE-LEG volume (sum of amount_usd per trade row). They are never
--      unique-user volume and must not be presented as trader counts.
--
-- Columns: metric_id VARCHAR, day DATE, dimension VARCHAR, value DOUBLE,
--          unit VARCHAR, sample_count BIGINT. ORDER BY day DESC.

WITH fee_payers AS (
    SELECT
        DATE_TRUNC('day', block_time) AS day,
        COUNT(DISTINCT signer)        AS value,
        COUNT(DISTINCT signer)        AS sample_count
    FROM solana.transactions
    WHERE block_time >= NOW() - INTERVAL '2' DAY
    GROUP BY 1
),
dex_trades AS (
    SELECT
        DATE_TRUNC('day', block_time) AS day,
        project,
        SUM(amount_usd)               AS value_usd,
        COUNT(*)                      AS sample_count
    FROM dex_solana.trades
    WHERE block_time >= NOW() - INTERVAL '8' DAY
      -- partition filter keeps the scan (and credit cost) bounded
      AND block_month >= DATE_TRUNC('month', NOW() - INTERVAL '8' DAY)
      AND block_month <= DATE_TRUNC('month', NOW())
    GROUP BY 1, 2
)
SELECT
    'daily_non_vote_fee_payers'      AS metric_id,
    day                              AS day,
    NULL                             AS dimension,
    CAST(value AS DOUBLE)            AS value,
    'fee_payers'                     AS unit,
    CAST(sample_count AS BIGINT)     AS sample_count
FROM fee_payers

UNION ALL

SELECT
    'daily_dex_volume_total'         AS metric_id,
    day                              AS day,
    NULL                             AS dimension,
    CAST(SUM(value_usd) AS DOUBLE)   AS value,
    'usd'                            AS unit,
    CAST(SUM(sample_count) AS BIGINT) AS sample_count
FROM dex_trades
GROUP BY day

UNION ALL

SELECT
    'daily_dex_volume_by_project'    AS metric_id,
    day                              AS day,
    project                          AS dimension,
    CAST(value_usd AS DOUBLE)        AS value,
    'usd'                            AS unit,
    CAST(sample_count AS BIGINT)     AS sample_count
FROM dex_trades

ORDER BY day DESC
