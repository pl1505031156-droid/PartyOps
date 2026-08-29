#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORMAT="${1:-}"
ARCH="${PARTYOPS_BUILD_ARCH:-}"
DEB_VERSION="1.4.5~rc.6"
RPM_VERSION="1.4.5"
RPM_RELEASE="0.rc.6.1"
ARTIFACTS="$ROOT/artifacts"

if [[ -z "${PYTHON_BIN:-}" && -f "$ROOT/.partyops-build.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.partyops-build.env"
fi
if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$(command -v python3.11 || command -v python3 || true)"
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "未找到可用 Python 3.11，请先运行 ensure-build-environment.sh。" >&2
  exit 2
fi

"$PYTHON_BIN" "$ROOT/scripts/verify-version-consistency.py" \
  --root "$ROOT" --expected "1.4.5-rc.6"
"$PYTHON_BIN" "$ROOT/scripts/verify-full-function-gate.py" verify --root "$ROOT"

[[ "$FORMAT" == "deb" || "$FORMAT" == "rpm" ]] || {
  echo "用法：build-native.sh deb|rpm（通过 PARTYOPS_BUILD_ARCH 指定 amd64/arm64）" >&2
  exit 2
}
if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "不支持的架构：$(uname -m)" >&2; exit 2 ;;
  esac
fi
[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] || {
  echo "仅支持 amd64/arm64：$ARCH" >&2
  exit 2
}
EXPECTED_MACHINE=x86_64
[[ "$ARCH" == arm64 ]] && EXPECTED_MACHINE=aarch64
EXPECTED_OFFICE_PATTERN='x86-64|x86_64'
[[ "$ARCH" == arm64 ]] && EXPECTED_OFFICE_PATTERN='aarch64|ARM64'
OFFICE_RUNTIME="${PARTYOPS_OFFICE_RUNTIME:-$ROOT/vendor/linux/libreoffice-headless-$ARCH}"
OFFICE_BINARY="$OFFICE_RUNTIME/program/soffice.bin"
if [[ ! -x "$OFFICE_RUNTIME/program/soffice" || ! -f "$OFFICE_BINARY" ||
  ! -f "$OFFICE_RUNTIME/SOURCE.json" || ! -d "$OFFICE_RUNTIME/licenses" ]]; then
  echo "[OFFICE_RUNTIME_MISSING] 缺少 $ARCH 经许可审计的 LibreOffice headless 运行时、来源清单或许可证。" >&2
  exit 2
fi
file "$OFFICE_BINARY" | grep -Eq "$EXPECTED_OFFICE_PATTERN" || {
  echo "[OFFICE_RUNTIME_ARCH_MISMATCH] LibreOffice 运行时与 $ARCH 不一致。" >&2
  exit 2
}
while IFS= read -r -d '' link; do
  resolved="$(readlink -f -- "$link" 2>/dev/null || true)"
  case "$resolved" in
    "$OFFICE_RUNTIME"/*) ;;
    *) echo "[OFFICE_RUNTIME_SYMLINK_INVALID] LibreOffice 运行时包含越界或损坏链接：$link" >&2; exit 2 ;;
  esac
done < <(find "$OFFICE_RUNTIME" -type l -print0)
[[ "$(uname -s)" == Linux ]] || {
  echo "原生包只能在 Linux manylinux2014 构建环境生成。" >&2
  exit 2
}
HOST_MACHINE="$(uname -m)"
if [[ "$HOST_MACHINE" != "$EXPECTED_MACHINE" &&
  "${PARTYOPS_ALLOW_CROSS_PACKAGE:-0}" != "1" ]]; then
  echo "目标为 $EXPECTED_MACHINE、当前为 $HOST_MACHINE；仅封装已在目标架构自检通过的载荷时，才可显式设置 PARTYOPS_ALLOW_CROSS_PACKAGE=1。" >&2
  exit 2
fi
GLIBC="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
[[ "$GLIBC" == 2.17 ]] || {
  echo "正式包要求 glibc 2.17 构建基线，当前为 $GLIBC；拒绝生成伪兼容制品。" >&2
  exit 2
}

WHEELHOUSE="$ROOT/vendor/wheels/$ARCH"
shopt -s nullglob
PACKAGING_WHEELS=("$WHEELHOUSE"/packaging-*.whl)
shopt -u nullglob
[[ "${#PACKAGING_WHEELS[@]}" -eq 1 ]] || {
  echo "离线 wheelhouse 必须且只能包含一个 packaging wheel。" >&2
  exit 2
}
# 基础构建解释器刻意不预装第三方包。直接从已经纳入 vendor 哈希门禁的
# 纯 Python wheel 加载 packaging，避免联网安装和构建机全局环境漂移。
PYTHONPATH="${PACKAGING_WHEELS[0]}${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" "$ROOT/scripts/validate-uos-wheelhouse.py" \
  --architecture "$ARCH" \
  --wheelhouse "$WHEELHOUSE" \
  --requirements "$ROOT/backend/requirements.txt" \
    "$ROOT/backend/requirements-local-ai.txt" \
    "$ROOT/packaging/uos/requirements-build.txt"

PORTABLE="$ARTIFACTS/PartyOps-linux-$ARCH.tar.zst"
[[ -f "$PORTABLE" ]] || {
  echo "缺少严格模式便携载荷：$PORTABLE" >&2
  exit 2
}
BUILD_PARENT="${PARTYOPS_NATIVE_BUILD_BASE:-$ROOT/.build-linux}"
mkdir -p "$BUILD_PARENT" "$ARTIFACTS"
BUILD_PARENT="$(cd "$BUILD_PARENT" && pwd -P)"
# DEB/RPM 元数据要求真实 POSIX 权限。WSL DrvFS 未启用 metadata 时会把
# DEBIAN/control 等文件和目录全部呈现为 0777，dpkg-deb 会直接拒绝；
# 先实测权限语义，不满足时把暂存树放到 Linux 本地文件系统。
MODE_PROBE="$(mktemp -d "$BUILD_PARENT/.mode-probe.XXXXXX")"
touch "$MODE_PROBE/file"
chmod 0700 "$MODE_PROBE"
chmod 0600 "$MODE_PROBE/file"
if [[ "$(stat -c '%a' "$MODE_PROBE")" != "700" ||
  "$(stat -c '%a' "$MODE_PROBE/file")" != "600" ]]; then
  PROBE_PARENT="$BUILD_PARENT"
  BUILD_PARENT="${TMPDIR:-/tmp}/partyops-native-build"
  mkdir -p "$BUILD_PARENT"
  chmod 0700 "$BUILD_PARENT"
  echo "原生包暂存目录 $PROBE_PARENT 不保存 POSIX 权限；改用 $BUILD_PARENT。"
fi
rm -f -- "$MODE_PROBE/file"
rmdir -- "$MODE_PROBE"
BUILD_PARENT="$(cd "$BUILD_PARENT" && pwd -P)"
BUILD="$(mktemp -d "$BUILD_PARENT/native.XXXXXX")"
cleanup() {
  status=$?
  trap - EXIT
  case "$BUILD" in
    "$BUILD_PARENT/native."*) rm -rf -- "$BUILD" ;;
    *) echo "拒绝清理异常构建目录：$BUILD" >&2 ;;
  esac
  exit "$status"
}
trap cleanup EXIT
PKG="$BUILD/root"
PORTABLE_COPY="$BUILD/portable.tar.zst"
command -v zstd >/dev/null 2>&1 || {
  echo "缺少 zstd，无法安全验证 Linux 便携载荷。" >&2
  exit 2
}
SOURCE_SHA="$(sha256sum "$PORTABLE" | awk '{print $1}')"
cp -- "$PORTABLE" "$PORTABLE_COPY"
COPY_SHA="$(sha256sum "$PORTABLE_COPY" | awk '{print $1}')"
[[ "$SOURCE_SHA" == "$COPY_SHA" ]] || {
  echo "Linux 便携载荷复制期间发生变化，拒绝继续构建。" >&2
  exit 2
}
zstd -dc -- "$PORTABLE_COPY" |
  "$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py" --expected-root PartyOps
mkdir -p "$PKG/opt/partyops" "$PKG/etc/partyops" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" \
  "$PKG/lib/systemd/system" "$PKG/usr/share/polkit-1/actions"
zstd -dc -- "$PORTABLE_COPY" |
  tar --extract --file - --directory "$BUILD" \
    --no-same-owner --no-same-permissions
cp -a "$BUILD/PartyOps/." "$PKG/opt/partyops/"
# 原生包对旧便携载荷再执行一次默认拒绝权限收敛。只有固定应用入口可以
# 执行；共享库、WASM、图片和许可证必须保持 0644，避免麒麟安全中心把
# libgcc_s.so.1 等运行库误判为自启动程序。
find "$PKG/opt/partyops" -type f -exec chmod 0644 {} +
chmod 0755 \
  "$PKG/opt/partyops/partyops" \
  "$PKG/opt/partyops/partyops-client" \
  "$PKG/opt/partyops/partyops-wizard" \
  "$PKG/opt/partyops/partyops-updater" \
  "$PKG/opt/partyops/start.sh" \
  "$PKG/opt/partyops/stop.sh" \
  "$PKG/opt/partyops/desktop-launcher.sh" \
  "$PKG/opt/partyops/open-local-file.sh" \
  "$PKG/opt/partyops/install-desktop-shortcut.sh" \
  "$PKG/opt/partyops/install-internal-ca.sh" \
  "$PKG/opt/partyops/ocr/bin/tesseract"
if [[ -f "$PKG/opt/partyops/llama-server" ]]; then
  chmod 0755 "$PKG/opt/partyops/llama-server"
fi
# LibreOffice 自带多个受许可约束的本机入口和动态库；在 PartyOps 基础
# 载荷完成权限收敛后再复制，保留其发行方所需的可执行位与相对布局。
cp -a "$OFFICE_RUNTIME" "$PKG/opt/partyops/office-runtime"
# WSL DrvFS 未启用 metadata 时会把 LibreOffice 的所有普通文件呈现为
# 0777。可执行入口仍由下方 office-runtime 白名单保留，但共享库本身只
# 需要读取权限；在封包副本中显式清除其执行位，避免国产系统安全中心把
# lib*.so 误判成可直接启动程序。不得修改经哈希审计的 vendor 源目录。
find "$PKG/opt/partyops/office-runtime" -type f -name '*.so*' \
  -exec chmod 0644 {} +
EXPECTED_PAYLOAD_PATTERN='x86-64'
[[ "$ARCH" == arm64 ]] && EXPECTED_PAYLOAD_PATTERN='ARM aarch64'
file "$PKG/opt/partyops/partyops" | grep -q "$EXPECTED_PAYLOAD_PATTERN" || {
  echo "便携载荷主程序架构与目标 $ARCH 不一致，拒绝封装。" >&2
  exit 2
}
[[ -s "$PKG/opt/partyops/update-public-key.txt" ]] || {
  echo "便携载荷缺少更新信任公钥，拒绝生成无法应用内升级的正式包。" >&2
  exit 2
}
for desktop_entry in partyops.desktop partyops-file.desktop partyops-client.desktop; do
  # Windows/DrvFS 检出可能带 CRLF。desktop-file-validate 在部分 UOS 版本会
  # 把节名末尾的 CR 当成格式错误，因此封包边界必须强制规范为 UTF-8/LF。
  sed 's/\r$//' "$ROOT/packaging/uos/$desktop_entry" \
    >"$PKG/usr/share/applications/$desktop_entry"
  if LC_ALL=C grep -q "$(printf '\r')" "$PKG/usr/share/applications/$desktop_entry"; then
    echo "桌面入口换行规范化失败：$desktop_entry" >&2
    exit 2
  fi
done
cp "$ROOT/packaging/uos/partyops.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/partyops.svg"
cp "$ROOT/packaging/uos/partyops.service" "$ROOT/packaging/uos/partyops-updater.service" \
  "$PKG/lib/systemd/system/"
cp "$ROOT/packaging/linux/partyops-install-verify.service" \
  "$PKG/lib/systemd/system/"
cp "$ROOT/packaging/uos/cn.partyops.update.policy" "$PKG/usr/share/polkit-1/actions/"
# 源码可能位于不保存 POSIX 权限的 WSL DrvFS；复制后显式收敛静态配置，
# 避免 systemd 单元、桌面入口和 polkit 策略被误标为可执行文件。
chmod 0644 \
  "$PKG/usr/share/applications/partyops.desktop" \
  "$PKG/usr/share/applications/partyops-file.desktop" \
  "$PKG/usr/share/applications/partyops-client.desktop" \
  "$PKG/usr/share/icons/hicolor/scalable/apps/partyops.svg" \
  "$PKG/lib/systemd/system/partyops.service" \
  "$PKG/lib/systemd/system/partyops-updater.service" \
  "$PKG/lib/systemd/system/partyops-install-verify.service" \
  "$PKG/usr/share/polkit-1/actions/cn.partyops.update.policy"
cp "$ROOT/packaging/linux/post-install-selftest.sh" \
  "$ROOT/packaging/linux/post-install-services.sh" \
  "$ROOT/packaging/linux/post-install-verify.sh" \
  "$ROOT/packaging/linux/post-install-transaction.sh" \
  "$PKG/opt/partyops/"
chmod 0755 \
  "$PKG/opt/partyops/post-install-selftest.sh" \
  "$PKG/opt/partyops/post-install-services.sh" \
  "$PKG/opt/partyops/post-install-verify.sh" \
  "$PKG/opt/partyops/post-install-transaction.sh"
while IFS= read -r -d '' executable; do
  case "$executable" in
    "$PKG/opt/partyops/partyops"|"$PKG/opt/partyops/partyops-client"|\
    "$PKG/opt/partyops/partyops-wizard"|"$PKG/opt/partyops/partyops-updater"|\
    "$PKG/opt/partyops/start.sh"|"$PKG/opt/partyops/stop.sh"|\
    "$PKG/opt/partyops/desktop-launcher.sh"|\
    "$PKG/opt/partyops/open-local-file.sh"|\
    "$PKG/opt/partyops/install-desktop-shortcut.sh"|\
    "$PKG/opt/partyops/install-internal-ca.sh"|\
    "$PKG/opt/partyops/ocr/bin/tesseract"|\
    "$PKG/opt/partyops/llama-server"|\
    "$PKG/opt/partyops/post-install-selftest.sh"|\
    "$PKG/opt/partyops/post-install-services.sh"|\
    "$PKG/opt/partyops/post-install-verify.sh"|\
    "$PKG/opt/partyops/post-install-transaction.sh"|\
    "$PKG/opt/partyops/office-runtime/"*) ;;
    *)
      echo "原生包包含未授权的可执行文件：$executable" >&2
      exit 2
      ;;
  esac
done < <(find "$PKG/opt/partyops" -type f -perm /111 -print0)
if find "$PKG/opt/partyops" -type f -name '*.so*' -perm /111 -print -quit | grep -q .; then
  echo "原生包共享库被错误标记为可执行文件，拒绝封装。" >&2
  exit 2
fi
(cd "$PKG/opt/partyops" && find . -type f ! -name release-files.sha256 -print0 | \
  sort -z | xargs -0 sha256sum >release-files.sha256)

if [[ "$FORMAT" == deb ]]; then
  mkdir -p "$PKG/DEBIAN"
  cat >"$PKG/DEBIAN/control" <<EOF
Package: partyops
Version: $DEB_VERSION
Section: office
Priority: optional
Architecture: $ARCH
Maintainer: PartyOps Local
Depends: libc6 (>= 2.17), bash, systemd, util-linux, coreutils, iproute2, curl, xdg-utils, policykit-1
Description: 党建智办 PartyOps 局域网协同系统
 原生离线主机、协同、中文 OCR、语义重排和本地 LLM。
EOF
  cp "$ROOT/packaging/linux/pre-install-stop.sh" "$PKG/DEBIAN/preinst"
  {
    cat "$ROOT/packaging/linux/post-install-configure.sh"
    printf '\n/opt/partyops/post-install-transaction.sh %s deb\n' "$ARCH"
  } >"$PKG/DEBIAN/postinst"
  cat >"$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
systemctl stop partyops.service partyops-updater.service >/dev/null 2>&1 || true
EOF
  cat >"$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
echo "PartyOps 业务数据保留在 /var/lib/partyops，卸载不会自动删除。" >&2
EOF
  chmod 0755 "$PKG/DEBIAN/preinst" "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"
  OUTPUT="$ARTIFACTS/PartyOps_1.4.5-rc.6_linux_${ARCH}.deb"
  if dpkg-deb --help 2>&1 | grep -q -- '--root-owner-group'; then
    dpkg-deb --root-owner-group --build "$PKG" "$OUTPUT"
  else
    # manylinux2014 的 dpkg-deb 早于 --root-owner-group。构建进程本身以
    # 隔离 root 运行时，先逐项证明载荷所有权，再使用兼容参数封装。
    if find "$PKG" \( ! -uid 0 -o ! -gid 0 \) -print -quit | grep -q .; then
      echo "旧版 dpkg-deb 环境中的载荷并非全部 root:root，拒绝封装。" >&2
      exit 2
    fi
    dpkg-deb --build "$PKG" "$OUTPUT"
  fi
else
  RPM_ARCH=x86_64
  [[ "$ARCH" == arm64 ]] && RPM_ARCH=aarch64
  PAYLOAD="$BUILD/partyops-payload.tar.gz"
  tar -czf "$PAYLOAD" -C "$PKG" .
  mkdir -p "$BUILD/rpmbuild/BUILD" "$BUILD/rpmbuild/RPMS" "$BUILD/rpmbuild/SOURCES" \
    "$BUILD/rpmbuild/SPECS" "$BUILD/rpmbuild/SRPMS"
  cp "$PAYLOAD" "$BUILD/rpmbuild/SOURCES/"
  RPM_PRE_SCRIPT="$(sed 's/%/%%/g' "$ROOT/packaging/linux/pre-install-stop.sh")"
  RPM_POST_SCRIPT="$(sed 's/%/%%/g' "$ROOT/packaging/linux/post-install-configure.sh")"
  cat >"$BUILD/rpmbuild/SPECS/partyops.spec" <<EOF
%global __os_install_post %{nil}
Name: partyops
Version: $RPM_VERSION
Release: %{partyops_release}
Summary: 党建智办 PartyOps 局域网协同系统
License: GPL-3.0-or-later AND AGPL-3.0-only
BuildArch: $RPM_ARCH
Requires: glibc >= 2.17, bash, systemd, util-linux, coreutils, iproute, curl, xdg-utils, polkit
Source0: partyops-payload.tar.gz

%description
原生离线主机、协同、中文 OCR、语义重排和本地 LLM。

%prep
%build
%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}

%pre
$RPM_PRE_SCRIPT

%post
$RPM_POST_SCRIPT
/opt/partyops/post-install-transaction.sh $ARCH rpm

%preun
if [ "\$1" -eq 0 ]; then
  systemctl stop partyops.service partyops-updater.service >/dev/null 2>&1 || true
fi

%postun
systemctl daemon-reload >/dev/null 2>&1 || true
if [ "\$1" -eq 0 ]; then
  echo "PartyOps 业务数据保留在 /var/lib/partyops，卸载不会自动删除。" >&2
fi

%files
/opt/partyops
/etc/partyops
/usr/share/applications/partyops.desktop
/usr/share/applications/partyops-file.desktop
/usr/share/applications/partyops-client.desktop
/usr/share/icons/hicolor/scalable/apps/partyops.svg
/usr/share/polkit-1/actions/cn.partyops.update.policy
/lib/systemd/system/partyops.service
/lib/systemd/system/partyops-updater.service
/lib/systemd/system/partyops-install-verify.service
%if 0%{?with_rollback_cache}
%dir %attr(0700,root,root) /var/cache/partyops
%dir %attr(0700,root,root) /var/cache/partyops/update-transactions
/var/cache/partyops/current.rpm
/var/cache/partyops/current.rpm.sha256
%endif
EOF
  # RPM 的脚本阶段拿不到原始安装包路径。先构建一个内容相同、Release
  # 略低且不递归包含自身的回滚包，再把它嵌入正式包。首次升级失败时可
  # 降级到该包；后续成功升级会用刚验证过的正式制品原子更新此缓存。
  # 回滚种子与稳定版保持同一 Version，只降低 RPM Release；这样首次安装
  # 失败可以恢复二进制，同时不会被运行时版本门禁误判成 rc 旧版本。
  SEED_RELEASE="0.rc.2.0"
  rpmbuild \
    --target "$RPM_ARCH" \
    --define "_topdir $BUILD/rpmbuild" \
    --define "partyops_release $SEED_RELEASE" \
    --define "with_rollback_cache 0" \
    -bb "$BUILD/rpmbuild/SPECS/partyops.spec"
  SEED_RPM="$BUILD/rpmbuild/RPMS/$RPM_ARCH/partyops-$RPM_VERSION-$SEED_RELEASE.$RPM_ARCH.rpm"
  [[ -f "$SEED_RPM" ]] || {
    echo "RPM 回滚种子包未生成，拒绝构建不可回滚制品。" >&2
    exit 2
  }
  mkdir -p "$PKG/var/cache/partyops/update-transactions"
  cp "$SEED_RPM" "$PKG/var/cache/partyops/current.rpm"
  sha256sum "$SEED_RPM" | awk '{print $1}' >"$PKG/var/cache/partyops/current.rpm.sha256"
  chmod 0700 "$PKG/var/cache/partyops" "$PKG/var/cache/partyops/update-transactions"
  chmod 0644 "$PKG/var/cache/partyops/current.rpm"
  chmod 0644 "$PKG/var/cache/partyops/current.rpm.sha256"
  tar -czf "$PAYLOAD" -C "$PKG" .
  cp "$PAYLOAD" "$BUILD/rpmbuild/SOURCES/partyops-payload.tar.gz"
  rpmbuild \
    --target "$RPM_ARCH" \
    --define "_topdir $BUILD/rpmbuild" \
    --define "partyops_release $RPM_RELEASE" \
    --define "with_rollback_cache 1" \
    -bb "$BUILD/rpmbuild/SPECS/partyops.spec"
  OUTPUT="$ARTIFACTS/PartyOps-1.4.5-0.rc.6.1.${RPM_ARCH}.rpm"
  cp "$BUILD/rpmbuild/RPMS/$RPM_ARCH/partyops-$RPM_VERSION-$RPM_RELEASE.$RPM_ARCH.rpm" "$OUTPUT"
fi
if [[ "$FORMAT" == deb ]]; then
  PACKAGE_IDENTITY="$(dpkg-deb --field "$OUTPUT" Package Version Architecture | tr '\n' '|')"
  [[ "$PACKAGE_IDENTITY" == "Package: partyops|Version: $DEB_VERSION|Architecture: $ARCH|" ]] || {
    echo "DEB 元数据与冻结版本/架构不一致：$PACKAGE_IDENTITY" >&2
    exit 2
  }
else
  PACKAGE_IDENTITY="$(rpm -qp --queryformat '%{NAME}|%{VERSION}|%{RELEASE}|%{ARCH}' "$OUTPUT")"
  [[ "$PACKAGE_IDENTITY" == "partyops|$RPM_VERSION|$RPM_RELEASE|$RPM_ARCH" ]] || {
    echo "RPM 元数据与冻结版本/架构不一致：$PACKAGE_IDENTITY" >&2
    exit 2
  }
fi
(
  cd "$(dirname "$OUTPUT")"
  output_name="$(basename "$OUTPUT")"
  sha256sum "$output_name" >"$output_name.sha256"
)
echo "原生安装包已生成：$OUTPUT"
