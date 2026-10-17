"""Services for Namudri Elektro."""

from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import NamudriCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_RESTORE_ALL = "restore_all"

SERVICE_RESTORE_ALL_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""

    if hass.services.has_service(DOMAIN, SERVICE_RESTORE_ALL):
        return

    async def async_restore_all(call: ServiceCall) -> None:
        """Restore auto-shed devices for one or all entries."""
        entry_id = call.data.get("entry_id")
        entries = hass.data.get(DOMAIN, {})
        targets: list[NamudriCoordinator] = []
        if entry_id:
            coord = entries.get(entry_id)
            if coord:
                targets.append(coord)
            else:
                _LOGGER.warning("Unknown entry_id for restore_all: %s", entry_id)
                return
        else:
            targets = list(entries.values())

        for coordinator in targets:
            await coordinator.controller.async_restore_all(reason="service")

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_ALL,
        async_restore_all,
        schema=SERVICE_RESTORE_ALL_SCHEMA,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove services when last entry unloads."""
    if DOMAIN in hass.data and hass.data[DOMAIN]:
        return
    if hass.services.has_service(DOMAIN, SERVICE_RESTORE_ALL):
        hass.services.async_remove(DOMAIN, SERVICE_RESTORE_ALL)
