import asyncio
import json

from app import DummyCsms, FORBIDDEN_OUTGOING_ACTIONS
from ocpp_codec import decode, encode_call, encode_call_result


def test_boot_notification_accepted():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("BootNotification", {"chargePointVendor": "go-e"}))
    assert result["status"] == "Accepted"
    assert result["interval"] == 300
    assert "T" in result["currentTime"]


def test_heartbeat_has_time():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("Heartbeat", {}))
    assert "currentTime" in result


def test_start_transaction_increments():
    csms = DummyCsms()
    first = asyncio.run(csms.handle_call("StartTransaction", {"connectorId": 1, "idTag": "x"}))
    second = asyncio.run(csms.handle_call("StartTransaction", {"connectorId": 1, "idTag": "x"}))
    assert first["transactionId"] == 1
    assert second["transactionId"] == 2
    assert first["idTagInfo"]["status"] == "Accepted"


def test_authorize_accepted():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("Authorize", {"idTag": "RFID"}))
    assert result["idTagInfo"]["status"] == "Accepted"


def test_meter_values_empty_ok():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("MeterValues", {"connectorId": 1, "meterValue": []}))
    assert result == {}


def test_data_transfer_accepted():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("DataTransfer", {"vendorId": "go-e"}))
    assert result["status"] == "Accepted"


def test_unknown_action_still_ok_payload():
    csms = DummyCsms()
    result = asyncio.run(csms.handle_call("TotallyUnknown", {}))
    assert result == {}


def test_forbidden_outgoing_set_is_complete():
    for action in (
        "SetChargingProfile",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "ChangeConfiguration",
        "Reset",
    ):
        assert action in FORBIDDEN_OUTGOING_ACTIONS


def test_call_result_is_not_a_call():
    raw = encode_call_result("1", {"status": "Accepted"})
    parsed = decode(raw)
    assert parsed["messageTypeName"] == "CallResult"
    call = decode(encode_call("1", "Reset", {"type": "Hard"}))
    assert call["action"] == "Reset"
    dumped = encode_call_result("1", {})
    assert json.loads(dumped)[0] == 3


def test_pick_cpid_single_socket():
    csms = DummyCsms()

    class _Open:
        closed = False

    csms.sockets["CP001"] = _Open()  # type: ignore[assignment]
    assert csms.pick_cpid(None) == "CP001"
    assert csms.pick_cpid("CP001") == "CP001"
    assert csms.pick_cpid("other") == "CP001"  # sole live socket


def test_inject_without_charger():
    csms = DummyCsms()
    result = asyncio.run(csms.inject_call("TriggerMessage", {"requestedMessage": "StatusNotification"}))
    assert result["ok"] is False
    assert result["error"] == "charger not connected"
