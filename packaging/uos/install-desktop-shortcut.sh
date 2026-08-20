#!/usr/bin/env bash
set -euo pipefail

DESKTOP_ENTRY="/usr/share/applications/partyops.desktop"
FILE_HANDLER="/usr/share/applications/partyops-file.desktop"
CLIENT_HANDLER="/usr/share/applications/partyops-client.desktop"
TARGET_USER="${1:-}"

if [[ ! -f "$DESKTOP_ENTRY" ]]; then
  echo "未找到党建智办应用入口：$DESKTOP_ENTRY" >&2
  exit 2
fi

if [[ -z "$TARGET_USER" && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  TARGET_USER="$SUDO_USER"
fi
if [[ -z "$TARGET_USER" && -n "${PKEXEC_UID:-}" ]]; then
  TARGET_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
if [[ -z "$TARGET_USER" && "$(id -u)" -ne 0 ]]; then
  TARGET_USER="$(id -un)"
fi
if [[ -z "$TARGET_USER" ]] && command -v loginctl >/dev/null 2>&1; then
  while read -r uid user; do
    if [[ "$uid" =~ ^[0-9]+$ ]] && ((uid >= 1000)) && [[ "$user" != "nobody" ]]; then
      TARGET_USER="$user"
      break
    fi
  done < <(loginctl list-users --no-legend 2>/dev/null || true)
fi

if [[ -z "$TARGET_USER" ]]; then
  echo "未检测到已登录桌面用户；应用菜单入口已安装，首次登录后仍可从启动器打开。"
  exit 0
fi
if [[ ! "$TARGET_USER" =~ ^[A-Za-z0-9._-]+$ ]] || ! getent passwd "$TARGET_USER" >/dev/null; then
  echo "桌面用户无效：$TARGET_USER" >&2
  exit 2
fi

USER_RECORD="$(getent passwd "$TARGET_USER")"
TARGET_UID="$(cut -d: -f3 <<<"$USER_RECORD")"
USER_HOME="$(cut -d: -f6 <<<"$USER_RECORD")"
USER_CONFIG_HOME="$USER_HOME/.config"
USER_RUNTIME_DIR="/run/user/$TARGET_UID"
USER_BUS_ADDRESS=""
if [[ -d "$USER_RUNTIME_DIR" && -S "$USER_RUNTIME_DIR/bus" ]]; then
  USER_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus"
fi

run_as_target_user() {
  local -a desktop_environment=(
    "HOME=$USER_HOME"
    "XDG_CONFIG_HOME=$USER_CONFIG_HOME"
  )
  if [[ -d "$USER_RUNTIME_DIR" ]]; then
    desktop_environment+=("XDG_RUNTIME_DIR=$USER_RUNTIME_DIR")
  fi
  if [[ -n "$USER_BUS_ADDRESS" ]]; then
    desktop_environment+=("DBUS_SESSION_BUS_ADDRESS=$USER_BUS_ADDRESS")
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    runuser -u "$TARGET_USER" -- env "${desktop_environment[@]}" "$@"
  elif [[ "$TARGET_USER" == "$(id -un)" ]]; then
    env "${desktop_environment[@]}" "$@"
  else
    echo "普通用户只能为自己执行桌面集成。" >&2
    return 2
  fi
}

DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(run_as_target_user xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ -z "$DESKTOP_DIR" || "$DESKTOP_DIR" == "$USER_HOME" ]]; then
  if [[ -d "$USER_HOME/桌面" ]]; then
    DESKTOP_DIR="$USER_HOME/桌面"
  else
    DESKTOP_DIR="$USER_HOME/Desktop"
  fi
fi
case "$DESKTOP_DIR" in
  "$USER_HOME"|"$USER_HOME"/*) ;;
  *)
    echo "桌面目录不在用户主目录内，拒绝写入：$DESKTOP_DIR" >&2
    exit 2
    ;;
esac

if [[ "$(id -u)" -ne 0 && "$TARGET_USER" != "$(id -un)" ]]; then
  echo "普通用户只能为自己创建桌面快捷方式。" >&2
  exit 2
fi
# Desktop 目录及其内容完全由日常用户控制。即使安装过程中目录被替换为
# 符号链接或 xdg-user-dir 返回了恶意路径，也只能以该用户自身权限写入，
# 禁止 root 跟随用户可控路径形成安装时本地提权。
run_as_target_user install -d -m 0755 "$DESKTOP_DIR"
run_as_target_user install -m 0755 \
  "$DESKTOP_ENTRY" "$DESKTOP_DIR/党建智办.desktop"

TRUST_STATUS="executable"
if command -v gio >/dev/null 2>&1; then
  if run_as_target_user gio set \
      "$DESKTOP_DIR/党建智办.desktop" metadata::trusted true >/dev/null 2>&1; then
    TRUST_STATUS="metadata::trusted"
  elif [[ -z "$USER_BUS_ADDRESS" ]]; then
    echo "未发现 $TARGET_USER 的桌面 D-Bus 会话；快捷方式已设为可执行，登录后也可从应用菜单打开。" >&2
  else
    echo "麒麟桌面可信元数据写入失败；快捷方式已设为可执行，应用菜单入口不受影响。" >&2
  fi
fi
[[ -f "$FILE_HANDLER" ]] || {
  echo "未找到党建智办文件打开助手：$FILE_HANDLER" >&2
  exit 3
}
[[ -f "$CLIENT_HANDLER" ]] || {
  echo "未找到党建智办本机共享助手：$CLIENT_HANDLER" >&2
  exit 3
}
command -v xdg-mime >/dev/null 2>&1 || {
  echo "系统缺少 xdg-mime，无法注册默认程序打开功能。" >&2
  exit 3
}
run_as_target_user xdg-mime default \
  partyops-file.desktop x-scheme-handler/partyops-file
run_as_target_user xdg-mime default \
  partyops-client.desktop x-scheme-handler/partyops-client
REGISTERED_HANDLER="$(
  run_as_target_user xdg-mime query default \
    x-scheme-handler/partyops-file 2>/dev/null || true
)"
[[ "$REGISTERED_HANDLER" == "partyops-file.desktop" ]] || {
  echo "文件打开助手注册失败，当前处理器：${REGISTERED_HANDLER:-未设置}" >&2
  exit 3
}
REGISTERED_CLIENT_HANDLER="$(
  run_as_target_user xdg-mime query default \
    x-scheme-handler/partyops-client 2>/dev/null || true
)"
[[ "$REGISTERED_CLIENT_HANDLER" == "partyops-client.desktop" ]] || {
  echo "本机共享助手注册失败，当前处理器：${REGISTERED_CLIENT_HANDLER:-未设置}" >&2
  exit 3
}
echo "桌面快捷方式已创建：$DESKTOP_DIR/党建智办.desktop"
echo "桌面快捷方式信任状态：$TRUST_STATUS"
echo "默认程序打开助手已为 $TARGET_USER 注册并验证。"
echo "本机共享目录助手已为 $TARGET_USER 注册并验证。"
