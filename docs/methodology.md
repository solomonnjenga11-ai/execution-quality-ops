# Methodology

All inputs are synthetic and generated from a seed by `src/eqops/simulate.py`. The point of the
simulator is to exercise the analytics on data with the same *shape and failure modes* as a real
desk: wide opens, liquidity gaps, informed flow, latency outliers, bad timestamps, booking breaks.
The analytics themselves are written the way they would be run on production data.

## Sign conventions

* `client_side` = +1 client buy, −1 client sell.
* Costs are positive when the client paid more than the benchmark.
* Markouts and P&L are from the **desk's** side (the liquidity provider).

## Execution quality (retail, principal)

| Metric | Definition |
|---|---|
| Quoted spread | `ask − bid` at order receipt |
| Effective spread | `2 · side · (price − mid_receipt)` |
| E/Q ratio | share-weighted effective ÷ quoted spread. Below 100% means clients beat the quote on average |
| Price improvement | `side · (far_quote_receipt − price)`; far quote = ask for buys, bid for sells |
| Trade-through | `side · (price − far_quote_execution) > $0.0001` |
| Realized spread (60s) | `2 · side · (price − mid_{t+60s})` |
| Markout (h) | `side · (price − mid_{t+h}) / mid_t`, in bps, h ∈ {1s, 5s, 15s, 1m, 5m} |

The Rule 605-style table groups orders by type (market, marketable limit) and size bucket. It
borrows the structure of SEC Rule 605 but is not a regulatory report: real 605 files have more
order types, notional buckets and specific timing conventions.

## Algo TCA (agency)

| Metric | Definition |
|---|---|
| Arrival slippage | `side · (avg_px − arrival_mid) / arrival_mid` in bps (implementation shortfall, ex fees) |
| Interval VWAP slippage | `side · (avg_px − VWAP_[start,end]) / VWAP` in bps |
| Market move | `side · (mid_end − arrival_mid) / arrival_mid` — separates timing luck from execution |
| Participation | filled shares ÷ market volume over the order's window |

Each order is judged against the benchmark its strategy targets: VWAP/TWAP vs interval VWAP
(alert > 15 bps), IS vs arrival (alert > 50 bps).

## Desk P&L attribution

Valued at the mid, the desk's day decomposes exactly:

```
spread capture = Σ client fills   desk_qty · (mid_t − price)
hedge cost     = Σ hedge fills    desk_qty · (mid_t − price)
inventory P&L  = Σ seconds        position_t · (mid_{t+1} − mid_t)
fees           = −(take fee or ATS fee) · hedge shares
```

Because the book is flattened in the closing cross, the sum of the first three terms equals the
desk's cash P&L. `tests/test_pnl.py` checks this per symbol.

Hedging rule: when |position| exceeds $400k the desk sends a hedge after a 2–15 s reaction delay,
trading back to $100k, half at the midpoint ATS and half aggressively on lit venues. Time spent
above the $3M hard limit is logged as a POSITION_LIMIT exception.

## Reconciliation

1. Key match on execution ID, then compare side, quantity, price (±$0.0001) and settlement date.
2. Fuzzy match the leftovers on symbol, side, quantity, price and a 2-second window. A match here
   is one **ID_MISMATCH**, not a missing trade plus an unknown trade.
3. Remaining internal-only rows are MISSING_AT_CLEARING; street-only rows are UNKNOWN_AT_CLEARING.

Dollar impact per break: quantity × price difference, price × quantity difference, full notional
for missing/unknown/settle-date breaks, twice notional for side breaks, zero for ID breaks.

Break lifecycle is simulated with a per-type daily resolution probability, which produces the
aging profile in the report. Open breaks older than 3 business days are escalated.

## Controls

| Control | Severity | Trigger |
|---|---|---|
| TRADE_THROUGH | Critical | retail fill outside the NBBO at execution |
| OUT_OF_SESSION_TIMESTAMP | High | booking timestamp outside 09:30–16:00 |
| POSITION_LIMIT | High | desk position above hard limit |
| LARGE_RECON_BREAK | High | break impact > $50k |
| STALE_RECON_BREAK | High | open break > 3 business days |
| ALGO_BENCHMARK_SLIPPAGE | Medium | slippage vs own benchmark above threshold |
| EXECUTION_LATENCY | Medium | receipt-to-execution > 500 ms |

All thresholds live in `src/eqops/config.py`.

## Known simplifications

* One-second NBBO, no depth of book; mid path is exogenous (orders do not move the market).
* Algo orders always complete; no cancels, amends or partial days.
* No short-sale, locate or corporate-action handling.
* Clearing statement is a perturbed copy of the blotter rather than a parsed vendor file.
