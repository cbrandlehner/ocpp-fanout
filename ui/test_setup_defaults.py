from setup_defaults import hydrate_charger


def test_hydrate_charger_interval_defaults():
    cfg = {"charger": {"cpid": "x", "listenPort": 9100}}
    changed = hydrate_charger(cfg)
    assert changed is True
    assert cfg["charger"]["statusIntervalChargingSec"] == 60
    assert cfg["charger"]["statusIntervalIdleSec"] == 7
    assert cfg["charger"]["cpid"] == "x"


def test_hydrate_charger_keeps_existing_intervals():
    cfg = {
        "charger": {
            "statusIntervalChargingSec": 120,
            "statusIntervalIdleSec": 30,
        }
    }
    assert hydrate_charger(cfg) is False
    assert cfg["charger"]["statusIntervalChargingSec"] == 120
    assert cfg["charger"]["statusIntervalIdleSec"] == 30


def test_hydrate_charger_creates_charger_block():
    cfg: dict = {}
    assert hydrate_charger(cfg) is True
    assert cfg["charger"]["statusIntervalChargingSec"] == 60
    assert cfg["charger"]["statusIntervalIdleSec"] == 7
