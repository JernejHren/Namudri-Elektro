"""Namudri Elektro — Slovenian network power monitoring for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_POWER_SENSOR, CONF_TARIFF_SENSOR, DOMAIN, PLATFORMS
from .coordinator import NamudriConfigEntry, NamudriCoordinator
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services (once, independent of config entries)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NamudriConfigEntry) -> bool:
    """Set up Namudri Elektro from a config entry."""
    config = {**entry.data, **entry.options}

    # The sensors can be missing for a while after a restart: retry later.
    for key in (CONF_POWER_SENSOR, CONF_TARIFF_SENSOR):
        entity_id = config[key]
        if hass.states.get(entity_id) is None:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="source_entity_not_found",
                translation_placeholders={"entity_id": entity_id},
            )

    coordinator = NamudriCoordinator(hass, entry.entry_id, config, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )
    # Number/switch entities have restored their values by now; only from this
    # point may the controller act on the projection.
    coordinator.async_mark_ready()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: NamudriConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NamudriConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    if unload_ok:
        entry.runtime_data.async_stop()
    return unload_ok
