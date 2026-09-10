"""OCPP 1.6 JSON array encoding. Shared copy of dummy-csms/ocpp_codec.py."""

from __future__ import annotations

import json
from typing import Any

CALL = 2
CALLRESULT = 3
CALLERROR = 4


class OcppDecodeError(ValueError):
    pass


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def encode_call(unique_id: str, action: str, payload: dict | None = None) -> str:
    if not unique_id:
        raise ValueError("unique_id must be a non-empty string")
    if not action:
        raise ValueError("action must be a non-empty string")
    return dumps([CALL, str(unique_id), str(action), payload or {}])


def encode_call_result(unique_id: str, payload: dict | None = None) -> str:
    return dumps([CALLRESULT, str(unique_id), payload or {}])


def encode_call_error(
    unique_id: str,
    error_code: str,
    error_description: str = "",
    error_details: dict | None = None,
) -> str:
    return dumps(
        [CALLERROR, str(unique_id), str(error_code), str(error_description), error_details or {}]
    )


def decode(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) < 2:
        raise OcppDecodeError("OCPP message must be a JSON array")
    msg_type = data[0]
    unique_id = str(data[1])
    if msg_type == CALL:
        payload = data[3] if len(data) > 3 and isinstance(data[3], dict) else {}
        return {
            "messageType": CALL,
            "uniqueId": unique_id,
            "action": str(data[2]),
            "payload": payload,
        }
    if msg_type == CALLRESULT:
        payload = data[2] if len(data) > 2 and isinstance(data[2], dict) else {}
        return {"messageType": CALLRESULT, "uniqueId": unique_id, "payload": payload}
    if msg_type == CALLERROR:
        return {
            "messageType": CALLERROR,
            "uniqueId": unique_id,
            "errorCode": str(data[2]) if len(data) > 2 else "",
        }
    raise OcppDecodeError(f"unknown messageType {msg_type}")
