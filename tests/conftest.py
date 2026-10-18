"""Shared fixtures for the tests that run against a real Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.namudri_elektro.const import (
    CONF_DEVICES,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_POWER_SENSOR,
    CONF_TARIFF_SENSOR,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

POWER = "sensor.house_power"
TARIFF = "sensor.network_tariff"


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A config entry with two sensors and one shed device."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Namudri Elektro",
        unique_id=f"{POWER}_{TARIFF}",
        data={
            CONF_NAME: "Namudri Elektro",
            CONF_POWER_SENSOR: POWER,
            CONF_TARIFF_SENSOR: TARIFF,
            CONF_DEVICES: ["switch.boiler"],
            CONF_NOTIFY: ["notify.mobile_app_phone"],
        },
    )


FAKE_DEVICE = "switch.boiler"


@pytest.fixture
async def switch_calls(hass: HomeAssistant) -> list[tuple[str, str]]:
    """A fake `switch.boiler` that really changes state; records the calls.

    Every other switch (e.g. the Namudri master switch) still goes to the real
    `switch` services.
    """
    calls: list[tuple[str, str]] = []
    # Load the real domain first, otherwise it replaces the fake services later
    assert await async_setup_component(hass, "switch", {})
    real_jobs = {
        name: service.job
        for name, service in hass.services.async_services_for_domain("switch").items()
    }

    def _handler(service: str, new_state: str) -> Callable:
        async def _call(call: ServiceCall) -> None:
            targets = call.data["entity_id"]
            targets = [targets] if isinstance(targets, str) else list(targets)
            if targets != [FAKE_DEVICE]:
                await real_jobs[service].target(call)
                return
            calls.append((service, FAKE_DEVICE))
            hass.states.async_set(FAKE_DEVICE, new_state)

        return _call

    hass.services.async_register("switch", "turn_off", _handler("turn_off", "off"))
    hass.services.async_register("switch", "turn_on", _handler("turn_on", "on"))
    return calls


@pytest.fixture
def notify_calls(hass: HomeAssistant) -> list[ServiceCall]:
    """Record what is sent to the phone."""
    return async_mock_service(hass, "notify", "mobile_app_phone")


def set_sources(hass: HomeAssistant, power: str = "1500", tariff: str = "1") -> None:
    """Create the two source sensors and the device that may be shed."""
    hass.states.async_set(
        POWER, power, {"device_class": "power", "unit_of_measurement": "W"}
    )
    hass.states.async_set(TARIFF, tariff)
    hass.states.async_set("switch.boiler", "on")


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    notify_calls: list[ServiceCall],
) -> AsyncGenerator[MockConfigEntry]:
    """A loaded config entry (unloaded again after the test)."""
    set_sources(hass)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_entry
    if mock_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()


def entity_id_of(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str
) -> str:
    """Entity id of an entity of the entry, by its unique id key."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, (platform, key)
    return entity_id
