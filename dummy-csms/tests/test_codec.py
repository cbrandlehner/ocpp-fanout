import json

import pytest

from ocpp_codec import (
    CALL,
    CALLERROR,
    CALLRESULT,
    OcppDecodeError,
    decode,
    encode_call,
    encode_call_error,
    encode_call_result,
)


def test_call_encoding_is_ocpp_array():
    raw = encode_call("abc-1", "BootNotification", {"chargePointVendor": "go-e"})
    data = json.loads(raw)
    assert data[0] == 2
    assert data[1] == "abc-1"
    assert data[2] == "BootNotification"
    assert data[3]["chargePointVendor"] == "go-e"
    assert raw.startswith("[2,")


def test_call_result_encoding_is_ocpp_array():
    raw = encode_call_result("abc-1", {"status": "Accepted"})
    data = json.loads(raw)
    assert data == [3, "abc-1", {"status": "Accepted"}]
    assert raw.startswith("[3,")


def test_call_error_encoding_is_ocpp_array():
    raw = encode_call_error("abc-1", "NotImplemented", "nope", {"k": 1})
    data = json.loads(raw)
    assert data[0] == 4
    assert data[1] == "abc-1"
    assert data[2] == "NotImplemented"
    assert data[3] == "nope"
    assert data[4] == {"k": 1}


def test_decode_call_roundtrip():
    raw = encode_call("id-9", "Heartbeat", {})
    parsed = decode(raw)
    assert parsed["messageType"] == CALL
    assert parsed["uniqueId"] == "id-9"
    assert parsed["action"] == "Heartbeat"
    assert parsed["payload"] == {}


def test_decode_call_result_roundtrip():
    raw = encode_call_result("id-9", {"currentTime": "2026-08-27T08:00:00Z"})
    parsed = decode(raw)
    assert parsed["messageType"] == CALLRESULT
    assert parsed["payload"]["currentTime"].endswith("Z")


def test_decode_rejects_object():
    with pytest.raises(OcppDecodeError):
        decode('{"action":"BootNotification"}')


def test_decode_rejects_unknown_type():
    with pytest.raises(OcppDecodeError):
        decode("[9,\"x\"]")


def test_unique_id_is_string_even_if_int_passed():
    raw = encode_call_result(123, {})  # type: ignore[arg-type]
    parsed = decode(raw)
    assert parsed["uniqueId"] == "123"
