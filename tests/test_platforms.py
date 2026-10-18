"""Switch and number entities (restoring state, writing to the controller)."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.namudri_elektro.const import DOMAIN

from .conftest import entity_id_of, set_sources

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_sources(hass)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_master_switch_toggles_the_controller(
    hass: HomeAssistant, init_integration: MockConfigEntry, switch_calls
) -> None:
    controller = init_integration.runtime_data.controller
    master = entity_id_of(hass, init_integration, "switch", "load_shedding")
    assert hass.states.get(master).state == STATE_ON
    assert controller.enabled

    await hass.services.async_call(DOMAIN, "force_shed", {}, blocking=True)
    assert controller.shed_entity_ids == ["switch.boiler"]

    # turning the master switch off puts back what was shed
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": master}, blocking=True
    )
    assert hass.states.get(master).state == STATE_OFF
    assert not controller.enabled
    assert controller.shed_entity_ids == []
    assert hass.states.get("switch.boiler").state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": master}, blocking=True
    )
    assert hass.states.get(master).state == STATE_ON
    assert controller.enabled


async def test_master_switch_restores_off(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    mock_restore_cache(hass, [State("switch.namudri_elektro_load_shedding", STATE_OFF)])

    await _setup(hass, mock_entry)

    assert not mock_entry.runtime_data.controller.enabled
    assert hass.states.get("switch.namudri_elektro_load_shedding").state == STATE_OFF
    await hass.config_entries.async_unload(mock_entry.entry_id)


async def test_master_switch_defaults_to_on(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_entry)

    assert mock_entry.runtime_data.controller.enabled
    await hass.config_entries.async_unload(mock_entry.entry_id)


async def test_agreed_power_number_updates_the_coordinator(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    coordinator = init_integration.runtime_data
    entity = entity_id_of(hass, init_integration, "number", "agreed_t2")
    assert float(hass.states.get(entity).state) == 0.0  # default: not set

    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity, "value": 9.5}, blocking=True
    )

    assert coordinator.get_agreed_kw(2) == 9.5
    assert float(hass.states.get(entity).state) == 9.5
    assert hass.states.get(entity).attributes["unit_of_measurement"] == "kW"


@pytest.mark.parametrize(
    ("key", "value", "check"),
    [
        ("shed_threshold", 800, lambda c: c.controller.threshold_w == 800),
        ("shed_hysteresis", 250, lambda c: c.controller.hysteresis_w == 250),
        ("min_off_minutes", 20, lambda c: c.controller.min_off_minutes == 20),
        ("max_off_minutes", 0, lambda c: c.controller.max_off_minutes == 0),
        ("stabilization_seconds", 120, lambda c: c.controller.stabilization_seconds == 120),
        ("power_smoothing_seconds", 0, lambda c: c.smoothing_seconds == 0),
        ("sensor_timeout_seconds", 0, lambda c: c.power_timeout_seconds == 0),
    ],
)
async def test_tuning_numbers_reach_the_controller(
    hass: HomeAssistant, init_integration: MockConfigEntry, key, value, check
) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id_of(hass, init_integration, "number", key), "value": value},
        blocking=True,
    )

    assert check(init_integration.runtime_data)


async def test_defaults_of_the_tuning_numbers(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    coordinator = init_integration.runtime_data
    assert coordinator.controller.stabilization_seconds == 300
    assert coordinator.controller.max_off_minutes == 120
    assert coordinator.controller.min_off_minutes == 15
    assert coordinator.smoothing_seconds == 30


async def test_numbers_restore_their_last_value_including_zero(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    def _stored(entity_id: str, value: float, unit: str, high: float, step: float):
        return (
            State(entity_id, str(value)),
            {
                "native_max_value": high,
                "native_min_value": 0.0,
                "native_step": step,
                "native_unit_of_measurement": unit,
                "native_value": value,
            },
        )

    mock_restore_cache_with_extra_data(
        hass,
        [
            _stored("number.namudri_elektro_agreed_power_t1", 7.5, "kW", 50.0, 0.1),
            _stored("number.namudri_elektro_maximum_off_time", 0.0, "min", 1440.0, 5.0),
            _stored("number.namudri_elektro_projection_stabilization", 90.0, "s", 900.0, 10.0),
        ],
    )

    await _setup(hass, mock_entry)

    coordinator = mock_entry.runtime_data
    assert coordinator.get_agreed_kw(1) == 7.5
    assert coordinator.controller.max_off_minutes == 0  # 0 is not "unset"
    assert coordinator.controller.stabilization_seconds == 90
    await hass.config_entries.async_unload(mock_entry.entry_id)
