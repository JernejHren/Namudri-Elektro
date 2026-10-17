"""Coordinator + controller tests against a minimal Home Assistant stub."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "namudri_elektro"
PKG = "ne_under_test"  # unique name so other test modules' fake packages don't clash
TZ = ZoneInfo("Europe/Ljubljana")

_clock: dict[str, datetime] = {"now": datetime(2026, 10, 13, 20, 0, 0, tzinfo=TZ)}


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def _install_stubs() -> None:
    class DataUpdateCoordinator:
        def __init__(self, hass, logger, *, name=None, update_interval=None):
            self.hass = hass
            self.data = None

        def __class_getitem__(cls, item):
            return cls

        def async_set_updated_data(self, data) -> None:
            self.data = data

    class Store:
        def __init__(self, *args, **kwargs) -> None:
            self.saved = None

        async def async_load(self):
            return None

        async def async_save(self, data) -> None:
            self.saved = data

    def noop_track(*args, **kwargs):
        return lambda: None

    ha = _module("homeassistant")
    core = _module(
        "homeassistant.core", HomeAssistant=object, callback=lambda func: func
    )
    helpers = _module("homeassistant.helpers")
    event = _module(
        "homeassistant.helpers.event",
        async_track_state_change_event=noop_track,
        async_track_time_interval=noop_track,
    )
    update = _module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
    )
    storage = _module("homeassistant.helpers.storage", Store=Store)
    util = _module("homeassistant.util")
    dt_mod = _module(
        "homeassistant.util.dt",
        now=lambda: _clock["now"],
        as_local=lambda value: value,
        parse_datetime=datetime.fromisoformat,
    )
    ha.core, ha.helpers, ha.util = core, helpers, util
    helpers.event, helpers.update_coordinator, helpers.storage = event, update, storage
    util.dt = dt_mod


def _load_component():
    for name in [n for n in sys.modules if n == PKG or n.startswith(f"{PKG}.")]:
        del sys.modules[name]
    _install_stubs()
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT)]
    sys.modules[PKG] = pkg
    return (
        importlib.import_module(f"{PKG}.const"),
        importlib.import_module(f"{PKG}.coordinator"),
        importlib.import_module(f"{PKG}.controller"),
    )


const, coordinator_mod, controller_mod = _load_component()


class FakeStates:
    def __init__(self) -> None:
        self._data: dict[str, SimpleNamespace] = {}

    def set(self, entity_id: str, state: str) -> None:
        self._data[entity_id] = SimpleNamespace(state=state, attributes={})

    def get(self, entity_id: str):
        return self._data.get(entity_id)


class FakeServices:
    def __init__(self, states: FakeStates) -> None:
        self._states = states
        self.calls: list[tuple[str, str, str | None]] = []

    async def async_call(self, domain, service, data, blocking=False) -> None:
        entity_id = data.get("entity_id")
        self.calls.append((domain, service, entity_id))
        if service == "turn_off":
            self._states.set(entity_id, "off")
        elif service == "turn_on":
            self._states.set(entity_id, "on")


class FakeHass:
    def __init__(self) -> None:
        self.states = FakeStates()
        self.services = FakeServices(self.states)
        self.tasks: list = []

    def async_create_task(self, coro) -> None:
        self.tasks.append(coro)
        coro.close()  # evaluation itself is covered by the controller tests


def at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 13, 20, minute, second, tzinfo=TZ)


def make_coordinator(power="1000", tariff="1", now: datetime | None = None):
    hass = FakeHass()
    hass.states.set("sensor.p", power)
    hass.states.set("sensor.t", tariff)
    _clock["now"] = now or at(0)
    coord = coordinator_mod.NamudriCoordinator(
        hass,
        "entry",
        {const.CONF_POWER_SENSOR: "sensor.p", const.CONF_TARIFF_SENSOR: "sensor.t"},
    )
    coord._init_window(_clock["now"])
    coord._ingest_power(_clock["now"], force=True)
    return coord, hass


def advance(coord, hass, now: datetime, power: str | None = None) -> None:
    _clock["now"] = now
    if power is not None:
        hass.states.set("sensor.p", power)
    coord._ingest_power(now)


# --- window rollover -------------------------------------------------------


def test_rollover_keeps_energy_between_boundary_and_now() -> None:
    coord, hass = make_coordinator(power="1000")
    advance(coord, hass, at(10), power="3000")  # 600 s at 1000 W integrated
    advance(coord, hass, at(15, 3))  # boundary 20:15:00, 3 s into the new window

    # 600 s @ 1000 W + 300 s @ 3000 W = 1 500 000 Ws -> 1666.67 W over 900 s
    assert coord._completed_average == pytest.approx(1666.67, abs=0.01)
    # the 3 s after the boundary belong to the new window (not lost)
    assert coord._energy_ws == pytest.approx(3 * 3000.0)
    assert coord._window_start == at(15)
    assert coord._window_complete is True


def test_rollover_over_several_missed_windows() -> None:
    coord, hass = make_coordinator(power="1000")
    advance(coord, hass, at(10), power="3000")
    advance(coord, hass, at(31))  # 20:15 and 20:30 windows both ended

    assert coord._completed_average == pytest.approx(3000.0)  # 20:15–20:30 at 3 kW
    assert coord._window_start == at(30)
    assert coord._energy_ws == pytest.approx(60 * 3000.0)


def test_rollover_across_dst_fall_back() -> None:
    # 2026-10-25: clocks go 03:00 CEST -> 02:00 CET
    start = datetime(2026, 10, 25, 2, 50, tzinfo=TZ)  # CEST (first 02:xx)
    coord, hass = make_coordinator(power="1000", now=start)
    later = datetime(2026, 10, 25, 2, 5, tzinfo=TZ, fold=1)  # CET, 75 min later
    advance(coord, hass, later)

    expected_start = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    assert coord._window_start.timestamp() == expected_start.timestamp()
    assert coord._energy_ws == pytest.approx(5 * 60 * 1000.0)  # 300 s into window


# --- starting mid-window ---------------------------------------------------


def test_mid_window_start_is_backfilled_and_flagged() -> None:
    coord, hass = make_coordinator(power="2000", now=at(10))
    snap = coord._compute()

    assert snap.window_complete is False
    assert snap.elapsed_seconds == 600.0
    assert snap.running_average == pytest.approx(2000.0)
    assert snap.projected_average == pytest.approx(2000.0)


def test_partial_window_is_complete_after_rollover() -> None:
    coord, hass = make_coordinator(power="2000", now=at(10))
    advance(coord, hass, at(15, 1))

    assert coord._completed_average is None  # partial window not reported
    assert coord._window_complete is True


def test_start_right_at_boundary_counts_as_complete() -> None:
    coord, _ = make_coordinator(now=at(15, 3))
    assert coord._window_complete is True


# --- tariff / limit handling ----------------------------------------------


def test_unknown_tariff_gives_unknown_limit() -> None:
    coord, hass = make_coordinator(power="8000", tariff="unavailable")
    snap = coord._compute()
    assert snap.tariff_block == 0
    assert snap.headroom_w is None
    assert snap.over_limit is None

    hass.states.set("sensor.t", "1")  # default agreed power T1 = 7 kW
    snap = coord._compute()
    assert snap.over_limit is True
    assert snap.headroom_w == pytest.approx(-1000.0)


# --- startup gate ----------------------------------------------------------


def test_evaluation_waits_until_ready() -> None:
    coord, hass = make_coordinator()
    hass.tasks.clear()

    coord._publish()
    assert hass.tasks == []

    coord.async_mark_ready()
    assert len(hass.tasks) == 1


# --- controller ------------------------------------------------------------


def _controller(hass, shed: dict | None = None):
    coord = SimpleNamespace(async_request_refresh_ui=lambda: None)
    ctrl = controller_mod.LoadShedController(hass, coord, "entry", ["switch.a"], [])
    ctrl._shed = shed or {}
    return ctrl


def _snap(**overrides):
    values = dict(
        tariff_block=1,
        projected_average=12000.0,
        agreed_power_w=7000.0,
        elapsed_seconds=300.0,
        window_complete=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


_SHED_RECORD = {"off_since": "2026-10-13T19:00:00+02:00", "previous": {}}


def test_unknown_tariff_keeps_shed_devices_off() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": dict(_SHED_RECORD)})

    asyncio.run(ctrl.async_evaluate(_snap(tariff_block=0, projected_average=0.0)))

    assert hass.services.calls == []
    assert "switch.a" in ctrl._shed


def test_outside_shed_blocks_still_restores() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": dict(_SHED_RECORD)})

    asyncio.run(ctrl.async_evaluate(_snap(tariff_block=3, projected_average=0.0)))

    assert ("switch", "turn_on", "switch.a") in hass.services.calls
    assert ctrl._shed == {}


def test_no_shedding_in_partial_window() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    asyncio.run(ctrl.async_evaluate(_snap(window_complete=False)))

    assert hass.services.calls == []


def test_sheds_in_complete_window() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    asyncio.run(ctrl.async_evaluate(_snap()))

    assert ("switch", "turn_off", "switch.a") in hass.services.calls
    assert "switch.a" in ctrl._shed
