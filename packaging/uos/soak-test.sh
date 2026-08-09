#!/usr/bin/env bash
set -euo pipefail

URL="${1:-https://127.0.0.1:18765}"
DURATION_SECONDS="${2:-3600}"
INTERVAL_SECONDS="${3:-10}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$ROOT/artifacts/uos-soak-test-$(date +%Y%m%d-%H%M%S).txt"

[[ "$DURATION_SECONDS" =~ ^[0-9]+$ ]] && ((DURATION_SECONDS >= 60)) || {
  echo "持续时间必须是至少 60 秒的整数。" >&2
  exit 2
}
[[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] && ((INTERVAL_SECONDS >= 2 && INTERVAL_SECONDS <= 60)) || {
  echo "检查间隔必须是 2—60 秒的整数。" >&2
  exit 2
}

mkdir -p "$ROOT/artifacts"
CURL_ARGS=(-fsS --connect-timeout 5 --max-time 15)
if [[ "$URL" == https://* && -f /var/lib/partyops/secrets/pki/ca.pem ]]; then
  CURL_ARGS+=(--cacert /var/lib/partyops/secrets/pki/ca.pem)
fi

START_TIME="$(date +%s)"
END_TIME=$((START_TIME + DURATION_SECONDS))
CHECKS=0
FAILURES=0
MAX_LATENCY_MS=0
START_RESTARTS=""
if systemctl is-active --quiet partyops 2>/dev/null; then
  START_RESTARTS="$(systemctl show partyops --property=NRestarts --value)"
fi

{
  echo "PartyOps UOS 稳定性连续检查"
  echo "started_at=$(date -Iseconds)"
  echo "url=$URL"
  echo "duration_seconds=$DURATION_SECONDS"
  echo "interval_seconds=$INTERVAL_SECONDS"
  echo "start_restarts=${START_RESTARTS:-not-systemd}"
  df -h /var/lib/partyops 2>/dev/null || true
} | tee "$RESULT"

while (( $(date +%s) < END_TIME )); do
  CHECKS=$((CHECKS + 1))
  REQUEST_STARTED="$(date +%s%3N)"
  if HEALTH="$(curl "${CURL_ARGS[@]}" "$URL/api/v1/health" 2>&1)" &&
    grep -q '"status":"ok"' <<<"$HEALTH" &&
    grep -q '"safe_version":true' <<<"$HEALTH" &&
    grep -q '"fts5":true' <<<"$HEALTH"; then
    REQUEST_ENDED="$(date +%s%3N)"
    LATENCY_MS=$((REQUEST_ENDED - REQUEST_STARTED))
    if ((LATENCY_MS > MAX_LATENCY_MS)); then
      MAX_LATENCY_MS="$LATENCY_MS"
    fi
    printf '%s check=%s status=ok latency_ms=%s\n' \
      "$(date -Iseconds)" "$CHECKS" "$LATENCY_MS" >>"$RESULT"
  else
    FAILURES=$((FAILURES + 1))
    printf '%s check=%s status=failed detail=%q\n' \
      "$(date -Iseconds)" "$CHECKS" "$HEALTH" >>"$RESULT"
  fi
  sleep "$INTERVAL_SECONDS"
done

END_RESTARTS=""
if systemctl is-active --quiet partyops 2>/dev/null; then
  END_RESTARTS="$(systemctl show partyops --property=NRestarts --value)"
fi

{
  echo "finished_at=$(date -Iseconds)"
  echo "checks=$CHECKS"
  echo "failures=$FAILURES"
  echo "max_latency_ms=$MAX_LATENCY_MS"
  echo "end_restarts=${END_RESTARTS:-not-systemd}"
  df -h /var/lib/partyops 2>/dev/null || true
} | tee -a "$RESULT"

if ((FAILURES > 0)); then
  echo "RESULT=failed：连续检查出现 $FAILURES 次失败。" | tee -a "$RESULT"
  exit 1
fi
if [[ -n "$START_RESTARTS" && "$END_RESTARTS" != "$START_RESTARTS" ]]; then
  echo "RESULT=failed：服务重启次数从 $START_RESTARTS 变为 $END_RESTARTS。" | tee -a "$RESULT"
  exit 1
fi

echo "RESULT=passed" | tee -a "$RESULT"
echo "连续检查记录：$RESULT"
