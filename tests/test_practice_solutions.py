import importlib.util
import sqlite3
from pathlib import Path

import pytest

from eqops.datasets import generate
from eqops.sql_runner import build_database

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sol", ROOT / "practice/solutions/python_solutions.py")
sol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sol)


def test_pair_validator():
    assert sol.is_valid_pair("EUR/USD") and sol.is_valid_pair("USDJPY") and sol.is_valid_pair("USD-KES")
    for bad in ["eurusd", "EUR/USS", "USD/USD", "EU/USD", "", None, "EUR/USDX", "EUR_USD"]:
        assert not sol.is_valid_pair(bad), bad


def test_fix_checksum_matches_log():
    line = (ROOT / "data/raw/fix_log.txt").read_text().splitlines()[0]
    assert sol.fix_checksum(line) == sol.parse_fix(line)["10"]


def test_fifo():
    # 100 @10 and 20 of the 50 @11 closed at 12 -> 100*2 + 20*1
    assert sol.fifo_realized_pnl([("BUY", 100, 10.0), ("BUY", 50, 11.0), ("SELL", 120, 12.0)]) == 220
    # flip: long 10 @100, sell 15 @110 (+100), buy 5 @105 closes the short (+25)
    assert sol.fifo_realized_pnl([("BUY", 10, 100), ("SELL", 15, 110), ("BUY", 5, 105)]) == 125


def test_match_orders():
    over, under, unknown = sol.match_orders(
        [("A", 100), ("B", 200), ("C", 50)],
        [("e1", "A", 100), ("e1", "A", 100), ("e2", "B", 50), ("e3", "Z", 10), ("e4", "C", 60)])
    assert (over, under, unknown) == (["C"], ["B"], ["e3"])


def test_duplicates_and_positions():
    assert sol.duplicates_in_order(["a", "b", "a", "c", "b", "a"]) == ["a", "b"]
    pos, big = sol.net_positions([("X", "BUY", 10), ("Y", "SELL", 30), ("X", "SELL", 4)])
    assert pos == {"X": 6, "Y": -30} and big == "Y"


def test_sql_solutions_run(tmp_path):
    generate(tmp_path, seed=2026)
    con, _, _ = build_database(tmp_path, tmp_path / "p.db")
    text = (ROOT / "practice/solutions/sql_solutions.sql").read_text()
    stmts = [s for s in text.split(";") if "SELECT" in s.upper()]
    assert len(stmts) == 6
    results = [con.execute(s).fetchall() for s in stmts]
    assert all(results)
    # S2 must agree with the repo's as-of view
    s2 = {r[0]: r[3] for r in results[1]}
    view = dict(con.execute("SELECT trade_id, mark_px FROM v_trade_marks").fetchall())
    assert all(s2[k] == pytest.approx(v) if v is not None else s2[k] is None for k, v in view.items())
    con.close()
