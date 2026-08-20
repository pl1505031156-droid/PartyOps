#!/bin/sh
set -e

migrate_legacy_host_config() {
  config="$1"
  [ -e "$config" ] || return 0
  if [ -L "$config" ] ||
    ! find "$config" -prune -type f -user root ! -perm /022 -print -quit |
      grep -q .; then
    echo "系统 PartyOps 配置不是 root 独占的普通文件，拒绝以特权迁移：$config" >&2
    exit 2
  fi
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
  configured_host="$(
    sed -n 's/^PARTYOPS_HOST=//p' "$config" |
      tail -n 1 |
      tr -d "'\"[:space:]"
  )"
  [ -n "$configured_host" ] || configured_host=127.0.0.1
  case "$configured_host" in
    127.*|localhost|::1) bind_host=127.0.0.1 ;;
    *) bind_host=0.0.0.0 ;;
  esac
  temporary="${config}.migration.$$"
  cp -p -- "$config" "$temporary"
  changed=0
  if ! grep -q '^PARTYOPS_AGENT_PORT=' "$temporary"; then
    printf 'PARTYOPS_AGENT_PORT=%s\n' "$agent_port" >>"$temporary"
    changed=1
  fi
  if ! grep -q '^PARTYOPS_BIND_HOST=' "$temporary"; then
    printf 'PARTYOPS_BIND_HOST=%s\n' "$bind_host" >>"$temporary"
    changed=1
  fi
  if ! grep -q '^PARTYOPS_ADVERTISE_HOST=' "$temporary"; then
    printf 'PARTYOPS_ADVERTISE_HOST=%s\n' "$configured_host" >>"$temporary"
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
    printf '旧版主机配置已迁移：%s\n' "$config" >&2
  else
    rm -f -- "$temporary"
  fi
}

getent group partyops >/dev/null || groupadd --system partyops
id partyops >/dev/null 2>&1 ||
  useradd --system --gid partyops --home-dir /var/lib/partyops --shell /sbin/nologin partyops
install -d -o partyops -g partyops -m 0750 /var/lib/partyops
install -d -o root -g partyops -m 0750 /etc/partyops
# 包管理器脚本以 root 执行，只处理 root 管理的系统配置。用户模式旧配置由
# 用户自己的启动器迁移，绝不在安装阶段扫描或改写任意家目录。
migrate_legacy_host_config /etc/partyops/partyops.env
if [ -f /opt/partyops/update-public-key.txt ]; then
  install -o root -g root -m 0644 /opt/partyops/update-public-key.txt \
    /etc/partyops/update-public-key
fi
if [ -r /etc/partyops/partyops.env ]; then
  awk -F= '/^(PARTYOPS_HOST|PARTYOPS_BIND_HOST|PARTYOPS_ADVERTISE_HOST|PARTYOPS_PORT|PARTYOPS_TLS_ENABLED)=/ {print}' \
    /etc/partyops/partyops.env >/etc/partyops/desktop.env
  chown root:root /etc/partyops/desktop.env
  chmod 0644 /etc/partyops/desktop.env
fi
systemctl daemon-reload >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
  gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
[ ! -x /opt/partyops/install-desktop-shortcut.sh ] ||
  /opt/partyops/install-desktop-shortcut.sh >/dev/null 2>&1 || true
