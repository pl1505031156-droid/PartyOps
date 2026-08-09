#!/usr/bin/env bash
set -euo pipefail

# x-scheme-handler/partyops-file：仅消费主机签发的一次性令牌，并把真实
# 文件路径交给 UOS 的 xdg-open。脚本不执行文件，也不解释服务端内容。
URI="${1:-}"
TOKEN="${URI#partyops-file://open/}"
LOG_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/partyops"
LOG_FILE="$LOG_ROOT/open-local-file.log"

fail_open() {
  local message="$1"
  local status="${2:-3}"
  mkdir -p "$LOG_ROOT" 2>/dev/null || true
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" \
    >>"$LOG_FILE" 2>/dev/null || true
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "党建智办文件打开失败" "$message" >/dev/null 2>&1 || true
  fi
  echo "$message" >&2
  exit "$status"
}

[[ "$TOKEN" != "$URI" && "$TOKEN" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || {
  fail_open "党建智办打开链接无效，请回到原始文件中心重试。" 2
}

CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
HOST_CONFIG="$CONFIG_ROOT/partyops.env"
if [[ ! -f "$HOST_CONFIG" && -f /etc/partyops/desktop.env ]]; then
  HOST_CONFIG=/etc/partyops/desktop.env
fi
if [[ ! -f "$HOST_CONFIG" ]]; then
  fail_open "未找到党建智办主机配置，请先从桌面图标进入系统。" 2
fi

set -a
# shellcheck disable=SC1090
source "$HOST_CONFIG"
set +a
HOST="${PARTYOPS_HOST:-127.0.0.1}"
PORT="${PARTYOPS_PORT:-18765}"
SCHEME="http"
[[ "${PARTYOPS_TLS_ENABLED:-false}" == "true" ]] && SCHEME="https"
[[ "$HOST" =~ ^[A-Za-z0-9.:_-]+$ && "$PORT" =~ ^[0-9]{1,5}$ ]] || {
  fail_open "党建智办主机地址配置无效。" 2
}

CURL_ARGS=(--fail --silent --show-error --max-time 10)
if [[ "$SCHEME" == "https" ]]; then
  CA_FILE=""
  for candidate in \
    "$CONFIG_ROOT/pki/ca.pem" \
    /usr/local/share/ca-certificates/partyops-internal-ca.crt; do
    if [[ -r "$candidate" && -f "$candidate" && ! -L "$candidate" ]]; then
      CA_FILE="$candidate"
      break
    fi
  done
  if [[ -n "$CA_FILE" ]]; then
    CURL_ARGS+=(--cacert "$CA_FILE")
  fi
fi

FILE_PATH="$(
  curl "${CURL_ARGS[@]}" \
    "$SCHEME://$HOST:$PORT/api/v1/workspace/open-tokens/$TOKEN"
)" || {
  fail_open "文件打开授权已过期、证书未就绪或主机不可用，请回到原始文件中心重试。" 3
}
[[ "$FILE_PATH" == /* && "$FILE_PATH" != *$'\n'* && "$FILE_PATH" != *$'\r'* ]] || {
  fail_open "主机返回的文件路径无效，已拒绝打开。" 3
}
command -v xdg-open >/dev/null 2>&1 || {
  fail_open "系统缺少 xdg-open，无法调用 WPS 或其他默认程序。" 4
}
xdg-open "$FILE_PATH" || {
  fail_open "系统默认程序未能打开该文件，请检查该文件类型是否已关联 WPS 或其他应用。" 4
}
