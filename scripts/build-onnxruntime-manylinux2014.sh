#!/usr/bin/env bash
set -euo pipefail

# 从 ONNX Runtime 官方不可变提交在真实 glibc 2.17 环境构建 CPU wheel。
# 该脚本不依赖 Docker，也不允许通过重命名伪造 manylinux2014 兼容性。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="1.22.1"
COMMIT="89746dc19a0a1ae59ebf4b16df9acab8f99f3925"
REPOSITORY="https://github.com/microsoft/onnxruntime.git"
ARCHITECTURE="${1:-}"
PYTHON_BIN="${2:-}"
SOURCE_DIR="${PARTYOPS_ONNX_SOURCE:-/opt/onnxruntime-v$VERSION}"
BUILD_DIR="${PARTYOPS_ONNX_BUILD:-/opt/onnxruntime-build-$VERSION}"
PARALLEL="${PARTYOPS_BUILD_JOBS:-12}"
REUSE_BUILD="${PARTYOPS_ONNX_REUSE_BUILD:-0}"

[[ "$ARCHITECTURE" == amd64 || "$ARCHITECTURE" == arm64 ]] || {
  echo "用法：build-onnxruntime-manylinux2014.sh amd64|arm64 /path/to/python" >&2
  exit 2
}
[[ -x "$PYTHON_BIN" ]] || { echo "Python 不可执行：$PYTHON_BIN" >&2; exit 2; }
EXPECTED_MACHINE=x86_64
[[ "$ARCHITECTURE" == arm64 ]] && EXPECTED_MACHINE=aarch64
[[ "$(uname -s)" == Linux && "$(uname -m)" == "$EXPECTED_MACHINE" ]] || {
  echo "必须在 $EXPECTED_MACHINE Linux 原生或受控仿真环境构建。" >&2
  exit 2
}
[[ "$(getconf GNU_LIBC_VERSION)" == "glibc 2.17" ]] || {
  echo "ONNX Runtime 正式包必须在 glibc 2.17 构建。" >&2
  exit 2
}
for command_name in git cmake ninja auditwheel patchelf; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "缺少固定版本构建工具：$command_name" >&2
    exit 2
  }
done
[[ "$PARALLEL" =~ ^[1-9][0-9]*$ ]] || { echo "并发数无效：$PARALLEL" >&2; exit 2; }
[[ "$REUSE_BUILD" == 0 || "$REUSE_BUILD" == 1 ]] || {
  echo "PARTYOPS_ONNX_REUSE_BUILD 只能是 0 或 1。" >&2
  exit 2
}

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  git clone --recursive --depth 1 --branch "v$VERSION" "$REPOSITORY" "$SOURCE_DIR"
fi
cd "$SOURCE_DIR"
[[ "$(git rev-parse HEAD)" == "$COMMIT" ]] || {
  echo "ONNX Runtime 源码提交不匹配，拒绝构建。" >&2
  exit 2
}
if git submodule status --recursive | grep -Eq '^[+-]'; then
  echo "ONNX Runtime 子模块未固定到提交记录。" >&2
  exit 2
fi

export CC="${CC:-$(command -v gcc)}"
export CXX="${CXX:-$(command -v g++)}"
# manylinux2014 ARM64 基础镜像的系统 binutils 2.27 不认识
# ``-march=armv8.2-a+bf16``。优先固定到镜像自带的 devtoolset-10
# binutils，避免 GCC 11 意外回退到旧汇编器并产生不可复现的构建结果。
if [[ "$ARCHITECTURE" == arm64 && -x /opt/rh/devtoolset-10/root/usr/bin/as ]]; then
  export PATH="/opt/rh/devtoolset-10/root/usr/bin:$PATH"
fi
export CFLAGS="${CFLAGS:--O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2}"
export CXXFLAGS="${CXXFLAGS:--O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2 -static-libstdc++ -static-libgcc}"
"$PYTHON_BIN" -c 'import flatbuffers, numpy, packaging, setuptools, wheel'

if [[ "$REUSE_BUILD" == 0 ]]; then
  ./build.sh \
    --build_dir "$BUILD_DIR" \
    --config Release \
    --update --build --build_wheel \
    --parallel "$PARALLEL" \
    --compile_no_warning_as_error \
    --skip_tests \
    --allow_running_as_root \
    --cmake_generator Ninja \
    --cmake_extra_defines \
      'CMAKE_SHARED_LINKER_FLAGS=-static-libstdc++ -static-libgcc' \
      'CMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc'
fi

# qemu/chroot 环境不一定挂载 /dev/fd，不能依赖 Bash 进程替换。普通临时
# 文件既能保留空格路径，也能让“编译完成、审计失败”后只重跑封装阶段。
wheel_list="$(mktemp)"
trap 'rm -f -- "$wheel_list"' EXIT
find "$BUILD_DIR/Release/dist" -maxdepth 1 -type f \
  -name "onnxruntime-$VERSION-*.whl" -print > "$wheel_list"
mapfile -t wheels < "$wheel_list"
[[ "${#wheels[@]}" == 1 ]] || {
  echo "ONNX Runtime wheel 数量异常：${#wheels[@]}" >&2
  exit 2
}
auditwheel show "${wheels[0]}"
REPAIRED="$BUILD_DIR/repaired"
mkdir -p "$REPAIRED" "$ROOT/artifacts/source-evidence" "$ROOT/vendor/wheels/$ARCHITECTURE"
auditwheel repair --plat "manylinux2014_$EXPECTED_MACHINE" \
  --wheel-dir "$REPAIRED" "${wheels[0]}"
: > "$wheel_list"
find "$REPAIRED" -maxdepth 1 -type f \
  -name "onnxruntime-$VERSION-*.whl" -print > "$wheel_list"
mapfile -t repaired < "$wheel_list"
[[ "${#repaired[@]}" == 1 ]] || {
  echo "修复后的 ONNX Runtime wheel 数量异常：${#repaired[@]}" >&2
  exit 2
}
destination="$ROOT/vendor/wheels/$ARCHITECTURE/$(basename "${repaired[0]}")"
[[ ! -e "$destination" ]] || { echo "目标 wheel 已存在：$destination" >&2; exit 2; }
cp -- "${repaired[0]}" "$destination"
sha256sum "$destination" | tee \
  "$ROOT/artifacts/source-evidence/onnxruntime-$VERSION-$ARCHITECTURE-wheel.sha256"
{
  printf 'repository=%s\n' "$REPOSITORY"
  printf 'tag=v%s\n' "$VERSION"
  printf 'commit=%s\n' "$COMMIT"
  printf 'architecture=%s\n' "$ARCHITECTURE"
  printf 'glibc=%s\n' "$(getconf GNU_LIBC_VERSION)"
  printf 'compiler=%s\n' "$($CXX --version | head -1)"
  printf 'cmake=%s\n' "$(cmake --version | head -1)"
  printf 'auditwheel=%s\n' "$(auditwheel --version | head -1)"
} > "$ROOT/artifacts/source-evidence/onnxruntime-$VERSION-$ARCHITECTURE-build.txt"
echo "已生成并验证：$destination"
