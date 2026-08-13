#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS="$ROOT/artifacts"
DESKTOP_USER="${PARTYOPS_DESKTOP_USER:-}"
FORCE_REBUILD="${PARTYOPS_FORCE_REBUILD:-0}"

while (($# > 0)); do
  case "$1" in
    --desktop-user)
      [[ $# -ge 2 ]] || { echo "--desktop-user 缺少用户名。" >&2; exit 2; }
      DESKTOP_USER="$2"
      shift 2
      ;;
    --rebuild)
      FORCE_REBUILD="1"
      shift
      ;;
    -h|--help)
      echo "用法：bash install.sh [--desktop-user 日常桌面用户名] [--rebuild]"
      exit 0
      ;;
    *)
      echo "未知参数：$1" >&2
      exit 2
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  CURRENT_USER="$(id -un)"
  if [[ -n "$DESKTOP_USER" && "$DESKTOP_USER" != "$CURRENT_USER" ]]; then
    echo "普通用户只能为自己安装桌面入口。" >&2
    exit 2
  fi
else
  CURRENT_USER="$DESKTOP_USER"
  if [[ -z "$CURRENT_USER" && -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    CURRENT_USER="$SUDO_USER"
  fi
  if [[ -z "$CURRENT_USER" ]] && command -v loginctl >/dev/null 2>&1; then
    while read -r uid user; do
      if [[ "$uid" =~ ^[0-9]+$ ]] && ((uid >= 1000)) && [[ "$user" != "nobody" ]]; then
        CURRENT_USER="$user"
        break
      fi
    done < <(loginctl list-users --no-legend 2>/dev/null || true)
  fi
fi

if [[ -z "$CURRENT_USER" || ! "$CURRENT_USER" =~ ^[A-Za-z0-9._-]+$ ]] ||
  ! getent passwd "$CURRENT_USER" >/dev/null; then
  echo "无法识别日常桌面账号。" >&2
  echo "请执行：bash install.sh --desktop-user 实际用户名" >&2
  exit 2
fi

CURRENT_GROUP="$(id -gn "$CURRENT_USER")"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

mkdir -p "$ARTIFACTS"
exec > >(tee "$ARTIFACTS/one-click-install.log") 2>&1

CURRENT_STAGE="启动安装器"
report_failure() {
  local status=$?
  trap - ERR
  echo
  echo "安装未完成，失败阶段：$CURRENT_STAGE" >&2
  echo "完整日志：$ARTIFACTS/one-click-install.log" >&2
  echo "诊断命令：tail -n 120 '$ARTIFACTS/one-click-install.log'" >&2
  exit "$status"
}
run_stage() {
  CURRENT_STAGE="$1"
  shift
  echo
  echo "---- $CURRENT_STAGE ----"
  "$@"
}
verify_build_kit() {
  local manifest="$ROOT/BUILD-KIT-SHA256SUMS"
  [[ -f "$manifest" ]] || {
    echo "缺少 ZIP 内置完整性清单：$manifest" >&2
    echo "请重新下载完整的 PartyOps UOS 单文件 ZIP，勿从旧目录覆盖解压。" >&2
    return 2
  }
  command -v sha256sum >/dev/null 2>&1 || {
    echo "系统缺少 sha256sum，无法在安装前自动验证单文件套件。" >&2
    return 2
  }
  (cd "$ROOT" && sha256sum -c "BUILD-KIT-SHA256SUMS")
}
verify_artifacts() {
  (cd "$ARTIFACTS" && sha256sum -c "SHA256SUMS.$ARCH")
}
verify_deb() {
  local package_name
  package_name="$(basename "$DEB")"
  local expected
  expected="$(awk -v name="$package_name" '$2 == name || $2 == ("*" name) { print; exit }' \
    "$ARTIFACTS/SHA256SUMS.$ARCH")"
  [[ -n "$expected" ]] || {
    echo "校验清单中没有 $package_name。" >&2
    return 2
  }
  (cd "$ARTIFACTS" && printf '%s\n' "$expected" | sha256sum -c -)
}
verify_installed_package() {
  local installed_version installed_arch updater_deadline
  installed_version="$(dpkg-query -W -f='${Version}' partyops)"
  installed_arch="$(dpkg-query -W -f='${Architecture}' partyops)"
  [[ "$installed_version" == "$VERSION" ]] || {
    echo "安装后版本不一致：期望 $VERSION，实际 $installed_version" >&2
    return 2
  }
  [[ "$installed_arch" == "$ARCH" ]] || {
    echo "安装后架构不一致：期望 $ARCH，实际 $installed_arch" >&2
    return 2
  }
  [[ -x /opt/partyops/desktop-launcher.sh ]] || {
    echo "安装后缺少桌面启动程序。" >&2
    return 2
  }
  [[ -f /usr/share/applications/partyops.desktop ]] || {
    echo "安装后缺少应用菜单入口。" >&2
    return 2
  }
  [[ -x /opt/partyops/open-local-file.sh ]] &&
    [[ -f /usr/share/applications/partyops-file.desktop ]] || {
    echo "安装后缺少系统默认程序文件打开助手。" >&2
    return 2
  }
  [[ -f /usr/share/applications/partyops-client.desktop ]] || {
    echo "安装后缺少本机共享目录协议助手。" >&2
    return 2
  }
  for executable in partyops partyops-client partyops-wizard partyops-updater; do
    [[ -x "/opt/partyops/$executable" ]] || {
      echo "安装后缺少核心程序：/opt/partyops/$executable" >&2
      return 2
    }
  done
  systemctl is-enabled --quiet partyops-updater.service || {
    echo "系统内更新助手未启用，已拒绝把本机标记为安装成功。" >&2
    return 2
  }
  updater_deadline=$((SECONDS + 30))
  while ((SECONDS < updater_deadline)); do
    systemctl is-active --quiet partyops-updater.service && break
    sleep 1
  done
  if ! systemctl is-active --quiet partyops-updater.service; then
    echo "系统内更新助手未能启动；以后将无法在界面内升级。" >&2
    journalctl -u partyops-updater -n 80 --no-pager >&2 || true
    return 2
  fi
}
install_or_upgrade_package() {
  local installed_version=""
  if dpkg-query -W -f='${Version}' partyops >/dev/null 2>&1; then
    installed_version="$(dpkg-query -W -f='${Version}' partyops)"
  fi
  if [[ "$installed_version" == "$VERSION" ]]; then
    echo "检测到同版本 $VERSION，将执行修复性重装并保留现有业务数据。"
    as_root apt-get install --reinstall -y "$DEB"
  else
    as_root apt-get install -y "$DEB"
  fi
}
trap report_failure ERR

echo "========================================"
echo "  党建智办（PartyOps）一键安装"
echo "========================================"
run_stage "0/5 自动校验 ZIP 内全部安装输入" verify_build_kit
ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "不支持的处理器架构：$(uname -m)" >&2; exit 2 ;;
  esac
fi
if [[ "$ARCH" != "amd64" && "$ARCH" != "arm64" ]]; then
  echo "党建智办仅支持 amd64 和 ARM64；本机架构为：$ARCH" >&2
  exit 2
fi
VERSION="${PARTYOPS_VERSION:-1.4.3}"
DEB="$ARTIFACTS/partyops_${VERSION}_${ARCH}.deb"

if [[ -f "$DEB" && "$FORCE_REBUILD" != "1" ]]; then
  PACKAGE_ARCH="$(dpkg-deb -f "$DEB" Architecture)"
  if [[ "$PACKAGE_ARCH" != "$ARCH" ]]; then
    echo "安装包架构为 $PACKAGE_ARCH，本机为 $ARCH，已拒绝强制安装。" >&2
    exit 2
  fi
  [[ -f "$ARTIFACTS/SHA256SUMS.$ARCH" ]] || {
    echo "缺少安装包校验清单：$ARTIFACTS/SHA256SUMS.$ARCH" >&2
    exit 2
  }
  run_stage "1/3 校验 $ARCH 离线安装包" verify_deb
else
  OTHER_ARCH="amd64"
  [[ "$ARCH" == "amd64" ]] && OTHER_ARCH="arm64"
  if [[ "$FORCE_REBUILD" != "1" && -f "$ARTIFACTS/partyops_${VERSION}_${OTHER_ARCH}.deb" ]]; then
    echo "本机为 $ARCH，但目录中只有 ${OTHER_ARCH} 安装包。" >&2
    if [[ "$ARCH" == "arm64" ]]; then
      echo "请复制 partyops_${VERSION}_arm64.deb；D2000/8 必须使用 ARM64 包，禁止 --force-architecture。" >&2
    else
      echo "请复制 partyops_${VERSION}_amd64.deb。" >&2
    fi
    exit 2
  fi
  if [[ "$FORCE_REBUILD" == "1" ]]; then
    echo "已启用强制重建，将忽略现有同版本安装包并从当前修复源码重新生成。"
  fi
  echo "未找到本机架构预构建包，将在当前 UOS V20 $ARCH 电脑原生构建。"
  run_stage "1/5 环境检测与补齐" \
    bash "$ROOT/packaging/uos/ensure-build-environment.sh"
  # shellcheck disable=SC1091
  source "$ROOT/.partyops-build.env"
  export PYTHON_BIN PARTYOPS_BUILD_ARCH
  export PARTYOPS_REQUIRE_LOCAL_AI_RUNTIME="${PARTYOPS_REQUIRE_LOCAL_AI_RUNTIME:-1}"
  run_stage "2/5 构建 $ARCH 离线便携运行时" \
    bash "$ROOT/packaging/uos/build-portable.sh"
  run_stage "3/5 生成 $ARCH Debian 安装包" \
    bash "$ROOT/packaging/uos/build-deb.sh"
  run_stage "4/5 校验安装制品" verify_artifacts
fi

run_stage "安装或原位升级党建智办 $VERSION" \
  install_or_upgrade_package
run_stage "创建应用菜单与桌面入口" \
  as_root /opt/partyops/install-desktop-shortcut.sh "$CURRENT_USER"
run_stage "核验安装、应用入口和系统内更新助手" verify_installed_package
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$CURRENT_USER:$CURRENT_GROUP" "$ARTIFACTS"
fi

echo
echo "安装完成：党建智办 $VERSION（$ARCH），应用菜单和桌面入口均已创建。"
echo "首次双击会出现“配置为主机 / 配置为协同终端”向导。"
echo "安装包：$DEB"
if [[ "$(id -u)" -ne 0 && ( -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ) ]]; then
  nohup /opt/partyops/desktop-launcher.sh \
    >"$ARTIFACTS/first-launch.log" 2>&1 &
  echo "首次配置向导已打开。"
else
  echo "请退出 root 后，双击 $CURRENT_USER 桌面上的“党建智办”完成首次配置。"
fi
