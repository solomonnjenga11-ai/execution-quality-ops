def test_injected_errors_surface_as_exceptions(small_run):
    exc = small_run.exceptions
    assert {"OUT_OF_SESSION_TIMESTAMP"} <= set(exc["control"])
    tt = small_run.fills
    n_through = int(((tt["flow"] == "RETAIL") & (tt["through_nbbo"] > 1e-4)).sum())
    assert (exc["control"] == "TRADE_THROUGH").sum() == n_through


def test_exceptions_sorted_by_severity(small_run):
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    ranks = small_run.exceptions["severity"].map(order).tolist()
    assert ranks == sorted(ranks)
