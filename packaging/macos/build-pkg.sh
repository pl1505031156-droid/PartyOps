#!/bin/bash
set -euo pipefail
umask 077

VERSION='1.4.3-rc.8'
PACKAGE_VERSION='1.4.3.8'
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$ROOT/artifacts/release-$VERSION-final"
OCR_RUNTIME="${PARTYOPS_MACOS_OCR_RUNTIME:-}"
LLAMA_RUNTIME="${PARTYOPS_MACOS_LLAMA_RUNTIME:-}"
for command in python3.11 uv node corepack sips iconutil pkgbuild pkgutil spctl xcrun \
  curl ditto gzip tar shasum file otool codesign; do
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
for binary in "$OCR_RUNTIME/bin/tesseract" "$LLAMA_RUNTIME/llama-server"; do
  description="$(file -b "$binary")"
  if [[ "$description" != *Mach-O* ]] || [[ "$description" != *"$TARGET_ARCH"* ]]; then
    printf '[MACOS_NATIVE_RUNTIME_ARCH_MISMATCH] %s 不是 %s Mach-O。\n' "$binary" "$TARGET_ARCH" >&2
    exit 2
  fi
done

mkdir -p "$OUTPUT_DIR"
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
  OUTPUT="$OUTPUT_DIR/PartyOps_${VERSION}_macos_${RELEASE_ARCH}.pkg"
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
UV_PROJECT_ENVIRONMENT="$VENV" uv sync --project "$ROOT/backend" --frozen --no-dev
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
"$APP/Contents/MacOS/llama-server" --version >/dev/null
"$SCRIPT_DIR/validate-bundle.sh" "$APP" "$TARGET_ARCH"

# 使用显式根载荷而不是 pkgbuild --component。前者把已验证的 App 原样
# 放入 /Applications，避免组件分析/重定位在不同 macOS 版本上重写 Bundle
# 布局。BUILD_ROOT 为本轮 mktemp 新目录；若 staging 意外存在则拒绝覆盖。
PAYLOAD_ROOT="$BUILD_ROOT/pkg-root"
PAYLOAD_APP="$PAYLOAD_ROOT/Applications/PartyOps.app"
COMPONENT_PLIST="$SCRIPT_DIR/component.plist"
stage_pkg_payload() {
  if [[ -e "$PAYLOAD_ROOT" ]]; then
    printf '%s\n' '[MACOS_PAYLOAD_DIR_DIRTY] PKG 临时载荷目录不是全新目录。' >&2
    exit 2
  fi
  /bin/mkdir -p "$PAYLOAD_ROOT/Applications"
  /usr/bin/ditto "$APP" "$PAYLOAD_APP"
  /usr/bin/plutil -lint "$PAYLOAD_APP/Contents/Info.plist" >/dev/null
  /usr/bin/plutil -lint "$COMPONENT_PLIST" >/dev/null
  "$SCRIPT_DIR/validate-bundle.sh" "$PAYLOAD_APP" "$TARGET_ARCH"
  codesign --verify --deep --strict --verbose=2 "$PAYLOAD_APP"
}

if [[ "$MODE" == 'release' ]]; then
  MACHO_CANDIDATE_LIST="$BUILD_ROOT/macho-candidates-release.bin"
  /usr/bin/find "$APP/Contents" -type f -print0 >"$MACHO_CANDIDATE_LIST"
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    codesign --force --timestamp --options runtime \
      --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$candidate"
  done <"$MACHO_CANDIDATE_LIST"
  codesign --force --timestamp --options runtime \
    --entitlements "$SCRIPT_DIR/entitlements.plist" \
    --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
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
  pkgbuild --root "$PAYLOAD_ROOT" --component-plist "$COMPONENT_PLIST" \
    --install-location / --ownership recommended \
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
  MACHO_CANDIDATE_LIST="$BUILD_ROOT/macho-candidates-adhoc.bin"
  /usr/bin/find "$APP/Contents" -type f -print0 >"$MACHO_CANDIDATE_LIST"
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    codesign --force --options runtime --sign - "$candidate"
  done <"$MACHO_CANDIDATE_LIST"
  codesign --force --options runtime \
    --entitlements "$SCRIPT_DIR/entitlements.plist" --sign - "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
  stage_pkg_payload
  pkgbuild --root "$PAYLOAD_ROOT" --component-plist "$COMPONENT_PLIST" \
    --install-location / --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" "$OUTPUT"
  SOURCE_COMMIT="${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD)}"
  GENERATED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  python3.11 - "$OUTPUT.attestation.json" "$TARGET_ARCH" "$SOURCE_COMMIT" "$GENERATED_AT" <<'PY'
import json
from pathlib import Path
import sys

path, architecture, source_commit, generated_at = sys.argv[1:]
Path(path).write_text(
    json.dumps(
        {
            "format_version": 1,
            "product": "PartyOps",
            "version": "1.4.3-rc.8",
            "architecture": architecture,
            "source_commit": source_commit,
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
  pkgbuild --root "$PAYLOAD_ROOT" --component-plist "$COMPONENT_PLIST" \
    --install-location / --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" "$OUTPUT"
fi

shasum -a 256 "$OUTPUT" >"$OUTPUT.sha256"
printf 'PartyOps macOS %s PKG 已生成：%s\n' "$TARGET_ARCH" "$OUTPUT"
