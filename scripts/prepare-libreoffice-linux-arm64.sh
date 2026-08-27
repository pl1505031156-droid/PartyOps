#!/usr/bin/env bash
set -euo pipefail

# 本脚本只在 scripts/build-linux-arm64-chroot.sh 建立的 aarch64、glibc
# 2.17 根环境内运行。官方 CentOS 7.9 Vault 包可覆盖国产 ARM64 系统常见
# 的旧 glibc 基线，并为 DOC/WPS 提供本地 Writer 转换器。
[[ "$(uname -m)" == aarch64 ]] || {
  echo "ARM64 LibreOffice 必须在 aarch64 根环境中准备。" >&2
  exit 2
}
[[ "$(getconf GNU_LIBC_VERSION)" == 'glibc 2.17' ]] || {
  echo "ARM64 LibreOffice 必须使用 glibc 2.17 基线。" >&2
  exit 2
}

PACKAGES=(
  libreoffice-core
  libreoffice-data
  libreoffice-graphicfilter
  libreoffice-ure
  libreoffice-ure-common
  libreoffice-writer
  libreoffice-xsltfilter
)
yum -y install "${PACKAGES[@]}"

SOURCE_ROOT=/usr/lib64/libreoffice
OUTPUT=/workspace/partyops/vendor/linux/libreoffice-headless-arm64
STAGE=/tmp/partyops-libreoffice-arm64-5.3.6.1
test -x "$SOURCE_ROOT/program/soffice"
if [[ -e "$OUTPUT" ]]; then
  echo "ARM64 LibreOffice 目标已存在，拒绝覆盖：$OUTPUT" >&2
  exit 2
fi
if [[ -e "$STAGE" ]]; then
  echo "ARM64 LibreOffice 暂存目录已存在，拒绝复用：$STAGE" >&2
  exit 2
fi

mkdir -p "$STAGE" "$STAGE/licenses"
cp -a "$SOURCE_ROOT"/. "$STAGE"/
# CentOS 包把少量许可/文档做成指向 /usr/share/doc 的绝对链接。运行时
# 不允许携带越界链接：存在的文件转为实体副本，失效文档链接直接移除，
# 许可证仍由下方独立目录完整收集。
while IFS= read -r -d '' link; do
  resolved="$(readlink -f "$link" 2>/dev/null || true)"
  if [[ -n "$resolved" && -f "$resolved" ]]; then
    unlink "$link"
    cp -aL "$resolved" "$link"
  else
    unlink "$link"
  fi
done < <(find "$STAGE" -type l -print0)
find /usr/share/licenses -maxdepth 2 -type f \
  \( -path '*libreoffice*' -o -path '*libwpd*' -o -path '*libwps*' \) \
  -exec cp -f {} "$STAGE/licenses"/ \;
test -n "$(find "$STAGE/licenses" -type f -print -quit)"
rpm -q "${PACKAGES[@]}" | sort >"$STAGE/packages.txt"

/opt/partyops-python-3.11.15-arm64/bin/python3.11 - \
  "$STAGE/SOURCE.json" "$STAGE/packages.txt" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
packages = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
target.write_text(
    json.dumps(
        {
            "component": "LibreOffice headless document converter",
            "version": "5.3.6.1-26.el7_9",
            "architecture": "arm64",
            "platform": "linux",
            "origin": "CentOS 7.9.2009 Vault official aarch64 packages",
            "upstream": (
                "https://vault.centos.org/altarch/7.9.2009/os/"
                "aarch64/Packages/"
            ),
            "packages": packages,
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

file "$STAGE/program/soffice.bin" | grep -Eq 'aarch64|ARM64'
mv "$STAGE" "$OUTPUT"
echo "Linux ARM64 LibreOffice 运行时已准备：$OUTPUT"
