#!/usr/bin/env bash
set -euo pipefail

# 在真实 glibc 2.17 环境中重建上游未提供 manylinux2014 文件的轮子。
# 不允许仅修改文件名或平台标签；auditwheel 必须检查并修复实际 ELF 依赖。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$#" -ge 3 ]] || {
  echo "用法：rebuild-manylinux2014-wheels.sh amd64|arm64 /path/to/python 包==版本 [...]" >&2
  exit 2
}
ARCHITECTURE="$1"
PYTHON_BIN="$2"
shift 2

[[ "$ARCHITECTURE" == "amd64" || "$ARCHITECTURE" == "arm64" ]] || {
  echo "用法：rebuild-manylinux2014-wheels.sh amd64|arm64 /path/to/python 包==版本 [...]" >&2
  exit 2
}
[[ -x "$PYTHON_BIN" ]] || {
  echo "Python 不存在或不可执行：$PYTHON_BIN" >&2
  exit 2
}
[[ "$#" -gt 0 ]] || {
  echo "至少指定一个带固定版本的包。" >&2
  exit 2
}
for requirement in "$@"; do
  [[ "$requirement" == *"=="* ]] || {
    echo "依赖必须固定为精确版本：$requirement" >&2
    exit 2
  }
done

EXPECTED_MACHINE="x86_64"
[[ "$ARCHITECTURE" == "arm64" ]] && EXPECTED_MACHINE="aarch64"
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "$EXPECTED_MACHINE" ]] || {
  echo "必须在 $EXPECTED_MACHINE Linux 原生或受控仿真环境构建。" >&2
  exit 2
}
[[ "$(getconf GNU_LIBC_VERSION)" == "glibc 2.17" ]] || {
  echo "必须在 glibc 2.17 构建，禁止生成伪 manylinux2014 文件。" >&2
  exit 2
}
command -v auditwheel >/dev/null 2>&1 || {
  echo "缺少 auditwheel；请先安装经固定版本的构建工具。" >&2
  exit 2
}
command -v patchelf >/dev/null 2>&1 || {
  echo "缺少 patchelf，无法修复并核验 ELF 依赖。" >&2
  exit 2
}
# Oracle Linux 7 的 GCC 4.8 默认仍按旧 C++ 方言编译；greenlet 3.x 明确
# 使用 C++11 语义。显式启用标准，避免构建参数随宿主默认值漂移。
export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-std=gnu++11"

mkdir -p "$ROOT/.build-linux" "$ROOT/artifacts/source-evidence" \
  "$ROOT/vendor/wheels/$ARCHITECTURE"
BUILD="$(mktemp -d "$ROOT/.build-linux/rebuild-wheels.XXXXXX")"
cleanup() {
  status=$?
  trap - EXIT
  case "$BUILD" in
    "$ROOT/.build-linux/rebuild-wheels."*) rm -rf -- "$BUILD" ;;
    *) echo "拒绝清理异常构建目录：$BUILD" >&2 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

for requirement in "$@"; do
  "$PYTHON_BIN" "$ROOT/scripts/download-pypi-sdist.py" \
    "$requirement" "$BUILD/sdist"
done
for evidence in "$BUILD"/sdist/*.pypi.json; do
  # 每个源码包独立保存官方元数据与本地摘要，避免后一次构建覆盖前一次证据。
  source_archive="${evidence%.pypi.json}"
  evidence_name="manylinux2014-$ARCHITECTURE-$(basename "$evidence")"
  digest_name="manylinux2014-$ARCHITECTURE-$(basename "$source_archive").sha256"
  cp -- "$evidence" "$ROOT/artifacts/source-evidence/$evidence_name"
  (cd "$(dirname "$source_archive")" && sha256sum "$(basename "$source_archive")") > \
    "$ROOT/artifacts/source-evidence/$digest_name"
done

for source_archive in "$BUILD"/sdist/*; do
  [[ "$source_archive" == *.pypi.json ]] && continue
  "$PYTHON_BIN" -m pip wheel --no-cache-dir --no-deps \
    --wheel-dir "$BUILD/raw" "$source_archive"
done
for wheel in "$BUILD"/raw/*.whl; do
  auditwheel show "$wheel"
  auditwheel repair --plat manylinux2014_"$EXPECTED_MACHINE" \
    --wheel-dir "$BUILD/repaired" "$wheel"
done

for wheel in "$BUILD"/repaired/*.whl; do
  destination="$ROOT/vendor/wheels/$ARCHITECTURE/$(basename "$wheel")"
  [[ ! -e "$destination" ]] || {
    echo "目标 wheel 已存在，拒绝覆盖或制造重复包：$destination" >&2
    exit 2
  }
  cp -- "$wheel" "$destination"
  [[ "$(sha256sum "$wheel" | awk '{print $1}')" == \
      "$(sha256sum "$destination" | awk '{print $1}')" ]] || {
    echo "wheel 复制后哈希不一致：$destination" >&2
    exit 2
  }
  echo "已生成：$destination"
done
