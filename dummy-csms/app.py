"""Minimal OCPP 1.6J Central System.

Accepts Charge Point connections (via joulo) and answers Core calls.
Does not originate charging control. POST /inject lets a shim send an
allowed CSMS Call through this primary link so joulo forwards it to
the wallbox. Setup may also enable a StatusNotification poll
(TriggerMessage) at charging vs idle intervals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from ui_events import UI_EVENT_URL, emit_soon
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

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
LISTEN_HOST = os.environ.get("DUMMY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DUMMY_PORT", "9001"))
HEARTBEAT_INTERVAL = int(os.environ.get("DUMMY_HEARTBEAT_INTERVAL", "300"))
DEFAULT_STATUS_INTERVAL_CHARGING_SEC = 60
DEFAULT_STATUS_INTERVAL_IDLE_SEC = 7
STATUS_POLL_IDLE_SLEEP_SEC = 2.0

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(message)s",
)
log = logging.getLogger("dummy-csms")

# CSMS → Charge Point actions that must never be sent by this dummy.
FORBIDDEN_OUTGOING_ACTIONS = frozenset(
    {
        "SetChargingProfile",
        "ClearChargingProfile",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "ChangeConfiguration",
        "Reset",
        "ChangeAvailability",
        "UnlockConnector",
        "GetConfiguration",
        "GetDiagnostics",
        "UpdateFirmware",
        "TriggerMessage",
        "ReserveNow",
        "CancelReservation",
        "SendLocalList",
        "GetLocalListVersion",
        "ClearCache",
        "DataTransfer",
    }
)

CORE_ACTIONS = frozenset(
    {
        "BootNotification",
        "Heartbeat",
        "StatusNotification",
        "Authorize",
        "StartTransaction",
        "StopTransaction",
        "MeterValues",
        "DataTransfer",
        "FirmwareStatusNotification",
        "DiagnosticsStatusNotification",
        "SecurityEventNotification",
        "LogStatusNotification",
        "SignedFirmwareStatusNotification",
        "SignCertificate",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def derive_ui_config_url(event_url: str) -> str:
    url = (event_url or "").strip()
    suffix = "/internal/event"
    if url.endswith(suffix):
        return url[: -len(suffix)] + "/api/config"
    return ""


def _as_interval(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, n)


def parse_status_intervals(charger: dict[str, Any] | None) -> tuple[int, int]:
    charger = charger or {}
    return (
        _as_interval(charger.get("statusIntervalChargingSec"), DEFAULT_STATUS_INTERVAL_CHARGING_SEC),
        _as_interval(charger.get("statusIntervalIdleSec"), DEFAULT_STATUS_INTERVAL_IDLE_SEC),
    )


def status_poll_interval(status: str | None, charging_sec: int, idle_sec: int) -> int:
    if status == "Charging":
        return max(0, int(charging_sec))
    return max(0, int(idle_sec))


def charger_status_from_call(action: str, payload: dict[str, Any] | None) -> str | None:
    if action != "StatusNotification":
        return None
    status = (payload or {}).get("status")
    if status is None or status == "":
        return None
    return str(status)


def transaction_id_of(value: Any) -> int | None:
    """Positive OCPP transactionId. Rejects bool, because bool is an int subclass."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    if number <= 0:
        return None
    return number


def ui_config_url() -> str:
    return os.environ.get("UI_CONFIG_URL", "").strip() or derive_ui_config_url(UI_EVENT_URL)


async def fetch_status_intervals() -> tuple[int, int]:
    url = ui_config_url()
    if not url:
        return parse_status_intervals(None)
    try:
        timeout = ClientTimeout(total=1.5)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                cfg = await resp.json(content_type=None)
        charger = cfg.get("charger") if isinstance(cfg, dict) else None
        return parse_status_intervals(charger if isinstance(charger, dict) else None)
    except Exception:  # noqa: BLE001
        return parse_status_intervals(None)


async def status_poll_loop() -> None:
    """Trigger StatusNotification at the Setup interval for charging vs idle."""
    while True:
        charging_sec, idle_sec = await fetch_status_intervals()
        interval = status_poll_interval(STATE.last_status, charging_sec, idle_sec)
        if interval <= 0 or not STATE.pick_cpid():
            await asyncio.sleep(STATUS_POLL_IDLE_SLEEP_SEC)
            continue
        emit_soon(
            src="dummy",
            dst="charger",
            action="TriggerMessage",
            payload={"requestedMessage": "StatusNotification"},
            fate="forward",
        )
        result = await STATE.inject_call(
            "TriggerMessage",
            {"requestedMessage": "StatusNotification"},
        )
        if not result.get("ok"):
            jlog("warn", "status poll inject failed", error=result.get("error"))
        await asyncio.sleep(interval)


def jlog(level: str, msg: str, **fields: Any) -> None:
    payload = {"time": utc_now_iso(), "level": level, "tag": "dummy-csms", "msg": msg}
    payload.update(fields)
    getattr(log, level if level != "warn" else "warning")(
        __import__("json").dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


class DummyCsms:
    def __init__(self) -> None:
        self._txn = 0
        self._lock = asyncio.Lock()
        self.connections: dict[str, dict[str, Any]] = {}
        self.sockets: dict[str, web.WebSocketResponse] = {}
        self.send_locks: dict[str, asyncio.Lock] = {}
        self.pending: dict[str, asyncio.Future] = {}
        self.pending_meta: dict[str, dict[str, str]] = {}
        self.started_at = utc_now_iso()
        self.last_status: str | None = None
        self.active_txn: dict[str, int] = {}
        self._closed_txn: dict[str, set[int]] = {}
        # (charge point, backend, requested id) -> charger transaction already stopped by that id.
        self._stop_alias: dict[tuple[str, str, int], int] = {}

    def pick_cpid(self, requested: str | None = None) -> str | None:
        live = [cp for cp, ws in self.sockets.items() if not ws.closed]
        if requested and requested in live:
            return requested
        if len(live) == 1:
            return live[0]
        if requested:
            return None
        return live[0] if live else None

    def note_active_transaction(self, charge_point_id: str | None, transaction_id: Any) -> None:
        txn = transaction_id_of(transaction_id)
        if not charge_point_id or txn is None:
            return
        if txn in self._closed_txn.get(charge_point_id, ()):
            return
        if txn > self._txn:
            self._txn = txn
        previous = self.active_txn.get(charge_point_id)
        if previous == txn:
            return
        self.active_txn[charge_point_id] = txn
        jlog(
            "info",
            "active transaction",
            chargePointId=charge_point_id,
            transactionId=txn,
            previous=previous,
        )

    def close_transaction(self, charge_point_id: str | None, transaction_id: Any) -> None:
        """Remember a charger transaction that has ended, so trailing meter values cannot reopen it."""
        txn = transaction_id_of(transaction_id)
        if not charge_point_id or txn is None:
            return
        self._closed_txn.setdefault(charge_point_id, set()).add(txn)
        if self.active_txn.get(charge_point_id) == txn:
            self.active_txn.pop(charge_point_id, None)

    def note_transaction_stopped(self, charge_point_id: str | None, transaction_id: Any) -> None:
        if not charge_point_id:
            return
        stopped = transaction_id_of(transaction_id)
        if stopped is None:
            return
        was_active = self.active_txn.get(charge_point_id) == stopped
        self.close_transaction(charge_point_id, stopped)
        if was_active:
            jlog("info", "transaction ended", chargePointId=charge_point_id, transactionId=stopped)

    def remote_stop_payload(
        self,
        charge_point_id: str,
        payload: dict[str, Any] | None,
        via: str = "",
    ) -> dict[str, Any]:
        """Send RemoteStop at the charger's live transaction when the backend id differs.

        A requested id is bound to the first live transaction it corrected. Later
        retries of that same id must not stop a newer session.
        """
        body = dict(payload or {})
        active = self.active_txn.get(charge_point_id)
        if active is None:
            return body
        requested = transaction_id_of(body.get("transactionId"))
        if requested == active:
            return body
        backend = via or ""
        if requested is not None:
            aimed = self._stop_alias.get((charge_point_id, backend, requested))
            if aimed is not None and aimed != active:
                jlog(
                    "info",
                    "RemoteStop stale id left unchanged",
                    chargePointId=charge_point_id,
                    via=backend,
                    requested=body.get("transactionId"),
                    transactionId=active,
                    alreadyStopped=aimed,
                )
                return body
            self._stop_alias[(charge_point_id, backend, requested)] = active
        jlog(
            "info",
            "RemoteStop transaction rewritten",
            chargePointId=charge_point_id,
            via=backend,
            requested=body.get("transactionId"),
            transactionId=active,
        )
        body["transactionId"] = active
        return body

    async def inject_call(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        charge_point_id: str | None = None,
        timeout: float = 12.0,
        via: str | None = None,
    ) -> dict[str, Any]:
        """Send a CSMS Call on the primary joulo link and wait for CallResult."""
        action = (action or "").strip()
        if not action:
            return {"ok": False, "error": "missing action"}
        cpid = self.pick_cpid(charge_point_id)
        if not cpid:
            return {"ok": False, "error": "charger not connected"}
        ws = self.sockets.get(cpid)
        if ws is None or ws.closed:
            return {"ok": False, "error": "charger not connected"}
        outgoing = dict(payload or {})
        if action == "RemoteStopTransaction":
            outgoing = self.remote_stop_payload(cpid, outgoing, via or "")
        unique_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[unique_id] = fut
        self.pending_meta[unique_id] = {"action": action, "via": via or "dummy"}
        raw = encode_call(unique_id, action, outgoing)
        try:
            lock = self.send_locks.setdefault(cpid, asyncio.Lock())
            async with lock:
                await ws.send_str(raw)
            emit_soon(
                src=via or "dummy",
                dst="charger",
                action=action,
                payload=outgoing,
                received=(payload or {}) if outgoing != (payload or {}) else None,
                fate="forward",
                unique_id=unique_id,
                hop="charger",
            )
            logged: dict[str, Any] = {
                "chargePointId": cpid,
                "action": action,
                "uniqueId": unique_id,
            }
            if action == "RemoteStopTransaction":
                logged["transactionId"] = outgoing.get("transactionId")
                logged["requested"] = (payload or {}).get("transactionId")
                if via:
                    logged["via"] = via
            jlog("info", "inject → charger", **logged)
            result = await asyncio.wait_for(fut, timeout)
            if action == "RemoteStopTransaction":
                result = dict(result)
                result["sentTransactionId"] = outgoing.get("transactionId")
                result["requestedTransactionId"] = (payload or {}).get("transactionId")
                result_payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                jlog(
                    "info",
                    "RemoteStop result",
                    chargePointId=cpid,
                    via=via or "",
                    transactionId=outgoing.get("transactionId"),
                    status=result_payload.get("status"),
                    ok=result.get("ok"),
                )
                if result_payload.get("status") == "Rejected":
                    self.close_transaction(cpid, outgoing.get("transactionId"))
            return result
        except TimeoutError:
            jlog("warn", "inject timeout", chargePointId=cpid, action=action, uniqueId=unique_id)
            return {"ok": False, "error": "timeout"}
        except Exception as exc:  # noqa: BLE001
            jlog("error", "inject failed", chargePointId=cpid, action=action, error=str(exc))
            return {"ok": False, "error": str(exc)}
        finally:
            self.pending.pop(unique_id, None)
            self.pending_meta.pop(unique_id, None)

    async def next_transaction_id(self) -> int:
        async with self._lock:
            self._txn += 1
            return self._txn

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "dummy-csms",
            "startedAt": self.started_at,
            "heartbeatInterval": HEARTBEAT_INTERVAL,
            "chargePoints": [
                {
                    "id": cp_id,
                    "connected": True,
                    "lastAction": info.get("lastAction"),
                    "lastSeen": info.get("lastSeen"),
                    "protocol": info.get("protocol"),
                    "activeTransactionId": self.active_txn.get(cp_id),
                }
                for cp_id, info in self.connections.items()
            ],
        }

    async def handle_call(
        self,
        action: str,
        payload: dict[str, Any],
        charge_point_id: str | None = None,
    ) -> dict[str, Any]:
        if action == "BootNotification":
            return {
                "status": "Accepted",
                "currentTime": utc_now_iso(),
                "interval": HEARTBEAT_INTERVAL,
            }
        if action == "Heartbeat":
            return {"currentTime": utc_now_iso()}
        if action == "Authorize":
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "StartTransaction":
            txn = await self.next_transaction_id()
            self.note_active_transaction(charge_point_id, txn)
            return {"transactionId": txn, "idTagInfo": {"status": "Accepted"}}
        if action == "StopTransaction":
            self.note_transaction_stopped(charge_point_id, payload.get("transactionId"))
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "MeterValues":
            self.note_active_transaction(charge_point_id, payload.get("transactionId"))
            return {}
        if action == "DataTransfer":
            return {"status": "Accepted"}
        if action in {
            "StatusNotification",
            "FirmwareStatusNotification",
            "DiagnosticsStatusNotification",
            "SecurityEventNotification",
            "LogStatusNotification",
            "SignedFirmwareStatusNotification",
        }:
            return {}
        if action == "SignCertificate":
            return {"status": "Rejected"}
        # Keep Gemini online: empty CallResult instead of NotImplemented.
        return {}


STATE = DummyCsms()


def charge_point_id_from_path(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unknown"
    if parts[0] in {"ocpp", "ws"} and len(parts) > 1:
        return parts[-1]
    return parts[-1]


async def health(_: web.Request) -> web.Response:
    return web.json_response(STATE.health())


async def inject(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    action = str(body.get("action") or "")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    cpid = str(body.get("chargePointId") or os.environ.get("CHARGE_POINT_ID") or "")
    via = str(body.get("via") or "")
    result = await STATE.inject_call(action, payload, cpid or None, via=via)
    status = 200 if result.get("ok") else 409
    if result.get("error") == "timeout":
        status = 504
    return web.json_response(result, status=status)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    cp_id = request.match_info.get("cpid") or charge_point_id_from_path(request.path)
    proto = None
    offered = request.headers.get("Sec-WebSocket-Protocol", "")
    for candidate in ("ocpp1.6", "ocpp2.0.1", "ocpp2.0"):
        if candidate in offered:
            proto = candidate
            break
    ws = web.WebSocketResponse(protocols=[proto] if proto else ["ocpp1.6"])
    await ws.prepare(request)
    negotiated = ws.ws_protocol or proto or "ocpp1.6"
    STATE.connections[cp_id] = {
        "lastAction": None,
        "lastSeen": utc_now_iso(),
        "protocol": negotiated,
    }
    STATE.sockets[cp_id] = ws
    STATE.send_locks[cp_id] = asyncio.Lock()
    jlog(
        "info",
        "charge point connected",
        chargePointId=cp_id,
        protocol=negotiated,
        path=request.path,
    )
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await _on_text(ws, cp_id, msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                break
            elif msg.type == WSMsgType.PING:
                await ws.pong(msg.data)
    finally:
        STATE.connections.pop(cp_id, None)
        STATE.sockets.pop(cp_id, None)
        STATE.send_locks.pop(cp_id, None)
        jlog("info", "charge point disconnected", chargePointId=cp_id)
    return ws


async def _on_text(ws: web.WebSocketResponse, cp_id: str, raw: str) -> None:
    try:
        parsed = decode(raw)
    except OcppDecodeError as exc:
        jlog("warn", "decode failed", chargePointId=cp_id, error=str(exc), raw=raw[:240])
        return

    STATE.connections[cp_id]["lastSeen"] = utc_now_iso()
    msg_type = parsed["messageType"]

    if msg_type == CALLRESULT:
        meta = STATE.pending_meta.get(parsed["uniqueId"]) or {}
        fut = STATE.pending.get(parsed["uniqueId"])
        result_payload = parsed.get("payload") or {}
        emit_soon(
            src="charger",
            dst=meta.get("via") or "dummy",
            action=meta.get("action") or "",
            payload=result_payload,
            fate="forward",
            unique_id=parsed["uniqueId"],
            message_type="CallResult",
            hop="charger",
        )
        if fut is not None and not fut.done():
            fut.set_result({"ok": True, "payload": result_payload})
            return
        jlog(
            "debug",
            "ignoring CallResult from charge point",
            chargePointId=cp_id,
            uniqueId=parsed["uniqueId"],
        )
        return
    if msg_type == CALLERROR:
        meta = STATE.pending_meta.get(parsed["uniqueId"]) or {}
        fut = STATE.pending.get(parsed["uniqueId"])
        error_payload = {
            "errorCode": parsed.get("errorCode") or "",
            "errorDescription": parsed.get("errorDescription") or "",
            "errorDetails": parsed.get("errorDetails") or {},
        }
        emit_soon(
            src="charger",
            dst=meta.get("via") or "dummy",
            action=meta.get("action") or "",
            payload=error_payload,
            fate="forward",
            unique_id=parsed["uniqueId"],
            message_type="CallError",
            hop="charger",
        )
        if fut is not None and not fut.done():
            fut.set_result(
                {
                    "ok": False,
                    "error": parsed.get("errorCode") or "CallError",
                    "errorDescription": parsed.get("errorDescription") or "",
                    "payload": error_payload,
                }
            )
            return
        jlog(
            "warn",
            "CallError from charge point",
            chargePointId=cp_id,
            uniqueId=parsed["uniqueId"],
            errorCode=parsed.get("errorCode"),
        )
        return
    if msg_type != CALL:
        return

    action = parsed["action"]
    unique_id = parsed["uniqueId"]
    payload = parsed.get("payload") or {}
    STATE.connections[cp_id]["lastAction"] = action
    status = charger_status_from_call(action, payload)
    if status:
        STATE.last_status = status
    jlog(
        "debug",
        "call",
        chargePointId=cp_id,
        action=action,
        uniqueId=unique_id,
    )

    if action in FORBIDDEN_OUTGOING_ACTIONS and action not in CORE_ACTIONS:
        # Incoming Calls use CP→CSMS names; never treat them as something to
        # forward. Just answer.
        pass

    try:
        result = await STATE.handle_call(action, payload, cp_id)
        await ws.send_str(encode_call_result(unique_id, result))
        emit_soon(
            src="charger",
            dst="dummy",
            action=action,
            payload=payload,
            fate="forward",
            unique_id=unique_id,
            hop="charger",
        )
        emit_soon(
            src="dummy",
            dst="charger",
            action=action,
            payload=result if isinstance(result, dict) else {},
            fate="forward",
            unique_id=unique_id,
            message_type="CallResult",
            hop="charger",
        )
    except Exception as exc:  # noqa: BLE001
        jlog("error", "handler failed", chargePointId=cp_id, action=action, error=str(exc))
        error_payload = {"errorCode": "InternalError", "errorDescription": str(exc), "errorDetails": {}}
        await ws.send_str(encode_call_error(unique_id, "InternalError", str(exc), {}))
        emit_soon(
            src="dummy",
            dst="charger",
            action=action,
            payload=error_payload,
            fate="forward",
            unique_id=unique_id,
            message_type="CallError",
            hop="charger",
        )


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/healthz", health)
    app.router.add_post("/inject", inject)
    app.router.add_get("/", ws_handler)
    app.router.add_get("/{cpid}", ws_handler)
    app.router.add_get("/ocpp/{cpid}", ws_handler)
    app.router.add_get("/ws/{cpid}", ws_handler)
    return app


async def _main() -> None:
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    jlog(
        "info",
        "dummy-csms listening",
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        interval=HEARTBEAT_INTERVAL,
    )
    stop = asyncio.Event()
    poll_task = asyncio.create_task(status_poll_loop())

    def _stop(*_: Any) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass
    await stop.wait()
    poll_task.cancel()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_main())
