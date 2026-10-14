import numpy as np
import pandas as pd
import pytest

from eqops import tca
from eqops.config import SESSION_SECONDS


def _flat_quotes(bid=100.00, ask=100.02):
    return {"TEST": pd.DataFrame({"bid": np.full(SESSION_SECONDS, bid),
                                  "ask": np.full(SESSION_SECONDS, ask),
                                  "mid": np.full(SESSION_SECONDS, (bid + ask) / 2),
                                  "mkt_volume": np.full(SESSION_SECONDS, 100)})}


def _fill(side, price, qty=100, flow="RETAIL"):
    return pd.DataFrame([{"symbol": "TEST", "client_side": side, "qty": qty, "price": price,
                          "rcv_s": 100, "exe_s": 100, "flow": flow, "latency_ms": 3.0,
                          "order_type": "MARKET", "parent_id": None, "liquidity": "INTERNAL"}])


def test_buy_with_half_penny_improvement():
    fm = tca.fill_metrics(_fill(+1, 100.015), _flat_quotes())
    r = fm.iloc[0]
    assert r["effective_spread"] == pytest.approx(0.01)        # 2 * (100.015 - 100.01)
    assert r["price_improvement"] == pytest.approx(0.005)      # 100.02 - 100.015
    assert r["pi_usd"] == pytest.approx(0.5)
    assert r["through_nbbo"] <= 0
    assert r["markout_60s_bps"] == pytest.approx(0.005 / 100.01 * 1e4)


def test_sell_through_the_bid_is_flagged():
    fm = tca.fill_metrics(_fill(-1, 99.99), _flat_quotes())
    r = fm.iloc[0]
    assert r["price_improvement"] == pytest.approx(-0.01)
    assert r["through_nbbo"] == pytest.approx(0.01)
    assert r["effective_spread"] == pytest.approx(0.04)


def test_rule605_share_buckets_sum_to_100(small_run):
    r = small_run.rule605
    total = r["pct_shares_improved"] + r["pct_shares_at_quote"] + r["pct_shares_outside"]
    assert np.allclose(total, 100)
    assert (r["eq_ratio_pct"] > 0).all()


def test_parent_tca_zero_slippage_when_filled_at_arrival():
    q = _flat_quotes()
    parents = pd.DataFrame([{"parent_id": "P1", "symbol": "TEST", "client_side": 1, "qty": 300,
                             "strategy": "VWAP", "start_s": 0, "end_s": 600, "arrival_mid": 100.01}])
    fills = pd.concat([_fill(1, 100.01, 100, "ALGO")] * 3, ignore_index=True)
    fills["parent_id"] = "P1"
    fills["liquidity"] = "MID"
    p = tca.parent_tca(parents, tca.fill_metrics(fills, q), q).iloc[0]
    assert p["arrival_slippage_bps"] == pytest.approx(0)
    assert p["vwap_slippage_bps"] == pytest.approx(0)
    assert p["fill_rate_pct"] == pytest.approx(100)
    assert p["pct_mid"] == pytest.approx(100)
