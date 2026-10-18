"""Diagnostics for Namudri Elektro."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY, CONF_NOTIFY_1, CONF_NOTIFY_2
from .coordinator import NamudriConfigEntry
from .entity import entry_config

# Notification targets reveal the names of the user's phones
TO_REDACT = {CONF_NOTIFY, CONF_NOTIFY_1, CONF_NOTIFY_2}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NamudriConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    return {
        "config": async_redact_data(dict(entry_config(entry)), TO_REDACT),
        "snapshot": asdict(snapshot) if snapshot is not None else None,
        "agreed_power_kw": coordinator.agreed_kw,
        "power_smoothing_seconds": coordinator.smoothing_seconds,
        "power_timeout_seconds": coordinator.power_timeout_seconds,
        "controller": coordinator.controller.diagnostics(),
    }
