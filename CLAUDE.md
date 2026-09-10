# Claude instructions

Follow [AGENTS.md](AGENTS.md) for architecture, safety, and how to test.

Short version:

- Unofficial open-source fan-out in front of one OCPP 1.6-J wallbox slot.
  Not affiliated with go-e, Enphase, everHome, Monta, Joulo, or Tesla.
- Do not commit serials, LAN IPs, real vendor URLs, or `.env`.
- Secondaries are read-only unless the Setup allowlist injects a Call through
  dummy-csms. Do not let CSMS control leak by default.
- Two Compose files: `docker-compose.yml` (health + log rotation) and
  `docker-compose.simple.yml` (no healthchecks; Setup UI holds URLs/CPID).
- Keep Compose YAML explicit. Update README and `docs/ANLEITUNG.md` together.
- Run pytest in `dummy-csms`, `everhome-shim`, and `ui` after code changes.
