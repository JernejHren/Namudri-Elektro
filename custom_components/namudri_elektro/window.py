"""Pure helpers for Slovenian 15-minute billing windows (no HA imports)."""

from __future__ import annotations

from datetime import datetime

from .const import WINDOW_SECONDS


def window_start_for(now: datetime) -> datetime:
    """Return start of the billing window (:00, :15, :30, :45)."""
    minute = (now.minute // 15) * 15
    return now.replace(minute=minute, second=0, microsecond=0)


def project_average(
    energy_ws: float,
    current_power_w: float,
    elapsed_seconds: float,
) -> tuple[float, float]:
    """Return (running_average_w, projected_average_w).

    energy_ws: watt-seconds accumulated in the window so far (up to now).
    current_power_w: assumed power for the remainder of the window.
    elapsed_seconds: seconds since window start (0 … WINDOW_SECONDS).
    """
    elapsed = max(0.0, min(float(elapsed_seconds), float(WINDOW_SECONDS)))
    remaining = float(WINDOW_SECONDS) - elapsed
    if elapsed > 0:
        running = energy_ws / elapsed
    else:
        running = float(current_power_w)
    projected = (energy_ws + float(current_power_w) * remaining) / WINDOW_SECONDS
    return running, projected
