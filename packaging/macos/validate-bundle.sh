#!/bin/bash
set -euo pipefail

APP_PATH="${1:-}"
EXPECTED_ARCH="${2:-}"
if [[ "$(uname -s)" != "Darwin" ]]; then
  printf '%s\n' '[MACOS_NATIVE_VALIDATION_REQUIRED] 必须在真实 macOS 上验证应用包。' >&2
  exit 2
fi
if [[ ! -d "$APP_PATH/Contents/MacOS" ]] ||
  [[ ! "$EXPECTED_ARCH" =~ ^(arm64|x86_64)$ ]]; then
  printf '%s\n' '[MACOS_BUNDLE_INVALID] 应用包路径或目标架构无效。' >&2
  exit 2
fi

required=(PartyOps partyops partyops-client partyops-wizard partyops-launch-agent partyops-updater)
for name in "${required[@]}"; do
  candidate="$APP_PATH/Contents/MacOS/$name"
  if [[ ! -f "$candidate" ]] || [[ ! -x "$candidate" ]]; then
    printf '[MACOS_BUNDLE_INCOMPLETE] 缺少可执行入口：%s\n' "$name" >&2
    exit 2
  fi
done

RUNTIME="$APP_PATH/Contents/MacOS"
if [[ ! -f "$RUNTIME/update-public-key.txt" ]] ||
  [[ "$(wc -c <"$RUNTIME/update-public-key.txt" | tr -d ' ')" -gt 4096 ]]; then
  printf '%s\n' '[MACOS_UPDATE_TRUST_ROOT_MISSING] 应用包缺少受控更新根公钥。' >&2
  exit 2
fi

if [[ ! -x "$APP_PATH/Contents/MacOS/tesseract" ]] ||
  [[ ! -f "$APP_PATH/Contents/Resources/ocr/tessdata/chi_sim.traineddata" ]] ||
  [[ ! -f "$APP_PATH/Contents/MacOS/llama-server" ]]; then
  printf '%s\n' '[MACOS_NATIVE_RUNTIME_INCOMPLETE] OCR 或本地 LLM 运行时不完整。' >&2
  exit 2
fi

bad_architecture=''
bad_dependency=''
while IFS= read -r -d '' candidate; do
  description="$(/usr/bin/file -b "$candidate" 2>/dev/null || true)"
  [[ "$description" == *Mach-O* ]] || continue
  if [[ "$description" != *"$EXPECTED_ARCH"* ]]; then
    bad_architecture="$candidate: $description"
    break
  fi
  dependencies="$(/usr/bin/otool -L "$candidate" 2>/dev/null || true)"
  if printf '%s\n' "$dependencies" | /usr/bin/grep -E '(/opt/homebrew|/usr/local|/Users/|\.build-macos)' >/dev/null; then
    bad_dependency="$candidate"
    break
  fi
done < <(/usr/bin/find "$APP_PATH/Contents" -type f -print0)

if [[ -n "$bad_architecture" ]]; then
  printf '[MACOS_ARCH_MISMATCH] 应用包混入错误架构：%s\n' "$bad_architecture" >&2
  exit 2
fi
if [[ -n "$bad_dependency" ]]; then
  printf '[MACOS_EXTERNAL_DEPENDENCY] 应用包仍依赖构建机路径：%s\n' "$bad_dependency" >&2
  exit 2
fi

"$APP_PATH/Contents/MacOS/PartyOps" --self-test
"$APP_PATH/Contents/MacOS/partyops-launch-agent" --mode personal --self-test
"$APP_PATH/Contents/MacOS/partyops-wizard" --self-test
"$APP_PATH/Contents/MacOS/partyops" --package-self-test
printf 'PartyOps macOS %s 应用包原生自检通过。\n' "$EXPECTED_ARCH"
