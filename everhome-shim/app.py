"""Read-only OCPP CSMS shim (EverHome / Enphase).

joulo-ocpp-proxy forwards charger → secondary CSMS but discards
CSMS → charger Calls. Enphase (and EverHome) then keep asking for
GetConfiguration / TriggerMessage and never show live data.

This service:
- accepts joulo with ocpp1.6 (so the proxy stays connected)
- connects upstream (EverHome may omit the subprotocol echo)
- forwards charger → upstream messages
- answers CSMS Calls locally (never towards the wallbox)
- replays the last StatusNotification/Boot/Heartbeat/MeterValues
  when the CSMS sends TriggerMessage
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from ocpp_codec import CALL, CALLERROR, CALLRESULT, decode, encode_call, encode_call_result
from ui_events import emit_soon

CHARGE_POINT_CALLS = frozenset(
    {
        "Authorize",
        "BootNotification",
        "DataTransfer",
        "DiagnosticsStatusNotification",
        "FirmwareStatusNotification",
        "Heartbeat",
        "LogStatusNotification",
        "MeterValues",
        "SecurityEventNotification",
        "SignCertificate",
        "SignedFirmwareStatusNotification",
        "StartTransaction",
        "StatusNotification",
        "StopTransaction",
    }
)


def should_forward_to_upstream(raw: str) -> bool:
    """Drop CSMS→CP Calls that Joulo leaked onto the secondary link."""
    try:
        parsed = decode(raw)
    except Exception:  # noqa: BLE001
        return True
    if parsed.get("messageType") != CALL:
        return True
    action = parsed.get("action") or ""
    return action in CHARGE_POINT_CALLS


LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
LISTEN_HOST = os.environ.get("SHIM_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("SHIM_PORT", "9003"))
SHIM_NAME = os.environ.get("SHIM_NAME", "csms-shim")
UPSTREAM = (
    os.environ.get("UPSTREAM_OCPP_URL", "").strip()
    or os.environ.get("EVERHOME_OCPP_URL", "").strip()
)
APPEND_CPID = os.environ.get("APPEND_CPID", "false").lower() in {"1", "true", "yes"}
CHARGE_POINT_ID = os.environ.get("CHARGE_POINT_ID", "").strip()
INJECT_URL = os.environ.get("INJECT_URL", "http://dummy-csms:9001/inject").rstrip("/")
UI_CONFIG_URL = os.environ.get("UI_CONFIG_URL", "http://ui:8080/api/config").strip()
ALLOWED_ENV = os.environ.get("ALLOWED_ACTIONS", "").strip()

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(message)s")
log = logging.getLogger(SHIM_NAME)

# key -> (value, readonly). Mirrors a go-e Gemini so CSMS ChangeConfiguration
# can be Accepted locally without writing through to the wallbox.
DEFAULT_CONFIG: dict[str, tuple[str, bool]] = {
    "AccessControl": ("Open", False),
    "AllowOfflineTxForUnknownId": ("false", False),
    "AuthorizationCacheEnabled": ("false", False),
    "AuthorizeRemoteTxRequests": ("true", False),
    "ChargeProfileMaxStackLevel": ("20", True),
    "ChargingScheduleAllowedChargingRateUnit": ("Current", True),
    "ChargingScheduleMaxPeriods": ("20", True),
    "ClockAlignedDataInterval": ("0", False),
    "ConnectionTimeOut": ("120", False),
    "ConnectorPhaseRotation": ("0.RST,1.RST", True),
    "ConnectorPhaseRotationMaxLength": ("3", True),
    "ConnectorSwitch3to1PhaseSupported": ("true", True),
    "ForceState": ("On", False),
    "GetConfigurationMaxKeys": ("99", True),
    "HeartbeatInterval": ("300", False),
    "ISO15118PnCEnabled": ("false", False),
    "LightIntensity": ("37", False),
    "LocalAuthListEnabled": ("false", False),
    "LocalAuthListMaxLength": ("10", True),
    "LocalAuthorizeOffline": ("true", False),
    "LocalPreAuthorize": ("false", False),
    "MaxChargingProfilesInstalled": ("20", True),
    "MeterValueSampleInterval": ("7", False),
    "MeterValuesAlignedData": (
        "Current.Import.L1,Current.Import.L2,Current.Import.L3,Current.Offered,"
        "Energy.Active.Import.Register,Power.Active.Import.L1,Power.Active.Import.L2,"
        "Power.Active.Import.L3,Power.Active.Import,Power.Offered",
        False,
    ),
    "MeterValuesAlignedDataMaxLength": ("1", True),
    "MeterValuesSampledData": (
        "Current.Import.L1,Current.Import.L2,Current.Import.L3,"
        "Energy.Active.Import.Register,Power.Active.Import",
        False,
    ),
    "MeterValuesSampledDataMaxLength": ("1", True),
    "MinChargingCurrent": ("6", False),
    "MinimumStatusDuration": ("0", False),
    "NumberOfConnectors": ("1", True),
    "PlugAndChargeIdentifier": ("no-card", False),
    "ResetRetries": ("0", True),
    "SendLocalListMaxLength": ("10", True),
    "StopChargingOnReboot": ("false", False),
    "StopTransactionOnEVSideDisconnect": ("true", False),
    "StopTransactionOnInvalidId": ("true", False),
    "StopTxnAlignedData": ("", False),
    "StopTxnSampledData": ("Energy.Active.Import.Register", False),
    "SupportedFeatureProfiles": (
        "Core,FirmwareManagement,LocalAuthListManagement,SmartCharging,RemoteTrigger",
        True,
    ),
    "SupportedFeatureProfilesMaxLength": ("6", True),
    "TransactionMessageAttempts": ("3", False),
    "UnlockConnectorOnEVSideDisconnect": ("true", False),
    "WebSocketPingInterval": ("10", False),
}


_setup_memo: dict[str, Any] = {
    "at": 0.0,
    "url": "",
    "appendCpid": None,
    "cpid": "",
    "allowed": [],
}


def parse_allowed(raw: list[Any] | str | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


def command_allowed(action: str, payload: dict[str, Any] | None, allowed: list[str]) -> bool:
    """True when Setup allowlisted this CSMS Call for forwarding to the charger."""
    payload = payload or {}
    req = str(payload.get("requestedMessage") or "")
    for item in allowed:
        if item == action:
            return True
        if ":" in item:
            name, wanted = item.split(":", 1)
            if action == name and (wanted == "*" or wanted == req):
                return True
        if req and item == f"{action}:{req}":
            return True
    return False


async def fetch_setup_config() -> dict[str, Any]:
    """URL, appendCpid, cpid, allowlist — env first, Setup UI overrides when set."""
    loop = asyncio.get_running_loop()
    now = loop.time()
    if now - float(_setup_memo["at"]) < 2.0:
        return dict(_setup_memo)
    url = UPSTREAM
    append: bool | None = APPEND_CPID
    cpid = CHARGE_POINT_ID
    allowed = parse_allowed(ALLOWED_ENV)
    if UI_CONFIG_URL:
        try:
            timeout = ClientTimeout(total=1.5)
            async with ClientSession(timeout=timeout) as session:
                async with session.get(UI_CONFIG_URL) as resp:
                    cfg = await resp.json()
            charger = cfg.get("charger") or {}
            if str(charger.get("cpid") or "").strip():
                cpid = str(charger.get("cpid")).strip()
            node = backend_node()
            for sec in cfg.get("secondaries") or []:
                if str(sec.get("id") or sec.get("kind") or "") != node:
                    continue
                if str(sec.get("url") or "").strip():
                    url = str(sec.get("url")).strip()
                if "appendCpid" in sec:
                    append = bool(sec.get("appendCpid"))
                allowed = parse_allowed(sec.get("allowedCommands") or [])
                break
        except Exception as exc:  # noqa: BLE001
            jlog("debug", "config fetch failed", error=str(exc))
    _setup_memo.update(at=now, url=url, appendCpid=append, cpid=cpid, allowed=allowed)
    return dict(_setup_memo)


async def fetch_allowed_commands() -> list[str]:
    cfg = await fetch_setup_config()
    return list(cfg.get("allowed") or [])


async def inject_to_charger(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not INJECT_URL:
        return {"ok": False, "error": "INJECT_URL empty"}
    body: dict[str, Any] = {
        "action": action,
        "payload": payload or {},
        "via": backend_node(),
    }
    setup = await fetch_setup_config()
    cpid = str(setup.get("cpid") or CHARGE_POINT_ID or "")
    if cpid:
        body["chargePointId"] = cpid
    try:
        timeout = ClientTimeout(total=15)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(INJECT_URL, json=body) as resp:
                data = await resp.json(content_type=None)
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": "invalid inject response"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def backend_node() -> str:
    name = SHIM_NAME.lower()
    if "enphase" in name:
        return "enphase"
    if "everhome" in name:
        return "everhome"
    if "monta" in name:
        return "monta"
    return name

# Answer locally with spec-valid success so the CSMS stops retrying,
# without forwarding (no RFID overwrite, no profile/config write on the CP).
CONTROL_ACCEPT = frozenset(
    {
        "ClearChargingProfile",
        "SendLocalList",
        "ClearCache",
    }
)
CONTROL_REJECT = frozenset(
    {
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "Reset",
        "UnlockConnector",
        "SetChargingProfile",
        "ChangeAvailability",
        "GetDiagnostics",
        "UpdateFirmware",
        "ReserveNow",
        "CancelReservation",
    }
)

REPLAYABLE = frozenset(
    {"StatusNotification", "BootNotification", "Heartbeat", "MeterValues"}
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def jlog(level: str, msg: str, **fields: Any) -> None:
    payload = {"time": utc_now_iso(), "level": level, "tag": SHIM_NAME, "msg": msg}
    payload.update(fields)
    getattr(log, "warning" if level == "warn" else level)(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def resolve_upstream(
    url: str,
    append: bool | None = None,
    cpid: str | None = None,
) -> str:
    url = url.rstrip("/")
    use_append = APPEND_CPID if append is None else append
    use_cpid = CHARGE_POINT_ID if cpid is None else (cpid or "")
    if use_append and use_cpid and not url.endswith("/" + use_cpid):
        url = f"{url}/{use_cpid}"
    return url


def patch_get_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    """Advertise RemoteStart support so Monta AutoStart is not 'unsupported'."""
    keys = [dict(item) for item in payload.get("configurationKey") or []]
    found = False
    for item in keys:
        if item.get("key") == "AuthorizeRemoteTxRequests":
            item["value"] = "true"
            item["readonly"] = False
            found = True
            break
    if not found:
        keys.append({"key": "AuthorizeRemoteTxRequests", "value": "true", "readonly": False})
    out = dict(payload)
    out["configurationKey"] = keys
    unknown = [k for k in (payload.get("unknownKey") or []) if k != "AuthorizeRemoteTxRequests"]
    if unknown:
        out["unknownKey"] = unknown
    else:
        out.pop("unknownKey", None)
    return out


def get_configuration_result(
    payload: dict[str, Any], config: dict[str, tuple[str, bool]]
) -> dict[str, Any]:
    requested = payload.get("key")
    if not requested:
        requested = list(config)
    found: list[dict[str, Any]] = []
    unknown: list[str] = []
    for key in requested:
        entry = config.get(str(key))
        if entry is None:
            unknown.append(str(key))
            continue
        value, readonly = entry
        found.append({"key": str(key), "readonly": readonly, "value": value})
    result: dict[str, Any] = {"configurationKey": found}
    if unknown:
        result["unknownKey"] = unknown
    return result


def apply_change_configuration(
    payload: dict[str, Any], config: dict[str, tuple[str, bool]]
) -> dict[str, Any]:
    key = str(payload.get("key") or "")
    value = payload.get("value")
    if not key:
        return {"status": "Rejected"}
    if key in config:
        current, readonly = config[key]
        if readonly:
            return {"status": "Rejected"}
        config[key] = (str(value) if value is not None else current, False)
        return {"status": "Accepted"}
    config[key] = (str(value) if value is not None else "", False)
    return {"status": "Accepted"}


def default_status_notification() -> dict[str, Any]:
    return {
        "connectorId": 1,
        "errorCode": "NoError",
        "status": "Available",
        "timestamp": utc_now_iso(),
    }


def reply_to_csms_call(
    action: str,
    payload: dict[str, Any] | None = None,
    config: dict[str, tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """Local CallResults so the CSMS sees a live CP, without controlling the box."""
    payload = payload or {}
    config = config if config is not None else DEFAULT_CONFIG
    if action == "TriggerMessage":
        return {"status": "Accepted"}
    if action == "GetConfiguration":
        return patch_get_configuration(get_configuration_result(payload, config))
    if action == "ChangeConfiguration":
        return apply_change_configuration(payload, config)
    if action == "DataTransfer":
        return {"status": "Rejected"}
    if action == "GetLocalListVersion":
        return {"listVersion": 0}
    if action in CONTROL_ACCEPT:
        return {"status": "Accepted"}
    if action in CONTROL_REJECT:
        return {"status": "Rejected"}
    return {}


STATE: dict[str, Any] = {
    "sessions": 0,
    "upstreamConnected": False,
    "lastForward": None,
    "lastCsmsCall": None,
    "lastReplay": None,
    "config": dict(DEFAULT_CONFIG),
    "lastCpCalls": {},
    "cp_txn": None,
    "csms_txn": None,
    "pending_start": None,
    "start_wait": None,
}

CACHE_ACTIONS = REPLAYABLE | {"StartTransaction", "StopTransaction", "Authorize"}


def cache_cp_call(action: str, payload: dict[str, Any]) -> None:
    if action in CACHE_ACTIONS:
        STATE["lastCpCalls"][action] = payload


def _energy_wh(payload: dict[str, Any]) -> int:
    for mv in payload.get("meterValue") or []:
        for sampled in mv.get("sampledValue") or []:
            if str(sampled.get("measurand") or "") != "Energy.Active.Import.Register":
                continue
            try:
                return int(float(sampled.get("value")))
            except (TypeError, ValueError):
                continue
    return 0


def synthesize_start_transaction(meter_payload: dict[str, Any]) -> dict[str, Any]:
    start = STATE["lastCpCalls"].get("StartTransaction") or {}
    auth = STATE["lastCpCalls"].get("Authorize") or {}
    id_tag = str(start.get("idTag") or auth.get("idTag") or "NOCARD")[:20]
    return {
        "connectorId": meter_payload.get("connectorId") or start.get("connectorId") or 1,
        "idTag": id_tag,
        "meterStart": _energy_wh(meter_payload),
        "timestamp": utc_now_iso(),
    }


def note_csms_start_result(unique_id: str, payload: dict[str, Any] | None) -> None:
    if unique_id != STATE.get("pending_start"):
        return
    payload = payload or {}
    txn = payload.get("transactionId")
    jlog("info", "CSMS StartTransaction result", uniqueId=unique_id, transactionId=txn, payload=payload)
    if txn is not None:
        STATE["csms_txn"] = txn
    wait = STATE.get("start_wait")
    if wait is not None and not wait.done():
        wait.set_result(True)
    STATE["pending_start"] = None


def rewrite_txn_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action not in {"MeterValues", "StopTransaction"}:
        return payload
    if STATE.get("csms_txn") is None:
        return payload
    if payload.get("transactionId") is None:
        return payload
    out = dict(payload)
    out["transactionId"] = STATE["csms_txn"]
    return out


def clear_txn_map() -> None:
    STATE["cp_txn"] = None
    STATE["csms_txn"] = None
    STATE["pending_start"] = None
    wait = STATE.get("start_wait")
    if wait is not None and not wait.done():
        wait.set_result(False)
    STATE["start_wait"] = None


def replay_for_trigger(requested: str) -> tuple[str, dict[str, Any]] | None:
    if requested not in REPLAYABLE:
        return None
    payload = STATE["lastCpCalls"].get(requested)
    if payload is None and requested == "StatusNotification":
        payload = default_status_notification()
    if payload is None and requested == "Heartbeat":
        payload = {"currentTime": utc_now_iso()}
    if payload is None:
        return None
    return requested, payload


async def health(_: web.Request) -> web.Response:
    upstream_display = resolve_upstream(UPSTREAM) if UPSTREAM else ""
    if len(upstream_display) > 48:
        upstream_display = upstream_display[:48] + "…"
    return web.json_response(
        {
            "status": "ok" if UPSTREAM else "misconfigured",
            "service": SHIM_NAME,
            "upstream": upstream_display,
            "sessions": STATE["sessions"],
            "upstreamConnected": STATE["upstreamConnected"],
            "lastForward": STATE["lastForward"],
            "lastCsmsCall": STATE["lastCsmsCall"],
            "lastReplay": STATE["lastReplay"],
        }
    )


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(protocols=["ocpp1.6", "ocpp2.0.1", "ocpp2.0"])
    await ws.prepare(request)
    STATE["sessions"] += 1
    jlog("info", "joulo connected", protocol=ws.ws_protocol, path=request.path)

    setup = await fetch_setup_config()
    upstream_raw = str(setup.get("url") or "").strip()
    if not upstream_raw:
        jlog("error", "no upstream URL in env or Setup")
        await ws.close(code=1011, message=b"no upstream")
        return ws

    upstream_url = resolve_upstream(
        upstream_raw,
        append=setup.get("appendCpid"),
        cpid=str(setup.get("cpid") or ""),
    )
    timeout = ClientTimeout(total=None, sock_connect=15, sock_read=None)
    session = ClientSession(timeout=timeout)
    upstream = None
    try:
        # Request ocpp1.6 but do not require the server to echo it (aiohttp allows None).
        upstream = await session.ws_connect(
            upstream_url,
            protocols=["ocpp1.6"],
            ssl=upstream_url.startswith("wss://"),
            heartbeat=30,
        )
        STATE["upstreamConnected"] = True
        jlog("info", "upstream connected", negotiated=upstream.protocol, url=upstream_url)

        async def ensure_csms_transaction(meter_payload: dict[str, Any]) -> None:
            if STATE.get("csms_txn") is not None:
                return
            wait = STATE.get("start_wait")
            if wait is not None and not wait.done():
                try:
                    await asyncio.wait_for(asyncio.shield(wait), 10)
                except TimeoutError:
                    jlog("warn", "waiting for CSMS StartTransaction result timed out")
                return
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            STATE["start_wait"] = fut
            uid = str(uuid.uuid4())
            STATE["pending_start"] = uid
            STATE["cp_txn"] = meter_payload.get("transactionId")
            start_payload = synthesize_start_transaction(meter_payload)
            jlog("info", "replay StartTransaction to csms", transactionId=STATE["cp_txn"])
            emit_soon(
                src="charger",
                dst=backend_node(),
                action="StartTransaction",
                payload=start_payload,
                fate="forward",
                unique_id=uid,
            )
            await upstream.send_str(encode_call(uid, "StartTransaction", start_payload))
            try:
                await asyncio.wait_for(asyncio.shield(fut), 10)
            except TimeoutError:
                jlog("warn", "CSMS StartTransaction timeout")

        async def pump_joulo_to_upstream() -> None:
            async for msg in ws:
                if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                    raw = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                    STATE["lastForward"] = utc_now_iso()
                    outgoing = raw
                    forward = should_forward_to_upstream(raw)
                    try:
                        parsed = decode(raw)
                        if parsed["messageType"] == CALL:
                            action = parsed.get("action") or ""
                            payload = parsed.get("payload") or {}
                            if not forward:
                                jlog("debug", "drop leaked CSMS call", action=action)
                            else:
                                cache_cp_call(action, payload)
                                emit_soon(src="charger", dst=backend_node(), action=action, payload=payload, fate="forward", unique_id=parsed.get("uniqueId"))
                                if action == "StartTransaction":
                                    loop = asyncio.get_running_loop()
                                    STATE["pending_start"] = parsed["uniqueId"]
                                    STATE["start_wait"] = loop.create_future()
                                elif action in {"MeterValues", "StopTransaction"}:
                                    if payload.get("transactionId") is not None:
                                        STATE["cp_txn"] = payload.get("transactionId")
                                    if STATE.get("csms_txn") is None:
                                        await ensure_csms_transaction(payload if action == "MeterValues" else STATE["lastCpCalls"].get("MeterValues") or payload)
                                    rewritten = rewrite_txn_payload(action, payload)
                                    if rewritten is not payload and rewritten.get("transactionId") != payload.get("transactionId"):
                                        outgoing = encode_call(parsed["uniqueId"], action, rewritten)
                                if action == "StopTransaction":
                                    clear_txn_map()
                    except Exception as exc:  # noqa: BLE001
                        jlog("debug", "forward rewrite skipped", error=str(exc))
                    if not forward:
                        continue
                    jlog("debug", "forward → upstream", preview=outgoing[:180])
                    if not upstream.closed:
                        await upstream.send_str(outgoing)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                    break

        async def pump_upstream_to_local() -> None:
            async for msg in upstream:
                if msg.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                        break
                    continue
                raw = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                try:
                    parsed = decode(raw)
                except Exception as exc:  # noqa: BLE001
                    jlog("warn", "upstream decode failed", error=str(exc), preview=raw[:180])
                    continue
                if parsed["messageType"] == CALL:
                    action = parsed.get("action") or ""
                    payload = parsed.get("payload") or {}
                    unique_id = parsed["uniqueId"]
                    STATE["lastCsmsCall"] = action
                    allowed = await fetch_allowed_commands()
                    if command_allowed(action, payload, allowed):
                        jlog("info", "csms call (forward to charger)", action=action)
                        emit_soon(
                            src=backend_node(),
                            dst="charger",
                            action=action,
                            payload=payload,
                            fate="forward",
                            unique_id=unique_id,
                        )
                        injected = await inject_to_charger(action, payload)
                        if injected.get("ok"):
                            result_payload = injected.get("payload") or {}
                            if action == "GetConfiguration":
                                result_payload = patch_get_configuration(result_payload)
                            await upstream.send_str(encode_call_result(unique_id, result_payload))
                            continue
                        jlog(
                            "warn",
                            "inject failed, answering locally",
                            action=action,
                            error=injected.get("error"),
                        )
                    jlog("info", "csms call (answered locally)", action=action)
                    emit_soon(
                        src=backend_node(),
                        dst="charger",
                        action=action,
                        payload=payload,
                        fate="drop",
                        unique_id=unique_id,
                    )
                    await upstream.send_str(
                        encode_call_result(
                            unique_id,
                            reply_to_csms_call(action, payload, STATE["config"]),
                        )
                    )
                    if action == "TriggerMessage":
                        requested = str(payload.get("requestedMessage") or "")
                        replay = replay_for_trigger(requested)
                        if replay is not None:
                            r_action, r_payload = replay
                            STATE["lastReplay"] = r_action
                            jlog("info", "replay to csms", action=r_action)
                            await upstream.send_str(
                                encode_call(str(uuid.uuid4()), r_action, r_payload)
                            )
                elif parsed["messageType"] == CALLRESULT:
                    note_csms_start_result(parsed["uniqueId"], parsed.get("payload") or {})
                    jlog("debug", "upstream CallResult dropped", uniqueId=parsed["uniqueId"])
                elif parsed["messageType"] == CALLERROR:
                    note_csms_start_result(parsed["uniqueId"], {})
                    jlog("warn", "upstream CallError", uniqueId=parsed["uniqueId"])

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(pump_joulo_to_upstream()),
                asyncio.create_task(pump_upstream_to_local()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                jlog("warn", "pump stopped", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        jlog("error", "shim session failed", error=str(exc))
    finally:
        STATE["upstreamConnected"] = False
        STATE["sessions"] = max(0, STATE["sessions"] - 1)
        if upstream is not None and not upstream.closed:
            await upstream.close()
        await session.close()
        if not ws.closed:
            await ws.close()
        jlog("info", "session ended")
    return ws


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/healthz", health)
    app.router.add_get("/", ws_handler)
    app.router.add_get("/{path:.*}", ws_handler)
    return app


async def _main() -> None:
    if not UPSTREAM:
        jlog("warn", "starting without UPSTREAM_OCPP_URL — health only")
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    jlog(
        "info",
        "shim listening",
        port=LISTEN_PORT,
        name=SHIM_NAME,
        upstream=bool(UPSTREAM),
        appendCpid=APPEND_CPID,
    )
    stop = asyncio.Event()

    def _stop(*_: Any) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass
    await stop.wait()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_main())
