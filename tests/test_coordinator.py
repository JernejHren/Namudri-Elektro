"""Coordinator + controller tests against a minimal Home Assistant stub."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
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
        def __init__(
            self, hass, logger, *, config_entry=None, name=None, update_interval=None
        ):
            self.hass = hass
            self.config_entry = config_entry
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
    config_entries = _module("homeassistant.config_entries", ConfigEntry=object)
    util = _module("homeassistant.util")
    dt_mod = _module(
        "homeassistant.util.dt",
        now=lambda: _clock["now"],
        as_local=lambda value: value,
        parse_datetime=datetime.fromisoformat,
    )
    ha.core, ha.helpers, ha.util = core, helpers, util
    ha.config_entries = config_entries
    helpers.event, helpers.update_coordinator, helpers.storage = event, update, storage
    util.dt = dt_mod


def _await(coro):
    """Run a coroutine on a private loop (the global loop belongs to the HA plugin)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _is_ha(name: str) -> bool:
    return name == "homeassistant" or name.startswith("homeassistant.")


def _load_component():
    """Import the component against stubs, then give the real HA modules back.

    The imported modules keep their references to the stubs, while the tests
    that run against a real Home Assistant (other files) are not affected.
    """
    for name in [n for n in sys.modules if n == PKG or n.startswith(f"{PKG}.")]:
        del sys.modules[name]
    real = {n: m for n, m in sys.modules.items() if _is_ha(n)}
    for name in real:
        del sys.modules[name]
    try:
        _install_stubs()
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(COMPONENT)]
        sys.modules[PKG] = pkg
        return (
            importlib.import_module(f"{PKG}.const"),
            importlib.import_module(f"{PKG}.coordinator"),
            importlib.import_module(f"{PKG}.controller"),
        )
    finally:
        for name in [n for n in sys.modules if _is_ha(n)]:
            del sys.modules[name]
        sys.modules.update(real)


const, coordinator_mod, controller_mod = _load_component()


class FakeStates:
    def __init__(self) -> None:
        self._data: dict[str, SimpleNamespace] = {}

    def set(
        self,
        entity_id: str,
        state: str,
        attributes: dict | None = None,
        age_seconds: float | None = None,
    ) -> None:
        """Set a state; ``age_seconds`` fakes how long ago it was last reported."""
        reported = (
            _clock["now"] - timedelta(seconds=age_seconds)
            if age_seconds is not None
            else None
        )
        self._data[entity_id] = SimpleNamespace(
            state=state, attributes=attributes or {}, last_reported=reported
        )

    def get(self, entity_id: str):
        return self._data.get(entity_id)


class FakeServices:
    def __init__(self, states: FakeStates) -> None:
        self._states = states
        self.calls: list[tuple[str, str, str | None]] = []
        self.payloads: list[dict] = []
        self.lag_entities: set[str] = set()  # accept turn_off but report it late
        self.fail_entities: set[str] = set()  # turn_off raises
        self.fail_services: set[str] = set()  # these service names always raise

    async def async_call(self, domain, service, data, blocking=False) -> None:
        entity_id = data.get("entity_id")
        self.calls.append((domain, service, entity_id))
        self.payloads.append(dict(data))
        if service in self.fail_services:
            raise RuntimeError(f"{service} failed")
        if service == "turn_off" and entity_id in self.fail_entities:
            raise RuntimeError("device unreachable")
        if service == "turn_off":
            if entity_id not in self.lag_entities:
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

    hass.states.set("sensor.t", "1")
    coord.set_agreed_kw(1, 7.0)
    snap = coord._compute()
    assert snap.over_limit is True
    assert snap.headroom_w == pytest.approx(-1000.0)


def test_agreed_power_not_set_means_unknown_limit() -> None:
    coord, hass = make_coordinator(power="8000", tariff="1")
    assert coord.get_agreed_kw(1) == 0.0  # default: not set

    snap = coord._compute()
    assert snap.tariff_block == 1
    assert snap.headroom_w is None
    assert snap.over_limit is None


# --- startup gate ----------------------------------------------------------


def test_evaluation_waits_until_ready() -> None:
    coord, hass = make_coordinator()
    hass.tasks.clear()

    coord._publish()
    assert hass.tasks == []

    coord.async_mark_ready()
    assert len(hass.tasks) == 1


# --- controller ------------------------------------------------------------


def _controller(hass, shed: dict | None = None, devices=("switch.a",)):
    coord = SimpleNamespace(async_request_refresh_ui=lambda: None)
    ctrl = controller_mod.LoadShedController(hass, coord, "entry", list(devices), [])
    ctrl._shed = shed or {}
    return ctrl


def _snap(**overrides):
    values = dict(
        tariff_block=1,
        projected_average=12000.0,
        agreed_power_w=7000.0,
        elapsed_seconds=300.0,
        window_complete=True,
        power_valid=True,
        current_power=12000.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


_SHED_RECORD = {"off_since": "2026-10-13T19:00:00+02:00", "previous": {}}


def test_unknown_tariff_keeps_shed_devices_off() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": dict(_SHED_RECORD)})

    _await(ctrl.async_evaluate(_snap(tariff_block=0, projected_average=0.0)))

    assert hass.services.calls == []
    assert "switch.a" in ctrl._shed


def test_outside_shed_blocks_still_restores() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": dict(_SHED_RECORD)})

    _await(ctrl.async_evaluate(_snap(tariff_block=3, projected_average=0.0)))

    assert ("switch", "turn_on", "switch.a") in hass.services.calls
    assert ctrl._shed == {}


def test_no_shedding_in_partial_window() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    _await(ctrl.async_evaluate(_snap(window_complete=False)))

    assert hass.services.calls == []


def test_sheds_in_complete_window() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    _await(ctrl.async_evaluate(_snap()))

    assert ("switch", "turn_off", "switch.a") in hass.services.calls
    assert "switch.a" in ctrl._shed


# --- controller: priority, timing, persistence ----------------------------


def _run(ctrl, snap, now: datetime) -> None:
    _clock["now"] = now
    _await(ctrl.async_evaluate(snap))


def _record(off_since: str = "2026-10-13T20:00:00+02:00") -> dict:
    return {"off_since": off_since, "previous": {}}


def test_sheds_in_priority_order() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "on")
    ctrl = _controller(hass, devices=("switch.a", "switch.b"))

    _run(ctrl, _snap(), at(5))
    _run(ctrl, _snap(), at(5, 20))  # past the short shed cooldown

    offs = [c for c in hass.services.calls if c[1] == "turn_off"]
    assert offs == [("switch", "turn_off", "switch.a"), ("switch", "turn_off", "switch.b")]


def test_restore_waits_for_min_off_time() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": _record()})

    _run(ctrl, _snap(projected_average=0.0), at(5))  # off only 5 min (< 15)
    assert hass.services.calls == []

    _run(ctrl, _snap(projected_average=0.0), at(20))  # 20 min
    assert hass.services.calls == [("switch", "turn_on", "switch.a")]


def test_restore_needs_projection_below_limit_minus_hysteresis() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": _record()})

    # limit 7000, hysteresis 500 -> must be below 6500; 6800 is not enough
    _run(ctrl, _snap(projected_average=6800.0), at(20))
    assert hass.services.calls == []

    _run(ctrl, _snap(projected_average=6000.0), at(21))
    assert hass.services.calls == [("switch", "turn_on", "switch.a")]


def test_restores_one_device_at_a_time_last_shed_first() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    hass.states.set("switch.b", "off")
    ctrl = _controller(
        hass,
        {"switch.a": _record(), "switch.b": _record()},
        devices=("switch.a", "switch.b"),
    )
    outside = _snap(tariff_block=3, projected_average=0.0)

    _run(ctrl, outside, at(20))
    assert hass.services.calls == [("switch", "turn_on", "switch.b")]

    _run(ctrl, outside, at(20, 10))  # inside the 30 s stagger -> nothing
    assert len(hass.services.calls) == 1

    _run(ctrl, outside, at(20, 40))
    assert hass.services.calls[-1] == ("switch", "turn_on", "switch.a")


def test_dropped_records_are_persisted() -> None:
    hass = FakeHass()  # switch.a has no state -> record is dropped
    ctrl = _controller(hass, {"switch.a": _record()})

    _run(ctrl, _snap(tariff_block=3, projected_average=0.0), at(20))

    assert ctrl._shed == {}
    assert ctrl._store.saved == {"shed": {}, "device_power": {}}


def test_shed_list_survives_restart() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)
    _run(ctrl, _snap(), at(5))
    saved = ctrl._store.saved
    assert "switch.a" in saved["shed"]

    ctrl2 = _controller(hass)

    async def _load():
        return saved

    ctrl2._store.async_load = _load
    _await(ctrl2.async_load())
    assert "switch.a" in ctrl2._shed


def test_force_shed_needs_shedding_enabled() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)
    ctrl.enabled = False

    assert _await(ctrl.async_force_shed()) is None
    assert hass.services.calls == []


def test_water_heater_operation_mode_is_restored() -> None:
    hass = FakeHass()
    hass.states.set("water_heater.tank", "eco", {"temperature": 55})
    ctrl = _controller(hass, devices=("water_heater.tank",))

    _run(ctrl, _snap(), at(5))  # shed
    assert ctrl._shed["water_heater.tank"]["previous"]["operation_mode"] == "eco"

    _run(ctrl, _snap(tariff_block=3, projected_average=0.0), at(25))  # restore
    assert ("water_heater", "set_operation_mode", "water_heater.tank") in (
        hass.services.calls
    )
    payload = next(
        p for c, p in zip(hass.services.calls, hass.services.payloads)
        if c[1] == "set_operation_mode"
    )
    assert payload["operation_mode"] == "eco"


def test_first_priority_device_is_always_shed_first() -> None:
    """After a restore the first device is shed again right away (no guard)."""
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "on")
    ctrl = _controller(hass, devices=("switch.a", "switch.b"))

    _run(ctrl, _snap(), at(0, 10))  # a shed
    hass.states.set("switch.a", "off")
    _run(ctrl, _snap(tariff_block=3, projected_average=0.0), at(20))  # a restored
    hass.services.calls.clear()

    _run(ctrl, _snap(), at(21))  # overload again -> a first, not b

    assert hass.services.calls == [("switch", "turn_off", "switch.a")]


def test_shed_device_stays_off_for_min_off_time() -> None:
    """Even with a very low projection nothing comes back before 15 minutes."""
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    _run(ctrl, _snap(), at(0, 10))  # shed at 20:00:10
    for minute in (5, 10, 14):
        _run(ctrl, _snap(projected_average=0.0), at(minute))
    assert [c for c in hass.services.calls if c[1] == "turn_on"] == []

    _run(ctrl, _snap(projected_average=0.0), at(15, 30))
    assert ("switch", "turn_on", "switch.a") in hass.services.calls


def test_force_shed_sheds_next_device() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)
    _clock["now"] = at(5)

    assert _await(ctrl.async_force_shed()) == "switch.a"
    assert ("switch", "turn_off", "switch.a") in hass.services.calls
    assert "switch.a" in ctrl._shed


# --- notification targets ---------------------------------------------------

notify_helpers = importlib.import_module(f"{PKG}.notify_helpers")


def test_notify_targets_from_new_list_field() -> None:
    cfg = {const.CONF_NOTIFY: ["notify.mobile_app_a", "mobile_app_b", "", "notify.mobile_app_a"]}
    assert notify_helpers.notify_targets(cfg) == [
        "notify.mobile_app_a",
        "notify.mobile_app_b",
    ]


def test_notify_targets_falls_back_to_legacy_fields() -> None:
    cfg = {const.CONF_NOTIFY_1: "notify.mobile_app_a", const.CONF_NOTIFY_2: ""}
    assert notify_helpers.notify_targets(cfg) == ["notify.mobile_app_a"]


def test_cleared_list_wins_over_legacy_fields() -> None:
    cfg = {const.CONF_NOTIFY: [], const.CONF_NOTIFY_1: "notify.mobile_app_a"}
    assert notify_helpers.notify_targets(cfg) == []


def test_notify_options_put_mobile_apps_first_and_hide_send_message() -> None:
    options = notify_helpers.build_notify_options(
        ["persistent_notification", "send_message", "mobile_app_phone", "alexa"],
        [],
    )
    assert [value for value, _ in options] == [
        "notify.mobile_app_phone",
        "notify.alexa",
        "notify.persistent_notification",
    ]


def test_notify_options_keep_missing_selected_target() -> None:
    options = notify_helpers.build_notify_options(
        ["mobile_app_phone"], ["notify.mobile_app_old"]
    )
    assert ("notify.mobile_app_old", "notify.mobile_app_old (unavailable)") in options


def test_coordinator_uses_notify_targets() -> None:
    hass = FakeHass()
    hass.states.set("sensor.p", "0")
    hass.states.set("sensor.t", "1")
    coord = coordinator_mod.NamudriCoordinator(
        hass,
        "entry",
        {
            const.CONF_POWER_SENSOR: "sensor.p",
            const.CONF_TARIFF_SENSOR: "sensor.t",
            const.CONF_NOTIFY: ["notify.mobile_app_a", "notify.mobile_app_b"],
        },
    )
    assert coord.controller.notify_services == [
        "notify.mobile_app_a",
        "notify.mobile_app_b",
    ]


# --- stale power sensor and smoothing (coordinator) -------------------------


def test_stale_or_unavailable_power_sensor_is_invalid() -> None:
    coord, hass = make_coordinator(power="5000")
    assert coord._compute().power_valid is True  # no timestamp -> nothing to check

    hass.states.set("sensor.p", "5000", age_seconds=10)
    assert coord._compute().power_valid is True

    hass.states.set("sensor.p", "5000", age_seconds=300)  # timeout default 120 s
    assert coord._compute().power_valid is False

    hass.states.set("sensor.p", "unavailable")
    assert coord._compute().power_valid is False

    coord.power_timeout_seconds = 0  # check disabled
    hass.states.set("sensor.p", "5000", age_seconds=3000)
    assert coord._compute().power_valid is True


def test_projection_uses_smoothed_power() -> None:
    coord, hass = make_coordinator(power="1000")
    coord.smoothing_seconds = 30
    advance(coord, hass, at(1, 0), power="5000")
    advance(coord, hass, at(1, 10))
    snap = coord._compute()

    energy = 1000 * 60 + 5000 * 10
    smoothed = (20 * 1000 + 10 * 5000) / 30  # 20 s at 1 kW, 10 s at 5 kW
    assert snap.current_power == 5000  # raw power stays visible
    assert snap.projected_average == pytest.approx(
        (energy + smoothed * 830) / 900, abs=0.05
    )

    coord.smoothing_seconds = 0
    snap = coord._compute()
    assert snap.projected_average == pytest.approx(
        (energy + 5000 * 830) / 900, abs=0.05
    )


# --- controller: safety, learning, timing ----------------------------------


def _offs(hass) -> list[str]:
    return [c[2] for c in hass.services.calls if c[1] == "turn_off"]


def test_unconfirmed_turn_off_is_still_recorded(monkeypatch) -> None:
    """A slow device is recorded and the next one is NOT shed as well."""
    monkeypatch.setattr(controller_mod, "STATE_CONFIRM_TIMEOUT_SECONDS", 0.6)
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "on")
    hass.services.lag_entities.add("switch.a")
    ctrl = _controller(hass, devices=("switch.a", "switch.b"))

    _run(ctrl, _snap(), at(5))

    assert "switch.a" in ctrl._shed
    assert _offs(hass) == ["switch.a"]


def test_failed_turn_off_moves_on_to_the_next_device() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "on")
    hass.services.fail_entities.add("switch.a")
    ctrl = _controller(hass, devices=("switch.a", "switch.b"))

    _run(ctrl, _snap(), at(5))

    assert "switch.a" not in ctrl._shed
    assert "switch.b" in ctrl._shed


def test_freshly_shed_device_reporting_on_is_not_a_user_override() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")  # state lags behind the turn_off
    ctrl = _controller(hass, {"switch.a": _record("2026-10-13T20:00:00+02:00")})
    outside = _snap(tariff_block=3, projected_average=0.0)

    _run(ctrl, outside, at(0, 10))  # 10 s after the shed
    assert "switch.a" in ctrl._shed

    _run(ctrl, outside, at(1, 0))  # still on a minute later -> user override
    assert "switch.a" not in ctrl._shed


def test_invalid_power_pauses_shedding_and_notifies_once() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    coord = SimpleNamespace(async_request_refresh_ui=lambda: None, current_power=0.0)
    ctrl = controller_mod.LoadShedController(
        hass, coord, "entry", ["switch.a"], ["notify.mobile_app_x"]
    )
    bad = _snap(power_valid=False)

    _run(ctrl, bad, at(5))
    _run(ctrl, bad, at(6, 10))  # more than 60 s without a usable sensor
    _run(ctrl, bad, at(7))
    notifications = [c for c in hass.services.calls if c[0] == "notify"]
    assert len(notifications) == 1
    assert _offs(hass) == []

    _run(ctrl, _snap(projected_average=0.0), at(8))  # sensor is back
    notifications = [c for c in hass.services.calls if c[0] == "notify"]
    assert len(notifications) == 2


def test_overdue_device_is_restored_whatever_the_projection() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": _record()})  # shed at 20:00
    ctrl.max_off_minutes = 30
    overloaded = _snap(projected_average=12000.0, power_valid=False)

    _run(ctrl, overloaded, at(20))  # off 20 min, cap is 30
    assert hass.services.calls == []

    _run(ctrl, overloaded, at(31))
    assert hass.services.calls == [("switch", "turn_on", "switch.a")]


def test_max_off_zero_disables_the_cap() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": _record("2026-10-13T19:00:00+02:00")})
    ctrl.max_off_minutes = 0

    _run(ctrl, _snap(projected_average=12000.0), at(30))  # off for 90 min

    assert hass.services.calls == []


def test_device_power_is_learned_from_the_drop_of_total_power() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    _run(ctrl, _snap(current_power=9500.0), at(0, 10))  # shed at 9.5 kW
    assert ctrl._shed["switch.a"]["power_before"] == 9500.0

    _run(ctrl, _snap(current_power=6500.0, projected_average=7000.0), at(0, 40))

    assert ctrl._shed["switch.a"]["estimated_w"] == 3000.0
    assert ctrl._device_power == {"switch.a": 3000.0}
    assert ctrl._store.saved["device_power"] == {"switch.a": 3000.0}


def test_restore_waits_until_the_learned_power_fits() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": {**_record(), "estimated_w": 3000.0}})

    # limit 7000, hysteresis 500: room is 1500 W at 5000 W -> a 3 kW load does not fit
    _run(ctrl, _snap(projected_average=5000.0), at(20))
    assert hass.services.calls == []

    _run(ctrl, _snap(projected_average=3000.0), at(21))  # room 3500 W
    assert hass.services.calls == [("switch", "turn_on", "switch.a")]


def test_restore_uses_remembered_power_when_this_shed_was_not_measured() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "off")
    ctrl = _controller(hass, {"switch.a": _record()})
    ctrl._device_power["switch.a"] = 3000.0

    _run(ctrl, _snap(projected_average=5000.0), at(20))

    assert hass.services.calls == []


def test_learned_power_survives_restart() -> None:
    hass = FakeHass()
    ctrl = _controller(hass)
    ctrl._device_power = {"switch.a": 1200.0}
    _await(ctrl.async_save())
    saved = ctrl._store.saved

    ctrl2 = _controller(hass)

    async def _load():
        return saved

    ctrl2._store.async_load = _load
    _await(ctrl2.async_load())
    assert ctrl2._device_power == {"switch.a": 1200.0}


def test_no_shedding_before_the_stabilization_time() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)
    assert ctrl.stabilization_seconds == 300.0  # default: first 5 minutes

    _run(ctrl, _snap(elapsed_seconds=120.0), at(2))
    assert hass.services.calls == []

    ctrl.stabilization_seconds = 60.0  # user lowers it
    _run(ctrl, _snap(elapsed_seconds=120.0), at(2, 5))
    assert _offs(hass) == ["switch.a"]


def test_shed_cooldown_covers_the_smoothing_window() -> None:
    """The smoothed projection needs time to show a shed, so wait for it."""
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "on")
    coord = SimpleNamespace(
        async_request_refresh_ui=lambda: None,
        current_power=0.0,
        smoothing_seconds=30.0,
    )
    ctrl = controller_mod.LoadShedController(
        hass, coord, "entry", ["switch.a", "switch.b"], []
    )

    _run(ctrl, _snap(), at(5))
    _run(ctrl, _snap(), at(5, 20))  # still within smoothing + 5 s
    assert _offs(hass) == ["switch.a"]

    _run(ctrl, _snap(), at(5, 40))
    assert _offs(hass) == ["switch.a", "switch.b"]


# --- controller: less common paths -----------------------------------------


def test_set_devices_forgets_removed_shed_devices() -> None:
    hass = FakeHass()
    ctrl = _controller(
        hass,
        {"switch.a": _record(), "switch.b": _record()},
        devices=("switch.a", "switch.b"),
    )

    ctrl.set_devices(["switch.a", ""])

    assert ctrl.devices == ["switch.a"]
    assert ctrl.shed_entity_ids == ["switch.a"]


def test_set_notify_services_drops_blanks() -> None:
    ctrl = _controller(FakeHass())
    ctrl.set_notify_services(["notify.x", ""])
    assert ctrl.notify_services == ["notify.x"]


def test_evaluation_is_skipped_while_another_one_runs() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass)

    async def scenario() -> None:
        async with ctrl._lock:
            await ctrl.async_evaluate(_snap())

    _clock["now"] = at(5)
    _await(scenario())

    assert hass.services.calls == []


def test_shed_skips_missing_unavailable_and_already_off_devices() -> None:
    hass = FakeHass()
    hass.states.set("switch.unavailable", "unavailable")
    hass.states.set("switch.off", "off")
    hass.states.set("switch.on", "on")
    ctrl = _controller(
        hass,
        devices=("switch.missing", "switch.unavailable", "switch.off", "switch.on"),
    )

    _run(ctrl, _snap(), at(5))

    assert _offs(hass) == ["switch.on"]


def test_record_is_dropped_when_the_device_was_switched_on_meanwhile() -> None:
    """Right after a shed the override check waits; the restore check does not."""
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    ctrl = _controller(hass, {"switch.a": _record("2026-10-13T20:00:00+02:00")})
    ctrl.min_off_minutes = 0

    _run(ctrl, _snap(tariff_block=3, projected_average=0.0), at(0, 10))

    assert ctrl._shed == {}
    assert hass.services.calls == []
    assert ctrl._store.saved["shed"] == {}


def test_restore_all_skips_devices_that_are_already_on() -> None:
    hass = FakeHass()
    hass.states.set("switch.a", "on")
    hass.states.set("switch.b", "off")
    ctrl = _controller(
        hass,
        {"switch.a": _record(), "switch.b": _record()},
        devices=("switch.a", "switch.b"),
    )

    _await(ctrl.async_restore_all(reason="test"))

    assert hass.services.calls == [("switch", "turn_on", "switch.b")]
    assert ctrl._shed == {}


def test_unreadable_off_since_is_handled(monkeypatch) -> None:
    ctrl = _controller(FakeHass())
    ctrl.max_off_minutes = 60
    _clock["now"] = at(5)

    assert ctrl._seconds_off({}) is None
    assert ctrl._seconds_off({"off_since": "garbage"}) is None
    assert ctrl._seconds_off({"off_since": "2026-10-13T19:00:00"}) is None  # naive
    monkeypatch.setattr(controller_mod.dt_util, "parse_datetime", lambda raw: None)
    assert ctrl._seconds_off({"off_since": "2026-10-13T19:00:00+02:00"}) is None

    assert ctrl._min_off_elapsed({}) is True
    assert ctrl._max_off_elapsed({}) is False  # unknown age: never force
    ctrl.max_off_minutes = 0
    assert ctrl._max_off_elapsed(_record()) is False


def test_climate_previous_state_is_captured_and_restored() -> None:
    hass = FakeHass()
    state = SimpleNamespace(state="heat", attributes={"temperature": 21})
    previous = controller_mod.LoadShedController._capture_previous("climate.ac", state)
    assert previous == {"domain": "climate", "state": "heat", "hvac_mode": "heat", "temperature": 21}
    ctrl = _controller(hass)

    assert _await(ctrl._turn_on("climate.ac", previous)) is True

    assert [c[1] for c in hass.services.calls] == ["set_hvac_mode", "set_temperature"]
    assert hass.services.payloads[1]["temperature"] == 21


def test_climate_without_a_remembered_mode_is_just_turned_on() -> None:
    hass = FakeHass()
    ctrl = _controller(hass)

    _await(ctrl._turn_on("climate.ac", {"state": "off"}))

    assert hass.services.calls == [("climate", "turn_on", "climate.ac")]


def test_water_heater_falls_back_to_turn_on() -> None:
    hass = FakeHass()
    hass.services.fail_services.add("set_operation_mode")
    ctrl = _controller(hass)

    assert _await(ctrl._turn_on("water_heater.tank", {"operation_mode": "eco"})) is True

    assert [c[1] for c in hass.services.calls] == ["set_operation_mode", "turn_on"]


def test_failed_turn_on_is_reported() -> None:
    hass = FakeHass()
    hass.services.fail_services.add("turn_on")
    ctrl = _controller(hass)

    assert _await(ctrl._turn_on("switch.a", {})) is False


def test_failing_notify_service_does_not_break_the_controller() -> None:
    hass = FakeHass()
    hass.services.fail_services.add("mobile_app_gone")
    ctrl = _controller(hass)
    ctrl.notify_services = ["notify.mobile_app_gone"]

    _await(ctrl._notify("title", "message"))  # must not raise

    assert hass.services.calls == [("notify", "mobile_app_gone", None)]


# --- coordinator: edge cases --------------------------------------------------


def test_unknown_block_is_ignored_when_setting_agreed_power() -> None:
    coord, _ = make_coordinator()
    coord.set_agreed_kw(9, 5.0)
    assert 9 not in coord.agreed_kw


@pytest.mark.parametrize(("state", "block"), [("abc", 0), ("7", 0), ("0", 0), ("2.0", 2), ("5", 5)])
def test_tariff_block_parsing(state: str, block: int) -> None:
    coord, hass = make_coordinator()
    hass.states.set("sensor.t", state)
    assert coord._read_tariff() == block


def test_non_numeric_power_is_invalid() -> None:
    coord, hass = make_coordinator()
    hass.states.set("sensor.p", "abc")

    assert coord._read_power() is None
    assert coord._read_power(warn=False) is None
    assert coord._compute().power_valid is False


def test_missing_power_keeps_the_last_value_unless_forced() -> None:
    coord, hass = make_coordinator(power="2000")
    hass.states.set("sensor.p", "unavailable")

    coord._ingest_power(at(1))
    assert coord._last_power == 2000.0  # carried over

    coord._ingest_power(at(2), force=True)
    assert coord._last_power == 0.0  # forced: nothing known


def test_sample_history_is_trimmed() -> None:
    coord, hass = make_coordinator()
    coord._samples = [(at(0), 1000.0)] * 2000

    advance(coord, hass, at(1))

    assert len(coord._samples) == 1000  # 2001 > 2000 -> the newest 1000 are kept


def test_window_is_created_on_first_use() -> None:
    coord, _ = make_coordinator(now=at(5))
    coord._window_start = None
    coord._ingest_power(at(5, 10))
    assert coord._window_start == at(0)

    coord._window_start = None
    assert coord._compute().window_start == at(0)


def test_notify_target_given_as_a_single_string() -> None:
    cfg = {const.CONF_NOTIFY: "notify.mobile_app_a"}
    assert notify_helpers.notify_targets(cfg) == ["notify.mobile_app_a"]
