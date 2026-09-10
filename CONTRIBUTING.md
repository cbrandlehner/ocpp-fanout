# Contributing

## Tests

```bash
(cd dummy-csms && pip install -r requirements-dev.txt && pytest -q)
(cd everhome-shim && pip install -r requirements-dev.txt && pytest -q)
(cd ui && pip install pytest && pytest -q)
```

Dockerfiles for `dummy-csms` and `everhome-shim` run the same tests in a build stage.

## Dependabot

Version bumps are grouped weekly. Dependabot still opens pull requests (GitHub
does not allow it to commit to `main` directly). Those PRs are squash-merged
automatically after CI is green — no review needed.

## Secrets

Do not commit `.env`, charger serials, vendor OCPP URLs, or LAN addresses.
Use `.env.example` as the template. Runtime config lives in the `ui-data` volume.
