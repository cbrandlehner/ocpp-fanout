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


def test_status_poll_interval_charging_vs_idle():
    from app import status_poll_interval

    assert status_poll_interval("Charging", 60, 7) == 60
    assert status_poll_interval("SuspendedEVSE", 60, 7) == 7
    assert status_poll_interval("Available", 60, 7) == 7
    assert status_poll_interval(None, 60, 7) == 7


def test_status_poll_interval_zero_disables():
    from app import status_poll_interval

    assert status_poll_interval("Charging", 0, 7) == 0
    assert status_poll_interval("Available", 60, 0) == 0


def test_parse_status_intervals_defaults_and_clamp():
    from app import parse_status_intervals

    assert parse_status_intervals({}) == (60, 7)
    assert parse_status_intervals(None) == (60, 7)
    assert parse_status_intervals(
        {"statusIntervalChargingSec": 90, "statusIntervalIdleSec": 15}
    ) == (90, 15)
    assert parse_status_intervals(
        {"statusIntervalChargingSec": -3, "statusIntervalIdleSec": "nope"}
    ) == (0, 7)


def test_derive_ui_config_url_from_event_url():
    from app import derive_ui_config_url

    assert derive_ui_config_url("http://ui:8080/internal/event") == "http://ui:8080/api/config"
    assert derive_ui_config_url("") == ""


def test_charger_status_from_status_notification():
    from app import charger_status_from_call

    assert charger_status_from_call("StatusNotification", {"status": "Charging"}) == "Charging"
    assert charger_status_from_call("MeterValues", {"status": "Charging"}) is None
    assert charger_status_from_call("StatusNotification", {}) is None


def test_transaction_id_of_rejects_bool_and_non_positive():
    from app import transaction_id_of

    assert transaction_id_of(True) is None
    assert transaction_id_of(False) is None
    assert transaction_id_of(0) is None
    assert transaction_id_of(-3) is None
    assert transaction_id_of("4") == 4
    assert transaction_id_of(" 7 ") == 7
    assert transaction_id_of(7.0) == 7
    assert transaction_id_of(7.5) is None
    assert transaction_id_of(None) is None


def test_meter_values_teach_the_live_transaction():
    csms = DummyCsms()
    result = asyncio.run(
        csms.handle_call(
            "MeterValues",
            {"connectorId": 1, "transactionId": 7, "meterValue": []},
            "206540",
        )
    )
    assert result == {}
    assert csms.active_txn["206540"] == 7
    asyncio.run(csms.handle_call("MeterValues", {"connectorId": 1, "meterValue": []}, "206540"))
    assert csms.active_txn["206540"] == 7


def test_start_after_meter_does_not_reuse_a_lower_id():
    csms = DummyCsms()
    asyncio.run(
        csms.handle_call("MeterValues", {"connectorId": 1, "transactionId": "7"}, "206540")
    )
    started = asyncio.run(csms.handle_call("StartTransaction", {"connectorId": 1, "idTag": "x"}, "206540"))
    assert started["transactionId"] == 8
    assert csms.active_txn["206540"] == 8


def test_remote_stop_rewrites_wrong_id_and_retries_same_session():
    csms = DummyCsms()
    csms.active_txn["206540"] = 7
    first = csms.remote_stop_payload("206540", {"transactionId": 4}, "enphase")
    second = csms.remote_stop_payload("206540", {"transactionId": 4}, "enphase")
    assert first["transactionId"] == 7
    assert second["transactionId"] == 7


def test_remote_stop_same_id_is_not_rewritten_again_onto_a_newer_session():
    csms = DummyCsms()
    csms.active_txn["206540"] = 7
    assert csms.remote_stop_payload("206540", {"transactionId": 4}, "enphase")["transactionId"] == 7
    asyncio.run(csms.handle_call("StopTransaction", {"transactionId": 7}, "206540"))
    assert "206540" not in csms.active_txn
    csms.note_active_transaction("206540", 8)
    stale = csms.remote_stop_payload("206540", {"transactionId": 4}, "enphase")
    assert stale == {"transactionId": 4}
    other_backend = csms.remote_stop_payload("206540", {"transactionId": 4}, "monta")
    assert other_backend["transactionId"] == 8
    new_enphase_id = csms.remote_stop_payload("206540", {"transactionId": 5}, "enphase")
    assert new_enphase_id["transactionId"] == 8


def test_remote_stop_without_live_transaction_is_unchanged():
    csms = DummyCsms()
    assert csms.remote_stop_payload("206540", {"transactionId": 4}, "enphase") == {"transactionId": 4}


def test_remote_stop_matching_and_missing_id():
    csms = DummyCsms()
    csms.active_txn["206540"] = 7
    assert csms.remote_stop_payload("206540", {"transactionId": "7"}, "enphase")["transactionId"] == "7"
    assert csms.remote_stop_payload("206540", {}, "enphase")["transactionId"] == 7


def test_rejected_stop_stays_closed_when_meters_repeat_its_id():
    csms = DummyCsms()
    csms.note_active_transaction("206540", 8)
    csms.close_transaction("206540", 8)
    csms.note_active_transaction("206540", 8)
    assert "206540" not in csms.active_txn


def test_meter_values_do_not_reopen_a_stopped_transaction():
    csms = DummyCsms()
    asyncio.run(
        csms.handle_call("MeterValues", {"connectorId": 1, "transactionId": 8}, "206540")
    )
    asyncio.run(csms.handle_call("StopTransaction", {"transactionId": 8}, "206540"))
    asyncio.run(
        csms.handle_call("MeterValues", {"connectorId": 1, "transactionId": 8, "meterValue": []}, "206540")
    )
    assert "206540" not in csms.active_txn
    asyncio.run(
        csms.handle_call("StartTransaction", {"connectorId": 1, "idTag": "no-card"}, "206540")
    )
    assert csms.active_txn["206540"] == 9


def test_stop_transaction_ignores_a_different_id():
    csms = DummyCsms()
    csms.active_txn["206540"] = 7
    asyncio.run(csms.handle_call("StopTransaction", {"transactionId": 4}, "206540"))
    assert csms.active_txn["206540"] == 7


def test_inject_remote_stop_sends_the_live_id():
    csms = DummyCsms()
    csms.active_txn["CP001"] = 7

    class _Ws:
        closed = False

        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_str(self, raw: str) -> None:
            self.sent.append(raw)
            parsed = decode(raw)
            csms.pending[parsed["uniqueId"]].set_result({"ok": True, "payload": {"status": "Accepted"}})

    ws = _Ws()
    csms.sockets["CP001"] = ws  # type: ignore[assignment]
    result = asyncio.run(
        csms.inject_call("RemoteStopTransaction", {"transactionId": 4}, "CP001", via="enphase")
    )
    assert result["ok"] is True
    assert result["payload"]["status"] == "Accepted"
    assert result["sentTransactionId"] == 7
    assert result["requestedTransactionId"] == 4
    sent = decode(ws.sent[0])
    assert sent["action"] == "RemoteStopTransaction"
    assert sent["payload"]["transactionId"] == 7
