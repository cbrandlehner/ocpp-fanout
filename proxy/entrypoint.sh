#!/bin/sh
# Build SECONDARY_CSMS_URLS with per-upstream Charge-Point-ID append.
# joulo-ocpp-proxy only has one global SECONDARY_CSMS_APPEND_CHARGE_POINT_ID,
# so we pre-compose the URLs and disable joulo's own append for secondaries.
set -eu

append_url() {
  url="${1:-}"
  append="${2:-false}"
  [ -z "$url" ] && return 0
  url="${url%/}"
  if [ "$append" = "true" ] && [ -n "${CHARGE_POINT_ID:-}" ]; then
    case "$url" in
      */"$CHARGE_POINT_ID") ;;
      *) url="$url/$CHARGE_POINT_ID" ;;
    esac
  fi
  if [ -z "${SECONDARIES:-}" ]; then
    SECONDARIES="$url"
  else
    SECONDARIES="$SECONDARIES,$url"
  fi
}

if [ -z "${ENPHASE_OCPP_URL:-}" ] && [ -n "${ENPHASE_ROUTER_IP:-}" ]; then
  ENPHASE_OCPP_URL="ws://${ENPHASE_ROUTER_IP}:8083"
fi

SECONDARIES=""
if [ "${INCLUDE_ALL_SHIMS:-false}" = "true" ]; then
  append_url "ws://enphase-shim:9004" false
  append_url "ws://everhome-shim:9003" false
  append_url "ws://monta-shim:9005" false
else
  if [ -n "${ENPHASE_OCPP_URL:-}" ]; then
    append_url "ws://enphase-shim:9004" false
  fi
  if [ -n "${EVERHOME_OCPP_URL:-}" ]; then
    append_url "ws://everhome-shim:9003" false
  fi
  if [ -n "${MONTA_OCPP_URL:-}" ]; then
    append_url "ws://monta-shim:9005" false
  fi
fi

export SECONDARY_CSMS_URLS="${SECONDARIES}"
export SECONDARY_CSMS_APPEND_CHARGE_POINT_ID=false
export PRIMARY_CSMS_URL="${PRIMARY_CSMS_URL:-ws://dummy-csms:9001}"
export PRIMARY_CSMS_APPEND_CHARGE_POINT_ID="${PRIMARY_CSMS_APPEND_CHARGE_POINT_ID:-true}"
export PORT="${PORT:-9000}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo "{\"tag\":\"proxy-entrypoint\",\"msg\":\"starting joulo-ocpp-proxy\",\"primary\":\"${PRIMARY_CSMS_URL}\",\"secondaries\":\"${SECONDARY_CSMS_URLS}\",\"appendPrimary\":\"${PRIMARY_CSMS_APPEND_CHARGE_POINT_ID}\",\"port\":\"${PORT}\"}"

exec node dist/index.js
