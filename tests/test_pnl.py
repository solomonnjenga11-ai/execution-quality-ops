import datetime as dt

import numpy as np
import pytest

from eqops import tca
from eqops.config import UNIVERSE, SimParams
from eqops.pnl import run_desk
from eqops.simulate import simulate_day


@pytest.fixture(scope="module")
def desk():
    p = SimParams(retail_orders_per_day=800, algo_parents_per_day=2)
    rng = np.random.default_rng(3)
    day = simulate_day(dt.date(2026, 10, 9), rng, p, {s: m["px"] for s, m in UNIVERSE.items()})
    fm = tca.fill_metrics(day.executions, day.quotes)
    pnl, hedges, breaches, paths = run_desk(day.trade_date, fm, day.quotes, p.limits, rng)
    return fm, pnl, hedges, paths, p


def test_attribution_reconciles_to_cash_pnl(desk):
    """capture + hedge cost + inventory = cash P&L of a book that ends flat."""
    fm, pnl, hedges, _, p = desk
    retail = fm[fm["flow"] == "RETAIL"]
    for row in pnl.itertuples():
        r = retail[retail["symbol"] == row.symbol]
        h = hedges[hedges["symbol"] == row.symbol]
        cash = float((r["client_side"] * r["qty"] * r["price"]).sum()          # desk sells to buyers
                     - (h["client_side"] * h["qty"] * h["price"]).sum())
        gross = row.spread_capture + row.hedge_cost + row.inventory_pnl
        assert gross == pytest.approx(cash, abs=0.5), row.symbol


def test_desk_is_flat_at_close(desk):
    *_, paths, _ = desk
    for pos in paths.values():
        assert pos[-1] == 0


def test_spread_capture_positive_on_aggregate(desk):
    _, pnl, *_ = desk
    assert pnl["spread_capture"].sum() > 0
    assert (pnl["fees"] <= 0).all()
