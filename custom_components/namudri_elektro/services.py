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
SERVICE_FORCE_SHED = "force_shed"
SERVICES = (SERVICE_RESTORE_ALL, SERVICE_FORCE_SHED)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
    }
)
SERVICE_RESTORE_ALL_SCHEMA = SERVICE_SCHEMA


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[NamudriCoordinator]:
    """Coordinators addressed by the call (one entry_id, or all)."""
    entry_id = call.data.get("entry_id")
    entries = hass.data.get(DOMAIN, {})
    if not entry_id:
        return list(entries.values())
    coord = entries.get(entry_id)
    if coord is None:
        _LOGGER.warning("Unknown entry_id for %s: %s", call.service, entry_id)
        return []
    return [coord]


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""

    if hass.services.has_service(DOMAIN, SERVICE_RESTORE_ALL):
        return

    async def async_restore_all(call: ServiceCall) -> None:
        """Restore auto-shed devices for one or all entries."""
        for coordinator in _targets(hass, call):
            await coordinator.controller.async_restore_all(reason="service")

    async def async_force_shed(call: ServiceCall) -> None:
        """Shed the next device in priority order for one or all entries."""
        for coordinator in _targets(hass, call):
            await coordinator.controller.async_force_shed()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_ALL,
        async_restore_all,
        schema=SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_SHED,
        async_force_shed,
        schema=SERVICE_SCHEMA,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove services when last entry unloads."""
    if DOMAIN in hass.data and hass.data[DOMAIN]:
        return
    for service in SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
