#!/usr/bin/env bash
set -euo pipefail

# PartyOps Linux 本地 LLM 运行时必须与 glibc 2.17 基线一致。上游 Ubuntu
# 预编译包面向更新系统，不能直接用于麒麟、UOS、deepin 和 openEuler 的
# 兼容包；这里从已锁定且已签名的上游标签重新构建单文件 CPU 运行时。

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="b10331"
COMMIT="7ba604f1cb61cd14898138e9abc0b4ff2601f180"
SHORT_COMMIT="7ba604f"
SOURCE_URL="https://github.com/ggml-org/llama.cpp/archive/refs/tags/${TAG}.tar.gz"
SOURCE_SHA256="73bfa7e5b56a818db7c9b3de5ab1156095eee6063efbb68d338c6a197ddac584"
SOURCE_DATE_EPOCH="1786228970"
ARCH="${PARTYOPS_BUILD_ARCH:-}"

if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "不支持的 llama.cpp 构建架构：$(uname -m)" >&2; exit 2 ;;
  esac
fi
[[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || {
  echo "仅支持 amd64/arm64：$ARCH" >&2
  exit 2
}
EXPECTED_MACHINE=x86_64
EXPECTED_FILE_PATTERN=x86-64
if [[ "$ARCH" == arm64 ]]; then
  EXPECTED_MACHINE=aarch64
  EXPECTED_FILE_PATTERN="ARM aarch64"
fi
[[ "$(uname -s)" == Linux && "$(uname -m)" == "$EXPECTED_MACHINE" ]] || {
  echo "必须在 $EXPECTED_MACHINE 原生或指令仿真环境构建；当前为 $(uname -m)。" >&2
  exit 2
}
GLIBC_VERSION="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
[[ "$GLIBC_VERSION" == 2.17 ]] || {
  echo "llama.cpp 正式运行时必须以 glibc 2.17 构建；当前为 $GLIBC_VERSION。" >&2
  exit 2
}

BUILD_BASE="${PARTYOPS_LLAMA_BUILD_BASE:-$ROOT/.build-linux}"
mkdir -p "$ROOT/.build-linux/inputs" "$ROOT/artifacts/tooling" "$BUILD_BASE"
SOURCE_ARCHIVE="${PARTYOPS_LLAMA_SOURCE_ARCHIVE:-$ROOT/.build-linux/inputs/llama.cpp-${TAG}.tar.gz}"
OUTPUT="${PARTYOPS_LLAMA_OUTPUT:-$ROOT/artifacts/tooling/llama-runtime-${ARCH}-${TAG}.tar.gz}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.11 || command -v python3 || true)}"
CMAKE_BIN="${CMAKE_BIN:-$(command -v cmake || true)}"
NINJA_BIN="${NINJA_BIN:-$(command -v ninja || true)}"
CC_BIN="${CC:-$(command -v gcc || true)}"
CXX_BIN="${CXX:-$(command -v g++ || true)}"
STRIP_BIN="${STRIP:-$(command -v strip || true)}"
for tool in "$PYTHON_BIN" "$CMAKE_BIN" "$NINJA_BIN" "$CC_BIN" "$CXX_BIN" "$STRIP_BIN"; do
  [[ -n "$tool" && -x "$tool" ]] || {
    echo "缺少 llama.cpp 可复现构建工具：${tool:-未找到}" >&2
    exit 2
  }
done

if [[ ! -f "$SOURCE_ARCHIVE" ]]; then
  command -v curl >/dev/null 2>&1 || {
    echo "缺少源码归档且未安装 curl：$SOURCE_ARCHIVE" >&2
    exit 2
  }
  TEMP_DOWNLOAD="${SOURCE_ARCHIVE}.download"
  curl --fail --location --silent --show-error "$SOURCE_URL" --output "$TEMP_DOWNLOAD"
  [[ "$(sha256sum "$TEMP_DOWNLOAD" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || {
    echo "llama.cpp 源码归档 SHA-256 不匹配，拒绝使用。" >&2
    exit 2
  }
  mv "$TEMP_DOWNLOAD" "$SOURCE_ARCHIVE"
fi
[[ "$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || {
  echo "llama.cpp 源码归档 SHA-256 不匹配：$SOURCE_ARCHIVE" >&2
  exit 2
}
gzip -dc "$SOURCE_ARCHIVE" |
  "$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py" \
    --expected-root "llama.cpp-${TAG}" --max-members 100000 --max-bytes 2147483648

BUILD="$(mktemp -d "$BUILD_BASE/llama-runtime.XXXXXX")"
cleanup() {
  status=$?
  trap - EXIT
  case "$BUILD" in
    "$BUILD_BASE/llama-runtime."*) rm -rf -- "$BUILD" ;;
    *) echo "拒绝清理异常 llama.cpp 构建目录：$BUILD" >&2 ;;
  esac
  exit "$status"
}
trap cleanup EXIT
tar -xzf "$SOURCE_ARCHIVE" -C "$BUILD" --no-same-owner --no-same-permissions
SOURCE_DIR="$BUILD/llama.cpp-${TAG}"
[[ -f "$SOURCE_DIR/CMakeLists.txt" ]] || {
  echo "llama.cpp 源码归档缺少 CMakeLists.txt。" >&2
  exit 2
}
# codeload 归档不包含 .git；只改构建元数据，使 --version 可追溯到锁定提交。
sed -i \
  -e 's/set(BUILD_NUMBER 0)/set(BUILD_NUMBER 1)/' \
  -e "s/set(BUILD_COMMIT \"unknown\")/set(BUILD_COMMIT \"$SHORT_COMMIT\")/" \
  "$SOURCE_DIR/cmake/build-info.cmake"

# 源码归档没有 .git，而且构建目录位于 PartyOps 仓库内部。若直接调用系统
# git，CMake 会错误继承 PartyOps 的提交号。只为上游构建元数据提供受限的
# 只读 git 响应，其他命令一律失败。
FAKE_GIT="$BUILD/partyops-llama-git-metadata"
cat >"$FAKE_GIT" <<EOF
#!/usr/bin/env bash
case "\$*" in
  "--version") echo "git version 2.0.0" ;;
  "rev-parse --short HEAD") echo "$SHORT_COMMIT" ;;
  "rev-list --count HEAD") echo "1" ;;
  "diff-index --quiet HEAD -- .") exit 0 ;;
  *) exit 2 ;;
esac
EOF
chmod 0755 "$FAKE_GIT"

BUILD_DIR="$BUILD/build"
"$CMAKE_BIN" -S "$SOURCE_DIR" -B "$BUILD_DIR" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA_BIN" \
  -DCMAKE_C_COMPILER="$CC_BIN" \
  -DCMAKE_CXX_COMPILER="$CXX_BIN" \
  -DGIT_EXECUTABLE="$FAKE_GIT" \
  -DGIT_EXE="$FAKE_GIT" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now" \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_BUILD_COMMIT="$SHORT_COMMIT" \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  -DGGML_BLAS=OFF \
  -DLLAMA_OPENSSL=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DLLAMA_USE_PREBUILT_UI=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_APP=OFF \
  -DLLAMA_BUILD_SERVER=ON
"$CMAKE_BIN" --build "$BUILD_DIR" --target llama-server -j "${PARTYOPS_BUILD_JOBS:-2}"

RUNTIME_DIR="$BUILD/runtime/llama-${TAG}"
mkdir -p "$RUNTIME_DIR"
cp "$BUILD_DIR/bin/llama-server" "$RUNTIME_DIR/llama-server"
"$STRIP_BIN" --strip-unneeded "$RUNTIME_DIR/llama-server"
chmod 0755 "$RUNTIME_DIR/llama-server"

file "$RUNTIME_DIR/llama-server" | grep -q "$EXPECTED_FILE_PATTERN" || {
  echo "llama-server ELF 架构与目标 $ARCH 不一致。" >&2
  exit 2
}
if ldd "$RUNTIME_DIR/llama-server" 2>&1 | grep -q 'not found'; then
  echo "llama-server 存在缺失的动态库依赖。" >&2
  ldd "$RUNTIME_DIR/llama-server" >&2 || true
  exit 2
fi
MAX_GLIBC="$(readelf --version-info "$RUNTIME_DIR/llama-server" |
  grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1)"
if [[ -z "$MAX_GLIBC" ]] ||
  [[ "$(printf '%s\n' "$MAX_GLIBC" GLIBC_2.17 | sort -V | tail -1)" != GLIBC_2.17 ]]; then
  echo "llama-server 最低 glibc 基线超出 2.17：${MAX_GLIBC:-未知}" >&2
  exit 2
fi
VERSION_OUTPUT="$("$RUNTIME_DIR/llama-server" --version 2>&1)"
grep -q "$SHORT_COMMIT" <<<"$VERSION_OUTPUT" || {
  echo "llama-server 版本信息未包含锁定提交 $SHORT_COMMIT。" >&2
  exit 2
}

mkdir -p "$(dirname "$OUTPUT")"
tar --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
  -czf "$OUTPUT" -C "$BUILD/runtime" "llama-${TAG}"
sha256sum "$OUTPUT" >"$OUTPUT.sha256"
echo "$VERSION_OUTPUT"
echo "llama.cpp $ARCH glibc 2.17 运行时已生成：$OUTPUT"
