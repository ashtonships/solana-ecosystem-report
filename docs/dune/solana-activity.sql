-- Solana ecosystem activity metrics for the report's Dune adapter.
--
-- Produces seven metric families with the adapter's expected columns:
--   daily_non_vote_fee_payers   — fee payers (vote transactions excluded)
--   daily_dex_volume_total      — TRADE-LEG DEX volume, all projects summed
--   daily_dex_volume_by_project — TRADE-LEG DEX volume per project
--   daily_xstocks_dex_volume — USD volume only with complete row pricing
--   daily_xstocks_dex_trade_legs — all OR-matched xStock trade legs
--   daily_xstocks_dex_priced_trade_legs — legs with valid USD pricing
--   daily_transaction_fees_sol — exact transaction fee total, not REV or Jito tips
--
-- IMPORTANT BASIS NOTES:
--   1. solana.transactions contains NO vote transactions at all (votes live in
--      solana.vote_transactions), so no vote filter is needed — or possible.
--   2. dex_solana.trades records swap legs. The DEX volume metrics here are
--      TRADE-LEG volume (sum of amount_usd per trade row). They are never
--      unique-user volume and must not be presented as trader counts.
--   3. The xStock scope is 107 mints from solana-foundation/tokens revision
--      661a6f0ca466ccf74ea967dae7e3abbcdc088bc0. Bought OR sold membership
--      counts each trade leg once. Missing prices withhold the USD value.
--   4. gas_solana.fees tx_fee covers vote and normal transaction fees. It is
--      not protocol REV and contains no separate Jito-tip claim here.
--
-- Only completed UTC days: both lower and upper boundaries are UTC midnight.
-- Columns: metric_id VARCHAR, day DATE, dimension VARCHAR, value DOUBLE,
--          unit VARCHAR, sample_count BIGINT. ORDER BY day DESC.

WITH registry (mint) AS (
    VALUES
        ('Xs151QeqTCiuKtinzfRATnUESM2xTU6V9Wy8Vy538ci'),
        ('Xs1CqRVt2mGVfMSHf7M6cDhkjpw8HDF14XbQNzDnGg4'),
        ('Xs2yquAgsHByNzx68WJC55WHjHBvG9JsMB7CWjTLyPy'),
        ('Xs31mE5EiqjSHEaiX9QDKCN6NvSGCqpJ6f1FNq2wri5'),
        ('Xs3ZFkPYT2BN7qBMqf1j1bfTeTm1rFzEFSsQ1z3wAKU'),
        ('Xs3c2aZenyRQwXjki5MDxJEJ2km27ef2rWQMFWx7QKJ'),
        ('Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg'),
        ('Xs4uNhvBDAcp2mz3g9XR5q3vzLgmF1ANWxJgWk2d5u3'),
        ('Xs5UJzmCRQ8DWZjskExdSQDnbE6iLkRu2jjrRAB1JSU'),
        ('Xs5ywCCaayf3TFdxFxuJCr25XMoqY6vdfJKkSHE4D6r'),
        ('Xs6B6zawENwAbWVi7w92rjazLuAr5Az59qgWKcNb45x'),
        ('Xs7ZdzSHLU9ftNJsii5fCeJhoRWSC32SQGzGQtePxNu'),
        ('Xs7xXqkcK7K8urEqGg52SECi79dRp2cEKKuYjUePYDw'),
        ('Xs8drBWy3Sd5QY3aifG9kt9KFs2K3PGZmx7jWrsrk57'),
        ('Xs936JPaWKEXmpu54DZ149GETV7PmsnTqnMWxgcPNAj'),
        ('XsApJFV9MAktqnAc6jqzsHVujxkGm9xcSUffaBoYLKC'),
        ('XsAsZLF4MmsvS1sDxRMrUz7REjHfwbC9UAMXSRBqgEB'),
        ('XsAtbqkAP1HJxy7hFDeq7ok6yM43DQ9mQ1Rh861X8rw'),
        ('XsBGEXxbBcuu8Nrokj14G8v4ezT3JYWWLneTzXK8t6Z'),
        ('XsBgbNRDujfznQq8P4NLKEkRTbPxUVt4v1xwkDzMMtQ'),
        ('XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN'),
        ('XsDYSbgLh3T7oh59UmRX1F5WjDgJD3nmzACaG5LR1MB'),
        ('XsDZMGEU8zadWFCkTtPBoPWYcUX3JHVmghnwf2Mve2q'),
        ('XsDgw22qRLTv5Uwuzn6T63cW69exG41T6gwQhEK22u2'),
        ('XsDmtC5EpfzJoCzDN2SJP98xpCsSR2CqdZauJSvHNXT'),
        ('XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB'),
        ('XsEH7wWfJJu2ZT3UCFeVfALnVA6CP5ur7Ee11KmzVpL'),
        ('XsEoih2x6nZuUjFwzGoba6MFmtzCkzW2c4YAm6baQbq'),
        ('XsFj75v4Tw8TWSDaDoVTHF8RAjEEkmUeKgoRjB9DHsf'),
        ('XsG5QyZTQnVSpsXRpD92K5ZGoxXjsmfTyx7fW7r18DV'),
        ('XsGVi5eo1Dh2zUpic4qACcjuWGjNv8GCt3dm5XcX6Dn'),
        ('XsGtpmjhmC8kyjVSWL4VicGu36ceq9u55PTgF8bhGv6'),
        ('XsHt1AVPshZpfFWGGpooJ8tdeGknUQVJ9CwhvKrnZrw'),
        ('XsHtf5RpxsQ7jeJ9ivNewouZKJHbPxhPoEy6yYvULr7'),
        ('XsJb1p4Ks6VFggq68JyMz7S3kdRfkqh4wA6EMyxi4DD'),
        ('XsKSh3QDynp6oms9yHjZXbZo3pKzUBoqUFKPHS2g9Bh'),
        ('XsMAqkcKsUewDrzVkait4e5u4y8REgtyS7jWgCpLV2C'),
        ('XsMJtFbb8BwzQtck3oRXyAfs7SAPRuTXnSEDNd7BAVz'),
        ('XsNNMt7WTNA2sV3jrb1NNfNgapxRF5i4i6GcnTRRHts'),
        ('XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ'),
        ('XsPLBFy59Q3hY59KLAJur8QyvziMF4xUxGTxXqXE7cT'),
        ('XsPdAVBi8Zc1xvv53k4JcMrQaEDTgkGqKYeh7AYgPHV'),
        ('XsQ6NfzzLH8nspjrB9X8R2K64Zz7Tnxqu12juDiMPMW'),
        ('XsQLZycSZ7QnBBdBXQaTbQdiUcbRqjNJgyBGAMzhHav'),
        ('XsR4LAtaBgTKTRUhiijY1ba13nx4bepeEcag2Pr4dZ1'),
        ('XsRbLZthfABAPAfumWNEJhPyiKDW6TvDVeAeW7oKqA2'),
        ('XsRiRZg9NGkL3WEZZmqnxvxEoyeHG8LhgzMQF2HRE6F'),
        ('XsRrH4fDA27xfyDjpcwefvcBdHCXs34krUuaXGEXzqz'),
        ('XsSr8anD1hkvNMu8XQiVcmiaTP7XGvYu7Q58LdmtE8Z'),
        ('XsTuW2zaiPbzgrnQ7b4Z99zeULMM7gPxppE61CtpyDj'),
        ('XsXcJ6GZ9kVnjqGsjBnktRcuwMBmvKWh8S93RefZ1rF'),
        ('XsYMHtwJcWon5GkPHzdDbCCztKtKzEurJnbydxgjsqS'),
        ('XsYdjDjNUygZ7yGKfQaB6TxLh2gC6RRjzLtLAGJrhzV'),
        ('XsZUSqxAXKJkEimvD4CoVvEb4WUC92TFgj5zRtBxFeL'),
        ('Xsa62P5mvPszXL1krVUnU5ar38bBSVcWAB6fmPCo5Zu'),
        ('XsaBXg8dU5cPM6ehmVctMkVqoiRG2ZjMo1cyBJ3AykQ'),
        ('XsaHND8sHyfMfsWPj6kSdd5VwvCayZvjYgKmmcNL5qh'),
        ('XsaQTCgebC2KPbf27KUhdv5JFvHhQ4GDAPURwrEhAzb'),
        ('XsafvsGtzFqqHgTnA3aPC83EAMkacU5mcGtcSayhpVV'),
        ('XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp'),
        ('Xsba6tUnSjDae2VcopDB6FGGDaxRrewFCDa5hKn5vT3'),
        ('Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh'),
        ('XscE4GUcsYhcyZu5ATiGUMmhxYa1D5fwbpJw4K6K4dp'),
        ('XsczbcQ3zfcgAEt9qHQES8pxKAVG5rujPSHQEXi4kaN'),
        ('XseQvfvuDT3KHKiwPNW1gcjfHbFjEMVTV5NJzDdNQmu'),
        ('Xseo8tgCZfkHxWS9xbFYeKFyMSbWEvZGFV1Gh53GtCV'),
        ('Xsf9mBktVB9BSU5kf4nHxPq5hCBJ2j2ui3ecFGxPRGc'),
        ('XsfAzPzYrYjd4Dpa9BU3cusBsvWfVB9gBcyGC87S57n'),
        ('XsgSaSvNSqLTtFuyWPBhK9196Xb9Bbdyjj4fH3cPJGo'),
        ('XsgaUyp4jd1fNBCxgtTKkW64xnnhQcvgaxzsbAq5ZD1'),
        ('Xsgrm4D6VBTfDx5bs7GNdjtmWDgZ8V79r3YpsYwf5py'),
        ('XsguBZPkM9BDmxspmWe29EmrYZBv21ENcC27Pqh7grB'),
        ('XshPgPdXFRWB8tP1j82rebb2Q9rPgGX37RuqzohmArM'),
        ('XshWQWYVp5ff8CrAEsGmLVKD47nBWi3Ygn5v8wXK27G'),
        ('XshuHQ6o6SVpUNawvnnTMxsZ4tacZsNgVCLorv7TkFq'),
        ('XsiRaD8qZm1P3KViowDYFS8rZ8BYT2g7ZEeyztthRye'),
        ('XsicTFC7G68d74FaYDctWggkKDgkMx8LpNoFNr81TrF'),
        ('XsjFwUPiLofddX5cWFHW35GCbXcSu1BCUGfxoQAQjeL'),
        ('XsjXR5jf4d4GNpJXwLvEBTMwK3ZYro2qZGgvUCzyJrx'),
        ('Xsn3H7ACEpSF2ULxeiD6kW4jRZXpurh8ZPttyfoS56W'),
        ('XsnQnU7AdbRZYe2akqqpibDdXjkieGFfSkbkjX1Sd1X'),
        ('Xsnuv4omNoHozR6EEW5mXkw8Nrny5rB3jVfLqi6gKMH'),
        ('XsoBhf2ufR8fTyNSjqfU71DYGaE6Z3SUGAidpzriAA4'),
        ('XspwhyYPdWVM8XBHZnpS9hgyag9MKjLRyE3tVfmCbSr'),
        ('XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX'),
        ('XsqE9cRRpzxcGKDXj1BJ7Xmg4GRhZoyY1KpmGSxAWT2'),
        ('XsqML71RLbyUM3CmY4EYN88zE6BJkpvJACnUKBdGD3x'),
        ('XsqgsbXwWogGJsNcVZ3TyVouy2MbTkfCFhCGGGcQZ2p'),
        ('Xsr3pdLQyXvDJBFgpR5nexCEZwXvigb8wbPYp4YoNFf'),
        ('XsrBCwaH8c46xiqXBChzobgufRKxQxAWUWbndgBNzFn'),
        ('Xss5RAku5EH6UViFdvW7ss9xQjwQLsrs2opPMhb3k43'),
        ('XssfYT48PeXG2EmNdEaVtQcveNhxYnM42KTqdjmiBS9'),
        ('XstYR6KGqZFchAuhHoEe4mL8W2kL9aqGW1EDBKRrmGB'),
        ('XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1'),
        ('XsuwUbQSzCJN2wZabD1Gxf1MER2Ypa7hMzVMYB2WawJ'),
        ('XsuxRGDzbLjnJ72v74b7p9VY6N66uYgTCyfwwRjVCJA'),
        ('Xsv99frTRUeornyvCfvhnDesQDWuvns1M852Pez91vF'),
        ('XsvKCaNsxg2GN8jjUmq71qukMJr7Q1c5R2Mk9P8kcS8'),
        ('XsvNBAYkrDRNhA7wPHQfX3ZUXZyZLdnCQDfHZ56bzpg'),
        ('XswCi2U1G6Ppbw1QhG45yKb8UKuR1FKLJrquv2FZSD4'),
        ('XswbinNKyPmzTa5CskMbCPvMW6G5CMnZXZEeQSSQoie'),
        ('Xswbpc8UqU6e1j9QZEWCjBMjyvz4twqD7PCy6j2e7jj'),
        ('XswsQk4duEQmCbGzfqUUWYmi7pV7xpJ9eEmLHXCaEQP'),
        ('XsyusqQvb8RDULsY9szwvUv2CxrDKa642w7kecZPdRM'),
        ('XszjVtyhowGjSC5odCqBpW1CtXXwXjYokymrk7fGKD3'),
        ('XszvaiXGPwvk2nwb3o9C1CX4K6zH8sez11E6uyup6fe'),
        ('suifhC9gU1VbJAPYPTBkHJyyyStKGLLYPVDTmPoqbvA')
),
fee_payers AS (
    SELECT
        CAST(DATE_TRUNC('day', block_time) AS DATE) AS day,
        COUNT(DISTINCT signer)        AS value,
        COUNT(DISTINCT signer)        AS sample_count
    FROM solana.transactions
    WHERE block_time >= CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE) - INTERVAL '2' DAY
      AND block_time < CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE)
    GROUP BY 1
),
transaction_fee_days AS (
    SELECT
        CAST(block_date AS DATE) AS day,
        COUNT(*) AS transaction_rows,
        COUNT_IF(tx_fee IS NOT NULL AND is_finite(tx_fee) AND tx_fee >= 0) AS valid_fee_rows,
        SUM(CASE WHEN tx_fee IS NOT NULL AND is_finite(tx_fee) AND tx_fee >= 0
                 THEN tx_fee END) AS transaction_fees_sol
    FROM gas_solana.fees
    WHERE block_date >= CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE) - INTERVAL '2' DAY
      AND block_date < CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE)
      AND block_month >= DATE_TRUNC('month', CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE) - INTERVAL '2' DAY)
      AND block_month <= DATE_TRUNC('month', CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE))
    GROUP BY 1
),
dex_trade_legs AS (
    SELECT
        CAST(DATE_TRUNC('day', block_time) AS DATE) AS day,
        project,
        amount_usd,
        (token_bought_mint_address IN (SELECT mint FROM registry)
         OR token_sold_mint_address IN (SELECT mint FROM registry)) AS is_xstock
    FROM dex_solana.trades
    WHERE block_time >= CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE) - INTERVAL '8' DAY
      AND block_time < CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE)
      -- partition filter keeps the scan (and credit cost) bounded
      AND block_month >= DATE_TRUNC('month', CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE) - INTERVAL '8' DAY)
      AND block_month <= DATE_TRUNC('month', CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE))
),
dex_trades AS (
    SELECT day, project, SUM(amount_usd) AS value_usd, COUNT(*) AS sample_count
    FROM dex_trade_legs
    GROUP BY 1, 2
),
xstock_daily AS (
    SELECT
        day,
        COUNT(*) AS trade_legs,
        COUNT_IF(amount_usd IS NOT NULL AND is_finite(amount_usd) AND amount_usd >= 0)
            AS priced_trade_legs,
        SUM(CASE WHEN amount_usd IS NOT NULL AND is_finite(amount_usd) AND amount_usd >= 0
                 THEN amount_usd END) AS priced_volume_usd
    FROM dex_trade_legs
    WHERE is_xstock
    GROUP BY 1
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
    'daily_transaction_fees_sol' AS metric_id,
    day,
    NULL AS dimension,
    CAST(transaction_fees_sol AS DOUBLE) AS value,
    'sol' AS unit,
    CAST(transaction_rows AS BIGINT) AS sample_count
FROM transaction_fee_days
WHERE transaction_rows = valid_fee_rows AND valid_fee_rows > 0

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

UNION ALL

SELECT
    'daily_xstocks_dex_volume' AS metric_id,
    day,
    'pinned_107_xstocks' AS dimension,
    CAST(priced_volume_usd AS DOUBLE) AS value,
    'usd' AS unit,
    CAST(priced_trade_legs AS BIGINT) AS sample_count
FROM xstock_daily
WHERE trade_legs = priced_trade_legs AND priced_trade_legs > 0

UNION ALL

SELECT
    'daily_xstocks_dex_trade_legs' AS metric_id,
    day,
    'pinned_107_xstocks' AS dimension,
    CAST(trade_legs AS DOUBLE) AS value,
    'trade_legs' AS unit,
    CAST(trade_legs AS BIGINT) AS sample_count
FROM xstock_daily

UNION ALL

SELECT
    'daily_xstocks_dex_priced_trade_legs' AS metric_id,
    day,
    'pinned_107_xstocks' AS dimension,
    CAST(priced_trade_legs AS DOUBLE) AS value,
    'trade_legs' AS unit,
    CAST(trade_legs AS BIGINT) AS sample_count
FROM xstock_daily

ORDER BY day DESC, metric_id
