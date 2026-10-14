"""Transaction-cost and execution-quality analytics.

Sign convention: ``client_side`` is +1 for a client buy, -1 for a client sell.
Costs are positive when the client paid more than the benchmark. Markouts are
from the *desk's* (liquidity provider's) point of view: positive = the desk made
money on the trade after h seconds, negative = adverse selection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MARKOUT_HORIZONS_S, SESSION_SECONDS


def _lookup(quotes: dict[str, pd.DataFrame], sym: pd.Series, sec: np.ndarray, col: str):
    out = np.empty(len(sym))
    sec = np.clip(sec, 0, SESSION_SECONDS - 1)
    for s in sym.unique():
        m = (sym == s).to_numpy()
        out[m] = quotes[s][col].to_numpy()[sec[m]]
    return out


def fill_metrics(ex: pd.DataFrame, quotes: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-execution NBBO context, spreads, price improvement and markouts."""
    df = ex.copy()
    rs, es = df["rcv_s"].to_numpy(), df["exe_s"].to_numpy()
    for when, sec in (("rcv", rs), ("exe", es)):
        df[f"bid_{when}"] = _lookup(quotes, df["symbol"], sec, "bid")
        df[f"ask_{when}"] = _lookup(quotes, df["symbol"], sec, "ask")
    df["mid_rcv"] = (df["bid_rcv"] + df["ask_rcv"]) / 2
    df["mid_exe"] = (df["bid_exe"] + df["ask_exe"]) / 2
    side, px = df["client_side"], df["price"]

    df["quoted_spread"] = df["ask_rcv"] - df["bid_rcv"]
    df["effective_spread"] = 2 * side * (px - df["mid_rcv"])
    df["eff_spread_bps"] = df["effective_spread"] / df["mid_rcv"] * 1e4
    far_rcv = np.where(side == 1, df["ask_rcv"], df["bid_rcv"])
    df["price_improvement"] = (side * (far_rcv - px)).round(6)
    df["pi_usd"] = df["price_improvement"].clip(lower=0) * df["qty"]
    far_exe = np.where(side == 1, df["ask_exe"], df["bid_exe"])
    df["through_nbbo"] = (side * (px - far_exe)).round(6)   # >0 = traded through the NBBO

    for h in MARKOUT_HORIZONS_S:
        mid_h = _lookup(quotes, df["symbol"], es + h, "mid")
        df[f"markout_{h}s_bps"] = side * (px - mid_h) / df["mid_exe"] * 1e4
    df["realized_spread_60s"] = 2 * side * (px - _lookup(quotes, df["symbol"], es + 60, "mid"))
    return df


SIZE_BINS = [0, 99, 499, 1999, 4999, np.inf]
SIZE_LABELS = ["<100 (odd lot)", "100–499", "500–1,999", "2,000–4,999", "5,000+"]


def rule605_style(fm: pd.DataFrame) -> pd.DataFrame:
    """Rule 605-style execution quality summary for internalised retail flow.

    Follows the spirit of SEC Rule 605 (size buckets, effective/quoted spread,
    price improvement, speed, realized spread). It is a teaching approximation,
    not a regulatory filing.
    """
    r = fm[fm["flow"] == "RETAIL"].copy()
    r["size_bucket"] = pd.cut(r["qty"], SIZE_BINS, labels=SIZE_LABELS)
    w = r["qty"]
    r["_eff_w"] = r["effective_spread"] * w
    r["_q_w"] = r["quoted_spread"] * w
    r["_rs_w"] = r["realized_spread_60s"] * w
    r["_improved_sh"] = np.where(r["price_improvement"] > 0, w, 0)
    r["_at_sh"] = np.where(r["price_improvement"] == 0, w, 0)
    r["_out_sh"] = np.where(r["price_improvement"] < 0, w, 0)
    g = r.groupby(["order_type", "size_bucket"], observed=True)
    out = pd.DataFrame({
        "orders": g.size(),
        "shares": g["qty"].sum(),
        "avg_eff_spread": g["_eff_w"].sum() / g["qty"].sum(),
        "avg_quoted_spread": g["_q_w"].sum() / g["qty"].sum(),
        "pct_shares_improved": g["_improved_sh"].sum() / g["qty"].sum() * 100,
        "pct_shares_at_quote": g["_at_sh"].sum() / g["qty"].sum() * 100,
        "pct_shares_outside": g["_out_sh"].sum() / g["qty"].sum() * 100,
        "price_improvement_usd": g["pi_usd"].sum(),
        "median_speed_ms": g["latency_ms"].median(),
        "avg_realized_spread_60s": g["_rs_w"].sum() / g["qty"].sum(),
    })
    out["eq_ratio_pct"] = out["avg_eff_spread"] / out["avg_quoted_spread"] * 100
    return out.reset_index()


def parent_tca(parents: pd.DataFrame, fm: pd.DataFrame,
               quotes: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Arrival (implementation shortfall) and interval-VWAP slippage per parent order."""
    a = fm[fm["flow"] == "ALGO"]
    agg = a.groupby("parent_id").apply(
        lambda g: pd.Series({
            "filled_qty": g["qty"].sum(),
            "avg_px": (g["price"] * g["qty"]).sum() / g["qty"].sum(),
            "n_fills": len(g),
            "pct_add": (g.loc[g["liquidity"] == "ADD", "qty"].sum()) / g["qty"].sum() * 100,
            "pct_mid": (g.loc[g["liquidity"] == "MID", "qty"].sum()) / g["qty"].sum() * 100,
            "pct_remove": (g.loc[g["liquidity"] == "REMOVE", "qty"].sum()) / g["qty"].sum() * 100,
        }), include_groups=False)
    p = parents.merge(agg, left_on="parent_id", right_index=True, how="left")
    vwaps, mkt_vol, end_mid = [], [], []
    for r in p.itertuples():
        w = quotes[r.symbol].iloc[r.start_s:r.end_s + 1]
        vwaps.append((w["mid"] * w["mkt_volume"]).sum() / w["mkt_volume"].sum())
        mkt_vol.append(w["mkt_volume"].sum())
        end_mid.append(w["mid"].iat[-1])
    p["interval_vwap"] = vwaps
    p["participation_pct"] = p["filled_qty"] / np.array(mkt_vol) * 100
    s = p["client_side"]
    p["arrival_slippage_bps"] = s * (p["avg_px"] - p["arrival_mid"]) / p["arrival_mid"] * 1e4
    p["vwap_slippage_bps"] = s * (p["avg_px"] - p["interval_vwap"]) / p["interval_vwap"] * 1e4
    p["market_move_bps"] = s * (np.array(end_mid) - p["arrival_mid"]) / p["arrival_mid"] * 1e4
    p["shortfall_usd"] = s * (p["avg_px"] - p["arrival_mid"]) * p["filled_qty"]
    p["fill_rate_pct"] = p["filled_qty"] / p["qty"] * 100
    return p


def markout_curve(fm: pd.DataFrame, by: str = "symbol") -> pd.DataFrame:
    """Share-weighted desk markouts (bps) by horizon for internalised retail flow."""
    r = fm[fm["flow"] == "RETAIL"]
    cols = [f"markout_{h}s_bps" for h in MARKOUT_HORIZONS_S]
    rows = []
    for key, g in r.groupby(by):
        w = g["qty"]
        rows.append({by: key, **{h: float(np.average(g[c], weights=w))
                                 for h, c in zip(MARKOUT_HORIZONS_S, cols)}})
    return pd.DataFrame(rows).set_index(by)
