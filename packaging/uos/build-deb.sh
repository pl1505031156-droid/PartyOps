#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS="$ROOT/artifacts"
mkdir -p "$ROOT/.build-uos"
BUILD="$(mktemp -d "$ROOT/.build-uos/deb.XXXXXX")"
PKG="$BUILD/deb-root"
VERSION="${PARTYOPS_VERSION:-1.4.3}"
ARCH="${PARTYOPS_BUILD_ARCH:-$(dpkg --print-architecture 2>/dev/null || true)}"

[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] || {
  echo "仅支持 amd64 与 arm64，本机架构为：${ARCH:-unknown}" >&2
  exit 2
}

cleanup_build() {
  local status=$?
  trap - EXIT
  case "$BUILD" in
    "$ROOT/.build-uos/deb."*) rm -rf -- "$BUILD" ;;
    *) echo "Debian 包暂存目录不在预期位置，拒绝清理：$BUILD" >&2 ;;
  esac
  exit "$status"
}
trap cleanup_build EXIT

PORTABLE="$ARTIFACTS/PartyOps-uos-$ARCH.tar.zst"
[[ -f "$PORTABLE" ]] || {
  echo "缺少 $ARCH 便携包，请先在同架构 UOS V20 运行 build-portable.sh。" >&2
  exit 2
}

mkdir -p "$PKG/DEBIAN" "$PKG/opt/partyops" "$PKG/etc/partyops" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" \
  "$PKG/lib/systemd/system" "$PKG/usr/share/polkit-1/actions"
tar --zstd -xf "$PORTABLE" -C "$BUILD"
cp -a "$BUILD/PartyOps/." "$PKG/opt/partyops/"
cp "$ROOT/packaging/uos/partyops.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/uos/partyops-file.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/uos/partyops-client.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/uos/partyops.svg" \
  "$PKG/usr/share/icons/hicolor/scalable/apps/partyops.svg"
cp "$ROOT/packaging/uos/partyops.service" "$PKG/lib/systemd/system/"
cp "$ROOT/packaging/uos/partyops-updater.service" "$PKG/lib/systemd/system/"
cp "$ROOT/packaging/uos/cn.partyops.update.policy" \
  "$PKG/usr/share/polkit-1/actions/"
cat > "$PKG/DEBIAN/control" <<EOF
Package: partyops
Version: $VERSION
Section: office
Priority: optional
Architecture: $ARCH
Maintainer: PartyOps Local
Depends: libc6, curl, xdg-utils, policykit-1
Description: 党建智办——基层党建工作闭环协同系统
 离线、单主机数据源、支持局域网协同和中文 OCR。
EOF
cat > "$PKG/DEBIAN/preinst" <<'EOF'
#!/bin/sh
set -e

is_partyops_process() {
  pid="$1"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ -r "/proc/$pid/stat" ] || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [ -n "$state" ] && [ "$state" != "Z" ] || return 1
  executable="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  case "$executable" in
    /opt/partyops/partyops|/opt/partyops/PartyOps/partyops) return 0 ;;
    *) return 1 ;;
  esac
}

record_stopped_pid() {
  pid="$1"
  grep -qx "$pid" /run/partyops/stopped-pids 2>/dev/null ||
    printf '%s\n' "$pid" >> /run/partyops/stopped-pids
}

install -d -m 0755 /run/partyops
: > /run/partyops/restart-users
: > /run/partyops/stopped-pids
if systemctl is-active --quiet partyops 2>/dev/null; then
  : > /run/partyops/restart-after-upgrade
fi
service_pid="$(systemctl show partyops -p MainPID --value 2>/dev/null || true)"
if is_partyops_process "$service_pid"; then
  record_stopped_pid "$service_pid"
fi
systemctl stop partyops >/dev/null 2>&1 || true
if [ -x /opt/partyops/stop.sh ]; then
  getent passwd | awk -F: '$3 >= 1000 && $1 != "nobody" {print $1}' |
    while IFS= read -r user; do
      home="$(getent passwd "$user" | cut -d: -f6)"
      [ -n "$home" ] || continue
      config="$home/.config/partyops/partyops.env"
      [ -f "$config" ] || continue
      pid="$(runuser -u "$user" -- env HOME="$home" \
        XDG_CONFIG_HOME="$home/.config" sh -c '
          set -a
          . "$HOME/.config/partyops/partyops.env"
          set +a
          data_dir="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
          [ -f "$data_dir/partyops.pid" ] && cat "$data_dir/partyops.pid"
        ' 2>/dev/null || true)"
      case "$pid" in
        ''|*[!0-9]*) pid="" ;;
      esac
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        if is_partyops_process "$pid"; then
          printf '%s\n' "$user" >> /run/partyops/restart-users
          record_stopped_pid "$pid"
          runuser -u "$user" -- env HOME="$home" \
            XDG_CONFIG_HOME="$home/.config" /opt/partyops/stop.sh \
            >/dev/null 2>&1 || true
          is_partyops_process "$pid" && kill -TERM "$pid" 2>/dev/null || true
        else
          echo "拒绝终止身份不匹配的进程 $pid，正在清理失效 PID 文件。" >&2
          runuser -u "$user" -- env HOME="$home" \
            XDG_CONFIG_HOME="$home/.config" sh -c '
              set -a
              . "$HOME/.config/partyops/partyops.env"
              set +a
              data_dir="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
              rm -f "$data_dir/partyops.pid"
            ' >/dev/null 2>&1 || true
        fi
      fi
    done
fi

# 早期版本可能不是由 dpkg 登记为“upgrade”，且旧 Uvicorn 可能因长连接
# 延迟退出。先等待优雅停止，再只对经过 /proc 身份校验的 PartyOps 进程
# 执行强制终止，既避免覆盖运行中的旧程序，也不会误杀 PID 已复用的进程。
attempt=0
while [ "$attempt" -lt 20 ]; do
  running=""
  if [ -s /run/partyops/stopped-pids ]; then
    while IFS= read -r pid; do
      is_partyops_process "$pid" && running="$running $pid"
    done < /run/partyops/stopped-pids
  fi
  [ -z "$running" ] && break
  attempt=$((attempt + 1))
  sleep 1
done
if [ -n "${running:-}" ]; then
  echo "旧版 PartyOps 未在 20 秒内优雅退出，正在终止已验证进程：$running" >&2
  for pid in $running; do
    is_partyops_process "$pid" && kill -KILL "$pid" 2>/dev/null || true
  done
fi
attempt=0
while [ "$attempt" -lt 5 ]; do
  running=""
  if [ -s /run/partyops/stopped-pids ]; then
    while IFS= read -r pid; do
      is_partyops_process "$pid" && running="$running $pid"
    done < /run/partyops/stopped-pids
  fi
  [ -z "$running" ] && break
  attempt=$((attempt + 1))
  sleep 1
done
if [ -n "${running:-}" ]; then
  echo "已验证的旧 PartyOps 进程仍无法停止：$running" >&2
  echo "进程可能处于不可中断 I/O 状态，已停止安装以保护业务数据。" >&2
  exit 1
fi
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

migrate_legacy_host_config() {
  config="$1"
  [ -f "$config" ] && [ -w "$config" ] || return 0
  configured_port="$(
    sed -n 's/^PARTYOPS_PORT=//p' "$config" |
      tail -n 1 |
      tr -d "'\"[:space:]"
  )"
  case "$configured_port" in
    ''|*[!0-9]*) configured_port=18765 ;;
  esac
  if [ "$configured_port" -lt 1024 ] || [ "$configured_port" -gt 65534 ]; then
    configured_port=18765
  fi
  agent_port=$((configured_port + 1))
  temporary="${config}.migration.$$"
  cp -p -- "$config" "$temporary"
  changed=0
  if ! grep -q '^PARTYOPS_AGENT_PORT=' "$temporary"; then
    printf 'PARTYOPS_AGENT_PORT=%s\n' "$agent_port" >>"$temporary"
    changed=1
  fi
  if ! grep -qx 'PARTYOPS_TLS_ENABLED=true' "$temporary"; then
    if grep -q '^PARTYOPS_TLS_ENABLED=' "$temporary"; then
      sed -i 's/^PARTYOPS_TLS_ENABLED=.*/PARTYOPS_TLS_ENABLED=true/' "$temporary"
    else
      printf 'PARTYOPS_TLS_ENABLED=true\n' >>"$temporary"
    fi
    changed=1
  fi
  if [ "$changed" -eq 1 ]; then
    mv -f -- "$temporary" "$config"
    echo "旧版主机配置已迁移：启用 HTTPS 和设备安全端口。"
  else
    rm -f -- "$temporary"
  fi
}

getent group partyops >/dev/null || groupadd --system partyops
id partyops >/dev/null 2>&1 || useradd --system --gid partyops --home /var/lib/partyops --shell /usr/sbin/nologin partyops
install -d -o partyops -g partyops -m 0750 /var/lib/partyops
install -d -o root -g partyops -m 0750 /etc/partyops
for config in \
  /etc/partyops/partyops.env \
  /home/*/.config/partyops/partyops.env \
  /data/home/*/.config/partyops/partyops.env; do
  migrate_legacy_host_config "$config"
done
if [ -f /opt/partyops/update-public-key.txt ]; then
  install -o root -g root -m 0644 /opt/partyops/update-public-key.txt \
    /etc/partyops/update-public-key
fi
if [ -r /etc/partyops/partyops.env ]; then
  awk -F= '
    /^(PARTYOPS_HOST|PARTYOPS_PORT|PARTYOPS_TLS_ENABLED)=/ {print}
  ' /etc/partyops/partyops.env > /etc/partyops/desktop.env
  chown root:root /etc/partyops/desktop.env
  chmod 0644 /etc/partyops/desktop.env
fi
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable partyops-updater.service >/dev/null 2>&1 || true
systemctl start partyops-updater.service >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
/opt/partyops/install-desktop-shortcut.sh >/dev/null 2>&1 || true
if [ -f /run/partyops/restart-after-upgrade ]; then
  rm -f /run/partyops/restart-after-upgrade
  systemctl restart partyops >/dev/null 2>&1 || true
fi
if [ -s /run/partyops/restart-users ]; then
  while IFS= read -r user; do
    case "$user" in
      *[!A-Za-z0-9._-]*|'') continue ;;
    esac
    home="$(getent passwd "$user" | cut -d: -f6)"
    [ -n "$home" ] || continue
    runuser -u "$user" -- env HOME="$home" \
      XDG_CONFIG_HOME="$home/.config" /opt/partyops/start.sh \
      >/dev/null 2>&1 || true
  done < /run/partyops/restart-users
fi
rm -f /run/partyops/restart-users /run/partyops/stopped-pids
EOF
cat > "$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
  systemctl stop partyops >/dev/null 2>&1 || true
fi
EOF
cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl disable --now partyops-updater.service >/dev/null 2>&1 || true
if [ "$1" = "purge" ]; then
  echo "业务数据仍保留在 /var/lib/partyops；为避免误删，卸载脚本不会自动清除。" >&2
fi
EOF
chmod 0755 "$PKG/DEBIAN/preinst" "$PKG/DEBIAN/postinst" \
  "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"

DEB="$ARTIFACTS/partyops_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$PKG" "$DEB"
(cd "$ARTIFACTS" && sha256sum \
  "PartyOps-uos-$ARCH.tar.zst" \
  "partyops_${VERSION}_${ARCH}.deb" \
  > "SHA256SUMS.$ARCH")
echo "Debian 包已生成：$DEB"
