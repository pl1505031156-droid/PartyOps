#!/usr/bin/env sh
set -eu

PACKAGE_FORMAT="${1:-}"
case "$PACKAGE_FORMAT" in
  deb|rpm) ;;
  *)
    echo '[PACKAGE_SCRIPT_ARGUMENT_INVALID] 安装后服务脚本缺少有效包格式。' >&2
    exit 2
    ;;
esac

# 测试根目录只允许源码副本使用；安装到 /opt 的正式脚本无条件写系统路径，
# 即使调用者向 sudo/dpkg 注入同名环境变量也不能绕过真实安装后自检。
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT_PREFIX=
if [ "$SCRIPT_DIR" != "/opt/partyops" ] && [ "${PARTYOPS_PACKAGE_TESTING:-0}" = "1" ]; then
  ROOT_PREFIX="${PARTYOPS_PACKAGE_TEST_ROOT:?缺少隔离测试根目录}"
fi
INSTALL_LOG="$ROOT_PREFIX/var/log/partyops-package-install.log"
RESTART_MARKER="$ROOT_PREFIX/run/partyops/restart-after-upgrade"
mkdir -p "$(dirname "$INSTALL_LOG")"

repair_hint() {
  if [ "$PACKAGE_FORMAT" = "deb" ]; then
    echo '修复上方原因后执行：sudo dpkg --configure -a' >&2
  else
    echo '修复上方原因后重新安装当前 RPM。' >&2
  fi
}

if ! {
  systemctl enable partyops-updater.service &&
    systemctl start partyops-updater.service
} >>"$INSTALL_LOG" 2>&1; then
  systemctl disable --now partyops-updater.service >/dev/null 2>&1 || true
  echo '[PACKAGE_UPDATER_START_FAILED] PartyOps 更新服务未能启用，安装配置已停止。' >&2
  tail -n 120 "$INSTALL_LOG" >&2 2>/dev/null || true
  journalctl -u partyops-updater.service -n 80 --no-pager >&2 2>/dev/null || true
  repair_hint
  exit 2
fi

if [ -f "$RESTART_MARKER" ]; then
  if ! systemctl restart partyops.service >>"$INSTALL_LOG" 2>&1; then
    echo '[PACKAGE_HOST_RESTART_FAILED] 升级后主机服务未能恢复，保留重试标记。' >&2
    journalctl -u partyops.service -n 80 --no-pager >&2 2>/dev/null || true
    repair_hint
    exit 2
  fi
  rm -f -- "$RESTART_MARKER"
fi

echo 'PartyOps 服务配置完成。' >&2
