import datetime as dt

import numpy as np

from eqops.calendar import settle_date, trading_days_ending
from eqops.config import TICK, UNIVERSE, SimParams
from eqops.simulate import simulate_day


def test_t_plus_one_skips_weekends_and_holidays():
    assert settle_date(dt.date(2026, 10, 9)) == dt.date(2026, 10, 12)   # Fri -> Mon (NYSE open)
    assert settle_date(dt.date(2026, 7, 2)) == dt.date(2026, 7, 6)      # Jul 3 holiday
    assert settle_date(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)  # Thanksgiving


def test_trading_days_ending_rolls_back_from_weekend():
    days = trading_days_ending("2026-10-11", 3)                        # a Sunday
    assert days == [dt.date(2026, 10, 7), dt.date(2026, 10, 8), dt.date(2026, 10, 9)]


def test_simulated_nbbo_is_valid_and_on_tick():
    p = SimParams(retail_orders_per_day=200, algo_parents_per_day=3)
    day = simulate_day(dt.date(2026, 10, 9), np.random.default_rng(1), p,
                       {s: m["px"] for s, m in UNIVERSE.items()})
    for q in day.quotes.values():
        assert (q["ask"] > q["bid"]).all()
        assert np.allclose(np.round(q["bid"] / TICK), q["bid"] / TICK)
    ex = day.executions
    assert ex["exec_id"].is_unique
    assert (ex["qty"] > 0).all()
    assert (ex["settle_date"] == np.datetime64("2026-10-12")).all()
