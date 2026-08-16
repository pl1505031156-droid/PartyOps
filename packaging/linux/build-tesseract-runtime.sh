#!/usr/bin/env bash
set -euo pipefail

# 从已固定的官方源码构建 glibc 2.17 双架构 OCR 运行时。
# 不允许把构建机上过时的 Tesseract/图像库直接复制进制品。

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCH="${PARTYOPS_BUILD_ARCH:-}"
TESSERACT_VERSION=5.5.3
TESSERACT_COMMIT=db0ec62f81b0737fbbe184d8fea40af5738f8eef
TESSERACT_SHA256=9218e62793116d42a9f6d14cd9348518b27f382096eea3d0f2d1a24616bb5884
LEPTONICA_VERSION=1.87.0
LEPTONICA_COMMIT=13275a278eb55b5746e33f95fbf5a2c8f604b3ab
LEPTONICA_SHA256=c73363397f96eb1295602bf44d708a994ad42046c791bf03ea0505d829bdb6a7
LIBPNG_VERSION=1.6.58
LIBPNG_COMMIT=3061454d980de7d53608f594194cfac722721d2a
LIBPNG_SHA256=a9d4df463d36a6e5f9c29bd6f4967312d17e996c1854f3511f833924eb1993cf
ZLIB_VERSION=1.3.2
ZLIB_SHA256=bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16
TESSDATA_COMMIT=87416418657359cb625c412a48b6e1d6d41c29bd
CHI_SIM_SHA256=a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730
ENG_SHA256=7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2
SOURCE_DATE_EPOCH=1784917980

if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "不支持的 OCR 构建架构：$(uname -m)" >&2; exit 2 ;;
  esac
fi
[[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || {
  echo "OCR 运行时仅支持 amd64/arm64：$ARCH" >&2
  exit 2
}
EXPECTED_MACHINE=x86_64
EXPECTED_FILE_PATTERN=x86-64
if [[ "$ARCH" == arm64 ]]; then
  EXPECTED_MACHINE=aarch64
  EXPECTED_FILE_PATTERN="ARM aarch64"
fi
[[ "$(uname -s)" == Linux && "$(uname -m)" == "$EXPECTED_MACHINE" ]] || {
  echo "必须在 $EXPECTED_MACHINE 原生或指令仿真环境构建 OCR。" >&2
  exit 2
}
GLIBC_VERSION="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
[[ "$GLIBC_VERSION" == 2.17 ]] || {
  echo "OCR 正式运行时必须以 glibc 2.17 构建；当前为 $GLIBC_VERSION。" >&2
  exit 2
}

INPUTS="${PARTYOPS_OCR_INPUT_DIR:-$ROOT/.build-linux/inputs}"
BUILD_BASE="${PARTYOPS_OCR_BUILD_BASE:-$ROOT/.build-linux}"
OUTPUT="${PARTYOPS_OCR_OUTPUT:-$ROOT/artifacts/tooling/tesseract-runtime-${ARCH}-${TESSERACT_VERSION}.tar.gz}"
mkdir -p "$INPUTS" "$BUILD_BASE" "$(dirname "$OUTPUT")"

CMAKE_BIN="${CMAKE_BIN:-$(command -v cmake || true)}"
NINJA_BIN="${NINJA_BIN:-$(command -v ninja || true)}"
CC_BIN="${CC:-$(command -v gcc || true)}"
CXX_BIN="${CXX:-$(command -v g++ || true)}"
STRIP_BIN="${STRIP:-$(command -v strip || true)}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.11 || command -v python3 || true)}"
for tool in "$CMAKE_BIN" "$NINJA_BIN" "$CC_BIN" "$CXX_BIN" "$STRIP_BIN" "$PYTHON_BIN"; do
  [[ -n "$tool" && -x "$tool" ]] || {
    echo "缺少 OCR 可重复构建工具：${tool:-未找到}" >&2
    exit 2
  }
done

download_locked() {
  local name="$1" url="$2" expected="$3"
  local path="$INPUTS/$name"
  if [[ ! -f "$path" ]]; then
    local incoming="${path}.download"
    curl --fail --location --silent --show-error --retry 1 "$url" --output "$incoming"
    [[ "$(sha256sum "$incoming" | awk '{print $1}')" == "$expected" ]] || {
      echo "OCR 输入 SHA-256 不匹配：$name" >&2
      exit 2
    }
    mv "$incoming" "$path"
  fi
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]] || {
    echo "OCR 输入 SHA-256 不匹配：$path" >&2
    exit 2
  }
}

download_locked "tesseract-${TESSERACT_VERSION}.tar.gz" \
  "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/${TESSERACT_VERSION}.tar.gz" \
  "$TESSERACT_SHA256"
download_locked "leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "https://github.com/DanBloomberg/leptonica/releases/download/${LEPTONICA_VERSION}/leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "$LEPTONICA_SHA256"
download_locked "libpng-${LIBPNG_VERSION}.tar.gz" \
  "https://github.com/pnggroup/libpng/archive/refs/tags/v${LIBPNG_VERSION}.tar.gz" \
  "$LIBPNG_SHA256"
download_locked "zlib-${ZLIB_VERSION}.tar.gz" \
  "https://zlib.net/zlib-${ZLIB_VERSION}.tar.gz" "$ZLIB_SHA256"
download_locked chi_sim.traineddata \
  "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_COMMIT}/chi_sim.traineddata" \
  "$CHI_SIM_SHA256"
download_locked eng.traineddata \
  "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_COMMIT}/eng.traineddata" \
  "$ENG_SHA256"

for archive_and_root in \
  "tesseract-${TESSERACT_VERSION}.tar.gz:tesseract-${TESSERACT_VERSION}" \
  "leptonica-${LEPTONICA_VERSION}.tar.gz:leptonica-${LEPTONICA_VERSION}" \
  "libpng-${LIBPNG_VERSION}.tar.gz:libpng-${LIBPNG_VERSION}" \
  "zlib-${ZLIB_VERSION}.tar.gz:zlib-${ZLIB_VERSION}"; do
  archive="${archive_and_root%%:*}"
  expected_root="${archive_and_root#*:}"
  gzip -dc "$INPUTS/$archive" |
    "$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py" \
      --expected-root "$expected_root" --allow-implicit-root \
      --max-members 100000 --max-bytes 2147483648
done

BUILD="$(mktemp -d "$BUILD_BASE/tesseract-runtime.XXXXXX")"
ARCHIVE_STAGE=""
cleanup() {
  status=$?
  trap - EXIT
  if [[ -n "$ARCHIVE_STAGE" ]]; then
    case "$ARCHIVE_STAGE" in
      /tmp/partyops-ocr-archive.*) rm -rf -- "$ARCHIVE_STAGE" ;;
      *) echo "拒绝清理异常 OCR 归档目录：$ARCHIVE_STAGE" >&2 ;;
    esac
  fi
  case "$BUILD" in
    "$BUILD_BASE/tesseract-runtime."*) rm -rf -- "$BUILD" ;;
    *) echo "拒绝清理异常 OCR 构建目录：$BUILD" >&2 ;;
  esac
  exit "$status"
}
trap cleanup EXIT
for archive in \
  "tesseract-${TESSERACT_VERSION}.tar.gz" \
  "leptonica-${LEPTONICA_VERSION}.tar.gz" \
  "libpng-${LIBPNG_VERSION}.tar.gz" \
  "zlib-${ZLIB_VERSION}.tar.gz"; do
  tar -xzf "$INPUTS/$archive" -C "$BUILD" --no-same-owner --no-same-permissions
done

PREFIX="$BUILD/prefix"
COMMON_FLAGS=(
  -G Ninja
  -DCMAKE_MAKE_PROGRAM="$NINJA_BIN"
  -DCMAKE_C_COMPILER="$CC_BIN"
  -DCMAKE_CXX_COMPILER="$CXX_BIN"
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_INSTALL_PREFIX="$PREFIX"
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
)
JOBS="${PARTYOPS_BUILD_JOBS:-2}"

"$CMAKE_BIN" -S "$BUILD/zlib-${ZLIB_VERSION}" -B "$BUILD/zlib-build" \
  "${COMMON_FLAGS[@]}" -DZLIB_BUILD_SHARED=OFF -DZLIB_BUILD_TESTING=OFF
"$CMAKE_BIN" --build "$BUILD/zlib-build" -j "$JOBS"
"$CMAKE_BIN" --install "$BUILD/zlib-build"

"$CMAKE_BIN" -S "$BUILD/libpng-${LIBPNG_VERSION}" -B "$BUILD/libpng-build" \
  "${COMMON_FLAGS[@]}" -DZLIB_ROOT="$PREFIX" -DPNG_SHARED=OFF \
  -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF \
  -DPNG_HARDWARE_OPTIMIZATIONS=OFF
"$CMAKE_BIN" --build "$BUILD/libpng-build" -j "$JOBS"
"$CMAKE_BIN" --install "$BUILD/libpng-build"

"$CMAKE_BIN" -S "$BUILD/leptonica-${LEPTONICA_VERSION}" -B "$BUILD/leptonica-build" \
  "${COMMON_FLAGS[@]}" -DCMAKE_PREFIX_PATH="$PREFIX" -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_PROG=OFF -DSW_BUILD=OFF -DENABLE_ZLIB=ON -DENABLE_PNG=ON \
  -DENABLE_GIF=OFF -DENABLE_JPEG=OFF -DENABLE_TIFF=OFF \
  -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF
"$CMAKE_BIN" --build "$BUILD/leptonica-build" -j "$JOBS"
"$CMAKE_BIN" --install "$BUILD/leptonica-build"

"$CMAKE_BIN" -S "$BUILD/tesseract-${TESSERACT_VERSION}" -B "$BUILD/tesseract-build" \
  "${COMMON_FLAGS[@]}" -DCMAKE_PREFIX_PATH="$PREFIX" -DBUILD_SHARED_LIBS=OFF \
  -DOPENMP_BUILD=OFF -DGRAPHICS_DISABLED=ON -DDISABLED_LEGACY_ENGINE=ON \
  -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF -DDISABLE_TIFF=ON \
  -DDISABLE_ARCHIVE=ON -DDISABLE_CURL=ON -DENABLE_NATIVE=OFF \
  -DENABLE_LTO=OFF -DENABLE_CCACHE=OFF -DINSTALL_CONFIGS=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now"
"$CMAKE_BIN" --build "$BUILD/tesseract-build" --target tesseract -j "$JOBS"

RUNTIME="$BUILD/runtime/tesseract-${TESSERACT_VERSION}"
mkdir -p "$RUNTIME/bin" "$RUNTIME/tessdata" "$RUNTIME/licenses"
cp "$BUILD/tesseract-build/bin/tesseract" "$RUNTIME/bin/tesseract"
cp "$INPUTS/chi_sim.traineddata" "$INPUTS/eng.traineddata" "$RUNTIME/tessdata/"
cp "$BUILD/tesseract-${TESSERACT_VERSION}/LICENSE" "$RUNTIME/licenses/tesseract-LICENSE"
cp "$BUILD/leptonica-${LEPTONICA_VERSION}/leptonica-license.txt" \
  "$RUNTIME/licenses/leptonica-LICENSE"
cp "$BUILD/libpng-${LIBPNG_VERSION}/LICENSE" "$RUNTIME/licenses/libpng-LICENSE"
cp "$BUILD/zlib-${ZLIB_VERSION}/LICENSE" "$RUNTIME/licenses/zlib-LICENSE"
"$STRIP_BIN" --strip-unneeded "$RUNTIME/bin/tesseract"
chmod 0755 "$RUNTIME/bin/tesseract"

file "$RUNTIME/bin/tesseract" | grep -q "$EXPECTED_FILE_PATTERN" || {
  echo "Tesseract ELF 架构与目标 $ARCH 不一致。" >&2
  exit 2
}
if ldd "$RUNTIME/bin/tesseract" 2>&1 | grep -q 'not found'; then
  echo "Tesseract 存在缺失的动态库依赖。" >&2
  ldd "$RUNTIME/bin/tesseract" >&2 || true
  exit 2
fi
if ldd "$RUNTIME/bin/tesseract" | grep -Eq 'lib(tesseract|lept|png|z|stdc\+\+|gcc_s)\.so'; then
  echo "Tesseract 未静态封入 OCR/图像/C++ 运行库，拒绝发布。" >&2
  ldd "$RUNTIME/bin/tesseract" >&2 || true
  exit 2
fi
MAX_GLIBC="$(readelf --version-info "$RUNTIME/bin/tesseract" |
  grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1)"
if [[ -z "$MAX_GLIBC" ]] ||
  [[ "$(printf '%s\n' "$MAX_GLIBC" GLIBC_2.17 | sort -V | tail -1)" != GLIBC_2.17 ]]; then
  echo "Tesseract 最低 glibc 基线超出 2.17：${MAX_GLIBC:-未知}" >&2
  exit 2
fi
VERSION_OUTPUT="$(TESSDATA_PREFIX="$RUNTIME/tessdata" "$RUNTIME/bin/tesseract" --version 2>&1)"
grep -q "tesseract ${TESSERACT_VERSION}" <<<"$VERSION_OUTPUT" || {
  echo "Tesseract 版本与锁定版本不一致。" >&2
  exit 2
}
LANGS="$(TESSDATA_PREFIX="$RUNTIME/tessdata" "$RUNTIME/bin/tesseract" --list-langs 2>&1)"
grep -qx chi_sim <<<"$LANGS" && grep -qx eng <<<"$LANGS" || {
  echo "Tesseract 中英文离线语言包未完整加载。" >&2
  exit 2
}

# 仅列出语言包不能证明 OCR 引擎真的可用。生成不依赖系统字体的 PGM
# 测试图并执行一次识别，覆盖图像解码、LSTM 模型加载与文本输出链路。
OCR_SMOKE_IMAGE="$BUILD/ocr-smoke.pgm"
"$PYTHON_BIN" - "$OCR_SMOKE_IMAGE" <<'PY'
from pathlib import Path
import sys

patterns = {
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "S": ("11111", "10000", "10000", "11111", "00001", "00001", "11111"),
}
text = "TEST"
scale = 12
margin = 24
width = margin * 2 + (5 * scale + scale) * len(text)
height = margin * 2 + 7 * scale
pixels = bytearray([255]) * (width * height)
for index, char in enumerate(text):
    x0 = margin + index * (6 * scale)
    for row, bits in enumerate(patterns[char]):
        for col, bit in enumerate(bits):
            if bit == "1":
                for dy in range(scale):
                    start = (margin + row * scale + dy) * width + x0 + col * scale
                    pixels[start : start + scale] = b"\x00" * scale
Path(sys.argv[1]).write_bytes(
    f"P5\n{width} {height}\n255\n".encode("ascii") + pixels
)
PY
OCR_SMOKE_OUTPUT="$(TESSDATA_PREFIX="$RUNTIME/tessdata" \
  "$RUNTIME/bin/tesseract" "$OCR_SMOKE_IMAGE" stdout -l eng --psm 7 \
  2>/dev/null | tr -d '[:space:]')"
[[ "$OCR_SMOKE_OUTPUT" == TEST ]] || {
  echo "Tesseract 识别链路自检失败：${OCR_SMOKE_OUTPUT:-无输出}" >&2
  exit 2
}

cat >"$RUNTIME/licenses/SOURCE.json" <<EOF
{
  "architecture": "$ARCH",
  "glibc_baseline": "2.17",
  "tesseract": {"version": "$TESSERACT_VERSION", "commit": "$TESSERACT_COMMIT", "source_sha256": "$TESSERACT_SHA256"},
  "leptonica": {"version": "$LEPTONICA_VERSION", "commit": "$LEPTONICA_COMMIT", "source_sha256": "$LEPTONICA_SHA256"},
  "libpng": {"version": "$LIBPNG_VERSION", "commit": "$LIBPNG_COMMIT", "source_sha256": "$LIBPNG_SHA256"},
  "zlib": {"version": "$ZLIB_VERSION", "source_sha256": "$ZLIB_SHA256"},
  "tessdata_fast": {"commit": "$TESSDATA_COMMIT", "chi_sim_sha256": "$CHI_SIM_SHA256", "eng_sha256": "$ENG_SHA256"},
  "compiler": "$($CXX_BIN --version | head -1)",
  "features": ["LSTM", "PNG", "PNM"],
  "disabled": ["legacy-engine", "training-tools", "curl", "libarchive", "TIFF", "JPEG", "WebP", "OpenJPEG", "OpenMP", "native-cpu-optimizations"]
}
EOF

mkdir -p "$(dirname "$OUTPUT")"
# Windows 的 DrvFs 挂载可能把全部构建文件呈现为 0777。先复制到 Linux
# 原生临时目录并逐类收紧权限，再生成归档，避免可写/可执行的许可文件
# 混入供应链输入。
ARCHIVE_STAGE="$(mktemp -d /tmp/partyops-ocr-archive.XXXXXX)"
cp -a "$RUNTIME" "$ARCHIVE_STAGE/"
find "$ARCHIVE_STAGE/tesseract-${TESSERACT_VERSION}" -type d -exec chmod 0755 {} +
find "$ARCHIVE_STAGE/tesseract-${TESSERACT_VERSION}" -type f -exec chmod 0644 {} +
chmod 0755 "$ARCHIVE_STAGE/tesseract-${TESSERACT_VERSION}/bin/tesseract"
tar --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
  -czf "$OUTPUT" -C "$ARCHIVE_STAGE" "tesseract-${TESSERACT_VERSION}"
sha256sum "$OUTPUT" >"$OUTPUT.sha256"
echo "$VERSION_OUTPUT"
echo "Tesseract OCR 识别链路自检通过：$OCR_SMOKE_OUTPUT"
echo "Tesseract $ARCH glibc 2.17 运行时已生成：$OUTPUT"
