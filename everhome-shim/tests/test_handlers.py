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
    synthesize_start_transaction,
    STATE,
)
from ocpp_codec import CALL, CALLRESULT, decode, encode_call, encode_call_result


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
    note_csms_start_result("uid-1", {"transactionId": 77, "idTagInfo": {"status": "Accepted"}})
    assert STATE["csms_txn"] == 77
    assert STATE["pending_start"] is None


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
    assert appmod.resolve_upstream(
        "ws://192.0.2.10:8083", append=True, cpid="ABC"
    ) == "ws://192.0.2.10:8083/ABC"
