-- Solutions to practice/README.md (SQLite). One statement per problem.

-- S1. Notional by symbol, each trade_id once
WITH t AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY trade_time) AS rn
    FROM raw_crypto_trades
)
SELECT symbol, COUNT(*) AS trades, ROUND(SUM(qty * price), 2) AS notional
FROM t WHERE rn = 1
GROUP BY symbol
ORDER BY notional DESC;

-- S2. As-of join
WITH t AS (
    SELECT trade_id, symbol, price,
           CAST(ROUND((julianday(trade_time) - 2440587.5) * 86400000.0) AS INTEGER) AS ts_ms
    FROM raw_crypto_trades
)
SELECT t.trade_id, t.symbol, t.price,
       (SELECT p.price FROM raw_crypto_prices p
         WHERE p.symbol = t.symbol AND p.ts_ms <= t.ts_ms
         ORDER BY p.ts_ms DESC LIMIT 1) AS mark_px
FROM t;

-- S3. Three worst executions per account
WITH t AS (
    SELECT trade_id, account, symbol, side, price,
           CAST(ROUND((julianday(trade_time) - 2440587.5) * 86400000.0) AS INTEGER) AS ts_ms
    FROM raw_crypto_trades
),
m AS (
    SELECT t.*,
           (SELECT p.price FROM raw_crypto_prices p
             WHERE p.symbol = t.symbol AND p.ts_ms <= t.ts_ms
             ORDER BY p.ts_ms DESC LIMIT 1) AS mark_px
    FROM t
),
s AS (
    SELECT *, (CASE side WHEN 'BUY' THEN 1 ELSE -1 END) * (price - mark_px) / mark_px * 10000 AS slip_bps
    FROM m WHERE mark_px IS NOT NULL
)
SELECT account, trade_id, symbol, side, price, mark_px, ROUND(slip_bps, 2) AS slip_bps
FROM (SELECT s.*, ROW_NUMBER() OVER (PARTITION BY account ORDER BY slip_bps DESC) AS r FROM s)
WHERE r <= 3
ORDER BY account, slip_bps DESC;

-- S4. Running position for CLIENT_ALPHA
SELECT trade_time, symbol, side, qty,
       SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END)
           OVER (PARTITION BY symbol ORDER BY trade_time, trade_id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_position
FROM raw_crypto_trades
WHERE account = 'CLIENT_ALPHA'
ORDER BY trade_time;

-- S5. Hourly VWAP
WITH t AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY trade_time) AS rn
    FROM raw_crypto_trades
)
SELECT symbol, strftime('%Y-%m-%d %H:00', trade_time) AS hour_utc,
       COUNT(*) AS trades, ROUND(SUM(qty), 4) AS volume,
       ROUND(SUM(qty * price) / SUM(qty), 4) AS vwap
FROM t WHERE rn = 1
GROUP BY symbol, hour_utc
ORDER BY symbol, hour_utc;

-- S6. Orders not fully filled and never canceled or rejected
WITH fills AS (
    SELECT cl_ord_id, SUM(last_qty) AS filled
    FROM (SELECT DISTINCT exec_id, cl_ord_id, last_qty
          FROM fix_messages WHERE msg_type = '8' AND exec_type = 'F')
    GROUP BY cl_ord_id
),
closed AS (
    SELECT DISTINCT cl_ord_id FROM fix_messages
    WHERE msg_type = '8' AND exec_type IN ('4', '8')
)
SELECT o.cl_ord_id, o.symbol, o.side, o.order_qty, COALESCE(f.filled, 0) AS filled
FROM fix_messages o
LEFT JOIN fills f ON f.cl_ord_id = o.cl_ord_id
WHERE o.msg_type = 'D'
  AND COALESCE(f.filled, 0) <> o.order_qty
  AND o.cl_ord_id NOT IN (SELECT cl_ord_id FROM closed)
ORDER BY o.cl_ord_id;
