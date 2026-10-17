"""Load shedding controller for tariff blocks 1 and 2."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MIN_OFF_MINUTES,
    DEFAULT_STABILIZATION_SECONDS,
    DEFAULT_THRESHOLD_W,
    DOMAIN,
    SHED_ACTION_COOLDOWN_SECONDS,
    SHED_TARIFF_BLOCKS,
    STORAGE_KEY,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from .coordinator import NamudriCoordinator, WindowSnapshot

_LOGGER = logging.getLogger(__name__)


class LoadShedController:
    """Shed/restore flexible loads based on projected 15-min power."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NamudriCoordinator,
        entry_id: str,
        devices: list[str],
        notify_services: list[str],
    ) -> None:
        """Initialize controller."""
        self.hass = hass
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.devices = [d for d in devices if d]
        self.notify_services = [n for n in notify_services if n]

        self.enabled = True
        self.threshold_w = DEFAULT_THRESHOLD_W
        self.hysteresis_w = DEFAULT_HYSTERESIS_W
        self.min_off_minutes = DEFAULT_MIN_OFF_MINUTES
        self.stabilization_seconds = DEFAULT_STABILIZATION_SECONDS

        self._shed: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._last_action: datetime | None = None
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")

    @property
    def shed_entity_ids(self) -> list[str]:
        """Entities currently auto-shed by this controller."""
        return list(self._shed.keys())

    @property
    def shedding_active(self) -> bool:
        """True if at least one device is auto-shed."""
        return bool(self._shed)

    async def async_load(self) -> None:
        """Restore persisted shed list after restart."""
        data = await self._store.async_load()
        if not data:
            return
        records = data.get("shed", {})
        if isinstance(records, dict):
            self._shed = {
                eid: rec
                for eid, rec in records.items()
                if isinstance(rec, dict) and eid in self.devices
            }
            if self._shed:
                _LOGGER.info(
                    "Restored %s auto-shed device(s): %s",
                    len(self._shed),
                    ", ".join(self._shed),
                )

    async def async_save(self) -> None:
        """Persist shed list."""
        await self._store.async_save({"shed": self._shed})

    def set_devices(self, devices: list[str]) -> None:
        """Update controllable device list."""
        self.devices = [d for d in devices if d]
        # Drop shed records for removed devices (do not auto-restore here)
        removed = [e for e in self._shed if e not in self.devices]
        for eid in removed:
            self._shed.pop(eid, None)

    def set_notify_services(self, services: list[str]) -> None:
        """Update notify service targets."""
        self.notify_services = [n for n in services if n]

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable/disable shedding; restore all when turning off."""
        self.enabled = enabled
        if not enabled and self._shed:
            await self.async_restore_all(reason="disabled")
        self.coordinator.async_request_refresh_ui()

    async def async_evaluate(self, snapshot: WindowSnapshot) -> None:
        """Evaluate shed/restore against current projection."""
        if self._lock.locked():
            return
        async with self._lock:
            await self._evaluate(snapshot)

    async def _evaluate(self, snapshot: WindowSnapshot) -> None:
        await self._prune_user_overrides()

        if not self.enabled:
            return

        if self._in_cooldown():
            return

        tariff = snapshot.tariff_block
        projected = snapshot.projected_average
        limit = snapshot.agreed_power_w

        # Outside shed blocks: restore what we turned off
        if tariff not in SHED_TARIFF_BLOCKS:
            if self._shed:
                await self._restore_ready(projected, limit, force_limit_ok=True)
            return

        if limit <= 0 or not self.devices:
            return

        # Shed: projection above limit + threshold, after stabilization
        if (
            snapshot.elapsed_seconds >= self.stabilization_seconds
            and projected > (limit + self.threshold_w)
        ):
            shed = await self._shed_next()
            if shed:
                self._last_action = dt_util.now()
                await self.async_save()
                await self._notify(
                    "Namudri — IZKLOP",
                    f"Izklopljeno: {shed}\n"
                    f"Projekcija: {projected:.0f} W > meja {limit:.0f} W "
                    f"(blok {tariff})",
                )
                self.coordinator.async_request_refresh_ui()
            return

        # Restore: projection below limit − hysteresis
        if self._shed and projected < (limit - self.hysteresis_w):
            restored = await self._restore_ready(projected, limit, force_limit_ok=False)
            if restored:
                self._last_action = dt_util.now()
                await self.async_save()
                await self._notify(
                    "Namudri — VKLOP",
                    f"Vklopljeno: {', '.join(restored)}\n"
                    f"Projekcija: {projected:.0f} W < meja {limit:.0f} W "
                    f"(blok {tariff})",
                )
                self.coordinator.async_request_refresh_ui()

    def _in_cooldown(self) -> bool:
        if self._last_action is None:
            return False
        return (dt_util.now() - self._last_action) < timedelta(
            seconds=SHED_ACTION_COOLDOWN_SECONDS
        )

    async def _prune_user_overrides(self) -> None:
        """If user turned a shed device back on, drop it from our list."""
        changed = False
        for entity_id in list(self._shed):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if self._is_on(entity_id, state.state):
                _LOGGER.info(
                    "User overrode shed on %s — removing from auto-shed list",
                    entity_id,
                )
                self._shed.pop(entity_id, None)
                changed = True
        if changed:
            await self.async_save()
            self.coordinator.async_request_refresh_ui()

    async def _shed_next(self) -> str | None:
        """Turn off the next on device in priority order."""
        for entity_id in self.devices:
            if entity_id in self._shed:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            if not self._is_on(entity_id, state.state):
                # Already off (manual) — leave alone
                continue

            previous = self._capture_previous(entity_id, state)
            ok = await self._turn_off(entity_id)
            if not ok:
                continue

            self._shed[entity_id] = {
                "off_since": dt_util.now().isoformat(),
                "previous": previous,
            }
            _LOGGER.info("Auto-shed %s", entity_id)
            return entity_id
        return None

    async def _restore_ready(
        self,
        projected: float,
        limit: float,
        *,
        force_limit_ok: bool,
    ) -> list[str]:
        """Restore devices that meet min-off; one per cycle unless force all outside blocks."""
        restored: list[str] = []
        # Restore in reverse priority (last shed first)
        for entity_id in reversed(list(self._shed.keys())):
            rec = self._shed[entity_id]
            if not self._min_off_elapsed(rec):
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                self._shed.pop(entity_id, None)
                continue
            # Only restore if still off (user didn't change)
            if self._is_on(entity_id, state.state):
                self._shed.pop(entity_id, None)
                continue

            ok = await self._turn_on(entity_id, rec.get("previous") or {})
            if ok:
                self._shed.pop(entity_id, None)
                restored.append(entity_id)
                _LOGGER.info("Auto-restored %s", entity_id)
                if not force_limit_ok:
                    # One restore per cycle inside shed blocks
                    break
        return restored

    async def async_restore_all(self, reason: str = "") -> None:
        """Restore every auto-shed device (ignores min-off when disabling)."""
        async with self._lock:
            restored: list[str] = []
            for entity_id in list(reversed(self._shed.keys())):
                rec = self._shed.pop(entity_id, {}) or {}
                state = self.hass.states.get(entity_id)
                if state and self._is_on(entity_id, state.state):
                    continue
                if await self._turn_on(entity_id, rec.get("previous") or {}):
                    restored.append(entity_id)
            await self.async_save()
            if restored:
                _LOGGER.info(
                    "Restored all shed devices (%s): %s", reason, ", ".join(restored)
                )
                await self._notify(
                    "Namudri — VKLOP",
                    f"Obnovljeno ({reason or 'all'}): {', '.join(restored)}",
                )
            self.coordinator.async_request_refresh_ui()

    def _min_off_elapsed(self, rec: dict[str, Any]) -> bool:
        raw = rec.get("off_since")
        if not raw:
            return True
        try:
            off_since = dt_util.parse_datetime(raw)
            if off_since is None:
                return True
            if off_since.tzinfo is None:
                off_since = dt_util.as_local(off_since)
            minutes = (dt_util.now() - off_since).total_seconds() / 60.0
            return minutes >= self.min_off_minutes
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _is_on(entity_id: str, state: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        if domain == "switch":
            return state == "on"
        return state not in ("off", "unknown", "unavailable")

    @staticmethod
    def _capture_previous(entity_id: str, state) -> dict[str, Any]:
        domain = entity_id.split(".", 1)[0]
        previous: dict[str, Any] = {"domain": domain, "state": state.state}
        if domain == "climate":
            previous["hvac_mode"] = state.state
            if "temperature" in state.attributes:
                previous["temperature"] = state.attributes.get("temperature")
        return previous

    async def _turn_off(self, entity_id: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        service = "turn_off"
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to turn off %s", entity_id)
            return False
        await asyncio.sleep(0.5)
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return not self._is_on(entity_id, state.state)

    async def _turn_on(self, entity_id: str, previous: dict[str, Any]) -> bool:
        domain = entity_id.split(".", 1)[0]
        try:
            if domain == "climate":
                hvac_mode = previous.get("hvac_mode") or previous.get("state")
                if hvac_mode and hvac_mode not in ("off", "unknown", "unavailable"):
                    data: dict[str, Any] = {
                        "entity_id": entity_id,
                        "hvac_mode": hvac_mode,
                    }
                    await self.hass.services.async_call(
                        "climate", "set_hvac_mode", data, blocking=True
                    )
                    temp = previous.get("temperature")
                    if temp is not None:
                        await self.hass.services.async_call(
                            "climate",
                            "set_temperature",
                            {"entity_id": entity_id, "temperature": temp},
                            blocking=True,
                        )
                else:
                    await self.hass.services.async_call(
                        "climate",
                        "turn_on",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
            else:
                await self.hass.services.async_call(
                    domain,
                    "turn_on",
                    {"entity_id": entity_id},
                    blocking=True,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to turn on %s", entity_id)
            return False
        await asyncio.sleep(0.5)
        return True

    async def _notify(self, title: str, message: str) -> None:
        for service in self.notify_services:
            # Accept "notify.mobile_app_x" or "mobile_app_x"
            target = service
            if target.startswith("notify."):
                target = target.split(".", 1)[1]
            try:
                await self.hass.services.async_call(
                    "notify",
                    target,
                    {"title": title, "message": message},
                    blocking=False,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Notify failed for %s", service)
