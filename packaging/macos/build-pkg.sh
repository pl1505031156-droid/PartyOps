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
  ditto tar shasum file otool codesign; do
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

SQLITE_SOURCE="$ROOT/vendor/sqlite-amalgamation-3510300.zip"
PYSQLITE_SOURCE="$ROOT/vendor/pysqlite3-0.5.4.tar.gz"
if [[ ! -f "$SQLITE_SOURCE" ]] || [[ ! -f "$PYSQLITE_SOURCE" ]]; then
  printf '%s\n' '[MACOS_SQLITE_SOURCE_MISSING] 缺少锁定的 SQLite/pysqlite3 源码归档。' >&2
  exit 2
fi
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

export PARTYOPS_MACOS_OCR_RUNTIME="$OCR_RUNTIME"
export PARTYOPS_MACOS_LLAMA_RUNTIME="$LLAMA_RUNTIME"
export PARTYOPS_MACOS_ICON="$ICON"
export PARTYOPS_MACOS_TARGET_ARCH="$TARGET_ARCH"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean \
  --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" \
  "$SCRIPT_DIR/partyops.spec"
APP="$BUILD_ROOT/dist/PartyOps.app"
"$SCRIPT_DIR/validate-bundle.sh" "$APP" "$TARGET_ARCH"

if [[ "$MODE" == 'release' ]]; then
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    codesign --force --timestamp --options runtime \
      --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$candidate"
  done < <(find "$APP/Contents" -type f -print0)
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
  pkgbuild --component "$APP" --install-location /Applications --ownership recommended \
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
  while IFS= read -r -d '' candidate; do
    [[ "$(file -b "$candidate" 2>/dev/null || true)" == *Mach-O* ]] || continue
    codesign --force --options runtime --sign - "$candidate"
  done < <(find "$APP/Contents" -type f -print0)
  codesign --force --options runtime \
    --entitlements "$SCRIPT_DIR/entitlements.plist" --sign - "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
  pkgbuild --component "$APP" --install-location /Applications --ownership recommended \
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
  pkgbuild --component "$APP" --install-location /Applications --ownership recommended \
    --identifier cn.partyops.desktop --version "$PACKAGE_VERSION" "$OUTPUT"
fi

shasum -a 256 "$OUTPUT" >"$OUTPUT.sha256"
printf 'PartyOps macOS %s PKG 已生成：%s\n' "$TARGET_ARCH" "$OUTPUT"
