#!/usr/bin/env sh
set -eu

EXPECTED_ARCH="${1:-}"
PACKAGE_FORMAT="${2:-}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
RUNTIME=/opt/partyops
if [ "$SCRIPT_DIR" != "/opt/partyops" ] && [ "${PARTYOPS_PACKAGE_TESTING:-0}" = "1" ]; then
  RUNTIME="${PARTYOPS_PACKAGE_TEST_RUNTIME:?缺少隔离测试运行目录}"
fi

case "$EXPECTED_ARCH" in
  amd64|arm64) ;;
  *)
    echo '[PACKAGE_SCRIPT_ARGUMENT_INVALID] 安装后事务缺少有效处理器架构。' >&2
    exit 2
    ;;
esac
case "$PACKAGE_FORMAT" in
  deb|rpm) ;;
  *)
    echo '[PACKAGE_SCRIPT_ARGUMENT_INVALID] 安装后事务缺少有效包格式。' >&2
    exit 2
    ;;
esac

# 包管理器事务只执行安装必需的快速检查；冻结运行时启动、数据库迁移和
# 健康检查交给下面的可观察 oneshot 服务，避免麒麟图形安装器在 1% 长时间假死。
"$RUNTIME/post-install-selftest.sh" "$EXPECTED_ARCH" quick
"$RUNTIME/post-install-services.sh" "$PACKAGE_FORMAT"
systemctl daemon-reload >/dev/null 2>&1 || {
  echo '[PACKAGE_SYSTEMD_RELOAD_FAILED] 无法刷新 PartyOps 安装后验证服务。' >&2
  exit 2
}
systemctl start --no-block partyops-install-verify.service >/dev/null 2>&1 || {
  echo '[PACKAGE_VERIFY_SERVICE_START_FAILED] 无法启动 PartyOps 安装后验证服务。' >&2
  exit 2
}
printf 'PartyOps 安装事务已完成；运行验证在后台继续，状态：/var/lib/partyops/install-verification.json\n' >&2
