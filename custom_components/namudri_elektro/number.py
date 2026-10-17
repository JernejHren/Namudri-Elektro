"""Number entities: dogovorjena moč and load-shedding tuning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_NAME,
    DEFAULT_AGREED_KW,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MIN_OFF_MINUTES,
    DEFAULT_STABILIZATION_SECONDS,
    DEFAULT_THRESHOLD_W,
    DOMAIN,
    VERSION,
)
from .coordinator import NamudriCoordinator


@dataclass(frozen=True, kw_only=True)
class ShedNumberSpec:
    """Spec for a shed-tuning number entity."""

    key: str
    translation_key: str
    name: str
    default: float
    min_value: float
    max_value: float
    step: float
    unit: str | None
    device_class: NumberDeviceClass | None
    apply: Callable[[NamudriCoordinator, float], None]


def _apply_threshold(coord: NamudriCoordinator, value: float) -> None:
    coord.update_shed_settings(threshold_w=value)


def _apply_hysteresis(coord: NamudriCoordinator, value: float) -> None:
    coord.update_shed_settings(hysteresis_w=value)


def _apply_min_off(coord: NamudriCoordinator, value: float) -> None:
    coord.update_shed_settings(min_off_minutes=value)


def _apply_stabilization(coord: NamudriCoordinator, value: float) -> None:
    coord.update_shed_settings(stabilization_seconds=value)


SHED_NUMBERS: tuple[ShedNumberSpec, ...] = (
    ShedNumberSpec(
        key="shed_threshold",
        translation_key="shed_threshold",
        name="Shed threshold",
        default=DEFAULT_THRESHOLD_W,
        min_value=0.0,
        max_value=5000.0,
        step=50.0,
        unit=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        apply=_apply_threshold,
    ),
    ShedNumberSpec(
        key="shed_hysteresis",
        translation_key="shed_hysteresis",
        name="Restore hysteresis",
        default=DEFAULT_HYSTERESIS_W,
        min_value=0.0,
        max_value=5000.0,
        step=50.0,
        unit=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        apply=_apply_hysteresis,
    ),
    ShedNumberSpec(
        key="min_off_minutes",
        translation_key="min_off_minutes",
        name="Minimum off time",
        default=DEFAULT_MIN_OFF_MINUTES,
        min_value=1.0,
        max_value=720.0,
        step=1.0,
        unit=UnitOfTime.MINUTES,
        device_class=None,
        apply=_apply_min_off,
    ),
    ShedNumberSpec(
        key="stabilization_seconds",
        translation_key="stabilization_seconds",
        name="Projection stabilization",
        default=DEFAULT_STABILIZATION_SECONDS,
        min_value=0.0,
        max_value=900.0,
        step=5.0,
        unit=UnitOfTime.SECONDS,
        device_class=None,
        apply=_apply_stabilization,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: NamudriCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME, "Namudri Elektro")
    entities: list[RestoreNumber] = [
        AgreedPowerNumber(coordinator, entry, name, block) for block in range(1, 6)
    ]
    entities.extend(
        ShedTuningNumber(coordinator, entry, name, spec) for spec in SHED_NUMBERS
    )
    async_add_entities(entities)


class AgreedPowerNumber(RestoreNumber):
    """Configurable dogovorjena moč for one tariff block."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_native_min_value = 0.0
    _attr_native_max_value = 50.0
    _attr_native_step = 0.1

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
        block: int,
    ) -> None:
        """Initialize number entity."""
        self._coordinator = coordinator
        self._block = block
        self._attr_translation_key = f"agreed_power_t{block}"
        self._attr_name = f"Agreed power T{block}"
        self._attr_unique_id = f"{entry.entry_id}_agreed_t{block}"
        self._attr_native_value = DEFAULT_AGREED_KW[block]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Namudri",
            model="Elektro",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Restore last value and sync coordinator."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = float(last.native_value)
        self._coordinator.set_agreed_kw(self._block, self._attr_native_value or 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set dogovorjena moč in kW."""
        self._attr_native_value = value
        self._coordinator.set_agreed_kw(self._block, value)
        self.async_write_ha_state()


class ShedTuningNumber(RestoreNumber):
    """Load-shedding tuning parameter."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: NamudriCoordinator,
        entry: ConfigEntry,
        device_name: str,
        spec: ShedNumberSpec,
    ) -> None:
        """Initialize shed tuning number."""
        self._coordinator = coordinator
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_name = spec.name
        self._attr_unique_id = f"{entry.entry_id}_{spec.key}"
        self._attr_native_value = spec.default
        self._attr_native_min_value = spec.min_value
        self._attr_native_max_value = spec.max_value
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Namudri",
            model="Elektro",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Restore and apply to controller."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = float(last.native_value)
        self._spec.apply(self._coordinator, self._attr_native_value or self._spec.default)

    async def async_set_native_value(self, value: float) -> None:
        """Update tuning value."""
        self._attr_native_value = value
        self._spec.apply(self._coordinator, value)
        self.async_write_ha_state()
