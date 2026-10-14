"""Internal blotter vs. clearing-firm statement reconciliation.

Two-pass match, the way most middle-office recon tools work:

1. **Key match** on execution ID, then field-by-field comparison
   (side, quantity, price within tolerance, settlement date).
2. **Fuzzy match** of whatever is left on (symbol, side, qty, price, time
   window) to catch trades the street re-keyed — these are ID breaks, not
   missing trades, and treating them as two breaks would double-count risk.

Anything still unmatched is MISSING_AT_CLEARING (we booked it, the street
didn't) or UNKNOWN_AT_CLEARING (the street has it, we didn't book it).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Controls

BREAK_TYPES = ["QTY_MISMATCH", "PRICE_MISMATCH", "SIDE_MISMATCH", "SETTLE_DATE_MISMATCH",
               "MISSING_AT_CLEARING", "UNKNOWN_AT_CLEARING", "ID_MISMATCH"]

ROOT_CAUSE_HINT = {
    "QTY_MISMATCH": "Partial fill booked as full, or late correction not sent to clearing",
    "PRICE_MISMATCH": "Average-price vs execution-price booking, or manual amend",
    "SIDE_MISMATCH": "Manual ticket entered with wrong side",
    "SETTLE_DATE_MISMATCH": "Settlement cycle in static data not updated to T+1",
    "MISSING_AT_CLEARING": "Drop-copy gap or booking submitted after clearing cutoff",
    "UNKNOWN_AT_CLEARING": "Trade not booked internally or give-up not claimed",
    "ID_MISMATCH": "Execution ID re-keyed by clearing firm; STP key match failed",
}

BOOK_COLS = ["exec_id", "trade_date", "settle_date", "exec_ts", "symbol", "side",
             "qty", "price", "account"]


def build_blotter(executions: pd.DataFrame, hedges: pd.DataFrame, trade_date) -> pd.DataFrame:
    ex = executions.copy()
    ex["account"] = np.select([ex["flow"] == "RETAIL", ex["flow"] == "ALGO"],
                              ["WHOLESALE_RETAIL", "AGENCY_ALGO"], "OTHER")
    ex["side"] = np.where(ex["client_side"] == 1, "B", "S")
    parts = [ex]
    if hedges is not None and len(hedges):
        h = hedges.copy()
        h["exec_id"] = [f"H{pd.Timestamp(trade_date):%y%m%d}{i:05d}" for i in range(len(h))]
        h["trade_date"] = ex["trade_date"].iat[0]
        h["settle_date"] = ex["settle_date"].iat[0]
        h["account"] = "MM_HEDGE"
        h["side"] = np.where(h["client_side"] == 1, "B", "S")
        parts.append(h)
    return pd.concat([p[BOOK_COLS] for p in parts], ignore_index=True)


def make_clearing_statement(blotter: pd.DataFrame, rng: np.random.Generator, rate: float):
    """Copy of the blotter as the clearing firm reports it, with injected breaks.

    Returns (statement, truth) — truth is only used by tests and the report's
    'detection rate' line; reconcile() never sees it.
    """
    st = blotter.copy()
    n_break = max(1, int(round(len(st) * rate)))
    idx = rng.choice(st.index, size=n_break, replace=False)
    kinds = rng.choice(BREAK_TYPES[:-2] + ["ID_MISMATCH"], size=n_break,
                       p=[0.26, 0.22, 0.06, 0.10, 0.18, 0.18])
    truth, drop = [], []
    for i, k in zip(idx, kinds):
        truth.append((st.at[i, "exec_id"], k))
        if k == "QTY_MISMATCH":
            q = st.at[i, "qty"]
            st.at[i, "qty"] = max(1, q - (100 if q > 100 else max(1, q // 2)))
        elif k == "PRICE_MISMATCH":
            st.at[i, "price"] = round(st.at[i, "price"] + rng.choice([-1, 1]) *
                                      rng.choice([0.01, 0.02, 0.05, 0.10]), 4)
        elif k == "SIDE_MISMATCH":
            st.at[i, "side"] = "S" if st.at[i, "side"] == "B" else "B"
        elif k == "SETTLE_DATE_MISMATCH":
            st.at[i, "settle_date"] = st.at[i, "settle_date"] + pd.offsets.BDay(1)
        elif k == "MISSING_AT_CLEARING":
            drop.append(i)
        elif k == "ID_MISMATCH":
            st.at[i, "exec_id"] = f"CLR{rng.integers(10**8, 10**9)}"
    st = st.drop(index=drop)
    # trades the street has that we never booked
    n_unknown = max(1, int(round(n_break * 0.15)))
    extra = blotter.sample(n_unknown, random_state=int(rng.integers(1 << 31))).copy()
    extra["exec_id"] = [f"CLR{rng.integers(10**8, 10**9)}" for _ in range(n_unknown)]
    extra["qty"] = (rng.integers(1, 30, n_unknown) * 100)
    extra["exec_ts"] = extra["exec_ts"] + pd.to_timedelta(rng.integers(60, 600, n_unknown), "s")
    for e in extra["exec_id"]:
        truth.append((e, "UNKNOWN_AT_CLEARING"))
    st = pd.concat([st, extra], ignore_index=True)
    truth_df = pd.DataFrame(truth, columns=["exec_id", "break_type"])
    return st.sample(frac=1, random_state=int(rng.integers(1 << 31))).reset_index(drop=True), truth_df


def reconcile(blotter: pd.DataFrame, statement: pd.DataFrame, controls: Controls) -> pd.DataFrame:
    m = blotter.merge(statement, on="exec_id", how="outer", suffixes=("_int", "_st"),
                      indicator=True)
    both = m[m["_merge"] == "both"].copy()
    rows = []

    def add(r, kind, impact):
        def pick(col):
            v = r.get(f"{col}_int")
            return v if pd.notna(v) else r.get(f"{col}_st")
        rows.append({
            "trade_date": pick("trade_date"),
            "exec_id": r["exec_id"], "street_ref": r.get("street_ref", r["exec_id"]),
            "symbol": pick("symbol"), "account": pick("account"),
            "break_type": kind,
            "internal_side": r.get("side_int"), "street_side": r.get("side_st"),
            "internal_qty": r.get("qty_int"), "street_qty": r.get("qty_st"),
            "internal_px": r.get("price_int"), "street_px": r.get("price_st"),
            "notional_impact_usd": float(impact),
        })

    for _, r in both.iterrows():
        notional = r["qty_int"] * r["price_int"]
        if r["side_int"] != r["side_st"]:
            add(r, "SIDE_MISMATCH", 2 * notional)
        if r["qty_int"] != r["qty_st"]:
            add(r, "QTY_MISMATCH", abs(r["qty_int"] - r["qty_st"]) * r["price_int"])
        if abs(r["price_int"] - r["price_st"]) > controls.price_match_tol:
            add(r, "PRICE_MISMATCH", abs(r["price_int"] - r["price_st"]) * r["qty_int"])
        if r["settle_date_int"] != r["settle_date_st"]:
            add(r, "SETTLE_DATE_MISMATCH", notional)

    left = m[m["_merge"] == "left_only"].copy()
    right = m[m["_merge"] == "right_only"].copy()
    used_right = set()
    for li, lr in left.iterrows():
        cand = right[(right["symbol_st"] == lr["symbol_int"]) & (right["side_st"] == lr["side_int"])
                     & (right["qty_st"] == lr["qty_int"])
                     & ((right["price_st"] - lr["price_int"]).abs() <= controls.price_match_tol)
                     & ((right["exec_ts_st"] - lr["exec_ts_int"]).abs()
                        <= pd.Timedelta(seconds=controls.recon_time_window_s))
                     & (~right.index.isin(used_right))]
        if len(cand):
            ri = cand.index[0]
            used_right.add(ri)
            r = lr.copy()
            for c in ["side", "qty", "price"]:
                r[f"{c}_st"] = right.at[ri, f"{c}_st"]
            r["street_ref"] = right.at[ri, "exec_id"]
            add(r, "ID_MISMATCH", 0.0)
            left = left.drop(index=li)
    right = right.drop(index=list(used_right))
    for _, r in left.iterrows():
        add(r, "MISSING_AT_CLEARING", r["qty_int"] * r["price_int"])
    for _, r in right.iterrows():
        add(r, "UNKNOWN_AT_CLEARING", r["qty_st"] * r["price_st"])

    br = pd.DataFrame(rows)
    if br.empty:
        return br
    br["root_cause_hint"] = br["break_type"].map(ROOT_CAUSE_HINT)
    br["trade_date"] = pd.to_datetime(br["trade_date"])
    br = br.sort_values(["notional_impact_usd"], ascending=False).reset_index(drop=True)
    return br


def match_rate(blotter: pd.DataFrame, breaks: pd.DataFrame) -> float:
    """Share of internal bookings that reconciled clean on the first pass."""
    if breaks.empty:
        return 100.0
    bad = breaks.loc[breaks["break_type"] != "UNKNOWN_AT_CLEARING", "exec_id"].nunique()
    return (1 - bad / len(blotter)) * 100
