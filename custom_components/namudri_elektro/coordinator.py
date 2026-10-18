"""Data update coordinator: 15-min power average and projection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICES,
    CONF_POWER_SENSOR,
    CONF_TARIFF_SENSOR,
    DEFAULT_AGREED_KW,
    DEFAULT_SENSOR_TIMEOUT_SECONDS,
    DEFAULT_SMOOTHING_SECONDS,
    DOMAIN,
    UPDATE_INTERVAL_SECONDS,
    WINDOW_SECONDS,
)
from .controller import LoadShedController
from .notify_helpers import notify_targets
from .window import project_average, window_start_for

if TYPE_CHECKING:
    from homeassistant.core import Event, EventStateChangedData

_LOGGER = logging.getLogger(__name__)

type NamudriConfigEntry = ConfigEntry[NamudriCoordinator]


def _seconds_between(start: datetime, end: datetime) -> float:
    """Elapsed seconds between two aware datetimes (DST-safe, uses UTC timestamps)."""
    return end.timestamp() - start.timestamp()


@dataclass
class WindowSnapshot:
    """Computed values for the current 15-minute billing window."""

    window_start: datetime
    elapsed_seconds: float
    remaining_seconds: float
    current_power: float
    running_average: float
    projected_average: float
    completed_average: float | None
    tariff_block: int  # 0 = unknown / invalid tariff sensor
    agreed_power_w: float
    headroom_w: float | None  # None when the limit is unknown
    over_limit: bool | None  # None when the limit is unknown
    measurement_count: int
    shedding_active: bool
    shed_devices: list[str]
    # False when the window began mid-way (restart/reload): energy before the
    # start was backfilled with the current power, so shedding must not trust it.
    window_complete: bool = True
    # False while the power sensor is unavailable or its state is stale
    power_valid: bool = True


class NamudriCoordinator(DataUpdateCoordinator[WindowSnapshot]):
    """Track import power and project end-of-window average."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=None,  # event + timer driven
        )
        self.entry_id = entry_id
        self.power_sensor: str = config[CONF_POWER_SENSOR]
        self.tariff_sensor: str = config[CONF_TARIFF_SENSOR]

        self._agreed_kw: dict[int, float] = dict(DEFAULT_AGREED_KW)
        self._window_start: datetime | None = None
        self._window_complete: bool = True
        self._samples: list[tuple[datetime, float]] = []
        self._energy_ws: float = 0.0  # watt-seconds in current window
        self._last_sample_time: datetime | None = None
        self._last_power: float = 0.0
        self._completed_average: float | None = None
        self._unsub_state: Callable[[], None] | None = None
        self._unsub_interval: Callable[[], None] | None = None
        # Shed evaluation stays off until number/switch entities restored their
        # values (see async_mark_ready), so defaults never trigger an action.
        self._ready: bool = False
        # Availability of the source sensors, to log only changes
        self._power_was_valid: bool = True
        self._tariff_was_valid: bool = True
        # Live tuning (number entities)
        self.power_timeout_seconds: float = DEFAULT_SENSOR_TIMEOUT_SECONDS
        self.smoothing_seconds: float = DEFAULT_SMOOTHING_SECONDS

        devices = list(config.get(CONF_DEVICES) or [])
        notify = notify_targets(config)
        self.controller = LoadShedController(
            hass, self, entry_id, devices, notify
        )

    def set_agreed_kw(self, block: int, value_kw: float) -> None:
        """Update dogovorjena moč for a tariff block (kW)."""
        if block not in self._agreed_kw:
            return
        self._agreed_kw[block] = max(0.0, float(value_kw))
        self._publish()

    @property
    def agreed_kw(self) -> dict[int, float]:
        """Agreed power per tariff block (kW)."""
        return dict(self._agreed_kw)

    def get_agreed_kw(self, block: int) -> float:
        """Return agreed power in kW for block."""
        return self._agreed_kw.get(block, DEFAULT_AGREED_KW.get(block, 0.0))

    def update_shed_settings(
        self,
        *,
        threshold_w: float | None = None,
        hysteresis_w: float | None = None,
        min_off_minutes: float | None = None,
        max_off_minutes: float | None = None,
        stabilization_seconds: float | None = None,
        smoothing_seconds: float | None = None,
        power_timeout_seconds: float | None = None,
    ) -> None:
        """Update live shed tuning parameters."""
        ctrl = self.controller
        if threshold_w is not None:
            ctrl.threshold_w = max(0.0, float(threshold_w))
        if hysteresis_w is not None:
            ctrl.hysteresis_w = max(0.0, float(hysteresis_w))
        if min_off_minutes is not None:
            ctrl.min_off_minutes = max(0.0, float(min_off_minutes))
        if max_off_minutes is not None:
            ctrl.max_off_minutes = max(0.0, float(max_off_minutes))
        if stabilization_seconds is not None:
            ctrl.stabilization_seconds = max(0.0, float(stabilization_seconds))
        if smoothing_seconds is not None:
            self.smoothing_seconds = max(0.0, float(smoothing_seconds))
        if power_timeout_seconds is not None:
            self.power_timeout_seconds = max(0.0, float(power_timeout_seconds))
        self._publish()

    @property
    def current_power(self) -> float:
        """Latest import power in W (not smoothed)."""
        return self._last_power

    @callback
    def async_request_refresh_ui(self) -> None:
        """Push updated snapshot (e.g. after shed list changes)."""
        self.async_set_updated_data(self._compute())

    @callback
    def async_mark_ready(self) -> None:
        """Allow shed evaluation once all entities restored their state."""
        self._ready = True
        self._publish()

    async def async_start(self) -> None:
        """Start listening to power/tariff sensors."""
        await self.controller.async_load()
        now = dt_util.now()
        self._init_window(now)
        self._ingest_power(now, force=True)

        self._unsub_state = async_track_state_change_event(
            self.hass,
            [self.power_sensor, self.tariff_sensor],
            self._handle_state_change,
        )
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._handle_interval,
            timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self._publish()

    @callback
    def async_stop(self) -> None:
        """Stop listeners."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

    @callback
    def _handle_state_change(self, _event: Event[EventStateChangedData]) -> None:
        """React to power or tariff changes."""
        self._ingest_power(dt_util.now())
        self._publish()

    @callback
    def _handle_interval(self, now: datetime) -> None:
        """Periodic sample so sparse sensor updates still integrate."""
        self._ingest_power(dt_util.as_local(now) if now.tzinfo else dt_util.now())
        self._publish()

    @callback
    def _publish(self) -> None:
        """Update listeners and (once ready) schedule shed evaluation."""
        snapshot = self._compute()
        self._log_source_availability(snapshot)
        self.async_set_updated_data(snapshot)
        if self._ready:
            coro = self.controller.async_evaluate(snapshot)
            if self.config_entry is not None:
                self.config_entry.async_create_background_task(
                    self.hass, coro, f"{DOMAIN}_evaluate"
                )
            else:
                self.hass.async_create_task(coro)

    def _log_source_availability(self, snapshot: WindowSnapshot) -> None:
        """Log once when a source sensor becomes unavailable and when it is back."""
        tariff_valid = snapshot.tariff_block != 0
        if self._power_was_valid and not snapshot.power_valid:
            _LOGGER.warning(
                "Power sensor %s is unavailable or its state is stale; "
                "projection and load shedding are paused",
                self.power_sensor,
            )
        elif not self._power_was_valid and snapshot.power_valid:
            _LOGGER.info("Power sensor %s is back", self.power_sensor)
        if self._tariff_was_valid and not tariff_valid:
            _LOGGER.warning(
                "Tariff sensor %s is unavailable or not a block 1-5; "
                "load shedding is paused",
                self.tariff_sensor,
            )
        elif not self._tariff_was_valid and tariff_valid:
            _LOGGER.info("Tariff sensor %s is back", self.tariff_sensor)
        self._power_was_valid = snapshot.power_valid
        self._tariff_was_valid = tariff_valid

    def _read_power(self, *, warn: bool = True) -> float | None:
        """Read current import power in W (export clamped to 0).

        Returns None when the sensor is unavailable, not numeric, or its state
        has not been refreshed for longer than the sensor timeout.
        """
        state = self.hass.states.get(self.power_sensor)
        if state is None or state.state in ("unknown", "unavailable", None, ""):
            return None
        stamp = getattr(state, "last_reported", None) or getattr(
            state, "last_updated", None
        )
        if (
            self.power_timeout_seconds > 0
            and stamp is not None
            and _seconds_between(stamp, dt_util.now()) > self.power_timeout_seconds
        ):
            return None
        try:
            return max(0.0, float(state.state))
        except (TypeError, ValueError):
            if warn:
                _LOGGER.warning(
                    "Invalid power state from %s: %s", self.power_sensor, state.state
                )
            return None

    def _read_tariff(self) -> int:
        """Read current network tariff block 1–5 (0 = unknown/invalid)."""
        state = self.hass.states.get(self.tariff_sensor)
        if state is None or state.state in ("unknown", "unavailable", None, ""):
            return 0
        try:
            block = int(float(state.state))
        except (TypeError, ValueError):
            return 0
        return block if 1 <= block <= 5 else 0

    def _reset_window(
        self, start: datetime, power: float, *, complete: bool
    ) -> None:
        """Begin a window at `start`; `power` is the power in force at `start`."""
        self._window_start = start
        self._window_complete = complete
        self._energy_ws = 0.0
        self._last_sample_time = start
        self._last_power = power
        self._samples = [(start, power)]
        _LOGGER.debug("Window reset at %s (complete=%s)", start, complete)

    def _init_window(self, now: datetime) -> None:
        """Start tracking mid-window (startup/reload).

        The window is anchored at its real start so elapsed time is correct;
        the unknown energy before `now` is backfilled with the current power
        and the window is flagged incomplete.
        """
        start = window_start_for(now)
        power = self._read_power()
        complete = _seconds_between(start, now) <= UPDATE_INTERVAL_SECONDS
        self._reset_window(start, power if power is not None else 0.0, complete=complete)

    def _roll_windows(self, now: datetime) -> None:
        """Close every window that ended before `now`, carrying power over."""
        assert self._window_start is not None
        while _seconds_between(self._window_start, now) >= WINDOW_SECONDS:
            boundary = datetime.fromtimestamp(
                self._window_start.timestamp() + WINDOW_SECONDS, tz=now.tzinfo
            )
            if self._last_sample_time is not None:
                dt = _seconds_between(self._last_sample_time, boundary)
                if dt > 0:
                    self._energy_ws += self._last_power * dt
            if self._window_complete:
                self._completed_average = round(self._energy_ws / WINDOW_SECONDS, 2)
            else:
                # Partial window (started mid-way): its average is not reliable.
                self._completed_average = None
            # The new window starts exactly on the boundary with the power that
            # was in force; the time from boundary to `now` is integrated below.
            self._reset_window(boundary, self._last_power, complete=True)

    def _ingest_power(self, now: datetime, force: bool = False) -> None:
        """Integrate power into the current window; roll over at boundaries."""
        if self._window_start is None:
            self._init_window(now)
            return

        self._roll_windows(now)

        power = self._read_power()
        if power is None and not force:
            power = self._last_power
        elif power is None:
            power = 0.0

        if self._last_sample_time is not None:
            dt = _seconds_between(self._last_sample_time, now)
            if dt > 0:
                self._energy_ws += self._last_power * dt

        self._last_sample_time = now
        self._last_power = power
        self._samples.append((now, power))
        if len(self._samples) > 2000:
            self._samples = self._samples[-1000:]

    def _projection_power(self, now: datetime) -> float:
        """Power assumed for the rest of the window.

        The average over the last ``smoothing_seconds`` (limited to the current
        window), so a short spike does not get extrapolated over minutes.
        With smoothing 0 it is the latest power.
        """
        span = self.smoothing_seconds
        if span <= 0 or not self._samples:
            return self._last_power
        end = now.timestamp()
        start = max(end - span, self._samples[0][0].timestamp())
        if end - start <= 0:
            return self._last_power
        total = 0.0
        newer = end
        # Each sample is the power in force from its time on (step function)
        for sample_time, sample_power in reversed(self._samples):
            begin = max(sample_time.timestamp(), start)
            if newer > begin:
                total += sample_power * (newer - begin)
            newer = begin
            if sample_time.timestamp() <= start:
                break
        return total / (end - start)

    def _compute(self) -> WindowSnapshot:
        """Compute running average, projection, headroom."""
        now = dt_util.now()
        if self._window_start is None:
            self._init_window(now)

        assert self._window_start is not None

        elapsed = _seconds_between(self._window_start, now)
        elapsed = max(0.0, min(elapsed, float(WINDOW_SECONDS)))
        remaining = float(WINDOW_SECONDS) - elapsed

        energy_ws = self._energy_ws
        if self._last_sample_time is not None:
            dt = _seconds_between(self._last_sample_time, now)
            if dt > 0:
                energy_ws += self._last_power * dt

        running_average, projected_average = project_average(
            energy_ws, self._projection_power(now), elapsed
        )

        tariff = self._read_tariff()
        agreed_w = self.get_agreed_kw(tariff) * 1000.0 if tariff else 0.0
        limit_known = tariff != 0 and agreed_w > 0
        headroom = round(agreed_w - projected_average, 2) if limit_known else None
        over_limit = projected_average > agreed_w if limit_known else None

        return WindowSnapshot(
            window_start=self._window_start,
            elapsed_seconds=round(elapsed, 1),
            remaining_seconds=round(remaining, 1),
            current_power=round(self._last_power, 2),
            running_average=round(running_average, 2),
            projected_average=round(projected_average, 2),
            completed_average=self._completed_average,
            tariff_block=tariff,
            agreed_power_w=round(agreed_w, 2),
            headroom_w=headroom,
            over_limit=over_limit,
            measurement_count=len(self._samples),
            shedding_active=self.controller.shedding_active,
            shed_devices=self.controller.shed_entity_ids,
            window_complete=self._window_complete,
            power_valid=self._read_power(warn=False) is not None,
        )
