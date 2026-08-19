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

# 必须先完成运行时、静态资源、健康端点和 systemd 自检，再启用更新服务。
# 任一步失败都原样返回给 dpkg/rpm，避免 GUI 在 95% 处只显示笼统异常。
"$RUNTIME/post-install-selftest.sh" "$EXPECTED_ARCH"
"$RUNTIME/post-install-services.sh" "$PACKAGE_FORMAT"
