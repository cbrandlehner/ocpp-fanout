---
name: Bug report
about: Create a report to help us improve
title: ''
labels: bug
assignees: ''

---

#### Checklist

- [ ] I read the [README](https://github.com/cbrandlehner/ocpp-fanout/blob/main/README.md) and [SECURITY](https://github.com/cbrandlehner/ocpp-fanout/blob/main/SECURITY.md) notes
- [ ] I searched existing issues and found no duplicate
- [ ] I am running the latest `main` (or a current release) and the issue still occurs
- [ ] The wallbox can open a WebSocket to this stack (`ws://<host>:9100/<chargePointId>`)
- [ ] I collected relevant logs (`docker compose logs`) and OCPP JSONL if it exists
- [ ] I redacted charger serials, vendor OCPP URLs, tokens, and LAN addresses from logs
- [ ] I am sure this is about ocpp-fanout (not the wallbox firmware, vendor CSMS, or the local LAN)

**Describe the bug**

A clear and concise description of what the bug is.

**Environment**

- Compose file: `docker-compose.yml` / `docker-compose.simple.yml` / other
- Host OS / Docker version:
- Wallbox brand and firmware (no serial):
- Secondaries in use (Enphase / EverHome / Monta / other):

**Expected behaviour**

**Actual behaviour**

**Logs**

Paste redacted logs here.

**Screenshots**

If applicable, drag and drop a screenshot into this editor.

**Additional context**
