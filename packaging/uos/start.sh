#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
USER_CONFIG="$CONFIG_ROOT/partyops.env"
PERSONAL_CONFIG="$CONFIG_ROOT/personal.env"
MODE_CONFIG="$CONFIG_ROOT/mode.json"
CONFIG="${PARTYOPS_ENV_FILE:-}"
if [[ -z "$CONFIG" ]]; then
  MODE=""
  if [[ -r "$MODE_CONFIG" ]]; then
    MODE="$(sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\(personal\|host\)".*/\1/p' "$MODE_CONFIG" | head -n 1)"
  fi
  if [[ "$MODE" == "personal" && -f "$PERSONAL_CONFIG" ]]; then
    CONFIG="$PERSONAL_CONFIG"
  else
    CONFIG="$USER_CONFIG"
  fi
fi

migrate_legacy_host_config() {
  local config="$1" configured_port agent_port temporary changed
  [[ -f "$config" && -w "$config" ]] || return 0
  configured_port="$(
    set +u
    # shellcheck disable=SC1090
    source "$config"
    printf '%s' "${PARTYOPS_PORT:-18765}"
  )"
  [[ "$configured_port" =~ ^[0-9]+$ ]] &&
    ((configured_port >= 1024 && configured_port <= 65534)) ||
    configured_port=18765
  agent_port=$((configured_port + 1))
  temporary="${config}.migration.$$"
  cp -p -- "$config" "$temporary"
  changed=0
  if ! grep -q '^PARTYOPS_AGENT_PORT=' "$temporary"; then
    printf 'PARTYOPS_AGENT_PORT=%s\n' "$agent_port" >>"$temporary"
    changed=1
  fi
  if ! grep -qx 'PARTYOPS_TLS_ENABLED=true' "$temporary"; then
    if grep -q '^PARTYOPS_TLS_ENABLED=' "$temporary"; then
      sed -i 's/^PARTYOPS_TLS_ENABLED=.*/PARTYOPS_TLS_ENABLED=true/' "$temporary"
    else
      printf 'PARTYOPS_TLS_ENABLED=true\n' >>"$temporary"
    fi
    changed=1
  fi
  if [[ "$changed" -eq 1 ]]; then
    mv -f -- "$temporary" "$config"
    echo "旧版主机配置已迁移：启用 HTTPS 和设备安全端口。"
  else
    rm -f -- "$temporary"
  fi
}

if [[ ! -f "$CONFIG" && -f /etc/partyops/partyops.env ]]; then
  CONFIG=/etc/partyops/partyops.env
fi
if [[ ! -f "$CONFIG" ]]; then
  exec "$APP_ROOT/partyops-wizard"
fi
# 旧版迁移只属于主机配置。个人模式刻意使用本机 HTTP；若把 personal.env
# 强制改成 HTTPS，首次重启会在尚未生成主机证书时表现为“双击无反应”。
if [[ "$CONFIG" != "$PERSONAL_CONFIG" ]]; then
  migrate_legacy_host_config "$CONFIG"
fi
if [[ -f "$CONFIG" ]]; then
  set -a
  set +u
  if ! source "$CONFIG"; then
    set -u
    echo "[CONFIG_INVALID] 配置文件无法读取：$CONFIG。请重新打开配置向导修复。" >&2
    exit 2
  fi
  set -u
  set +a
fi
export PARTYOPS_ENVIRONMENT="${PARTYOPS_ENVIRONMENT:-production}"
export PARTYOPS_DATA_DIR="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
export PARTYOPS_STRICT_SQLITE="${PARTYOPS_STRICT_SQLITE:-true}"
export PARTYOPS_SEED_DEMO="${PARTYOPS_SEED_DEMO:-false}"
export PARTYOPS_HOST="${PARTYOPS_HOST:-127.0.0.1}"
export PARTYOPS_PORT="${PARTYOPS_PORT:-18765}"

mkdir -p "$PARTYOPS_DATA_DIR"
PIDFILE="$PARTYOPS_DATA_DIR/partyops.pid"
LAUNCHER_LOG="$PARTYOPS_DATA_DIR/launcher.log"
rotate_launcher_log() {
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
rotate_launcher_log "$LAUNCHER_LOG" || true
: >>"$LAUNCHER_LOG"
chmod 0600 "$LAUNCHER_LOG" 2>/dev/null || true
printf '%s 主程序启动检查：配置=%s，端口=%s\n' \
  "$(date -Iseconds 2>/dev/null || date)" "$CONFIG" "$PARTYOPS_PORT" >>"$LAUNCHER_LOG"
is_partyops_process() {
  local pid="$1" state executable
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ -n "$state" && "$state" != "Z" ]] || return 1
  executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  case "$executable" in
    "$APP_ROOT/partyops"|"$APP_ROOT/PartyOps/partyops") return 0 ;;
    *) return 1 ;;
  esac
}

if [[ -f "$PIDFILE" ]]; then
  RECORDED_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if is_partyops_process "$RECORDED_PID"; then
    printf '%s 复用正在运行的 PartyOps 进程：pid=%s\n' \
      "$(date -Iseconds 2>/dev/null || date)" "$RECORDED_PID" >>"$LAUNCHER_LOG"
    echo "党建智办已在运行。"
    exit 0
  fi
  # PID 已结束、损坏或被其他程序复用时只能删除 PartyOps 自己的记录；
  # 绝不向身份不明的进程发信号，也不能因此永久跳过启动。
  rm -f -- "$PIDFILE"
fi
nohup "$APP_ROOT/partyops" >> "$LAUNCHER_LOG" 2>&1 &
STARTED_PID=$!
echo "$STARTED_PID" > "$PIDFILE"
sleep 0.5
if ! is_partyops_process "$STARTED_PID"; then
  rm -f -- "$PIDFILE"
  echo "[CHILD_EXITED] 党建智办启动后提前退出；启动日志如下：" >&2
  tail -n 80 "$LAUNCHER_LOG" >&2 2>/dev/null || true
  exit 2
fi
echo "党建智办已启动：http://$PARTYOPS_HOST:$PARTYOPS_PORT"
