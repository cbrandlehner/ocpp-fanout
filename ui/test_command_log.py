from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from command_log import CommandLog, TZ


def test_write_and_cleanup(tmp_path: Path) -> None:
    log = CommandLog(tmp_path)
    path = log.write(
        {
            "src": "enphase",
            "dst": "charger",
            "action": "ChangeConfiguration",
            "fate": "drop",
            "uniqueId": "abc",
            "label": "Change MeterValueSampleInterval",
            "payload": {"key": "MeterValueSampleInterval", "value": "7"},
        }
    )
    assert path is not None and path.exists()
    line = path.read_text(encoding="utf-8").strip()
    assert "MeterValueSampleInterval" in line
    assert '"value":"7"' in line

    old = tmp_path / "ocpp-commands-2020-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    yesterday = (datetime.now(TZ).date() - timedelta(days=1)).isoformat()
    keep = tmp_path / f"ocpp-commands-{yesterday}.jsonl"
    keep.write_text("{}\n", encoding="utf-8")
    removed = log.cleanup()
    assert old in removed
    assert not old.exists()
    assert keep.exists()
