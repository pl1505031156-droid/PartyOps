#!/usr/bin/env bash
set -euo pipefail

# 在本机 WSL 的 glibc 2.17 aarch64 根文件系统内真实执行 ARM64 构建。
# QEMU 只负责指令翻译；Python、GCC、PyInstaller 和运行时自检均使用
# aarch64 二进制，避免在 x86_64 上伪造 ARM 载荷。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ACTION="${1:-portable}"
ROOTFS="${PARTYOPS_ARM64_ROOTFS:-/opt/manylinux2014-aarch64-rootfs}"
MOUNT_POINT="$ROOTFS/workspace/partyops"
QEMU_INTERPRETER="${PARTYOPS_QEMU_AARCH64:-/usr/bin/qemu-aarch64-static}"
HOST_PYTHON_BIN="${PARTYOPS_HOST_PYTHON_BIN:-/opt/partyops-python/cpython-3.11.15-linux-x86_64-gnu/bin/python3}"
REGISTERED_BY_SCRIPT=0
MOUNTS=()

command -v flock >/dev/null 2>&1 || {
  echo "缺少 flock，无法串行化 ARM64 chroot 构建。" >&2
  exit 2
}
exec 9>/var/lock/partyops-arm64-build.lock
flock -w 3600 9 || {
  echo "另一个 ARM64 构建超过 60 分钟仍未结束，拒绝并发写入同一制品。" >&2
  exit 2
}

case "$ACTION" in
  portable|deb|rpm|test-deb|test-rpm) ;;
  *)
    echo "用法：build-linux-arm64-chroot.sh [portable|deb|rpm|test-deb|test-rpm]" >&2
    exit 2
    ;;
esac

[[ "$(id -u)" -eq 0 ]] || {
  echo "ARM64 chroot 构建需要在隔离 WSL 中以 root 执行。" >&2
  exit 2
}
[[ -d "$ROOTFS" && -x "$QEMU_INTERPRETER" ]] || {
  echo "缺少 ARM64 根文件系统或 QEMU：$ROOTFS / $QEMU_INTERPRETER" >&2
  exit 2
}
[[ -x "$ROOTFS/opt/partyops-python-3.11.15-arm64/bin/python3.11" &&
  -x "$ROOTFS/opt/gcc-11.5.0-aarch64/bin/gcc" ]] || {
  echo "ARM64 根文件系统缺少冻结 Python 3.11 或 GCC 11。" >&2
  exit 2
}

# DEB/RPM 的 cpio/tar 元数据封装与目标 CPU 无关；ARM64 最小运行根故意
# 不安装 dpkg/rpmbuild。载荷仍必须先在上面的 ARM64 chroot 中冻结并通过
# 自检，随后由宿主官方包工具封装，最终成品再送回 chroot 动态启动。
if [[ "$ACTION" == deb ]]; then
  [[ -x "$HOST_PYTHON_BIN" ]] || {
    echo "宿主缺少 Linux 打包门禁 Python：$HOST_PYTHON_BIN" >&2
    exit 2
  }
  (
    cd "$ROOT"
    export PYTHON_BIN="$HOST_PYTHON_BIN"
    export PARTYOPS_BUILD_ARCH=arm64
    export PARTYOPS_BUILD_BASE=/tmp/partyops-build-arm64-package
    # 本入口的 portable 阶段只会在上方受控 ARM64 chroot 中成功产出；
    # 普通 build-native 直接交叉封装仍保持默认拒绝。
    export PARTYOPS_ALLOW_CROSS_PACKAGE=1
    bash packaging/linux/build-native.sh deb
  )
  exit 0
fi

cleanup() {
  local status=$? index
  trap - EXIT INT TERM
  for ((index=${#MOUNTS[@]} - 1; index >= 0; index--)); do
    umount "${MOUNTS[$index]}" >/dev/null 2>&1 || true
  done
  if [[ "$REGISTERED_BY_SCRIPT" -eq 1 &&
    -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
    printf '%s\n' -1 >/proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
  printf '%s\n' \
    ':qemu-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/usr/bin/qemu-aarch64-static:CF' \
    >/proc/sys/fs/binfmt_misc/register
  REGISTERED_BY_SCRIPT=1
fi

mount_once() {
  local source="$1" target="$2"
  mkdir -p "$target"
  if ! mountpoint -q "$target"; then
    mount --bind "$source" "$target"
    MOUNTS+=("$target")
  fi
}

mount_once /proc "$ROOTFS/proc"
mount_once /dev "$ROOTFS/dev"
mount_once /sys "$ROOTFS/sys"
mount_once "$ROOT" "$MOUNT_POINT"
# 发布工作树为节省空间会把大型 vendor/artifacts 指向外层固定目录。
# chroot 不会自动解析宿主机绝对链接，必须把解析后的目标挂到相同绝对
# 路径；只允许这两个已知顶层链接，禁止跟随任意仓库内链接扩大范围。
for linked_root in vendor artifacts; do
  linked_target="$(readlink -f "$ROOT/$linked_root")"
  [[ -d "$linked_target" ]] || {
    echo "构建输入链接无效：$ROOT/$linked_root -> $linked_target" >&2
    exit 2
  }
  mount_once "$linked_target" "$ROOTFS$linked_target"
done

chroot "$ROOTFS" /bin/bash -lc "
  set -euo pipefail
  cd /workspace/partyops
  export PATH=/opt/gcc-11.5.0-aarch64/bin:/opt/partyops-python-3.11.15-arm64/bin:/usr/local/bin:/usr/bin:/bin
  export PYTHON_BIN=/opt/partyops-python-3.11.15-arm64/bin/python3.11
  export PARTYOPS_BUILD_ARCH=arm64
  export PARTYOPS_BUILD_BASE=/tmp/partyops-build-arm64
  case '$ACTION' in
    portable) bash packaging/uos/build-portable.sh ;;
    rpm) bash packaging/linux/build-native.sh rpm ;;
    test-deb)
      bash scripts/test-native-package-runtime.sh \
        artifacts/PartyOps_1.4.5-rc.2_linux_arm64.deb arm64
      ;;
    test-rpm)
      bash scripts/test-native-package-runtime.sh \
        artifacts/PartyOps-1.4.5-0.rc.1.1.aarch64.rpm arm64
      ;;
  esac
"
