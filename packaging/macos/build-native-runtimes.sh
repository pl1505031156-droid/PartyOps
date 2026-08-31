#!/bin/bash
set -euo pipefail
umask 077

# 在与目标一致的真实 Mac 上，从已锁定源码生成不依赖 Homebrew 路径的
# OCR 与 llama.cpp 运行时。制品会再次由 validate-bundle.sh 扫描 Mach-O。

TARGET_ARCH=''
OUTPUT_ROOT=''
while (($#)); do
  case "$1" in
    --architecture)
      TARGET_ARCH="${2:-}"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="${2:-}"
      shift 2
      ;;
    *)
      printf '未知参数：%s\n' "$1" >&2
      exit 2
      ;;
  esac
done

case "$TARGET_ARCH" in
  arm64|x86_64) ;;
  *)
    printf '%s\n' '用法：build-native-runtimes.sh --architecture arm64|x86_64 --output-root <目录>' >&2
    exit 2
    ;;
esac
if [[ "$(uname -s)" != 'Darwin' ]] || [[ "$(uname -m)" != "$TARGET_ARCH" ]]; then
  printf '[MACOS_RUNTIME_NATIVE_REQUIRED] 当前为 %s/%s，目标为 Darwin/%s。\n' \
    "$(uname -s)" "$(uname -m)" "$TARGET_ARCH" >&2
  exit 2
fi
if [[ -z "$OUTPUT_ROOT" ]]; then
  printf '%s\n' '[MACOS_RUNTIME_OUTPUT_REQUIRED] 必须指定运行时输出目录。' >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_ROOT="$(python3.11 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUTPUT_ROOT")"
BUILD_BASE="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/partyops-macos-runtimes.${TARGET_ARCH}.XXXXXX")"
INPUTS="$BUILD_BASE/inputs"
mkdir -p "$INPUTS" "$OUTPUT_ROOT"

cleanup() {
  if [[ -d "$BUILD_BASE" ]] && [[ "$BUILD_BASE" == *"/partyops-macos-runtimes.${TARGET_ARCH}."* ]]; then
    chmod -R u+w "$BUILD_BASE" 2>/dev/null || true
    find "$BUILD_BASE" -depth -delete 2>/dev/null || true
  fi
}
trap cleanup EXIT

for command in python3.11 cmake ninja clang clang++ curl tar shasum file otool strip sips; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf '[MACOS_RUNTIME_TOOL_MISSING] 缺少工具：%s\n' "$command" >&2
    exit 2
  fi
done

download_locked() {
  local name="$1" url="$2" expected="$3" path="$INPUTS/$1"
  curl --fail --location --silent --show-error --retry 1 \
    "$url" --output "$path.download"
  local actual
  actual="$(shasum -a 256 "$path.download" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf '[MACOS_RUNTIME_SOURCE_HASH_MISMATCH] %s：期望 %s，实际 %s。\n' \
      "$name" "$expected" "$actual" >&2
    exit 2
  fi
  mv "$path.download" "$path"
}

validate_archive() {
  local archive="$1" expected_root="$2"
  gzip -dc "$archive" | python3.11 "$ROOT/scripts/validate-portable-tar.py" \
    --expected-root "$expected_root" --allow-implicit-root \
    --max-members 100000 --max-bytes 2147483648
}

assert_thin_architecture() {
  local binary="$1" arches
  arches="$(/usr/bin/lipo -archs "$binary" 2>/dev/null || true)"
  if [[ "$arches" != "$TARGET_ARCH" ]]; then
    printf '[MACOS_RUNTIME_ARCH_MISMATCH] %s 架构为 %s，目标为 %s。\n' \
      "$binary" "${arches:-未知}" "$TARGET_ARCH" >&2
    exit 2
  fi
}

assert_system_dependencies_only() {
  local binary="$1" bad
  bad="$(otool -L "$binary" | awk 'NR > 1 {print $1}' | \
    grep -Ev '^(/usr/lib/|/System/Library/)' || true)"
  if [[ -n "$bad" ]]; then
    printf '[MACOS_RUNTIME_EXTERNAL_DEPENDENCY] %s 仍含外部依赖：\n%s\n' \
      "$binary" "$bad" >&2
    exit 2
  fi
}

TESSERACT_VERSION='5.5.3'
TESSERACT_SHA256='9218e62793116d42a9f6d14cd9348518b27f382096eea3d0f2d1a24616bb5884'
LEPTONICA_VERSION='1.87.0'
LEPTONICA_SHA256='c73363397f96eb1295602bf44d708a994ad42046c791bf03ea0505d829bdb6a7'
LIBJPEG_TURBO_VERSION='3.1.3'
LIBJPEG_TURBO_SHA256='075920b826834ac4ddf97661cc73491047855859affd671d52079c6867c1c6c0'
LIBTIFF_VERSION='4.7.1'
LIBTIFF_SHA256='f698d94f3103da8ca7438d84e0344e453fe0ba3b7486e04c5bf7a9a3fabe9b69'
LIBPNG_VERSION='1.6.58'
LIBPNG_SHA256='a9d4df463d36a6e5f9c29bd6f4967312d17e996c1854f3511f833924eb1993cf'
ZLIB_VERSION='1.3.2'
ZLIB_SHA256='bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16'
TESSDATA_COMMIT='87416418657359cb625c412a48b6e1d6d41c29bd'
CHI_SIM_SHA256='a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730'
ENG_SHA256='7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'

download_locked "tesseract-${TESSERACT_VERSION}.tar.gz" \
  "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/${TESSERACT_VERSION}.tar.gz" \
  "$TESSERACT_SHA256"
download_locked "leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "https://github.com/DanBloomberg/leptonica/releases/download/${LEPTONICA_VERSION}/leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "$LEPTONICA_SHA256"
download_locked "libjpeg-turbo-${LIBJPEG_TURBO_VERSION}.tar.gz" \
  "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/${LIBJPEG_TURBO_VERSION}/libjpeg-turbo-${LIBJPEG_TURBO_VERSION}.tar.gz" \
  "$LIBJPEG_TURBO_SHA256"
download_locked "tiff-${LIBTIFF_VERSION}.tar.gz" \
  "https://download.osgeo.org/libtiff/tiff-${LIBTIFF_VERSION}.tar.gz" \
  "$LIBTIFF_SHA256"
download_locked "libpng-${LIBPNG_VERSION}.tar.gz" \
  "https://github.com/pnggroup/libpng/archive/refs/tags/v${LIBPNG_VERSION}.tar.gz" \
  "$LIBPNG_SHA256"
download_locked "zlib-${ZLIB_VERSION}.tar.gz" \
  "https://zlib.net/zlib-${ZLIB_VERSION}.tar.gz" "$ZLIB_SHA256"
download_locked 'chi_sim.traineddata' \
  "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_COMMIT}/chi_sim.traineddata" \
  "$CHI_SIM_SHA256"
download_locked 'eng.traineddata' \
  "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_COMMIT}/eng.traineddata" \
  "$ENG_SHA256"

validate_archive "$INPUTS/tesseract-${TESSERACT_VERSION}.tar.gz" "tesseract-${TESSERACT_VERSION}"
validate_archive "$INPUTS/leptonica-${LEPTONICA_VERSION}.tar.gz" "leptonica-${LEPTONICA_VERSION}"
validate_archive "$INPUTS/libjpeg-turbo-${LIBJPEG_TURBO_VERSION}.tar.gz" "libjpeg-turbo-${LIBJPEG_TURBO_VERSION}"
validate_archive "$INPUTS/tiff-${LIBTIFF_VERSION}.tar.gz" "tiff-${LIBTIFF_VERSION}"
validate_archive "$INPUTS/libpng-${LIBPNG_VERSION}.tar.gz" "libpng-${LIBPNG_VERSION}"
validate_archive "$INPUTS/zlib-${ZLIB_VERSION}.tar.gz" "zlib-${ZLIB_VERSION}"

OCR_BUILD="$BUILD_BASE/ocr"
mkdir -p "$OCR_BUILD"
for archive in \
  "tesseract-${TESSERACT_VERSION}.tar.gz" \
  "leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "libjpeg-turbo-${LIBJPEG_TURBO_VERSION}.tar.gz" \
  "tiff-${LIBTIFF_VERSION}.tar.gz" \
  "libpng-${LIBPNG_VERSION}.tar.gz" \
  "zlib-${ZLIB_VERSION}.tar.gz"; do
  tar -xzf "$INPUTS/$archive" -C "$OCR_BUILD" --no-same-owner --no-same-permissions
done

PREFIX="$OCR_BUILD/prefix"
export PARTYOPS_OCR_PREFIX="$PREFIX"
COMMON_FLAGS=(
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_INSTALL_PREFIX="$PREFIX"
  -DCMAKE_OSX_ARCHITECTURES="$TARGET_ARCH"
  -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  -DCMAKE_MACOSX_RPATH=OFF
  -DCMAKE_C_COMPILER="$(command -v clang)"
  -DCMAKE_CXX_COMPILER="$(command -v clang++)"
)
JOBS="${PARTYOPS_BUILD_JOBS:-3}"

cmake -S "$OCR_BUILD/zlib-${ZLIB_VERSION}" -B "$OCR_BUILD/zlib-build" \
  "${COMMON_FLAGS[@]}" -DZLIB_BUILD_SHARED=OFF -DZLIB_BUILD_TESTING=OFF
cmake --build "$OCR_BUILD/zlib-build" -j "$JOBS"
cmake --install "$OCR_BUILD/zlib-build"

cmake -S "$OCR_BUILD/libpng-${LIBPNG_VERSION}" -B "$OCR_BUILD/libpng-build" \
  "${COMMON_FLAGS[@]}" -DZLIB_ROOT="$PREFIX" -DPNG_SHARED=OFF \
  -DPNG_STATIC=ON -DPNG_FRAMEWORK=OFF -DPNG_TESTS=OFF -DPNG_TOOLS=OFF
cmake --build "$OCR_BUILD/libpng-build" -j "$JOBS"
cmake --install "$OCR_BUILD/libpng-build"

cmake -S "$OCR_BUILD/libjpeg-turbo-${LIBJPEG_TURBO_VERSION}" \
  -B "$OCR_BUILD/libjpeg-turbo-build" \
  "${COMMON_FLAGS[@]}" -DENABLE_SHARED=OFF -DENABLE_STATIC=ON \
  -DWITH_JPEG8=ON -DWITH_TURBOJPEG=OFF -DWITH_TOOLS=OFF \
  -DWITH_TESTS=OFF -DWITH_SIMD=OFF
cmake --build "$OCR_BUILD/libjpeg-turbo-build" -j "$JOBS"
cmake --install "$OCR_BUILD/libjpeg-turbo-build"

# Intel runner 的 /usr/local 预装了 JPEG v8 头文件；libtiff 本身也会直接
# 包含 jpeglib.h。除了在 libtiff 配置前锁定查找路径，还把自带静态
# libjpeg-turbo 编译为 v8 ABI：即使上游 CMake 把系统头文件排在前面，调用方
# 与最终静态库的 ABI 仍保持一致，避免“caller expects 80, library is 62”。
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
LOCKED_OCR_BASE_FIND_FLAGS=(
  -DCMAKE_PREFIX_PATH="$PREFIX"
  -DCMAKE_IGNORE_PREFIX_PATH='/usr/local;/opt/homebrew'
  -DCMAKE_IGNORE_PATH='/usr/local/include;/usr/local/lib;/opt/homebrew/include;/opt/homebrew/lib'
  -DZLIB_ROOT="$PREFIX"
  -DZLIB_LIBRARY_RELEASE="$PREFIX/lib/libz.a"
  -DZLIB_LIBRARY="$PREFIX/lib/libz.a"
  -DZLIB_INCLUDE_DIR="$PREFIX/include"
  -DPNG_PNG_INCLUDE_DIR="$PREFIX/include"
  -DPNG_LIBRARY_RELEASE="$PREFIX/lib/libpng16.a"
  -DPNG_LIBRARY="$PREFIX/lib/libpng16.a"
  -DJPEG_INCLUDE_DIR="$PREFIX/include"
  -DJPEG_LIBRARY_RELEASE="$PREFIX/lib/libjpeg.a"
  -DJPEG_LIBRARY="$PREFIX/lib/libjpeg.a"
)

# Leptonica 内置字库和用户常见扫描件都会经过 TIFF/JPEG 解码。必须把
# 两个解码器静态收入 tesseract；仅支持 PNG 会令 --list-langs 本身出现
# pixReadMemTiff 错误，也会使 JPG/TIFF 公文扫描件在用户电脑上无法识别。
cmake -S "$OCR_BUILD/tiff-${LIBTIFF_VERSION}" -B "$OCR_BUILD/libtiff-build" \
  "${COMMON_FLAGS[@]}" "${LOCKED_OCR_BASE_FIND_FLAGS[@]}" -DBUILD_SHARED_LIBS=OFF \
  -Dtiff-static=ON -Dtiff-tools=OFF -Dtiff-tests=OFF -Dtiff-contrib=OFF -Dtiff-docs=OFF \
  -Djpeg=ON -Dzlib=ON -Dlibdeflate=OFF -Dlzma=OFF -Djbig=OFF \
  -Dwebp=OFF -Dzstd=OFF
cmake --build "$OCR_BUILD/libtiff-build" -j "$JOBS"
cmake --install "$OCR_BUILD/libtiff-build"

LOCKED_OCR_FIND_FLAGS=(
  "${LOCKED_OCR_BASE_FIND_FLAGS[@]}"
  -DTIFF_INCLUDE_DIR="$PREFIX/include"
  -DTIFF_LIBRARY_RELEASE="$PREFIX/lib/libtiff.a"
  -DTIFF_LIBRARY="$PREFIX/lib/libtiff.a"
)

cmake -S "$OCR_BUILD/leptonica-${LEPTONICA_VERSION}" -B "$OCR_BUILD/leptonica-build" \
  "${COMMON_FLAGS[@]}" "${LOCKED_OCR_FIND_FLAGS[@]}" \
  -DCMAKE_PROJECT_INCLUDE_BEFORE="$SCRIPT_DIR/ocr-static-targets.cmake" \
  -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_PROG=OFF -DSW_BUILD=OFF -DENABLE_ZLIB=ON -DENABLE_PNG=ON \
  -DENABLE_GIF=OFF -DENABLE_JPEG=ON -DENABLE_TIFF=ON \
  -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF -DSTRICT_CONF=ON
cmake --build "$OCR_BUILD/leptonica-build" -j "$JOBS"
cmake --install "$OCR_BUILD/leptonica-build"

cmake -S "$OCR_BUILD/tesseract-${TESSERACT_VERSION}" -B "$OCR_BUILD/tesseract-build" \
  "${COMMON_FLAGS[@]}" "${LOCKED_OCR_FIND_FLAGS[@]}" \
  -DCMAKE_PROJECT_INCLUDE_BEFORE="$SCRIPT_DIR/ocr-static-targets.cmake" \
  -DBUILD_SHARED_LIBS=OFF \
  -DOPENMP_BUILD=OFF -DGRAPHICS_DISABLED=ON -DDISABLED_LEGACY_ENGINE=ON \
  -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF -DDISABLE_TIFF=OFF \
  -DDISABLE_ARCHIVE=ON -DDISABLE_CURL=ON -DENABLE_NATIVE=OFF \
  -DENABLE_LTO=OFF -DENABLE_CCACHE=OFF -DINSTALL_CONFIGS=OFF \
  -DCMAKE_EXE_LINKER_FLAGS='-Wl,-dead_strip'
cmake --build "$OCR_BUILD/tesseract-build" --target tesseract -j "$JOBS"

OCR_RUNTIME="$OUTPUT_ROOT/ocr-$TARGET_ARCH"
mkdir -p "$OCR_RUNTIME/bin" "$OCR_RUNTIME/tessdata" "$OCR_RUNTIME/licenses"
cp "$OCR_BUILD/tesseract-build/bin/tesseract" "$OCR_RUNTIME/bin/tesseract"
cp "$INPUTS/chi_sim.traineddata" "$INPUTS/eng.traineddata" "$OCR_RUNTIME/tessdata/"
cp "$OCR_BUILD/tesseract-${TESSERACT_VERSION}/LICENSE" "$OCR_RUNTIME/licenses/tesseract-LICENSE"
cp "$OCR_BUILD/leptonica-${LEPTONICA_VERSION}/leptonica-license.txt" "$OCR_RUNTIME/licenses/leptonica-LICENSE"
cp "$OCR_BUILD/libjpeg-turbo-${LIBJPEG_TURBO_VERSION}/LICENSE.md" \
  "$OCR_RUNTIME/licenses/libjpeg-turbo-LICENSE.md"
cp "$OCR_BUILD/tiff-${LIBTIFF_VERSION}/LICENSE.md" \
  "$OCR_RUNTIME/licenses/libtiff-LICENSE.md"
strip -x "$OCR_RUNTIME/bin/tesseract"
chmod 0755 "$OCR_RUNTIME/bin/tesseract"
assert_thin_architecture "$OCR_RUNTIME/bin/tesseract"
assert_system_dependencies_only "$OCR_RUNTIME/bin/tesseract"
OCR_PROBE_STDERR="$BUILD_BASE/tesseract-probe.stderr"
TESSDATA_PREFIX="$OCR_RUNTIME/tessdata" \
  "$OCR_RUNTIME/bin/tesseract" --list-langs 2>"$OCR_PROBE_STDERR" | grep -qx 'chi_sim'
if grep -Eq 'Error in pixReadMem(Tiff)?|function not present' "$OCR_PROBE_STDERR"; then
  printf '%s\n' '[MACOS_OCR_DECODER_INCOMPLETE] OCR 运行时缺少内置字库需要的 TIFF 解码能力。' >&2
  cat "$OCR_PROBE_STDERR" >&2
  exit 2
fi
# 使用仓库真实图标生成 JPEG/TIFF 探针，验证静态解码器确实可在没有
# Homebrew 的独立运行时中工作；识别结果可为空，但解码与推理必须成功。
for image_format in jpeg tiff; do
  probe_image="$BUILD_BASE/ocr-probe.$image_format"
  sips -s format "$image_format" "$ROOT/packaging/windows/partyops-1024.png" \
    --out "$probe_image" >/dev/null
  TESSDATA_PREFIX="$OCR_RUNTIME/tessdata" \
    "$OCR_RUNTIME/bin/tesseract" "$probe_image" stdout -l eng \
    >/dev/null 2>"$BUILD_BASE/tesseract-$image_format.stderr" || {
      printf '[MACOS_OCR_FORMAT_SELFTEST_FAILED] OCR 无法读取 %s 探针。\n' "$image_format" >&2
      cat "$BUILD_BASE/tesseract-$image_format.stderr" >&2
      exit 2
    }
done

LLAMA_TAG='b10331'
LLAMA_SHA256='73bfa7e5b56a818db7c9b3de5ab1156095eee6063efbb68d338c6a197ddac584'
download_locked "llama.cpp-${LLAMA_TAG}.tar.gz" \
  "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${LLAMA_TAG}.tar.gz" \
  "$LLAMA_SHA256"
validate_archive "$INPUTS/llama.cpp-${LLAMA_TAG}.tar.gz" "llama.cpp-${LLAMA_TAG}"

LLAMA_BUILD="$BUILD_BASE/llama"
mkdir -p "$LLAMA_BUILD"
tar -xzf "$INPUTS/llama.cpp-${LLAMA_TAG}.tar.gz" -C "$LLAMA_BUILD" \
  --no-same-owner --no-same-permissions
LLAMA_SOURCE="$LLAMA_BUILD/llama.cpp-${LLAMA_TAG}"
cmake -S "$LLAMA_SOURCE" -B "$LLAMA_BUILD/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES="$TARGET_ARCH" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
  -DCMAKE_C_COMPILER="$(command -v clang)" \
  -DCMAKE_CXX_COMPILER="$(command -v clang++)" \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_NATIVE=OFF \
  -DGGML_OPENMP=OFF \
  -DGGML_BLAS=OFF \
  -DGGML_METAL=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DLLAMA_OPENSSL=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DLLAMA_USE_PREBUILT_UI=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_APP=OFF \
  -DLLAMA_BUILD_SERVER=ON
cmake --build "$LLAMA_BUILD/build" --target llama-server -j "$JOBS"

LLAMA_RUNTIME="$OUTPUT_ROOT/llama-$TARGET_ARCH"
mkdir -p "$LLAMA_RUNTIME/licenses"
cp "$LLAMA_BUILD/build/bin/llama-server" "$LLAMA_RUNTIME/llama-server"
cp "$LLAMA_SOURCE/LICENSE" "$LLAMA_RUNTIME/licenses/llama.cpp-LICENSE"
strip -x "$LLAMA_RUNTIME/llama-server"
chmod 0755 "$LLAMA_RUNTIME/llama-server"
assert_thin_architecture "$LLAMA_RUNTIME/llama-server"
assert_system_dependencies_only "$LLAMA_RUNTIME/llama-server"
python3 - "$LLAMA_RUNTIME/llama-server" <<'PY'
import subprocess
import sys

subprocess.run(
    [sys.argv[1], "--version"],
    check=True,
    stdout=subprocess.DEVNULL,
    timeout=120,
)
PY

printf 'macOS %s 原生 OCR 与 llama.cpp 运行时已生成：%s\n' \
  "$TARGET_ARCH" "$OUTPUT_ROOT"
