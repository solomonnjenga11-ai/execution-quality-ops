"""Synthetic market and order-flow generator.

Nothing here is real market data. The generator exists so the analytics can be
run end-to-end and reproduced from a seed. It models the pieces that matter for
execution-quality and operations work:

* a 1-second NBBO per symbol (random-walk mid, intraday U-shaped volatility,
  spreads that widen at the open and on occasional liquidity gaps);
* U-shaped market volume (used for VWAP benchmarks);
* retail marketable orders internalised by a principal desk with sub-penny
  price improvement, a small informed component (so markouts show real
  adverse selection), latency outliers and a few injected execution errors;
* institutional agency parent orders (VWAP / TWAP / IS) sliced into child
  fills across lit venues and a midpoint ATS.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .calendar import settle_date
from .config import SESSION_SECONDS, TICK, UNIVERSE, SimParams

LIT_VENUES = ["NYSE", "NASDAQ", "ARCA", "CBOE_BZX", "IEX", "MEMX"]
ATS_VENUE = "MIDPOINT_ATS"


@dataclass
class MarketDay:
    trade_date: dt.date
    quotes: dict[str, pd.DataFrame]   # symbol -> 1s NBBO (index = second of session)
    executions: pd.DataFrame          # client executions (retail principal + algo agency)
    parents: pd.DataFrame             # algo parent orders


# ----------------------------------------------------------------------------
# Market
# ----------------------------------------------------------------------------
def _u_shape(n: int = SESSION_SECONDS) -> np.ndarray:
    """Intraday activity profile: high at open, trough at lunch, rise into close."""
    x = np.linspace(0, 1, n)
    prof = 1.0 + 2.2 * np.exp(-x / 0.06) + 1.4 * np.exp(-(1 - x) / 0.05)
    return prof / prof.mean()


def session_ts(trade_date: dt.date, seconds) -> pd.DatetimeIndex:
    base = pd.Timestamp(f"{trade_date} 09:30:00")
    return base + pd.to_timedelta(np.asarray(seconds, dtype="float64"), unit="s")


def simulate_quotes(trade_date: dt.date, rng: np.random.Generator,
                    open_px: dict[str, float]) -> dict[str, pd.DataFrame]:
    prof = _u_shape()
    out: dict[str, pd.DataFrame] = {}
    for sym, meta in UNIVERSE.items():
        sigma_s = meta["vol"] / np.sqrt(252 * SESSION_SECONDS)
        rets = rng.standard_t(df=5, size=SESSION_SECONDS) * np.sqrt(3 / 5)
        rets *= sigma_s * np.sqrt(prof)
        theo = open_px[sym] * np.exp(np.cumsum(rets))

        spread = np.full(SESSION_SECONDS, meta["spread_ticks"], dtype=float)
        spread[:600] += rng.integers(1, 4)                  # wide at the open
        spread[-120:] += 1                                  # into the close
        gaps = rng.random(SESSION_SECONDS) < 0.0015         # liquidity gaps
        for g in np.flatnonzero(gaps):
            spread[g:g + rng.integers(3, 30)] += rng.integers(1, 4)

        bid = np.round((theo - spread * TICK / 2) / TICK) * TICK
        ask = np.round(bid + spread * TICK, 2)
        bid = np.round(bid, 2)
        vol = meta["adv"] / SESSION_SECONDS * prof * rng.lognormal(0, 0.6, SESSION_SECONDS)
        out[sym] = pd.DataFrame({
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "mkt_volume": np.round(vol).astype(np.int64),
        })
    return out


# ----------------------------------------------------------------------------
# Retail flow (principal / internalised)
# ----------------------------------------------------------------------------
_SIZE_BUCKETS = [((1, 99), 0.36), ((100, 499), 0.40), ((500, 1999), 0.17),
                 ((2000, 4999), 0.05), ((5000, 9999), 0.02)]


def _draw_sizes(rng, n):
    idx = rng.choice(len(_SIZE_BUCKETS), size=n, p=[p for _, p in _SIZE_BUCKETS])
    lo = np.array([_SIZE_BUCKETS[i][0][0] for i in idx])
    hi = np.array([_SIZE_BUCKETS[i][0][1] for i in idx])
    q = rng.integers(lo, hi + 1)
    round_lot = (q >= 100) & (rng.random(n) < 0.7)
    q[round_lot] = (q[round_lot] // 100) * 100
    return q


def simulate_retail(trade_date, quotes, rng, p: SimParams, seq_start: int) -> pd.DataFrame:
    syms = list(UNIVERSE)
    weights = np.array([np.sqrt(UNIVERSE[s]["adv"]) for s in syms])
    n = p.retail_orders_per_day
    sym_idx = rng.choice(len(syms), size=n, p=weights / weights.sum())
    prof = _u_shape()
    arr_s = np.sort(rng.choice(SESSION_SECONDS - 400, size=n, p=prof[:-400] / prof[:-400].sum()))
    arr_ms = rng.integers(0, 1000, size=n)
    qty = _draw_sizes(rng, n)
    order_type = np.where(rng.random(n) < 0.72, "MARKET", "MARKETABLE_LIMIT")
    latency = rng.lognormal(np.log(3.5), 0.7, n)
    slow = rng.random(n) < 0.004
    latency[slow] = rng.uniform(550, 2500, slow.sum())

    rows = []
    for i in range(n):
        sym = syms[sym_idx[i]]
        q = quotes[sym]
        s_rcv = int(arr_s[i])
        s_exe = min(s_rcv + int((arr_ms[i] + latency[i]) // 1000), SESSION_SECONDS - 1)
        # informed component: larger orders are more likely to know the next minute
        p_inf = 0.03 + 0.07 * (qty[i] >= 2000)
        fut = q["mid"].iat[min(s_exe + 60, SESSION_SECONDS - 1)] - q["mid"].iat[s_exe]
        if rng.random() < p_inf and fut != 0:
            side = 1 if fut > 0 else -1
        else:
            side = 1 if rng.random() < 0.51 else -1
        bid, ask = q["bid"].iat[s_exe], q["ask"].iat[s_exe]
        half = (ask - bid) / 2
        p_improve = 0.84 if qty[i] < 2000 else 0.55
        if rng.random() < p_improve:
            pi = max(0.0001, round(rng.uniform(0.08, 0.55) * half, 4))
        else:
            pi = 0.0
        px = ask - pi if side == 1 else bid + pi
        err = rng.random() < p.trade_through_rate
        if err:  # injected execution error: printed through the NBBO
            px = px + side * rng.choice([0.01, 0.02, 0.05])
        rows.append((sym, side, int(qty[i]), round(px, 4), s_rcv, int(arr_ms[i]),
                     s_exe, float(latency[i]), order_type[i]))

    df = pd.DataFrame(rows, columns=["symbol", "client_side", "qty", "price", "rcv_s",
                                     "rcv_ms", "exe_s", "latency_ms", "order_type"])
    df["flow"] = "RETAIL"
    df["capacity"] = "PRINCIPAL"
    df["venue"] = "INTERNAL"
    df["liquidity"] = "INTERNAL"
    df["parent_id"] = pd.NA
    df["strategy"] = pd.NA
    df["order_id"] = [f"R{trade_date:%y%m%d}{seq_start + i:06d}" for i in range(n)]
    return df


# ----------------------------------------------------------------------------
# Algo flow (agency)
# ----------------------------------------------------------------------------
def simulate_algo(trade_date, quotes, rng, p: SimParams):
    syms = list(UNIVERSE)
    parents, fills = [], []
    for k in range(p.algo_parents_per_day):
        sym = syms[rng.integers(len(syms))]
        adv = UNIVERSE[sym]["adv"]
        side = 1 if rng.random() < 0.5 else -1
        qty = int(max(500, round(adv * rng.uniform(0.0003, 0.006), -2)))
        start = int(rng.integers(0, SESSION_SECONDS - 3600))
        dur = int(min(rng.integers(1800, 9000), SESSION_SECONDS - start - 1))
        strat = rng.choice(["VWAP", "TWAP", "IS"], p=[0.5, 0.25, 0.25])
        q = quotes[sym]
        window = q.iloc[start:start + dur]
        if strat == "IS":   # front-loaded
            w = np.exp(-np.arange(dur) / (dur / 3))
        elif strat == "TWAP":
            w = np.ones(dur)
        else:
            w = window["mkt_volume"].to_numpy(dtype=float)
        n_child = int(rng.integers(12, 40))
        cuts = np.sort(rng.choice(np.arange(1, dur), size=n_child - 1, replace=False))
        seg_w = np.add.reduceat(w, np.r_[0, cuts])
        child_q = np.floor(qty * seg_w / seg_w.sum() / 100) * 100
        child_q[-1] += qty - child_q.sum()
        child_t = start + np.r_[0, cuts] + rng.integers(0, 30, size=n_child)
        child_t = np.clip(child_t, start, start + dur)
        pid = f"P{trade_date:%y%m%d}{k:04d}"
        for cq, ct in zip(child_q, child_t):
            if cq <= 0:
                continue
            bid, ask = q["bid"].iat[ct], q["ask"].iat[ct]
            r = rng.random()
            if r < 0.40:
                px, liq, ven = (bid if side == 1 else ask), "ADD", rng.choice(LIT_VENUES)
            elif r < 0.58:
                px, liq, ven = (bid + ask) / 2, "MID", ATS_VENUE
            else:
                px, liq, ven = (ask if side == 1 else bid), "REMOVE", rng.choice(LIT_VENUES)
            fills.append((sym, side, int(cq), round(px, 4), int(ct), int(rng.integers(0, 1000)),
                          int(ct), float(rng.lognormal(np.log(1.2), 0.5)), "LIMIT",
                          "ALGO", "AGENCY", ven, liq, pid, strat))
        parents.append({"parent_id": pid, "symbol": sym, "client_side": side, "qty": qty,
                        "strategy": strat, "start_s": start, "end_s": start + dur,
                        "arrival_mid": q["mid"].iat[start]})
    f = pd.DataFrame(fills, columns=["symbol", "client_side", "qty", "price", "rcv_s", "rcv_ms",
                                     "exe_s", "latency_ms", "order_type", "flow", "capacity",
                                     "venue", "liquidity", "parent_id", "strategy"])
    f["order_id"] = [f"C{trade_date:%y%m%d}{i:06d}" for i in range(len(f))]
    return pd.DataFrame(parents), f


# ----------------------------------------------------------------------------
def simulate_day(trade_date: dt.date, rng: np.random.Generator, p: SimParams,
                 open_px: dict[str, float]) -> MarketDay:
    quotes = simulate_quotes(trade_date, rng, open_px)
    retail = simulate_retail(trade_date, quotes, rng, p, 0)
    parents, algo = simulate_algo(trade_date, quotes, rng, p)
    ex = pd.concat([retail, algo], ignore_index=True)
    ex = ex.sort_values(["exe_s", "rcv_ms"], kind="stable").reset_index(drop=True)

    ex["trade_date"] = pd.Timestamp(trade_date)
    ex["exec_ts"] = session_ts(trade_date, ex["exe_s"]) + pd.to_timedelta(
        (ex["rcv_ms"] + ex["latency_ms"]) % 1000, unit="ms")
    ex["receipt_ts"] = session_ts(trade_date, ex["rcv_s"]) + pd.to_timedelta(ex["rcv_ms"], unit="ms")
    # a couple of bookings carry a bad (out-of-session) timestamp — a classic ops issue
    bad = rng.choice(ex.index[ex["flow"] == "RETAIL"], size=p.late_timestamp_count, replace=False)
    ex.loc[bad, "exec_ts"] = ex.loc[bad, "exec_ts"] + pd.Timedelta(hours=7)
    ex["exec_id"] = [f"{trade_date:%y%m%d}{i:07d}" for i in range(len(ex))]
    ex["settle_date"] = pd.Timestamp(settle_date(trade_date))
    ex["notional"] = ex["qty"] * ex["price"]
    return MarketDay(trade_date, quotes, ex, parents)
