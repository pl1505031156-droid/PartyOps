#!/usr/bin/env bash
set -euo pipefail

# 只刷新与桌面启动闭环有关的文本入口。冻结后的 Python/本地 AI/OCR 二进制
# 未发生变化时，无需重复编译；随后仍必须重新封装 DEB/RPM 并执行成品动态门禁。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${1:-}"
[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] || {
  echo "用法：refresh-linux-portable-launchers.sh amd64|arm64" >&2
  exit 2
}

ARTIFACT="$ROOT/artifacts/PartyOps-linux-$ARCH.tar.zst"
OUTPUT="$ARTIFACT.new"
[[ -f "$ARTIFACT" ]] || {
  echo "缺少待刷新的便携载荷：$ARTIFACT" >&2
  exit 2
}

TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-launcher-refresh.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  rm -f -- "$OUTPUT"
  case "$TEMP_ROOT" in
    "${TMPDIR:-/tmp}"/partyops-launcher-refresh.*) rm -rf -- "$TEMP_ROOT" ;;
    *) echo "拒绝清理异常临时目录：$TEMP_ROOT" >&2; status=3 ;;
  esac
  exit "$status"
}
trap cleanup EXIT INT TERM

zstd -dc -- "$ARTIFACT" | tar -xf - -C "$TEMP_ROOT"
RUNTIME="$TEMP_ROOT/PartyOps"
[[ -x "$RUNTIME/partyops" && -f "$RUNTIME/VERSION" ]] || {
  echo "便携载荷结构无效，拒绝刷新。" >&2
  exit 2
}

normalize_install() {
  local mode="$1" source="$2" target="$3" temporary="$TEMP_ROOT/normalized"
  sed 's/\r$//' "$source" >"$temporary"
  install -m "$mode" "$temporary" "$target"
}

normalize_install 0755 "$ROOT/packaging/uos/desktop-launcher.sh" \
  "$RUNTIME/desktop-launcher.sh"
normalize_install 0755 "$ROOT/packaging/uos/install-desktop-shortcut.sh" \
  "$RUNTIME/install-desktop-shortcut.sh"
normalize_install 0644 "$ROOT/packaging/uos/partyops.desktop" \
  "$RUNTIME/partyops.desktop"
normalize_install 0644 "$ROOT/packaging/uos/partyops-client.desktop" \
  "$RUNTIME/partyops-client.desktop"

bash -n "$RUNTIME/desktop-launcher.sh" "$RUNTIME/install-desktop-shortcut.sh"
grep -q '^Exec=/bin/bash /opt/partyops/desktop-launcher.sh$' \
  "$RUNTIME/partyops.desktop"
grep -q '^TryExec=/bin/bash$' "$RUNTIME/partyops.desktop"

SOURCE_DATE_EPOCH="$(cd "$ROOT" && git log -1 --format=%ct)"
tar --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 \
  --numeric-owner -C "$TEMP_ROOT" -cf - PartyOps |
  zstd -T0 -19 -f -o "$OUTPUT"
zstd -t -- "$OUTPUT"
zstd -dc -- "$OUTPUT" | tar -tf - >"$TEMP_ROOT/archive-list.txt"
grep -qx 'PartyOps/desktop-launcher.sh' "$TEMP_ROOT/archive-list.txt"
mv -f -- "$OUTPUT" "$ARTIFACT"

HASH="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
printf '%s  %s\n' "$HASH" "$(basename "$ARTIFACT")" \
  >"$ROOT/artifacts/SHA256SUMS.$ARCH"
echo "Linux $ARCH 桌面启动入口已刷新：$HASH"
