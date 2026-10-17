"""Constants for Namudri Elektro."""

from __future__ import annotations

DOMAIN = "namudri_elektro"
VERSION = "0.2.0"

CONF_POWER_SENSOR = "power_sensor"
CONF_TARIFF_SENSOR = "tariff_sensor"
CONF_NAME = "name"
CONF_DEVICES = "devices"
CONF_NOTIFY_1 = "notify_1"
CONF_NOTIFY_2 = "notify_2"

WINDOW_SECONDS = 900  # Slovenian network billing: 15-minute windows
UPDATE_INTERVAL_SECONDS = 5
SHED_ACTION_COOLDOWN_SECONDS = 8
# Restore one device at a time with this pause, so loads do not return in a burst
RESTORE_STAGGER_SECONDS = 30
# After an auto-restore the same device may not be shed again for this long
# (anti-flapping; matches the length of one billing window)
RESTORE_GUARD_MINUTES = 15

# Load shedding only in expensive blocks
SHED_TARIFF_BLOCKS = frozenset({1, 2})

# Default dogovorjena obračunska moč (kW) per tariff block
DEFAULT_AGREED_KW = {
    1: 7.0,
    2: 9.0,
    3: 11.0,
    4: 11.0,
    5: 11.0,
}

DEFAULT_THRESHOLD_W = 500.0
DEFAULT_HYSTERESIS_W = 500.0
DEFAULT_MIN_OFF_MINUTES = 15.0
DEFAULT_STABILIZATION_SECONDS = 60.0

ATTR_WINDOW_START = "window_start"
ATTR_ELAPSED_SECONDS = "elapsed_seconds"
ATTR_REMAINING_SECONDS = "remaining_seconds"
ATTR_CURRENT_POWER = "current_power"
ATTR_RUNNING_AVERAGE = "running_average"
ATTR_TARIFF_BLOCK = "tariff_block"
ATTR_AGREED_POWER_W = "agreed_power_w"
ATTR_MEASUREMENT_COUNT = "measurement_count"
ATTR_SHED_DEVICES = "shed_devices"

STORAGE_KEY = f"{DOMAIN}.shed_state"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "number", "binary_sensor", "switch"]
