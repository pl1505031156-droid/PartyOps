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

required=(partyops-desktop partyops-desktop-bin partyops partyops-client partyops-wizard partyops-launch-agent partyops-updater)
casefold_names='|'
for name in "${required[@]}"; do
  folded="$(printf '%s' "$name" | /usr/bin/tr '[:upper:]' '[:lower:]')"
  case "$casefold_names" in
    *"|$folded|"*)
      printf '[MACOS_CASEFOLD_NAME_COLLISION] 可执行入口仅大小写不同：%s\n' "$name" >&2
      exit 2
      ;;
  esac
  casefold_names="${casefold_names}${folded}|"
  candidate="$APP_PATH/Contents/MacOS/$name"
  if [[ ! -f "$candidate" ]] || [[ ! -x "$candidate" ]]; then
    printf '[MACOS_BUNDLE_INCOMPLETE] 缺少可执行入口：%s\n' "$name" >&2
    exit 2
  fi
done

RUNTIME="$APP_PATH/Contents/MacOS"
UPDATE_PUBLIC_KEY="$APP_PATH/Contents/Resources/update-public-key.txt"
bundle_identifier="$(/usr/bin/plutil -extract CFBundleIdentifier raw \
  "$APP_PATH/Contents/Info.plist" 2>/dev/null || true)"
bundle_executable="$(/usr/bin/plutil -extract CFBundleExecutable raw \
  "$APP_PATH/Contents/Info.plist" 2>/dev/null || true)"
if [[ "$bundle_identifier" != 'cn.partyops.desktop' ]]; then
  printf '[MACOS_BUNDLE_IDENTIFIER_INVALID] Bundle ID 无效：%s\n' "$bundle_identifier" >&2
  exit 2
fi
if [[ "$bundle_executable" != 'partyops-desktop' ]]; then
  printf '[MACOS_BUNDLE_EXECUTABLE_INVALID] 主入口无效：%s\n' "$bundle_executable" >&2
  exit 2
fi
if [[ ! -f "$UPDATE_PUBLIC_KEY" ]] ||
  [[ "$(wc -c <"$UPDATE_PUBLIC_KEY" | tr -d ' ')" -gt 4096 ]]; then
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
bad_deployment_target=''
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
  deployment_target="$(/usr/bin/otool -l "$candidate" 2>/dev/null | /usr/bin/awk '
    $1 == "cmd" && $2 == "LC_BUILD_VERSION" { section = "build"; next }
    $1 == "cmd" && $2 == "LC_VERSION_MIN_MACOSX" { section = "legacy"; next }
    section == "build" && $1 == "minos" { print $2; exit }
    section == "legacy" && $1 == "version" { print $2; exit }
  ')"
  if [[ -z "$deployment_target" ]]; then
    bad_deployment_target="$candidate: 未找到 macOS 最低版本载入命令"
    break
  fi
  deployment_major="${deployment_target%%.*}"
  deployment_tail="${deployment_target#*.}"
  deployment_minor="${deployment_tail%%.*}"
  if ! [[ "$deployment_major" =~ ^[0-9]+$ && "$deployment_minor" =~ ^[0-9]+$ ]] ||
    ((deployment_major > 11)) ||
    ((deployment_major == 11 && deployment_minor > 0)); then
    bad_deployment_target="$candidate: min macOS $deployment_target（发布基线为 11.0）"
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
if [[ -n "$bad_deployment_target" ]]; then
  printf '[MACOS_DEPLOYMENT_TARGET_TOO_NEW] 应用包包含无法在 macOS 11 启动的组件：%s\n' "$bad_deployment_target" >&2
  exit 2
fi

"$APP_PATH/Contents/MacOS/partyops-desktop" --self-test
"$APP_PATH/Contents/MacOS/partyops-launch-agent" --mode personal --self-test
"$APP_PATH/Contents/MacOS/partyops-wizard" --self-test
"$APP_PATH/Contents/MacOS/partyops" --package-self-test
printf 'PartyOps macOS %s 应用包原生自检通过。\n' "$EXPECTED_ARCH"
