-- 02  Positions and P&L per account and symbol, average-cost method, in pure SQL.
--
-- A recursive CTE walks each account/symbol's trades in time order and carries
-- position, average cost and realized P&L forward one trade at a time —
-- including trades that flip a position from long to short. Unrealized P&L is
-- marked at the last price of the day. Results are cross-checked against a
-- plain-Python implementation in tests/test_sql_layer.py.
--
-- export: v_pnl_ledger
-- export: v_positions_eod

DROP VIEW IF EXISTS v_clean_trades;
CREATE VIEW v_clean_trades AS          -- only trades that priced cleanly in step 01
SELECT trade_id, trade_time, account, symbol, side,
       CASE side WHEN 'BUY' THEN qty ELSE -qty END AS signed_qty,
       price
FROM v_trade_marks
WHERE pricing_status IN ('OK', 'STALE_MARK');

DROP VIEW IF EXISTS v_pnl_ledger;
CREATE VIEW v_pnl_ledger AS
WITH RECURSIVE
ordered AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY account, symbol
                                 ORDER BY trade_time, trade_id) AS rn
    FROM v_clean_trades
),
ledger (account, symbol, rn, trade_id, trade_time, signed_qty, price,
        position, avg_cost, realized_pnl, trade_realized) AS (
    SELECT account, symbol, rn, trade_id, trade_time, signed_qty, price,
           signed_qty, price, 0.0, 0.0
    FROM ordered WHERE rn = 1

    UNION ALL

    SELECT o.account, o.symbol, o.rn, o.trade_id, o.trade_time, o.signed_qty, o.price,
           -- new position
           ROUND(l.position + o.signed_qty, 8),
           -- new average cost
           CASE
               WHEN ROUND(l.position, 8) = 0 THEN o.price                         -- opening from flat
               WHEN (l.position > 0) = (o.signed_qty > 0)                          -- adding
                   THEN (l.position * l.avg_cost + o.signed_qty * o.price) / (l.position + o.signed_qty)
               WHEN ABS(o.signed_qty) < ABS(l.position) THEN l.avg_cost           -- partial close
               WHEN ABS(o.signed_qty) = ABS(l.position) THEN 0.0                  -- flat
               ELSE o.price                                                        -- flip: rest opens at trade px
           END,
           -- cumulative realized P&L
           l.realized_pnl +
           CASE WHEN ROUND(l.position, 8) <> 0 AND (l.position > 0) <> (o.signed_qty > 0)
                THEN MIN(ABS(o.signed_qty), ABS(l.position)) * (o.price - l.avg_cost)
                     * (CASE WHEN l.position > 0 THEN 1 ELSE -1 END)
                ELSE 0 END,
           -- realized P&L on this trade alone
           CASE WHEN ROUND(l.position, 8) <> 0 AND (l.position > 0) <> (o.signed_qty > 0)
                THEN MIN(ABS(o.signed_qty), ABS(l.position)) * (o.price - l.avg_cost)
                     * (CASE WHEN l.position > 0 THEN 1 ELSE -1 END)
                ELSE 0 END
    FROM ledger l
    JOIN ordered o
      ON o.account = l.account AND o.symbol = l.symbol AND o.rn = l.rn + 1
)
SELECT account, symbol, rn AS trade_seq, trade_id, trade_time, signed_qty, price,
       position, ROUND(avg_cost, 4) AS avg_cost,
       ROUND(trade_realized, 2) AS trade_realized_pnl,
       ROUND(realized_pnl, 2)   AS cum_realized_pnl
FROM ledger;

DROP VIEW IF EXISTS v_positions_eod;
CREATE VIEW v_positions_eod AS
WITH last_row AS (
    SELECT l.*, ROW_NUMBER() OVER (PARTITION BY account, symbol ORDER BY trade_seq DESC) AS r
    FROM v_pnl_ledger l
),
close_px AS (
    SELECT symbol, price AS close_px
    FROM (SELECT symbol, price,
                 ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts_ms DESC) AS r
          FROM raw_crypto_prices)
    WHERE r = 1
)
SELECT lr.account, lr.symbol,
       lr.trade_seq                                              AS trades,
       lr.position,
       lr.avg_cost,
       c.close_px,
       lr.cum_realized_pnl                                       AS realized_pnl,
       ROUND(lr.position * (c.close_px - lr.avg_cost), 2)        AS unrealized_pnl,
       ROUND(lr.cum_realized_pnl + lr.position * (c.close_px - lr.avg_cost), 2) AS total_pnl,
       ROUND(ABS(lr.position) * c.close_px, 0)                   AS gross_exposure_usd
FROM last_row lr
JOIN close_px c USING (symbol)
WHERE lr.r = 1
ORDER BY total_pnl DESC;
