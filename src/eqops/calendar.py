"""Minimal NYSE trading calendar (2026) and T+1 settlement helper."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

NYSE_HOLIDAYS_2026 = [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
]
_BDAY = pd.offsets.CustomBusinessDay(holidays=NYSE_HOLIDAYS_2026)


def trading_days_ending(end: str | dt.date, n: int) -> list[dt.date]:
    end_ts = pd.Timestamp(end)
    if not is_trading_day(end_ts):
        end_ts = end_ts - _BDAY
    days = pd.date_range(end=end_ts, periods=n, freq=_BDAY)
    return [d.date() for d in days]


def is_trading_day(d) -> bool:
    ts = pd.Timestamp(d).normalize()
    return bool(np.is_busday(ts.date(), holidays=NYSE_HOLIDAYS_2026))


def add_business_days(d, n: int) -> dt.date:
    return (pd.Timestamp(d) + n * _BDAY).date()


def settle_date(trade_date, cycle: int = 1) -> dt.date:
    """US equities settle T+1 since 28 May 2024."""
    return add_business_days(trade_date, cycle)


def business_days_between(start, end) -> int:
    return int(np.busday_count(pd.Timestamp(start).date(), pd.Timestamp(end).date(),
                               holidays=NYSE_HOLIDAYS_2026))
