#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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
  --root "$ROOT" --expected "1.4.4"
APP_VERSION="1.4.4"
BUILD_PARENT="${PARTYOPS_BUILD_BASE:-$ROOT/.build-uos}"
mkdir -p "$BUILD_PARENT"
BUILD_PARENT="$(cd "$BUILD_PARENT" && pwd -P)"
# Windows 的 WSL DrvFS 在未启用 metadata 时会把所有条目呈现为 0777，
# 即使 chmod 返回成功也不会保存 POSIX 权限。若继续在该目录构建，最终
# TAR 会让普通文档也可执行且全员可写。发布构建必须先实测权限语义；
# 默认目录不具备 POSIX 权限时，自动转到 Linux 本地临时文件系统。
MODE_PROBE="$(mktemp -d "$BUILD_PARENT/.mode-probe.XXXXXX")"
touch "$MODE_PROBE/file"
chmod 0700 "$MODE_PROBE"
chmod 0600 "$MODE_PROBE/file"
if [[ "$(stat -c '%a' "$MODE_PROBE")" != "700" ||
  "$(stat -c '%a' "$MODE_PROBE/file")" != "600" ]]; then
  PROBE_PARENT="$BUILD_PARENT"
  BUILD_PARENT="${TMPDIR:-/tmp}/partyops-build"
  mkdir -p "$BUILD_PARENT"
  chmod 0700 "$BUILD_PARENT"
  echo "构建目录 $PROBE_PARENT 不保存 POSIX 权限；改用 $BUILD_PARENT。"
fi
rm -f -- "$MODE_PROBE/file"
rmdir -- "$MODE_PROBE"
BUILD_PARENT="$(cd "$BUILD_PARENT" && pwd -P)"
BUILD="$(mktemp -d "$BUILD_PARENT/portable.XXXXXX")"
PYI_DIST="$BUILD/pyinstaller-dist"
PYI_WORK="$BUILD/pyinstaller-work"
ARCH="${PARTYOPS_BUILD_ARCH:-$(dpkg --print-architecture 2>/dev/null || true)}"
if [[ -z "$ARCH" ]]; then
  case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
  esac
fi
[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] || {
  echo "仅支持 amd64 与 arm64，本机为：${ARCH:-unknown}" >&2
  exit 2
}
WHEELHOUSE="$ROOT/vendor/wheels/$ARCH"
if [[ "$ARCH" == "amd64" && ! -d "$WHEELHOUSE" ]]; then
  WHEELHOUSE="$ROOT/vendor/wheels"
fi
LOCAL_AI_RUNTIME="$ROOT/vendor/local-ai/$ARCH"
LOCAL_AI_ARCHIVE="$LOCAL_AI_RUNTIME/llama-runtime.tar.gz"
OCR_RUNTIME="$ROOT/vendor/ocr/$ARCH"
OCR_ARCHIVE="$OCR_RUNTIME/tesseract-runtime.tar.gz"
REQUIRE_LOCAL_AI_RUNTIME=1
SQLITE_ARCHIVE="$ROOT/vendor/sqlite-amalgamation-3510300.zip"
PYSQLITE_ARCHIVE="$ROOT/vendor/pysqlite3-0.5.4.tar.gz"
PID=""

cleanup_build() {
  local status=$?
  trap - EXIT
  if [[ -n "$PID" ]]; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  case "$BUILD" in
    "$BUILD_PARENT/portable."*)
      rm -rf -- "$BUILD"
      ;;
    *)
      echo "构建暂存目录不在预期位置，拒绝清理：$BUILD" >&2
      ;;
  esac
  exit "$status"
}
trap cleanup_build EXIT

if ! "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import sys
import sysconfig

shared = int(sysconfig.get_config_var("Py_ENABLE_SHARED") or 0)
library = str(sysconfig.get_config_var("LDLIBRARY") or "")
candidate = Path(sys.base_prefix) / "lib" / library
raise SystemExit(0 if shared == 1 and library and candidate.is_file() else 1)
PY
then
  echo "发布冻结要求带共享 libpython 的 Python 3.11；当前解释器仅支持静态嵌入，无法生成可启动的 PyInstaller 载荷。" >&2
  exit 2
fi

EXPECTED_MACHINE="x86_64"
[[ "$ARCH" == "arm64" ]] && EXPECTED_MACHINE="aarch64"
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "$EXPECTED_MACHINE" ]]; then
  echo "必须在 UOS V20 $ARCH 目标机原生构建；当前为 $(uname -m)。" >&2
  exit 2
fi
GLIBC_VERSION="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
if ! "$PYTHON_BIN" - "$GLIBC_VERSION" <<'PY'
import sys

current = tuple(int(part) for part in sys.argv[1].split(".")[:2])
raise SystemExit(0 if current == (2, 17) else 1)
PY
then
  echo "正式构建必须在 glibc 2.17 的 manylinux2014 工具链中运行；当前为 $GLIBC_VERSION。" >&2
  exit 2
fi
for required in "$SQLITE_ARCHIVE" "$PYSQLITE_ARCHIVE" "$WHEELHOUSE" "$OCR_ARCHIVE"; do
  [[ -e "$required" ]] || { echo "缺少离线构建输入：$required" >&2; exit 2; }
done
LOCAL_EMBEDDING_AVAILABLE=1
for wheel_prefix in numpy onnxruntime tokenizers; do
  if ! compgen -G "$WHEELHOUSE/${wheel_prefix}-*.whl" >/dev/null; then
    echo "缺少 $ARCH 本地语义离线轮子 ${wheel_prefix}，严格模式拒绝构建。" >&2
    exit 2
  fi
done
LOCAL_LLM_AVAILABLE=1
case "$ARCH" in
  amd64) EXPECTED_LLAMA_SHA256="dfb51ab3c3d0ca61054a4c2df37fc27d037f9f2c3284300ef743875fd8731d9f" ;;
  arm64) EXPECTED_LLAMA_SHA256="0fad023bd95e1a26bdaa972b737ff636091e75eb2e743ab98ee726ec0c64ad0f" ;;
esac
if [[ ! -f "$LOCAL_AI_ARCHIVE" ]] ||
  [[ "$(sha256sum "$LOCAL_AI_ARCHIVE" | awk '{print $1}')" != "$EXPECTED_LLAMA_SHA256" ]]; then
  echo "缺少或校验失败的 $ARCH llama.cpp b10331 运行时，严格模式拒绝构建。" >&2
  exit 2
fi
if [[ ! -f "$LOCAL_AI_RUNTIME/LICENSE" || ! -f "$LOCAL_AI_RUNTIME/SOURCE.json" ]]; then
  echo "缺少 llama.cpp 许可文件，严格模式拒绝构建。" >&2
  exit 2
fi
# 部分 Windows 解压/重打包工具会把清单改成 CRLF。校验前只规范行尾，
# 避免 sha256sum 把不可见的 \r 误认为文件名的一部分。
sed -i 's/\r$//' "$ROOT/vendor/SHA256SUMS"
(cd "$ROOT/vendor" && sha256sum -c SHA256SUMS)
gzip -dc "$OCR_ARCHIVE" |
  "$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py" \
    --expected-root tesseract-5.5.3 --max-members 1000 --max-bytes 536870912

# python-build-standalone 由 Clang 构建，其 sysconfig 会默认调用 clang/llvm-ar。
# UOS 的 build-essential 提供 GCC 工具链，因此为本机扩展显式覆盖编译与链接命令。
for tool in gcc g++ ar ranlib; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "缺少本机编译工具：$tool；请重新运行环境补齐脚本。" >&2
    exit 2
  }
done
export CC="${CC:-$(command -v gcc)}"
export CXX="${CXX:-$(command -v g++)}"
export AR="${AR:-$(command -v ar)}"
export RANLIB="${RANLIB:-$(command -v ranlib)}"
# 发布运行时不包含调试信息。显式把 -g0 放在用户/解释器默认参数之后，
# 避免 GCC 11 生成 CentOS 7 / manylinux2014 的 binutils 2.27 无法识别的
# `.loc view` 指令；同时保持 x86_64 与 ARM64 构建参数一致、可复现。
export CFLAGS="${CFLAGS:-} -O3 -g0 -fPIC"
export CXXFLAGS="${CXXFLAGS:-} -O3 -g0 -fPIC"
export LDSHARED="${LDSHARED:-$CC -pthread -shared -Wl,-z,noexecstack -Wl,--exclude-libs,ALL}"
export LDCXXSHARED="${LDCXXSHARED:-$CXX -pthread -shared -Wl,-z,noexecstack}"
echo "本机扩展编译器：$CC；链接器：$LDSHARED"

mkdir -p "$ARTIFACTS"
"$PYTHON_BIN" -m venv "$BUILD/venv"
PY="$BUILD/venv/bin/python"
"$PY" -m pip install --no-index --find-links "$WHEELHOUSE" \
  -r "$ROOT/packaging/uos/requirements-build.txt"
"$PY" "$ROOT/scripts/validate-uos-wheelhouse.py" \
  --architecture "$ARCH" \
  --wheelhouse "$WHEELHOUSE" \
  --requirements \
  "$ROOT/backend/requirements.txt" \
  "$ROOT/backend/requirements-local-ai.txt" \
  "$ROOT/packaging/uos/requirements-build.txt" || {
    echo "严格模式：$ARCH 离线依赖存在重复包、错误架构、glibc 超限、缺失项或版本冲突，拒绝构建。" >&2
    exit 2
  }

mkdir -p "$BUILD/sqlite" "$BUILD/pysqlite3"
unzip -q "$SQLITE_ARCHIVE" -d "$BUILD/sqlite"
tar -xzf "$PYSQLITE_ARCHIVE" -C "$BUILD/pysqlite3" --strip-components=1
cp "$BUILD"/sqlite/sqlite-amalgamation-3510300/sqlite3.c "$BUILD/pysqlite3/"
cp "$BUILD"/sqlite/sqlite-amalgamation-3510300/sqlite3.h "$BUILD/pysqlite3/"
(
  cd "$BUILD/pysqlite3"
  "$PY" setup.py build_static bdist_wheel
)
"$PY" -m pip install --no-index --find-links "$WHEELHOUSE" \
  -r "$ROOT/backend/requirements.txt"
if [[ "$LOCAL_EMBEDDING_AVAILABLE" == "1" ]]; then
  "$PY" -m pip install --no-index --find-links "$WHEELHOUSE" \
    -r "$ROOT/backend/requirements-local-ai.txt"
fi
"$PY" -m pip install "$BUILD"/pysqlite3/dist/pysqlite3-*.whl
"$PY" -m pip check
PARTYOPS_DATA_DIR="$BUILD/dependency-smoke-data" \
PARTYOPS_ENVIRONMENT=production \
PARTYOPS_STRICT_SQLITE=true \
PARTYOPS_SEED_DEMO=false \
PYTHONPATH="$ROOT/backend" \
"$PY" - <<'PY'
from app.database import db_runtime
from uvicorn.config import Config

capabilities = db_runtime.validate_capabilities()
config = Config(
    "app.main:app",
    loop="asyncio",
    http="h11",
    ws="none",
    workers=1,
)
config.load()
print(
    "运行依赖自检通过："
    f"SQLite {capabilities['version']}，"
    f"FTS5={capabilities['fts5']}，"
    "Uvicorn=asyncio+h11。"
)
PY

if [[ "${PARTYOPS_REBUILD_FRONTEND:-0}" == "1" ]]; then
  pnpm --dir "$ROOT/frontend" install --offline --frozen-lockfile
  pnpm --dir "$ROOT/frontend" run build
elif [[ ! -f "$ROOT/frontend/dist/client/index.html" ]]; then
  echo "缺少已构建前端；请在准备机运行 scripts/build.ps1，或设置 PARTYOPS_REBUILD_FRONTEND=1。" >&2
  exit 2
fi
(
  cd "$ROOT"
  # python-build-standalone 将 Tcl/Tk 放在自身 lib 目录。如果构建时未
  # 显式加入动态库搜索路径，PyInstaller 会生成一个缺少
  # libtcl/libtk 的向导程序，只有用户点击“选择数据目录”时才崩溃。
  # 发布构建必须在冻结时解析并封入这两个库。
  PYTHON_BASE_LIB="$("$PY" -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve().parents[1] / "lib")')"
  LD_LIBRARY_PATH="$PYTHON_BASE_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$PY" -m PyInstaller --noconfirm --clean \
      --distpath "$PYI_DIST" --workpath "$PYI_WORK" \
      "$ROOT/packaging/uos/partyops.spec"
)

RUNTIME="$BUILD/PartyOps"
mkdir -p "$RUNTIME"
cp -a "$PYI_DIST/PartyOps/." "$RUNTIME/"

# 打包完整性断言：冒烟测试之前先确认关键数据已随运行时打包，避免
# spec 漏配导致"构建成功、启动即失败"。1.3.3 曾因漏打包 alembic 在
# 2/5 阶段构建失败；此处把同类问题拦在构建侧，而不是等安装机报错。
verify_runtime_bundle() {
  local runtime_dir="$1"
  local contents
  if [[ -d "$runtime_dir/_internal" ]]; then
    contents="$runtime_dir/_internal"
  else
    contents="$runtime_dir"
  fi
  local missing=()
  [[ -f "$contents/alembic.ini" ]] || missing+=("alembic.ini")
  [[ -f "$contents/alembic/env.py" ]] || missing+=("alembic/env.py")
  [[ -d "$contents/alembic/versions" ]] || missing+=("alembic/versions/")
  [[ -f "$contents/frontend/index.html" ]] || missing+=("frontend/index.html")
  local entrypoint
  for entrypoint in partyops partyops-client partyops-wizard partyops-updater; do
    [[ -x "$runtime_dir/$entrypoint" ]] || missing+=("$entrypoint 运行入口")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "便携运行时打包不完整，缺少以下关键数据：" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    echo "请检查 packaging/uos/partyops.spec 的 datas 清单后重新构建。" >&2
    return 1
  fi
  local expect_versions bundled_versions
  expect_versions="$(find "$ROOT/backend/alembic/versions" -maxdepth 1 -name '*.py' | wc -l)"
  bundled_versions="$(find "$contents/alembic/versions" -maxdepth 1 -name '*.py' | wc -l)"
  if [[ "$bundled_versions" -lt "$expect_versions" ]]; then
    echo "迁移脚本数量异常：源码 $expect_versions 个，打包 $bundled_versions 个。" >&2
    return 1
  fi
  echo "便携运行时打包完整性核验通过（alembic 迁移 $bundled_versions 个）。"
}
verify_runtime_bundle "$RUNTIME" || exit 2
cp "$ROOT/packaging/uos/start.sh" "$ROOT/packaging/uos/stop.sh" \
  "$ROOT/packaging/uos/desktop-launcher.sh" \
  "$ROOT/packaging/uos/open-local-file.sh" \
  "$ROOT/packaging/uos/install-desktop-shortcut.sh" \
  "$ROOT/packaging/uos/install-internal-ca.sh" "$RUNTIME/"
cp "$ROOT/packaging/uos/partyops.desktop" "$ROOT/packaging/uos/partyops-file.desktop" \
  "$ROOT/packaging/uos/partyops-client.desktop" \
  "$ROOT/packaging/uos/partyops.svg" "$RUNTIME/"
cp "$ROOT/packaging/uos/client-config.example.json" "$RUNTIME/"
printf '%s\n' "$APP_VERSION" >"$RUNTIME/VERSION"
if [[ -f "$ROOT/packaging/uos/update-public-key.txt" ]]; then
  cp "$ROOT/packaging/uos/update-public-key.txt" "$RUNTIME/"
elif [[ "${PARTYOPS_REQUIRE_UPDATE_SIGNING:-0}" == "1" ]]; then
  echo "缺少 update-public-key.txt，发布模式拒绝生成不可更新的安装包。" >&2
  exit 2
else
  echo "警告：未配置发布公钥，生产更新中心将拒绝更新包。" >&2
fi
for notice in README.md CHANGELOG.md LICENSE THIRD_PARTY_NOTICES.md; do
  [[ -f "$ROOT/$notice" ]] || {
    echo "发布包缺少开源声明文件：$notice" >&2
    exit 2
  }
  cp "$ROOT/$notice" "$RUNTIME/"
done
# 安装载荷只携带当前版本会直接用到的离线用户文档。验收记录、制品哈希
# 清单和发布就绪报告必须在制品冻结后生成；若把它们反向封入制品，会形成
# 自身哈希循环，也会让用户在离线包中看到上一候选版的发布状态。
mkdir -p "$RUNTIME/docs"
USER_DOCUMENTS=(
  "user-guide.md"
  "deployment.md"
  "upgrade-1.4.3.md"
  "installation-checklist.md"
  "backup-restore.md"
  "operations-runbook.md"
  "release-notes-v1.4.4.md"
)
for document in "${USER_DOCUMENTS[@]}"; do
  [[ -f "$ROOT/docs/$document" ]] || {
    echo "发布包缺少当前版本用户文档：$document" >&2
    exit 2
  }
  cp "$ROOT/docs/$document" "$RUNTIME/docs/"
done

mkdir -p "$RUNTIME/licenses"
# llama.cpp 固定为官方 b10331 标签源码，并由 glibc 2.17 工具链重建 CPU
# 运行时；解包后立即执行版本验证。模型通过签名 .partyops-modelpack 独立
# 导入，主程序包不携带权重。
if [[ "$LOCAL_LLM_AVAILABLE" == "1" ]]; then
  mkdir -p "$BUILD/llama-runtime"
  tar -xzf "$LOCAL_AI_ARCHIVE" -C "$BUILD/llama-runtime"
  LLAMA_SOURCE_DIR="$(dirname "$(find "$BUILD/llama-runtime" -type f -name llama-server -print -quit)")"
  [[ -x "$LLAMA_SOURCE_DIR/llama-server" ]] || {
    echo "llama.cpp 官方包缺少可执行的 llama-server。" >&2
    exit 2
  }
  cp -a "$LLAMA_SOURCE_DIR/llama-server" "$RUNTIME/llama-server"
  find "$LLAMA_SOURCE_DIR" -maxdepth 1 \( -type f -o -type l \) -name '*.so*' \
    -exec cp -a -t "$RUNTIME" {} +
  cp "$LOCAL_AI_RUNTIME/LICENSE" "$RUNTIME/licenses/llama.cpp-LICENSE"
  cp "$LOCAL_AI_RUNTIME/SOURCE.json" "$RUNTIME/licenses/llama.cpp-SOURCE.json"
  LD_LIBRARY_PATH="$RUNTIME${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$RUNTIME/llama-server" --version >/dev/null
fi
{
  echo "PartyOps 本地智能运行能力"
  [[ "$LOCAL_EMBEDDING_AVAILABLE" == "1" ]] &&
    echo "本地中文语义重排：已包含" || echo "本地中文语义重排：未包含"
  [[ "$LOCAL_LLM_AVAILABLE" == "1" ]] &&
    echo "本地 LLM 运行时：已包含" || echo "本地 LLM 运行时：未包含"
  echo "缺失的增强能力不会影响任务、文件、档案、协同、规则推荐和外部 AI。"
} >"$RUNTIME/local-ai-capabilities.txt"

# 将固定官方源码重建的 OCR 引擎与中英文语言数据随应用一并交付。
# 禁止从构建机复制系统 Tesseract：manylinux2014 环境里的系统版本可能
# 已停止维护，也会让不同时间生成的制品内容无法复现。
OCR_ROOT="$RUNTIME/ocr"
mkdir -p "$OCR_ROOT/bin" "$OCR_ROOT/lib" "$OCR_ROOT/tessdata" "$OCR_ROOT/licenses"
mkdir -p "$BUILD/ocr-runtime"
tar -xzf "$OCR_ARCHIVE" -C "$BUILD/ocr-runtime" \
  --no-same-owner --no-same-permissions
OCR_SOURCE_DIR="$(find "$BUILD/ocr-runtime" -mindepth 1 -maxdepth 1 \
  -type d -name 'tesseract-5.5.3' -print -quit)"
[[ -n "$OCR_SOURCE_DIR" && -x "$OCR_SOURCE_DIR/bin/tesseract" ]] || {
  echo "固定 OCR 运行时缺少 Tesseract 5.5.3 可执行文件。" >&2
  exit 2
}
cp -a "$OCR_SOURCE_DIR/bin/tesseract" "$OCR_ROOT/bin/"
cp -a "$OCR_SOURCE_DIR/tessdata/." "$OCR_ROOT/tessdata/"
cp -a "$OCR_SOURCE_DIR/licenses/." "$OCR_ROOT/licenses/"
EXPECTED_OCR_PATTERN=x86-64
[[ "$ARCH" == arm64 ]] && EXPECTED_OCR_PATTERN='ARM aarch64'
file "$OCR_ROOT/bin/tesseract" | grep -q "$EXPECTED_OCR_PATTERN" || {
  echo "OCR ELF 架构与目标 $ARCH 不一致。" >&2
  exit 2
}
if ldd "$OCR_ROOT/bin/tesseract" 2>&1 | grep -q 'not found'; then
  echo "OCR 运行时存在缺失的动态库依赖。" >&2
  exit 2
fi
if ldd "$OCR_ROOT/bin/tesseract" |
  grep -Eq 'lib(tesseract|lept|png|z|stdc\+\+|gcc_s)\.so'; then
  echo "OCR/图像/C++ 运行库未静态封入 Tesseract，拒绝构建。" >&2
  exit 2
fi
OCR_VERSION="$(TESSDATA_PREFIX="$OCR_ROOT/tessdata" \
  "$OCR_ROOT/bin/tesseract" --version 2>&1)"
grep -q '^tesseract 5\.5\.3' <<<"$OCR_VERSION" || {
  echo "OCR 版本不是已冻结的 Tesseract 5.5.3。" >&2
  exit 2
}
OCR_LANGS="$(TESSDATA_PREFIX="$OCR_ROOT/tessdata" \
  "$OCR_ROOT/bin/tesseract" --list-langs 2>&1)"
grep -qx chi_sim <<<"$OCR_LANGS" && grep -qx eng <<<"$OCR_LANGS" || {
  echo "OCR 中英文离线语言数据未完整加载。" >&2
  exit 2
}
# WSL DrvFS 和部分 PyInstaller wheel 会把共享库、前端图片、WASM 乃至
# 许可证统一标成可执行文件。麒麟安全中心会因此把 libgcc_s.so.1 当成
# “启动程序”反复拦截。权限安全模型改成默认拒绝：先清除所有普通文件
# 的执行位，再只为经过审计的 PartyOps 入口恢复执行位。动态链接器加载
# .so 只需要读取权限，不需要文件本身具有执行位。
find "$RUNTIME" -type f -exec chmod 0644 {} +
chmod 0755 "$RUNTIME/partyops" "$RUNTIME/partyops-client" "$RUNTIME/partyops-wizard" \
  "$RUNTIME/partyops-updater" \
  "$RUNTIME/start.sh" "$RUNTIME/stop.sh" "$RUNTIME/desktop-launcher.sh" \
  "$RUNTIME/open-local-file.sh" \
  "$RUNTIME/install-desktop-shortcut.sh" "$RUNTIME/install-internal-ca.sh" \
  "$OCR_ROOT/bin/tesseract"
if [[ "$LOCAL_LLM_AVAILABLE" == "1" ]]; then
  chmod 0755 "$RUNTIME/llama-server"
fi

# 封包前再次执行白名单门禁，防止后续新增依赖把任意共享库或数据文件
# 重新带上执行位，避免同类问题只更换一个库名后再次出现。
while IFS= read -r -d '' executable; do
  case "$executable" in
    "$RUNTIME/partyops"|"$RUNTIME/partyops-client"|\
    "$RUNTIME/partyops-wizard"|"$RUNTIME/partyops-updater"|\
    "$RUNTIME/start.sh"|"$RUNTIME/stop.sh"|\
    "$RUNTIME/desktop-launcher.sh"|"$RUNTIME/open-local-file.sh"|\
    "$RUNTIME/install-desktop-shortcut.sh"|\
    "$RUNTIME/install-internal-ca.sh"|"$OCR_ROOT/bin/tesseract"|\
    "$RUNTIME/llama-server") ;;
    *)
      echo "运行时包含未授权的可执行文件：$executable" >&2
      exit 2
      ;;
  esac
done < <(find "$RUNTIME" -type f -perm /111 -print0)
if find "$RUNTIME" -type f -name '*.so*' -perm /111 -print -quit | grep -q .; then
  echo "共享库被错误标记为可执行文件，拒绝生成 Linux 制品。" >&2
  exit 2
fi

# 回归解压后的单文件入口，特别防止向导程序在构建时
# 遗漏 Tcl/Tk 动态库。这里不打开图形窗口，仅要求冻结运行时
# 能完成导入、解析中文命令行并正常退出。
for entrypoint in partyops-client partyops-wizard partyops-updater; do
  "$RUNTIME/$entrypoint" --help >/dev/null 2>&1 || {
    echo "冻结入口自检失败：$entrypoint" >&2
    exit 2
  }
done
# 配置向导是桌面首次启动的必经路径，必须证明当前冻结布局没有退回会在
# /tmp/_MEI* 解包共享库的单文件模式，并验证所有共享库都没有执行位。
"$RUNTIME/partyops-wizard" --runtime-layout-self-test || {
  echo "冻结入口仍在使用不安全的单文件解包布局。" >&2
  exit 2
}

SMOKE_PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SMOKE_TIMEOUT_SECONDS="${PARTYOPS_SMOKE_TIMEOUT_SECONDS:-180}"
[[ "$SMOKE_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] &&
  ((SMOKE_TIMEOUT_SECONDS >= 30 && SMOKE_TIMEOUT_SECONDS <= 900)) || {
  echo "便携运行时自检等待时间必须是 30—900 秒的整数。" >&2
  exit 2
}
SMOKE_LOG="$BUILD/smoke-server.log"
HEALTH_FILE="$BUILD/health.json"
PARTYOPS_DATA_DIR="$BUILD/smoke-data" \
PARTYOPS_ENVIRONMENT=production \
PARTYOPS_STRICT_SQLITE=true \
PARTYOPS_SEED_DEMO=false \
PARTYOPS_HOST=127.0.0.1 \
PARTYOPS_PORT="$SMOKE_PORT" \
"$RUNTIME/partyops" >"$SMOKE_LOG" 2>&1 &
PID=$!

# UOS 首次启动会执行数据库迁移、字体和动态库初始化，部分国产电脑需
# 30 秒以上。旧逻辑固定等待 30 秒，可能在服务刚启动完成时误报失败。
# 这里使用可配置截止时间，同时监控进程是否提前退出，并只在最终失败时
# 输出服务日志，避免正常等待期间刷出大量“拒绝连接”噪声。
SMOKE_STARTED_AT="$SECONDS"
SMOKE_DEADLINE=$((SECONDS + SMOKE_TIMEOUT_SECONDS))
while ((SECONDS < SMOKE_DEADLINE)); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "便携运行时在健康检查完成前退出。" >&2
    tail -n 120 "$SMOKE_LOG" >&2 || true
    cp "$SMOKE_LOG" "$ARTIFACTS/portable-smoke-failure-$ARCH.log" || true
    exit 2
  fi
  if curl -fsS --connect-timeout 1 --max-time 3 \
    "http://127.0.0.1:$SMOKE_PORT/api/v1/health" \
    >"$HEALTH_FILE.tmp" 2>/dev/null; then
    mv "$HEALTH_FILE.tmp" "$HEALTH_FILE"
    break
  fi
  ELAPSED=$((SECONDS - SMOKE_STARTED_AT))
  if ((ELAPSED > 0 && ELAPSED % 15 == 0)); then
    echo "便携运行时仍在启动，已等待 ${ELAPSED} 秒……"
  fi
  sleep 1
done
rm -f "$HEALTH_FILE.tmp"
if [[ ! -s "$HEALTH_FILE" ]]; then
  echo "便携运行时在 ${SMOKE_TIMEOUT_SECONDS} 秒内未通过健康检查。" >&2
  tail -n 120 "$SMOKE_LOG" >&2 || true
  cp "$SMOKE_LOG" "$ARTIFACTS/portable-smoke-failure-$ARCH.log" || true
  exit 2
fi
if ! grep -q '"safe_version":true' "$HEALTH_FILE" ||
  ! grep -q '"fts5":true' "$HEALTH_FILE"; then
  echo "便携运行时已启动，但 SQLite 安全版本或 FTS5 检查未通过：" >&2
  cat "$HEALTH_FILE" >&2
  cp "$SMOKE_LOG" "$ARTIFACTS/portable-smoke-failure-$ARCH.log" || true
  exit 2
fi
echo "便携运行时健康检查通过，启动耗时 $((SECONDS - SMOKE_STARTED_AT)) 秒。"
kill "$PID"
wait "$PID" || true
PID=""

command -v zstd >/dev/null 2>&1 || {
  echo "缺少 zstd，无法生成严格模式便携载荷。" >&2
  exit 2
}
# CentOS/manylinux2014 自带的 GNU tar 版本较旧，不支持 --zstd。使用
# POSIX tar 流交给固定的 zstd 程序，输出仍是标准 .tar.zst，且不依赖
# 构建机 tar 的可选压缩参数。
# 原生包入口拒绝 TAR 内的符号链接和其他特殊文件。PyInstaller 会为
# 部分共享库创建相对符号链接，因此在受控构建目录内归档时显式展开，
# 让下游只接收普通目录与文件，并继续由 validate-portable-tar.py 严格校验。
tar --dereference --hard-dereference -cf - -C "$BUILD" PartyOps |
  zstd -T0 -19 -f -o "$ARTIFACTS/PartyOps-linux-$ARCH.tar.zst"
zstd -dc -- "$ARTIFACTS/PartyOps-linux-$ARCH.tar.zst" |
  "$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py" \
    --expected-root PartyOps
(cd "$WHEELHOUSE" && find . -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum) \
  > "$ARTIFACTS/dependency-sha256-$ARCH.txt"
(cd "$ARTIFACTS" && sha256sum "PartyOps-linux-$ARCH.tar.zst" > "SHA256SUMS.$ARCH")
echo "便携包已生成：$ARTIFACTS/PartyOps-linux-$ARCH.tar.zst"
