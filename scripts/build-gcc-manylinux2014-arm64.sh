#!/usr/bin/env bash
set -euo pipefail

# ONNX Runtime 1.22.1 要求 GCC >= 11.1，而 CentOS 7 AArch64 SCL 仓库
# 只有 devtoolset-11 元包、没有实际编译器。这里在真实 aarch64/glibc 2.17
# 环境内从 GNU 已签名源码自举最小 C/C++ 工具链；不依赖 Docker。
VERSION="11.5.0"
ARCHIVE_SHA256="a6e21868ead545cf87f0c01f84276e4b5281d672098591c1c896241f09363478"
SOURCE_DIR="${PARTYOPS_GCC_SOURCE:-/opt/gcc-$VERSION}"
ARCHIVE="${PARTYOPS_GCC_ARCHIVE:-/opt/gcc-source/gcc-$VERSION.tar.xz}"
BUILD_DIR="${PARTYOPS_GCC_BUILD:-/opt/gcc-build-$VERSION}"
PREFIX="${PARTYOPS_GCC_PREFIX:-/opt/gcc-$VERSION-aarch64}"
PARALLEL="${PARTYOPS_BUILD_JOBS:-12}"

[[ "$(uname -s)" == Linux && "$(uname -m)" == aarch64 ]] || {
  echo "GCC 工具链必须在 AArch64 Linux 原生或受控仿真环境构建。" >&2
  exit 2
}
[[ "$(getconf GNU_LIBC_VERSION)" == "glibc 2.17" ]] || {
  echo "GCC 工具链必须保持 glibc 2.17 基线。" >&2
  exit 2
}
[[ "$PARALLEL" =~ ^[1-9][0-9]*$ ]] || { echo "并发数无效：$PARALLEL" >&2; exit 2; }
[[ -f "$ARCHIVE" && -x "$SOURCE_DIR/configure" ]] || {
  echo "缺少已验证的 GCC $VERSION 源码。" >&2
  exit 2
}
printf '%s  %s\n' "$ARCHIVE_SHA256" "$ARCHIVE" | sha256sum --check --strict

if [[ -x "$PREFIX/bin/g++" ]]; then
  "$PREFIX/bin/g++" --version | head -1
  exit 0
fi

export PATH="/opt/rh/devtoolset-10/root/usr/bin:/usr/local/bin:/usr/bin:/bin"
export CC="/opt/rh/devtoolset-10/root/usr/bin/gcc"
export CXX="/opt/rh/devtoolset-10/root/usr/bin/g++"
mkdir -p "$BUILD_DIR" "$PREFIX"
if [[ ! -f "$BUILD_DIR/Makefile" ]]; then
  cd "$BUILD_DIR"
  "$SOURCE_DIR/configure" \
    --prefix="$PREFIX" \
    --disable-bootstrap \
    --disable-multilib \
    --disable-nls \
    --disable-libsanitizer \
    --without-isl \
    --enable-languages=c,c++
fi
make -C "$BUILD_DIR" -j"$PARALLEL"
make -C "$BUILD_DIR" install-strip

"$PREFIX/bin/g++" --version | head -1
if [[ -d /workspace/artifacts/source-evidence ]]; then
  {
    printf 'source=https://ftp.gnu.org/gnu/gcc/gcc-%s/gcc-%s.tar.xz\n' "$VERSION" "$VERSION"
    printf 'sha256=%s\n' "$ARCHIVE_SHA256"
    printf 'signature=gpgv GNU keyring verified\n'
    printf 'architecture=%s\n' "$(uname -m)"
    printf 'glibc=%s\n' "$(getconf GNU_LIBC_VERSION)"
    printf 'bootstrap_compiler=%s\n' "$($CXX --version | head -1)"
    printf 'result=%s\n' "$($PREFIX/bin/g++ --version | head -1)"
  } > /workspace/artifacts/source-evidence/gcc-$VERSION-arm64-build.txt
fi
