"""Fire-and-forget OCPP live events to the fanout UI hub."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

log = logging.getLogger("ui-events")
UI_EVENT_URL = os.environ.get("UI_EVENT_URL", "").strip()


async def emit_ocpp_event(
    *,
    src: str,
    dst: str,
    action: str,
    payload: dict[str, Any] | None = None,
    fate: str = "forward",
    unique_id: str | None = None,
) -> None:
    if not UI_EVENT_URL:
        return
    body = {
        "src": src,
        "dst": dst,
        "action": action or "",
        "payload": payload or {},
        "fate": fate,
        "uniqueId": unique_id,
    }
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
