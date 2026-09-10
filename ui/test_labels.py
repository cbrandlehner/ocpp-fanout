from labels import apply_charger_state, meter_amps, meter_phases, power_watts


def _meter(samples):
    return {"meterValue": [{"sampledValue": samples}]}


def test_goe_phase_currents_and_power():
    payload = _meter(
        [
            {"measurand": "Current.Import.L1", "value": "16.02", "unit": "A"},
            {"measurand": "Current.Import.L2", "value": "15.9", "unit": "A"},
            {"measurand": "Current.Import.L3", "value": "0.00", "unit": "A"},
            {"measurand": "Power.Active.Import", "value": "7360", "unit": "W"},
        ]
    )
    amps = meter_amps(payload)
    assert round(amps["L1"], 1) == 16.0
    assert round(amps["L2"], 1) == 15.9
    assert amps["L3"] == 0.0
    assert meter_phases(amps, 7360) == 2
    assert power_watts(payload) == 7360
    state = apply_charger_state({"status": "Charging"}, "MeterValues", payload)
    assert state["charging"] is True
    assert state["phases"] == 2
    assert state["kw"] == 7.36


def test_ocpp_phase_attribute():
    payload = _meter(
        [
            {"measurand": "Current.Import", "phase": "L1", "value": "6", "unit": "A"},
            {"measurand": "Current.Import", "phase": "L2", "value": "6", "unit": "A"},
            {"measurand": "Current.Import", "phase": "L3", "value": "6", "unit": "A"},
            {"measurand": "Power.Active.Import", "value": "4140", "unit": "W"},
        ]
    )
    assert meter_phases(meter_amps(payload), 4140) == 3
