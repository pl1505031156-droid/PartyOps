#!/usr/bin/env bash
set -euo pipefail

PACKAGE="${1:-}"
EXPECTED_ARCH="${2:-}"
[[ -f "$PACKAGE" && ( "$EXPECTED_ARCH" == "amd64" || "$EXPECTED_ARCH" == "arm64" ) ]] || {
  echo "用法：test-native-package-runtime.sh <deb|rpm> <amd64|arm64>" >&2
  exit 2
}
PACKAGE="$(readlink -f "$PACKAGE")"

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-native-runtime.XXXXXX")"
ROOT="$TEST_ROOT/root"
SERVER_PID=""
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  [[ -z "$SERVER_PID" ]] || kill "$SERVER_PID" >/dev/null 2>&1 || true
  [[ -z "$SERVER_PID" ]] || wait "$SERVER_PID" >/dev/null 2>&1 || true
  case "$TEST_ROOT" in
    "${TMPDIR:-/tmp}"/partyops-native-runtime.*) rm -rf -- "$TEST_ROOT" ;;
    *) echo "拒绝清理异常测试目录：$TEST_ROOT" >&2; status=3 ;;
  esac
  exit "$status"
}
trap cleanup EXIT INT TERM
mkdir -p "$ROOT"

case "$PACKAGE" in
  *.deb)
    if command -v dpkg-deb >/dev/null 2>&1; then
      dpkg-deb -x "$PACKAGE" "$ROOT"
    else
      # manylinux2014 ARM64 验证根通常不含 Debian 包管理器，但带有
      # 标准 ar/tar。只展开唯一 data.tar 成员，仍对最终 DEB 成品做
      # 动态运行门禁，而不是退回验证构建目录中的便携载荷。
      command -v ar >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 || {
        echo "缺少 dpkg-deb，且没有 ar/tar 兼容展开工具。" >&2
        exit 2
      }
      DATA_MEMBER="$(ar t "$PACKAGE" | awk '/^data\.tar(\.[A-Za-z0-9]+)?$/ { print }')"
      [[ -n "$DATA_MEMBER" && "$(printf '%s\n' "$DATA_MEMBER" | wc -l)" -eq 1 ]] || {
        echo "DEB 成品缺少唯一 data.tar 载荷。" >&2
        exit 2
      }
      case "$DATA_MEMBER" in
        *.tar.xz) ar p "$PACKAGE" "$DATA_MEMBER" | tar -xJ -C "$ROOT" ;;
        *.tar.gz) ar p "$PACKAGE" "$DATA_MEMBER" | tar -xz -C "$ROOT" ;;
        *.tar.bz2) ar p "$PACKAGE" "$DATA_MEMBER" | tar -xj -C "$ROOT" ;;
        *.tar) ar p "$PACKAGE" "$DATA_MEMBER" | tar -x -C "$ROOT" ;;
        *)
          echo "当前验证环境不支持 DEB 压缩成员：$DATA_MEMBER" >&2
          exit 2
          ;;
      esac
    fi
    ;;
  *.rpm)
    command -v rpm2cpio >/dev/null 2>&1 && command -v cpio >/dev/null 2>&1 || {
      echo "缺少 rpm2cpio/cpio，无法展开 RPM 成品。" >&2
      exit 2
    }
    (cd "$ROOT" && rpm2cpio "$PACKAGE" | cpio -idm --quiet --no-absolute-filenames)
    ;;
  *) echo "只接受 DEB/RPM 成品。" >&2; exit 2 ;;
esac

RUNTIME="$ROOT/opt/partyops"
[[ "$(cat "$RUNTIME/VERSION" 2>/dev/null || true)" == "1.4.5-rc.4" ]] || {
  echo "成品缺少正确的冻结版本标识。" >&2
  exit 2
}
for runtime_entrypoint in \
  partyops partyops-client partyops-wizard partyops-updater \
  desktop-launcher.sh start.sh; do
  [[ -x "$RUNTIME/$runtime_entrypoint" ]] || {
    echo "成品缺少可执行入口：$runtime_entrypoint。" >&2
    exit 2
  }
done
if find "$RUNTIME" -type f -name '*.so*' -perm /111 -print -quit | grep -q .; then
  echo "成品仍有共享库携带执行位，会触发国产系统安全中心反复拦截。" >&2
  exit 2
fi

MACHINE="$(uname -m)"
if [[ ( "$EXPECTED_ARCH" == "amd64" && "$MACHINE" != "x86_64" ) ||
      ( "$EXPECTED_ARCH" == "arm64" && "$MACHINE" != "aarch64" ) ]]; then
  echo "原生包静态权限门禁通过；当前机器 $MACHINE 不执行 $EXPECTED_ARCH 运行验收。"
  exit 0
fi

"$RUNTIME/partyops" --package-self-test
"$RUNTIME/partyops-wizard" --runtime-layout-self-test

HOME_DIR="$TEST_ROOT/home"
CONFIG_ROOT="$HOME_DIR/.config/partyops"
DATA_ROOT="$TEST_ROOT/data"
BIN_DIR="$TEST_ROOT/bin"
OPEN_LOG="$TEST_ROOT/opened.log"
PORT=$((28000 + $$ % 10000))
mkdir -p "$CONFIG_ROOT" "$DATA_ROOT" "$BIN_DIR"
cat >"$BIN_DIR/xdg-open" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >>"$OPEN_LOG"
EOF
cat >"$BIN_DIR/xdg-mime" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$BIN_DIR/update-desktop-database" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod 0755 "$BIN_DIR"/*

export HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" OPEN_LOG
export PATH="$BIN_DIR:/usr/local/bin:/usr/bin:/bin"
export PARTYOPS_MODE=personal PARTYOPS_BIND_HOST=127.0.0.1
export PARTYOPS_ADVERTISE_HOST=127.0.0.1 PARTYOPS_PORT="$PORT"
export PARTYOPS_TLS_ENABLED=false PARTYOPS_DATA_DIR="$DATA_ROOT"
export PARTYOPS_CONFIG_FILE="$CONFIG_ROOT/personal.env"
"$RUNTIME/partyops" >"$TEST_ROOT/server.log" 2>&1 &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" >"$DATA_ROOT/partyops.pid"

READY=0
for _attempt in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1/health" >"$TEST_ROOT/health.json"; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$TEST_ROOT/server.log" >&2
    echo "成品主程序在健康检查前退出。" >&2
    exit 4
  fi
  sleep 1
done
[[ "$READY" == 1 ]] || {
  cat "$TEST_ROOT/server.log" >&2
  echo "成品主程序在 90 秒内未就绪。" >&2
  exit 5
}
grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$TEST_ROOT/health.json" || {
  echo "健康响应缺少 status=ok：$(cat "$TEST_ROOT/health.json")" >&2
  exit 6
}
grep -Eq '"mode"[[:space:]]*:[[:space:]]*"personal"' "$TEST_ROOT/health.json" || {
  echo "健康响应不是个人模式：$(cat "$TEST_ROOT/health.json")" >&2
  exit 6
}

# 透明 QEMU binfmt 会把 /proc/<pid>/exe 暴露为 qemu-aarch64-static；而
# start.sh 有意要求该链接指向 PartyOps 本体来防 PID 复用。在仿真器中
# 继续执行桌面入口只会制造确定性的环境误报。此处保留最终 ARM 二进制
# 的自检与健康启动，并额外做启动脚本语法门禁；真实 ARM 机器仍执行下方
# 完整的 PID 归属、桌面入口和浏览器打开闭环。
SERVER_EXECUTABLE="$(readlink -f "/proc/$SERVER_PID/exe" 2>/dev/null || true)"
if [[ "$SERVER_EXECUTABLE" == */qemu-aarch64-static ]]; then
  bash -n "$RUNTIME/start.sh" "$RUNTIME/desktop-launcher.sh"
  echo "ARM64 QEMU 成品动态门禁通过；桌面 PID 归属闭环需在真实 ARM 内核执行。"
  exit 0
fi

cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"personal"}
EOF
cat >"$CONFIG_ROOT/personal.env" <<EOF
PARTYOPS_MODE=personal
PARTYOPS_BIND_HOST=127.0.0.1
PARTYOPS_ADVERTISE_HOST=127.0.0.1
PARTYOPS_PORT=$PORT
PARTYOPS_TLS_ENABLED=false
PARTYOPS_DATA_DIR='$DATA_ROOT'
EOF
chmod 0600 "$CONFIG_ROOT/mode.json" "$CONFIG_ROOT/personal.env"
DESKTOP_STATUS=0
"$RUNTIME/desktop-launcher.sh" || DESKTOP_STATUS=$?
if [[ "$DESKTOP_STATUS" -ne 0 ]]; then
  echo "桌面入口退出码：$DESKTOP_STATUS" >&2
  [[ ! -f "$OPEN_LOG" ]] || cat "$OPEN_LOG" >&2
  [[ ! -f "$CONFIG_ROOT/desktop-launch.log" ]] || \
    cat "$CONFIG_ROOT/desktop-launch.log" >&2
  exit 7
fi
# 桌面入口会附加每次启动唯一的运行时指纹，避免覆盖升级后浏览器继续
# 复用旧版 HTML；门禁应校验完整本机地址和指纹，而不是旧的无参数 URL。
if ! grep -Eq "^http://127\\.0\\.0\\.1:$PORT/\\?partyops_runtime=[0-9]+$" "$OPEN_LOG"; then
  echo "桌面入口没有打开带版本指纹的本机地址。" >&2
  [[ ! -f "$OPEN_LOG" ]] || cat "$OPEN_LOG" >&2
  [[ ! -f "$CONFIG_ROOT/desktop-launch.log" ]] || \
    cat "$CONFIG_ROOT/desktop-launch.log" >&2
  exit 7
fi
grep -qx 'PARTYOPS_TLS_ENABLED=false' "$CONFIG_ROOT/personal.env" || {
  echo "个人模式启动错误地改写了 TLS 配置。" >&2
  exit 7
}

# 对最终成品执行一次确定性启动失败注入。即使主程序尚未来得及创建自己的
# launcher.log，桌面入口也必须立即生成非空、0600 的统一诊断文件。
kill "$SERVER_PID" >/dev/null 2>&1 || true
wait "$SERVER_PID" >/dev/null 2>&1 || true
SERVER_PID=""
rm -f -- "$DATA_ROOT/partyops.pid"
cat >"$CONFIG_ROOT/personal.env" <<EOF
PARTYOPS_MODE=personal
PARTYOPS_BIND_HOST=127.0.0.1
PARTYOPS_ADVERTISE_HOST=127.0.0.1
PARTYOPS_PORT=$PORT
PARTYOPS_TLS_ENABLED=false
PARTYOPS_DATA_DIR='/proc/partyops-startup-failure-test'
EOF
set +e
"$RUNTIME/desktop-launcher.sh"
FAILURE_STATUS=$?
set -e
[[ "$FAILURE_STATUS" -eq 2 ]] || {
  echo "故障注入时桌面入口退出码异常：$FAILURE_STATUS" >&2
  exit 8
}
[[ -s "$CONFIG_ROOT/desktop-launch.log" &&
  -s "$CONFIG_ROOT/startup-diagnostic.txt" ]] || {
  echo "最终成品启动失败后没有生成非空诊断文件。" >&2
  exit 8
}
[[ "$(stat -c '%a' "$CONFIG_ROOT/startup-diagnostic.txt")" == "600" ]] || {
  echo "最终成品启动诊断权限不是 0600。" >&2
  exit 8
}
grep -q '\[START_COMMAND_FAILED\]' "$CONFIG_ROOT/startup-diagnostic.txt" || {
  echo "最终成品启动诊断缺少稳定错误码。" >&2
  cat "$CONFIG_ROOT/startup-diagnostic.txt" >&2
  exit 8
}

# 用同一最终 DEB/RPM 中的冻结主程序执行真实 0023→0024 覆盖升级。
# 构建机 Python 仅负责按仓库 Alembic 历史生成真实旧库和读回结果，迁移
# 本身完全由待发布二进制完成。
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE_PYTHON="${PARTYOPS_SOURCE_PYTHON:-${PYTHON_BIN:-}}"
if [[ -z "$SOURCE_PYTHON" || ! -x "$SOURCE_PYTHON" ]]; then
  SOURCE_PYTHON="$(command -v python3.11 || command -v python3 || true)"
fi
[[ -n "$SOURCE_PYTHON" && -x "$SOURCE_PYTHON" ]] || {
  echo "缺少用于生成真实 0023 测试库的 Python。" >&2
  exit 9
}
UPGRADE_VENV="$TEST_ROOT/upgrade-venv"
"$SOURCE_PYTHON" -m venv "$UPGRADE_VENV"
WHEELHOUSE="$SOURCE_ROOT/vendor/wheels/$EXPECTED_ARCH"
[[ -d "$WHEELHOUSE" ]] || WHEELHOUSE="$SOURCE_ROOT/vendor/wheels"
"$UPGRADE_VENV/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE" \
  -r "$SOURCE_ROOT/backend/requirements.txt" >/dev/null
UPGRADE_DATA="$TEST_ROOT/upgrade-data"
"$UPGRADE_VENV/bin/python" "$SOURCE_ROOT/scripts/create-0023-upgrade-fixture.py" \
  --repo-root "$SOURCE_ROOT" --data-root "$UPGRADE_DATA" \
  >"$TEST_ROOT/upgrade-fixture.json"
UPGRADE_PORT="$("$UPGRADE_VENV/bin/python" -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
unset PARTYOPS_CONFIG_FILE
export PARTYOPS_MODE=host PARTYOPS_HOST=127.0.0.1
export PARTYOPS_PORT="$UPGRADE_PORT" PARTYOPS_AGENT_PORT=$((UPGRADE_PORT + 1))
export PARTYOPS_ENVIRONMENT=test PARTYOPS_STRICT_SQLITE=true
export PARTYOPS_TLS_ENABLED=false PARTYOPS_SEED_DEMO=false
export PARTYOPS_DATA_DIR="$UPGRADE_DATA"
"$RUNTIME/partyops" >"$TEST_ROOT/upgrade-server.log" 2>&1 &
SERVER_PID=$!
UPGRADE_READY=0
for _attempt in $(seq 1 180); do
  if curl -fsS --connect-timeout 1 --max-time 3 \
    "http://127.0.0.1:$UPGRADE_PORT/api/v1/health" \
    >"$TEST_ROOT/upgrade-health.json" 2>/dev/null; then
    UPGRADE_READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$UPGRADE_READY" != 1 ]]; then
  tail -n 160 "$TEST_ROOT/upgrade-server.log" >&2 || true
  echo "[NATIVE_0023_UPGRADE_FAILED] 最终成品未完成 0023→0024 健康启动。" >&2
  exit 9
fi
revision="$("$UPGRADE_VENV/bin/python" -c \
  'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute("select version_num from alembic_version").fetchone()[0])' \
  "$UPGRADE_DATA/partyops.db")"
display_name="$("$UPGRADE_VENV/bin/python" -c \
  'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute("select display_name from users where id=?", ("rc4-native-upgrade-admin",)).fetchone()[0])' \
  "$UPGRADE_DATA/partyops.db")"
[[ "$revision" == 0024 && "$display_name" == 原生覆盖升级管理员 ]] || {
  echo "覆盖升级后的迁移版本或管理员记录不一致。" >&2
  exit 9
}
[[ "$(cat "$UPGRADE_DATA/attachments/preserved.txt")" == \
  "rc4 原生覆盖升级必须保留附件" ]] || {
  echo "覆盖升级后的附件不一致。" >&2
  exit 9
}
mapfile -t upgrade_backups < <(find "$UPGRADE_DATA/backups" -maxdepth 1 \
  -name 'PartyOps-pre-upgrade-*.partyops-backup' -type f -print)
[[ "${#upgrade_backups[@]}" == 1 ]] || {
  echo "覆盖升级没有生成唯一迁移前备份。" >&2
  exit 9
}
"$UPGRADE_VENV/bin/python" -c \
  'import pathlib,sys; sys.path.insert(0,str(pathlib.Path(sys.argv[1])/"backend")); from app.backups import verify_backup; manifest=verify_backup(pathlib.Path(sys.argv[2])); raise SystemExit(0 if manifest.get("schema_version")=="0023" else 2)' \
  "$SOURCE_ROOT" "${upgrade_backups[0]}" || {
  echo "覆盖升级的迁移前备份未通过 ZIP 边界、哈希或旧版本校验。" >&2
  exit 9
}
kill "$SERVER_PID" >/dev/null 2>&1 || true
wait "$SERVER_PID" >/dev/null 2>&1 || true
SERVER_PID=""

echo "原生包动态启动门禁通过：$PACKAGE（$EXPECTED_ARCH）"
