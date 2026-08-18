#!/usr/bin/env bash
set -euo pipefail

PACKAGE="${1:-}"
EXPECTED_ARCH="${2:-}"
[[ -f "$PACKAGE" && ( "$EXPECTED_ARCH" == "amd64" || "$EXPECTED_ARCH" == "arm64" ) ]] || {
  echo "用法：test-native-package-runtime.sh <deb|rpm> <amd64|arm64>" >&2
  exit 2
}

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
  *.deb) dpkg-deb -x "$PACKAGE" "$ROOT" ;;
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
[[ -x "$RUNTIME/partyops" && -x "$RUNTIME/desktop-launcher.sh" ]] || {
  echo "成品缺少可执行主程序或统一桌面入口。" >&2
  exit 2
}
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
"$RUNTIME/desktop-launcher.sh"
grep -qx "http://127.0.0.1:$PORT" "$OPEN_LOG"
grep -qx 'PARTYOPS_TLS_ENABLED=false' "$CONFIG_ROOT/personal.env" || {
  echo "个人模式启动错误地改写了 TLS 配置。" >&2
  exit 7
}

echo "原生包动态启动门禁通过：$PACKAGE（$EXPECTED_ARCH）"
