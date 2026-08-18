#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-desktop-launcher.XXXXXX")"
RUNTIME="$TEST_ROOT/runtime"
HOME_DIR="$TEST_ROOT/home"
BIN_DIR="$TEST_ROOT/bin"
OPEN_LOG="$TEST_ROOT/opened.log"
NOTIFY_LOG="$TEST_ROOT/notified.log"
SERVER_PID=""

cleanup() {
  [[ -z "$SERVER_PID" ]] || kill "$SERVER_PID" >/dev/null 2>&1 || true
  pkill -f "$RUNTIME/partyops-wizard" >/dev/null 2>&1 || true
  pkill -f "$RUNTIME/partyops-client" >/dev/null 2>&1 || true
  case "$TEST_ROOT" in
    "${TMPDIR:-/tmp}"/partyops-desktop-launcher.*) rm -rf -- "$TEST_ROOT" ;;
    *) printf '拒绝清理异常测试目录：%s\n' "$TEST_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

mkdir -p "$RUNTIME" "$HOME_DIR/.config/partyops" "$BIN_DIR"
cp "$ROOT/packaging/uos/desktop-launcher.sh" "$RUNTIME/"
chmod 0755 "$RUNTIME/desktop-launcher.sh"

cat >"$RUNTIME/install-desktop-shortcut.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$RUNTIME/install-internal-ca.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$RUNTIME/start.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${PARTYOPS_ENV_FILE:-}" >"$TEST_ROOT/started-with-config.log"
exit 0
EOF
cat >"$RUNTIME/partyops-wizard" <<'EOF'
#!/usr/bin/env bash
marker=wizard.url
for argument in "$@"; do
  [[ "$argument" == "--manage-shared-roots" ]] && marker=shared-root-manager.url
done
printf 'http://127.0.0.1:%s\n' "$TEST_PORT" >"$HOME/.config/partyops/$marker"
chmod 0600 "$HOME/.config/partyops/$marker"
sleep 120
EOF
cat >"$RUNTIME/partyops-client" <<'EOF'
#!/usr/bin/env bash
marker=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--browser-url-file" ]]; then
    marker="$2"
    shift 2
  else
    shift
  fi
done
if [[ -n "$marker" ]]; then
  printf 'http://127.0.0.1:%s/device-launch?token=test\n' "$TEST_PORT" >"$marker"
  chmod 0600 "$marker"
  exit 0
fi
sleep 120
EOF
cat >"$BIN_DIR/xdg-open" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >>"$OPEN_LOG"
[[ "${FAKE_XDG_FAIL:-0}" != "1" ]]
EOF
cat >"$BIN_DIR/notify-send" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$NOTIFY_LOG"
exit 0
EOF
chmod 0755 "$RUNTIME"/* "$BIN_DIR"/*

export TEST_ROOT TEST_PORT=$((25000 + $$ % 10000)) OPEN_LOG NOTIFY_LOG
export HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config"
export PATH="$BIN_DIR:$PATH"

perl -MIO::Socket::INET -e '
  my $port = shift;
  my $server = IO::Socket::INET->new(
    LocalAddr => "127.0.0.1", LocalPort => $port, Listen => 8, ReuseAddr => 1
  ) or die "listen: $!";
  while (my $client = $server->accept()) {
    while (my $line = <$client>) { last if $line =~ /^\r?\n$/; }
    print $client "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}";
    close $client;
  }
' "$TEST_PORT" &
SERVER_PID=$!
sleep 0.3
kill -0 "$SERVER_PID"

CONFIG_ROOT="$HOME/.config/partyops"

# 首次双击：向导就绪后必须调用默认浏览器。
"$RUNTIME/desktop-launcher.sh"
grep -qx "http://127.0.0.1:$TEST_PORT" "$OPEN_LOG"
pkill -f "$RUNTIME/partyops-wizard" >/dev/null 2>&1 || true
rm -f "$CONFIG_ROOT/wizard.url" "$OPEN_LOG"

# 个人模式：必须使用 personal.env 启动，并等健康端点后打开页面。
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"personal"}
EOF
cat >"$CONFIG_ROOT/personal.env" <<EOF
PARTYOPS_MODE=personal
PARTYOPS_PORT=$TEST_PORT
PARTYOPS_TLS_ENABLED=false
EOF
"$RUNTIME/desktop-launcher.sh"
grep -qx "$CONFIG_ROOT/personal.env" "$TEST_ROOT/started-with-config.log"
grep -qx "http://127.0.0.1:$TEST_PORT" "$OPEN_LOG"
rm -f "$CONFIG_ROOT/personal.env" "$OPEN_LOG"

# 主机模式：用户配置同样走受控启动、健康等待和本机地址。
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"host"}
EOF
cat >"$CONFIG_ROOT/partyops.env" <<EOF
PARTYOPS_MODE=host
PARTYOPS_PORT=$TEST_PORT
PARTYOPS_TLS_ENABLED=false
EOF
"$RUNTIME/desktop-launcher.sh"
grep -qx "$CONFIG_ROOT/partyops.env" "$TEST_ROOT/started-with-config.log"
grep -qx "http://127.0.0.1:$TEST_PORT" "$OPEN_LOG"
rm -f "$CONFIG_ROOT/partyops.env" "$OPEN_LOG"

# 协同模式：页面令牌通过 0600 文件交接，不依赖隐藏进程内部打开浏览器。
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"client"}
EOF
printf '{}\n' >"$CONFIG_ROOT/client.json"
"$RUNTIME/desktop-launcher.sh"
grep -qx "http://127.0.0.1:$TEST_PORT/device-launch?token=test" "$OPEN_LOG"
[[ ! -e "$CONFIG_ROOT/client-browser.url" ]]
pkill -f "$RUNTIME/partyops-client" >/dev/null 2>&1 || true
rm -f "$CONFIG_ROOT/client.json" "$CONFIG_ROOT/client-agent.pid" "$OPEN_LOG"

# 浏览器关联失败必须返回明确状态并留下中文桌面通知，不允许静默无响应。
rm -f "$CONFIG_ROOT/mode.json"
set +e
FAKE_XDG_FAIL=1 "$RUNTIME/desktop-launcher.sh"
failure_status=$?
set -e
[[ "$failure_status" -eq 3 ]]
grep -q '党建智办启动失败' "$NOTIFY_LOG"
grep -q '系统默认浏览器未能打开' "$CONFIG_ROOT/desktop-launch.log"

printf 'Linux 桌面启动回归通过：首次配置、个人、主机、协同及浏览器失败诊断均正常。\n'
