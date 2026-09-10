"""Defaults for Setup fields that older config.json files may omit."""

from __future__ import annotations

from typing import Any

DEFAULT_STATUS_INTERVAL_CHARGING_SEC = 60
DEFAULT_STATUS_INTERVAL_IDLE_SEC = 7


def hydrate_charger(cfg: dict[str, Any]) -> bool:
    changed = False
    charger = cfg.get("charger")
    if not isinstance(charger, dict):
        charger = {}
        cfg["charger"] = charger
        changed = True
    if "statusIntervalChargingSec" not in charger:
        charger["statusIntervalChargingSec"] = DEFAULT_STATUS_INTERVAL_CHARGING_SEC
        changed = True
    if "statusIntervalIdleSec" not in charger:
        charger["statusIntervalIdleSec"] = DEFAULT_STATUS_INTERVAL_IDLE_SEC
        changed = True
    return changed
