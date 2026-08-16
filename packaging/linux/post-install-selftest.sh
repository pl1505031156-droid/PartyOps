#!/usr/bin/env sh
set -eu

RUNTIME=/opt/partyops
EXPECTED_ARCH="${1:-}"
LOG=/var/log/partyops-package-selftest.log
mkdir -p /var/log /var/lib/partyops
: >"$LOG"
exec 3>&1
exec >>"$LOG" 2>&1

fail() {
  code="$1"
  shift
  # 系统内升级的自检由更新服务子进程执行；失败时保留父更新服务，才能
  # 把包配置失败返回给执行器并完成回滚。人工安装失败仍停止两个服务。
  if [ "${PARTYOPS_IN_APP_UPDATE:-0}" = "1" ]; then
    systemctl stop partyops.service >/dev/null 2>&1 || true
  else
    systemctl stop partyops.service partyops-updater.service >/dev/null 2>&1 || true
  fi
  printf '[%s] %s\n' "$code" "$*" >&2
  printf '[%s] %s；详情：%s\n' "$code" "$*" "$LOG" >&3
  exit 2
}

case "$(uname -m)" in
  x86_64|amd64) ACTUAL_ARCH=amd64 ;;
  aarch64|arm64) ACTUAL_ARCH=arm64 ;;
  *) fail PACKAGE_ARCH_UNSUPPORTED "不支持的处理器架构：$(uname -m)" ;;
esac
[ -z "$EXPECTED_ARCH" ] || [ "$EXPECTED_ARCH" = "$ACTUAL_ARCH" ] ||
  fail PACKAGE_ARCH_MISMATCH "安装包架构 $EXPECTED_ARCH 与本机 $ACTUAL_ARCH 不一致"

for required in \
  "$RUNTIME/partyops" \
  "$RUNTIME/partyops-client" \
  "$RUNTIME/partyops-wizard" \
  "$RUNTIME/partyops-updater" \
  "$RUNTIME/release-files.sha256"; do
  [ -e "$required" ] || fail PACKAGE_FILE_MISSING "缺少安装文件：$required"
done
(cd "$RUNTIME" && sha256sum -c release-files.sha256) ||
  fail PACKAGE_FILE_HASH_MISMATCH "安装文件清单校验失败"

TEMP_ROOT="$(mktemp -d /run/partyops-package-selftest.XXXXXX)"
chown root:partyops "$TEMP_ROOT"
chmod 0750 "$TEMP_ROOT"
install -d -o partyops -g partyops -m 0700 "$TEMP_ROOT/data" "$TEMP_ROOT/health"
PID=
cleanup() {
  [ -z "$PID" ] || kill "$PID" >/dev/null 2>&1 || true
  [ -z "$PID" ] || wait "$PID" >/dev/null 2>&1 || true
  case "$TEMP_ROOT" in
    /run/partyops-package-selftest.*) rm -rf -- "$TEMP_ROOT" ;;
    *) printf '拒绝清理异常自检目录：%s\n' "$TEMP_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

runuser -u partyops -- env \
  PARTYOPS_DATA_DIR="$TEMP_ROOT/data" \
  PARTYOPS_ENVIRONMENT=production \
  PARTYOPS_STRICT_SQLITE=true \
  PARTYOPS_SEED_DEMO=false \
  "$RUNTIME/partyops" --package-self-test ||
  fail PACKAGE_RUNTIME_SELFTEST_FAILED "SQLite、前端、OCR 或本地智能运行时自检失败"

PORT=18796
while [ "$PORT" -lt 18896 ] && command -v ss >/dev/null 2>&1 &&
  ss -ltn | awk '{print $4}' | grep -Eq "[:.]$PORT$"; do
  PORT=$((PORT + 1))
done
[ "$PORT" -lt 18896 ] || fail PACKAGE_HEALTH_PORT_BUSY "找不到可用的回环自检端口"
runuser -u partyops -- env \
  PARTYOPS_DATA_DIR="$TEMP_ROOT/health" \
  PARTYOPS_ENVIRONMENT=production \
  PARTYOPS_STRICT_SQLITE=true \
  PARTYOPS_SEED_DEMO=false \
  PARTYOPS_BIND_HOST=127.0.0.1 \
  PARTYOPS_ADVERTISE_HOST=127.0.0.1 \
  PARTYOPS_HOST=127.0.0.1 \
  PARTYOPS_PORT="$PORT" \
  "$RUNTIME/partyops" >"$TEMP_ROOT/host.log" 2>&1 &
PID=$!
attempt=0
while [ "$attempt" -lt 180 ]; do
  kill -0 "$PID" >/dev/null 2>&1 || {
    tail -n 120 "$TEMP_ROOT/host.log" >&2 || true
    fail PACKAGE_CHILD_EXITED "临时主机进程提前退出"
  }
  if curl -fsS --connect-timeout 1 --max-time 3 \
    "http://127.0.0.1:$PORT/api/v1/health" >"$TEMP_ROOT/health.json" 2>/dev/null; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done
[ -s "$TEMP_ROOT/health.json" ] || fail PACKAGE_HEALTH_TIMEOUT "健康端点 180 秒内未就绪"
grep -q '"safe_version":true' "$TEMP_ROOT/health.json" ||
  fail PACKAGE_SQLITE_UNSAFE "SQLite 版本自检失败"
grep -q '"fts5":true' "$TEMP_ROOT/health.json" ||
  fail PACKAGE_FTS5_MISSING "SQLite FTS5 自检失败"

systemctl daemon-reload >/dev/null 2>&1 || fail PACKAGE_SYSTEMD_RELOAD_FAILED "systemd 配置刷新失败"
systemd-analyze verify /lib/systemd/system/partyops.service \
  /lib/systemd/system/partyops-updater.service >/dev/null 2>&1 ||
  fail PACKAGE_SYSTEMD_INVALID "systemd 服务定义验证失败"
printf 'PartyOps 安装后自检通过：架构=%s，健康端点与完整运行时正常。\n' "$ACTUAL_ARCH" >&3
