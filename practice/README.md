# Practice set: trading operations SQL & Python

Twelve timed-test-style problems built on this repo's raw data. They cover the
patterns that come up in trading-operations coding assessments: joining feeds
with mismatched timestamps, P&L, input validation, FIX parsing and order/fill
matching.

**Setup:** run `python -m eqops sql` once. It creates
`reports/sql/trade_analysis.db` with the raw tables `raw_crypto_trades`,
`raw_crypto_prices`, `raw_fx_trades`, `fix_messages` and `ref_currencies`.

**Rules for yourself:** 15 minutes per problem, no looking at
`sql/analysis/` or `practice/solutions/` until you have an answer. Then check
it, and redo any you missed two days later.

Open the database with `sqlite3 reports/sql/trade_analysis.db`, DB Browser for
SQLite, or `pd.read_sql(query, sqlite3.connect(path))`.

---

## SQL

**S1. Notional by symbol.** From `raw_crypto_trades`, return each symbol's
trade count and total notional (`qty * price`), counting each `trade_id` only
once. Sort by notional, largest first.

**S2. As-of join.** Trades are stamped `trade_time` (ISO text); prices are
stamped `ts_ms` (epoch milliseconds). For every trade return `trade_id`,
`symbol`, `price` and `mark_px`: the most recent price for the same symbol at
or before the trade. Trades with no earlier price must still appear, with
`mark_px` NULL.
*Hint:* `(julianday(t) - 2440587.5) * 86400000` turns ISO text into epoch ms.

**S3. Worst executions per account.** Using your S2 result, compute slippage in
bps (positive = cost: buys above mark, sells below mark). Return the 3 worst
trades per account. Use a window function, not `LIMIT`.

**S4. Running position.** For account `CLIENT_ALPHA`, show every trade in time
order with a running signed position per symbol (buys +, sells −).

**S5. Hourly VWAP.** For each symbol and UTC hour, return trade count, volume
and VWAP (`SUM(qty*price)/SUM(qty)`). Exclude duplicate trade IDs.

**S6. Unfinished orders.** In `fix_messages`, `msg_type = 'D'` rows are orders
and `msg_type = '8'` with `exec_type = 'F'` are fills. Return every order whose
filled quantity differs from its `order_qty` and which was never canceled
(`exec_type = '4'`) or rejected (`exec_type = '8'`). Watch out: some fills were
sent twice with the same `exec_id`.

---

## Python (standard library only)

**P1. Currency pair validator.** Write `is_valid_pair(s: str) -> bool`. Accept
`"EUR/USD"`, `"EURUSD"` and `"EUR-USD"`; reject lowercase, wrong length, codes
not in a given set, and same-currency pairs. Test it against the `ccy_pair`
column of `data/raw/fx_trades.csv`.

**P2. Parse FIX.** Write `parse_fix(msg: str) -> dict` for messages like
`8=FIX.4.4|9=126|35=D|...|10=036|`, returning `{tag: value}`. Then write
`fix_checksum(msg) -> str`: sum of the ASCII codes of everything before
`10=`, with `|` counted as SOH (`\x01`), mod 256, zero-padded to 3 digits.
Verify it against the first line of `data/raw/fix_log.txt`.

**P3. FIFO P&L.** Given `trades = [("BUY", 100, 10.0), ("BUY", 50, 11.0),
("SELL", 120, 12.0), ...]`, return realized P&L using first-in-first-out lots.
(The repo uses average cost; FIFO gives different numbers. Know both.)

**P4. Order/fill matching.** Given `orders = [(cl_ord_id, qty)]` and
`fills = [(exec_id, cl_ord_id, qty)]`, return three lists: overfilled orders,
underfilled orders, and fills whose `cl_ord_id` is unknown. Ignore repeated
`exec_id`s.

**P5. First-seen duplicates.** Given a list of trade IDs, return the IDs that
appear more than once, in the order their *second* occurrence appears. No
`collections.Counter`.

**P6. Net position by symbol.** Given `[(symbol, side, qty)]`, return a dict
`{symbol: net_qty}` and the symbol with the largest absolute exposure.

---

Solutions: `practice/solutions/sql_solutions.sql` and
`practice/solutions/python_solutions.py` (runnable; `pytest` checks them).
