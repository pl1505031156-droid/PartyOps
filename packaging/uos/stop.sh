#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/partyops/partyops.env"
if [[ -f "$USER_CONFIG" ]]; then
  set -a
  source "$USER_CONFIG"
  set +a
fi
DATA_DIR="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
PIDFILE="$DATA_DIR/partyops.pid"
if [[ ! -f "$PIDFILE" ]]; then
  echo "未发现运行中的党建智办。"
  exit 0
fi
PID="$(cat "$PIDFILE")"

is_partyops_process() {
  local pid="$1" state executable
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ "$state" != "Z" && -n "$state" ]] || return 1
  executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  case "$executable" in
    "$APP_ROOT/partyops"|"$APP_ROOT/PartyOps/partyops") return 0 ;;
    *) return 1 ;;
  esac
}

if ! is_partyops_process "$PID"; then
  rm -f "$PIDFILE"
  echo "检测到失效的 PartyOps PID 文件，已安全清理。"
  exit 0
fi

kill -TERM "$PID"
for _attempt in {1..20}; do
  is_partyops_process "$PID" || break
  sleep 1
done
if is_partyops_process "$PID"; then
  echo "PartyOps 未在 20 秒内完成优雅退出，正在终止已验证的旧进程。"
  kill -KILL "$PID"
  for _attempt in {1..5}; do
    is_partyops_process "$PID" || break
    sleep 1
  done
fi
if is_partyops_process "$PID"; then
  echo "PartyOps 进程无法停止，请在运行诊断中检查进程状态：$PID" >&2
  exit 1
fi
rm -f "$PIDFILE"
echo "党建智办已停止。"
