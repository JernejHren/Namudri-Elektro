"""Sensor platform for Namudri Elektro."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_AGREED_POWER_W,
    ATTR_CURRENT_POWER,
    ATTR_ELAPSED_SECONDS,
    ATTR_MEASUREMENT_COUNT,
    ATTR_REMAINING_SECONDS,
    ATTR_RUNNING_AVERAGE,
    ATTR_SHED_DEVICES,
    ATTR_TARIFF_BLOCK,
    ATTR_WINDOW_START,
    CONF_NAME,
    DOMAIN,
    VERSION,
)
from .coordinator import NamudriCoordinator, WindowSnapshot


@dataclass(frozen=True, kw_only=True)
class NamudriSensorDescription(SensorEntityDescription):
    """Sensor description with value extractor."""

    value_fn: Callable[[WindowSnapshot], float | int | None]
    attrs_fn: Callable[[WindowSnapshot], dict] | None = None


SENSORS: tuple[NamudriSensorDescription, ...] = (
    NamudriSensorDescription(
        key="projected_power",
        translation_key="projected_power",
        name="Projected 15 min power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.projected_average,
        attrs_fn=lambda d: {
            ATTR_WINDOW_START: d.window_start.isoformat(),
            ATTR_ELAPSED_SECONDS: d.elapsed_seconds,
            ATTR_REMAINING_SECONDS: d.remaining_seconds,
            ATTR_CURRENT_POWER: d.current_power,
            ATTR_RUNNING_AVERAGE: d.running_average,
            ATTR_MEASUREMENT_COUNT: d.measurement_count,
            "window_complete": d.window_complete,
        },
    ),
    NamudriSensorDescription(
        key="running_average",
        translation_key="running_average",
        name="Running 15 min average",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.running_average,
        attrs_fn=lambda d: {
            ATTR_WINDOW_START: d.window_start.isoformat(),
            ATTR_ELAPSED_SECONDS: d.elapsed_seconds,
            ATTR_CURRENT_POWER: d.current_power,
            ATTR_MEASUREMENT_COUNT: d.measurement_count,
        },
    ),
    NamudriSensorDescription(
        key="completed_window",
        translation_key="completed_window",
        name="Completed 15 min average",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.completed_average,
    ),
    NamudriSensorDescription(
        key="agreed_power",
        translation_key="agreed_power",
        name="Agreed power (current block)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: (
            d.agreed_power_w if d.tariff_block and d.agreed_power_w > 0 else None
        ),
        attrs_fn=lambda d: {ATTR_TARIFF_BLOCK: d.tariff_block},
    ),
    NamudriSensorDescription(
        key="headroom",
        translation_key="headroom",
        name="Headroom",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.headroom_w if d.tariff_block else None,
        attrs_fn=lambda d: {
            ATTR_TARIFF_BLOCK: d.tariff_block,
            ATTR_AGREED_POWER_W: d.agreed_power_w,
            "projected_average": d.projected_average,
        },
    ),
    NamudriSensorDescription(
        key="tariff_block",
        translation_key="tariff_block",
        name="Tariff block",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.tariff_block or None,
    ),
    NamudriSensorDescription(
        key="shed_device_count",
        translation_key="shed_device_count",
        name="Shed device count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.shed_devices),
        attrs_fn=lambda d: {ATTR_SHED_DEVICES: d.shed_devices},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator: NamudriCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME, "Namudri Elektro")
    async_add_entities(
        NamudriSensor(coordinator, entry, name, description) for description in SENSORS
    )


class NamudriSensor(CoordinatorEntity[NamudriCoordinator], SensorEntity):
    """Namudri monitoring sensor."""

    entity_description: NamudriSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
        description: NamudriSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Namudri",
            model="Elektro",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> float | int | None:
        """Return sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return attributes."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
