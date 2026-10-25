from ui_events import event_body
from app import (
    apply_change_configuration,
    cache_cp_call,
    command_allowed,
    get_configuration_result,
    note_csms_start_result,
    parse_allowed,
    patch_get_configuration,
    reply_to_csms_call,
    replay_for_trigger,
    rewrite_txn_payload,
    settle_remote_stop,
    should_forward_to_upstream,
    synthesize_start_transaction,
    STATE,
)
from ocpp_codec import CALL, CALLRESULT, decode, encode_call, encode_call_error, encode_call_result


def test_trigger_message_accepted():
    assert reply_to_csms_call("TriggerMessage") == {"status": "Accepted"}


def test_control_calls_rejected():
    for action in (
        "SetChargingProfile",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "Reset",
    ):
        assert reply_to_csms_call(action)["status"] == "Rejected"


def test_rfid_and_clear_profile_accepted_locally():
    assert reply_to_csms_call("SendLocalList", {"updateType": "Full", "localAuthorizationList": []}) == {
        "status": "Accepted"
    }
    assert reply_to_csms_call("ClearChargingProfile", {}) == {"status": "Accepted"}
    assert reply_to_csms_call("ClearCache") == {"status": "Accepted"}


def test_enphase_get_configuration_is_not_rewritten(monkeypatch):
    monkeypatch.setattr("app.SHIM_NAME", "enphase-shim")
    original = {
        "configurationKey": [
            {"key": "AuthorizeRemoteTxRequests", "value": "false", "readonly": True}
        ],
        "unknownKey": ["AuthorizeRemoteTxRequests"],
    }
    assert patch_get_configuration(original) == original


def test_patch_get_configuration_enables_remote_start():
    patched = patch_get_configuration(
        {
            "configurationKey": [
                {"key": "AuthorizeRemoteTxRequests", "value": "false", "readonly": True},
                {"key": "HeartbeatInterval", "value": "300", "readonly": False},
            ]
        }
    )
    by_key = {i["key"]: i for i in patched["configurationKey"]}
    assert by_key["AuthorizeRemoteTxRequests"]["value"] == "true"
    assert by_key["AuthorizeRemoteTxRequests"]["readonly"] is False
    assert by_key["HeartbeatInterval"]["value"] == "300"


def test_get_configuration_returns_keys():
    result = reply_to_csms_call("GetConfiguration", {})
    keys = {item["key"] for item in result["configurationKey"]}
    assert "NumberOfConnectors" in keys
    assert "MeterValuesSampledData" in keys
    assert "unknownKey" not in result


def test_get_configuration_unknown_key():
    result = get_configuration_result({"key": ["NoSuchKey"]}, {})
    assert result["configurationKey"] == []
    assert result["unknownKey"] == ["NoSuchKey"]


def test_change_configuration_accepted_locally():
    config = {"MeterValueSampleInterval": ("30", False)}
    result = apply_change_configuration(
        {"key": "MeterValueSampleInterval", "value": "60"}, config
    )
    assert result == {"status": "Accepted"}
    assert config["MeterValueSampleInterval"][0] == "60"


def test_change_configuration_readonly_rejected():
    config = {"NumberOfConnectors": ("1", True)}
    result = apply_change_configuration({"key": "NumberOfConnectors", "value": "2"}, config)
    assert result == {"status": "Rejected"}


def test_change_configuration_unknown_key_accepted_locally():
    config: dict = {}
    result = apply_change_configuration({"key": "ISO15118PnCEnabled", "value": "false"}, config)
    assert result == {"status": "Accepted"}
    assert config["ISO15118PnCEnabled"][0] == "false"


def test_monta_goe_keys_present():
    result = reply_to_csms_call("GetConfiguration", {"key": [
        "ISO15118PnCEnabled",
        "SendLocalListMaxLength",
        "AllowOfflineTxForUnknownId",
        "SupportedFeatureProfiles",
    ]})
    by_key = {item["key"]: item for item in result["configurationKey"]}
    assert "unknownKey" not in result
    assert by_key["SendLocalListMaxLength"]["readonly"] is True
    assert by_key["SupportedFeatureProfiles"]["readonly"] is True
    assert by_key["ISO15118PnCEnabled"]["readonly"] is False


def test_call_result_encoding():
    raw = encode_call_result("trigger_1", {"status": "Accepted"})
    parsed = decode(raw)
    assert parsed["messageType"] == CALLRESULT
    assert parsed["uniqueId"] == "trigger_1"
    assert parsed["payload"]["status"] == "Accepted"


def test_encode_call():
    raw = encode_call("id-1", "StatusNotification", {"status": "Available"})
    parsed = decode(raw)
    assert parsed["messageType"] == CALL
    assert parsed["action"] == "StatusNotification"


def test_replay_status_after_cache():
    STATE["lastCpCalls"] = {}
    cache_cp_call("StatusNotification", {"connectorId": 1, "status": "Charging"})
    replay = replay_for_trigger("StatusNotification")
    assert replay is not None
    action, payload = replay
    assert action == "StatusNotification"
    assert payload["status"] == "Charging"


def test_replay_status_fabricated_when_empty():
    STATE["lastCpCalls"] = {}
    replay = replay_for_trigger("StatusNotification")
    assert replay is not None
    assert replay[1]["status"] == "Available"


def test_parse_allowed_csv_and_list():
    assert parse_allowed("TriggerMessage:StatusNotification,GetConfiguration") == [
        "TriggerMessage:StatusNotification",
        "GetConfiguration",
    ]
    assert parse_allowed([" Reset ", ""]) == ["Reset"]
    assert parse_allowed(None) == []


def test_command_allowed_trigger_status_only():
    allowed = ["TriggerMessage:StatusNotification"]
    assert command_allowed(
        "TriggerMessage", {"requestedMessage": "StatusNotification"}, allowed
    )
    assert not command_allowed(
        "TriggerMessage", {"requestedMessage": "MeterValues"}, allowed
    )
    assert not command_allowed("Reset", {}, allowed)


def test_command_allowed_bare_action():
    assert command_allowed("GetConfiguration", {}, ["GetConfiguration"])
    assert command_allowed(
        "TriggerMessage", {"requestedMessage": "Heartbeat"}, ["TriggerMessage"]
    )


def test_synthesize_start_from_meter_values():
    STATE["lastCpCalls"] = {}
    meter = {
        "connectorId": 1,
        "transactionId": 4,
        "meterValue": [{"sampledValue": [{"measurand": "Energy.Active.Import.Register", "value": "7522450.44"}]}],
    }
    start = synthesize_start_transaction(meter)
    assert start["connectorId"] == 1
    assert start["idTag"] == "NOCARD"
    assert start["meterStart"] == 7522450


def test_rewrite_meter_values_to_csms_txn():
    STATE["csms_txn"] = 99
    out = rewrite_txn_payload("MeterValues", {"connectorId": 1, "transactionId": 4})
    assert out["transactionId"] == 99
    assert out["connectorId"] == 1


def test_note_csms_start_result_sets_txn():
    STATE["pending_start"] = "uid-1"
    STATE["csms_txn"] = None
    STATE["start_wait"] = None
    STATE["start_rejected"] = False
    note_csms_start_result("uid-1", {"transactionId": 77, "idTagInfo": {"status": "Accepted"}})
    assert STATE["csms_txn"] == 77
    assert STATE["pending_start"] is None
    assert STATE["start_rejected"] is False


def test_note_csms_start_result_ignores_invalid_transaction():
    STATE["pending_start"] = "uid-2"
    STATE["csms_txn"] = None
    STATE["start_wait"] = None
    STATE["start_rejected"] = False
    note_csms_start_result("uid-2", {"transactionId": -1, "idTagInfo": {"status": "Invalid"}})
    assert STATE["csms_txn"] is None
    assert STATE["start_rejected"] is True
    assert STATE["pending_start"] is None


def test_stale_remote_stop_is_closed_towards_csms():
    STATE["lastCpCalls"] = {
        "MeterValues": {
            "meterValue": [
                {"sampledValue": [{"measurand": "Energy.Active.Import.Register", "value": "7724064.00"}]}
            ]
        }
    }
    reply, stop = settle_remote_stop(
        {"transactionId": 3},
        {"payload": {"status": "Rejected"}, "sentTransactionId": 3},
        2,
    )
    assert reply == {"status": "Accepted"}
    assert stop is not None
    assert stop["transactionId"] == 3
    assert stop["meterStop"] == 7724064
    assert stop["reason"] == "Remote"


def test_rejected_stop_of_the_live_transaction_stays_rejected():
    reply, stop = settle_remote_stop(
        {"transactionId": 3},
        {"payload": {"status": "Rejected"}, "sentTransactionId": 2},
        2,
    )
    assert reply == {"status": "Rejected"}
    assert stop is None


def test_stop_log_keeps_both_transaction_ids():
    STATE["csms_txn"] = 1
    STATE["cp_txn"] = 2
    original = {"transactionId": 2, "meterStop": 10, "timestamp": "2026-10-25T18:59:21Z"}
    wire = rewrite_txn_payload("StopTransaction", original)
    body = event_body(
        src="charger",
        dst="enphase",
        action="StopTransaction",
        payload=wire,
        received=original,
        unique_id="stop-1",
        hop="upstream",
    )
    assert body["payload"]["transactionId"] == 1
    assert body["received"]["transactionId"] == 2
    assert body["hop"] == "upstream"
    assert body["messageType"] == "Call"
    unchanged = event_body(src="charger", dst="enphase", action="Heartbeat", payload={}, received={})
    assert "received" not in unchanged


def test_upstream_forward_keeps_charge_point_calls_only():
    assert should_forward_to_upstream(encode_call("1", "StartTransaction", {}))
    assert should_forward_to_upstream(encode_call("2", "MeterValues", {"transactionId": 1}))
    assert not should_forward_to_upstream(encode_call("3", "RemoteStartTransaction", {}))
    assert not should_forward_to_upstream(encode_call_result("64d1fb90", {"status": "Accepted"}))
    assert not should_forward_to_upstream(
        encode_call_error("e50f8aa7", "InternalError", "An internal error occurred", {})
    )


def test_call_error_keeps_description():
    parsed = decode('[4,"err-1","InternalError","txn rejected",{"transactionId":1}]')
    assert parsed["errorCode"] == "InternalError"
    assert parsed["errorDescription"] == "txn rejected"
    assert parsed["errorDetails"]["transactionId"] == 1


def test_resolve_upstream_appends_cpid(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "APPEND_CPID", True)
    monkeypatch.setattr(appmod, "CHARGE_POINT_ID", "CP001")
    assert appmod.resolve_upstream("ws://192.0.2.10:8083") == (
        "ws://192.0.2.10:8083/CP001"
    )
    assert appmod.resolve_upstream("ws://192.0.2.10:8083/CP001") == (
        "ws://192.0.2.10:8083/CP001"
    )
