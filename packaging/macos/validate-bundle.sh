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

# 先把扫描目标固化到普通文件。Darwin find 通过进程替换向提前退出的
# while 写入时会用“stdout: Undefined error: 0”掩盖真正失败点；固定清单
# 既避免 EPIPE，也让架构、依赖、签名三项检查针对完全相同的文件集合。
SCAN_LIST="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/partyops-bundle-files.XXXXXX")"
WIZARD_REPORT="${SCAN_LIST}.wizard.json"
cleanup() {
  /bin/rm -f "$SCAN_LIST" "$WIZARD_REPORT"
}
trap cleanup EXIT
/usr/bin/find "$APP_PATH/Contents" -type f -print0 >"$SCAN_LIST"

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
OFFICE_APP="$APP_PATH/Contents/Resources/office-runtime/LibreOffice.app"
OFFICE_ENTRY="$OFFICE_APP/Contents/MacOS/soffice"
OFFICE_COMPAT_ENTRY="$APP_PATH/Contents/Resources/office-runtime/program/soffice"
if [[ ! -d "$OFFICE_APP" ]] || [[ ! -f "$OFFICE_APP/Contents/Info.plist" ]] ||
  [[ ! -x "$OFFICE_ENTRY" ]] || [[ ! -x "$OFFICE_COMPAT_ENTRY" ]]; then
  printf '%s\n' '[MACOS_OFFICE_BUNDLE_INCOMPLETE] 公文转换运行时未保留完整 LibreOffice.app 签名边界。' >&2
  exit 2
fi

bad_architecture=''
bad_dependency=''
bad_deployment_target=''
team_ids=''
while IFS= read -r -d '' candidate; do
  description="$(/usr/bin/file -b "$candidate" 2>/dev/null || true)"
  [[ "$description" == *Mach-O* ]] || continue
  if [[ "$description" != *"$EXPECTED_ARCH"* ]]; then
    bad_architecture="$candidate: $description"
    break
  fi
  # otool -L 会把 LC_ID_DYLIB 也列为“依赖”。LibreOfficePython.framework
  # 的上游 ID 带构建目录，但 ID 本身不会在加载当前文件时访问该目录；把它
  # 当作外部依赖会误拒绝可重定位的官方 App。直接解析加载命令，只检查真正
  # 会参与运行时解析的 dylib 和 rpath，同时把命中的具体路径写入诊断。
  external_dependency="$(
    /usr/bin/otool -l "$candidate" 2>/dev/null | /usr/bin/awk '
      $1 == "cmd" { command = $2; next }
      command == "LC_RPATH" && $1 == "path" { print $2; command = ""; next }
      command ~ /^LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD|LOAD_UPWARD)_DYLIB$/ &&
        $1 == "name" { print $2; command = ""; next }
    ' | /usr/bin/grep -E '(/opt/homebrew|/usr/local|/Users/|\.build-macos)' |
      /usr/bin/head -n 1 || true
  )"
  if [[ -n "$external_dependency" ]]; then
    bad_dependency="$candidate -> $external_dependency"
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
    # 在未显式设置 UTF-8 locale 的 Darwin bash 中，紧邻变量名的全角
    # 括号会被旧版词法器误并入参数名，触发 set -u。使用花括号和 ASCII
    # 分隔符，确保安装器校验在 Finder、终端与 GitHub runner 中一致。
    bad_deployment_target="${candidate}: min macOS ${deployment_target} (发布基线为 11.0)"
    break
  fi
  # 逐个检查嵌套 Mach-O 的签名身份，避免 Python.framework、扩展和主入口
  # 混用 Team ID。未签名候选允许 ad-hoc（TeamIdentifier=not set）。
  identity="$(
    /usr/bin/codesign --display --verbose=4 "$candidate" 2>&1 |
      /usr/bin/awk -F= '/TeamIdentifier=/{print $2; exit}' || true
  )"
  if [[ -n "$identity" && "$identity" != 'not set' ]]; then
    if [[ -z "$team_ids" ]]; then team_ids="$identity"; elif [[ "$team_ids" != "$identity" ]]; then
      printf '[MACOS_TEAM_ID_MISMATCH] %s 的 Team ID 为 %s，已发现 %s。\n' "$candidate" "$identity" "$team_ids" >&2
      exit 2
    fi
  fi
done <"$SCAN_LIST"

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

run_bundle_selftest() {
  local error_code="$1" label="$2"
  shift 2
  printf '[MACOS_SELFTEST_BEGIN] %s\n' "$label"
  if "$@"; then
    printf '[MACOS_SELFTEST_PASSED] %s\n' "$label"
    return 0
  else
    local status=$?
    printf '[%s] %s失败，退出码 %s。\n' "$error_code" "$label" "$status" >&2
    if [[ "$error_code" == 'MACOS_PACKAGE_SELFTEST_FAILED' ]]; then
      printf '%s\n' '[MACOS_NATIVE_FAILURE_DIAGNOSTICS_BEGIN]' >&2
      /usr/bin/codesign --verify --deep --strict --verbose=4 "$OFFICE_APP" >&2 || true
      /usr/bin/codesign --display --verbose=4 --entitlements :- "$OFFICE_ENTRY" >&2 || true
      /usr/bin/otool -L "$OFFICE_ENTRY" >&2 || true
      /usr/bin/log show --last 3m --style compact \
        --predicate 'process == "soffice" OR process == "partyops" OR eventMessage CONTAINS[c] "LibreOffice"' \
        2>/dev/null | /usr/bin/tail -n 120 >&2 || true
      printf '%s\n' '[MACOS_NATIVE_FAILURE_DIAGNOSTICS_END]' >&2
    fi
    return "$status"
  fi
}

run_bundle_selftest MACOS_DESKTOP_SELFTEST_FAILED '桌面启动器自检' \
  "$APP_PATH/Contents/MacOS/partyops-desktop" --self-test
run_bundle_selftest MACOS_LAUNCH_AGENT_SELFTEST_FAILED 'LaunchAgent 自检' \
  "$APP_PATH/Contents/MacOS/partyops-launch-agent" --mode personal --self-test
if ! run_bundle_selftest MACOS_WIZARD_SELFTEST_FAILED '配置向导图形运行时自检' \
  /usr/bin/env "PARTYOPS_WIZARD_SELFTEST_REPORT=$WIZARD_REPORT" \
  "$APP_PATH/Contents/MacOS/partyops-wizard" --self-test; then
  [[ ! -s "$WIZARD_REPORT" ]] || /bin/cat "$WIZARD_REPORT" >&2
  exit 2
fi
run_bundle_selftest MACOS_PACKAGE_SELFTEST_FAILED '完整随包运行时自检' \
  "$APP_PATH/Contents/MacOS/partyops" --package-self-test
printf 'PartyOps macOS %s 应用包原生自检通过。\n' "$EXPECTED_ARCH"
