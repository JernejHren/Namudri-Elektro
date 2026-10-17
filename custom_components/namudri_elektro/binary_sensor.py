"""Binary sensors for Namudri Elektro."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_SHED_DEVICES, CONF_NAME, DOMAIN, VERSION
from .coordinator import NamudriCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator: NamudriCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME, "Namudri Elektro")
    async_add_entities(
        [
            OverLimitBinarySensor(coordinator, entry, name),
            SheddingActiveBinarySensor(coordinator, entry, name),
        ]
    )


class _NamudriBinarySensor(CoordinatorEntity[NamudriCoordinator], BinarySensorEntity):
    """Base binary sensor on the Namudri device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
        unique_suffix: str,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Namudri",
            model="Elektro",
            sw_version=VERSION,
        )


class OverLimitBinarySensor(_NamudriBinarySensor):
    """True when projected 15-min average exceeds agreed power."""

    _attr_translation_key = "over_agreed_power"
    _attr_name = "Over agreed power"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
    ) -> None:
        """Initialize over-limit sensor."""
        super().__init__(coordinator, entry, device_name, "over_limit")

    @property
    def is_on(self) -> bool | None:
        """Return True if projected average exceeds agreed power."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.over_limit


class SheddingActiveBinarySensor(_NamudriBinarySensor):
    """True while at least one device is auto-shed by Namudri."""

    _attr_translation_key = "shedding_active"
    _attr_name = "Shedding active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
    ) -> None:
        """Initialize shedding-active sensor."""
        super().__init__(coordinator, entry, device_name, "shedding_active")

    @property
    def is_on(self) -> bool | None:
        """Return True if auto-shed list is non-empty."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.shedding_active

    @property
    def extra_state_attributes(self) -> dict | None:
        """List of currently auto-shed entities."""
        if self.coordinator.data is None:
            return None
        return {ATTR_SHED_DEVICES: self.coordinator.data.shed_devices}
