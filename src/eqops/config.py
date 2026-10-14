"""Central configuration: universe, session, desk limits and control thresholds.

All thresholds live here so the ops controls are auditable in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Synthetic universe. Prices/ADV are rough, realistic levels only; every quote,
# order and fill in this project is SIMULATED (see simulate.py).
UNIVERSE: dict[str, dict] = {
    "AAPL": {"px": 228.0, "vol": 0.24, "adv": 52_000_000, "spread_ticks": 1},
    "MSFT": {"px": 431.0, "vol": 0.22, "adv": 20_000_000, "spread_ticks": 2},
    "NVDA": {"px": 182.0, "vol": 0.45, "adv": 180_000_000, "spread_ticks": 1},
    "AMZN": {"px": 221.0, "vol": 0.30, "adv": 38_000_000, "spread_ticks": 1},
    "JPM":  {"px": 297.0, "vol": 0.21, "adv": 9_000_000,  "spread_ticks": 2},
    "XOM":  {"px": 116.0, "vol": 0.23, "adv": 15_000_000, "spread_ticks": 1},
    "F":    {"px": 11.4,  "vol": 0.33, "adv": 60_000_000, "spread_ticks": 1},
    "PLTR": {"px": 168.0, "vol": 0.60, "adv": 70_000_000, "spread_ticks": 3},
}

TICK = 0.01
SESSION_START = "09:30:00"
SESSION_END = "16:00:00"
SESSION_SECONDS = 6 * 3600 + 30 * 60  # 23,400
MARKOUT_HORIZONS_S = (1, 5, 15, 60, 300)


@dataclass(frozen=True)
class DeskLimits:
    """Principal market-making desk risk limits (per symbol)."""
    max_abs_position_usd: float = 3_000_000.0  # hard limit -> exception
    hedge_trigger_usd: float = 400_000.0       # desk hedges back toward flat above this
    hedge_target_usd: float = 100_000.0
    take_fee_per_share: float = 0.0030         # paid when hedging aggressively on lit venues
    ats_fee_per_share: float = 0.0010          # paid when hedging at the midpoint
    midpoint_hedge_share: float = 0.5          # share of hedges worked at the midpoint


@dataclass(frozen=True)
class Controls:
    """Thresholds for the daily operational exception checks."""
    trade_through_tol: float = 0.0001         # $ outside NBBO before flagging
    max_latency_ms: float = 500.0             # order receipt -> execution
    is_slippage_alert_bps: float = 50.0       # IS orders: |slippage vs arrival|
    vwap_slippage_alert_bps: float = 15.0     # VWAP/TWAP orders: |slippage vs interval VWAP|
    break_notional_alert_usd: float = 50_000.0
    price_match_tol: float = 0.0001           # recon price tolerance ($/share)
    recon_time_window_s: int = 2              # fuzzy-match window for ID breaks
    stale_break_days: int = 3                 # open breaks older than this escalate


@dataclass(frozen=True)
class SimParams:
    retail_orders_per_day: int = 3_200
    algo_parents_per_day: int = 45
    break_rate: float = 0.008                 # share of executions with a booking break
    trade_through_rate: float = 0.0015        # injected execution errors
    late_timestamp_count: int = 2             # bookings stamped outside session
    seed: int = 7
    limits: DeskLimits = field(default_factory=DeskLimits)
    controls: Controls = field(default_factory=Controls)
