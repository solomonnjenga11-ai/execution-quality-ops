-- 03  Data-quality rules over both blotters, returned as one issues table.
--
-- Each rule is a SELECT; UNION ALL stacks them so ops gets a single queue with
-- source, record, rule, severity and a human-readable detail. Currency-pair
-- checks run against the ref_currencies table and match the Python validator
-- in src/eqops/currency.py (checked in tests).
--
-- export: v_fx_pair_check
-- export: v_dq_issues
-- export: v_dq_summary

DROP VIEW IF EXISTS v_fx_pair_check;
CREATE VIEW v_fx_pair_check AS
WITH norm AS (
    SELECT trade_id, ccy_pair,
           TRIM(COALESCE(ccy_pair, ''))                                       AS raw_pair,
           UPPER(REPLACE(REPLACE(TRIM(COALESCE(ccy_pair, '')), '/', ''), '-', '')) AS compact
    FROM raw_fx_trades
),
split AS (
    SELECT *,
           CASE WHEN raw_pair = '' THEN 'MISSING'
                WHEN compact NOT GLOB '[A-Z][A-Z][A-Z][A-Z][A-Z][A-Z]'
                  OR (LENGTH(raw_pair) = 7 AND SUBSTR(raw_pair, 4, 1) NOT IN ('/', '-'))
                  OR LENGTH(raw_pair) NOT IN (6, 7)                    THEN 'BAD_FORMAT'
           END                                                          AS format_issue,
           SUBSTR(compact, 1, 3) AS base_ccy,
           SUBSTR(compact, 4, 3) AS quote_ccy
    FROM norm
)
SELECT s.trade_id, s.ccy_pair,
       CASE WHEN s.format_issue IS NULL THEN s.base_ccy || '/' || s.quote_ccy END AS normalised_pair,
       COALESCE(
           s.format_issue,
           CASE WHEN b.ccy IS NULL THEN 'UNKNOWN_CCY:' || s.base_ccy
                WHEN b.tradable = 0 THEN 'NON_TRADABLE:' || s.base_ccy END,
           CASE WHEN q.ccy IS NULL THEN 'UNKNOWN_CCY:' || s.quote_ccy
                WHEN q.tradable = 0 THEN 'NON_TRADABLE:' || s.quote_ccy END,
           CASE WHEN s.base_ccy = s.quote_ccy THEN 'SAME_CCY' END
       )                                                                   AS pair_error,
       CASE WHEN s.format_issue IS NULL AND s.raw_pair <> UPPER(s.raw_pair) THEN 1 ELSE 0 END
                                                                           AS lowercase_warning
FROM split s
LEFT JOIN ref_currencies b ON b.ccy = s.base_ccy
LEFT JOIN ref_currencies q ON q.ccy = s.quote_ccy;

DROP VIEW IF EXISTS v_dq_issues;
CREATE VIEW v_dq_issues AS
-- FX: invalid currency pair (blocks settlement instructions)
SELECT 'FX' AS source, trade_id AS record_id, 'INVALID_CCY_PAIR' AS rule, 'HIGH' AS severity,
       'Booked as "' || COALESCE(ccy_pair, '') || '": ' || pair_error AS detail
FROM v_fx_pair_check WHERE pair_error IS NOT NULL
UNION ALL
SELECT 'FX', trade_id, 'NON_STANDARD_PAIR_FORMAT', 'LOW',
       'Booked as "' || ccy_pair || '", repairable to ' || normalised_pair
FROM v_fx_pair_check WHERE pair_error IS NULL AND lowercase_warning = 1
UNION ALL
-- FX: economics
SELECT 'FX', trade_id, 'NON_POSITIVE_RATE', 'HIGH', 'Rate = ' || rate
FROM raw_fx_trades WHERE rate IS NULL OR rate <= 0
UNION ALL
SELECT 'FX', trade_id, 'NON_POSITIVE_NOTIONAL', 'HIGH', 'Notional = ' || notional_base
FROM raw_fx_trades WHERE notional_base IS NULL OR notional_base <= 0
UNION ALL
-- FX: dates
SELECT 'FX', trade_id, 'VALUE_DATE_BEFORE_TRADE_DATE', 'HIGH',
       'Trade ' || trade_date || ', value ' || value_date
FROM raw_fx_trades WHERE DATE(value_date) < DATE(trade_date)
UNION ALL
SELECT 'FX', trade_id, 'VALUE_DATE_ON_WEEKEND', 'MEDIUM', 'Value date ' || value_date
FROM raw_fx_trades WHERE strftime('%w', value_date) IN ('0', '6')
UNION ALL
-- duplicates (both blotters)
SELECT 'FX', trade_id, 'DUPLICATE_TRADE_ID', 'HIGH', COUNT(*) || ' rows share this ID'
FROM raw_fx_trades GROUP BY trade_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'CRYPTO', trade_id, 'DUPLICATE_TRADE_ID', 'HIGH', COUNT(*) || ' rows share this ID'
FROM raw_crypto_trades GROUP BY trade_id HAVING COUNT(*) > 1
UNION ALL
-- crypto: pricing problems found in step 01
SELECT 'CRYPTO', trade_id, pricing_status,
       CASE pricing_status WHEN 'PRICE_OUTLIER' THEN 'HIGH' WHEN 'NO_PRICE_FEED' THEN 'HIGH' ELSE 'MEDIUM' END,
       symbol || ' ' || side || ' ' || qty || ' @ ' || price ||
       COALESCE(' vs mark ' || mark_px || ' (' || slippage_bps || ' bps)', '')
FROM v_trade_marks
WHERE pricing_status IN ('PRICE_OUTLIER', 'NO_PRICE_FEED', 'BEFORE_FIRST_TICK')
UNION ALL
SELECT 'CRYPTO', trade_id, 'NON_POSITIVE_QTY_OR_PRICE', 'HIGH', 'qty ' || qty || ', price ' || price
FROM raw_crypto_trades WHERE qty <= 0 OR price <= 0
UNION ALL
SELECT 'CRYPTO', trade_id, 'INVALID_SIDE', 'HIGH', 'side = ' || COALESCE(side, 'NULL')
FROM raw_crypto_trades WHERE UPPER(TRIM(side)) NOT IN ('BUY', 'SELL') OR side IS NULL;

DROP VIEW IF EXISTS v_dq_summary;
CREATE VIEW v_dq_summary AS
SELECT source, rule, severity, COUNT(*) AS issues
FROM v_dq_issues
GROUP BY source, rule, severity
ORDER BY CASE severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, issues DESC;
