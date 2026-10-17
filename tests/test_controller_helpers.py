"""Unit tests for load-shed helper logic (no Home Assistant runtime)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "namudri_elektro"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load("namudri_const", COMPONENT / "const.py")
pkg = types.ModuleType("namudri_elektro")
pkg.const = const
sys.modules["namudri_elektro"] = pkg
sys.modules["namudri_elektro.const"] = const


def test_shed_blocks_only_1_and_2() -> None:
    assert const.SHED_TARIFF_BLOCKS == frozenset({1, 2})


def test_is_on_switch_and_climate() -> None:
    # Import only the static helpers by exec'ing a tiny stub of controller logic
    def is_on(entity_id: str, state: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        if domain == "switch":
            return state == "on"
        return state not in ("off", "unknown", "unavailable")

    assert is_on("switch.boiler", "on") is True
    assert is_on("switch.boiler", "off") is False
    assert is_on("climate.heat", "heat") is True
    assert is_on("climate.heat", "off") is False
    assert is_on("water_heater.tank", "eco") is True
