"""ocpp-fanout configuration + live event hub."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from command_log import CommandLog
from labels import apply_charger_state, label_for
from setup_defaults import hydrate_charger

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("UI_DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
LOG_DIR = Path(os.environ.get("OCPP_LOG_DIR", str(DATA_DIR / "logs")))
DEFAULT_CONFIG = json.loads((ROOT / "default_config.json").read_text())
COMMAND_LOG = CommandLog(LOG_DIR)

app = FastAPI(title="ocpp-fanout")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class EventIn(BaseModel):
    src: str
    dst: str
    action: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    fate: str = "forward"
    uniqueId: str | None = None


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.recent: list[dict[str, Any]] = []

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.recent.append(event)
        self.recent = self.recent[-80:]
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


HUB = Hub()
CHARGER_STATE: dict[str, Any] = {
    "status": None,
    "watts": None,
    "plugged": False,
    "charging": False,
}


def charger_public() -> dict[str, Any]:
    amps = CHARGER_STATE.get("amps") or {}
    return {
        "status": CHARGER_STATE["status"],
        "watts": CHARGER_STATE["watts"],
        "plugged": bool(CHARGER_STATE["plugged"]),
        "charging": bool(CHARGER_STATE["charging"]),
        "phases": int(CHARGER_STATE.get("phases") or 0),
        "amps": [amps.get("L1"), amps.get("L2"), amps.get("L3")],
        "kw": CHARGER_STATE.get("kw") or 0,
    }


ENV_URLS = {
    "everhome": "EVERHOME_OCPP_URL",
    "enphase": "ENPHASE_OCPP_URL",
    "monta": "MONTA_OCPP_URL",
}


def hydrate_urls(cfg: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Fill blank secondary URLs from the stack env so Setup matches the running shims."""
    changed = False
    for sec in cfg.get("secondaries") or []:
        kind = str(sec.get("kind") or sec.get("id") or "")
        env_key = ENV_URLS.get(kind)
        if env_key and not str(sec.get("url") or "").strip():
            value = os.environ.get(env_key, "").strip()
            if value:
                sec["url"] = value
                changed = True
        if "allowedCommands" not in sec:
            sec["allowedCommands"] = []
            changed = True
    return cfg, changed


def load_config() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg, filled = hydrate_urls(cfg)
    if hydrate_charger(cfg):
        filled = True
    if filled or not CONFIG_PATH.exists():
        save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hydrate_charger(cfg)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return load_config()


@app.put("/api/config")
async def put_config(body: dict[str, Any]) -> dict[str, Any]:
    return save_config(body)


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {
        "ok": True,
        "clients": len(HUB.clients),
        "recent": len(HUB.recent),
        "configPath": str(CONFIG_PATH),
        "logPath": str(COMMAND_LOG.path_for()),
        "charger": charger_public(),
    }


@app.get("/api/charger")
async def charger() -> dict[str, Any]:
    return charger_public()


@app.get("/api/catalog")
async def catalog() -> dict[str, Any]:
    return {
        "secondaries": [
            {"id": "enphase", "label": "Enphase IQ Energy Router", "profile": "answer", "appendCpid": True, "photo": "/static/img/enphase-iq-router.png"},
            {"id": "everhome", "label": "EverHome", "profile": "lax-ws", "appendCpid": False, "photo": "/static/img/everhome-ecotracker.png"},
            {"id": "monta", "label": "Monta", "profile": "answer", "appendCpid": False, "photo": "/static/img/monta.svg"},
            {"id": "joulo", "label": "Joulo", "profile": "mirror", "appendCpid": True, "photo": "/static/img/joulo.svg"},
            {"id": "homeassistant", "label": "Home Assistant OCPP", "profile": "answer", "appendCpid": True, "photo": "/static/img/ha.svg"},
            {"id": "steve", "label": "SteVe", "profile": "answer", "appendCpid": True, "photo": "/static/img/steve.svg"},
            {"id": "custom", "label": "Custom CSMS", "profile": "answer", "appendCpid": True, "photo": "/static/img/custom.svg"},
        ]
    }


@app.post("/internal/event")
async def internal_event(body: EventIn) -> JSONResponse:
    if body.src == "charger":
        CHARGER_STATE.update(apply_charger_state(CHARGER_STATE, body.action, body.payload))
    event = {
        "id": str(uuid.uuid4()),
        "src": body.src,
        "dst": body.dst,
        "action": body.action,
        "label": label_for(body.action, body.payload),
        "fate": body.fate if body.fate in {"forward", "drop"} else "forward",
        "uniqueId": body.uniqueId,
        "charger": charger_public(),
    }
    try:
        COMMAND_LOG.write(
            {
                "src": event["src"],
                "dst": event["dst"],
                "action": event["action"],
                "fate": event["fate"],
                "uniqueId": event["uniqueId"],
                "label": event["label"],
                "payload": body.payload,
            }
        )
    except Exception:
        pass
    await HUB.broadcast(event)
    return JSONResponse({"ok": True, "label": event["label"]})


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    HUB.clients.add(ws)
    try:
        await ws.send_json({"type": "snapshot", "charger": charger_public()})
        for event in HUB.recent[-20:]:
            await ws.send_json(event)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        HUB.clients.discard(ws)


@app.on_event("startup")
async def _startup() -> None:
    load_config()
    COMMAND_LOG.cleanup()
    asyncio.get_event_loop()
