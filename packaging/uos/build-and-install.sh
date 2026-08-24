#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_IP="${1:-}"
PORT="${2:-18765}"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ -z "$HOST_IP" ]]; then
  echo "用法：bash packaging/uos/build-and-install.sh <本机局域网IP> [端口]" >&2
  exit 2
fi
if [[ ! "$HOST_IP" =~ ^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9.]+$ ]]; then
  echo "必须填写 RFC1918 局域网 IPv4 地址，禁止公网地址和 0.0.0.0。" >&2
  exit 2
fi
if ! hostname -I | tr ' ' '\n' | grep -Fxq "$HOST_IP"; then
  echo "地址 $HOST_IP 不属于本机当前网卡。" >&2
  exit 2
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1024 || PORT > 65535)); then
  echo "端口必须在 1024—65535 之间。" >&2
  exit 2
fi

bash "$ROOT/packaging/uos/ensure-build-environment.sh"
# shellcheck disable=SC1091
source "$ROOT/.partyops-build.env"
export PYTHON_BIN PARTYOPS_BUILD_ARCH
ARCH="${PARTYOPS_BUILD_ARCH:-$(dpkg --print-architecture)}"

for command in gcc g++ ar ranlib make unzip tar gzip zstd sha256sum curl \
  dpkg-deb systemctl file readelf ldd; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "缺少目标机构建/安装命令：$command" >&2
    exit 2
  }
done
"$PYTHON_BIN" -c "import venv" >/dev/null 2>&1 || {
  echo "选定的 Python 3.11 缺少 venv 模块，无法创建离线构建环境。" >&2
  exit 2
}
AVAILABLE_KIB="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
if [[ ! "$AVAILABLE_KIB" =~ ^[0-9]+$ ]] || ((AVAILABLE_KIB < 4 * 1024 * 1024)); then
  echo "构建目录可用空间不足 4 GiB，请释放空间后重试。" >&2
  exit 2
fi

export PARTYOPS_REQUIRE_LOCAL_AI_RUNTIME="${PARTYOPS_REQUIRE_LOCAL_AI_RUNTIME:-1}"
bash "$ROOT/packaging/uos/build-portable.sh"
bash "$ROOT/packaging/uos/build-deb.sh"
(cd "$ROOT/artifacts" && sha256sum -c "SHA256SUMS.$ARCH")

VERSION="${PARTYOPS_VERSION:-1.4.5-rc.2}"
as_root apt-get install -y "$ROOT/artifacts/PartyOps_1.4.5-rc.2_linux_${ARCH}.deb"
CONFIG="$(mktemp)"
trap 'rm -f "$CONFIG"' EXIT
cat > "$CONFIG" <<EOF
PARTYOPS_MODE=host
PARTYOPS_ENVIRONMENT=production
PARTYOPS_HOST=$HOST_IP
PARTYOPS_BIND_HOST=0.0.0.0
PARTYOPS_ADVERTISE_HOST=$HOST_IP
PARTYOPS_PORT=$PORT
PARTYOPS_AGENT_PORT=$((PORT + 1))
PARTYOPS_DATA_DIR=/var/lib/partyops
PARTYOPS_STRICT_SQLITE=true
PARTYOPS_SEED_DEMO=false
PARTYOPS_TLS_ENABLED=true
PARTYOPS_BACKUP_HOUR=18
PARTYOPS_BACKUP_MINUTE=30
PARTYOPS_BACKUP_DAILY_KEEP=14
PARTYOPS_BACKUP_WEEKLY_KEEP=8
EOF
as_root install -o root -g partyops -m 0640 "$CONFIG" /etc/partyops/partyops.env
DESKTOP_CONFIG="$(mktemp)"
trap 'rm -f "$CONFIG" "$DESKTOP_CONFIG"' EXIT
grep -E '^(PARTYOPS_HOST|PARTYOPS_BIND_HOST|PARTYOPS_ADVERTISE_HOST|PARTYOPS_PORT|PARTYOPS_TLS_ENABLED)=' "$CONFIG" > "$DESKTOP_CONFIG"
as_root install -o root -g root -m 0644 "$DESKTOP_CONFIG" /etc/partyops/desktop.env
as_root systemctl enable --now partyops
as_root systemctl enable --now partyops-updater.service

UPDATER_DEADLINE=$((SECONDS + 30))
while ((SECONDS < UPDATER_DEADLINE)); do
  systemctl is-active --quiet partyops-updater.service && break
  sleep 1
done
if ! systemctl is-active --quiet partyops-updater.service; then
  echo "系统内更新助手未能启动；安装已停止在验收阶段，业务数据未被删除。" >&2
  as_root journalctl -u partyops-updater -n 80 --no-pager >&2 || true
  exit 2
fi

HEALTH_TIMEOUT_SECONDS="${PARTYOPS_HEALTH_TIMEOUT_SECONDS:-180}"
[[ "$HEALTH_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] &&
  ((HEALTH_TIMEOUT_SECONDS >= 30 && HEALTH_TIMEOUT_SECONDS <= 900)) || {
  echo "安装后健康检查等待时间必须是 30—900 秒的整数。" >&2
  exit 2
}
HEALTH_FILE="$ROOT/artifacts/installed-health.json"
HEALTH_STARTED_AT="$SECONDS"
HEALTH_DEADLINE=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
while ((SECONDS < HEALTH_DEADLINE)); do
  if ! systemctl is-active --quiet partyops; then
    echo "PartyOps 服务在健康检查完成前退出。" >&2
    as_root journalctl -u partyops -n 120 --no-pager >&2 || true
    exit 2
  fi
  if [[ -f /var/lib/partyops/secrets/pki/ca.pem ]] &&
    curl --cacert /var/lib/partyops/secrets/pki/ca.pem -fsS \
      --connect-timeout 1 --max-time 3 \
      "https://$HOST_IP:$PORT/api/v1/health" >"$HEALTH_FILE.tmp" 2>/dev/null; then
    mv "$HEALTH_FILE.tmp" "$HEALTH_FILE"
    break
  fi
  ELAPSED=$((SECONDS - HEALTH_STARTED_AT))
  if ((ELAPSED > 0 && ELAPSED % 15 == 0)); then
    echo "安装后服务仍在启动，已等待 ${ELAPSED} 秒……"
  fi
  sleep 1
done
rm -f "$HEALTH_FILE.tmp"
if [[ ! -s "$HEALTH_FILE" ]]; then
  echo "PartyOps 服务在 ${HEALTH_TIMEOUT_SECONDS} 秒内未通过健康检查。" >&2
  as_root journalctl -u partyops -n 120 --no-pager >&2 || true
  exit 2
fi
grep -q '"safe_version":true' "$HEALTH_FILE"
grep -q '"fts5":true' "$HEALTH_FILE"

echo "党建智办已安装并启动：https://$HOST_IP:$PORT"
echo "请在浏览器打开上述地址，完成首次管理员配置。"
