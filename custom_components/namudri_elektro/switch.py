"""Switch platform — master enable for load shedding."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME, DOMAIN, VERSION
from .coordinator import NamudriCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator: NamudriCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME, "Namudri Elektro")
    async_add_entities([LoadSheddingSwitch(coordinator, entry, name)])


class LoadSheddingSwitch(SwitchEntity, RestoreEntity):
    """Enable/disable automatic load shedding."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "load_shedding"
    _attr_name = "Load shedding"

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
    ) -> None:
        """Initialize switch."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_load_shedding"
        self._attr_is_on = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Namudri",
            model="Elektro",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous on/off state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "off":
            self._attr_is_on = False
            await self._coordinator.controller.async_set_enabled(False)
        else:
            self._attr_is_on = True
            self._coordinator.controller.enabled = True

    async def async_turn_on(self, **kwargs) -> None:
        """Enable shedding."""
        self._attr_is_on = True
        await self._coordinator.controller.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable shedding and restore auto-shed devices."""
        self._attr_is_on = False
        await self._coordinator.controller.async_set_enabled(False)
        self.async_write_ha_state()
