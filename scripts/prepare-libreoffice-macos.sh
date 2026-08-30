#!/bin/bash
set -euo pipefail
umask 077

# 从 The Document Foundation 官方、固定哈希的 DMG 中提取原生 macOS
# LibreOffice 闭包。PartyOps 只调用无界面的 Writer 转换入口，不依赖用户
# 另行安装 LibreOffice，也不引用 Homebrew 或 Runner 私有路径。
VERSION='26.2.5.2'
ARCHITECTURE=''
OUTPUT_ROOT=''
while (($#)); do
  case "$1" in
    --architecture)
      ARCHITECTURE="${2:-}"
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

[[ "$(uname -s)" == Darwin ]] || {
  printf '%s\n' '[MACOS_OFFICE_NATIVE_REQUIRED] LibreOffice 运行时必须在真实 macOS 上准备。' >&2
  exit 2
}
[[ -n "$OUTPUT_ROOT" ]] || {
  printf '%s\n' '用法：prepare-libreoffice-macos.sh --architecture arm64|x86_64 --output-root DIR' >&2
  exit 2
}
case "$ARCHITECTURE" in
  arm64)
    ARCHIVE="LibreOffice_${VERSION}_MacOS_aarch64.dmg"
    EXPECTED_SHA256='c99fb4fe574437fc4cb820a4ca15271bca325920861f7139858b36d7f9df78ad'
    ARCHIVE_DIRECTORY='aarch64'
    ;;
  x86_64)
    ARCHIVE="LibreOffice_${VERSION}_MacOS_x86-64.dmg"
    EXPECTED_SHA256='e26180298685274b54aa7fe6e1101c65465a372f457a6748ebd642720811db36'
    ARCHIVE_DIRECTORY='x86_64'
    ;;
  *)
    printf '%s\n' '架构必须为 arm64 或 x86_64。' >&2
    exit 2
    ;;
esac
[[ "$(uname -m)" == "$ARCHITECTURE" ]] || {
  printf '[MACOS_OFFICE_ARCH_MISMATCH] 构建机为 %s，目标为 %s。\n' \
    "$(uname -m)" "$ARCHITECTURE" >&2
  exit 2
}

OUTPUT_ROOT="$(mkdir -p "$OUTPUT_ROOT" && cd "$OUTPUT_ROOT" && pwd -P)"
OUTPUT="$OUTPUT_ROOT/office-$ARCHITECTURE"
DOWNLOADS="$OUTPUT_ROOT/downloads"
DOWNLOAD="$DOWNLOADS/$ARCHIVE"
URL="https://downloadarchive.documentfoundation.org/libreoffice/old/${VERSION}/mac/${ARCHIVE_DIRECTORY}/${ARCHIVE}"
if [[ -e "$OUTPUT" ]]; then
  printf '[MACOS_OFFICE_OUTPUT_EXISTS] 拒绝覆盖运行时：%s\n' "$OUTPUT" >&2
  exit 2
fi
/bin/mkdir -p "$DOWNLOADS"
if [[ ! -f "$DOWNLOAD" ]]; then
  curl --fail --location --silent --show-error --retry 1 --retry-delay 2 \
    "$URL" --output "$DOWNLOAD.part"
  /bin/mv "$DOWNLOAD.part" "$DOWNLOAD"
fi
ACTUAL_SHA256="$(shasum -a 256 "$DOWNLOAD" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  printf '[MACOS_OFFICE_SOURCE_HASH_MISMATCH] 期望 %s，实际 %s。\n' \
    "$EXPECTED_SHA256" "$ACTUAL_SHA256" >&2
  exit 2
fi

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/partyops-libreoffice-${ARCHITECTURE}.XXXXXX")"
MOUNT="$STAGE/mount"
RUNTIME="$STAGE/runtime"
MOUNTED=0
cleanup() {
  if [[ "$MOUNTED" == 1 ]]; then
    hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
  fi
  if [[ "$STAGE" == "${TMPDIR:-/tmp}"/partyops-libreoffice-"$ARCHITECTURE".* ]] &&
    [[ -d "$STAGE" ]]; then
    /usr/bin/find "$STAGE" -depth -delete 2>/dev/null || true
  fi
}
trap cleanup EXIT
/bin/mkdir -p "$MOUNT"
hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT" "$DOWNLOAD" >/dev/null
MOUNTED=1
SOURCE_APP="$MOUNT/LibreOffice.app"
[[ -d "$SOURCE_APP/Contents" ]] || {
  printf '%s\n' '[MACOS_OFFICE_APP_MISSING] 官方 DMG 中未发现 LibreOffice.app。' >&2
  exit 2
}
/usr/bin/ditto "$SOURCE_APP/Contents" "$RUNTIME"

# 官方 macOS Bundle 的程序目录名为 MacOS；PartyOps 跨平台能力契约固定从
# office-runtime/program/soffice 启动。使用闭包内相对链接统一布局，不复制
# 可执行文件，也不改变 LibreOffice 对 Frameworks/Resources 的相对定位。
if [[ ! -e "$RUNTIME/program" ]]; then
  /bin/ln -s MacOS "$RUNTIME/program"
fi
[[ -x "$RUNTIME/program/soffice" ]] || {
  printf '%s\n' '[MACOS_OFFICE_ENTRY_MISSING] 提取后缺少 macOS 原生 soffice。' >&2
  exit 2
}
DESCRIPTION="$(file -b "$RUNTIME/program/soffice")"
if [[ "$DESCRIPTION" != *Mach-O* ]] || [[ "$DESCRIPTION" != *"$ARCHITECTURE"* ]]; then
  printf '[MACOS_OFFICE_BINARY_ARCH_MISMATCH] soffice 不是 %s Mach-O。\n' \
    "$ARCHITECTURE" >&2
  exit 2
fi

/bin/mkdir -p "$RUNTIME/licenses"
for candidate in \
  "$RUNTIME/Resources/LICENSE" \
  "$RUNTIME/Resources/LICENSE.html" \
  "$RUNTIME/Resources/NOTICE"; do
  if [[ -f "$candidate" ]]; then
    /usr/bin/install -m 0644 "$candidate" "$RUNTIME/licenses/$(basename "$candidate")"
  fi
done
[[ -n "$(/usr/bin/find "$RUNTIME/licenses" -type f -print -quit)" ]] || {
  printf '%s\n' '[MACOS_OFFICE_LICENSE_MISSING] 官方闭包中未发现许可证文件。' >&2
  exit 2
}
python3.11 - "$RUNTIME/SOURCE.json" "$URL" "$EXPECTED_SHA256" \
  "$VERSION" "$ARCHITECTURE" <<'PY'
import json
from pathlib import Path
import sys

target, url, sha256, version, architecture = sys.argv[1:]
path = Path(target)
path.write_text(
    json.dumps(
        {
            "component": "LibreOffice headless document converter",
            "version": version,
            "architecture": architecture,
            "platform": "macos",
            "minimum_macos": "11.0",
            "origin": "The Document Foundation official archive",
            "upstream": url,
            "archive_sha256": sha256,
            "usage": (
                "Bundled only as a local, network-disabled DOC/WPS conversion "
                "runtime for PartyOps official-document formatting"
            ),
            "licenses": sorted(
                str(item.relative_to(path.parent))
                for item in (path.parent / "licenses").iterdir()
                if item.is_file()
            ),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

# 在仍处于独立闭包位置时执行一次真实无界面启动；正式 App 内还会再次由
# bundle validator 和 package self-test 验证，避免只检查文件存在。
python3.11 - "$RUNTIME/program/soffice" "$STAGE/profile" <<'PY'
import subprocess
import sys

subprocess.run(
    [
        sys.argv[1],
        "--headless",
        f"-env:UserInstallation=file://{sys.argv[2]}",
        "--version",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    timeout=120,
)
PY
hdiutil detach "$MOUNT" -quiet
MOUNTED=0
/bin/mv "$RUNTIME" "$OUTPUT"
printf 'macOS %s LibreOffice %s 运行时已准备：%s\n' \
  "$ARCHITECTURE" "$VERSION" "$OUTPUT"
