-- Ops analyst queries against reports/eqops.db (SQLite).
-- Run:  sqlite3 -header -column reports/eqops.db < sql/daily_ops_review.sql

-- 1. Open break inventory by type and age bucket, with dollars at risk
SELECT break_type,
       SUM(CASE WHEN age_bd <= 1 THEN 1 ELSE 0 END)            AS age_0_1,
       SUM(CASE WHEN age_bd BETWEEN 2 AND 3 THEN 1 ELSE 0 END) AS age_2_3,
       SUM(CASE WHEN age_bd >= 4 THEN 1 ELSE 0 END)            AS age_4_plus,
       COUNT(*)                                                AS open_breaks,
       ROUND(SUM(notional_impact_usd), 0)                      AS usd_at_risk
FROM recon_breaks
WHERE status = 'OPEN'
GROUP BY break_type
ORDER BY usd_at_risk DESC;

-- 2. Escalation list: open breaks older than 3 business days
SELECT break_id, DATE(trade_date) AS trade_date, age_bd, account, symbol, break_type,
       ROUND(notional_impact_usd, 0) AS usd_at_risk, root_cause_hint
FROM recon_breaks
WHERE status = 'OPEN' AND age_bd > 3
ORDER BY age_bd DESC, usd_at_risk DESC;

-- 3. Daily trend: match rate, new breaks, open breaks, desk P&L with running total
SELECT DATE(trade_date)                          AS trade_date,
       ROUND(match_rate_pct, 2)                  AS match_rate_pct,
       new_breaks,
       open_breaks_eod,
       ROUND(desk_pnl_usd, 0)                    AS desk_pnl_usd,
       ROUND(SUM(desk_pnl_usd) OVER (ORDER BY trade_date), 0) AS cum_pnl_usd
FROM daily_kpis
ORDER BY trade_date;

-- 4. Retail price improvement and effective/quoted spread by symbol (report day)
SELECT symbol,
       COUNT(*)                                                     AS orders,
       SUM(qty)                                                     AS shares,
       ROUND(SUM(pi_usd), 2)                                        AS price_improvement_usd,
       ROUND(100.0 * SUM(CASE WHEN price_improvement > 0 THEN qty ELSE 0 END) / SUM(qty), 1)
                                                                    AS pct_shares_improved,
       ROUND(100.0 * SUM(effective_spread * qty) / SUM((ask_rcv - bid_rcv) * qty), 1)
                                                                    AS eq_ratio_pct,
       ROUND(SUM(markout_60s_bps * qty) / SUM(qty), 2)              AS markout_60s_bps
FROM executions
WHERE flow = 'RETAIL'
GROUP BY symbol
ORDER BY price_improvement_usd DESC;

-- 5. Algo liquidity and venue mix (report day)
SELECT venue, liquidity, COUNT(*) AS fills, SUM(qty) AS shares,
       ROUND(100.0 * SUM(qty) / (SELECT SUM(qty) FROM executions WHERE flow = 'ALGO'), 1) AS pct_shares
FROM executions
WHERE flow = 'ALGO'
GROUP BY venue, liquidity
ORDER BY shares DESC;

-- 6. Algo orders furthest from their own benchmark (all days)
SELECT parent_id, DATE(trade_date) AS trade_date, symbol, strategy,
       CASE client_side WHEN 1 THEN 'BUY' ELSE 'SELL' END AS side,
       filled_qty, ROUND(participation_pct, 2) AS pov_pct,
       ROUND(CASE WHEN strategy = 'IS' THEN arrival_slippage_bps ELSE vwap_slippage_bps END, 1)
                                                         AS bench_slippage_bps,
       ROUND(shortfall_usd, 0)                           AS shortfall_usd
FROM parent_orders
ORDER BY ABS(CASE WHEN strategy = 'IS' THEN arrival_slippage_bps ELSE vwap_slippage_bps END) DESC
LIMIT 10;

-- 7. Exceptions queue summary
SELECT severity, control, COUNT(*) AS n
FROM exceptions
GROUP BY severity, control
ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, n DESC;
