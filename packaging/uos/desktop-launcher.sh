#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
CLIENT_CONFIG="$CONFIG_ROOT/client.json"
USER_HOST_CONFIG="$CONFIG_ROOT/partyops.env"
PERSONAL_CONFIG="$CONFIG_ROOT/personal.env"
MODE_CONFIG="$CONFIG_ROOT/mode.json"
CLIENT_BROWSER_URL="$CONFIG_ROOT/client-browser.url"
CA_HELPER="$APP_ROOT/install-internal-ca.sh"
CA_MARKER="$CONFIG_ROOT/pki/ca-trusted.sha256"

mkdir -p "$CONFIG_ROOT"
LAUNCH_LOG="$CONFIG_ROOT/desktop-launch.log"

show_launch_failure() {
  local message="$1"
  printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$message" >>"$LAUNCH_LOG"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="党建智办启动失败" \
      --text="$message\n\n诊断日志：$LAUNCH_LOG" >/dev/null 2>&1 || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title "党建智办启动失败" \
      --error "$message\n\n诊断日志：$LAUNCH_LOG" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -u critical -t 15000 "党建智办启动失败" \
      "$message；诊断日志：$LAUNCH_LOG" >/dev/null 2>&1 || true
  fi
}

read_local_tool_url() {
  local marker="$1" url=""
  [[ -r "$marker" ]] || return 1
  IFS= read -r url <"$marker" || true
  [[ "$url" =~ ^http://127\.0\.0\.1:[0-9]{1,5}/?$ ]] || return 1
  printf '%s' "$url"
}

read_browser_url() {
  local marker="$1" url="" line_count=0
  [[ -r "$marker" && ! -L "$marker" ]] || return 1
  line_count="$(wc -l <"$marker" 2>/dev/null | tr -d '[:space:]')"
  [[ "$line_count" == "1" ]] || return 1
  IFS= read -r url <"$marker" || true
  [[ "$url" =~ ^https?://[^[:space:]]+$ ]] || return 1
  printf '%s' "$url"
}

open_browser_url() {
  local url="$1"
  [[ "$url" =~ ^https?://[^[:space:]]+$ ]] || return 1
  if command -v xdg-open >/dev/null 2>&1 &&
    timeout 15s xdg-open "$url" >>"$LAUNCH_LOG" 2>&1; then
    return 0
  fi
  if command -v gio >/dev/null 2>&1 &&
    timeout 15s gio open "$url" >>"$LAUNCH_LOG" 2>&1; then
    return 0
  fi
  return 1
}

open_local_tool_url() {
  local url="$1"
  [[ "$url" =~ ^http://127\.0\.0\.1:[0-9]{1,5}/?$ ]] || return 1
  open_browser_url "$url"
}

wait_and_open_local_host() {
  local scheme="$1" port="$2" url attempt=0
  [[ "$scheme" == "http" || "$scheme" == "https" ]] || return 1
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1024 && port <= 65534)) || {
    show_launch_failure "配置中的服务端口无效，请重新配置。"
    return 2
  }
  url="$scheme://127.0.0.1:$port"
  while ((attempt < 240)); do
    if curl -kfsS --connect-timeout 1 --max-time 2 \
      "$url/api/v1/health" >/dev/null 2>&1; then
      if open_browser_url "$url"; then
        printf '%s 业务页面已打开：%s\n' \
          "$(date -Iseconds 2>/dev/null || date)" "$url" >>"$LAUNCH_LOG"
        return 0
      fi
      show_launch_failure "党建智办已经就绪，但系统默认浏览器未能打开。请复制地址 $url 到浏览器。"
      return 3
    fi
    attempt=$((attempt + 1))
    sleep 0.5
  done
  show_launch_failure "党建智办在 120 秒内未能就绪，请查看启动日志后重试。"
  return 2
}

launch_browser_tool() {
  local marker_name="wizard.url" marker url pid attempt first_argument="${1:-}"
  if [[ "$first_argument" == "--manage-shared-roots" ]]; then
    marker_name="shared-root-manager.url"
  fi
  marker="$CONFIG_ROOT/$marker_name"

  # 重复双击优先复用仍在运行的本地工具，不再叠加多个冻结进程。
  if url="$(read_local_tool_url "$marker" 2>/dev/null)" &&
    curl -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
    if open_local_tool_url "$url"; then
      return 0
    fi
    show_launch_failure "配置工具正在运行，但系统默认浏览器未能打开。请复制地址 $url 到浏览器。"
    return 3
  fi
  rm -f -- "$marker"

  printf '%s 正在启动配置工具：%q\n' \
    "$(date -Iseconds 2>/dev/null || date)" "${*:-首次配置}" >>"$LAUNCH_LOG"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send -t 6000 "党建智办正在启动" \
      "首次打开可能需要十几秒，请稍候。" >/dev/null 2>&1 || true
  fi
  if (($# > 0)); then
    nohup "$APP_ROOT/partyops-wizard" --no-browser "$@" \
      >>"$LAUNCH_LOG" 2>&1 </dev/null &
  else
    nohup "$APP_ROOT/partyops-wizard" --no-browser \
      >>"$LAUNCH_LOG" 2>&1 </dev/null &
  fi
  pid=$!

  attempt=0
  while ((attempt < 120)); do
    if url="$(read_local_tool_url "$marker" 2>/dev/null)" &&
      curl -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
      if open_local_tool_url "$url"; then
        printf '%s 配置页面已打开：%s\n' \
          "$(date -Iseconds 2>/dev/null || date)" "$url" >>"$LAUNCH_LOG"
        return 0
      fi
      show_launch_failure "配置工具已经就绪，但系统默认浏览器未能打开。请复制地址 $url 到浏览器。"
      return 3
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      show_launch_failure "配置工具启动失败。请打开日志查看中文诊断后重试。"
      return 2
    fi
    attempt=$((attempt + 1))
    sleep 0.5
  done
  show_launch_failure "配置工具在 60 秒内未能显示页面，进程已保留用于诊断。"
  return 2
}

if [[ "${1:-}" == "--configure" ]]; then
  shift
  launch_browser_tool "$@"
  exit $?
fi
if [[ "${1:-}" == "--manage-shared-roots" ]]; then
  launch_browser_tool "$@"
  exit $?
fi
# 每次启动都快速复核 URI 处理器。旧版可能已经写入快捷方式标记，但当时
# xdg-mime 注册失败被忽略，导致“使用默认程序打开”点击后没有反应。
DESKTOP_INTEGRATION_LOG="$CONFIG_ROOT/desktop-integration.log"
if "$APP_ROOT/install-desktop-shortcut.sh" "$(id -un)" \
  >"$DESKTOP_INTEGRATION_LOG" 2>&1; then
  touch "$CONFIG_ROOT/.desktop-shortcut-created"
else
  echo "党建智办桌面集成自检未通过，文件默认打开功能暂不可用。" >&2
  echo "诊断日志：$DESKTOP_INTEGRATION_LOG" >&2
fi

ensure_ca_trust() {
  local ca_path="$1"
  local current_fingerprint=""
  local installed_fingerprint=""

  [[ -x "$CA_HELPER" && -n "$ca_path" ]] || return 0
  if [[ -r "$ca_path" ]] && command -v openssl >/dev/null 2>&1; then
    current_fingerprint="$(
      openssl x509 -in "$ca_path" -noout -fingerprint -sha256 2>/dev/null |
        cut -d= -f2 |
        tr -d '[:space:]'
    )"
  fi
  if [[ -r "$CA_MARKER" ]]; then
    installed_fingerprint="$(tr -d '[:space:]' <"$CA_MARKER")"
    if [[ -z "$current_fingerprint" ||
      "$installed_fingerprint" == "$current_fingerprint" ]]; then
      return 0
    fi
  fi

  # 旧版已经完成主机或协同配置时不会再次进入首次向导。升级后的首次
  # 桌面启动在此请求一次管理员授权，成功后由 helper 写入 CA 指纹标记，
  # 以后启动不再重复提示。
  pkexec "$CA_HELPER" --desktop-user "$(id -un)" "$ca_path" || true
}

MODE=""
if [[ -r "$MODE_CONFIG" ]]; then
  MODE="$(sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\(personal\|host\|client\)".*/\1/p' "$MODE_CONFIG" | head -n 1)"
fi
# 旧版升级只在角色可以由单一用户配置明确判断时自动迁移；同时残留主机与
# 协同配置时必须进入恢复向导，禁止继续用文件存在顺序猜测运行角色。
if [[ -z "$MODE" ]]; then
  if [[ -f "$CLIENT_CONFIG" && ! -f "$USER_HOST_CONFIG" && ! -f "$PERSONAL_CONFIG" ]]; then
    MODE="client"
  elif [[ ! -f "$CLIENT_CONFIG" && -f "$PERSONAL_CONFIG" && ! -f "$USER_HOST_CONFIG" ]]; then
    MODE="personal"
  elif [[ ! -f "$CLIENT_CONFIG" && ! -f "$PERSONAL_CONFIG" && -f "$USER_HOST_CONFIG" ]]; then
    MODE="host"
  else
    launch_browser_tool
    exit $?
  fi
  printf '{\n  "format_version": 1,\n  "mode": "%s"\n}\n' "$MODE" >"$MODE_CONFIG"
  chmod 600 "$MODE_CONFIG"
fi

if [[ "$MODE" == "client" ]]; then
  if [[ ! -f "$CLIENT_CONFIG" ]]; then
    launch_browser_tool
    exit $?
  fi
  if [[ -f "$CONFIG_ROOT/pki/ca.pem" ]]; then
    ensure_ca_trust "$CONFIG_ROOT/pki/ca.pem"
  fi
  AGENT_PID_FILE="$CONFIG_ROOT/client-agent.pid"
  AGENT_LOG="$CONFIG_ROOT/client-agent.log"
  AGENT_RUNNING=0
  if command -v pgrep >/dev/null 2>&1 &&
    pgrep -u "$(id -u)" -f \
      'partyops-client.*--config.*partyops/client\.json.*--no-open-browser' \
      >/dev/null 2>&1; then
    AGENT_RUNNING=1
  fi
  if [[ -s "$AGENT_PID_FILE" ]]; then
    AGENT_PID="$(cat "$AGENT_PID_FILE" 2>/dev/null || true)"
    case "$AGENT_PID" in
      ''|*[!0-9]*) AGENT_PID="" ;;
    esac
    if [[ -n "$AGENT_PID" ]] && kill -0 "$AGENT_PID" 2>/dev/null; then
      if [[ -r "/proc/$AGENT_PID/cmdline" ]] &&
        tr '\0' ' ' <"/proc/$AGENT_PID/cmdline" |
          grep -Fq -- "$APP_ROOT/partyops-client"; then
        AGENT_RUNNING=1
      fi
    fi
  fi
  if [[ "$AGENT_RUNNING" -ne 1 ]]; then
    nohup "$APP_ROOT/partyops-client" --config "$CLIENT_CONFIG" --no-open-browser \
      >>"$AGENT_LOG" 2>&1 &
    printf '%s\n' "$!" >"$AGENT_PID_FILE"
  fi
  # 单次调用由受保护文件把页面地址交回桌面启动器。这样系统浏览器关联
  # 失败时能给出中文诊断，不会让 Terminal=false 的进程静默消失。
  rm -f -- "$CLIENT_BROWSER_URL"
  CLIENT_STATUS=0
  "$APP_ROOT/partyops-client" --config "$CLIENT_CONFIG" --once \
    --no-open-browser --browser-url-file "$CLIENT_BROWSER_URL" \
    >>"$AGENT_LOG" 2>&1 || CLIENT_STATUS=$?
  if CLIENT_URL="$(read_browser_url "$CLIENT_BROWSER_URL" 2>/dev/null)"; then
    if open_browser_url "$CLIENT_URL"; then
      rm -f -- "$CLIENT_BROWSER_URL"
      exit 0
    fi
    show_launch_failure "协同页面已经准备好，但系统默认浏览器未能打开。请复制地址 $CLIENT_URL 到浏览器。"
    exit 3
  fi
  show_launch_failure "协同终端未能准备页面（退出码 $CLIENT_STATUS），请打开协同日志查看诊断。"
  exit 2
fi

HOST_CONFIG=""
if [[ "$MODE" == "personal" && -f "$PERSONAL_CONFIG" ]]; then
  HOST_CONFIG="$PERSONAL_CONFIG"
elif [[ "$MODE" == "host" && -f "$USER_HOST_CONFIG" ]]; then
  HOST_CONFIG="$USER_HOST_CONFIG"
elif [[ "$MODE" == "host" && -f /etc/partyops/partyops.env ]]; then
  HOST_CONFIG=/etc/partyops/partyops.env
fi
if [[ ( "$MODE" == "host" || "$MODE" == "personal" ) && -f "$HOST_CONFIG" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$HOST_CONFIG"
  set +a
  PORT="${PARTYOPS_PORT:-18765}"
  SCHEME="http"
  [[ "${PARTYOPS_TLS_ENABLED:-false}" == "true" ]] && SCHEME="https"
  if [[ "$SCHEME" == "https" ]]; then
    if [[ -f "$CONFIG_ROOT/pki/ca.pem" ]]; then
      ensure_ca_trust "$CONFIG_ROOT/pki/ca.pem"
    elif [[ "$HOST_CONFIG" == "/etc/partyops/partyops.env" ]]; then
      ensure_ca_trust "${PARTYOPS_DATA_DIR:-/var/lib/partyops}/secrets/pki/ca.pem"
    fi
  fi
  if [[ "$HOST_CONFIG" != "/etc/partyops/partyops.env" ]]; then
    PARTYOPS_ENV_FILE="$HOST_CONFIG" "$APP_ROOT/start.sh"
  fi
  wait_and_open_local_host "$SCHEME" "$PORT"
  exit $?
fi

launch_browser_tool
