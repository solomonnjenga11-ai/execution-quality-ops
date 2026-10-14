"""Market-making desk P&L attribution.

For internalised retail flow the desk is the counterparty, so it accumulates
inventory. It hedges on lit venues (paying the spread plus a take fee) when a
position gets too large, with a realistic reaction delay, and flattens at the
close. Daily mark-to-market P&L (valued at the mid) decomposes exactly into:

    spread capture   = sum over client fills of desk_qty * (mid_at_fill - price)
    hedge cost       = sum over hedge fills  of desk_qty * (mid_at_fill - price)
    inventory P&L    = sum over seconds of position_t * (mid_{t+1} - mid_t)
    fees             = - take fee * hedge shares

    total = capture + hedge cost + inventory + fees

The identity is checked in tests/test_pnl.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SESSION_SECONDS, DeskLimits
from .simulate import ATS_VENUE, LIT_VENUES, session_ts


def run_desk(trade_date, fm: pd.DataFrame, quotes: dict[str, pd.DataFrame],
             limits: DeskLimits, rng: np.random.Generator):
    """Returns (pnl_by_symbol, hedge_fills, position_breaches, position_path)."""
    retail = fm[fm["flow"] == "RETAIL"]
    pnl_rows, hedges, breaches, paths = [], [], [], {}
    for sym, q in quotes.items():
        mid = q["mid"].to_numpy()
        bid, ask = q["bid"].to_numpy(), q["ask"].to_numpy()
        r = retail[retail["symbol"] == sym]
        desk_qty_at = np.zeros(SESSION_SECONDS)
        np.add.at(desk_qty_at, r["exe_s"].to_numpy(), -(r["client_side"] * r["qty"]).to_numpy())

        pos = np.zeros(SESSION_SECONDS)
        cur, hedge_at, in_breach = 0.0, None, None
        sym_hedges = []
        for t in range(SESSION_SECONDS):
            cur += desk_qty_at[t]
            usd = abs(cur) * mid[t]
            # hedge decision with a reaction delay (risk check -> order -> fill)
            if hedge_at is None and usd > limits.hedge_trigger_usd:
                hedge_at = t + int(rng.integers(2, 16))
            if hedge_at == t:
                hedge_at = None
            if hedge_at is None and usd > limits.hedge_trigger_usd and \
                    (len(sym_hedges) == 0 or sym_hedges[-1][0] < t):
                target = np.sign(cur) * limits.hedge_target_usd / mid[t]
                hq = int(round((cur - target) / 100.0) * 100)   # shares to sell (+) / buy (-)
                if hq != 0:
                    if rng.random() < limits.midpoint_hedge_share:
                        px, how = mid[t], "MID"
                    else:
                        px, how = (bid[t] if hq > 0 else ask[t]), "REMOVE"
                    sym_hedges.append((t, -hq, px, how))
                    cur -= hq
            if t == SESSION_SECONDS - 1 and cur != 0:            # flatten in the closing cross
                sym_hedges.append((t, -cur, mid[t], "AUCTION"))
                cur = 0.0
            pos[t] = cur
            usd = abs(cur) * mid[t]
            if usd > limits.max_abs_position_usd and in_breach is None:
                in_breach = t
            elif usd <= limits.max_abs_position_usd and in_breach is not None:
                breaches.append({"symbol": sym, "start_s": in_breach, "end_s": t,
                                 "duration_s": t - in_breach})
                in_breach = None

        # pos[t] is the position held over (t, t+1]
        inventory = float(np.sum(pos[:-1] * np.diff(mid)))
        capture = float(np.sum(-(r["client_side"] * r["qty"]) * (r["mid_exe"] - r["price"])))
        h = pd.DataFrame(sym_hedges, columns=["exe_s", "desk_qty", "price", "how"])
        h_mid = mid[h["exe_s"].to_numpy()] if len(h) else np.array([])
        hedge_cost = float(np.sum(h["desk_qty"] * (h_mid - h["price"]))) if len(h) else 0.0
        lit = h[h["how"] != "AUCTION"] if len(h) else h
        fees = -float(np.sum(np.where(lit["how"] == "MID", limits.ats_fee_per_share,
                                      limits.take_fee_per_share) * lit["desk_qty"].abs())) if len(lit) else 0.0
        pnl_rows.append({"symbol": sym, "spread_capture": capture, "hedge_cost": hedge_cost,
                         "inventory_pnl": inventory, "fees": fees,
                         "client_shares": int(r["qty"].sum()),
                         "max_abs_position_usd": float(np.max(np.abs(pos) * mid)),
                         "n_hedges": len(lit)})
        paths[sym] = pos
        if len(h):
            h["symbol"] = sym
            hedges.append(h)

    pnl = pd.DataFrame(pnl_rows)
    pnl["total_pnl"] = pnl[["spread_capture", "hedge_cost", "inventory_pnl", "fees"]].sum(axis=1)
    pnl["capture_per_100sh"] = pnl["spread_capture"] / pnl["client_shares"] * 100
    hedge_df = _hedges_to_executions(trade_date, pd.concat(hedges, ignore_index=True)
                                     if hedges else pd.DataFrame(), rng)
    return pnl, hedge_df, pd.DataFrame(breaches, columns=["symbol", "start_s", "end_s",
                                                          "duration_s"]), paths


def _hedges_to_executions(trade_date, h: pd.DataFrame, rng) -> pd.DataFrame:
    if h.empty:
        return h
    out = pd.DataFrame({
        "symbol": h["symbol"],
        "client_side": np.sign(h["desk_qty"]).astype(int),   # firm's own side for hedges
        "qty": h["desk_qty"].abs().astype(int),
        "price": h["price"].round(4),
        "exe_s": h["exe_s"],
        "flow": "HEDGE",
        "capacity": "PRINCIPAL",
        "venue": np.select([h["how"] == "AUCTION", h["how"] == "MID"],
                           ["CLOSING_CROSS", ATS_VENUE], rng.choice(LIT_VENUES, size=len(h))),
        "liquidity": h["how"].to_numpy(),
    })
    out["exec_ts"] = session_ts(trade_date, out["exe_s"]) + pd.to_timedelta(
        rng.integers(0, 1000, len(out)), unit="ms")
    return out.reset_index(drop=True)
