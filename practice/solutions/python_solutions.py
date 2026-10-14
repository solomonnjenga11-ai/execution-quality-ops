"""Solutions to practice/README.md, standard library only.

Run directly to see each answer on the repo's data:  python practice/solutions/python_solutions.py
"""
from __future__ import annotations

import csv
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CURRENCIES = {"USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "MXN", "ZAR", "KES",
              "SEK", "NOK", "HKD", "SGD", "CNH"}


# P1 -------------------------------------------------------------------------
def is_valid_pair(s, valid=CURRENCIES) -> bool:
    if not isinstance(s, str):
        return False
    if len(s) == 7 and s[3] in "/-":
        base, quote = s[:3], s[4:]
    elif len(s) == 6:
        base, quote = s[:3], s[3:]
    else:
        return False
    if not (base.isalpha() and quote.isalpha() and base.isupper() and quote.isupper()):
        return False
    return base in valid and quote in valid and base != quote


# P2 -------------------------------------------------------------------------
def parse_fix(msg: str) -> dict:
    out = {}
    for field in msg.strip().strip("|").split("|"):
        tag, _, value = field.partition("=")
        out[tag] = value
    return out


def fix_checksum(msg: str) -> str:
    body = msg[: msg.rindex("|10=") + 1].replace("|", "\x01")
    total = 0
    for ch in body:
        total += ord(ch)
    return f"{total % 256:03d}"


# P3 -------------------------------------------------------------------------
def fifo_realized_pnl(trades) -> float:
    """trades: iterable of (side, qty, price). Handles long and short lots."""
    lots: deque = deque()          # each lot: [signed_qty, price]
    realized = 0.0
    for side, qty, price in trades:
        q = qty if side == "BUY" else -qty
        while q != 0 and lots and (lots[0][0] > 0) != (q > 0):
            lot = lots[0]
            closed = min(abs(q), abs(lot[0]))
            direction = 1 if lot[0] > 0 else -1
            realized += closed * (price - lot[1]) * direction
            lot[0] -= closed * direction
            q += closed * direction
            if lot[0] == 0:
                lots.popleft()
        if q != 0:
            lots.append([q, price])
    return realized


# P4 -------------------------------------------------------------------------
def match_orders(orders, fills):
    ordered = {cl: qty for cl, qty in orders}
    filled = {cl: 0 for cl in ordered}
    seen, unknown = set(), []
    for exec_id, cl, qty in fills:
        if exec_id in seen:
            continue
        seen.add(exec_id)
        if cl not in ordered:
            unknown.append(exec_id)
            continue
        filled[cl] += qty
    over = [cl for cl in ordered if filled[cl] > ordered[cl]]
    under = [cl for cl in ordered if filled[cl] < ordered[cl]]
    return over, under, unknown


# P5 -------------------------------------------------------------------------
def duplicates_in_order(ids):
    seen, reported, out = set(), set(), []
    for i in ids:
        if i in seen and i not in reported:
            out.append(i)
            reported.add(i)
        seen.add(i)
    return out


# P6 -------------------------------------------------------------------------
def net_positions(trades):
    pos = {}
    for sym, side, qty in trades:
        pos[sym] = pos.get(sym, 0) + (qty if side == "BUY" else -qty)
    biggest = None
    for sym, q in pos.items():
        if biggest is None or abs(q) > abs(pos[biggest]):
            biggest = sym
    return pos, biggest


if __name__ == "__main__":
    raw = ROOT / "data" / "raw"
    with open(raw / "fx_trades.csv") as f:
        pairs = [r["ccy_pair"] for r in csv.DictReader(f)]
    print("P1 invalid pairs:", sorted({p for p in pairs if not is_valid_pair(p)}))
    first = (raw / "fix_log.txt").read_text().splitlines()[0]
    print("P2 checksum ok:", fix_checksum(first) == parse_fix(first)["10"])
    print("P3 FIFO:", fifo_realized_pnl([("BUY", 100, 10.0), ("BUY", 50, 11.0), ("SELL", 120, 12.0)]))
    print("P4:", match_orders([("A", 100), ("B", 200)], [("e1", "A", 100), ("e1", "A", 100),
                                                         ("e2", "B", 50), ("e3", "Z", 10)]))
    with open(raw / "crypto_trades.csv") as f:
        ids = [r["trade_id"] for r in csv.DictReader(f)]
    print("P5 duplicate trade IDs:", duplicates_in_order(ids))
    print("P6:", net_positions([("AAPL", "BUY", 100), ("AAPL", "SELL", 30), ("MSFT", "SELL", 200)]))
