"""Static charts (PNG) and a single-file HTML daily operations report."""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .config import MARKOUT_HORIZONS_S  # noqa: E402

# Reference categorical palette (fixed order) + sequential blue + status colours.
C = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "yellow": "#eda100",
     "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#8a8984", "grid": "#e6e5e0",
     "surface": "#fcfcfb"}
SEQ = ["#86b6ef", "#2a78d6", "#104281"]
STATUS = {"CRITICAL": "#d03b3b", "HIGH": "#ec835a", "MEDIUM": "#fab219", "LOW": "#0ca30c"}


def _style(ax, title, ylabel=None):
    ax.set_facecolor(C["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(C["muted"])
    ax.tick_params(colors=C["ink2"], labelsize=9, length=0)
    ax.grid(axis="y", color=C["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=12, color=C["ink"], pad=12, fontweight="bold")
    if ylabel:
        ax.set_ylabel(ylabel, color=C["ink2"], fontsize=9)


def _fig(w=8.2, h=3.6):
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(C["surface"])
    return fig, ax


def chart_pnl_attribution(pnl: pd.DataFrame):
    d = pnl.groupby("trade_date")[["spread_capture", "hedge_cost", "inventory_pnl", "fees",
                                   "total_pnl"]].sum()
    labels = [f"{t:%a %d}" for t in d.index]
    x = np.arange(len(d))
    fig, ax = _fig()
    comps = [("spread_capture", "Spread capture", C["blue"]), ("hedge_cost", "Hedge cost", C["orange"]),
             ("inventory_pnl", "Inventory P&L", C["aqua"]), ("fees", "Fees", C["yellow"])]
    pos_base, neg_base = np.zeros(len(d)), np.zeros(len(d))
    for col, lab, color in comps:
        v = d[col].to_numpy() / 1e3
        base = np.where(v >= 0, pos_base, neg_base)
        ax.bar(x, v, 0.62, bottom=base, color=color, label=lab, edgecolor=C["surface"], linewidth=1.5)
        pos_base += np.clip(v, 0, None)
        neg_base += np.clip(v, None, 0)
    ax.scatter(x, d["total_pnl"] / 1e3, s=46, color=C["ink"], edgecolor=C["surface"],
               linewidth=2, zorder=5, label="Net P&L")
    ax.axhline(0, color=C["muted"], linewidth=0.8)
    ax.set_xticks(x, labels)
    _style(ax, "Market-making desk P&L attribution by day", "$ thousands")
    ax.legend(frameon=False, fontsize=8.5, ncol=5, loc="upper left", bbox_to_anchor=(0, -0.1))
    fig.tight_layout()
    return fig


def chart_markouts(retail: pd.DataFrame):
    r = retail.copy()
    r["size_class"] = pd.cut(r["qty"], [0, 499, 1999, np.inf],
                             labels=["< 500 sh", "500–1,999 sh", "2,000+ sh"])
    fig, ax = _fig()
    colors = [C["blue"], C["orange"], C["aqua"]]
    xs = np.arange(len(MARKOUT_HORIZONS_S))
    for (cls, g), color in zip(r.groupby("size_class", observed=True), colors):
        ys = [np.average(g[f"markout_{h}s_bps"], weights=g["qty"]) for h in MARKOUT_HORIZONS_S]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=6,
                markeredgecolor=C["surface"], markeredgewidth=1.5, label=cls)
        ax.annotate(cls, (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=C["ink2"])
    ax.axhline(0, color=C["muted"], linewidth=0.8)
    ax.set_xticks(xs, [f"{h}s" if h < 60 else f"{h // 60}m" for h in MARKOUT_HORIZONS_S])
    ax.set_xlim(-0.3, len(xs) - 0.2)
    _style(ax, f"Desk markouts on internalised retail flow, by order size ({r['trade_date'].nunique()} days)", "bps (desk view, share-weighted)")
    ax.set_xlabel("Horizon after execution", color=C["ink2"], fontsize=9)
    ax.legend(frameon=False, fontsize=8.5, loc="lower left")
    fig.tight_layout()
    return fig


def chart_break_aging(breaks: pd.DataFrame, days):
    rows = []
    for d in days:
        d = pd.Timestamp(d)
        o = breaks[(breaks["trade_date"] <= d) & (breaks["resolved_date"] > d)]
        age = np.busday_count(o["trade_date"].values.astype("datetime64[D]"),
                              np.datetime64(d.date()))
        rows.append({"day": d, "0–1 days": int((age <= 1).sum()),
                     "2–3 days": int(((age >= 2) & (age <= 3)).sum()), "4+ days": int((age >= 4).sum())})
    a = pd.DataFrame(rows).set_index("day")
    fig, ax = _fig()
    x = np.arange(len(a))
    base = np.zeros(len(a))
    for col, color in zip(a.columns, SEQ):
        ax.bar(x, a[col], 0.62, bottom=base, color=color, label=col, edgecolor=C["surface"], linewidth=1.5)
        base += a[col].to_numpy()
    for xi, tot in zip(x, base):
        ax.text(xi, tot + 1, f"{int(tot)}", ha="center", fontsize=8, color=C["ink2"])
    ax.set_xticks(x, [f"{t:%a %d}" for t in a.index])
    _style(ax, "Open reconciliation breaks at end of day, by age", "open breaks")
    leg = ax.legend(frameon=False, fontsize=8.5, ncol=3, loc="upper left", title="Age (business days)",
                    title_fontsize=8.5)
    leg._legend_box.align = "left"
    ax.set_ylim(0, base.max() * 1.25)
    fig.tight_layout()
    return fig


def chart_algo_slippage(parents: pd.DataFrame):
    p = parents.copy()
    p["bench_slip"] = np.where(p["strategy"] == "IS", p["arrival_slippage_bps"], p["vwap_slippage_bps"])
    fig, ax = _fig()
    bins = np.arange(-60, 62.5, 5)
    ax.hist(p["bench_slip"].clip(-60, 60), bins=bins, color=C["blue"], edgecolor=C["surface"], linewidth=1.5)
    med = p["bench_slip"].median()
    ax.axvline(med, color=C["ink"], linewidth=1.2, linestyle="--")
    ax.annotate(f"median {med:+.1f} bps", (med, ax.get_ylim()[1] * 0.95), xytext=(-8, 0),
                textcoords="offset points", ha="right", fontsize=8.5, color=C["ink2"])
    _style(ax, "Algo parent orders: slippage vs. own benchmark (all days)", "parent orders")
    ax.set_xlabel("bps (VWAP/TWAP vs interval VWAP, IS vs arrival; + = cost; clipped at ±60)",
                  color=C["ink2"], fontsize=9)
    fig.tight_layout()
    return fig


def _b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def build_charts(res, image_dir: Path | None = None) -> dict[str, str]:
    figs = {
        "pnl_attribution": chart_pnl_attribution(res.pnl),
        "markouts": chart_markouts(res.retail_markouts),
        "break_aging": chart_break_aging(res.breaks, res.days),
        "algo_slippage": chart_algo_slippage(res.parents),
    }
    out = {}
    for name, fig in figs.items():
        if image_dir is not None:
            image_dir.mkdir(parents=True, exist_ok=True)
            fig.savefig(image_dir / f"{name}.png", facecolor=fig.get_facecolor())
        out[name] = _b64(fig)
    return out


# ----------------------------------------------------------------------------- HTML
def _table(df: pd.DataFrame, fmt: dict | None = None, max_rows: int = 25) -> str:
    fmt = fmt or {}
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = []
    for _, r in df.head(max_rows).iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c == "severity":
                cells.append(f'<td><span class="sev" style="--c:{STATUS.get(v, "#888")}">'
                             f'<i></i>{html.escape(str(v))}</span></td>')
                continue
            s = fmt[c](v) if c in fmt and pd.notna(v) else ("" if pd.isna(v) else str(v))
            num = isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)
            cells.append(f'<td class="{"num" if num else ""}">{html.escape(s)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    more = f'<p class="note">Showing {min(max_rows, len(df))} of {len(df)} rows.</p>' if len(df) > max_rows else ""
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{more}'


def write_html(res, charts: dict[str, str], path: Path, seed: int) -> Path:
    day = res.daily.iloc[-1]
    prev = res.daily.iloc[-2] if len(res.daily) > 1 else day
    exc = res.exceptions
    open_b = res.breaks[res.breaks["status"] == "OPEN"]
    pnl_d = res.pnl[res.pnl["trade_date"] == res.report_date]
    par_d = res.parents[res.parents["trade_date"] == res.report_date].copy()
    par_d["bench_slip"] = np.where(par_d["strategy"] == "IS", par_d["arrival_slippage_bps"],
                                   par_d["vwap_slippage_bps"])
    usd = lambda v: f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"  # noqa: E731
    f2 = lambda v: f"{v:,.2f}"                # noqa: E731
    f4 = lambda v: f"{v:,.4f}"                # noqa: E731
    pct = lambda v: f"{v:.1f}%"               # noqa: E731
    bps = lambda v: f"{v:+.1f}"               # noqa: E731

    def tile(label, value, sub):
        return f'<div class="tile"><div class="tl">{label}</div><div class="tv">{value}</div><div class="ts">{sub}</div></div>'

    sev_counts = exc["severity"].value_counts()
    tiles = "".join([
        tile("Executions booked", f"{int(day.executions):,}", f"${day.notional_usd / 1e9:,.2f}B notional"),
        tile("Retail price improvement", usd(day.price_improvement_usd),
             f"{day.pct_shares_improved:.1f}% of shares improved"),
        tile("Effective / quoted spread", pct(day.eq_ratio_pct), "lower is better for clients"),
        tile("Desk P&L (MTM)", usd(day.desk_pnl_usd), f"prior day {usd(prev.desk_pnl_usd)}"),
        tile("Recon match rate", f"{day.match_rate_pct:.2f}%", f"{int(day.new_breaks)} new breaks today"),
        tile("Open breaks", f"{len(open_b)}", f"{usd(open_b['notional_impact_usd'].sum())} at risk"),
        tile("Exceptions", f"{len(exc)}",
             f"{sev_counts.get('CRITICAL', 0)} critical · {sev_counts.get('HIGH', 0)} high"),
    ])

    r605 = res.rule605[["order_type", "size_bucket", "orders", "shares", "avg_eff_spread",
                        "avg_quoted_spread", "eq_ratio_pct", "pct_shares_improved",
                        "price_improvement_usd", "median_speed_ms", "avg_realized_spread_60s"]]
    pnl_tbl = pnl_d[["symbol", "client_shares", "spread_capture", "hedge_cost", "inventory_pnl",
                     "fees", "total_pnl", "capture_per_100sh", "n_hedges", "max_abs_position_usd"]]
    pnl_tbl = pd.concat([pnl_tbl, pd.DataFrame([{"symbol": "TOTAL", **pnl_tbl.drop(columns="symbol").sum()}])])
    pnl_tbl.loc[pnl_tbl["symbol"] == "TOTAL", ["capture_per_100sh", "max_abs_position_usd"]] = np.nan
    worst = par_d.reindex(par_d["bench_slip"].abs().sort_values(ascending=False).index)[
        ["parent_id", "symbol", "strategy", "client_side", "filled_qty", "participation_pct",
         "arrival_slippage_bps", "vwap_slippage_bps", "market_move_bps", "shortfall_usd"]]
    brk = open_b.sort_values("notional_impact_usd", ascending=False)[
        ["break_id", "trade_date", "age_bd", "account", "symbol", "break_type",
         "internal_qty", "street_qty", "internal_px", "street_px", "notional_impact_usd",
         "root_cause_hint"]].copy()
    brk["trade_date"] = brk["trade_date"].dt.strftime("%Y-%m-%d")
    btype = res.breaks[res.breaks["trade_date"] == res.report_date]["break_type"].value_counts()

    css = """
:root{--bg:#f6f5f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e6e5e0;--accent:#2a78d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e87;--line:#2e2e2b;--accent:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Inter",system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 60px}
header{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}h2{font-size:16px;margin:30px 0 4px}
.sub{color:var(--ink2);font-size:13px}.note{color:var(--muted);font-size:12px;margin:6px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tl{font-size:11.5px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}.tv{font-size:22px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}.ts{font-size:12px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:14px;margin-top:14px}
.card{background:#fcfcfb;border:1px solid var(--line);border-radius:10px;padding:10px} /* charts are light-mode PNGs */
img.chart{width:100%;height:auto;display:block}
.tw{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-weight:600;color:var(--ink2);background:var(--bg);position:sticky;top:0}td.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}
td:last-child{white-space:normal;min-width:300px}
.sev{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:11.5px}.sev i{width:9px;height:9px;border-radius:2px;background:var(--c);display:inline-block}
.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:2px 10px;margin:4px 6px 0 0;font-size:12px;color:var(--ink2);background:var(--surface)}
footer{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:520px){.grid2{grid-template-columns:1fr}.tv{font-size:19px}}
"""
    img = lambda k, alt: f'<div class="card"><img class="chart" alt="{alt}" src="data:image/png;base64,{charts[k]}"></div>'  # noqa: E731
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Daily Trading Ops Report</title>
<style>{css}</style></head><body><div class="wrap">
<header><div><h1>Daily Trading Operations &amp; Execution Quality</h1>
<div class="sub">Trade date {res.report_date:%A %d %B %Y} · Principal wholesale desk + agency algo flow · US equities</div></div>
<div class="sub">Synthetic data · seed {seed}</div></header>
<div class="tiles">{tiles}</div>

<h2>Exceptions queue</h2><p class="note">Every control that fired today, highest severity first. Each row is something an ops analyst would action or sign off.</p>
{_table(exc.drop(columns="trade_date"), {"metric": f2}, 30)}

<div class="grid2">{img("pnl_attribution", "Desk P&L attribution by day")}{img("markouts", "Markout curves by order size")}</div>

<h2>Desk P&amp;L by symbol</h2><p class="note">Mark-to-market at the mid. Spread capture + hedge cost + inventory + fees = total, exactly.</p>
{_table(pnl_tbl, {c: usd for c in ["spread_capture", "hedge_cost", "inventory_pnl", "fees", "total_pnl", "max_abs_position_usd"]} | {"client_shares": lambda v: f"{v:,.0f}", "capture_per_100sh": f2, "n_hedges": lambda v: f"{v:,.0f}"})}

<h2>Retail execution quality (Rule 605-style)</h2><p class="note">Spreads and price improvement measured against the NBBO at order receipt, share-weighted. Realized spread uses the mid 60 s after execution.</p>
{_table(r605, {"avg_eff_spread": f4, "avg_quoted_spread": f4, "eq_ratio_pct": pct, "pct_shares_improved": pct, "price_improvement_usd": usd, "median_speed_ms": lambda v: f"{v:.1f}", "avg_realized_spread_60s": f4, "shares": lambda v: f"{v:,.0f}"})}

<div class="grid2">{img("break_aging", "Open breaks by age")}{img("algo_slippage", "Algo slippage distribution")}</div>

<h2>Open reconciliation breaks</h2><p class="note">Internal blotter vs clearing statement. Today's new breaks by type:
{"".join(f'<span class="pill">{html.escape(k)} · {v}</span>' for k, v in btype.items())}</p>
{_table(brk, {"internal_px": f4, "street_px": f4, "notional_impact_usd": usd, "internal_qty": lambda v: f"{v:,.0f}", "street_qty": lambda v: f"{v:,.0f}"}, 20)}

<h2>Algo orders furthest from benchmark</h2>
{_table(worst, {"filled_qty": lambda v: f"{v:,.0f}", "participation_pct": pct, "arrival_slippage_bps": bps, "vwap_slippage_bps": bps, "market_move_bps": bps, "shortfall_usd": usd}, 10)}

<footer>All market data, orders and executions in this report are synthetic, generated by the project's simulator. Methodology: docs/methodology.md.
Generated by <code>eqops</code>.</footer>
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path
