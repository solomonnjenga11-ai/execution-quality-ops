-- Raw landing tables. Loaded as-is from data/raw/ by src/eqops/sql_runner.py.
-- Nothing is cleaned on the way in: the analysis files decide what is valid.

DROP TABLE IF EXISTS raw_crypto_prices;
CREATE TABLE raw_crypto_prices (
    symbol   TEXT    NOT NULL,
    ts_ms    INTEGER NOT NULL,          -- epoch milliseconds, UTC
    price    REAL    NOT NULL,
    venue    TEXT
);

DROP TABLE IF EXISTS raw_crypto_trades;
CREATE TABLE raw_crypto_trades (
    trade_id   TEXT,
    trade_time TEXT,                    -- ISO-8601 string, e.g. 2026-10-09T14:03:11.257Z
    account    TEXT,
    symbol     TEXT,
    side       TEXT,
    qty        REAL,
    price      REAL
);

DROP TABLE IF EXISTS raw_fx_trades;
CREATE TABLE raw_fx_trades (
    trade_id      TEXT,
    trade_date    TEXT,
    value_date    TEXT,
    book          TEXT,
    ccy_pair      TEXT,                 -- free text as booked: 'EUR/USD', 'EURUSD', 'eurusd', ...
    side          TEXT,
    notional_base REAL,
    rate          REAL,
    counterparty  TEXT
);

DROP TABLE IF EXISTS fix_messages;
CREATE TABLE fix_messages (             -- one row per FIX message, parsed in Python
    line_no       INTEGER PRIMARY KEY,
    msg_type      TEXT,                 -- D = NewOrderSingle, 8 = ExecutionReport
    seq_num       INTEGER,
    sending_time  TEXT,
    transact_time TEXT,
    cl_ord_id     TEXT,
    order_id      TEXT,
    exec_id       TEXT,
    exec_type     TEXT,                 -- 0 new, F trade, 4 canceled, 8 rejected
    ord_status    TEXT,
    symbol        TEXT,
    side          TEXT,
    order_qty     REAL,
    ord_type      TEXT,
    limit_px      REAL,
    last_qty      REAL,
    last_px       REAL,
    cum_qty       REAL,
    leaves_qty    REAL,
    avg_px        REAL,
    text          TEXT,
    parse_errors  TEXT                  -- e.g. BAD_CHECKSUM; NULL when clean
);

DROP TABLE IF EXISTS ref_currencies;
CREATE TABLE ref_currencies (
    ccy       TEXT PRIMARY KEY,
    tradable  INTEGER NOT NULL          -- 0 for codes like XXX (no currency) or metals
);

CREATE INDEX ix_prices_sym_ts ON raw_crypto_prices (symbol, ts_ms);
CREATE INDEX ix_fix_clordid   ON fix_messages (cl_ord_id);
