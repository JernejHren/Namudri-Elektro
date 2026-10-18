"""Setup, unload, services and the controller wired into a real Home Assistant."""

from __future__ import annotations

from datetime import timedelta
import json

from freezegun.api import FrozenDateTimeFactory
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_dumps
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.namudri_elektro.const import DOMAIN
from custom_components.namudri_elektro.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import POWER, TARIFF, entity_id_of, set_sources

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

WINDOW_START = "2026-10-18 10:00:03+02:00"  # 3 s into a billing window


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _set_agreed(hass: HomeAssistant, entry: MockConfigEntry, kw: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id_of(hass, entry, "number", "agreed_t1"), "value": kw},
        blocking=True,
    )
    await hass.async_block_till_done()


# --- setup / unload -------------------------------------------------------


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.power_sensor == POWER

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("missing", [POWER, TARIFF])
async def test_setup_retries_while_a_source_sensor_is_missing(
    hass: HomeAssistant, mock_entry: MockConfigEntry, missing: str
) -> None:
    set_sources(hass)
    hass.states.async_remove(missing)
    mock_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_change_reloads_the_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        init_integration, options={"devices": []}
    )
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data.controller.devices == []


# --- services -------------------------------------------------------------


async def test_services_are_registered_without_an_entry(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})

    assert hass.services.has_service(DOMAIN, "force_shed")
    assert hass.services.has_service(DOMAIN, "restore_all")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "restore_all", {}, blocking=True)
    assert err.value.translation_key == "no_loaded_entry"


async def test_unknown_entry_id(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "force_shed", {"entry_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_found"


async def test_force_shed_and_restore_all(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    switch_calls: list[tuple[str, str]],
    notify_calls: list[ServiceCall],
) -> None:
    await hass.services.async_call(
        DOMAIN, "force_shed", {"entry_id": init_integration.entry_id}, blocking=True
    )
    assert hass.states.get("switch.boiler").state == STATE_OFF
    assert switch_calls == [("turn_off", "switch.boiler")]
    assert notify_calls  # the phone was told
    shedding = entity_id_of(hass, init_integration, "binary_sensor", "shedding_active")
    assert hass.states.get(shedding).state == STATE_ON

    await hass.services.async_call(DOMAIN, "restore_all", {}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get("switch.boiler").state == STATE_ON
    assert hass.states.get(shedding).state == STATE_OFF


async def test_force_shed_with_nothing_to_shed(
    hass: HomeAssistant, init_integration: MockConfigEntry, switch_calls
) -> None:
    hass.states.async_set("switch.boiler", STATE_OFF)  # already off

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "force_shed", {}, blocking=True)

    assert err.value.translation_key == "nothing_to_shed"


async def test_force_shed_needs_shedding_enabled(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id_of(hass, init_integration, "switch", "load_shedding")},
        blocking=True,
    )
    assert not init_integration.runtime_data.controller.enabled

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "force_shed", {}, blocking=True)

    assert err.value.translation_key == "shedding_disabled"
    assert hass.states.get("switch.boiler").state == STATE_ON


# --- availability ---------------------------------------------------------


async def test_not_set_agreed_power_is_unknown_not_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    for platform, key in (
        ("sensor", "headroom"),
        ("sensor", "agreed_power"),
        ("binary_sensor", "over_limit"),
    ):
        state = hass.states.get(entity_id_of(hass, init_integration, platform, key))
        assert state.state == STATE_UNKNOWN, key


async def test_power_sensor_unavailable_makes_dependent_entities_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _set_agreed(hass, init_integration, 7.0)
    ids = {
        key: entity_id_of(hass, init_integration, platform, key)
        for platform, key in (
            ("sensor", "projected_power"),
            ("sensor", "running_average"),
            ("sensor", "headroom"),
            ("binary_sensor", "over_limit"),
            ("sensor", "tariff_block"),
            ("sensor", "agreed_power"),
            ("sensor", "completed_window"),
            ("binary_sensor", "shedding_active"),
        )
    }
    assert hass.states.get(ids["projected_power"]).state != STATE_UNAVAILABLE

    hass.states.async_set(POWER, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    for key in ("projected_power", "running_average", "headroom", "over_limit"):
        assert hass.states.get(ids[key]).state == STATE_UNAVAILABLE, key
    for key in ("tariff_block", "agreed_power", "completed_window", "shedding_active"):
        assert hass.states.get(ids[key]).state != STATE_UNAVAILABLE, key
    assert caplog.text.count("is unavailable or its state is stale") == 1

    hass.states.async_set(POWER, "2000")
    await hass.async_block_till_done()

    assert hass.states.get(ids["projected_power"]).state != STATE_UNAVAILABLE
    assert caplog.text.count("is back") == 1  # logged once, not on every update


async def test_tariff_sensor_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass.states.async_set(TARIFF, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    for platform, key in (
        ("sensor", "tariff_block"),
        ("sensor", "agreed_power"),
        ("sensor", "headroom"),
        ("binary_sensor", "over_limit"),
    ):
        state = hass.states.get(entity_id_of(hass, init_integration, platform, key))
        assert state.state == STATE_UNAVAILABLE, key
    assert hass.states.get(
        entity_id_of(hass, init_integration, "sensor", "projected_power")
    ).state != STATE_UNAVAILABLE
    assert "Tariff sensor" in caplog.text

    hass.states.async_set(TARIFF, "2")
    await hass.async_block_till_done()
    assert hass.states.get(
        entity_id_of(hass, init_integration, "sensor", "tariff_block")
    ).state == "2"


async def test_stale_power_sensor_becomes_unavailable(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_entry: MockConfigEntry,
    switch_calls,
    notify_calls,
) -> None:
    freezer.move_to(WINDOW_START)
    set_sources(hass)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    projected = entity_id_of(hass, mock_entry, "sensor", "projected_power")
    assert hass.states.get(projected).state != STATE_UNAVAILABLE

    await _tick(hass, freezer, 200)  # > 120 s without a new power value

    assert hass.states.get(projected).state == STATE_UNAVAILABLE
    await hass.config_entries.async_unload(mock_entry.entry_id)


# --- the controller inside Home Assistant ----------------------------------


async def test_overload_sheds_the_device_and_notifies(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_entry: MockConfigEntry,
    switch_calls,
    notify_calls,
) -> None:
    freezer.move_to(WINDOW_START)
    set_sources(hass, power="12000", tariff="1")
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await _set_agreed(hass, mock_entry, 7.0)

    # inside the first 5 minutes of the window nothing is switched off
    await _tick(hass, freezer, 100)
    hass.states.async_set(POWER, "12000")
    assert switch_calls == []

    await _tick(hass, freezer, 250)
    hass.states.async_set(POWER, "12000")
    await _tick(hass, freezer, 6)

    assert switch_calls == [("turn_off", "switch.boiler")]
    assert hass.states.get("switch.boiler").state == STATE_OFF
    assert len(notify_calls) == 1
    assert "switch.boiler" in notify_calls[0].data["message"]
    assert mock_entry.runtime_data.controller.shed_entity_ids == ["switch.boiler"]
    await hass.config_entries.async_unload(mock_entry.entry_id)


async def test_nothing_is_shed_before_entities_restored_their_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_entry: MockConfigEntry,
    switch_calls,
    notify_calls,
) -> None:
    """The coordinator evaluates only once marked ready (after the platforms)."""
    freezer.move_to(WINDOW_START)
    set_sources(hass, power="12000")
    mock_entry.add_to_hass(hass)

    # a restored 7 kW limit and an overload exist from the very first update,
    # yet the first 5 minutes of the window never allow a shed
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert switch_calls == []
    await hass.config_entries.async_unload(mock_entry.entry_id)


# --- entities -------------------------------------------------------------


async def test_entity_registry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, init_integration.entry_id)

    by_platform: dict[str, int] = {}
    for entry in entries:
        by_platform[entry.domain] = by_platform.get(entry.domain, 0) + 1
    assert by_platform == {"sensor": 7, "binary_sensor": 2, "switch": 1, "number": 12}

    for entry in entries:
        if entry.domain == "number":
            assert entry.entity_category is er.EntityCategory.CONFIG, entry.entity_id
        assert entry.translation_key, entry.entity_id

    count = registry.async_get(
        entity_id_of(hass, init_integration, "sensor", "shed_device_count")
    )
    assert count.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert count.entity_category is er.EntityCategory.DIAGNOSTIC


async def test_all_entities_share_one_device(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    from homeassistant.helpers import device_registry as dr

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), init_integration.entry_id
    )
    assert len(devices) == 1
    assert devices[0].name == "Namudri Elektro"


@pytest.mark.parametrize(
    ("language", "name"),
    [
        ("en", "Namudri Elektro Projected 15 min power"),
        ("sl", "Namudri Elektro Projekcija 15 min moči"),
    ],
)
async def test_entity_names_come_from_the_translations(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    switch_calls,
    language: str,
    name: str,
) -> None:
    hass.config.language = language
    set_sources(hass)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id_of(hass, mock_entry, "sensor", "projected_power"))
    assert state.attributes["friendly_name"] == name
    await hass.config_entries.async_unload(mock_entry.entry_id)


async def test_sensor_values_and_attributes(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_entry: MockConfigEntry,
    switch_calls,
    notify_calls,
) -> None:
    freezer.move_to(WINDOW_START)
    set_sources(hass, power="9000", tariff="1")
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await _set_agreed(hass, mock_entry, 7.0)

    projected = hass.states.get(entity_id_of(hass, mock_entry, "sensor", "projected_power"))
    assert float(projected.state) == pytest.approx(9000, abs=1)
    assert projected.attributes["window_complete"] is True
    assert projected.attributes["current_power"] == 9000

    assert float(
        hass.states.get(entity_id_of(hass, mock_entry, "sensor", "agreed_power")).state
    ) == 7000
    assert float(
        hass.states.get(entity_id_of(hass, mock_entry, "sensor", "headroom")).state
    ) == pytest.approx(-2000, abs=1)
    assert (
        hass.states.get(entity_id_of(hass, mock_entry, "binary_sensor", "over_limit")).state
        == STATE_ON
    )
    await hass.config_entries.async_unload(mock_entry.entry_id)


# --- diagnostics ----------------------------------------------------------


async def test_diagnostics_redacts_notification_targets(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["config"]["notify_services"] == "**REDACTED**"
    assert diagnostics["config"]["devices"] == ["switch.boiler"]
    assert diagnostics["snapshot"]["tariff_block"] == 1
    assert diagnostics["controller"]["enabled"] is True
    assert diagnostics["agreed_power_kw"] == {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    json.loads(json_dumps(diagnostics))  # can be serialized for the download


async def test_diagnostics_includes_shed_devices(
    hass: HomeAssistant, init_integration: MockConfigEntry, switch_calls
) -> None:
    await hass.services.async_call(DOMAIN, "force_shed", {}, blocking=True)

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert list(diagnostics["controller"]["shed"]) == ["switch.boiler"]
    assert dt_util.parse_datetime(
        diagnostics["controller"]["shed"]["switch.boiler"]["off_since"]
    )
