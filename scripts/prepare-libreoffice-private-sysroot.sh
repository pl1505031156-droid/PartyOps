#!/usr/bin/env bash
set -euo pipefail

# 为 LibreOffice 构造固定架构的 glibc 2.34 私有运行根。该根只用于提取
# 必需共享库，不会安装到用户系统，也不会替换宿主机 glibc。
ARCHITECTURE="${1:-}"
case "$ARCHITECTURE" in
  amd64)
    IMAGE='quay.io/pypa/manylinux_2_34_x86_64@sha256:224ae18ddb6998745b5554185f8f1a4d256543199272ecab4389d310e5e36146'
    EXPECTED_MACHINE=x86_64
    EXPECTED_PACKAGE_MANIFEST_SHA=dd5ddb478f8863533b48baf2273411ab7c110f4609a74590223f7d9716dcb6cb
    ;;
  arm64)
    IMAGE='quay.io/pypa/manylinux_2_34_aarch64@sha256:b3f10ce321fc98427010670982361997e7d1ccd6dbfed1a4b69dc28cab61ee6a'
    EXPECTED_MACHINE=aarch64
    EXPECTED_PACKAGE_MANIFEST_SHA=82b2b3b8c65cc1fcd369b86b0ca0b3b3ed675304898354b8f15143ba57365e90
    ;;
  *)
    echo "用法：prepare-libreoffice-private-sysroot.sh amd64|arm64" >&2
    exit 2
    ;;
esac

CRANE="${PARTYOPS_CRANE:-/opt/crane-v0.21.9/crane}"
ROOT="${PARTYOPS_LIBREOFFICE_SYSROOT:-/opt/partyops-libreoffice-sysroot-$ARCHITECTURE}"
MARKER="$ROOT/PARTYOPS_SYSROOT_SOURCE.txt"
QEMU="${PARTYOPS_QEMU_AARCH64:-/usr/bin/qemu-aarch64-static}"
REGISTERED=0
MOUNTS=()

[[ "$(id -u)" -eq 0 && "$(uname -s)" == Linux ]] || {
  echo "私有运行根必须在隔离 Linux 构建环境中以 root 准备。" >&2
  exit 2
}
[[ -x "$CRANE" ]] || { echo "缺少固定 crane 工具：$CRANE" >&2; exit 2; }

cleanup() {
  local status=$? index
  trap - EXIT INT TERM
  for ((index=${#MOUNTS[@]} - 1; index >= 0; index--)); do
    umount "${MOUNTS[$index]}" >/dev/null 2>&1 || true
  done
  if [[ "$REGISTERED" -eq 1 && -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
    printf '%s\n' -1 >/proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

if [[ ! -e "$ROOT" ]]; then
  PARENT="$(dirname "$ROOT")"
  mkdir -p "$PARENT"
  STAGE="$(mktemp -d "$PARENT/.partyops-libreoffice-sysroot-$ARCHITECTURE.XXXXXX")"
  "$CRANE" export "$IMAGE" - | tar -xf - -C "$STAGE"
  printf '%s\n' "$IMAGE" >"$STAGE/PARTYOPS_SYSROOT_SOURCE.txt"
  mv "$STAGE" "$ROOT"
fi
[[ -f "$MARKER" && "$(cat "$MARKER")" == "$IMAGE" ]] || {
  echo "私有运行根来源不匹配，拒绝复用：$ROOT" >&2
  exit 2
}

if [[ "$ARCHITECTURE" == arm64 ]]; then
  [[ -x "$QEMU" ]] || { echo "缺少 ARM64 指令翻译器：$QEMU" >&2; exit 2; }
  if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
    printf '%s\n' \
      ':qemu-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/usr/bin/qemu-aarch64-static:CF' \
      >/proc/sys/fs/binfmt_misc/register
    REGISTERED=1
  fi
  if [[ ! -x "$ROOT/usr/bin/qemu-aarch64-static" ]]; then
    cp "$QEMU" "$ROOT/usr/bin/qemu-aarch64-static"
  fi
fi

for system_path in proc dev sys; do
  mkdir -p "$ROOT/$system_path"
  if ! mountpoint -q "$ROOT/$system_path"; then
    mount --bind "/$system_path" "$ROOT/$system_path"
    MOUNTS+=("$ROOT/$system_path")
  fi
done
if ! mountpoint -q "$ROOT/etc/resolv.conf"; then
  mount --bind /etc/resolv.conf "$ROOT/etc/resolv.conf"
  MOUNTS+=("$ROOT/etc/resolv.conf")
fi

[[ "$(chroot "$ROOT" /usr/bin/uname -m)" == "$EXPECTED_MACHINE" ]] || {
  echo "私有运行根 CPU 架构错误。" >&2
  exit 2
}
[[ "$(chroot "$ROOT" /usr/bin/getconf GNU_LIBC_VERSION)" == 'glibc 2.34' ]] || {
  echo "私有运行根必须固定为 glibc 2.34。" >&2
  exit 2
}

chroot "$ROOT" /bin/bash -lc '
  set -euo pipefail
  dnf -y --setopt=install_weak_deps=False install \
    avahi-libs cairo cups-libs dbus-libs fontconfig freetype glib2 gpgme \
    krb5-libs libICE libSM libX11 libXext libXinerama libXrandr \
    libXrender libassuan libepoxy libgpg-error libxcb libxml2 libxslt \
    nspr nss pango zlib
  rpm -qa --qf "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n" | sort \
    >/PARTYOPS_SYSROOT_PACKAGES.txt
'
test -s "$ROOT/PARTYOPS_SYSROOT_PACKAGES.txt"
ACTUAL_PACKAGE_MANIFEST_SHA="$(sha256sum "$ROOT/PARTYOPS_SYSROOT_PACKAGES.txt" | awk '{print $1}')"
[[ "$ACTUAL_PACKAGE_MANIFEST_SHA" == "$EXPECTED_PACKAGE_MANIFEST_SHA" ]] || {
  echo "私有运行根软件包版本发生漂移：$ACTUAL_PACKAGE_MANIFEST_SHA" >&2
  exit 2
}
printf 'LibreOffice 私有运行根已准备：%s（%s / glibc 2.34）\n' \
  "$ROOT" "$ARCHITECTURE"
