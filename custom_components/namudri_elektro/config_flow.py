"""Config flow for Namudri Elektro."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_DEVICES,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_1,
    CONF_NOTIFY_2,
    CONF_POWER_SENSOR,
    CONF_TARIFF_SENSOR,
    DEFAULT_NAME,
    DOMAIN,
)
from .coordinator import NamudriConfigEntry
from .notify_helpers import build_notify_options, notify_targets


def _sensor_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
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


def _notify_service_names(hass: HomeAssistant) -> list[str]:
    """Names of the registered notify services (without the domain)."""
    getter = getattr(hass.services, "async_services_for_domain", None)
    if getter is not None:
        return list(getter("notify"))
    return list(hass.services.async_services().get("notify", {}))  # pragma: no cover


def _shedding_schema(
    hass: HomeAssistant, defaults: dict[str, Any] | None = None
) -> vol.Schema:
    defaults = defaults or {}
    devices = defaults.get(CONF_DEVICES) or []
    selected = notify_targets(defaults)
    options = [
        SelectOptionDict(value=value, label=label)
        for value, label in build_notify_options(
            _notify_service_names(hass), selected
        )
    ]
    return vol.Schema(
        {
            vol.Optional(CONF_DEVICES, default=devices): EntitySelector(
                EntitySelectorConfig(
                    domain=["switch", "climate", "water_heater"],
                    multiple=True,
                )
            ),
            vol.Optional(CONF_NOTIFY, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _validate_sensors(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Check the chosen sensors before the entry is created (form errors)."""
    errors: dict[str, str] = {}
    power = user_input[CONF_POWER_SENSOR]
    tariff = user_input[CONF_TARIFF_SENSOR]
    if power == tariff:
        errors["base"] = "same_sensor"
        return errors
    if hass.states.get(power) is None:
        errors[CONF_POWER_SENSOR] = "sensor_not_found"
    if hass.states.get(tariff) is None:
        errors[CONF_TARIFF_SENSOR] = "sensor_not_found"
    return errors


def _normalize_shedding(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize devices/notify fields and drop the legacy text fields."""
    out = dict(data)
    out[CONF_NOTIFY] = notify_targets(out)
    for key in (CONF_NOTIFY_1, CONF_NOTIFY_2):
        out.pop(key, None)
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
    ) -> ConfigFlowResult:
        """Initial setup step (sensors)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_POWER_SENSOR]}_{user_input[CONF_TARIFF_SENSOR]}"
            )
            self._abort_if_unique_id_configured()
            errors = _validate_sensors(self.hass, user_input)
            if not errors:
                self._sensors = user_input
                return await self.async_step_shedding()

        return self.async_show_form(
            step_id="user",
            data_schema=_sensor_schema(user_input),
            errors=errors,
        )

    async def async_step_shedding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional load-shedding devices (can also be set later in options)."""
        if user_input is not None:
            data = _normalize_shedding({**self._sensors, **user_input})
            return self.async_create_entry(
                title=data[CONF_NAME],
                data=data,
            )

        return self.async_show_form(
            step_id="shedding",
            data_schema=_shedding_schema(self.hass),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the name and the two source sensors without deleting the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = (
                f"{user_input[CONF_POWER_SENSOR]}_{user_input[CONF_TARIFF_SENSOR]}"
            )
            errors = _validate_sensors(self.hass, user_input)
            if not errors and any(
                other.entry_id != entry.entry_id and other.unique_id == unique_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")
            if not errors:
                sensors = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_POWER_SENSOR: user_input[CONF_POWER_SENSOR],
                    CONF_TARIFF_SENSOR: user_input[CONF_TARIFF_SENSOR],
                }
                # Options win over the setup data, so they must follow too.
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=user_input[CONF_NAME],
                    data={**entry.data, **sensors},
                    options={
                        **entry.options,
                        **{k: v for k, v in sensors.items() if k in entry.options},
                    },
                )

        defaults = user_input or {**entry.data, **entry.options}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_sensor_schema(defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: NamudriConfigEntry,
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
    ) -> ConfigFlowResult:
        """Sensors step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_sensors(self.hass, user_input)
            if not errors:
                self._sensors = user_input
                return await self.async_step_shedding()

        defaults = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_sensor_schema(defaults),
            errors=errors,
        )

    async def async_step_shedding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Load shedding devices and notifications."""
        if user_input is not None:
            data = _normalize_shedding({**self._sensors, **user_input})
            return self.async_create_entry(title="", data=data)

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="shedding",
            data_schema=_shedding_schema(self.hass, defaults),
        )
