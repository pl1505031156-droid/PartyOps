#!/usr/bin/env bash
set -euo pipefail

# 从 The Document Foundation 官方归档准备双架构、无界面且不依赖宿主
# LibreOffice 的转换运行时。宿主只执行包装脚本；LibreOffice 及其共享库
# 始终由随包携带的私有 glibc 2.34 加载，不替换系统组件。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ARCHITECTURE="${1:-}"
VERSION=25.8.7.2
case "$ARCHITECTURE" in
  amd64)
    ARCHIVE_ARCH=x86-64
    RPM_ARCH=x86_64
    ARCHIVE_SHA256=a893a4f37a8b3fe110da92bb0135f488f8d695cd40cb7ce59c65bb525849bb67
    LOADER=ld-linux-x86-64.so.2
    EXPECTED_PATTERN='x86-64|x86_64'
    SYSROOT_IMAGE='quay.io/pypa/manylinux_2_34_x86_64@sha256:224ae18ddb6998745b5554185f8f1a4d256543199272ecab4389d310e5e36146'
    SYSROOT_PACKAGES_SHA256=dd5ddb478f8863533b48baf2273411ab7c110f4609a74590223f7d9716dcb6cb
    ;;
  arm64)
    ARCHIVE_ARCH=aarch64
    RPM_ARCH=aarch64
    ARCHIVE_SHA256=a47d693dce67d5f5e15ee6f7ed2faaba5a2234fd21c3cd0227cf0567e63f95a4
    LOADER=ld-linux-aarch64.so.1
    EXPECTED_PATTERN='aarch64|ARM64'
    SYSROOT_IMAGE='quay.io/pypa/manylinux_2_34_aarch64@sha256:b3f10ce321fc98427010670982361997e7d1ccd6dbfed1a4b69dc28cab61ee6a'
    SYSROOT_PACKAGES_SHA256=82b2b3b8c65cc1fcd369b86b0ca0b3b3ed675304898354b8f15143ba57365e90
    ;;
  *)
    echo "用法：prepare-libreoffice-linux.sh amd64|arm64" >&2
    exit 2
    ;;
esac

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
if [[ -z "$PYTHON_BIN" && -x /opt/partyops-python/cpython-3.11.15-linux-x86_64-gnu/bin/python3 ]]; then
  PYTHON_BIN=/opt/partyops-python/cpython-3.11.15-linux-x86_64-gnu/bin/python3
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || {
  echo "缺少用于构建清单的 Python 3。" >&2
  exit 2
}
for command_name in curl rpm2cpio cpio readelf file sha256sum; do
  command -v "$command_name" >/dev/null || {
    echo "缺少 LibreOffice 构建工具：$command_name" >&2
    exit 2
  }
done

ARCHIVE="LibreOffice_${VERSION}_Linux_${ARCHIVE_ARCH}_rpm.tar.gz"
URL="https://downloadarchive.documentfoundation.org/libreoffice/old/${VERSION}/rpm/${RPM_ARCH}/${ARCHIVE}"
DOWNLOAD_DIR="$ROOT/vendor/downloads/libreoffice"
DOWNLOAD="$DOWNLOAD_DIR/$ARCHIVE"
OUTPUT="$ROOT/vendor/linux/libreoffice-headless-$ARCHITECTURE"
SYSROOT="${PARTYOPS_LIBREOFFICE_SYSROOT:-/opt/partyops-libreoffice-sysroot-$ARCHITECTURE}"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/partyops-libreoffice-${VERSION}-${ARCHITECTURE}.XXXXXX")"

[[ ! -e "$OUTPUT" ]] || {
  echo "LibreOffice 输出已存在，拒绝覆盖：$OUTPUT" >&2
  exit 2
}
mkdir -p "$DOWNLOAD_DIR" "$ROOT/vendor/linux" "$STAGE/archive" "$STAGE/root"
if [[ ! -f "$DOWNLOAD" ]]; then
  curl -fL --retry 1 --retry-delay 2 -o "$DOWNLOAD" "$URL"
fi
printf '%s  %s\n' "$ARCHIVE_SHA256" "$DOWNLOAD" | sha256sum --check --strict

if [[ "${PARTYOPS_SKIP_LIBREOFFICE_SYSROOT_PREP:-0}" != 1 ]]; then
  PARTYOPS_LIBREOFFICE_SYSROOT="$SYSROOT" \
    bash "$ROOT/scripts/prepare-libreoffice-private-sysroot.sh" "$ARCHITECTURE"
fi
[[ -f "$SYSROOT/PARTYOPS_SYSROOT_SOURCE.txt" &&
  "$(cat "$SYSROOT/PARTYOPS_SYSROOT_SOURCE.txt")" == "$SYSROOT_IMAGE" &&
  -s "$SYSROOT/PARTYOPS_SYSROOT_PACKAGES.txt" ]] || {
  echo "私有 glibc 运行根未通过来源门禁：$SYSROOT" >&2
  exit 2
}
printf '%s  %s\n' "$SYSROOT_PACKAGES_SHA256" \
  "$SYSROOT/PARTYOPS_SYSROOT_PACKAGES.txt" | sha256sum --check --strict

tar -xzf "$DOWNLOAD" -C "$STAGE/archive"
RPM_ROOT="$STAGE/archive/LibreOffice_${VERSION}_Linux_${ARCHIVE_ARCH}_rpm/RPMS"
[[ -d "$RPM_ROOT" ]] || { echo "官方归档缺少 RPM 目录。" >&2; exit 2; }
RPM_PATTERNS=(
  "libreoffice25.8-ure-*.$RPM_ARCH.rpm"
  "libreoffice25.8-${VERSION}-*.$RPM_ARCH.rpm"
  "libreoffice25.8-writer-*.$RPM_ARCH.rpm"
  "libreoffice25.8-draw-*.$RPM_ARCH.rpm"
  "libreoffice25.8-en-US-*.$RPM_ARCH.rpm"
  "libobasis25.8-core-*.$RPM_ARCH.rpm"
  "libobasis25.8-writer-*.$RPM_ARCH.rpm"
  "libobasis25.8-draw-*.$RPM_ARCH.rpm"
  "libobasis25.8-graphicfilter-*.$RPM_ARCH.rpm"
  "libobasis25.8-xsltfilter-*.$RPM_ARCH.rpm"
  "libobasis25.8-ooofonts-*.$RPM_ARCH.rpm"
  "libobasis25.8-images-*.$RPM_ARCH.rpm"
  "libobasis25.8-en-US-*.$RPM_ARCH.rpm"
)
: >"$STAGE/UPSTREAM_RPMS.txt"
for pattern in "${RPM_PATTERNS[@]}"; do
  matches=("$RPM_ROOT"/$pattern)
  if [[ "${#matches[@]}" -ne 1 || ! -f "${matches[0]}" ]]; then
    echo "官方 RPM 选择必须唯一：$pattern" >&2
    exit 2
  fi
  rpm2cpio "${matches[0]}" | (cd "$STAGE/root" && cpio -idm --quiet)
  basename "${matches[0]}" >>"$STAGE/UPSTREAM_RPMS.txt"
done

RUNTIME="$STAGE/root/opt/libreoffice25.8"
[[ -x "$RUNTIME/program/soffice" && -f "$RUNTIME/program/soffice.bin" ]] || {
  echo "提取后的 LibreOffice Writer 运行时不完整。" >&2
  exit 2
}
file "$RUNTIME/program/soffice.bin" | grep -Eq "$EXPECTED_PATTERN" || {
  echo "LibreOffice 运行时 CPU 架构错误。" >&2
  exit 2
}

# PartyOps 只在页面内调用无界面 Writer；移除会动态引入 Qt/GTK/媒体栈
# 的外部 UI 与媒体入口，既缩小攻击面，也防止处理时弹出系统外窗口。
mkdir -p "$STAGE/omitted-ui-media"
for optional_name in \
  libavmediaqt6.so libavmediagtk.so libavmediagst.so \
  liblibreofficekitgtk.so libofficebean.so; do
  if [[ -f "$RUNTIME/program/$optional_name" ]]; then
    mv "$RUNTIME/program/$optional_name" "$STAGE/omitted-ui-media/$optional_name"
  fi
done

"$PYTHON_BIN" "$ROOT/scripts/collect-linux-private-runtime.py" \
  --runtime "$RUNTIME" --sysroot "$SYSROOT" \
  --output "$RUNTIME/private-runtime" --loader "$LOADER" \
  --seed-name libfreebl3.so --seed-name libfreeblpriv3.so \
  --seed-name libnssckbi.so --seed-name libnsssysinit.so \
  --seed-name libsoftokn3.so \
  --copy-name libfreebl3.chk --copy-name libfreeblpriv3.chk \
  --copy-name libsoftokn3.chk \
  --exclude-name libavmediaqt6.so --exclude-name libavmediagtk.so \
  --exclude-name libavmediagst.so --exclude-name liblibreofficekitgtk.so \
  --exclude-name libofficebean.so
mv "$RUNTIME/program/soffice" "$RUNTIME/program/soffice.upstream"
cat >"$RUNTIME/program/soffice" <<EOF
#!/usr/bin/env bash
set -u
program="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd -P)"
private="\$program/../private-runtime"
loader="\$private/$LOADER"
export SAL_USE_VCLPLUGIN=svp
export SAL_DISABLEGL=1
export SAL_DISABLESKIA=1
export SAL_DISABLE_OPENCL=1
run_once() {
  "\$loader" --library-path "\$program:\$private" "\$program/soffice.bin" "\$@"
}
run_once "\$@"
status=\$?
if [[ "\$status" -eq 81 ]]; then
  run_once "\$@"
  status=\$?
fi
exit "\$status"
EOF
chmod 0755 "$RUNTIME/program/soffice"

mkdir -p "$RUNTIME/licenses/libreoffice" "$RUNTIME/licenses/private-runtime"
for license in "$RUNTIME/LICENSE" "$RUNTIME/LICENSE.html" "$RUNTIME/NOTICE"; do
  [[ ! -f "$license" ]] || cp -f "$license" "$RUNTIME/licenses/libreoffice/"
done
if [[ -d "$RUNTIME/readmes" ]]; then
  cp -a "$RUNTIME/readmes" "$RUNTIME/licenses/libreoffice/readmes"
fi
cp -a "$SYSROOT/usr/share/licenses"/. "$RUNTIME/licenses/private-runtime/"
cp "$STAGE/UPSTREAM_RPMS.txt" "$RUNTIME/UPSTREAM_RPMS.txt"
cp "$SYSROOT/PARTYOPS_SYSROOT_PACKAGES.txt" "$RUNTIME/PRIVATE_RUNTIME_PACKAGES.txt"
test -n "$(find "$RUNTIME/licenses" -type f -print -quit)"

"$PYTHON_BIN" - "$RUNTIME/SOURCE.json" "$URL" "$ARCHIVE_SHA256" \
  "$ARCHITECTURE" "$SYSROOT_IMAGE" "$SYSROOT_PACKAGES_SHA256" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
target.write_text(
    json.dumps(
        {
            "component": "LibreOffice headless Writer converter with private glibc runtime",
            "version": "25.8.7.2",
            "architecture": sys.argv[4],
            "platform": "linux",
            "origin": "The Document Foundation official archive",
            "upstream": sys.argv[2],
            "archive_sha256": sys.argv[3],
            "private_runtime_image": sys.argv[5],
            "private_runtime_packages_sha256": sys.argv[6],
            "private_runtime_glibc": "2.34",
            "minimum_host_glibc": "2.17",
            "host_isolation": (
                "PartyOps invokes the bundled ELF loader and library closure; "
                "the system glibc is neither replaced nor added to LD_LIBRARY_PATH"
            ),
            "first_profile_restart": "exit status 81 is retried exactly once",
            "omitted_non_headless_components": [
                "Qt/GTK media backends",
                "LibreOfficeKit GTK frontend",
                "Java office bean",
            ],
            "usage": (
                "Bundled for local DOC/DOCX/WPS/PDF-related official-document "
                "conversion initiated inside PartyOps; no external office window"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
PY

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-libreoffice-test-${ARCHITECTURE}.XXXXXX")"
TEST_INPUT="$TEST_ROOT/北京时间公文转换验证.docx"
mkdir -p "$TEST_ROOT/output" "$TEST_ROOT/home"
"$PYTHON_BIN" - "$TEST_INPUT" <<'PY'
import pathlib
import sys
import zipfile

target = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(
        "[Content_Types].xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>',
    )
    archive.writestr(
        "_rels/.rels",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
    )
    archive.writestr(
        "word/document.xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>PartyOps 北京时间公文转换验证</w:t></w:r></w:p>'
        '<w:sectPr/></w:body></w:document>',
    )
PY

run_test_office() {
  if [[ "$ARCHITECTURE" == amd64 ]]; then
    "$RUNTIME/program/soffice" "$@"
  else
    private="$RUNTIME/private-runtime"
    set +e
    HOME="$TEST_ROOT/home" SAL_USE_VCLPLUGIN=svp \
      SAL_DISABLEGL=1 SAL_DISABLESKIA=1 SAL_DISABLE_OPENCL=1 \
      "${PARTYOPS_QEMU_AARCH64:-/usr/bin/qemu-aarch64-static}" \
      "$private/$LOADER" --library-path "$RUNTIME/program:$private" \
      "$RUNTIME/program/soffice.bin" "$@"
    status=$?
    set -e
    if [[ "$status" -eq 81 ]]; then
      HOME="$TEST_ROOT/home" SAL_USE_VCLPLUGIN=svp \
        SAL_DISABLEGL=1 SAL_DISABLESKIA=1 SAL_DISABLE_OPENCL=1 \
        "${PARTYOPS_QEMU_AARCH64:-/usr/bin/qemu-aarch64-static}" \
        "$private/$LOADER" --library-path "$RUNTIME/program:$private" \
        "$RUNTIME/program/soffice.bin" "$@"
    elif [[ "$status" -ne 0 ]]; then
      return "$status"
    fi
  fi
}
for target_format in pdf 'doc:MS Word 97'; do
  run_test_office \
    --headless --nologo --nodefault --nolockcheck --nofirststartwizard \
    -env:UserInstallation="file://$TEST_ROOT/profile" \
    --convert-to "$target_format" --outdir "$TEST_ROOT/output" "$TEST_INPUT"
done
test -s "$TEST_ROOT/output/北京时间公文转换验证.pdf"
test -s "$TEST_ROOT/output/北京时间公文转换验证.doc"
file "$TEST_ROOT/output/北京时间公文转换验证.pdf" | grep -Fq 'PDF document'
file "$TEST_ROOT/output/北京时间公文转换验证.doc" | grep -Fq 'Composite Document File'

mv "$RUNTIME" "$OUTPUT"
test -x "$OUTPUT/program/soffice"
test -s "$OUTPUT/SOURCE.json"
echo "Linux $ARCHITECTURE LibreOffice $VERSION 私有运行时已准备：$OUTPUT"
