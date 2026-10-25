"""Fire-and-forget OCPP live events to the fanout UI hub."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

log = logging.getLogger("ui-events")
UI_EVENT_URL = os.environ.get("UI_EVENT_URL", "").strip()


def event_body(
    *,
    src: str,
    dst: str,
    action: str = "",
    payload: dict[str, Any] | None = None,
    received: dict[str, Any] | None = None,
    fate: str = "forward",
    unique_id: str | None = None,
    message_type: str = "Call",
    hop: str = "",
) -> dict[str, Any]:
    """One OCPP frame as it crossed a socket.

    ``payload`` is the body on the wire. ``received`` is the body before a
    rewrite, and is omitted when nothing changed. ``hop`` is ``upstream``
    (shim to a backend) or ``charger`` (dummy to the wallbox).
    """
    wire = payload or {}
    body: dict[str, Any] = {
        "src": src,
        "dst": dst,
        "action": action or "",
        "payload": wire,
        "fate": fate,
        "uniqueId": unique_id,
        "messageType": message_type or "Call",
    }
    if hop:
        body["hop"] = hop
    if received is not None and received != wire:
        body["received"] = received
    return body


async def emit_ocpp_event(
    *,
    src: str,
    dst: str,
    action: str,
    payload: dict[str, Any] | None = None,
    received: dict[str, Any] | None = None,
    fate: str = "forward",
    unique_id: str | None = None,
    message_type: str = "Call",
    hop: str = "",
) -> None:
    if not UI_EVENT_URL:
        return
    body = event_body(
        src=src,
        dst=dst,
        action=action,
        payload=payload,
        received=received,
        fate=fate,
        unique_id=unique_id,
        message_type=message_type,
        hop=hop,
    )
    try:
        timeout = aiohttp.ClientTimeout(total=0.8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(UI_EVENT_URL, json=body) as resp:
                await resp.read()
    except Exception as exc:  # noqa: BLE001
        log.debug("ui event failed: %s", exc)


def emit_soon(**kwargs: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(emit_ocpp_event(**kwargs))
