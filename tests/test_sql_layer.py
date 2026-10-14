"""The SQL layer is checked against independent Python implementations."""
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eqops.currency import validate_pair
from eqops.datasets import generate
from eqops.fix_parser import build, parse
from eqops.sql_runner import SQL_DIR, build_database, load_raw


@pytest.fixture(scope="module")
def sql_env(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    generate(raw, seed=2026)
    con, counts, res = build_database(raw, raw / "t.db")
    yield raw, con, res
    con.close()


# ---------------------------------------------------------------- 01 as-of join
def test_asof_join_matches_pandas_merge_asof(sql_env):
    raw, _, res = sql_env
    prices = pd.read_csv(raw / "crypto_prices.csv").sort_values("ts_ms")
    trades = pd.read_csv(raw / "crypto_trades.csv").drop_duplicates("trade_id")
    trades["ts_ms"] = pd.to_datetime(trades["trade_time"]).dt.as_unit("ms").astype("int64")
    exp = pd.merge_asof(trades.sort_values("ts_ms"), prices[["symbol", "ts_ms", "price"]]
                        .rename(columns={"price": "mark_px"}), on="ts_ms", by="symbol",
                        direction="backward")
    got = res["v_trade_marks"].query("pricing_status != 'DUPLICATE_BOOKING'")
    m = got.merge(exp[["trade_id", "mark_px"]], on="trade_id", suffixes=("_sql", "_py"))
    assert len(m) == len(trades)
    both = m.dropna(subset=["mark_px_sql", "mark_px_py"])
    assert len(both) > 1400
    assert np.allclose(both["mark_px_sql"], both["mark_px_py"])
    assert m["mark_px_sql"].isna().equals(m["mark_px_py"].isna())


def test_injected_pricing_problems_found(sql_env):
    *_, res = sql_env
    s = res["v_trade_marks"]["pricing_status"].value_counts()
    assert s["DUPLICATE_BOOKING"] == 4
    assert s["NO_PRICE_FEED"] == 3
    assert s["BEFORE_FIRST_TICK"] == 4
    assert s["PRICE_OUTLIER"] == 3


# ---------------------------------------------------------------- 02 recursive P&L
def _avg_cost(trades):
    """Reference average-cost P&L, one account/symbol, trades as (signed_qty, px)."""
    pos = avg = realized = 0.0
    for q, px in trades:
        if round(pos, 8) != 0 and (pos > 0) != (q > 0):
            closed = min(abs(q), abs(pos))
            realized += closed * (px - avg) * (1 if pos > 0 else -1)
            if abs(q) > abs(pos):
                avg = px
            elif abs(q) == abs(pos):
                avg = 0.0
        else:
            avg = px if round(pos, 8) == 0 else (pos * avg + q * px) / (pos + q)
        pos = round(pos + q, 8)
    return pos, avg, realized


def test_recursive_cte_handles_flip_through_zero(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    con.executescript((SQL_DIR / "00_schema.sql").read_text())
    rows = [("T1", "2026-10-09T10:00:00.000Z", "A", "BTC-USD", "BUY", 10, 100.0),
            ("T2", "2026-10-09T10:01:00.000Z", "A", "BTC-USD", "SELL", 15, 110.0),
            ("T3", "2026-10-09T10:02:00.000Z", "A", "BTC-USD", "BUY", 5, 105.0)]
    con.executemany("INSERT INTO raw_crypto_trades VALUES (?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO raw_crypto_prices VALUES (?,?,?,?)",
                    [("BTC-USD", pd.Timestamp(t).value // 10**6, px, "X") for t, px in
                     [("2026-10-09T09:59:30Z", 100.0), ("2026-10-09T10:00:30Z", 109.0),
                      ("2026-10-09T10:01:30Z", 106.0)]])
    con.executescript((SQL_DIR / "01_trade_pricing_asof.sql").read_text())
    con.executescript((SQL_DIR / "02_position_pnl.sql").read_text())
    led = pd.read_sql("SELECT * FROM v_pnl_ledger ORDER BY trade_seq", con)
    assert led["position"].tolist() == [10, -5, 0]
    assert led["trade_realized_pnl"].tolist() == [0, 100, 25]       # 10*(110-100), 5*(110-105)
    assert led["avg_cost"].tolist() == [100, 110, 0]


def test_sql_pnl_matches_python_reference(sql_env):
    _, con, res = sql_env
    clean = pd.read_sql("SELECT * FROM v_clean_trades ORDER BY trade_time, trade_id", con)
    book = defaultdict(list)
    for r in clean.itertuples():
        book[(r.account, r.symbol)].append((r.signed_qty, r.price))
    eod = res["v_positions_eod"].set_index(["account", "symbol"])
    for key, trades in book.items():
        pos, avg, realized = _avg_cost(trades)
        assert eod.loc[key, "position"] == pytest.approx(pos, abs=1e-6)
        assert eod.loc[key, "realized_pnl"] == pytest.approx(realized, abs=0.02)


# ---------------------------------------------------------------- 03 data quality
def test_sql_currency_checks_agree_with_python(sql_env):
    raw, _, res = sql_env
    fx = pd.read_csv(raw / "fx_trades.csv", dtype={"ccy_pair": str}, keep_default_na=False)
    py_bad = {r.trade_id for r in fx.itertuples() if validate_pair(r.ccy_pair)[0] is None}
    sql_bad = set(res["v_fx_pair_check"].dropna(subset=["pair_error"])["trade_id"])
    assert py_bad == sql_bad and len(py_bad) > 0


def test_dq_rules_catch_injected_fx_problems(sql_env):
    *_, res = sql_env
    rules = set(res["v_dq_issues"]["rule"])
    assert {"INVALID_CCY_PAIR", "NON_POSITIVE_RATE", "VALUE_DATE_BEFORE_TRADE_DATE",
            "DUPLICATE_TRADE_ID", "NON_POSITIVE_NOTIONAL"} <= rules


# ---------------------------------------------------------------- 04 FIX
def test_fix_checksum_and_body_length_roundtrip():
    msg = build([("35", "D"), ("11", "ORD1"), ("55", "AAPL"), ("54", "1"), ("38", "100"), ("40", "1")])
    assert parse(msg).errors == []
    tampered = msg.replace("38=100", "38=900")         # same length, different bytes
    assert "BAD_CHECKSUM" in parse(tampered).errors
    longer = msg.replace("38=100", "38=1000")
    assert "BAD_BODY_LENGTH" in parse(longer).errors


def test_fix_exceptions_find_every_injected_problem(sql_env):
    *_, res = sql_env
    s = res["v_fix_exceptions"].groupby("exception").size()
    assert s["FILL_THROUGH_LIMIT"] == 3
    assert s["OVERFILL"] == 1
    assert s["ORPHAN_EXECUTION"] == 1
    assert s["DUPLICATE_EXEC_ID"] == 2
    assert s["MESSAGE_INTEGRITY"] == 1
    assert s.get("UNACKNOWLEDGED_ORDER", 0) >= 1


def test_duplicate_exec_reports_not_double_counted(sql_env):
    _, con, _ = sql_env
    lc = pd.read_sql("SELECT * FROM v_order_lifecycle", con)
    filled = lc[lc["final_state"] == "FILLED"]
    assert (filled["filled_qty"] == filled["order_qty"]).all()
