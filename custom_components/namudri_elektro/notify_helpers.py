"""Pure helpers for notification targets (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .const import CONF_NOTIFY, CONF_NOTIFY_1, CONF_NOTIFY_2

# notify services that cannot be called with just a title and a message
_EXCLUDED_SERVICES = frozenset({"send_message"})

MOBILE_APP_PREFIX = "notify.mobile_app_"


def normalize_target(service: str | None) -> str:
    """Return a target as ``notify.<service>`` (empty string if blank)."""
    service = (service or "").strip()
    if not service:
        return ""
    return service if service.startswith("notify.") else f"notify.{service}"


def notify_targets(config: Mapping[str, Any]) -> list[str]:
    """Notification targets from the config.

    Uses the list field when present (even if empty, so clearing it works) and
    falls back to the legacy ``notify_1`` / ``notify_2`` text fields.
    """
    if CONF_NOTIFY in config:
        raw: Any = config.get(CONF_NOTIFY) or []
        if isinstance(raw, str):
            raw = [raw]
    else:
        raw = [config.get(CONF_NOTIFY_1), config.get(CONF_NOTIFY_2)]

    targets: list[str] = []
    for item in raw:
        target = normalize_target(item)
        if target and target not in targets:
            targets.append(target)
    return targets


def build_notify_options(
    available: Iterable[str], selected: Iterable[str]
) -> list[tuple[str, str]]:
    """(value, label) options for the picker, mobile apps first.

    ``available`` are notify service names without the domain. Targets that are
    already selected but no longer registered stay selectable (marked as
    unavailable) so that saving the form does not fail validation.
    """
    values = {
        normalize_target(name)
        for name in available
        if name and name not in _EXCLUDED_SERVICES
    }
    ordered = sorted(
        values, key=lambda value: (not value.startswith(MOBILE_APP_PREFIX), value)
    )
    options = [(value, value) for value in ordered]
    for target in selected:
        if target and target not in values:
            options.append((target, f"{target} (unavailable)"))
            values.add(target)
    return options
