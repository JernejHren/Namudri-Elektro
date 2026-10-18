"""Binary sensors for Namudri Elektro."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_SHED_DEVICES
from .coordinator import NamudriConfigEntry, NamudriCoordinator
from .entity import NamudriCoordinatorEntity

# Coordinator-driven, nothing is polled: no limit on parallel updates
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NamudriConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            OverLimitBinarySensor(coordinator, entry),
            SheddingActiveBinarySensor(coordinator, entry),
        ]
    )


class OverLimitBinarySensor(NamudriCoordinatorEntity, BinarySensorEntity):
    """True when projected 15-min average exceeds agreed power."""

    _attr_translation_key = "over_agreed_power"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: NamudriCoordinator, entry: NamudriConfigEntry
    ) -> None:
        """Initialize over-limit sensor."""
        super().__init__(coordinator, entry, "over_limit")

    @property
    def available(self) -> bool:
        """Unavailable while the power or the tariff sensor is unavailable."""
        data = self.coordinator.data
        return super().available and data.power_valid and data.tariff_block != 0

    @property
    def is_on(self) -> bool | None:
        """Projected average above agreed power (unknown if no limit is set)."""
        return self.coordinator.data.over_limit


class SheddingActiveBinarySensor(NamudriCoordinatorEntity, BinarySensorEntity):
    """True while at least one device is auto-shed by Namudri."""

    _attr_translation_key = "shedding_active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self, coordinator: NamudriCoordinator, entry: NamudriConfigEntry
    ) -> None:
        """Initialize shedding-active sensor."""
        super().__init__(coordinator, entry, "shedding_active")

    @property
    def is_on(self) -> bool | None:
        """Return True if auto-shed list is non-empty."""
        return self.coordinator.data.shedding_active

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """List of currently auto-shed entities."""
        return {ATTR_SHED_DEVICES: self.coordinator.data.shed_devices}
