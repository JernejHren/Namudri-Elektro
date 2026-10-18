"""Constants for Namudri Elektro."""

from __future__ import annotations

DOMAIN = "namudri_elektro"
VERSION = "0.4.0"

CONF_POWER_SENSOR = "power_sensor"
CONF_TARIFF_SENSOR = "tariff_sensor"
CONF_NAME = "name"
DEFAULT_NAME = "Namudri Elektro"
CONF_DEVICES = "devices"
CONF_NOTIFY = "notify_services"  # list of notify targets picked from a list
CONF_NOTIFY_1 = "notify_1"  # legacy free-text fields (still read for old entries)
CONF_NOTIFY_2 = "notify_2"

WINDOW_SECONDS = 900  # Slovenian network billing: 15-minute windows
UPDATE_INTERVAL_SECONDS = 5
SHED_ACTION_COOLDOWN_SECONDS = 8
# Restore one device at a time with this pause, so loads do not return in a burst
RESTORE_STAGGER_SECONDS = 30

# Load shedding only in expensive blocks
SHED_TARIFF_BLOCKS = frozenset({1, 2})

# Default dogovorjena obračunska moč (kW) per tariff block.
# 0 = "not set": no limit is known, so nothing is reported as over the limit
# and nothing is shed until the user sets T1–T5.
DEFAULT_AGREED_KW = {
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 0.0,
    5: 0.0,
}

DEFAULT_THRESHOLD_W = 500.0
DEFAULT_HYSTERESIS_W = 500.0
DEFAULT_MIN_OFF_MINUTES = 15.0
# No shedding during the first N seconds of a window: early on the projection
# extrapolates a few seconds of consumption over most of the window.
DEFAULT_STABILIZATION_SECONDS = 300.0
# Safety cap: a shed device is restored after this long whatever the projection
# (0 = no cap).
DEFAULT_MAX_OFF_MINUTES = 120.0
# The power assumed for the rest of the window is averaged over this many
# seconds (0 = use the instantaneous power).
DEFAULT_SMOOTHING_SECONDS = 30.0
# Power sensor state older than this is treated as unavailable (0 = no check).
DEFAULT_SENSOR_TIMEOUT_SECONDS = 120.0

# After turn_off, wait this long for the entity to report "off" (slow cloud devices)
STATE_CONFIRM_TIMEOUT_SECONDS = 10.0
STATE_CONFIRM_INTERVAL_SECONDS = 0.5
# A freshly shed device that still reports "on" is not taken for a user override
# during this grace period (its state may simply lag)
SHED_RECORD_GRACE_SECONDS = 30
# The power of a shed device is learned from the drop of the total power,
# measured this long after the shed (and not later than the max age)
LEARN_DELAY_SECONDS = 15
LEARN_MAX_AGE_SECONDS = 120
# Notify when the power sensor has been unusable for this long
POWER_ALERT_AFTER_SECONDS = 60
# A power sensor that stays unusable this long becomes a repair issue.
POWER_ISSUE_AFTER_SECONDS = 600
ISSUE_POWER_SENSOR = "power_sensor_unavailable"
ISSUE_AGREED_POWER = "agreed_power_not_set"

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
