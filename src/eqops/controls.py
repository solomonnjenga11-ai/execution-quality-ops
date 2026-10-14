"""Daily operational control checks -> one exceptions queue for the ops team."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Controls

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _exc(df, control, severity, ref_col, detail_fn, metric_col):
    if df is None or df.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "control": control, "severity": severity,
        "symbol": df["symbol"].to_numpy(), "ref": df[ref_col].astype(str).to_numpy(),
        "metric": df[metric_col].astype(float).round(4).to_numpy(),
        "detail": [detail_fn(r) for r in df.itertuples()],
    })


def run_controls(trade_date, fm: pd.DataFrame, parents_tca: pd.DataFrame,
                 breaches: pd.DataFrame, breaks: pd.DataFrame, c: Controls) -> pd.DataFrame:
    out = []
    tt = fm[(fm["flow"] == "RETAIL") & (fm["through_nbbo"] > c.trade_through_tol)]
    out.append(_exc(tt, "TRADE_THROUGH", "CRITICAL", "exec_id",
                    lambda r: f"Filled {r.qty} @ {r.price:.4f}, {r.through_nbbo:.4f} through "
                              f"NBBO {r.bid_exe:.2f}/{r.ask_exe:.2f}; review for Rule 611 / client "
                              f"remediation", "through_nbbo"))

    t = fm["exec_ts"].dt.time
    oos = fm[(t < pd.Timestamp("09:30").time()) | (t > pd.Timestamp("16:00").time())]
    out.append(_exc(oos, "OUT_OF_SESSION_TIMESTAMP", "HIGH", "exec_id",
                    lambda r: f"Booked at {r.exec_ts:%H:%M:%S.%f} but executed in RTH; "
                              f"timestamp feeds CAT reporting — correct before submission",
                    "qty"))

    slow = fm[fm["latency_ms"] > c.max_latency_ms]
    out.append(_exc(slow, "EXECUTION_LATENCY", "MEDIUM", "exec_id",
                    lambda r: f"Receipt-to-execution {r.latency_ms:,.0f} ms "
                              f"(limit {c.max_latency_ms:.0f} ms); check router/feed health",
                    "latency_ms"))

    # each algo is judged against the benchmark it was trying to hit
    is_ord = parents_tca["strategy"] == "IS"
    sl = parents_tca[(is_ord & (parents_tca["arrival_slippage_bps"].abs() > c.is_slippage_alert_bps))
                     | (~is_ord & (parents_tca["vwap_slippage_bps"].abs() > c.vwap_slippage_alert_bps))].copy()
    sl["bench_slip"] = np.where(sl["strategy"] == "IS", sl["arrival_slippage_bps"], sl["vwap_slippage_bps"])
    out.append(_exc(sl, "ALGO_BENCHMARK_SLIPPAGE", "MEDIUM", "parent_id",
                    lambda r: f"{r.strategy} {'buy' if r.client_side == 1 else 'sell'} "
                              f"{r.filled_qty:,.0f} sh at {r.participation_pct:.1f}% POV: "
                              f"{r.bench_slip:+.1f} bps vs {'arrival' if r.strategy == 'IS' else 'interval VWAP'}"
                              f" (market moved {r.market_move_bps:+.1f} bps)",
                    "bench_slip"))

    if breaches is not None and len(breaches):
        b = breaches.copy()
        b["ref"] = b["symbol"] + "@" + b["start_s"].astype(str)
        out.append(_exc(b, "POSITION_LIMIT", "HIGH", "ref",
                        lambda r: f"Desk position above hard limit for {r.duration_s}s "
                                  f"before hedge completed", "duration_s"))

    if breaks is not None and len(breaks):
        big = breaks[breaks["notional_impact_usd"] > c.break_notional_alert_usd]
        out.append(_exc(big, "LARGE_RECON_BREAK", "HIGH", "exec_id",
                        lambda r: f"{r.break_type} on {r.account}: "
                                  f"${r.notional_impact_usd:,.0f} at risk", "notional_impact_usd"))

    exc = pd.concat([o for o in out if len(o)], ignore_index=True) if any(len(o) for o in out) \
        else pd.DataFrame(columns=["control", "severity", "symbol", "ref", "metric", "detail"])
    exc.insert(0, "trade_date", pd.Timestamp(trade_date))
    exc["_s"] = exc["severity"].map(SEVERITY_ORDER)
    return exc.sort_values(["_s", "control"]).drop(columns="_s").reset_index(drop=True)
