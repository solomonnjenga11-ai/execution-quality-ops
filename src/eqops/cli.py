"""Command line entry point.

    python -m eqops --date 2026-10-09 --days 10 --seed 7 --out reports/   # daily ops report
    python -m eqops sql                                                   # SQL analysis layer
"""
from __future__ import annotations

import argparse
import time
from dataclasses import replace
from pathlib import Path

from .config import SimParams
from .pipeline import run, save
from .report import build_charts, write_html


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "sql":
        from .sql_runner import main as sql_main
        return sql_main(argv[1:])
    ap = argparse.ArgumentParser(prog="eqops", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="2026-10-09", help="report trade date (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=10, help="trading days to simulate, ending --date")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("reports"))
    ap.add_argument("--images", type=Path, default=None,
                    help="also save chart PNGs here (e.g. docs/images)")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    params = replace(SimParams(), seed=a.seed)
    res = run(a.date, a.days, params)
    paths = save(res, a.out)
    charts = build_charts(res, a.images)
    html_path = write_html(res, charts, a.out / f"daily_ops_report_{res.report_date:%Y%m%d}.html", a.seed)

    d = res.daily.iloc[-1]
    sev = res.exceptions["severity"].value_counts()
    print(f"eqops  trade date {res.report_date:%Y-%m-%d}  ({a.days} days simulated, seed {a.seed})")
    print(f"  executions booked      {int(d.executions):>10,}")
    print(f"  retail PI              ${d.price_improvement_usd:>10,.0f}   E/Q {d.eq_ratio_pct:.1f}%")
    print(f"  desk P&L (MTM)         ${d.desk_pnl_usd:>10,.0f}")
    print(f"  recon match rate       {d.match_rate_pct:>10.2f}%   open breaks {int(d.open_breaks_eod)}")
    print(f"  exceptions             {len(res.exceptions):>10}   "
          + ", ".join(f"{k.lower()} {v}" for k, v in sev.items()))
    print(f"  report  -> {html_path}")
    print(f"  sqlite  -> {paths['db']}")
    print(f"  done in {time.perf_counter() - t0:.1f}s")
    return 0
