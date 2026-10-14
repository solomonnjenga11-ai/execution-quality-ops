-- 04  FIX order lifecycle: match every NewOrderSingle (35=D) to its
--     ExecutionReports (35=8) and flag anything an ops desk would chase.
--
-- Messages are parsed from the raw log in Python (src/eqops/fix_parser.py,
-- which also validates CheckSum and BodyLength); all matching happens here.
--
-- export: v_order_lifecycle
-- export: v_fix_exceptions
-- export: v_fix_exception_summary

DROP VIEW IF EXISTS v_execs_dedup;
CREATE VIEW v_execs_dedup AS            -- a resent ExecutionReport must not be counted twice
SELECT * FROM (
    SELECT m.*, ROW_NUMBER() OVER (PARTITION BY exec_id ORDER BY line_no) AS copy_no
    FROM fix_messages m
    WHERE msg_type = '8'
) WHERE copy_no = 1;

DROP VIEW IF EXISTS v_order_lifecycle;
CREATE VIEW v_order_lifecycle AS
WITH o AS (
    SELECT cl_ord_id, symbol, side, order_qty, limit_px, sending_time AS sent_at
    FROM fix_messages WHERE msg_type = 'D'
),
e AS (
    SELECT cl_ord_id,
           MAX(exec_type = '0')                                   AS acked,
           MAX(exec_type = '8')                                   AS rejected,
           MAX(exec_type = '4')                                   AS canceled,
           SUM(CASE WHEN exec_type = 'F' THEN 1 ELSE 0 END)       AS n_fills,
           SUM(CASE WHEN exec_type = 'F' THEN last_qty ELSE 0 END) AS filled_qty,
           SUM(CASE WHEN exec_type = 'F' THEN last_qty * last_px END) AS filled_notional,
           MIN(CASE WHEN exec_type = '0' THEN sending_time END)  AS acked_at,
           MAX(sending_time)                                      AS last_update
    FROM v_execs_dedup
    GROUP BY cl_ord_id
)
SELECT o.cl_ord_id, o.symbol, o.side, o.order_qty, o.limit_px,
       COALESCE(e.filled_qty, 0)                                    AS filled_qty,
       COALESCE(e.n_fills, 0)                                       AS n_fills,
       ROUND(e.filled_notional / NULLIF(e.filled_qty, 0), 4)        AS avg_fill_px,
       CASE
           WHEN e.cl_ord_id IS NULL                  THEN 'NO_RESPONSE'
           WHEN e.rejected = 1                       THEN 'REJECTED'
           WHEN e.filled_qty > o.order_qty           THEN 'OVERFILLED'
           WHEN e.filled_qty = o.order_qty           THEN 'FILLED'
           WHEN e.canceled = 1                       THEN 'PARTIAL_THEN_CANCELED'
           WHEN e.filled_qty > 0                     THEN 'PARTIALLY_FILLED_OPEN'
           ELSE 'ACKED_OPEN'
       END                                                          AS final_state,
       o.sent_at, e.acked_at, e.last_update
FROM o LEFT JOIN e USING (cl_ord_id);

DROP VIEW IF EXISTS v_fix_exceptions;
CREATE VIEW v_fix_exceptions AS
-- 1. order sent, nothing came back
SELECT 'UNACKNOWLEDGED_ORDER' AS exception, 'HIGH' AS severity, cl_ord_id, NULL AS exec_id,
       symbol, side || ' ' || CAST(order_qty AS INT) || ' @ ' || limit_px || ' sent ' || sent_at AS detail
FROM v_order_lifecycle WHERE final_state = 'NO_RESPONSE'
UNION ALL
-- 2. fill for an order we have no record of sending
SELECT 'ORPHAN_EXECUTION', 'CRITICAL', e.cl_ord_id, e.exec_id, e.symbol,
       e.side || ' ' || CAST(e.last_qty AS INT) || ' @ ' || e.last_px || ' — unknown ClOrdID'
FROM v_execs_dedup e
WHERE e.cl_ord_id NOT IN (SELECT cl_ord_id FROM fix_messages WHERE msg_type = 'D')
UNION ALL
-- 3. the same ExecID received more than once
SELECT 'DUPLICATE_EXEC_ID', 'MEDIUM', cl_ord_id, exec_id, symbol,
       COUNT(*) || ' copies received; counted once'
FROM fix_messages WHERE msg_type = '8'
GROUP BY exec_id HAVING COUNT(*) > 1
UNION ALL
-- 4. more shares filled than ordered
SELECT 'OVERFILL', 'CRITICAL', cl_ord_id, NULL, symbol,
       'Filled ' || CAST(filled_qty AS INT) || ' vs ordered ' || CAST(order_qty AS INT)
FROM v_order_lifecycle WHERE final_state = 'OVERFILLED'
UNION ALL
-- 5. fill priced worse than the client's limit
SELECT 'FILL_THROUGH_LIMIT', 'CRITICAL', e.cl_ord_id, e.exec_id, e.symbol,
       e.side || ' limit ' || o.limit_px || ' filled ' || CAST(e.last_qty AS INT) || ' @ ' || e.last_px
FROM v_execs_dedup e
JOIN fix_messages o ON o.msg_type = 'D' AND o.cl_ord_id = e.cl_ord_id
WHERE e.exec_type = 'F'
  AND ((e.side = 'BUY' AND e.last_px > o.limit_px) OR (e.side = 'SELL' AND e.last_px < o.limit_px))
UNION ALL
-- 6. broker's CumQty disagrees with the sum of fills received so far
SELECT 'CUMQTY_MISMATCH', 'HIGH', cl_ord_id, exec_id, symbol,
       'Reported CumQty ' || CAST(cum_qty AS INT) || ', sum of fills ' || CAST(running AS INT)
FROM (
    SELECT e.*, SUM(last_qty) OVER (PARTITION BY cl_ord_id ORDER BY line_no) AS running
    FROM v_execs_dedup e WHERE exec_type = 'F'
) WHERE cum_qty <> running
UNION ALL
-- 7. message failed CheckSum / BodyLength / required-tag validation
SELECT 'MESSAGE_INTEGRITY', 'HIGH', cl_ord_id, exec_id, symbol,
       'Line ' || line_no || ': ' || parse_errors
FROM fix_messages WHERE parse_errors IS NOT NULL;

DROP VIEW IF EXISTS v_fix_exception_summary;
CREATE VIEW v_fix_exception_summary AS
SELECT severity, exception, COUNT(*) AS n
FROM v_fix_exceptions
GROUP BY severity, exception
ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 ELSE 2 END, n DESC;
