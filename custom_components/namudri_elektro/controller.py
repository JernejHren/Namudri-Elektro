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
    DEFAULT_MAX_OFF_MINUTES,
    DEFAULT_MIN_OFF_MINUTES,
    DEFAULT_STABILIZATION_SECONDS,
    DEFAULT_THRESHOLD_W,
    LEARN_DELAY_SECONDS,
    LEARN_MAX_AGE_SECONDS,
    POWER_ALERT_AFTER_SECONDS,
    RESTORE_STAGGER_SECONDS,
    SHED_ACTION_COOLDOWN_SECONDS,
    SHED_RECORD_GRACE_SECONDS,
    SHED_TARIFF_BLOCKS,
    STATE_CONFIRM_INTERVAL_SECONDS,
    STATE_CONFIRM_TIMEOUT_SECONDS,
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
        self.max_off_minutes = DEFAULT_MAX_OFF_MINUTES
        self.stabilization_seconds = DEFAULT_STABILIZATION_SECONDS

        self._shed: dict[str, dict[str, Any]] = {}
        # entity_id -> power (W) the device freed the last time it was shed
        self._device_power: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._last_action: datetime | None = None
        self._action_cooldown: float = float(SHED_ACTION_COOLDOWN_SECONDS)
        self._power_invalid_since: datetime | None = None
        self._power_alert_sent = False
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
        """Restore persisted shed list and learned device powers after restart."""
        data = await self._store.async_load()
        if not data:
            return
        powers = data.get("device_power", {})
        if isinstance(powers, dict):
            self._device_power = {
                eid: float(value)
                for eid, value in powers.items()
                if isinstance(value, (int, float))
            }
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
        """Persist shed list and learned device powers."""
        await self._store.async_save(
            {"shed": self._shed, "device_power": self._device_power}
        )

    def diagnostics(self) -> dict[str, Any]:
        """Settings and state for the diagnostics download."""
        return {
            "enabled": self.enabled,
            "devices": list(self.devices),
            "threshold_w": self.threshold_w,
            "hysteresis_w": self.hysteresis_w,
            "min_off_minutes": self.min_off_minutes,
            "max_off_minutes": self.max_off_minutes,
            "stabilization_seconds": self.stabilization_seconds,
            "shed": {eid: dict(rec) for eid, rec in self._shed.items()},
            "learned_device_power_w": dict(self._device_power),
        }

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

    async def async_force_shed(self) -> str | None:
        """Shed the next device in priority order now (ignores the projection)."""
        if not self.enabled:
            _LOGGER.warning("force_shed ignored: load shedding is disabled")
            return None
        async with self._lock:
            shed = await self._shed_next(
                power_before=getattr(self.coordinator, "current_power", None)
            )
            if shed:
                self._mark_action(self._shed_cooldown())
                await self.async_save()
                await self._notify(
                    "Namudri — IZKLOP",
                    f"Ročni izklop (force_shed): {shed}",
                )
                self.coordinator.async_request_refresh_ui()
            return shed

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

        await self._check_power_health(snapshot)
        await self._learn_device_power(snapshot)

        if self._in_cooldown():
            return

        tariff = snapshot.tariff_block
        projected = snapshot.projected_average
        limit = snapshot.agreed_power_w

        # Safety net first: a device never stays off longer than the max off
        # time, whatever the projection, tariff or sensor state is.
        if self._shed and self.max_off_minutes > 0:
            if await self._restore_step(
                tariff, projected, limit, budget_w=None, overdue_only=True
            ):
                return

        # Unknown tariff block (sensor unavailable/invalid): do nothing and keep
        # the current state — it could be an expensive block.
        if tariff == 0:
            return

        # Outside shed blocks: restore what we turned off
        if tariff not in SHED_TARIFF_BLOCKS:
            if self._shed:
                await self._restore_step(tariff, projected, limit, budget_w=None)
            return

        if limit <= 0 or not self.devices:
            return

        # Power sensor unavailable/stale: the projection cannot be trusted
        if not snapshot.power_valid:
            return

        # Window started mid-way (restart/reload): the projection rests on
        # backfilled energy, so neither shed nor restore until the next window.
        if not snapshot.window_complete:
            return

        # Shed: projection above limit + threshold, after stabilization
        if (
            snapshot.elapsed_seconds >= self.stabilization_seconds
            and projected > (limit + self.threshold_w)
        ):
            shed = await self._shed_next(power_before=snapshot.current_power)
            if shed:
                self._mark_action(self._shed_cooldown())
                await self.async_save()
                await self._notify(
                    "Namudri — IZKLOP",
                    f"Izklopljeno: {shed}\n"
                    f"Projekcija: {projected:.0f} W > meja {limit:.0f} W "
                    f"(blok {tariff})",
                )
                self.coordinator.async_request_refresh_ui()
            return

        # Restore: projection below limit − hysteresis, and the device must fit
        # into the room that is left (its learned power is added to the projection)
        if self._shed and projected < (limit - self.hysteresis_w):
            await self._restore_step(
                tariff,
                projected,
                limit,
                budget_w=limit - self.hysteresis_w - projected,
            )

    def _in_cooldown(self) -> bool:
        if self._last_action is None:
            return False
        return (dt_util.now() - self._last_action) < timedelta(
            seconds=self._action_cooldown
        )

    def _mark_action(self, cooldown_seconds: float) -> None:
        """Remember an action and how long to pause before the next one."""
        self._last_action = dt_util.now()
        self._action_cooldown = float(cooldown_seconds)

    def _smoothing_seconds(self) -> float:
        return float(getattr(self.coordinator, "smoothing_seconds", 0.0) or 0.0)

    def _shed_cooldown(self) -> float:
        """Pause after a shed: long enough for the smoothed power to follow."""
        smoothing = self._smoothing_seconds()
        return max(
            float(SHED_ACTION_COOLDOWN_SECONDS),
            smoothing + 5.0 if smoothing > 0 else 0.0,
        )

    def _restore_cooldown(self) -> float:
        """Pause after a restore (staggered, and long enough for smoothing)."""
        smoothing = self._smoothing_seconds()
        return max(
            float(RESTORE_STAGGER_SECONDS),
            smoothing + 5.0 if smoothing > 0 else 0.0,
        )

    async def _check_power_health(self, snapshot: WindowSnapshot) -> None:
        """Tell the user once when the power sensor stays unusable."""
        if snapshot.power_valid:
            self._power_invalid_since = None
            if self._power_alert_sent:
                self._power_alert_sent = False
                await self._notify(
                    "Namudri — senzor moči", "Senzor moči spet deluje."
                )
            return

        now = dt_util.now()
        if self._power_invalid_since is None:
            self._power_invalid_since = now
        if (
            not self._power_alert_sent
            and (now - self._power_invalid_since).total_seconds()
            >= POWER_ALERT_AFTER_SECONDS
        ):
            self._power_alert_sent = True
            await self._notify(
                "Namudri — senzor moči",
                "Senzor moči ni na voljo ali se ne osvežuje. "
                "Izklapljanje je začasno ustavljeno.",
            )

    async def _learn_device_power(self, snapshot: WindowSnapshot) -> None:
        """Learn what a shed device freed from the drop of the total power."""
        if not snapshot.power_valid:
            return
        changed = False
        for entity_id, rec in self._shed.items():
            if rec.get("estimated_w") is not None:
                continue
            before = rec.get("power_before")
            seconds_off = self._seconds_off(rec)
            if before is None or seconds_off is None:
                continue
            if not (LEARN_DELAY_SECONDS <= seconds_off <= LEARN_MAX_AGE_SECONDS):
                continue
            estimate = round(max(0.0, float(before) - float(snapshot.current_power)), 1)
            rec["estimated_w"] = estimate
            self._device_power[entity_id] = estimate
            _LOGGER.info("Shedding %s freed about %.0f W", entity_id, estimate)
            changed = True
        if changed:
            await self.async_save()

    def _estimated_power(self, entity_id: str, rec: dict[str, Any]) -> float:
        """Power (W) a restored device is expected to draw again."""
        estimate = rec.get("estimated_w")
        if estimate is None:
            estimate = self._device_power.get(entity_id, 0.0)
        return float(estimate)

    async def _restore_step(
        self,
        tariff: int,
        projected: float,
        limit: float,
        *,
        budget_w: float | None,
        overdue_only: bool = False,
    ) -> list[str]:
        """Restore at most one device, then pause so loads return gradually."""
        restored = await self._restore_ready(
            budget_w=budget_w, overdue_only=overdue_only
        )
        if not restored:
            return []
        self._mark_action(self._restore_cooldown())
        await self.async_save()
        if overdue_only:
            message = (
                f"Vklopljeno (največji čas izklopa {self.max_off_minutes:.0f} min): "
                f"{', '.join(restored)}"
            )
        else:
            message = (
                f"Vklopljeno: {', '.join(restored)}\n"
                f"Projekcija: {projected:.0f} W (meja {limit:.0f} W, blok {tariff})"
            )
        await self._notify("Namudri — VKLOP", message)
        self.coordinator.async_request_refresh_ui()
        return restored

    async def _prune_user_overrides(self) -> None:
        """If user turned a shed device back on, drop it from our list."""
        changed = False
        for entity_id in list(self._shed):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if not self._is_on(entity_id, state.state):
                continue
            # A device we shed a moment ago may simply report its new state late
            seconds_off = self._seconds_off(self._shed[entity_id])
            if seconds_off is not None and seconds_off < SHED_RECORD_GRACE_SECONDS:
                continue
            _LOGGER.info(
                "User overrode shed on %s — removing from auto-shed list",
                entity_id,
            )
            self._shed.pop(entity_id, None)
            changed = True
        if changed:
            await self.async_save()
            self.coordinator.async_request_refresh_ui()

    async def _shed_next(self, power_before: float | None = None) -> str | None:
        """Turn off the next on device in priority order (first priority first)."""
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
                "power_before": (
                    round(float(power_before), 1) if power_before is not None else None
                ),
            }
            _LOGGER.info("Auto-shed %s", entity_id)
            return entity_id
        return None

    async def _restore_ready(
        self, *, budget_w: float | None, overdue_only: bool = False
    ) -> list[str]:
        """Restore at most one device.

        Normally a device must have met its min-off time and fit into
        ``budget_w`` (the room left below the limit; None = no check). With
        ``overdue_only`` only devices past the max off time qualify.

        Callers save the shed list when something was restored; records that
        were only dropped (device gone / switched on by the user) are saved here.
        """
        restored: list[str] = []
        pruned = False
        # Restore in reverse priority (last shed first)
        for entity_id in reversed(list(self._shed.keys())):
            rec = self._shed[entity_id]
            if overdue_only:
                if not self._max_off_elapsed(rec):
                    continue
            else:
                if not self._min_off_elapsed(rec):
                    continue
                if (
                    budget_w is not None
                    and self._estimated_power(entity_id, rec) > budget_w
                ):
                    continue
            state = self.hass.states.get(entity_id)
            if state is None:
                self._shed.pop(entity_id, None)
                pruned = True
                continue
            # Only restore if still off (user didn't change)
            if self._is_on(entity_id, state.state):
                self._shed.pop(entity_id, None)
                pruned = True
                continue

            ok = await self._turn_on(entity_id, rec.get("previous") or {})
            if ok:
                self._shed.pop(entity_id, None)
                restored.append(entity_id)
                _LOGGER.info("Auto-restored %s", entity_id)
                break  # one device per step; the next one follows after a pause
        if pruned and not restored:
            await self.async_save()
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

    def _seconds_off(self, rec: dict[str, Any]) -> float | None:
        """Seconds since the device was shed (None if unknown)."""
        raw = rec.get("off_since")
        if not raw:
            return None
        try:
            off_since = dt_util.parse_datetime(raw)
            if off_since is None:
                return None
            if off_since.tzinfo is None:
                off_since = dt_util.as_local(off_since)
            return (dt_util.now() - off_since).total_seconds()
        except (TypeError, ValueError):
            return None

    def _min_off_elapsed(self, rec: dict[str, Any]) -> bool:
        seconds = self._seconds_off(rec)
        if seconds is None:
            return True
        return seconds / 60.0 >= self.min_off_minutes

    def _max_off_elapsed(self, rec: dict[str, Any]) -> bool:
        if self.max_off_minutes <= 0:
            return False
        seconds = self._seconds_off(rec)
        if seconds is None:
            return False
        return seconds / 60.0 >= self.max_off_minutes

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
        elif domain == "water_heater":
            # The state of a water heater is its operation mode (eco, performance…)
            previous["operation_mode"] = state.state
        return previous

    async def _turn_off(self, entity_id: str) -> bool:
        """Send turn_off; True if the call was accepted.

        The state of slow (e.g. cloud) devices can take seconds to follow, so
        success is the service call, not the state. When the state does not
        confirm in time the device is still recorded: it is dropped again by
        the user-override check if it really stayed on.
        """
        domain = entity_id.split(".", 1)[0]
        try:
            await self.hass.services.async_call(
                domain,
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to turn off %s", entity_id)
            return False
        if not await self._wait_until_off(entity_id):
            _LOGGER.warning(
                "%s did not report 'off' within %.0f s after turn_off; keeping it "
                "in the shed list (dropped again if it stays on)",
                entity_id,
                STATE_CONFIRM_TIMEOUT_SECONDS,
            )
        return True

    async def _wait_until_off(self, entity_id: str) -> bool:
        """Wait for the entity to report off, up to the confirm timeout."""
        waited = 0.0
        while True:
            state = self.hass.states.get(entity_id)
            if (
                state is not None
                and state.state not in ("unknown", "unavailable")
                and not self._is_on(entity_id, state.state)
            ):
                return True
            if waited >= STATE_CONFIRM_TIMEOUT_SECONDS:
                return False
            await asyncio.sleep(STATE_CONFIRM_INTERVAL_SECONDS)
            waited += STATE_CONFIRM_INTERVAL_SECONDS

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
            elif domain == "water_heater" and previous.get("operation_mode") not in (
                None,
                "off",
                "unknown",
                "unavailable",
            ):
                try:
                    await self.hass.services.async_call(
                        "water_heater",
                        "set_operation_mode",
                        {
                            "entity_id": entity_id,
                            "operation_mode": previous["operation_mode"],
                        },
                        blocking=True,
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "set_operation_mode failed for %s, falling back to turn_on",
                        entity_id,
                    )
                    await self.hass.services.async_call(
                        "water_heater",
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
