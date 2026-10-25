"""Map OCPP actions/payloads to short live-view chip labels and charger state."""

from __future__ import annotations

from typing import Any

PLUGGED_STATUSES = {
    "Preparing",
    "Charging",
    "SuspendedEV",
    "SuspendedEVSE",
    "Finishing",
}

EMPTY_CHARGER_STATE: dict[str, Any] = {
    "status": None,
    "watts": None,
    "plugged": False,
    "charging": False,
    "phases": 0,
    "amps": {"L1": None, "L2": None, "L3": None},
    "kw": 0.0,
    "transactionId": None,
    "idTag": None,
    "energyWh": None,
}


def _measurand(sampled: dict[str, Any]) -> tuple[str | None, Any]:
    meas = str(sampled.get("measurand") or "")
    value = sampled.get("value")
    return meas, value


def _sampled_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mv in payload.get("meterValue") or []:
        out.extend(mv.get("sampledValue") or [])
    return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def power_watts(payload: dict[str, Any] | None) -> int | None:
    for sampled in _sampled_values(payload or {}):
        meas, value = _measurand(sampled)
        if meas != "Power.Active.Import" or sampled.get("phase"):
            continue
        watts = _as_float(value)
        if watts is None:
            continue
        return int(round(watts))
    return None


def meter_amps(payload: dict[str, Any] | None) -> dict[str, float | None]:
    """Per-phase Current.Import in amperes (L1/L2/L3)."""
    amps: dict[str, float | None] = {"L1": None, "L2": None, "L3": None}
    unphased: list[float] = []
    for sampled in _sampled_values(payload or {}):
        meas = str(sampled.get("measurand") or "")
        if not meas.startswith("Current.Import"):
            continue
        raw = _as_float(sampled.get("value"))
        if raw is None:
            continue
        unit = str(sampled.get("unit") or "A")
        value = raw / 1000.0 if unit.lower() == "ma" else raw
        phase = str(sampled.get("phase") or "").upper()
        if meas.endswith(".L1") or phase == "L1":
            amps["L1"] = value
        elif meas.endswith(".L2") or phase == "L2":
            amps["L2"] = value
        elif meas.endswith(".L3") or phase == "L3":
            amps["L3"] = value
        elif meas == "Current.Import" and not phase:
            unphased.append(value)
    if unphased and all(amps[k] is None for k in amps):
        for key, value in zip(("L1", "L2", "L3"), unphased):
            amps[key] = value
    return amps


def transaction_id_of(value: Any) -> int | None:
    """Positive OCPP transactionId. Rejects bool, because bool is an int subclass."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if number > 0 else None


def energy_wh(payload: dict[str, Any] | None) -> float | None:
    for sampled in _sampled_values(payload or {}):
        meas, value = _measurand(sampled)
        if meas != "Energy.Active.Import.Register" or sampled.get("phase"):
            continue
        wh = _as_float(value)
        if wh is not None:
            return wh
    return None


def _closed_ids(state: dict[str, Any]) -> set[int]:
    found: set[int] = set()
    for value in state.get("_closedTxn") or []:
        txn = transaction_id_of(value)
        if txn is not None:
            found.add(txn)
    return found


def meter_phases(amps: dict[str, float | None], watts: int | None = None) -> int:
    active = sum(1 for value in amps.values() if value is not None and value >= 0.4)
    if active:
        return active
    if watts and watts > 0:
        return 0
    return 0


def apply_charger_state(
    state: dict[str, Any] | None,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive plug/charging flags from Charge Point OCPP calls."""
    out = dict(EMPTY_CHARGER_STATE)
    if state:
        out.update(state)
    closed = _closed_ids(out)
    payload = payload or {}
    if action == "StatusNotification":
        status = payload.get("status")
        if status:
            out["status"] = status
            out["plugged"] = status in PLUGGED_STATUSES
            if status == "Available":
                out["charging"] = False
                out["watts"] = 0
                out["kw"] = 0.0
                out["phases"] = 0
                out["amps"] = {"L1": None, "L2": None, "L3": None}
                out["transactionId"] = None
                out["idTag"] = None
            else:
                out["charging"] = status == "Charging"
    elif action == "MeterValues":
        watts = power_watts(payload)
        amps = meter_amps(payload)
        wh = energy_wh(payload)
        if any(v is not None for v in amps.values()):
            out["amps"] = amps
            out["phases"] = meter_phases(amps, watts)
        if watts is not None:
            out["watts"] = watts
            out["kw"] = round(watts / 1000.0, 2)
            status = out.get("status")
            if watts > 0:
                out["plugged"] = True
                paused = status in {"Preparing", "SuspendedEV", "SuspendedEVSE", "Finishing"}
                out["charging"] = not paused
            elif status != "Charging":
                out["charging"] = False
                out["phases"] = meter_phases(amps, watts)
        if wh is not None:
            out["energyWh"] = wh
        txn = transaction_id_of(payload.get("transactionId"))
        if txn is not None and txn not in closed:
            out["transactionId"] = txn
        else:
            out["transactionId"] = None
    elif action == "StartTransaction":
        out["plugged"] = True
        out["charging"] = True
        tag = payload.get("idTag")
        if tag:
            out["idTag"] = str(tag)
        txn = transaction_id_of(payload.get("transactionId"))
        if txn is not None and txn not in closed:
            out["transactionId"] = txn
    elif action == "StopTransaction":
        stopped = transaction_id_of(payload.get("transactionId"))
        if stopped is not None:
            closed.add(stopped)
        if stopped is None or out.get("transactionId") == stopped:
            out["transactionId"] = None
            out["idTag"] = None
        out["charging"] = False
        out["watts"] = 0
        out["kw"] = 0.0
        out["phases"] = 0
        out["amps"] = {"L1": None, "L2": None, "L3": None}
        out["plugged"] = True
    out["_closedTxn"] = sorted(closed)
    return out


def label_for(action: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    if action in {"StartTransaction", "RemoteStartTransaction"}:
        return "Start charging"
    if action in {"StopTransaction", "RemoteStopTransaction"}:
        return "Stop charging"
    if action == "MeterValues":
        watts = power_watts(payload)
        if watts is not None:
            return f"{watts} W"
        values = []
        for s in _sampled_values(payload):
            meas, value = _measurand(s)
            if value is None:
                continue
            if meas == "Energy.Active.Import.Register":
                values.append(("energy", value))
            if meas == "Current.Import":
                values.append(("current", value))
        if values:
            kind, value = values[0]
            if kind == "energy":
                try:
                    wh = float(value)
                    if wh >= 1000:
                        return f"Energy {wh / 1000:.1f} kWh"
                    return f"Energy {int(wh)} Wh"
                except (TypeError, ValueError):
                    return f"Energy {value}"
            return f"{value} A"
        return "Meter values"
    if action == "StatusNotification":
        status = payload.get("status") or "Status"
        return f"Status: {status}"
    if action == "BootNotification":
        return "Boot"
    if action == "Heartbeat":
        return "Heartbeat"
    if action == "GetConfiguration":
        return "Get config"
    if action == "TriggerMessage":
        requested = payload.get("requestedMessage") or "Trigger"
        return f"Trigger {requested}"
    if action == "SetChargingProfile":
        return "Set profile"
    if action == "ClearChargingProfile":
        return "Clear profile"
    if action == "ChangeConfiguration":
        key = payload.get("key") or "config"
        value = payload.get("value")
        if value is None or value == "":
            return f"Change {key}"
        return f"Change {key} = {value}"
    if action == "Reset":
        return "Reset"
    if action == "Authorize":
        return "Authorize"
    if action == "ChangeAvailability":
        return "Availability"
    if action == "DataTransfer":
        return "Data transfer"
    return action or "OCPP"
