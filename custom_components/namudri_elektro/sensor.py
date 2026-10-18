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
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
)
from .coordinator import NamudriConfigEntry, NamudriCoordinator, WindowSnapshot
from .entity import NamudriCoordinatorEntity

# Coordinator-driven, nothing is polled: no limit on parallel updates
PARALLEL_UPDATES = 0


def _power_ok(data: WindowSnapshot) -> bool:
    return data.power_valid


def _tariff_ok(data: WindowSnapshot) -> bool:
    return data.tariff_block != 0


def _power_and_tariff_ok(data: WindowSnapshot) -> bool:
    return data.power_valid and data.tariff_block != 0


def _always(data: WindowSnapshot) -> bool:
    return True


@dataclass(frozen=True, kw_only=True)
class NamudriSensorDescription(SensorEntityDescription):
    """Sensor description with value extractor."""

    value_fn: Callable[[WindowSnapshot], float | int | None]
    attrs_fn: Callable[[WindowSnapshot], dict] | None = None
    # False -> the entity is shown as unavailable (its source sensor is gone);
    # a value of None with available=True is shown as unknown
    available_fn: Callable[[WindowSnapshot], bool] = _power_ok


SENSORS: tuple[NamudriSensorDescription, ...] = (
    NamudriSensorDescription(
        key="projected_power",
        translation_key="projected_power",
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
        available_fn=_always,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.completed_average,
    ),
    NamudriSensorDescription(
        key="agreed_power",
        translation_key="agreed_power",
        available_fn=_tariff_ok,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.agreed_power_w if d.agreed_power_w > 0 else None,
        attrs_fn=lambda d: {ATTR_TARIFF_BLOCK: d.tariff_block},
    ),
    NamudriSensorDescription(
        key="headroom",
        translation_key="headroom",
        available_fn=_power_and_tariff_ok,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.headroom_w,
        attrs_fn=lambda d: {
            ATTR_TARIFF_BLOCK: d.tariff_block,
            ATTR_AGREED_POWER_W: d.agreed_power_w,
            "projected_average": d.projected_average,
        },
    ),
    NamudriSensorDescription(
        key="tariff_block",
        translation_key="tariff_block",
        available_fn=_tariff_ok,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.tariff_block or None,
    ),
    NamudriSensorDescription(
        key="shed_device_count",
        translation_key="shed_device_count",
        available_fn=_always,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.shed_devices),
        attrs_fn=lambda d: {ATTR_SHED_DEVICES: d.shed_devices},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NamudriConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        NamudriSensor(coordinator, entry, description) for description in SENSORS
    )


class NamudriSensor(NamudriCoordinatorEntity, SensorEntity):
    """Namudri monitoring sensor."""

    entity_description: NamudriSensorDescription

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: NamudriConfigEntry,
        description: NamudriSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Unavailable while the source sensor it depends on is unavailable."""
        return super().available and self.entity_description.available_fn(
            self.coordinator.data
        )

    @property
    def native_value(self) -> float | int | None:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
