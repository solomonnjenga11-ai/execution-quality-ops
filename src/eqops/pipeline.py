"""End-to-end daily run: simulate -> TCA -> desk P&L -> recon -> controls -> store."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import tca
from .calendar import add_business_days, business_days_between, trading_days_ending
from .config import UNIVERSE, SimParams
from .controls import SEVERITY_ORDER, run_controls
from .pnl import run_desk
from .recon import build_blotter, make_clearing_statement, match_rate, reconcile
from .simulate import simulate_day

# Probability a break is cleared on each following business day, by type.
RESOLVE_P = {"ID_MISMATCH": 0.8, "SETTLE_DATE_MISMATCH": 0.7, "PRICE_MISMATCH": 0.6,
             "QTY_MISMATCH": 0.55, "MISSING_AT_CLEARING": 0.5, "SIDE_MISMATCH": 0.4,
             "UNKNOWN_AT_CLEARING": 0.3}


@dataclass
class RunResult:
    report_date: pd.Timestamp
    days: list
    fills: pd.DataFrame = field(default_factory=pd.DataFrame)          # report day only
    parents: pd.DataFrame = field(default_factory=pd.DataFrame)        # all days
    pnl: pd.DataFrame = field(default_factory=pd.DataFrame)            # all days
    breaks: pd.DataFrame = field(default_factory=pd.DataFrame)         # all days, with lifecycle
    exceptions: pd.DataFrame = field(default_factory=pd.DataFrame)     # report day
    rule605: pd.DataFrame = field(default_factory=pd.DataFrame)        # report day
    markouts: pd.DataFrame = field(default_factory=pd.DataFrame)       # report day
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)          # one row per day KPIs
    recon_truth: pd.DataFrame = field(default_factory=pd.DataFrame)
    retail_markouts: pd.DataFrame = field(default_factory=pd.DataFrame)  # all days, retail only


def run(end_date: str, n_days: int = 10, params: SimParams | None = None) -> RunResult:
    p = params or SimParams()
    rng = np.random.default_rng(p.seed)
    days = trading_days_ending(end_date, n_days)
    open_px = {s: m["px"] for s, m in UNIVERSE.items()}
    res = RunResult(report_date=pd.Timestamp(days[-1]), days=days)
    parents_all, pnl_all, breaks_all, daily, truth_all, exc_all, mk_all = [], [], [], [], [], [], []

    for d in days:
        day = simulate_day(d, rng, p, open_px)
        open_px = {s: q["mid"].iat[-1] * np.exp(rng.normal(0, 0.006)) for s, q in day.quotes.items()}

        fm = tca.fill_metrics(day.executions, day.quotes)
        ptca = tca.parent_tca(day.parents, fm, day.quotes)
        ptca["trade_date"] = pd.Timestamp(d)
        pnl, hedges, breaches, _ = run_desk(d, fm, day.quotes, p.limits, rng)
        pnl["trade_date"] = pd.Timestamp(d)

        blotter = build_blotter(day.executions, hedges, d)
        stmt, truth = make_clearing_statement(blotter, rng, p.break_rate)
        breaks = reconcile(blotter, stmt, p.controls)
        truth["trade_date"] = pd.Timestamp(d)
        if len(breaks):
            n = rng.geometric([RESOLVE_P[k] for k in breaks["break_type"]])
            breaks["resolved_date"] = [pd.Timestamp(add_business_days(d, int(k))) for k in n]

        r = fm[fm["flow"] == "RETAIL"]
        daily.append({
            "trade_date": pd.Timestamp(d),
            "retail_orders": len(r), "algo_parents": len(ptca),
            "executions": len(blotter), "notional_usd": float(blotter["qty"].mul(blotter["price"]).sum()),
            "price_improvement_usd": float(r["pi_usd"].sum()),
            "pct_shares_improved": float((r["qty"] * (r["price_improvement"] > 0)).sum() / r["qty"].sum() * 100),
            "eq_ratio_pct": float((r["effective_spread"] * r["qty"]).sum() /
                                  (r["quoted_spread"] * r["qty"]).sum() * 100),
            "desk_pnl_usd": float(pnl["total_pnl"].sum()),
            "new_breaks": len(breaks), "match_rate_pct": match_rate(blotter, breaks),
            "avg_arrival_slippage_bps": float(np.average(ptca["arrival_slippage_bps"],
                                                         weights=ptca["filled_qty"] * ptca["arrival_mid"])),
        })
        parents_all.append(ptca)
        pnl_all.append(pnl)
        breaks_all.append(breaks)
        truth_all.append(truth)
        mk_cols = ["trade_date", "symbol", "qty"] + [c for c in fm.columns if c.startswith("markout_")]
        mk_all.append(fm.loc[fm["flow"] == "RETAIL", mk_cols])

        if pd.Timestamp(d) == res.report_date:
            exc = run_controls(d, fm, ptca, breaches, breaks, p.controls)
            exc_all.append(exc)
            res.fills = fm
            res.rule605 = tca.rule605_style(fm)
            res.markouts = tca.markout_curve(fm)

    res.parents = pd.concat(parents_all, ignore_index=True)
    res.pnl = pd.concat(pnl_all, ignore_index=True)
    res.breaks = _break_lifecycle(pd.concat(breaks_all, ignore_index=True), res.report_date)
    res.recon_truth = pd.concat(truth_all, ignore_index=True)
    res.daily = pd.DataFrame(daily)
    res.retail_markouts = pd.concat(mk_all, ignore_index=True)
    res.daily["open_breaks_eod"] = [
        int(((res.breaks["trade_date"] <= dd) & (res.breaks["resolved_date"] > dd)).sum())
        for dd in res.daily["trade_date"]]

    # stale-break control needs the multi-day view
    stale = res.breaks[(res.breaks["status"] == "OPEN") &
                       (res.breaks["age_bd"] > p.controls.stale_break_days)]
    if len(stale):
        exc_all.append(pd.DataFrame({
            "trade_date": res.report_date, "control": "STALE_RECON_BREAK", "severity": "HIGH",
            "symbol": stale["symbol"].to_numpy(), "ref": stale["exec_id"].astype(str).to_numpy(),
            "metric": stale["age_bd"].astype(float).to_numpy(),
            "detail": [f"{b.break_type} open {b.age_bd} business days "
                       f"(${b.notional_impact_usd:,.0f}); escalate to clearing relationship manager"
                       for b in stale.itertuples()],
        }))
    exc = pd.concat(exc_all, ignore_index=True)
    exc["_s"] = exc["severity"].map(SEVERITY_ORDER)
    res.exceptions = exc.sort_values(["_s", "control"], kind="stable").drop(columns="_s").reset_index(drop=True)
    return res


def _break_lifecycle(br: pd.DataFrame, report_date: pd.Timestamp) -> pd.DataFrame:
    br = br.copy()
    br["status"] = np.where(br["resolved_date"] > report_date, "OPEN", "RESOLVED")
    br["age_bd"] = [business_days_between(t, report_date) for t in br["trade_date"]]
    br["break_id"] = [f"BRK-{t:%y%m%d}-{i:04d}" for i, t in enumerate(br["trade_date"])]
    cols = ["break_id"] + [c for c in br.columns if c != "break_id"]
    return br[cols]


def save(res: RunResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "eqops.db"
    if db.exists():
        db.unlink()
    tag = f"{res.report_date:%Y%m%d}"
    fills_cols = ["exec_id", "order_id", "parent_id", "trade_date", "receipt_ts", "exec_ts",
                  "settle_date", "symbol", "client_side", "qty", "price", "flow", "capacity",
                  "order_type", "strategy", "venue", "liquidity", "latency_ms", "bid_rcv",
                  "ask_rcv", "bid_exe", "ask_exe", "effective_spread", "eff_spread_bps",
                  "price_improvement", "pi_usd", "through_nbbo", "markout_1s_bps",
                  "markout_5s_bps", "markout_15s_bps", "markout_60s_bps", "markout_300s_bps"]
    with sqlite3.connect(db) as con:
        res.fills[fills_cols].to_sql("executions", con, index=False)
        res.parents.to_sql("parent_orders", con, index=False)
        res.pnl.to_sql("desk_pnl", con, index=False)
        res.breaks.to_sql("recon_breaks", con, index=False)
        res.exceptions.to_sql("exceptions", con, index=False)
        res.rule605.to_sql("rule605_summary", con, index=False)
        res.daily.to_sql("daily_kpis", con, index=False)
    paths = {"db": db}
    for name, df in [("exceptions", res.exceptions),
                     ("open_breaks", res.breaks[res.breaks["status"] == "OPEN"]),
                     ("rule605_summary", res.rule605), ("desk_pnl", res.pnl[res.pnl["trade_date"] == res.report_date]),
                     ("parent_tca", res.parents[res.parents["trade_date"] == res.report_date])]:
        path = out_dir / f"{name}_{tag}.csv"
        df.to_csv(path, index=False, float_format="%.4f")
        paths[name] = path
    return paths
