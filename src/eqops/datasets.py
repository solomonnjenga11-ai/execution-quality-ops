"""Generates the raw input files in data/raw/ used by the SQL layer and practice set.

The formats deliberately differ the way real feeds do:

* crypto_prices.csv  — price ticks stamped in **epoch milliseconds**
* crypto_trades.csv  — trade blotter stamped as **ISO-8601 UTC strings**
* fx_trades.csv      — FX bookings with free-text currency pairs
* fix_log.txt        — raw FIX 4.4 messages, '|' standing in for SOH

Every file is synthetic. Known problems are injected on purpose and listed in
data/raw/README.md, so the checks have something real to find.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

from .fix_parser import build

DAY = pd.Timestamp("2026-10-09", tz="UTC")
CRYPTO = {"BTC-USD": (121_500.0, 0.45), "ETH-USD": (4_380.0, 0.60), "SOL-USD": (224.0, 0.80)}
ACCOUNTS = ["MM_CRYPTO_01", "MM_CRYPTO_02", "CLIENT_ALPHA", "CLIENT_BRAVO", "CLIENT_DELTA"]


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def make_crypto(rng: np.random.Generator):
    price_rows, marks = [], {}
    day_ms = 86_400_000
    start_ms = int(DAY.timestamp() * 1000)
    for sym, (px0, vol) in CRYPTO.items():
        n = int(rng.integers(5000, 8000))
        t = np.sort(rng.choice(np.arange(60_000, day_ms), size=n, replace=False)) + start_ms
        dt_years = np.diff(np.r_[start_ms, t]) / (365 * day_ms)
        px = px0 * np.exp(np.cumsum(rng.standard_normal(n) * vol * np.sqrt(dt_years)))
        dec = 2 if px0 > 1000 else 3
        px = np.round(px, dec)
        marks[sym] = (t, px)
        price_rows += [(sym, int(a), float(b)) for a, b in zip(t, px)]
    prices = pd.DataFrame(price_rows, columns=["symbol", "ts_ms", "price"])
    prices["venue"] = rng.choice(["COINBASE", "KRAKEN", "BITSTAMP"], len(prices))
    prices = prices.sort_values(["ts_ms", "symbol"]).reset_index(drop=True)

    trades = []
    for i in range(1500):
        sym = rng.choice(list(CRYPTO))
        t_arr, p_arr = marks[sym]
        tms = int(rng.integers(t_arr[0], t_arr[-1]))
        mark = p_arr[np.searchsorted(t_arr, tms, side="right") - 1]
        side = rng.choice(["BUY", "SELL"])
        slip = rng.normal(0.0002, 0.0004) * (1 if side == "BUY" else -1)
        qty = {"BTC-USD": rng.uniform(0.01, 2.0), "ETH-USD": rng.uniform(0.1, 40),
               "SOL-USD": rng.uniform(5, 900)}[sym]
        trades.append([f"CT{100000 + i}", rng.choice(ACCOUNTS), sym, side, round(qty, 4),
                       round(mark * (1 + slip), 2), tms])
    df = pd.DataFrame(trades, columns=["trade_id", "account", "symbol", "side", "qty", "price", "ts_ms"])
    first = {s: marks[s][0][0] for s in CRYPTO}
    # injected issues
    for k, idx in enumerate(rng.choice(len(df), 4, replace=False)):     # before first tick
        df.loc[idx, "ts_ms"] = first[df.loc[idx, "symbol"]] - int(rng.integers(1_000, 50_000))
    for idx in rng.choice(len(df), 3, replace=False):                   # no price feed
        df.loc[idx, ["symbol", "price"]] = ["DOGE-USD", 0.2741]
    for idx in rng.choice(len(df), 3, replace=False):                   # fat finger
        df.loc[idx, "price"] = round(df.loc[idx, "price"] * rng.choice([0.9, 1.1]), 2)
    dups = df.sample(4, random_state=1).copy()                          # duplicate bookings
    df = pd.concat([df, dups], ignore_index=True).sort_values("ts_ms").reset_index(drop=True)
    df["trade_time"] = [_iso(pd.Timestamp(x, unit="ms", tz="UTC")) for x in df["ts_ms"]]
    return prices, df.drop(columns="ts_ms")[["trade_id", "trade_time", "account", "symbol",
                                              "side", "qty", "price"]]


_PAIRS = {"EUR/USD": 1.0985, "USD/JPY": 151.42, "GBP/USD": 1.3071, "AUD/USD": 0.6644,
          "USD/CAD": 1.3752, "USD/CHF": 0.8031, "EUR/GBP": 0.8404, "USD/MXN": 18.214,
          "USD/ZAR": 17.402, "USD/KES": 129.10}


def make_fx(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    bdays = pd.bdate_range("2026-10-05", "2026-10-09")
    for i in range(1200):
        pair = rng.choice(list(_PAIRS))
        td = bdays[rng.integers(len(bdays))]
        lag = 1 if pair == "USD/CAD" else 2
        vd = td + pd.offsets.BDay(lag)
        rate = _PAIRS[pair] * (1 + rng.normal(0, 0.003))
        rows.append([f"FX{200000 + i}", td.date(), vd.date(), rng.choice(["FX_FLOW", "FX_HEDGE", "CLIENT_ECHO"]),
                     pair if rng.random() > 0.3 else pair.replace("/", ""), rng.choice(["BUY", "SELL"]),
                     int(rng.choice([1, 2, 3, 5, 10, 25]) * 1_000_000), round(rate, 5 if rate < 10 else 3),
                     rng.choice(["JPM", "CITI", "BARX", "UBS", "GS", "SCB"])])
    df = pd.DataFrame(rows, columns=["trade_id", "trade_date", "value_date", "book", "ccy_pair",
                                     "side", "notional_base", "rate", "counterparty"])
    bad = rng.choice(len(df), 26, replace=False)
    fixes = [("ccy_pair", "EUR/USS"), ("ccy_pair", "eurusd"), ("ccy_pair", "USD/USD"),
             ("ccy_pair", "XXX/USD"), ("ccy_pair", "EU/USD"), ("ccy_pair", ""),
             ("ccy_pair", "gbp/usd"), ("ccy_pair", "USD-JPY"), ("ccy_pair", "USDKSH"),
             ("rate", -1.0985), ("rate", 0.0), ("notional_base", 0)]
    for j, idx in enumerate(bad):
        col, val = fixes[j % len(fixes)]
        df.loc[idx, col] = val
    for idx in rng.choice(len(df), 3, replace=False):                   # value date before trade date
        df.loc[idx, "value_date"] = (pd.Timestamp(df.loc[idx, "trade_date"]) - pd.offsets.BDay(1)).date()
    df = pd.concat([df, df.sample(3, random_state=2)], ignore_index=True)
    return df.sample(frac=1, random_state=3).reset_index(drop=True)


def make_fix_log(rng: np.random.Generator) -> list[str]:
    syms = {"AAPL": 228.0, "MSFT": 431.0, "NVDA": 182.0, "JPM": 297.0}
    lines, seq = [], 1
    t0 = pd.Timestamp("2026-10-09 13:30:00")

    def stamp(t):
        return t.strftime("%Y%m%d-%H:%M:%S.") + f"{t.microsecond // 1000:03d}"

    def send(fields, t, sender="CLIENT", target="BROKER"):
        nonlocal seq
        hdr = [("35", fields[0][1]), ("49", sender), ("56", target), ("34", str(seq)), ("52", stamp(t))]
        seq += 1
        lines.append(build(hdr + fields[1:]))

    exec_n = 0
    for k in range(220):
        sym = rng.choice(list(syms))
        side = rng.choice(["1", "2"])
        qty = int(rng.choice([100, 200, 300, 500, 1000, 2000]))
        lim = round(syms[sym] * (1 + rng.normal(0, 0.002)), 2)
        cl = f"ORD{50000 + k}"
        oid = f"BRK{900000 + k}"
        t = t0 + pd.Timedelta(seconds=float(k * 95 + rng.integers(0, 60)))
        send([("35", "D"), ("11", cl), ("55", sym), ("54", side), ("38", str(qty)), ("40", "2"),
              ("44", f"{lim:.2f}"), ("60", stamp(t))], t)
        fate = rng.random()
        if fate < 0.03:                      # never acknowledged
            continue
        t += pd.Timedelta(milliseconds=int(rng.integers(1, 40)))
        if fate < 0.06:
            send([("35", "8"), ("11", cl), ("37", oid), ("17", f"EX{exec_n}"), ("150", "8"),
                  ("39", "8"), ("55", sym), ("54", side), ("38", str(qty)), ("14", "0"),
                  ("151", "0"), ("58", "Price outside collar")], t, "BROKER", "CLIENT")
            exec_n += 1
            continue
        send([("35", "8"), ("11", cl), ("37", oid), ("17", f"EX{exec_n}"), ("150", "0"), ("39", "0"),
              ("55", sym), ("54", side), ("38", str(qty)), ("14", "0"), ("151", str(qty))], t, "BROKER", "CLIENT")
        exec_n += 1
        n_fills = int(rng.integers(1, 4))
        if qty >= 300 and n_fills > 1:
            cuts = np.sort(rng.choice(np.arange(1, qty // 100), min(n_fills - 1, qty // 100 - 1),
                                      replace=False)) * 100
            fill_q = np.diff(np.r_[0, cuts, qty]).astype(int)
        else:
            fill_q = np.array([qty])
        cum, notional = 0, 0.0
        partial_cancel = fate > 0.94
        if partial_cancel and len(fill_q) > 1:
            fill_q = fill_q[:-1]
        for fq in fill_q:
            t += pd.Timedelta(milliseconds=int(rng.integers(50, 4000)))
            px = round(lim - (0.01 if side == "1" else -0.01) * int(rng.integers(0, 4)), 2)
            cum += int(fq)
            notional += fq * px
            leaves = qty - cum
            send([("35", "8"), ("11", cl), ("37", oid), ("17", f"EX{exec_n}"), ("150", "F"),
                  ("39", "2" if leaves == 0 else "1"), ("55", sym), ("54", side), ("38", str(qty)),
                  ("32", str(int(fq))), ("31", f"{px:.2f}"), ("14", str(cum)), ("151", str(leaves)),
                  ("6", f"{notional / cum:.4f}"), ("60", stamp(t))], t, "BROKER", "CLIENT")
            exec_n += 1
        if partial_cancel and cum < qty:
            t += pd.Timedelta(seconds=2)
            send([("35", "8"), ("11", cl), ("37", oid), ("17", f"EX{exec_n}"), ("150", "4"), ("39", "4"),
                  ("55", sym), ("54", side), ("38", str(qty)), ("14", str(cum)), ("151", "0")], t,
                 "BROKER", "CLIENT")
            exec_n += 1

    # ---- injected problems -------------------------------------------------
    fills = [i for i, l in enumerate(lines) if "|150=F|" in l]
    # 1. fill priced through the limit
    for i in rng.choice(fills, 3, replace=False):
        f = dict(p.split("=", 1) for p in lines[i].strip("|").split("|"))
        order = next(l for l in lines if "|35=D|" in l and f"|11={f['11']}|" in l)
        lim = float(dict(p.split("=", 1) for p in order.strip("|").split("|"))["44"])
        bad_px = lim + (0.05 if f["54"] == "1" else -0.05)
        new = [(t, (f"{bad_px:.2f}" if t == "31" else v)) for t, v in
               (p.split("=", 1) for p in lines[i].strip("|").split("|")) if t not in ("8", "9", "10")]
        lines[i] = build(new)
    # 2. duplicate execution report (same ExecID resent)
    for i in rng.choice(fills, 2, replace=False):
        lines.insert(i + 1, lines[i])
    # 3. orphan fill for an order the client never sent
    send([("35", "8"), ("11", "ORD99999"), ("37", "BRK999999"), ("17", "EX99999"), ("150", "F"),
          ("39", "2"), ("55", "NVDA"), ("54", "1"), ("38", "500"), ("32", "500"), ("31", "182.10"),
          ("14", "500"), ("151", "0"), ("6", "182.1000")], t0 + pd.Timedelta(hours=5), "BROKER", "CLIENT")
    # 4. overfill: an extra fill pushes CumQty above OrderQty
    fills = [i for i, l in enumerate(lines) if "|150=F|" in l and "|11=ORD99999|" not in l]
    i = fills[int(rng.integers(len(fills) // 2, len(fills)))]
    f = dict(p.split("=", 1) for p in lines[i].strip("|").split("|"))
    extra = [(t, v) for t, v in (p.split("=", 1) for p in lines[i].strip("|").split("|"))
             if t not in ("8", "9", "10")]
    extra = [(t, {"17": "EX88888", "32": "100", "14": str(int(f["14"]) + 100),
                  "151": "0", "39": "2"}.get(t, v)) for t, v in extra]
    lines.insert(i + 1, build(extra))
    # 5. corrupted checksum (bit flip in transit)
    j = int(rng.integers(10, len(lines) - 10))
    lines[j] = lines[j][:-4] + ("000|" if not lines[j].endswith("000|") else "001|")
    return lines


def generate(out_dir: Path, seed: int = 2026) -> dict[str, Path]:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    prices, trades = make_crypto(rng)
    fx = make_fx(rng)
    fix = make_fix_log(rng)
    paths = {
        "crypto_prices": out_dir / "crypto_prices.csv",
        "crypto_trades": out_dir / "crypto_trades.csv",
        "fx_trades": out_dir / "fx_trades.csv",
        "fix_log": out_dir / "fix_log.txt",
    }
    prices.to_csv(paths["crypto_prices"], index=False)
    trades.to_csv(paths["crypto_trades"], index=False, quoting=csv.QUOTE_MINIMAL)
    fx.to_csv(paths["fx_trades"], index=False)
    paths["fix_log"].write_text("\n".join(fix) + "\n", encoding="ascii")
    return paths
