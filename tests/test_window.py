"""Unit tests for 15-minute window helpers (no Home Assistant required)."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "namudri_elektro"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load("namudri_const", COMPONENT / "const.py")
# window.py imports .const — inject a fake package
import sys
import types

pkg = types.ModuleType("namudri_elektro")
pkg.const = const
sys.modules["namudri_elektro"] = pkg
sys.modules["namudri_elektro.const"] = const
window = _load("namudri_elektro.window", COMPONENT / "window.py")


def test_window_aligns_to_quarter_hours() -> None:
    tz = ZoneInfo("Europe/Ljubljana")
    cases = [
        (datetime(2026, 10, 13, 20, 0, 0, tzinfo=tz), 0),
        (datetime(2026, 10, 13, 20, 7, 30, tzinfo=tz), 0),
        (datetime(2026, 10, 13, 20, 14, 59, tzinfo=tz), 0),
        (datetime(2026, 10, 13, 20, 15, 0, tzinfo=tz), 15),
        (datetime(2026, 10, 13, 20, 29, 1, tzinfo=tz), 15),
        (datetime(2026, 10, 13, 20, 30, 0, tzinfo=tz), 30),
        (datetime(2026, 10, 13, 20, 44, 0, tzinfo=tz), 30),
        (datetime(2026, 10, 13, 20, 45, 0, tzinfo=tz), 45),
        (datetime(2026, 10, 13, 20, 59, 59, tzinfo=tz), 45),
    ]
    for now, expected_minute in cases:
        start = window.window_start_for(now)
        assert start.minute == expected_minute
        assert start.second == 0
        assert start.microsecond == 0
        assert start.hour == now.hour


def test_projection_formula() -> None:
    """If 300 s at 2000 W then 600 s at 5000 W → 4000 W average."""
    energy_ws = 2000.0 * 300.0
    running, projected = window.project_average(energy_ws, 5000.0, 300.0)
    assert running == 2000.0
    assert projected == 4000.0
    assert const.WINDOW_SECONDS == 900
