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
USER_HOME="$(cut -d: -f6 <<<"$USER_RECORD")"
USER_GROUP="$(id -gn "$TARGET_USER")"
USER_CONFIG_HOME="$USER_HOME/.config"

run_as_target_user() {
  if [[ "$(id -u)" -eq 0 ]]; then
    runuser -u "$TARGET_USER" -- env \
      HOME="$USER_HOME" \
      XDG_CONFIG_HOME="$USER_CONFIG_HOME" \
      "$@"
  elif [[ "$TARGET_USER" == "$(id -un)" ]]; then
    env HOME="$USER_HOME" XDG_CONFIG_HOME="$USER_CONFIG_HOME" "$@"
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

if [[ "$(id -u)" -eq 0 ]]; then
  install -d -o "$TARGET_USER" -g "$USER_GROUP" -m 0755 "$DESKTOP_DIR"
  install -o "$TARGET_USER" -g "$USER_GROUP" -m 0755 \
    "$DESKTOP_ENTRY" "$DESKTOP_DIR/党建智办.desktop"
elif [[ "$TARGET_USER" == "$(id -un)" ]]; then
  install -d -m 0755 "$DESKTOP_DIR"
  install -m 0755 "$DESKTOP_ENTRY" "$DESKTOP_DIR/党建智办.desktop"
else
  echo "普通用户只能为自己创建桌面快捷方式。" >&2
  exit 2
fi

if command -v gio >/dev/null 2>&1; then
  run_as_target_user gio set \
    "$DESKTOP_DIR/党建智办.desktop" metadata::trusted true >/dev/null 2>&1 || true
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
echo "默认程序打开助手已为 $TARGET_USER 注册并验证。"
echo "本机共享目录助手已为 $TARGET_USER 注册并验证。"
