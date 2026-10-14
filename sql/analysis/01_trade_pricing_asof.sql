-- 01  Price every crypto trade against the market at the moment it happened.
--
-- The problem: trades are stamped as ISO strings, prices as epoch milliseconds,
-- and the two never share a timestamp. Each trade needs the LAST price at or
-- before its own time for the same symbol (an "as-of" join), not an equi-join.
--
-- export: v_trade_marks
-- export: v_slippage_by_account

DROP VIEW IF EXISTS v_trades_norm;
CREATE VIEW v_trades_norm AS
SELECT
    trade_id,
    trade_time,
    -- ISO-8601 -> epoch ms so both feeds sit on one clock
    CAST(ROUND((julianday(trade_time) - 2440587.5) * 86400000.0) AS INTEGER) AS trade_ts_ms,
    account,
    UPPER(TRIM(symbol))                                  AS symbol,
    UPPER(TRIM(side))                                    AS side,
    CASE UPPER(TRIM(side)) WHEN 'BUY' THEN 1 WHEN 'SELL' THEN -1 END AS side_sign,
    qty,
    price,
    ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY trade_time) AS dup_rank  -- >1 = repeat booking
FROM raw_crypto_trades;

DROP VIEW IF EXISTS v_trade_marks;
CREATE VIEW v_trade_marks AS
WITH asof AS (
    SELECT t.*,
           (SELECT MAX(p.ts_ms)
              FROM raw_crypto_prices p
             WHERE p.symbol = t.symbol
               AND p.ts_ms <= t.trade_ts_ms)            AS mark_ts_ms
    FROM v_trades_norm t
),
marked AS (
    SELECT a.*, p.price AS mark_px, p.venue AS mark_venue,
           a.trade_ts_ms - a.mark_ts_ms                 AS mark_age_ms,
           EXISTS (SELECT 1 FROM raw_crypto_prices x WHERE x.symbol = a.symbol) AS has_feed
    FROM asof a
    LEFT JOIN raw_crypto_prices p
           ON p.symbol = a.symbol AND p.ts_ms = a.mark_ts_ms
)
SELECT
    trade_id, trade_time, account, symbol, side, qty, price,
    mark_px,
    strftime('%Y-%m-%dT%H:%M:%fZ', mark_ts_ms / 1000.0, 'unixepoch') AS mark_time,
    mark_age_ms,
    ROUND(side_sign * (price - mark_px) / mark_px * 10000.0, 2)      AS slippage_bps,
    ROUND(side_sign * (price - mark_px) * qty, 2)                    AS slippage_usd,
    CASE
        WHEN dup_rank > 1                                   THEN 'DUPLICATE_BOOKING'
        WHEN has_feed = 0                                   THEN 'NO_PRICE_FEED'
        WHEN mark_px IS NULL                                THEN 'BEFORE_FIRST_TICK'
        WHEN ABS(price - mark_px) / mark_px * 10000 > 200   THEN 'PRICE_OUTLIER'
        WHEN mark_age_ms > 60000                            THEN 'STALE_MARK'
        ELSE 'OK'
    END AS pricing_status
FROM marked;

-- Execution cost by account, using only trades with a trustworthy mark.
DROP VIEW IF EXISTS v_slippage_by_account;
CREATE VIEW v_slippage_by_account AS
SELECT
    account,
    COUNT(*)                                                    AS trades,
    ROUND(SUM(qty * price), 0)                                  AS notional_usd,
    ROUND(SUM(slippage_usd), 2)                                 AS slippage_usd,
    ROUND(SUM(slippage_usd) / SUM(qty * price) * 10000.0, 2)    AS slippage_bps_wtd,
    ROUND(AVG(mark_age_ms) / 1000.0, 1)                         AS avg_mark_age_s
FROM v_trade_marks
WHERE pricing_status IN ('OK', 'STALE_MARK')
GROUP BY account
ORDER BY slippage_bps_wtd DESC;
