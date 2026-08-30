#!/bin/bash
set -euo pipefail
umask 077

VERSION='1.4.5-rc.6'
PACKAGE_VERSION='1.4.5.6'
MODE='release'
TARGET_ARCH=''
while (($#)); do
  case "$1" in
    --architecture)
      TARGET_ARCH="${2:-}"
      shift 2
      ;;
    --unsigned-development)
      MODE='unsigned-development'
      shift
      ;;
    --unsigned-candidate)
      MODE='unsigned-candidate'
      shift
      ;;
    *)
      printf '未知参数：%s\n' "$1" >&2
      exit 2
      ;;
  esac
done

case "$TARGET_ARCH" in
  arm64) RELEASE_ARCH='arm64' ;;
  x86_64|amd64) TARGET_ARCH='x86_64'; RELEASE_ARCH='x86_64' ;;
  *)
    printf '%s\n' '用法：build-pkg.sh --architecture arm64|x86_64 [--unsigned-development|--unsigned-candidate]' >&2
    exit 2
    ;;
esac
if [[ "$(uname -s)" != 'Darwin' ]]; then
  printf '%s\n' '[MACOS_NATIVE_BUILD_REQUIRED] macOS PKG 必须在真实 macOS 上构建。' >&2
  exit 2
fi
if [[ "$(uname -m)" != "$TARGET_ARCH" ]]; then
  printf '[MACOS_BUILD_ARCH_MISMATCH] 构建机为 %s，目标为 %s；拒绝混用 Rosetta 依赖。\n' \
    "$(uname -m)" "$TARGET_ARCH" >&2
  exit 2
fi

# 所有从源码构建的 Python 扩展和 PyInstaller bootloader 均必须继承相同
# 的系统基线；只给最后一层 C 启动器传 -mmacosx-version-min 不足以保证
# 用户电脑能装且能启动。
export MACOSX_DEPLOYMENT_TARGET='11.0'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$ROOT/artifacts/release-$VERSION-final"
python3.11 "$ROOT/scripts/verify-full-function-gate.py" verify --root "$ROOT" --scope package
OCR_RUNTIME="${PARTYOPS_MACOS_OCR_RUNTIME:-}"
LLAMA_RUNTIME="${PARTYOPS_MACOS_LLAMA_RUNTIME:-}"
OFFICE_RUNTIME="${PARTYOPS_MACOS_OFFICE_RUNTIME:-}"
for command in python3.11 uv node corepack sips iconutil pkgbuild pkgutil spctl xcrun \
  curl ditto gzip tar shasum file otool codesign make perl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf '[MACOS_BUILD_TOOL_MISSING] 缺少构建工具：%s\n' "$command" >&2
    exit 2
  fi
done
if [[ ! -d "$OCR_RUNTIME" ]] || [[ ! -f "$OCR_RUNTIME/bin/tesseract" ]] ||
  [[ ! -f "$OCR_RUNTIME/tessdata/chi_sim.traineddata" ]]; then
  printf '%s\n' '[MACOS_OCR_RUNTIME_MISSING] 请提供当前架构、可审计的 OCR 运行时目录。' >&2
  exit 2
fi
if [[ ! -d "$LLAMA_RUNTIME" ]] || [[ ! -f "$LLAMA_RUNTIME/llama-server" ]]; then
  printf '%s\n' '[MACOS_LLM_RUNTIME_MISSING] 请提供当前架构、可审计的 llama.cpp 运行时目录。' >&2
  exit 2
fi
if [[ ! -x "$OFFICE_RUNTIME/program/soffice" ]] ||
  [[ ! -f "$OFFICE_RUNTIME/SOURCE.json" ]] ||
  [[ ! -d "$OFFICE_RUNTIME/licenses" ]]; then
  printf '%s\n' '[MACOS_OFFICE_RUNTIME_MISSING] 请提供当前架构、包含来源清单和许可证的 LibreOffice headless 运行时。' >&2
  exit 2
fi
OFFICE_RUNTIME="$(cd "$OFFICE_RUNTIME" && pwd -P)"
while IFS= read -r -d '' link; do
  resolved="$(python3.11 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$link")"
  case "$resolved" in
    "$OFFICE_RUNTIME"/*) ;;
    *)
      printf '[MACOS_OFFICE_RUNTIME_SYMLINK_INVALID] LibreOffice 运行时包含越界或损坏链接：%s\n' "$link" >&2
      exit 2
      ;;
  esac
done < <(/usr/bin/find "$OFFICE_RUNTIME" -type l -print0)
for binary in "$OCR_RUNTIME/bin/tesseract" "$LLAMA_RUNTIME/llama-server"; do
  description="$(file -b "$binary")"
  if [[ "$description" != *Mach-O* ]] || [[ "$description" != *"$TARGET_ARCH"* ]]; then
    printf '[MACOS_NATIVE_RUNTIME_ARCH_MISMATCH] %s 不是 %s Mach-O。\n' "$binary" "$TARGET_ARCH" >&2
    exit 2
  fi
done
office_description="$(file -b "$OFFICE_RUNTIME/program/soffice")"
if [[ "$office_description" != *Mach-O* ]] || [[ "$office_description" != *"$TARGET_ARCH"* ]]; then
  printf '[MACOS_OFFICE_RUNTIME_ARCH_MISMATCH] soffice 不是 %s Mach-O。\n' "$TARGET_ARCH" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"
# 对每个嵌套 Mach-O 和代码目录逐层签名，再签主 App。--deep 只用于最终
# 验证，不能代替由内向外签名，否则 Python.framework 与主 App 可能出现
# 不同 Team ID，Finder 会在映射运行时前直接拒绝加载。
sign_bundle_code() {
  local identity="$1"; shift
  local timestamp_args=("$@")
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    codesign --force "${timestamp_args[@]}" --options runtime --sign "$identity" "$candidate"
  # 调用方在签名阶段先生成候选清单，避免把根可执行文件和嵌套入口
  # 混在同一次签名中；清单本身也作为制品审计证据留在构建临时目录。
  done <"$MACHO_CANDIDATE_LIST"
  while IFS= read -r -d '' bundle; do
    [[ "$bundle" == "$APP" ]] && continue
    codesign --force "${timestamp_args[@]}" --options runtime --sign "$identity" "$bundle"
  done < <(/usr/bin/find "$APP/Contents" -depth \( -name '*.framework' -o -name '*.app' -o -name '*.xpc' -o -name '*.bundle' -o -name '*.plugin' \) -type d -print0)
}

verify_team_ids() {
  local mode="$1" expected=''
  if [[ "$mode" == 'release' ]]; then
    expected="$(codesign --display --verbose=4 "$APP" 2>&1 | awk -F= '/TeamIdentifier=/{print $2; exit}')"
    [[ -n "$expected" && "$expected" != 'not set' ]] || { printf '%s\n' '[MACOS_TEAM_ID_MISSING] 正式 App 未发现 Team ID。' >&2; exit 2; }
  fi
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    team="$(codesign --display --verbose=4 "$candidate" 2>&1 | awk -F= '/TeamIdentifier=/{print $2; exit}')"
    if [[ "$mode" == 'release' && "$team" != "$expected" ]]; then
      printf '[MACOS_TEAM_ID_MISMATCH] %s 的 Team ID 为 %s，期望 %s。\n' "$candidate" "${team:-未签名}" "$expected" >&2
      exit 2
    fi
    if [[ "$mode" != 'release' && -n "$team" && "$team" != 'not set' ]]; then
      printf '[MACOS_TEAM_ID_MISMATCH] 未签名候选仍含非空 Team ID：%s=%s。\n' "$candidate" "$team" >&2
      exit 2
    fi
  done < <(/usr/bin/find "$APP/Contents" -type f -print0)
}

if [[ "$MODE" == 'release' ]]; then
  OUTPUT="$OUTPUT_DIR/PartyOps_${VERSION}_macos_${RELEASE_ARCH}.pkg"
  if [[ -z "${PARTYOPS_MACOS_APPLICATION_IDENTITY:-}" ]] ||
    [[ -z "${PARTYOPS_MACOS_INSTALLER_IDENTITY:-}" ]] ||
    [[ -z "${PARTYOPS_MACOS_NOTARY_PROFILE:-}" ]]; then
    printf '%s\n' '[MACOS_RELEASE_IDENTITY_MISSING] 正式 PKG 必须提供 Developer ID Application、Installer 与 notarytool 钥匙串配置。' >&2
    exit 2
  fi
elif [[ "$MODE" == 'unsigned-candidate' ]]; then
  # 没有 Developer ID 时仍只允许在真实、同架构 Mac 上生成候选。所有
  # Mach-O 使用 ad-hoc 签名，官网与 Release 必须明确标注未公证。
  OUTPUT="$OUTPUT_DIR/PartyOps_${VERSION}_macos_${RELEASE_ARCH}-UNSIGNED-UNNOTARIZED-CANDIDATE.pkg"
else
  OUTPUT="$OUTPUT_DIR/PartyOps_${VERSION}_macos_${RELEASE_ARCH}-UNSIGNED-DO-NOT-PUBLISH.pkg"
fi
if [[ -e "$OUTPUT" ]]; then
  printf '[MACOS_ARTIFACT_EXISTS] 拒绝覆盖不可变制品：%s\n' "$OUTPUT" >&2
  exit 2
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-macos-${TARGET_ARCH}.XXXXXX")"
cleanup() {
  if [[ "$BUILD_ROOT" == "${TMPDIR:-/tmp}"/partyops-macos-"$TARGET_ARCH".* ]] &&
    [[ -d "$BUILD_ROOT" ]]; then
    chmod -R u+w "$BUILD_ROOT" 2>/dev/null || true
    find "$BUILD_ROOT" -depth -delete 2>/dev/null || true
  fi
}
trap cleanup EXIT

VENV="$BUILD_ROOT/venv"
python3.11 -m venv "$VENV"

# GitHub 的 Intel 原生 Runner 同时存在 Homebrew OpenSSL 与 setup-python
# 自带 OpenSSL。若让 cryptography 从源码自动探测，编译时可能使用前者、
# PyInstaller 却收集后者，最终在用户双击启动时因 EVP_DigestSqueeze 等符号
# 不一致而退出。Intel 构建固定使用经哈希验证、macOS 11 基线的 OpenSSL
# 3.5 LTS 静态闭包；这样 cryptography 不再依赖 Runner 上任意动态 OpenSSL。
if [[ "$TARGET_ARCH" == 'x86_64' ]]; then
  OPENSSL_VERSION='3.5.7'
  OPENSSL_SOURCE_NAME="openssl-$OPENSSL_VERSION.tar.gz"
  OPENSSL_SOURCE_URL="https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/$OPENSSL_SOURCE_NAME"
  OPENSSL_SOURCE_SHA256='a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8'
  OPENSSL_SOURCE_DIR="$BUILD_ROOT/openssl-source"
  OPENSSL_SOURCE="$OPENSSL_SOURCE_DIR/$OPENSSL_SOURCE_NAME"
  OPENSSL_PREFIX="$BUILD_ROOT/openssl-static"
  /bin/mkdir -p "$OPENSSL_SOURCE_DIR"
  curl --fail --location --silent --show-error --retry 1 \
    "$OPENSSL_SOURCE_URL" --output "$OPENSSL_SOURCE.download"
  OPENSSL_ACTUAL_SHA256="$(shasum -a 256 "$OPENSSL_SOURCE.download" | awk '{print $1}')"
  if [[ "$OPENSSL_ACTUAL_SHA256" != "$OPENSSL_SOURCE_SHA256" ]]; then
    printf '[MACOS_OPENSSL_SOURCE_HASH_MISMATCH] 期望 %s，实际 %s。\n' \
      "$OPENSSL_SOURCE_SHA256" "$OPENSSL_ACTUAL_SHA256" >&2
    exit 2
  fi
  /bin/mv "$OPENSSL_SOURCE.download" "$OPENSSL_SOURCE"
  gzip -dc "$OPENSSL_SOURCE" | python3.11 \
    "$ROOT/scripts/validate-portable-tar.py" \
    --expected-root "openssl-$OPENSSL_VERSION" \
    --max-members 100000 --max-bytes 1073741824 --allow-implicit-root
  /bin/mkdir -p "$OPENSSL_SOURCE_DIR/unpacked"
  tar -xzf "$OPENSSL_SOURCE" -C "$OPENSSL_SOURCE_DIR/unpacked"
  (
    cd "$OPENSSL_SOURCE_DIR/unpacked/openssl-$OPENSSL_VERSION"
    ./Configure darwin64-x86_64-cc no-shared no-tests \
      -mmacosx-version-min=11.0 \
      --prefix="$OPENSSL_PREFIX" --openssldir="$OPENSSL_PREFIX/ssl"
    make -j"${PARTYOPS_BUILD_JOBS:-2}"
    make install_sw
  )
  OPENSSL_STATIC=1 OPENSSL_DIR="$OPENSSL_PREFIX" \
    UV_PROJECT_ENVIRONMENT="$VENV" uv sync --project "$ROOT/backend" \
      --frozen --no-dev --no-cache --no-binary-package cryptography
  CRYPTOGRAPHY_OPENSSL_VERSION="$(
    "$VENV/bin/python" -c \
      'from cryptography.hazmat.backends.openssl.backend import backend; print(backend.openssl_version_text())'
  )"
  if [[ "$CRYPTOGRAPHY_OPENSSL_VERSION" != "OpenSSL $OPENSSL_VERSION "* ]]; then
    printf '[MACOS_CRYPTOGRAPHY_OPENSSL_MISMATCH] 期望 OpenSSL %s，实际 %s。\n' \
      "$OPENSSL_VERSION" "$CRYPTOGRAPHY_OPENSSL_VERSION" >&2
    exit 2
  fi
else
  UV_PROJECT_ENVIRONMENT="$VENV" uv sync --project "$ROOT/backend" --frozen --no-dev
fi
# 主线锁文件中的 numpy/onnxruntime 官方新 wheel 超出 macOS 11 基线，不能
# 跟随应用进入安装包。这里使用单独、带哈希的 macOS 运行时锁覆盖它们；
# 不解析依赖，其他依赖仍完全来自主线 uv.lock。
uv pip install --python "$VENV/bin/python" --no-deps --require-hashes \
  --requirement "$SCRIPT_DIR/requirements-runtime.txt"
uv pip install --python "$VENV/bin/python" \
  --requirement "$SCRIPT_DIR/requirements-build.txt"

SOURCE_INPUTS="$BUILD_ROOT/source-inputs"
mkdir -p "$SOURCE_INPUTS"
resolve_locked_source() {
  local name="$1" url="$2" expected="$3" local_source="$4"
  local target="$SOURCE_INPUTS/$name" actual
  if [[ -f "$local_source" ]]; then
    cp "$local_source" "$target"
  else
    curl --fail --location --silent --show-error --retry 1 \
      "$url" --output "$target.download"
    mv "$target.download" "$target"
  fi
  actual="$(shasum -a 256 "$target" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf '[MACOS_SQLITE_SOURCE_HASH_MISMATCH] %s：期望 %s，实际 %s。\n' \
      "$name" "$expected" "$actual" >&2
    exit 2
  fi
  printf '%s\n' "$target"
}

SQLITE_SOURCE="$(resolve_locked_source \
  'sqlite-amalgamation-3510300.zip' \
  'https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip' \
  'acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4' \
  "$ROOT/vendor/sqlite-amalgamation-3510300.zip")"
PYSQLITE_SOURCE="$(resolve_locked_source \
  'pysqlite3-0.5.4.tar.gz' \
  'https://files.pythonhosted.org/packages/33/cb/ef7d041dbecfbf47f9241d7cb6328311fd80fe15bd61a6253d9ab36e9d6d/pysqlite3-0.5.4.tar.gz' \
  'fbc69bfdc0cb43a5badd5403b126d5151371b5037e0397ba9802bb440c5b0021' \
  "$ROOT/vendor/pysqlite3-0.5.4.tar.gz")"

# 下载哈希只能证明内容一致，解压前还要拒绝绝对路径、父目录穿越、链接和
# 异常膨胀归档，避免上游源码包在构建机上写出临时目录。
"$VENV/bin/python" - "$SQLITE_SOURCE" <<'PY'
from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import PurePosixPath

archive_path = sys.argv[1]
expected_root = "sqlite-amalgamation-3510300"
with zipfile.ZipFile(archive_path) as archive:
    entries = archive.infolist()
    if not entries or len(entries) > 1000:
        raise SystemExit("[MACOS_SQLITE_ARCHIVE_UNSAFE] SQLite 源码归档成员数量异常。")
    total = 0
    for entry in entries:
        path = PurePosixPath(entry.filename)
        total += entry.file_size
        mode = (entry.external_attr >> 16) & 0o170000
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != expected_root
            or mode == stat.S_IFLNK
        ):
            raise SystemExit(
                f"[MACOS_SQLITE_ARCHIVE_UNSAFE] SQLite 源码归档包含危险成员：{entry.filename}"
            )
    if total > 128 * 1024 * 1024:
        raise SystemExit("[MACOS_SQLITE_ARCHIVE_UNSAFE] SQLite 源码归档解压大小异常。")
PY
gzip -dc "$PYSQLITE_SOURCE" | "$VENV/bin/python" \
  "$ROOT/scripts/validate-portable-tar.py" \
  --expected-root 'pysqlite3-0.5.4' --max-members 1000 --max-bytes 134217728
mkdir -p "$BUILD_ROOT/sqlite" "$BUILD_ROOT/pysqlite"
ditto -x -k "$SQLITE_SOURCE" "$BUILD_ROOT/sqlite"
tar -xzf "$PYSQLITE_SOURCE" -C "$BUILD_ROOT/pysqlite" --strip-components=1
cp "$BUILD_ROOT"/sqlite/sqlite-amalgamation-3510300/sqlite3.{c,h} "$BUILD_ROOT/pysqlite/"
(
  cd "$BUILD_ROOT/pysqlite"
  "$VENV/bin/python" setup.py build_static bdist_wheel
)
uv pip install --python "$VENV/bin/python" "$BUILD_ROOT"/pysqlite/dist/pysqlite3-*.whl
"$VENV/bin/python" - <<'PY'
from pysqlite3 import dbapi2 as sqlite

if sqlite.sqlite_version != "3.51.3":
    raise SystemExit(
        "[MACOS_SQLITE_VERSION_MISMATCH] "
        f"冻结 SQLite 为 {sqlite.sqlite_version}，期望 3.51.3。"
    )
connection = sqlite.connect(":memory:")
try:
    options = {row[0] for row in connection.execute("PRAGMA compile_options")}
finally:
    connection.close()
if not any("ENABLE_FTS5" in option for option in options):
    raise SystemExit("[MACOS_SQLITE_FTS5_MISSING] 冻结 SQLite 未启用 FTS5。")
PY

corepack pnpm --dir "$ROOT/frontend" install --frozen-lockfile
corepack pnpm --dir "$ROOT/frontend" run build
if [[ ! -f "$ROOT/frontend/dist/client/index.html" ]]; then
  printf '%s\n' '[MACOS_FRONTEND_BUILD_FAILED] 前端生产资源未生成。' >&2
  exit 2
fi

ICONSET="$BUILD_ROOT/PartyOps.iconset"
ICON="$BUILD_ROOT/partyops.icns"
mkdir -p "$ICONSET"
SOURCE_ICON="$ROOT/packaging/windows/partyops-1024.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  doubled=$((size * 2))
  sips -z "$doubled" "$doubled" "$SOURCE_ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICON"

export PARTYOPS_MACOS_ICON="$ICON"
export PARTYOPS_MACOS_TARGET_ARCH="$TARGET_ARCH"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" \
  "$SCRIPT_DIR/partyops.spec"
APP="$BUILD_ROOT/dist/PartyOps.app"
# Intel cryptography 必须把固定 OpenSSL 静态收入 Rust 扩展；如果这里仍出现
# libssl/libcrypto 动态依赖，用户电脑就可能再次遇到构建库与随包库不一致。
CRYPTOGRAPHY_RUST_BINDING="$(
  /usr/bin/find "$APP/Contents/Frameworks/cryptography" -type f \
    -name '_rust.abi3.so' -print -quit
)"
if [[ ! -f "$CRYPTOGRAPHY_RUST_BINDING" ]]; then
  printf '%s\n' '[MACOS_CRYPTOGRAPHY_BINDING_MISSING] 冻结包缺少 cryptography Rust 绑定。' >&2
  exit 2
fi
if [[ "$TARGET_ARCH" == 'x86_64' ]] &&
  otool -L "$CRYPTOGRAPHY_RUST_BINDING" |
    /usr/bin/grep -Eq 'lib(ssl|crypto)\.3\.dylib'; then
  printf '%s\n' '[MACOS_CRYPTOGRAPHY_DYNAMIC_OPENSSL] Intel 冻结包仍动态依赖 Runner OpenSSL。' >&2
  otool -L "$CRYPTOGRAPHY_RUST_BINDING" >&2
  exit 2
fi
# setup-python 的 Intel 运行时可能携带仅供旧算法按需加载的 OpenSSL legacy
# provider；runner 上的该插件以 macOS 15 为最低目标，但 PartyOps 只使用
# 默认 provider 和现代算法。先证明没有 Mach-O 对它形成链接依赖，再从应用
# 闭包移除；随后冻结程序的 Ed25519、TLS 与全部包级自检会再次验证密码能力。
LEGACY_OPENSSL_PROVIDER="$APP/Contents/Frameworks/ossl-modules/legacy.dylib"
if [[ -f "$LEGACY_OPENSSL_PROVIDER" ]]; then
  legacy_inbound_dependency=''
  while IFS= read -r -d '' candidate; do
    [[ "$candidate" == "$LEGACY_OPENSSL_PROVIDER" ]] && continue
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    if otool -L "$candidate" 2>/dev/null | /usr/bin/grep -Fq 'legacy.dylib'; then
      legacy_inbound_dependency="$candidate"
      break
    fi
  done < <(/usr/bin/find "$APP/Contents" -type f -print0)
  if [[ -n "$legacy_inbound_dependency" ]]; then
    printf '[MACOS_OPENSSL_LEGACY_PROVIDER_REFERENCED] %s 仍依赖 legacy.dylib。\n' \
      "$legacy_inbound_dependency" >&2
    exit 2
  fi
  /bin/rm -f "$LEGACY_OPENSSL_PROVIDER"
fi
if /usr/bin/find "$APP/Contents" -type f -name 'legacy.dylib' -print -quit |
  /usr/bin/grep -q .; then
  printf '%s\n' '[MACOS_OPENSSL_LEGACY_PROVIDER_REMAINED] 应用仍包含旧算法 provider。' >&2
  exit 2
fi
# Finder/LaunchServices 先进入一个独立、极小的原生 Mach-O，再 exec 冻结的
# Python 桌面启动器。原生入口会在 Python 引导前写 launch-probe.log，避免
# bootloader 或签名加载失败时继续出现“无窗口、无日志、无证据”。
PYTHON_DESKTOP="$APP/Contents/MacOS/partyops-desktop-bin"
/bin/mv "$APP/Contents/MacOS/partyops-desktop" "$PYTHON_DESKTOP"
xcrun clang -arch "$TARGET_ARCH" -mmacosx-version-min=11.0 -std=c11 \
  -O2 -Wall -Wextra -Werror "$SCRIPT_DIR/launcher-wrapper.c" \
  -o "$APP/Contents/MacOS/partyops-desktop"
for desktop_entry in "$APP/Contents/MacOS/partyops-desktop" "$PYTHON_DESKTOP"; do
  description="$(file -b "$desktop_entry")"
  if [[ "$description" != *Mach-O* ]] || [[ "$description" != *"$TARGET_ARCH"* ]]; then
    printf '[MACOS_DESKTOP_ENTRY_ARCH_MISMATCH] %s 不是 %s Mach-O。\n' \
      "$desktop_entry" "$TARGET_ARCH" >&2
    exit 2
  fi
done
# OCR 与本地 LLM 不能作为 PyInstaller datas 交给 BUNDLE 重排。Mach-O
# 可执行文件放入 MacOS，词库和许可证放入 Resources；把 traineddata 放进
# MacOS 会被 codesign 误判成嵌套代码，导致“能自检但无法形成可信 App”。
/bin/mkdir -p "$APP/Contents/Resources/ocr"
/usr/bin/install -m 0755 \
  "$OCR_RUNTIME/bin/tesseract" "$APP/Contents/MacOS/tesseract"
/usr/bin/ditto "$OCR_RUNTIME/tessdata" "$APP/Contents/Resources/ocr/tessdata"
/usr/bin/ditto "$OCR_RUNTIME/licenses" "$APP/Contents/Resources/ocr/licenses"
/usr/bin/install -m 0755 \
  "$LLAMA_RUNTIME/llama-server" "$APP/Contents/MacOS/llama-server"
/bin/mkdir -p "$APP/Contents/Resources/licenses"
/usr/bin/install -m 0644 "$LLAMA_RUNTIME/licenses/llama.cpp-LICENSE" \
  "$APP/Contents/Resources/licenses/llama.cpp-LICENSE"
/usr/bin/ditto "$OFFICE_RUNTIME" "$APP/Contents/Resources/office-runtime"
# 生产更新器只信任随 PKG 安装且由 root 保护的应用资源。公钥不是可执行
# 代码，必须放入 Apple 约定的 Resources；放在 MacOS 会被 codesign 当成
# 未签名嵌套代码。PyInstaller 对 datas 的重排位置也不是运行时契约，因此
# 在应用封装完成后显式安装并回读。
UPDATE_PUBLIC_KEY_SOURCE="$ROOT/packaging/uos/update-public-key.txt"
UPDATE_PUBLIC_KEY_TARGET="$APP/Contents/Resources/update-public-key.txt"
if [[ ! -f "$UPDATE_PUBLIC_KEY_SOURCE" ]] ||
  [[ "$(wc -c <"$UPDATE_PUBLIC_KEY_SOURCE" | tr -d ' ')" -gt 4096 ]]; then
  printf '%s\n' '[MACOS_UPDATE_TRUST_ROOT_INVALID] 更新根公钥缺失或尺寸异常。' >&2
  exit 2
fi
/usr/bin/install -m 0644 "$UPDATE_PUBLIC_KEY_SOURCE" "$UPDATE_PUBLIC_KEY_TARGET"
/usr/bin/cmp -s "$UPDATE_PUBLIC_KEY_SOURCE" "$UPDATE_PUBLIC_KEY_TARGET" || {
  printf '%s\n' '[MACOS_UPDATE_TRUST_ROOT_COPY_FAILED] 应用内更新根公钥回读不一致。' >&2
  exit 2
}
TESSDATA_PREFIX="$APP/Contents/Resources/ocr/tessdata" \
  "$APP/Contents/MacOS/tesseract" --list-langs | /usr/bin/grep -qx 'chi_sim'
# Intel 首次加载嵌入的 Metal 运行时在原生 Runner 上实测可能超过 30 秒；
# 使用 120 秒有界探测，既不把正确程序误判为失败，也不允许构建无限挂死。
"$VENV/bin/python" - "$APP/Contents/MacOS/llama-server" <<'PY'
import subprocess
import sys

subprocess.run(
    [sys.argv[1], "--version"],
    check=True,
    stdout=subprocess.DEVNULL,
    timeout=120,
)
PY
"$SCRIPT_DIR/validate-bundle.sh" "$APP" "$TARGET_ARCH"

# PKG 载荷只携带 App 的不透明 ZIP，不直接携带 .app 目录。pkgbuild 会递归
# 识别 PyInstaller 内嵌的 Python.framework，并在安装时按“可重定位组件”改写
# Bundle；结果可能是 App 结构损坏，或只安装空目录。postinstall 会先完整
# 解包并验证签名，再以事务方式替换 /Applications/PartyOps.app。
PAYLOAD_ROOT="$BUILD_ROOT/pkg-root"
PAYLOAD_INSTALLER_DIR="$PAYLOAD_ROOT/Library/Application Support/PartyOps/Installer"
PAYLOAD_ARCHIVE="$PAYLOAD_INSTALLER_DIR/PartyOps.app.zip"
PKG_SCRIPTS="$SCRIPT_DIR/pkg-scripts"
stage_pkg_payload() {
  if [[ -e "$PAYLOAD_ROOT" ]]; then
    printf '%s\n' '[MACOS_PAYLOAD_DIR_DIRTY] PKG 临时载荷目录不是全新目录。' >&2
    exit 2
  fi
  /bin/mkdir -p "$PAYLOAD_INSTALLER_DIR"
  /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$PAYLOAD_ARCHIVE"
  [[ -s "$PAYLOAD_ARCHIVE" ]] || {
    printf '%s\n' '[MACOS_PKG_PAYLOAD_ARCHIVE_EMPTY] App 安装载荷归档为空。' >&2
    exit 2
  }
  for script in preinstall postinstall; do
    [[ -x "$PKG_SCRIPTS/$script" ]] || {
      printf '[MACOS_PKG_SCRIPT_INVALID] 安装脚本缺失或不可执行：%s。\n' "$script" >&2
      exit 2
    }
  done

  # codesign 会在 umask 077 的构建目录中生成 _CodeSignature/CodeResources。
  # 若不在归档前显式放开读取/遍历权限，root 安装后普通桌面用户无法启动 App。
  /bin/chmod -R a+rX,go-w "$APP"
  if /usr/bin/find "$APP" -type d ! -perm -0001 -print -quit | /usr/bin/grep -q . ||
    /usr/bin/find "$APP" -type f ! -perm -0004 -print -quit | /usr/bin/grep -q .; then
    printf '%s\n' '[MACOS_APP_PERMISSIONS_PRIVATE] App 含普通用户不可读取或遍历的文件。' >&2
    exit 2
  fi

  # 打包前执行一次 ZIP 往返校验，确保权限、符号链接、签名资源和主程序
  # 均能按 postinstall 的真实解包方式恢复。
  local roundtrip="$BUILD_ROOT/payload-roundtrip"
  /bin/mkdir -p "$roundtrip"
  /usr/bin/ditto -x -k "$PAYLOAD_ARCHIVE" "$roundtrip"
  [[ ! -L "$roundtrip/PartyOps.app" ]] || {
    printf '%s\n' '[MACOS_PKG_PAYLOAD_APP_LINKED] 解包后的 App 不能是符号链接。' >&2
    exit 2
  }
  "$SCRIPT_DIR/validate-bundle.sh" "$roundtrip/PartyOps.app" "$TARGET_ARCH"
  codesign --verify --deep --strict --verbose=2 "$roundtrip/PartyOps.app"
}

if [[ "$MODE" == 'release' ]]; then
  BUNDLE_EXECUTABLE="$APP/Contents/MacOS/partyops-desktop"
  MACHO_CANDIDATE_LIST="$BUILD_ROOT/macho-candidates-release.bin"
  # CFBundleExecutable 是整个 App 的主签名边界。必须先签完新加入的
  # tesseract、llama-server 等嵌套 Mach-O，再签主入口；否则没有预存
  # 签名的全新运行时会令 codesign 在主入口阶段提前失败。
  /usr/bin/find "$APP/Contents" -type f ! -path "$BUNDLE_EXECUTABLE" -print0 >"$MACHO_CANDIDATE_LIST"
  sign_bundle_code "$PARTYOPS_MACOS_APPLICATION_IDENTITY" --timestamp
  codesign --force --timestamp --options runtime \
    --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$BUNDLE_EXECUTABLE"
  codesign --force --timestamp --options runtime \
    --entitlements "$SCRIPT_DIR/entitlements.plist" \
    --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
  verify_team_ids release
  APP_NOTARY_ARCHIVE="$BUILD_ROOT/PartyOps-notary.zip"
  ditto -c -k --keepParent "$APP" "$APP_NOTARY_ARCHIVE"
  xcrun notarytool submit "$APP_NOTARY_ARCHIVE" \
    --keychain-profile "$PARTYOPS_MACOS_NOTARY_PROFILE" --wait \
    --output-format json >"$OUTPUT.app.notary.json"
  "$VENV/bin/python" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if data.get("status") == "Accepted" else 2)' \
    "$OUTPUT.app.notary.json"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
  stage_pkg_payload
  pkgbuild --root "$PAYLOAD_ROOT" \
    --scripts "$PKG_SCRIPTS" --install-location / --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" \
    --sign "$PARTYOPS_MACOS_INSTALLER_IDENTITY" "$OUTPUT"
  pkgutil --check-signature "$OUTPUT"
  xcrun notarytool submit "$OUTPUT" \
    --keychain-profile "$PARTYOPS_MACOS_NOTARY_PROFILE" --wait \
    --output-format json >"$OUTPUT.notary.json"
  "$VENV/bin/python" -c \
    'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if data.get("status") == "Accepted" else 2)' \
    "$OUTPUT.notary.json"
  xcrun stapler staple "$OUTPUT"
  xcrun stapler validate "$OUTPUT"
  spctl --assess --type install --verbose=2 "$OUTPUT"
elif [[ "$MODE" == 'unsigned-candidate' ]]; then
  BUNDLE_EXECUTABLE="$APP/Contents/MacOS/partyops-desktop"
  MACHO_CANDIDATE_LIST="$BUILD_ROOT/macho-candidates-adhoc.bin"
  /usr/bin/find "$APP/Contents" -type f ! -path "$BUNDLE_EXECUTABLE" -print0 >"$MACHO_CANDIDATE_LIST"
  sign_bundle_code -
  codesign --force --options runtime --sign - "$BUNDLE_EXECUTABLE"
  codesign --force --options runtime \
    --entitlements "$SCRIPT_DIR/entitlements.plist" --sign - "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
  verify_team_ids unsigned
  stage_pkg_payload
  pkgbuild --root "$PAYLOAD_ROOT" \
    --scripts "$PKG_SCRIPTS" --install-location / --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" "$OUTPUT"
  # GITHUB_SHA 是触发工作流的提交；手动发布工作流会再检出固定的实际构建
  # 提交，两者不能混写。source_commit 必须取工作树真实 HEAD，另行记录
  # workflow_commit，保证同版本补充制品的来源可以独立复核。
  SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
  WORKFLOW_COMMIT="${GITHUB_SHA:-$SOURCE_COMMIT}"
  GENERATED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  python3.11 - "$OUTPUT.attestation.json" "$TARGET_ARCH" "$SOURCE_COMMIT" \
    "$WORKFLOW_COMMIT" "$GENERATED_AT" <<'PY'
import json
from pathlib import Path
import sys

path, architecture, source_commit, workflow_commit, generated_at = sys.argv[1:]
Path(path).write_text(
    json.dumps(
        {
            "format_version": 1,
            "product": "PartyOps",
            "version": "1.4.5-rc.6",
            "architecture": architecture,
            "source_commit": source_commit,
            "workflow_commit": workflow_commit,
            "generated_at_utc": generated_at,
            "code_signature": "ad-hoc",
            "developer_id_signed": False,
            "notarized": False,
            "native_runtime_selftest": True,
            "real_device_validation": False,
            "publication_class": "unsigned-unnotarized-test-candidate",
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
else
  stage_pkg_payload
  pkgbuild --root "$PAYLOAD_ROOT" \
    --scripts "$PKG_SCRIPTS" --install-location / --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" "$OUTPUT"
fi

OUTPUT_HASH="$(shasum -a 256 "$OUTPUT" | awk '{print $1}')"
printf '%s  %s\n' "$OUTPUT_HASH" "$(basename "$OUTPUT")" >"$OUTPUT.sha256"
printf 'PartyOps macOS %s PKG 已生成：%s\n' "$TARGET_ARCH" "$OUTPUT"
