#!/usr/bin/env sh
set -eu

RUNTIME=/opt/partyops
EXPECTED_ARCH="${1:-}"
EXPECTED_VERSION="1.4.5-rc.1"
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
  tail -n 120 "$LOG" >&3 2>/dev/null || true
  printf '修复原因后可执行：sudo dpkg --configure -a（DEB）或重新安装当前 RPM。\n' >&3
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
  "$RUNTIME/VERSION" \
  "$RUNTIME/release-files.sha256"; do
  [ -e "$required" ] || fail PACKAGE_FILE_MISSING "缺少安装文件：$required"
done
[ "$(cat "$RUNTIME/VERSION" 2>/dev/null)" = "$EXPECTED_VERSION" ] ||
  fail PACKAGE_VERSION_MISMATCH "安装载荷版本标识与当前版本不一致"
(command -v bash >/dev/null 2>&1 &&
  bash -n "$RUNTIME/desktop-launcher.sh" "$RUNTIME/start.sh") ||
  fail PACKAGE_DESKTOP_SCRIPT_INVALID "桌面启动脚本缺失或语法检查失败"
runuser -u partyops -- "$RUNTIME/partyops-wizard" --runtime-layout-self-test ||
  fail PACKAGE_RUNTIME_LAYOUT_INVALID \
    "配置向导仍在临时目录解包共享库，可能被国产系统安全中心拦截"
WIZARD_TEMP_ROOT="$(mktemp -d /run/partyops-wizard-selftest.XXXXXX)"
chown partyops:partyops "$WIZARD_TEMP_ROOT"
chmod 0700 "$WIZARD_TEMP_ROOT"
if ! runuser -u partyops -- env \
  PARTYOPS_DESKTOP_SELFTEST_ROOT="$WIZARD_TEMP_ROOT" \
  "$RUNTIME/partyops-wizard" --desktop-server-self-test; then
  case "$WIZARD_TEMP_ROOT" in
    /run/partyops-wizard-selftest.*) rm -rf -- "$WIZARD_TEMP_ROOT" ;;
  esac
  fail PACKAGE_WIZARD_SERVER_INVALID \
    "配置向导无法绑定回环页面或发布桌面启动标记"
fi
case "$WIZARD_TEMP_ROOT" in
  /run/partyops-wizard-selftest.*) rm -rf -- "$WIZARD_TEMP_ROOT" ;;
esac
for desktop_entry in \
  /usr/share/applications/partyops.desktop \
  /usr/share/applications/partyops-client.desktop \
  /usr/share/applications/partyops-file.desktop; do
  [ -r "$desktop_entry" ] ||
    fail PACKAGE_DESKTOP_ENTRY_MISSING "缺少桌面入口：$desktop_entry"
  if LC_ALL=C grep -q "$(printf '\r')" "$desktop_entry"; then
    fail PACKAGE_DESKTOP_ENTRY_INVALID "桌面入口包含 Windows CRLF 换行：$desktop_entry"
  fi
  grep -q '^Type=Application$' "$desktop_entry" ||
    fail PACKAGE_DESKTOP_ENTRY_INVALID "桌面入口格式无效：$desktop_entry"
  case "$desktop_entry" in
    */partyops-file.desktop)
      grep -q '^TryExec=/opt/partyops/open-local-file.sh$' "$desktop_entry" ||
        fail PACKAGE_DESKTOP_ENTRY_INVALID "文件桌面入口未绑定受控打开器：$desktop_entry"
      ;;
    *)
      grep -q '^TryExec=/bin/bash$' "$desktop_entry" ||
        fail PACKAGE_DESKTOP_ENTRY_INVALID "桌面入口未绑定受控启动器：$desktop_entry"
      ;;
  esac
done
if command -v desktop-file-validate >/dev/null 2>&1; then
  DESKTOP_VALIDATE_LOG=/var/log/partyops-desktop-file-validate.log
  : >"$DESKTOP_VALIDATE_LOG"
  if ! desktop-file-validate \
    /usr/share/applications/partyops.desktop \
    /usr/share/applications/partyops-client.desktop \
    /usr/share/applications/partyops-file.desktop >"$DESKTOP_VALIDATE_LOG" 2>&1; then
    cat "$DESKTOP_VALIDATE_LOG" >&2 2>/dev/null || true
    fail PACKAGE_DESKTOP_ENTRY_INVALID "桌面入口未通过系统格式校验"
  fi
fi
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
grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$TEMP_ROOT/health.json" ||
  fail PACKAGE_HEALTH_INVALID "健康端点没有返回就绪状态"
grep -Fq "\"app_version\":\"$EXPECTED_VERSION\"" "$TEMP_ROOT/health.json" ||
  fail PACKAGE_VERSION_MISMATCH "健康端点运行的不是当前 PartyOps 版本"
grep -q '"safe_version":true' "$TEMP_ROOT/health.json" ||
  fail PACKAGE_SQLITE_UNSAFE "SQLite 版本自检失败"
grep -q '"fts5":true' "$TEMP_ROOT/health.json" ||
  fail PACKAGE_FTS5_MISSING "SQLite FTS5 自检失败"

systemctl daemon-reload >/dev/null 2>&1 || fail PACKAGE_SYSTEMD_RELOAD_FAILED "systemd 配置刷新失败"
SYSTEMD_VERIFY_LOG="$TEMP_ROOT/systemd-verify.log"
if ! systemd-analyze verify /lib/systemd/system/partyops.service \
  /lib/systemd/system/partyops-updater.service >"$SYSTEMD_VERIFY_LOG" 2>&1; then
  cat "$SYSTEMD_VERIFY_LOG" >&2 2>/dev/null || true
  fail PACKAGE_SYSTEMD_INVALID \
    "systemd 服务定义与当前麒麟/UOS版本不兼容"
fi
printf 'PartyOps 安装后自检通过：架构=%s，桌面入口、健康端点与完整运行时正常。\n' "$ACTUAL_ARCH" >&3
