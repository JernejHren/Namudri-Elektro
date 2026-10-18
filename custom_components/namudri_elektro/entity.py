"""Shared entity helpers for Namudri Elektro."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DEFAULT_NAME, DOMAIN, VERSION
from .coordinator import NamudriConfigEntry, NamudriCoordinator


def entry_config(entry: NamudriConfigEntry) -> Mapping[str, Any]:
    """Setup data merged with the options saved later (options win)."""
    return {**entry.data, **entry.options}


def build_device_info(entry: NamudriConfigEntry) -> DeviceInfo:
    """The one device all Namudri entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry_config(entry).get(CONF_NAME) or DEFAULT_NAME,
        manufacturer="Namudri",
        model="Elektro",
        sw_version=VERSION,
    )


class NamudriCoordinatorEntity(CoordinatorEntity[NamudriCoordinator]):
    """Entity that follows the coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: NamudriCoordinator, entry: NamudriConfigEntry, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = build_device_info(entry)
