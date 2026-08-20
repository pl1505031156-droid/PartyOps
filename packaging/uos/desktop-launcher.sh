#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
CLIENT_CONFIG="$CONFIG_ROOT/client.json"
USER_HOST_CONFIG="$CONFIG_ROOT/partyops.env"
PERSONAL_CONFIG="$CONFIG_ROOT/personal.env"
MODE_CONFIG="$CONFIG_ROOT/mode.json"
CLIENT_BROWSER_URL="$CONFIG_ROOT/client-browser.url"
CA_HELPER="$APP_ROOT/install-internal-ca.sh"
CA_MARKER="$CONFIG_ROOT/pki/ca-trusted.sha256"

prepare_private_writable_directory() {
  local candidate="$1" probe=""
  mkdir -p -- "$candidate" 2>/dev/null || return 1
  [[ -d "$candidate" ]] || return 1
  # 仅检查 -w 会被 ACL、只读挂载及 root 特权绕过。必须实际创建文件，
  # 才能证明后续 marker、配置和诊断日志确实可以落盘。
  probe="$(mktemp "$candidate/.partyops-write-test.XXXXXX" 2>/dev/null)" || return 1
  rm -f -- "$probe" 2>/dev/null || return 1
  chmod 0700 "$candidate" 2>/dev/null || return 1
  printf '%s' "$candidate"
}

CONFIG_ROOT_READY=1
if ! LOG_ROOT="$(prepare_private_writable_directory "$CONFIG_ROOT")"; then
  CONFIG_ROOT_READY=0
  STATE_LOG_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/partyops"
  if ! LOG_ROOT="$(prepare_private_writable_directory "$STATE_LOG_ROOT")"; then
    TEMP_LOG_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-$(id -u).XXXXXX" 2>/dev/null || true)"
    if [[ -z "$TEMP_LOG_ROOT" ]] ||
      ! LOG_ROOT="$(prepare_private_writable_directory "$TEMP_LOG_ROOT")"; then
      printf '%s\n' \
        "[DIAGNOSTIC_DIR_UNAVAILABLE] 无法创建 PartyOps 用户诊断目录：$CONFIG_ROOT、$STATE_LOG_ROOT、$TEMP_LOG_ROOT" >&2
      exit 2
    fi
  fi
fi
LAUNCH_LOG="$LOG_ROOT/desktop-launch.log"
DIAGNOSTIC_FILE="$LOG_ROOT/startup-diagnostic.txt"
ACTIVE_DATA_DIR=""
ACTIVE_PIDFILE=""
ACTIVE_RUNTIME_LOG=""
ACTIVE_APP_LOG=""
ACTIVE_SERVICE=""
LAST_HEALTH_ERROR=""
EXPECTED_VERSION=""
if [[ -r "$APP_ROOT/VERSION" ]]; then
  IFS= read -r EXPECTED_VERSION <"$APP_ROOT/VERSION" || true
fi

rotate_desktop_log() {
  local log="$1" index
  [[ -f "$log" ]] || return 0
  [[ "$(wc -c <"$log" 2>/dev/null || printf 0)" -ge 5242880 ]] || return 0
  rm -f -- "$log.5"
  index=4
  while ((index >= 1)); do
    [[ ! -e "$log.$index" ]] || mv -f -- "$log.$index" "$log.$((index + 1))"
    index=$((index - 1))
  done
  mv -f -- "$log" "$log.1"
}
rotate_desktop_log "$LAUNCH_LOG" || true
# 日志必须在任何启动动作之前落盘。过去只有实际写入时才创建文件，导致
# Terminal=false 的桌面启动失败后，用户按弹窗路径只能看到空目录。
if ! : >>"$LAUNCH_LOG" 2>/dev/null; then
  printf '%s\n' "[DIAGNOSTIC_FILE_UNAVAILABLE] 无法创建 PartyOps 桌面启动日志：$LAUNCH_LOG" >&2
  exit 2
fi
chmod 0600 "$LAUNCH_LOG" 2>/dev/null || true
printf '%s 桌面启动开始：uid=%s，程序=%s\n' \
  "$(date -Iseconds 2>/dev/null || date)" "$(id -u)" "$APP_ROOT" >>"$LAUNCH_LOG"

runtime_pid_alive() {
  local pid="$1" state
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ -n "$state" && "$state" != "Z" ]]
}

append_diagnostic_log() {
  local label="$1" path="$2"
  [[ -n "$path" && -f "$path" && ! -L "$path" ]] || return 0
  printf '\n===== %s：%s =====\n' "$label" "$path"
  tail -n 160 "$path" 2>/dev/null || true
}

write_diagnostic_snapshot() {
  local message="$1" temporary="${DIAGNOSTIC_FILE}.tmp.$$" pid="" service_state=""
  if ! {
    printf 'PartyOps Linux 启动诊断\n'
    printf '时间：%s\n' "$(date -Iseconds 2>/dev/null || date)"
    printf '错误：%s\n' "$message"
    printf '用户：%s（uid=%s）\n' "$(id -un)" "$(id -u)"
    printf '模式：%s\n' "${MODE:-unknown}"
    printf '配置：%s\n' "${HOST_CONFIG:-未选择}"
    printf '数据目录：%s\n' "${ACTIVE_DATA_DIR:-未选择}"
    printf '本机端点：%s\n' "${ACTIVE_BASE_URL:-未确定}"
    [[ -z "$LAST_HEALTH_ERROR" ]] || printf '最近健康检查：%s\n' "$LAST_HEALTH_ERROR"
    if [[ -n "$ACTIVE_PIDFILE" ]]; then
      printf 'PID 文件：%s\n' "$ACTIVE_PIDFILE"
      if [[ -r "$ACTIVE_PIDFILE" ]]; then
        IFS= read -r pid <"$ACTIVE_PIDFILE" || true
        printf '记录 PID：%s\n' "${pid:-空}"
        if runtime_pid_alive "$pid"; then
          printf '进程状态：运行中，exe=%s\n' \
            "$(readlink -f "/proc/$pid/exe" 2>/dev/null || printf 未知)"
        else
          printf '进程状态：已退出或为僵尸进程\n'
        fi
      else
        printf '进程状态：PID 文件不存在或不可读\n'
      fi
    fi
    if [[ -n "$ACTIVE_SERVICE" && -x "$(command -v systemctl 2>/dev/null || true)" ]]; then
      service_state="$(systemctl is-active "$ACTIVE_SERVICE" 2>/dev/null || true)"
      printf '系统服务：%s（%s）\n' "$ACTIVE_SERVICE" "${service_state:-unknown}"
      printf '\n===== systemctl status =====\n'
      systemctl --no-pager --full status "$ACTIVE_SERVICE" 2>&1 || true
      if command -v journalctl >/dev/null 2>&1; then
        printf '\n===== journalctl =====\n'
        journalctl -u "$ACTIVE_SERVICE" -n 120 --no-pager 2>&1 || true
      fi
    fi
    if command -v ss >/dev/null 2>&1; then
      printf '\n===== 本机监听端口 =====\n'
      ss -ltn 2>&1 || true
    fi
    append_diagnostic_log "桌面启动日志" "$LAUNCH_LOG"
    append_diagnostic_log "主程序启动日志" "$ACTIVE_RUNTIME_LOG"
    append_diagnostic_log "PartyOps 业务日志" "$ACTIVE_APP_LOG"
  } >"$temporary" 2>&1; then
    rm -f -- "$temporary"
    return 1
  fi
  chmod 0600 "$temporary" 2>/dev/null || true
  mv -f -- "$temporary" "$DIAGNOSTIC_FILE"
}

show_launch_failure() {
  local message="$1"
  printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$message" \
    >>"$LAUNCH_LOG" 2>/dev/null || true
  write_diagnostic_snapshot "$message" || true
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="党建智办启动失败" \
      --text="$message\n\n完整诊断：$DIAGNOSTIC_FILE\n桌面日志：$LAUNCH_LOG" >/dev/null 2>&1 || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title "党建智办启动失败" \
      --error "$message\n\n完整诊断：$DIAGNOSTIC_FILE\n桌面日志：$LAUNCH_LOG" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -u critical -t 15000 "党建智办启动失败" \
      "$message；完整诊断：$DIAGNOSTIC_FILE" >/dev/null 2>&1 || true
  fi
}

if [[ ! "$EXPECTED_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]]; then
  show_launch_failure \
    "[PACKAGE_VERSION_MISSING] PartyOps 安装载荷缺少有效版本标识：$APP_ROOT/VERSION"
  exit 2
fi

if [[ "$CONFIG_ROOT_READY" -ne 1 ]]; then
  show_launch_failure \
    "[CONFIG_DIR_UNAVAILABLE] 无法访问用户配置目录 $CONFIG_ROOT，请检查主目录权限或磁盘状态。"
  exit 2
fi

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
  local scheme="$1" port="$2" base_url url runtime_fingerprint
  local timeout_seconds deadline health_body="" health_compact="" health_error=""
  local pid="" service_state=""
  [[ "$scheme" == "http" || "$scheme" == "https" ]] || return 1
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1024 && port <= 65534)) || {
    show_launch_failure "配置中的服务端口无效，请重新配置。"
    return 2
  }
  base_url="$scheme://127.0.0.1:$port"
  ACTIVE_BASE_URL="$base_url"
  timeout_seconds="${PARTYOPS_DESKTOP_HEALTH_TIMEOUT_SECONDS:-180}"
  [[ "$timeout_seconds" =~ ^[0-9]+$ ]] &&
    ((timeout_seconds >= 1 && timeout_seconds <= 900)) || timeout_seconds=180
  deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if health_body="$(curl -kfsS --connect-timeout 1 --max-time 2 \
      "$base_url/api/v1/health" 2>&1)"; then
      health_compact="${health_body//[$' \t\r\n']/}"
      if [[ "$health_compact" != *'"status":"ok"'* ]]; then
        LAST_HEALTH_ERROR="[HEALTH_RESPONSE_INVALID] 健康端点没有返回 status=ok。"
      elif [[ "$health_compact" != *'"app_version":"'"$EXPECTED_VERSION"'"'* ]]; then
        LAST_HEALTH_ERROR="[RUNTIME_VERSION_MISMATCH] 端口 $port 不是当前 PartyOps $EXPECTED_VERSION 运行时。"
        show_launch_failure "$LAST_HEALTH_ERROR 系统没有打开旧版本页面，请重新启动或升级服务。"
        return 2
      else
        # 每次桌面启动使用新的首页指纹，确保原生包覆盖升级后浏览器不会
        # 复用上个版本的 HTML 入口；带哈希的 JS/CSS 仍可正常使用缓存。
        runtime_fingerprint="$(date +%s 2>/dev/null || printf '%s' "$$")"
        url="$base_url/?partyops_runtime=$runtime_fingerprint"
        if open_browser_url "$url"; then
          printf '%s 业务页面已打开：%s\n' \
            "$(date -Iseconds 2>/dev/null || date)" "$url" >>"$LAUNCH_LOG"
          return 0
        fi
        show_launch_failure "党建智办已经就绪，但系统默认浏览器未能打开。请复制地址 $url 到浏览器。"
        return 3
      fi
    fi
    health_error="$health_body"
    LAST_HEALTH_ERROR="${health_error//$'\n'/ }"
    if [[ -n "$ACTIVE_SERVICE" ]]; then
      service_state="$(systemctl is-active "$ACTIVE_SERVICE" 2>/dev/null || true)"
      case "$service_state" in
        active|activating) ;;
        *)
          show_launch_failure "[SERVICE_STOPPED] PartyOps 主机服务当前为 ${service_state:-unknown}，已停止等待。"
          return 2
          ;;
      esac
    elif [[ -n "$ACTIVE_PIDFILE" ]]; then
      if [[ ! -r "$ACTIVE_PIDFILE" ]]; then
        show_launch_failure "[PID_FILE_MISSING] PartyOps 启动后没有生成受控 PID 文件，已停止等待。"
        return 2
      fi
      IFS= read -r pid <"$ACTIVE_PIDFILE" || true
      if ! runtime_pid_alive "$pid"; then
        show_launch_failure "[CHILD_EXITED] PartyOps 主程序已经退出，已收集主程序与业务日志。"
        return 2
      fi
    fi
    sleep 0.5
  done
  show_launch_failure "[HEALTH_TIMEOUT] 党建智办在 ${timeout_seconds} 秒内未能就绪，已生成包含进程、端口和主程序日志的完整诊断。"
  return 2
}

launch_browser_tool() {
  local marker_name="wizard.url" marker url pid attempt wizard_executable first_argument="${1:-}"
  local lock_file
  if [[ "$first_argument" == "--manage-shared-roots" ]]; then
    marker_name="shared-root-manager.url"
  fi
  marker="$CONFIG_ROOT/$marker_name"
  lock_file="$CONFIG_ROOT/.${marker_name}.lock"

  command -v flock >/dev/null 2>&1 || {
    show_launch_failure "[LAUNCH_LOCK_UNAVAILABLE] 系统缺少 util-linux/flock，无法安全启动配置工具。"
    return 2
  }
  exec 9>"$lock_file"
  if ! flock -n 9; then
    exec 9>&-
    # 另一次双击已经负责启动。当前进程只观察对方发布的受控回环地址，
    # 不再排队持有 190 秒锁，也不会叠加第二个向导进程。
    attempt=0
    while ((attempt < 90)); do
      if url="$(read_local_tool_url "$marker" 2>/dev/null)" &&
        curl -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
        if open_local_tool_url "$url"; then
          return 0
        fi
        show_launch_failure "[BROWSER_OPEN_FAILED] 配置工具正在运行，但系统默认浏览器未能打开。请复制地址 $url 到浏览器。"
        return 3
      fi
      attempt=$((attempt + 1))
      sleep 0.5
    done
    show_launch_failure "[LAUNCH_IN_PROGRESS] 另一个配置工具仍在启动。系统没有重复创建进程；请查看 $LAUNCH_LOG。"
    return 2
  fi

  # 重复双击优先复用仍在运行的本地工具，不再叠加多个冻结进程。
  if url="$(read_local_tool_url "$marker" 2>/dev/null)" &&
    curl -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
    flock -u 9
    exec 9>&-
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
      >>"$LAUNCH_LOG" 2>&1 </dev/null 9>&- &
  else
    nohup "$APP_ROOT/partyops-wizard" --no-browser \
      >>"$LAUNCH_LOG" 2>&1 </dev/null 9>&- &
  fi
  pid=$!

  attempt=0
  while ((attempt < 360)); do
    if url="$(read_local_tool_url "$marker" 2>/dev/null)" &&
      curl -fsS --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
      flock -u 9
      exec 9>&-
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
      flock -u 9
      exec 9>&-
      show_launch_failure "配置工具启动失败。请打开日志查看中文诊断后重试。"
      return 2
    fi
    attempt=$((attempt + 1))
    sleep 0.5
  done
  # PID 来自本函数刚启动的固定随包入口。超时后只终止这个已核验子进程，
  # 防止僵死向导永久占住后续启动事务；日志在终止前已经完整落盘。
  wizard_executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  if [[ "$wizard_executable" == "$APP_ROOT/partyops-wizard" ]]; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  flock -u 9
  exec 9>&-
  show_launch_failure "[WIZARD_PAGE_TIMEOUT] 配置工具在 180 秒内未能显示页面，已结束本次受控进程并保留日志用于诊断。"
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
  ACTIVE_RUNTIME_LOG="$AGENT_LOG"
  rotate_desktop_log "$AGENT_LOG" || true
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
  set +u
  if ! source "$HOST_CONFIG"; then
    set -u
    set +a
    show_launch_failure "配置文件无法读取（诊断码 CONFIG_INVALID），请重新打开配置向导修复。"
    exit 2
  fi
  set -u
  set +a
  PORT="${PARTYOPS_PORT:-18765}"
  SCHEME="http"
  [[ "${PARTYOPS_TLS_ENABLED:-false}" == "true" ]] && SCHEME="https"
  ACTIVE_DATA_DIR="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
  ACTIVE_RUNTIME_LOG="$ACTIVE_DATA_DIR/launcher.log"
  ACTIVE_APP_LOG="$ACTIVE_DATA_DIR/logs/partyops.log"
  ACTIVE_PIDFILE="$ACTIVE_DATA_DIR/partyops.pid"
  ACTIVE_SERVICE=""
  if [[ "$SCHEME" == "https" ]]; then
    if [[ -f "$CONFIG_ROOT/pki/ca.pem" ]]; then
      ensure_ca_trust "$CONFIG_ROOT/pki/ca.pem"
    elif [[ "$HOST_CONFIG" == "/etc/partyops/partyops.env" ]]; then
      ensure_ca_trust "${PARTYOPS_DATA_DIR:-/var/lib/partyops}/secrets/pki/ca.pem"
    fi
  fi
  if [[ "$HOST_CONFIG" != "/etc/partyops/partyops.env" ]]; then
    if ! PARTYOPS_ENV_FILE="$HOST_CONFIG" "$APP_ROOT/start.sh" \
      >>"$LAUNCH_LOG" 2>&1; then
      show_launch_failure "[START_COMMAND_FAILED] 党建智办启动命令执行失败，已收集桌面与主程序诊断。"
      exit 2
    fi
  else
    ACTIVE_PIDFILE=""
    ACTIVE_SERVICE="partyops.service"
  fi
  wait_and_open_local_host "$SCHEME" "$PORT"
  exit $?
fi

launch_browser_tool
