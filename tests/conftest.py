import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eqops.config import SimParams  # noqa: E402
from eqops.pipeline import run  # noqa: E402


@pytest.fixture(scope="session")
def small_params():
    return replace(SimParams(), retail_orders_per_day=600, algo_parents_per_day=8, seed=11)


@pytest.fixture(scope="session")
def small_run(small_params):
    return run("2026-10-09", 3, small_params)
