from labels import apply_charger_state, label_for, meter_amps, meter_phases, power_watts


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


def test_meter_values_track_transaction_and_energy():
    payload = _meter(
        [
            {"measurand": "Energy.Active.Import.Register", "value": "7721241.46", "unit": "Wh"},
            {"measurand": "Power.Active.Import", "value": "8723", "unit": "W"},
        ]
    )
    payload["transactionId"] = 8
    state = apply_charger_state({}, "MeterValues", payload)
    assert state["transactionId"] == 8
    assert state["energyWh"] == 7721241.46
    assert state["watts"] == 8723


def test_stop_closes_transaction_and_trailing_meter_does_not_reopen_it():
    started = apply_charger_state({}, "StartTransaction", {"connectorId": 1, "idTag": "no-card"})
    assert started["idTag"] == "no-card"
    live = apply_charger_state(started, "MeterValues", {"transactionId": 7, "meterValue": []})
    assert live["transactionId"] == 7
    stopped = apply_charger_state(live, "StopTransaction", {"transactionId": 7, "meterStop": 1})
    assert stopped["transactionId"] is None
    assert stopped["idTag"] is None
    trailed = apply_charger_state(stopped, "MeterValues", {"transactionId": 7, "meterValue": []})
    assert trailed["transactionId"] is None
    nxt = apply_charger_state(trailed, "MeterValues", {"transactionId": 9, "meterValue": []})
    assert nxt["transactionId"] == 9


def test_meter_without_transaction_clears_the_id():
    live = apply_charger_state({}, "MeterValues", {"transactionId": "7", "meterValue": []})
    assert live["transactionId"] == 7
    idle = apply_charger_state(live, "MeterValues", {"meterValue": []})
    assert idle["transactionId"] is None


def test_available_clears_the_session():
    live = apply_charger_state(
        {"transactionId": 7, "idTag": "no-card"},
        "StatusNotification",
        {"status": "Available"},
    )
    assert live["status"] == "Available"
    assert live["transactionId"] is None
    assert live["idTag"] is None


def test_change_configuration_label_includes_the_requested_value():
    assert (
        label_for(
            "ChangeConfiguration",
            {"key": "MeterValueSampleInterval", "value": "7"},
        )
        == "Change MeterValueSampleInterval = 7"
    )
    assert label_for("ChangeConfiguration", {"key": "AuthorizeRemoteTxRequests"}) == (
        "Change AuthorizeRemoteTxRequests"
    )


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
