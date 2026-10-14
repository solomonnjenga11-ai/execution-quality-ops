# LinkedIn post (draft — edit freely, not part of the code)

I built the daily checks a trading operations analyst runs, in SQL and Python.

Part 1 is pure SQL over raw trade, price, FX and FIX files:
• An as-of join that prices every trade against the last market tick before it, when the two feeds use different timestamp formats
• Position and P&L per account with a recursive CTE, including long-to-short flips
• Currency-pair and data-quality rules feeding one issues queue
• FIX order-to-fill matching that catches overfills, orphan fills, fills through the limit and resent messages

Each SQL result is cross-checked against an independent Python version in the test suite.

Part 2 is a Python end-of-day pipeline for a desk that internalises retail flow and executes algo orders:

• Execution quality: price improvement, effective vs quoted spread, Rule 605-style stats, markouts
• Algo TCA: slippage vs arrival and interval VWAP, each order judged against its own benchmark
• Desk P&L attribution: spread capture vs hedge cost vs inventory risk, reconciled to cash to the cent
• Trade reconciliation: blotter vs clearing statement, break classification and aging
• One ranked exceptions queue: trade-throughs, bad timestamps, position-limit breaches, stale breaks

Two things the data showed:
1. Spread capture was steady day to day; inventory risk decided whether the desk made or lost money.
2. Small retail orders stayed profitable at every horizon, but 2,000+ share orders turned negative after one minute, the signature of informed flow.

The data is synthetic (generated from a seed so anyone can reproduce it), but the methods are the ones used on production data. Python, pandas, SQLite, 30 tests, CI on GitHub Actions.

Code and sample report: https://github.com/solomonnjenga11-ai/execution-quality-ops

I'm looking for trading operations, middle office or execution analytics roles, and can start immediately (authorized to work in the US). If your team does this work, I'd value a conversation.

#TradingOperations #MarketStructure #TCA #Python #SQL #FinTech
