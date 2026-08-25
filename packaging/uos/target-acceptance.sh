#!/usr/bin/env bash
set -euo pipefail

URL="${1:-https://127.0.0.1:18765}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$ROOT/artifacts/uos-target-acceptance.txt"
mkdir -p "$ROOT/artifacts"

{
  echo "PartyOps UOS 目标机验收"
  date -Iseconds
  uname -a
  case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "不支持的处理器架构：$(uname -m)" >&2; exit 2 ;;
  esac
  echo "architecture=$ARCH"
  VERSION="${PARTYOPS_VERSION:-1.4.5-rc.3}"
  PACKAGE_VERSION="${PARTYOPS_PACKAGE_VERSION:-1.4.5~rc.3}"
  INSTALLED_VERSION="$(dpkg-query -W -f='${Version}' partyops)"
  INSTALLED_ARCH="$(dpkg-query -W -f='${Architecture}' partyops)"
  echo "installed_version=$INSTALLED_VERSION"
  echo "installed_architecture=$INSTALLED_ARCH"
  test "$INSTALLED_VERSION" = "$PACKAGE_VERSION"
  test "$INSTALLED_ARCH" = "$ARCH"
  test -x /opt/partyops/desktop-launcher.sh
  test -f /usr/share/applications/partyops.desktop
  test -x /opt/partyops/open-local-file.sh
  test -f /usr/share/applications/partyops-file.desktop
  test -f /usr/share/applications/partyops-client.desktop
  test -x /opt/partyops/llama-server
  LD_LIBRARY_PATH=/opt/partyops /opt/partyops/llama-server --version >/dev/null
  grep -q '^Exec=/bin/bash /opt/partyops/desktop-launcher.sh$' \
    /usr/share/applications/partyops.desktop
  DEB="$ROOT/artifacts/PartyOps_1.4.5-rc.3_linux_${ARCH}.deb"
  if [[ -f "$DEB" ]]; then
    test "$(dpkg-deb -f "$DEB" Architecture)" = "$ARCH"
    test "$(dpkg-deb -f "$DEB" Version)" = "$PACKAGE_VERSION"
    (cd "$ROOT/artifacts" && sha256sum -c "SHA256SUMS.$ARCH")
  fi
  ldd --version 2>&1 | sed -n '1p'
  if command -v python3.11 >/dev/null 2>&1; then
    python3.11 --version
  elif [[ -x "$ROOT/.partyops-python/bin/python3.11" ]]; then
    "$ROOT/.partyops-python/bin/python3.11" --version
  else
    echo "Python 3.11 仅用于构建，目标运行时已由 PyInstaller 内置。"
  fi
  if systemctl is-active --quiet partyops; then
    echo "launch_mode=systemd"
    systemctl is-enabled partyops
    systemctl show partyops \
      --property=ActiveState,SubState,NRestarts,Restart,RestartUSec \
      --no-pager
  else
    echo "launch_mode=user-session"
  fi
  test -x /opt/partyops/ocr/bin/tesseract
  test -f /opt/partyops/ocr/tessdata/chi_sim.traineddata
  LD_LIBRARY_PATH=/opt/partyops/ocr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} \
    TESSDATA_PREFIX=/opt/partyops/ocr/tessdata \
    /opt/partyops/ocr/bin/tesseract --list-langs 2>/dev/null | grep -Fx "chi_sim"
  df -h /var/lib/partyops

  CURL_ARGS=(-fsS)
  if [[ "$URL" == https://* && -f /var/lib/partyops/secrets/pki/ca.pem ]]; then
    CURL_ARGS+=(--cacert /var/lib/partyops/secrets/pki/ca.pem)
  fi
  for attempt in 1 2 3; do
    HEALTH="$(curl "${CURL_ARGS[@]}" "$URL/api/v1/health")"
    echo "health_attempt_${attempt}=$HEALTH"
    grep -q '"status":"ok"' <<<"$HEALTH"
    grep -q '"safe_version":true' <<<"$HEALTH"
    grep -q '"fts5":true' <<<"$HEALTH"
    [[ "$attempt" -eq 3 ]] || sleep 2
  done

  INDEX="$(curl "${CURL_ARGS[@]}" "$URL/")"
  grep -q '党建智办' <<<"$INDEX"
  BOOTSTRAP="$(curl "${CURL_ARGS[@]}" "$URL/api/v1/bootstrap/status")"
  echo "$BOOTSTRAP"
  grep -q '"configured":true' <<<"$BOOTSTRAP"
  echo
  echo "RESULT=passed"
} | tee "$RESULT"

echo "验收记录：$RESULT"
