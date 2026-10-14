import pandas as pd

from eqops.config import Controls
from eqops.recon import reconcile


def _blotter():
    t = pd.Timestamp("2026-10-09 10:00:00")
    rows = [("A1", "AAPL", "B", 100, 228.10, t), ("A2", "MSFT", "S", 200, 431.05, t),
            ("A3", "NVDA", "B", 300, 182.20, t), ("A4", "JPM", "B", 50, 297.00, t)]
    df = pd.DataFrame(rows, columns=["exec_id", "symbol", "side", "qty", "price", "exec_ts"])
    df["trade_date"] = pd.Timestamp("2026-10-09")
    df["settle_date"] = pd.Timestamp("2026-10-12")
    df["account"] = "WHOLESALE_RETAIL"
    return df


def test_each_break_type_is_classified_once():
    b = _blotter()
    s = b.copy()
    s.loc[0, "qty"] = 90                       # QTY_MISMATCH on A1
    s.loc[1, "exec_id"] = "CLR123"             # re-keyed -> ID_MISMATCH, not missing + unknown
    s = s[s["exec_id"] != "A3"]                # MISSING_AT_CLEARING
    extra = b.iloc[[3]].copy()
    extra["exec_id"], extra["qty"] = "CLR999", 700
    s = pd.concat([s, extra], ignore_index=True)  # UNKNOWN_AT_CLEARING
    br = reconcile(b, s, Controls())
    got = dict(zip(br["break_type"], br["exec_id"]))
    assert got["QTY_MISMATCH"] == "A1"
    assert got["ID_MISMATCH"] == "A2"
    assert got["MISSING_AT_CLEARING"] == "A3"
    assert got["UNKNOWN_AT_CLEARING"] == "CLR999"
    assert len(br) == 4
    assert br.loc[br["break_type"] == "ID_MISMATCH", "notional_impact_usd"].iat[0] == 0


def test_price_within_tolerance_is_not_a_break():
    b = _blotter()
    s = b.copy()
    s.loc[0, "price"] += 0.00005
    assert reconcile(b, s, Controls()).empty


def test_engine_finds_every_injected_break(small_run):
    found = small_run.breaks.groupby("break_type").size().sort_index()
    truth = small_run.recon_truth.groupby("break_type").size().sort_index()
    assert found.to_dict() == truth.to_dict()
