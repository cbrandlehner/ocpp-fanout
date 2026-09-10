"""OCPP 1.6 JSON array encoding.

Call:       [2, uniqueId, action, payload]
CallResult: [3, uniqueId, payload]
CallError:  [4, uniqueId, errorCode, errorDescription, errorDetails]
"""

from __future__ import annotations

import json
from typing import Any

CALL = 2
CALLRESULT = 3
CALLERROR = 4

MESSAGE_TYPES = {CALL: "Call", CALLRESULT: "CallResult", CALLERROR: "CallError"}


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
    if not unique_id:
        raise ValueError("unique_id must be a non-empty string")
    return dumps([CALLRESULT, str(unique_id), payload or {}])


def encode_call_error(
    unique_id: str,
    error_code: str,
    error_description: str = "",
    error_details: dict | None = None,
) -> str:
    if not unique_id:
        raise ValueError("unique_id must be a non-empty string")
    return dumps(
        [
            CALLERROR,
            str(unique_id),
            str(error_code),
            str(error_description),
            error_details or {},
        ]
    )


def decode(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OcppDecodeError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, list) or len(data) < 2:
        raise OcppDecodeError("OCPP message must be a JSON array with >= 2 elements")
    msg_type = data[0]
    unique_id = str(data[1])
    if msg_type == CALL:
        if len(data) != 4:
            raise OcppDecodeError("Call must be [2, id, Action, payload]")
        payload = data[3] if isinstance(data[3], dict) else {}
        return {
            "messageType": CALL,
            "messageTypeName": "Call",
            "uniqueId": unique_id,
            "action": str(data[2]),
            "payload": payload,
        }
    if msg_type == CALLRESULT:
        if len(data) != 3:
            raise OcppDecodeError("CallResult must be [3, id, payload]")
        payload = data[2] if isinstance(data[2], dict) else {}
        return {
            "messageType": CALLRESULT,
            "messageTypeName": "CallResult",
            "uniqueId": unique_id,
            "payload": payload,
        }
    if msg_type == CALLERROR:
        if len(data) < 4:
            raise OcppDecodeError(
                "CallError must be [4, id, errorCode, errorDescription, errorDetails?]"
            )
        details = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
        return {
            "messageType": CALLERROR,
            "messageTypeName": "CallError",
            "uniqueId": unique_id,
            "errorCode": str(data[2]),
            "errorDescription": str(data[3]) if len(data) > 3 else "",
            "errorDetails": details,
        }
    raise OcppDecodeError(f"unknown OCPP messageType {msg_type}")
