#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "不支持的处理器架构：$(uname -m)" >&2; exit 2 ;;
  esac
fi
case "$ARCH" in
  amd64)
    PYTHON_ARCHIVE="$ROOT/vendor/cpython-3.11.15+20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PYTHON_ARCHIVE_SHA256="0604cd029b142dc223e131f17f5941c0c8d2d5074997c8178b515b19eea2a6c2"
    ;;
  arm64)
    PYTHON_ARCHIVE="$ROOT/vendor/cpython-3.11.15+20260623-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PYTHON_ARCHIVE_SHA256="9ac18c9a761e91e6c6452bc0ef0082922a00a3fdec734555635d57c3169309b7"
    ;;
  *)
    echo "党建智办目前仅支持 amd64 与 arm64，本机为：$ARCH" >&2
    exit 2
    ;;
esac
SYSTEM_DEBS="$ROOT/vendor/system-debs/$ARCH"
[[ -d "$SYSTEM_DEBS" ]] || SYSTEM_DEBS="$ROOT/vendor/system-debs"
MANAGED_PYTHON="$ROOT/.partyops-python"
BUILD_ENV="$ROOT/.partyops-build.env"
PACKAGES=(
  build-essential
  unzip
  tar
  gzip
  zstd
  curl
  dpkg-dev
  file
  binutils
)
COMMANDS=(gcc g++ ar ranlib make unzip tar gzip zstd sha256sum curl dpkg-deb systemctl file readelf ldd)

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "缺少 sudo，请使用管理员账号执行安装。" >&2
    return 2
  fi
}

system_environment_ready() {
  local command
  for command in "${COMMANDS[@]}"; do
    command -v "$command" >/dev/null 2>&1 || return 1
  done
}

select_python() {
  local candidate=""
  if command -v python3.11 >/dev/null 2>&1; then
    candidate="$(command -v python3.11)"
  elif [[ -x "$MANAGED_PYTHON/bin/python3.11" ]]; then
    candidate="$MANAGED_PYTHON/bin/python3.11"
  fi
  if [[ -n "$candidate" ]] &&
    "$candidate" -c 'import ctypes, ssl, venv; import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' \
      >/dev/null 2>&1; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

if ! system_environment_ready; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "检测到系统构建工具缺失，但系统没有 apt-get；请由单位管理员安装：${PACKAGES[*]}" >&2
    exit 2
  fi
  shopt -s nullglob
  LOCAL_DEBS=("$SYSTEM_DEBS"/*.deb)
  shopt -u nullglob
  if ((${#LOCAL_DEBS[@]} > 0)); then
    if [[ ! -f "$SYSTEM_DEBS/SHA256SUMS" ]]; then
      echo "发现离线系统包但缺少 $SYSTEM_DEBS/SHA256SUMS，拒绝安装未校验文件。" >&2
      exit 2
    fi
    (cd "$SYSTEM_DEBS" && sha256sum -c SHA256SUMS)
    echo "正在使用随包离线系统依赖补齐环境……"
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-download \
      "${LOCAL_DEBS[@]}"
  elif [[ "${PARTYOPS_OFFLINE_ONLY:-0}" == "1" ]]; then
    echo "当前要求完全离线安装，但套件未包含与本机 UOS 版本匹配的 system-debs。" >&2
    echo "请先由单位管理员准备 ${SYSTEM_DEBS}，或临时连接可信 UOS 软件源。" >&2
    exit 2
  else
    echo "正在从当前配置的软件源自动补齐 UOS 系统构建工具……"
    as_root apt-get update
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "${PACKAGES[@]}"
  fi
fi

if ! system_environment_ready; then
  echo "自动安装后系统工具仍不完整，请检查编译、压缩和 ELF 检查工具。" >&2
  exit 2
fi

PYTHON_BIN="$(select_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ ! -f "$PYTHON_ARCHIVE" ]]; then
    echo "UOS 软件源没有可用 Python 3.11，套件也缺少独立 Python 制品：$PYTHON_ARCHIVE" >&2
    exit 2
  fi
  echo "$PYTHON_ARCHIVE_SHA256  $PYTHON_ARCHIVE" | sha256sum -c -
  if [[ -e "$MANAGED_PYTHON" ]]; then
    BROKEN_PYTHON="$ROOT/.partyops-python.broken.$(date +%Y%m%d%H%M%S)"
    mv "$MANAGED_PYTHON" "$BROKEN_PYTHON"
    echo "原独立 Python 不完整，已保留为：$BROKEN_PYTHON"
  fi
  PYTHON_TEMP="$(mktemp -d "$ROOT/.partyops-python.XXXXXX")"
  tar -xzf "$PYTHON_ARCHIVE" -C "$PYTHON_TEMP"
  [[ -x "$PYTHON_TEMP/python/bin/python3.11" ]] || {
    echo "独立 Python 制品目录结构不正确。" >&2
    exit 2
  }
  mv "$PYTHON_TEMP/python" "$MANAGED_PYTHON"
  PYTHON_BIN="$MANAGED_PYTHON/bin/python3.11"
fi

"$PYTHON_BIN" -c 'import ctypes, ssl, venv; print("独立 Python：", __import__("sys").version)'
printf 'PYTHON_BIN=%q\n' "$PYTHON_BIN" > "$BUILD_ENV"
printf 'PARTYOPS_BUILD_ARCH=%q\n' "$ARCH" >> "$BUILD_ENV"
echo "UOS 构建环境补齐完成，使用：$PYTHON_BIN"
