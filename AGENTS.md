# Agent instructions

This repository is **ocpp-fanout**: an OCPP 1.6-J fan-out in front of a single
wallbox slot. Read [README.md](README.md) and [SECURITY.md](SECURITY.md) before
changing behaviour.

## Product rules

- Independent, unofficial, no vendor support. Do not imply endorsement by go-e,
  Enphase, everHome, Monta, Joulo, Tesla, or others.
- Do not commit site identity: charger serials, LAN IPs, vendor OCPP URLs with
  real tokens, hostnames, or `.env`. Keep placeholders in `.env.example` and
  empty defaults in `ui/default_config.json`.
- Do not publish the OCPP WebSocket or the Setup UI to the public internet.
- Charging current and phases belong to a **local controller** (MQTT / Enphase
  HTTP), not to a secondary CSMS, unless the operator allowlists a command.
- Joulo secondaries must not control the charger by default. CSMS Calls are
  answered **locally** in the shim. Only allowlisted actions go to the wallbox
  via dummy-csms `POST /inject` (primary path).
- Do not fork or vendor joulo-ocpp-proxy unless there is no other option. Wrap
  it (`proxy/entrypoint.sh`) and keep the image from `ghcr.io`.

## Layout

| Path | Role |
|------|------|
| `docker-compose.yml` | Full stack: healthchecks + rotated json-file logs; URLs may come from env |
| `docker-compose.simple.yml` | No healthchecks; Docker default logging; Setup UI is the source of truth for CPID, backend URLs, allowlists |
| `dummy-csms/` | Primary CSMS: CallResults, `/inject` |
| `everhome-shim/` | One image, three services (`everhome-shim`, `enphase-shim`, `monta-shim`) via `SHIM_NAME` / `SHIM_PORT` |
| `proxy/entrypoint.sh` | Builds `SECONDARY_CSMS_URLS`; `INCLUDE_ALL_SHIMS=true` always attaches all shims |
| `ui/` | FastAPI + static Live/Setup UI; OCPP JSONL under the `ui-data` volume |

## Code

- Python 3.12, existing aiohttp/FastAPI style. Match neighbouring files.
- Keep changes scoped. No drive-by refactors.
- English for code, comments, commit messages, and README. German operator
  guide lives in `docs/ANLEITUNG.md` — update both when behaviour changes.
- Tests: `pytest` in `dummy-csms`, `everhome-shim`, and `ui`. Dockerfiles for
  dummy and shim run pytest in a build stage. Do not weaken tests to make a
  change pass.
- Compose YAML must stay explicit (no merge anchors). Portainer users edit it.
- When adding a Setup-configurable field, the **simple** compose file must not
  require a matching environment variable.

## Commands

```bash
(cd dummy-csms && pip install -r requirements-dev.txt && pytest -q)
(cd everhome-shim && pip install -r requirements-dev.txt && pytest -q)
(cd ui && pip install pytest && pytest -q)
docker compose --env-file .env.example config --quiet
docker compose -f docker-compose.simple.yml config --quiet
```
