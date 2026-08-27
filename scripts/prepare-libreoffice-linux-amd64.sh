#!/usr/bin/env bash
set -euo pipefail

# 从 The Document Foundation 官方归档准备 PartyOps 随包携带的 Linux
# AMD64 无界面公文转换运行时。大型运行时位于 vendor 外置目录，不提交 Git。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VERSION="26.2.5.2"
ARCHIVE="LibreOffice_${VERSION}_Linux_x86-64_rpm.tar.gz"
EXPECTED_SHA256="f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d"
URL="https://downloadarchive.documentfoundation.org/libreoffice/old/${VERSION}/rpm/x86_64/${ARCHIVE}"
DOWNLOAD_DIR="$ROOT/vendor/downloads/libreoffice"
DOWNLOAD="$DOWNLOAD_DIR/$ARCHIVE"
OUTPUT="$ROOT/vendor/linux/libreoffice-headless-amd64"
STAGE="${TMPDIR:-/tmp}/partyops-libreoffice-${VERSION}-amd64"

mkdir -p "$DOWNLOAD_DIR" "$ROOT/vendor/linux"
if [[ ! -f "$DOWNLOAD" ]]; then
  curl -fL --retry 1 --retry-delay 2 -o "$DOWNLOAD" "$URL"
fi
printf '%s  %s\n' "$EXPECTED_SHA256" "$DOWNLOAD" | sha256sum -c -
if [[ ! -e "$OUTPUT" ]]; then
  if [[ -e "$STAGE" ]]; then
    echo "暂存目录已存在，拒绝复用：$STAGE" >&2
    exit 2
  fi
  mkdir -p "$STAGE/archive" "$STAGE/root"
  tar -xzf "$DOWNLOAD" -C "$STAGE/archive"
  command -v rpm2cpio >/dev/null
  command -v cpio >/dev/null
  for rpm_file in "$STAGE/archive/LibreOffice_${VERSION}_Linux_x86-64_rpm/RPMS/"*.rpm; do
    (cd "$STAGE/root" && rpm2cpio "$rpm_file" | cpio -idm --quiet)
  done
  RUNTIME="$STAGE/root/opt/libreoffice26.2"
  test -x "$RUNTIME/program/soffice"
  file "$RUNTIME/program/soffice.bin" | grep -Eq 'x86-64|x86_64'
  cp -a "$RUNTIME" "$OUTPUT"
fi
test -x "$OUTPUT/program/soffice"
file "$OUTPUT/program/soffice.bin" | grep -Eq 'x86-64|x86_64'
mkdir -p "$OUTPUT/licenses"
for license in "$OUTPUT/LICENSE" "$OUTPUT/LICENSE.html" "$OUTPUT/NOTICE"; do
  if [[ -f "$license" ]]; then
    cp -f "$license" "$OUTPUT/licenses/"
  fi
done
if [[ -z "$(find "$OUTPUT/licenses" -type f -print -quit)" ]]; then
  echo "提取后未发现许可证文本，拒绝使用运行时。" >&2
  exit 2
fi
PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" && -x /opt/partyops-python/cpython-3.11.15-linux-x86_64-gnu/bin/python3 ]]; then
  PYTHON_BIN=/opt/partyops-python/cpython-3.11.15-linux-x86_64-gnu/bin/python3
fi
test -n "$PYTHON_BIN"
"$PYTHON_BIN" - "$OUTPUT/SOURCE.json" "$URL" "$EXPECTED_SHA256" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
target.write_text(
    json.dumps(
        {
            "component": "LibreOffice headless document converter",
            "version": "26.2.5.2",
            "architecture": "amd64",
            "platform": "linux",
            "origin": "The Document Foundation official archive",
            "upstream": sys.argv[2],
            "archive_sha256": sys.argv[3],
            "minimum_runtime_note": (
                "Modern UOS/Kylin/openEuler candidate runtime; the packaging "
                "gate records unresolved shared-library and GLIBC requirements"
            ),
            "licenses": sorted(
                str(path.relative_to(target.parent))
                for path in (target.parent / "licenses").iterdir()
                if path.is_file()
            ),
            "usage": (
                "Bundled only as a local, network-disabled DOC/WPS conversion "
                "runtime for PartyOps official-document formatting"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

echo "Linux AMD64 LibreOffice 运行时已准备：$OUTPUT"
