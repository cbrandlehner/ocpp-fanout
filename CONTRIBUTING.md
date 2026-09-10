# Contributing

## Tests

```bash
(cd dummy-csms && pip install -r requirements-dev.txt && pytest -q)
(cd everhome-shim && pip install -r requirements-dev.txt && pytest -q)
(cd ui && pip install pytest && pytest -q)
```

Dockerfiles for `dummy-csms` and `everhome-shim` run the same tests in a build stage.

## Secrets

Do not commit `.env`, charger serials, vendor OCPP URLs, or LAN addresses.
Use `.env.example` as the template. Runtime config lives in the `ui-data` volume.
