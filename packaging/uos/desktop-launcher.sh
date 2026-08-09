#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
CLIENT_CONFIG="$CONFIG_ROOT/client.json"
USER_HOST_CONFIG="$CONFIG_ROOT/partyops.env"
MODE_CONFIG="$CONFIG_ROOT/mode.json"
CA_HELPER="$APP_ROOT/install-internal-ca.sh"
CA_MARKER="$CONFIG_ROOT/pki/ca-trusted.sha256"

mkdir -p "$CONFIG_ROOT"
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
  MODE="$(sed -n 's/.*"mode"[[:space:]]*:[[:space:]]*"\(host\|client\)".*/\1/p' "$MODE_CONFIG" | head -n 1)"
fi
# 旧版升级只在角色可以由单一用户配置明确判断时自动迁移；同时残留主机与
# 协同配置时必须进入恢复向导，禁止继续用文件存在顺序猜测运行角色。
if [[ -z "$MODE" ]]; then
  if [[ -f "$CLIENT_CONFIG" && ! -f "$USER_HOST_CONFIG" ]]; then
    MODE="client"
  elif [[ ! -f "$CLIENT_CONFIG" && -f "$USER_HOST_CONFIG" ]]; then
    MODE="host"
  else
    exec "$APP_ROOT/partyops-wizard"
  fi
  printf '{\n  "format_version": 1,\n  "mode": "%s"\n}\n' "$MODE" >"$MODE_CONFIG"
  chmod 600 "$MODE_CONFIG"
fi

if [[ "$MODE" == "client" ]]; then
  [[ -f "$CLIENT_CONFIG" ]] || exec "$APP_ROOT/partyops-wizard"
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
  # 单次前台调用只负责立即心跳并打开业务页面；常驻进程持续上报状态、
  # 接收更新和同步获批目录。即使桌面环境没有执行 autostart，也能自愈。
  exec "$APP_ROOT/partyops-client" --config "$CLIENT_CONFIG" --once
fi

HOST_CONFIG="$USER_HOST_CONFIG"
if [[ ! -f "$HOST_CONFIG" && -f /etc/partyops/partyops.env ]]; then
  HOST_CONFIG=/etc/partyops/partyops.env
fi
if [[ "$MODE" == "host" && -f "$HOST_CONFIG" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$HOST_CONFIG"
  set +a
  HOST="${PARTYOPS_HOST:-127.0.0.1}"
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
  if [[ "$HOST_CONFIG" == "$USER_HOST_CONFIG" ]]; then
    "$APP_ROOT/start.sh"
  fi
  exec xdg-open "$SCHEME://$HOST:$PORT"
fi

exec "$APP_ROOT/partyops-wizard"
