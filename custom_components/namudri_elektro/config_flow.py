"""Config flow for Namudri Elektro."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_DEVICES,
    CONF_NAME,
    CONF_NOTIFY_1,
    CONF_NOTIFY_2,
    CONF_POWER_SENSOR,
    CONF_TARIFF_SENSOR,
    DOMAIN,
)


def _sensor_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, default=defaults.get(CONF_NAME, "Namudri Elektro")
        ): TextSelector(),
    }
    if defaults.get(CONF_POWER_SENSOR):
        schema[
            vol.Required(CONF_POWER_SENSOR, default=defaults[CONF_POWER_SENSOR])
        ] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="power")
        )
    else:
        schema[vol.Required(CONF_POWER_SENSOR)] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="power")
        )
    if defaults.get(CONF_TARIFF_SENSOR):
        schema[
            vol.Required(CONF_TARIFF_SENSOR, default=defaults[CONF_TARIFF_SENSOR])
        ] = EntitySelector(EntitySelectorConfig(domain="sensor"))
    else:
        schema[vol.Required(CONF_TARIFF_SENSOR)] = EntitySelector(
            EntitySelectorConfig(domain="sensor")
        )
    return vol.Schema(schema)


def _shedding_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    devices = defaults.get(CONF_DEVICES) or []
    return vol.Schema(
        {
            vol.Optional(CONF_DEVICES, default=devices): EntitySelector(
                EntitySelectorConfig(
                    domain=["switch", "climate", "water_heater"],
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_NOTIFY_1, default=defaults.get(CONF_NOTIFY_1) or ""
            ): TextSelector(),
            vol.Optional(
                CONF_NOTIFY_2, default=defaults.get(CONF_NOTIFY_2) or ""
            ): TextSelector(),
        }
    )


def _normalize_shedding(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize devices/notify fields."""
    out = dict(data)
    for key in (CONF_NOTIFY_1, CONF_NOTIFY_2):
        if not out.get(key):
            out[key] = ""
    out[CONF_DEVICES] = list(out.get(CONF_DEVICES) or [])
    return out


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._sensors: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Initial setup step (sensors)."""
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_POWER_SENSOR]}_{user_input[CONF_TARIFF_SENSOR]}"
            )
            self._abort_if_unique_id_configured()
            self._sensors = user_input
            return await self.async_step_shedding()

        return self.async_show_form(step_id="user", data_schema=_sensor_schema())

    async def async_step_shedding(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optional load-shedding devices (can also be set later in options)."""
        if user_input is not None:
            data = _normalize_shedding({**self._sensors, **user_input})
            return self.async_create_entry(
                title=data[CONF_NAME],
                data=data,
            )

        return self.async_show_form(
            step_id="shedding",
            data_schema=_shedding_schema(),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Return options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Configure sensors and load-shedding devices."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._sensors: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Sensors step."""
        if user_input is not None:
            self._sensors = user_input
            return await self.async_step_shedding()

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_sensor_schema(defaults),
        )

    async def async_step_shedding(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Load shedding devices and notifications."""
        if user_input is not None:
            data = _normalize_shedding({**self._sensors, **user_input})
            return self.async_create_entry(title="", data=data)

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="shedding",
            data_schema=_shedding_schema(defaults),
        )
