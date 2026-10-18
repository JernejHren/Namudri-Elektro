"""Services for Namudri Elektro."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import NamudriConfigEntry, NamudriCoordinator

SERVICE_RESTORE_ALL = "restore_all"
SERVICE_FORCE_SHED = "force_shed"

ATTR_ENTRY_ID = "entry_id"

SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[NamudriCoordinator]:
    """Coordinators addressed by the call (one entry_id, or every loaded entry)."""
    loaded: list[NamudriConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    entry_id = call.data.get(ATTR_ENTRY_ID)
    if entry_id:
        loaded = [entry for entry in loaded if entry.entry_id == entry_id]
        if not loaded:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
    elif not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_loaded_entry"
        )
    return [entry.runtime_data for entry in loaded]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration services."""

    async def async_restore_all(call: ServiceCall) -> None:
        """Restore auto-shed devices for one or all entries."""
        for coordinator in _coordinators(hass, call):
            await coordinator.controller.async_restore_all(reason="service")

    async def async_force_shed(call: ServiceCall) -> None:
        """Shed the next device in priority order for one or all entries."""
        shed_any = False
        coordinators = _coordinators(hass, call)
        if not any(coordinator.controller.enabled for coordinator in coordinators):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="shedding_disabled"
            )
        for coordinator in coordinators:
            if await coordinator.controller.async_force_shed():
                shed_any = True
        if not shed_any:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_shed"
            )

    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE_ALL, async_restore_all, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_SHED, async_force_shed, schema=SERVICE_SCHEMA
    )
