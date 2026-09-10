# Security

The OCPP WebSocket (`PROXY_PORT`, default 9100) is a **full charge-point
protocol**. Bind it to a trusted LAN or VPN. Do not publish it on the internet.

The web UI (`UI_PORT`, default 8088) can change backend URLs and the command
allowlist. Keep it off the public internet, or put authentication in front of it.

`POST /inject` on dummy-csms can send OCPP Calls to the wallbox. It is exposed
only on the Compose network, not published to the host.

Report vulnerabilities privately rather than in a public issue.
