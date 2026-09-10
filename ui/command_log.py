"""Daily JSONL log of OCPP Calls (with payload). Drops files older than one day."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Vienna"))
KEEP_DAYS = int(os.environ.get("OCPP_LOG_KEEP_DAYS", "1"))
MAX_BYTES = int(os.environ.get("OCPP_LOG_MAX_BYTES", str(80 * 1024 * 1024)))
MAX_PAYLOAD = int(os.environ.get("OCPP_LOG_MAX_PAYLOAD", "32000"))


class CommandLog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._warned_full = False

    def path_for(self, day: datetime | None = None) -> Path:
        when = day or datetime.now(TZ)
        return self.directory / f"ocpp-commands-{when.date().isoformat()}.jsonl"

    def cleanup(self, now: datetime | None = None) -> list[Path]:
        self.directory.mkdir(parents=True, exist_ok=True)
        today = (now or datetime.now(TZ)).date()
        cutoff = today - timedelta(days=KEEP_DAYS)
        removed: list[Path] = []
        for path in self.directory.glob("ocpp-commands-*.jsonl"):
            stamp = path.stem.removeprefix("ocpp-commands-")
            try:
                day = datetime.strptime(stamp, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                path.unlink(missing_ok=True)
                removed.append(path)
        return removed

    def _payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = payload or {}
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(raw) <= MAX_PAYLOAD:
            return data
        return {"_truncated": True, "bytes": len(raw), "preview": raw[:MAX_PAYLOAD]}

    def write(self, record: dict[str, Any]) -> Path | None:
        self.cleanup()
        path = self.path_for()
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            if not self._warned_full:
                self._warned_full = True
            return None
        line = {
            "ts": datetime.now(TZ).isoformat(timespec="milliseconds"),
            "src": record.get("src"),
            "dst": record.get("dst"),
            "action": record.get("action") or "",
            "fate": record.get("fate") or "forward",
            "uniqueId": record.get("uniqueId"),
            "label": record.get("label") or "",
            "payload": self._payload(record.get("payload")),
        }
        encoded = json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
        return path
