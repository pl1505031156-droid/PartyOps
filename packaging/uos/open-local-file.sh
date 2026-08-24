#!/usr/bin/env bash
set -euo pipefail

# x-scheme-handler/partyops-file：仅消费主机签发的一次性令牌，并把真实
# 文件路径交给 UOS 的 xdg-open。脚本不执行文件，也不解释服务端内容。
URI="${1:-}"
TOKEN="${URI#partyops-file://open/}"
LOG_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/partyops"
LOG_FILE="$LOG_ROOT/open-local-file.log"
RESPONSE_FILE=""

cleanup_response() {
  [[ -z "$RESPONSE_FILE" ]] || rm -f -- "$RESPONSE_FILE" 2>/dev/null || true
}
trap cleanup_response EXIT

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

read_config_value() {
  local key="$1"
  awk -F= -v wanted="$key" '
    $1 == wanted {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      if ($0 ~ /^\047.*\047$/ || $0 ~ /^".*"$/) {
        print substr($0, 2, length($0) - 2)
      } else {
        print
      }
      exit
    }
  ' "$HOST_CONFIG"
}

# 配置文件只按白名单读取三个纯数据字段，禁止通过 shell source 执行内容。
PORT="$(read_config_value PARTYOPS_PORT)"
TLS_ENABLED="$(read_config_value PARTYOPS_TLS_ENABLED)"
PORT="${PORT:-18765}"
SCHEME="http"
[[ "$TLS_ENABLED" == "true" ]] && SCHEME="https"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || {
  fail_open "党建智办主机地址配置无效。" 2
}
# 文件助手与 PartyOps 主进程位于同一台电脑。协同公布地址可能是局域网
# IP，但一次性令牌端点故意只接受回环来源，因此这里永远使用 127.0.0.1。
LOCAL_HOST="127.0.0.1"

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
  [[ -n "$CA_FILE" ]] || {
    fail_open "党建智办内部 CA 尚未就绪，已拒绝不受信的 HTTPS 文件打开请求。" 3
  }
  CURL_ARGS+=(--cacert "$CA_FILE")
fi

mkdir -p "$LOG_ROOT" 2>/dev/null || true
RESPONSE_FILE="$(mktemp "$LOG_ROOT/open-response.XXXXXX")" || {
  fail_open "无法创建本机安全临时文件，请检查当前用户目录权限。" 3
}
chmod 600 "$RESPONSE_FILE" 2>/dev/null || true
CURL_EXIT=0
HTTP_STATUS="$(
  curl "${CURL_ARGS[@]}" --output "$RESPONSE_FILE" --write-out '%{http_code}' \
    "$SCHEME://$LOCAL_HOST:$PORT/api/v1/workspace/open-tokens/$TOKEN"
)" || CURL_EXIT=$?
if [[ "$CURL_EXIT" -ne 0 ]]; then
  case "$CURL_EXIT" in
    60) fail_open "[CERTIFICATE_FAILED] PartyOps 内部证书校验失败，请从桌面图标重新修复证书。" 3 ;;
    7) fail_open "[HOST_UNREACHABLE] PartyOps 本机服务未监听，请重新双击桌面图标。" 3 ;;
    28) fail_open "[HOST_TIMEOUT] PartyOps 本机服务响应超时，请在运行诊断中检查服务状态。" 3 ;;
    *) fail_open "[HELPER_REQUEST_FAILED] 本机文件助手无法连接 PartyOps，请查看运行诊断。" 3 ;;
  esac
fi
if [[ "$HTTP_STATUS" != "200" ]]; then
  PROBLEM_CODE="$(sed -n 's/.*"code"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$RESPONSE_FILE" | head -n 1)"
  case "$PROBLEM_CODE" in
    OPEN_GRANT_EXPIRED) fail_open "[OPEN_GRANT_EXPIRED] 文件打开授权已过期，请回到原始文件中心重试。" 3 ;;
    OPEN_GRANT_ALREADY_USED) fail_open "[OPEN_GRANT_ALREADY_USED] 该打开授权已经使用，请重新点击打开。" 3 ;;
    OPEN_GRANT_REVOKED) fail_open "[OPEN_GRANT_REVOKED] 文件权限已经变化，请重新选择文件。" 3 ;;
    WORKSPACE_FILE_UNAVAILABLE) fail_open "[FILE_MISSING] 原文件已移动、删除或不再授权。" 3 ;;
    *) fail_open "[OPEN_GRANT_REJECTED] PartyOps 拒绝了本次文件打开请求，请重新发起。" 3 ;;
  esac
fi
FILE_PATH="$(<"$RESPONSE_FILE")"
[[ "$FILE_PATH" == /* && "$FILE_PATH" != *$'\n'* && "$FILE_PATH" != *$'\r'* ]] || {
  fail_open "主机返回的文件路径无效，已拒绝打开。" 3
}
command -v xdg-open >/dev/null 2>&1 || {
  fail_open "系统缺少 xdg-open，无法调用 WPS 或其他默认程序。" 4
}
report_result() {
  local result_code="$1"
  curl "${CURL_ARGS[@]}" --request POST \
    --header 'Content-Type: application/json' \
    --data "{\"result_code\":\"$result_code\",\"detail\":\"\"}" \
    "$SCHEME://$LOCAL_HOST:$PORT/api/v1/workspace/open-tokens/$TOKEN/complete" \
    >/dev/null 2>&1 || true
}

xdg-open "$FILE_PATH" || {
  report_result "DEFAULT_APP_FAILED"
  fail_open "系统默认程序未能打开该文件，请检查该文件类型是否已关联 WPS 或其他应用。" 4
}
report_result "OPENED"
