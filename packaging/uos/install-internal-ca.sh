#!/usr/bin/env bash
set -euo pipefail

# 仅安装 PartyOps 动态生成的内部根证书。此脚本由桌面向导通过 pkexec
# 调用，避免业务服务或浏览器进程长期持有 root 权限。
[[ "$(id -u)" -eq 0 ]] || {
  echo "需要管理员权限安装 PartyOps 内部 CA。" >&2
  exit 2
}

DESKTOP_USER=""
if [[ "${1:-}" == "--desktop-user" ]]; then
  [[ $# -ge 3 ]] || {
    echo "缺少桌面账号或 CA 文件。" >&2
    exit 2
  }
  DESKTOP_USER="$2"
  shift 2
fi
[[ $# -eq 1 ]] || {
  echo "用法：install-internal-ca.sh [--desktop-user 用户名] <ca.pem>" >&2
  exit 2
}

if [[ -n "$DESKTOP_USER" ]]; then
  [[ "$DESKTOP_USER" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "桌面账号格式无效。" >&2
    exit 2
  }
  PASSWD_ENTRY="$(getent passwd "$DESKTOP_USER" || true)"
  [[ -n "$PASSWD_ENTRY" ]] || {
    echo "找不到指定的桌面账号。" >&2
    exit 2
  }
  IFS=: read -r _ _ DESKTOP_UID DESKTOP_GID _ DESKTOP_HOME _ <<<"$PASSWD_ENTRY"
  [[ "$DESKTOP_UID" =~ ^[0-9]+$ && "$DESKTOP_UID" -ge 1000 ]] || {
    echo "指定账号不是日常桌面账号。" >&2
    exit 2
  }
  case "$DESKTOP_HOME" in
    /home/*|/data/home/*) ;;
    *)
      echo "桌面账号主目录不在允许范围。" >&2
      exit 2
      ;;
  esac
fi

SOURCE="$(readlink -f -- "$1")"
case "$SOURCE" in
  /home/*/.config/partyops/pki/ca.pem|\
  /data/home/*/.config/partyops/pki/ca.pem|\
  /home/*/.local/share/partyops/secrets/pki/ca.pem|\
  /data/home/*/.local/share/partyops/secrets/pki/ca.pem|\
  /var/lib/partyops/secrets/pki/ca.pem)
    ;;
  *)
    echo "CA 文件不在 PartyOps 受管目录，拒绝安装。" >&2
    exit 3
    ;;
esac
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || {
  echo "CA 文件不存在或是符号链接。" >&2
  exit 3
}
command -v openssl >/dev/null 2>&1 || {
  echo "系统缺少 openssl，无法验证 CA。" >&2
  exit 4
}
openssl x509 -in "$SOURCE" -noout -checkend 86400 >/dev/null
SUBJECT="$(openssl x509 -in "$SOURCE" -noout -subject -nameopt RFC2253)"
[[ "$SUBJECT" == *"CN=PartyOps Local Root"* ]] || {
  echo "证书主题不是 PartyOps Local Root。" >&2
  exit 4
}
openssl x509 -in "$SOURCE" -noout -text |
  grep -q "CA:TRUE" || {
    echo "证书不是有效 CA。" >&2
    exit 4
  }
CA_FINGERPRINT="$(
  openssl x509 -in "$SOURCE" -noout -fingerprint -sha256 |
    cut -d= -f2 |
    tr -d '[:space:]'
)"
[[ -n "$CA_FINGERPRINT" ]] || {
  echo "无法计算 PartyOps 内部 CA 指纹。" >&2
  exit 4
}

TARGET="/usr/local/share/ca-certificates/partyops-internal-ca.crt"
install -o root -g root -m 0644 "$SOURCE" "$TARGET"
update-ca-certificates >/dev/null
openssl verify -CApath /etc/ssl/certs "$SOURCE" >/dev/null || {
  echo "系统证书库未能验证 PartyOps 内部 CA。" >&2
  exit 5
}

# Chromium、统信浏览器等版本可能使用当前桌面账号的 NSS 证书库，
# 因此在 certutil 可用时同步导入同一根证书。此处只写固定昵称，
# 不读取或修改其他证书条目。
if [[ -n "$DESKTOP_USER" ]]; then
  if command -v certutil >/dev/null 2>&1; then
    NSS_DIR="$DESKTOP_HOME/.pki/nssdb"
    install -d -o "$DESKTOP_UID" -g "$DESKTOP_GID" -m 0700 "$NSS_DIR"
    if [[ ! -f "$NSS_DIR/cert9.db" ]]; then
      runuser -u "$DESKTOP_USER" -- env HOME="$DESKTOP_HOME" \
        certutil -N --empty-password -d "sql:$NSS_DIR" >/dev/null || {
          echo "无法初始化当前用户的浏览器证书库，请关闭浏览器后重试。" >&2
          exit 6
        }
    fi
    runuser -u "$DESKTOP_USER" -- env HOME="$DESKTOP_HOME" \
      certutil -D -d "sql:$NSS_DIR" -n "PartyOps Local Root" \
      >/dev/null 2>&1 || true
    runuser -u "$DESKTOP_USER" -- env HOME="$DESKTOP_HOME" \
      certutil -A -d "sql:$NSS_DIR" -n "PartyOps Local Root" \
      -t "C,," -i "$SOURCE" >/dev/null || {
        echo "无法写入当前用户的浏览器证书库，请关闭浏览器后重试。" >&2
        exit 6
      }
    runuser -u "$DESKTOP_USER" -- env HOME="$DESKTOP_HOME" \
      certutil -L -d "sql:$NSS_DIR" -n "PartyOps Local Root" \
      >/dev/null || {
        echo "浏览器证书库未能验证 PartyOps 内部 CA。" >&2
        exit 6
      }
    echo "PartyOps 内部 CA 已安装到系统和当前用户浏览器证书库。"
  else
    echo "PartyOps 内部 CA 已安装到系统证书库；未检测到 certutil，浏览器将使用系统信任库。"
  fi

  # 固定位置的指纹标记供桌面启动器判断是否已经完成当前 CA 的信任安装。
  # 标记不包含私钥，也不授予额外权限；CA 轮换后指纹变化会触发重新安装。
  MARKER_DIR="$DESKTOP_HOME/.config/partyops/pki"
  MARKER="$MARKER_DIR/ca-trusted.sha256"
  MARKER_TEMP="$MARKER.tmp.$$"
  install -d -o "$DESKTOP_UID" -g "$DESKTOP_GID" -m 0700 "$MARKER_DIR"
  printf '%s\n' "$CA_FINGERPRINT" >"$MARKER_TEMP"
  chown "$DESKTOP_UID:$DESKTOP_GID" "$MARKER_TEMP"
  chmod 0600 "$MARKER_TEMP"
  mv -f -- "$MARKER_TEMP" "$MARKER"
else
  echo "PartyOps 内部 CA 已安装到系统证书库。"
fi
