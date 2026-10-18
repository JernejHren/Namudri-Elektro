"""Config flow and options flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.namudri_elektro.const import (
    CONF_DEVICES,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_1,
    CONF_POWER_SENSOR,
    CONF_TARIFF_SENSOR,
    DOMAIN,
)

from .conftest import POWER, TARIFF, set_sources

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SENSORS = {
    CONF_NAME: "My house",
    CONF_POWER_SENSOR: POWER,
    CONF_TARIFF_SENSOR: TARIFF,
}


async def _start(hass: HomeAssistant, sensors: dict | None = None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], sensors or SENSORS
    )


async def test_full_flow_creates_entry(hass: HomeAssistant) -> None:
    set_sources(hass)
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "send_message")

    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await _start(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "shedding"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_DEVICES: ["switch.boiler"],
                CONF_NOTIFY: ["notify.mobile_app_phone"],
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My house"
    assert result["data"] == {
        **SENSORS,
        CONF_DEVICES: ["switch.boiler"],
        CONF_NOTIFY: ["notify.mobile_app_phone"],
    }


async def test_shedding_step_can_be_left_empty(hass: HomeAssistant) -> None:
    set_sources(hass)

    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICES] == []
    assert result["data"][CONF_NOTIFY] == []


async def test_send_message_is_not_offered_as_notify_target(
    hass: HomeAssistant,
) -> None:
    set_sources(hass)
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "send_message")

    result = await _start(hass)
    options = result["data_schema"].schema[CONF_NOTIFY].config["options"]

    assert [option["value"] for option in options] == ["notify.mobile_app_phone"]


@pytest.mark.parametrize(
    ("sensors", "errors"),
    [
        ({CONF_POWER_SENSOR: "sensor.missing"}, {CONF_POWER_SENSOR: "sensor_not_found"}),
        ({CONF_TARIFF_SENSOR: "sensor.missing"}, {CONF_TARIFF_SENSOR: "sensor_not_found"}),
        ({CONF_TARIFF_SENSOR: POWER}, {"base": "same_sensor"}),
    ],
)
async def test_sensor_errors(hass: HomeAssistant, sensors: dict, errors: dict) -> None:
    set_sources(hass)

    result = await _start(hass, {**SENSORS, **sensors})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == errors


async def test_error_can_be_corrected(hass: HomeAssistant) -> None:
    set_sources(hass)
    result = await _start(hass, {**SENSORS, CONF_POWER_SENSOR: "sensor.missing"})
    assert result["errors"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SENSORS)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "shedding"


async def test_same_sensors_cannot_be_configured_twice(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    set_sources(hass)
    mock_entry.add_to_hass(hass)

    result = await _start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_changes_sensors_devices_and_notifications(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    set_sources(hass)
    hass.states.async_set("sensor.other_power", "10", {"device_class": "power"})
    hass.states.async_set("switch.heater", "on")
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "mobile_app_tablet")
    mock_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SENSORS, CONF_POWER_SENSOR: "sensor.other_power"}
    )
    assert result["step_id"] == "shedding"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEVICES: ["switch.heater", "switch.boiler"],
            CONF_NOTIFY: ["notify.mobile_app_tablet"],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[CONF_POWER_SENSOR] == "sensor.other_power"
    assert mock_entry.options[CONF_DEVICES] == ["switch.heater", "switch.boiler"]
    assert mock_entry.options[CONF_NOTIFY] == ["notify.mobile_app_tablet"]


async def test_options_flow_sensor_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    set_sources(hass)
    mock_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SENSORS, CONF_TARIFF_SENSOR: "sensor.missing"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {CONF_TARIFF_SENSOR: "sensor_not_found"}


async def test_options_flow_keeps_legacy_notify_target(hass: HomeAssistant) -> None:
    """Entries made before the notify picker still have the two text fields."""
    set_sources(hass)
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="legacy",
        data={
            CONF_NAME: "Old",
            CONF_POWER_SENSOR: POWER,
            CONF_TARIFF_SENSOR: TARIFF,
            CONF_NOTIFY_1: "notify.mobile_app_old_phone",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], SENSORS)
    # the stored target is offered (marked unavailable) and pre-selected
    options = result["data_schema"].schema[CONF_NOTIFY].config["options"]
    assert {"value": "notify.mobile_app_old_phone", "label": "notify.mobile_app_old_phone (unavailable)"} in options
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY] == ["notify.mobile_app_old_phone"]
    assert CONF_NOTIFY_1 not in entry.options
