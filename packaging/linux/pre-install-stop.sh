#!/bin/sh
set -e

# 升级覆盖冻结运行时前，只处理能够由 /proc 可执行文件与进程状态共同
# 证明属于 PartyOps 的进程。PID 已复用或身份不明时宁可中止安装也不误杀。
is_partyops_process() {
  pid="$1"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ -r "/proc/$pid/stat" ] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [ -n "$state" ] && [ "$state" != "Z" ] || return 1
  executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  case "$executable" in
    /opt/partyops/partyops|/opt/partyops/PartyOps/partyops) return 0 ;;
    *) return 1 ;;
  esac
}

record_stopped_pid() {
  pid="$1"
  is_partyops_process "$pid" || return 0
  grep -qx "$pid" /run/partyops/stopped-pids 2>/dev/null ||
    printf '%s\n' "$pid" >>/run/partyops/stopped-pids
}

install -d -m 0755 /run/partyops
: >/run/partyops/stopped-pids
if systemctl is-active --quiet partyops.service 2>/dev/null; then
  : >/run/partyops/restart-after-upgrade
fi

service_pid="$(systemctl show partyops.service -p MainPID --value 2>/dev/null || true)"
record_stopped_pid "$service_pid"
for process in /proc/[0-9]*; do
  record_stopped_pid "${process##*/}"
done

# 受限更新器本身运行在 partyops-updater.service 中。系统内升级若在此处
# 停掉父服务，systemd 默认会连同正在执行的 dpkg/dnf 一起终止，留下半安装
# 状态。人工包管理器升级没有事务标记，仍同时停止两个服务。
if [ "${PARTYOPS_IN_APP_UPDATE:-0}" = "1" ]; then
  systemctl stop partyops.service >/dev/null 2>&1 || true
else
  systemctl stop partyops.service partyops-updater.service >/dev/null 2>&1 || true
fi
if [ -s /run/partyops/stopped-pids ]; then
  while IFS= read -r pid; do
    is_partyops_process "$pid" && kill -TERM "$pid" 2>/dev/null || true
  done </run/partyops/stopped-pids
fi

attempt=0
while [ "$attempt" -lt 20 ]; do
  running=""
  if [ -s /run/partyops/stopped-pids ]; then
    while IFS= read -r pid; do
      is_partyops_process "$pid" && running="$running $pid"
    done </run/partyops/stopped-pids
  fi
  [ -z "$running" ] && break
  attempt=$((attempt + 1))
  sleep 1
done

if [ -n "${running:-}" ]; then
  printf '旧版 PartyOps 未在 20 秒内退出，正在终止已验证进程：%s\n' "$running" >&2
  for pid in $running; do
    is_partyops_process "$pid" && kill -KILL "$pid" 2>/dev/null || true
  done
fi

attempt=0
while [ "$attempt" -lt 5 ]; do
  running=""
  if [ -s /run/partyops/stopped-pids ]; then
    while IFS= read -r pid; do
      is_partyops_process "$pid" && running="$running $pid"
    done </run/partyops/stopped-pids
  fi
  [ -z "$running" ] && break
  attempt=$((attempt + 1))
  sleep 1
done
if [ -n "${running:-}" ]; then
  printf '已验证的旧 PartyOps 进程仍无法停止：%s\n' "$running" >&2
  echo '进程可能处于不可中断 I/O 状态，已停止安装以保护业务数据。' >&2
  exit 1
fi
