"""Load data/raw/ into SQLite and run the SQL analysis layer (sql/analysis/*.sql).

    python -m eqops sql --raw data/raw --out reports/sql

Python does the loading (CSV, and FIX parsing with checksum validation);
every piece of analysis is plain SQL in numbered files. Each file declares the
views it produces with ``-- export: <view>`` and those are written to CSV.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from .currency import ISO_4217, NON_TRADABLE
from .fix_parser import to_records

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql" / "analysis"
_EXPORT = re.compile(r"^--\s*export:\s*(\w+)", re.M)


def load_raw(con: sqlite3.Connection, raw_dir: Path) -> dict[str, int]:
    con.executescript((SQL_DIR / "00_schema.sql").read_text())
    counts = {}
    for table, fname in [("raw_crypto_prices", "crypto_prices.csv"),
                         ("raw_crypto_trades", "crypto_trades.csv"),
                         ("raw_fx_trades", "fx_trades.csv")]:
        df = pd.read_csv(raw_dir / fname, dtype={"ccy_pair": str, "trade_id": str},
                         keep_default_na=False, na_values=[])
        if "ccy_pair" in df:
            df["ccy_pair"] = df["ccy_pair"].replace("", None)
        df.to_sql(table, con, if_exists="append", index=False)
        counts[table] = len(df)
    fix = pd.DataFrame(to_records((raw_dir / "fix_log.txt").read_text().splitlines()))
    fix.to_sql("fix_messages", con, if_exists="append", index=False)
    counts["fix_messages"] = len(fix)
    ref = [(c, 1) for c in sorted(ISO_4217)] + [(c, 0) for c in sorted(NON_TRADABLE)]
    con.executemany("INSERT INTO ref_currencies VALUES (?, ?)", ref)
    con.commit()
    return counts


def run_analysis(con: sqlite3.Connection, out_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for f in sorted(SQL_DIR.glob("[0-9][0-9]_*.sql")):
        if f.name.startswith("00_"):
            continue
        text = f.read_text()
        con.executescript(text)
        for view in _EXPORT.findall(text):
            results[view] = pd.read_sql_query(f"SELECT * FROM {view}", con)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, df in results.items():
            df.to_csv(out_dir / f"{name}.csv", index=False)
    return results


def build_database(raw_dir: Path, db_path: Path, out_dir: Path | None = None):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    counts = load_raw(con, raw_dir)
    results = run_analysis(con, out_dir)
    return con, counts, results


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="eqops sql", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--out", type=Path, default=Path("reports") / "sql")
    ap.add_argument("--regenerate", action="store_true",
                    help="rebuild data/raw from the generator before loading")
    a = ap.parse_args(argv)
    if a.regenerate or not (a.raw / "fix_log.txt").exists():
        from .datasets import generate
        generate(a.raw)
    con, counts, res = build_database(a.raw, a.out / "trade_analysis.db", a.out)

    print("Loaded: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    tm = res["v_trade_marks"]
    print("\n[01] As-of pricing of crypto trades")
    print(tm["pricing_status"].value_counts().to_string())
    print("\n[02] End-of-day positions (top 5 by total P&L)")
    print(res["v_positions_eod"].head(5).to_string(index=False))
    print("\n[03] Data-quality issues")
    print(res["v_dq_summary"].to_string(index=False))
    print("\n[04] FIX lifecycle exceptions")
    print(res["v_fix_exception_summary"].to_string(index=False))
    print(f"\nSQLite -> {a.out / 'trade_analysis.db'}   CSVs -> {a.out}/")
    con.close()
    return 0
