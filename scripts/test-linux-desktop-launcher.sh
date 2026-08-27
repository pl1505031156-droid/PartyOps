#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-desktop-launcher.XXXXXX")"
RUNTIME="$TEST_ROOT/runtime"
HOME_DIR="$TEST_ROOT/home"
BIN_DIR="$TEST_ROOT/bin"
OPEN_LOG="$TEST_ROOT/opened.log"
NOTIFY_LOG="$TEST_ROOT/notified.log"
HEALTH_BODY_FILE="$TEST_ROOT/health-body.json"
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
printf '1.4.5-rc.6\n' >"$RUNTIME/VERSION"
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
set -a
# shellcheck disable=SC1090
source "$PARTYOPS_ENV_FILE"
set +a
DATA_DIR="${PARTYOPS_DATA_DIR:-$HOME/.local/share/partyops}"
mkdir -p "$DATA_DIR"
printf '模拟主程序启动日志：%s\n' "${FAKE_START_MARKER:-normal}" >>"$DATA_DIR/launcher.log"
if [[ "${FAKE_START_CHILD_EXIT:-0}" == "1" ]]; then
  sleep 0.2 &
  printf '%s\n' "$!" >"$DATA_DIR/partyops.pid"
else
  printf '%s\n' "$SERVER_PID" >"$DATA_DIR/partyops.pid"
fi
exit 0
EOF
cat >"$RUNTIME/partyops-wizard" <<'EOF'
#!/usr/bin/env bash
marker=wizard.url
for argument in "$@"; do
  [[ "$argument" == "--manage-shared-roots" ]] && marker=shared-root-manager.url
done
printf '%s\n' "$$" >>"$TEST_ROOT/wizard-starts.log"
sleep "${FAKE_WIZARD_MARKER_DELAY:-0}"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}/partyops"
printf 'http://127.0.0.1:%s\n' "$TEST_PORT" >"$config_root/$marker"
chmod 0600 "$config_root/$marker"
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

printf '{"status":"ok","app_version":"1.4.5-rc.6"}\n' >"$HEALTH_BODY_FILE"
export TEST_ROOT TEST_PORT=$((25000 + $$ % 10000)) OPEN_LOG NOTIFY_LOG HEALTH_BODY_FILE
export HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config"
export PATH="$BIN_DIR:$PATH"

perl -MIO::Socket::INET -e '
  my $port = shift;
  my $server = IO::Socket::INET->new(
    LocalAddr => "127.0.0.1", LocalPort => $port, Listen => 8, ReuseAddr => 1
  ) or die "listen: $!";
  while (my $client = $server->accept()) {
    while (my $line = <$client>) { last if $line =~ /^\r?\n$/; }
    open my $body_file, "<", $ENV{"HEALTH_BODY_FILE"} or die "health body: $!";
    local $/;
    my $body = <$body_file>;
    close $body_file;
    print $client "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " . length($body) . "\r\nConnection: close\r\n\r\n" . $body;
    close $client;
  }
' "$TEST_PORT" &
SERVER_PID=$!
export SERVER_PID
sleep 0.3
kill -0 "$SERVER_PID"

CONFIG_ROOT="$HOME/.config/partyops"

# 首次双击：向导就绪后必须调用默认浏览器。
"$RUNTIME/desktop-launcher.sh"
grep -qx "http://127.0.0.1:$TEST_PORT" "$OPEN_LOG"
pkill -f "$RUNTIME/partyops-wizard" >/dev/null 2>&1 || true
rm -f "$CONFIG_ROOT/wizard.url" "$OPEN_LOG"

# 并发双击必须由当前用户锁串行化，最终只允许一个配置向导进程。
rm -f "$TEST_ROOT/wizard-starts.log"
FAKE_WIZARD_MARKER_DELAY=0.5 "$RUNTIME/desktop-launcher.sh" &
first_launcher=$!
FAKE_WIZARD_MARKER_DELAY=0.5 "$RUNTIME/desktop-launcher.sh" &
second_launcher=$!
wait "$first_launcher"
wait "$second_launcher"
[[ "$(wc -l <"$TEST_ROOT/wizard-starts.log")" -eq 1 ]]
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
PARTYOPS_DATA_DIR='$TEST_ROOT/personal-data'
EOF
"$RUNTIME/desktop-launcher.sh"
grep -qx "$CONFIG_ROOT/personal.env" "$TEST_ROOT/started-with-config.log"
grep -Eq "^http://127\\.0\\.0\\.1:$TEST_PORT/\\?partyops_runtime=[0-9]+$" "$OPEN_LOG"
rm -f "$CONFIG_ROOT/personal.env" "$OPEN_LOG"

# 主机模式：用户配置同样走受控启动、健康等待和本机地址。
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"host"}
EOF
cat >"$CONFIG_ROOT/partyops.env" <<EOF
PARTYOPS_MODE=host
PARTYOPS_PORT=$TEST_PORT
PARTYOPS_TLS_ENABLED=false
PARTYOPS_DATA_DIR='$TEST_ROOT/host-data'
EOF
"$RUNTIME/desktop-launcher.sh"
grep -qx "$CONFIG_ROOT/partyops.env" "$TEST_ROOT/started-with-config.log"
grep -Eq "^http://127\\.0\\.0\\.1:$TEST_PORT/\\?partyops_runtime=[0-9]+$" "$OPEN_LOG"
rm -f "$CONFIG_ROOT/partyops.env" "$OPEN_LOG"

# 端口上残留旧版本时不得打开旧页面。启动器必须识别 app_version 不符，
# 等到受控截止时间后把 RUNTIME_VERSION_MISMATCH 写入稳定诊断。
printf '{"status":"ok","app_version":"1.4.3-rc.7"}\n' >"$HEALTH_BODY_FILE"
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"personal"}
EOF
cat >"$CONFIG_ROOT/personal.env" <<EOF
PARTYOPS_MODE=personal
PARTYOPS_PORT=$TEST_PORT
PARTYOPS_TLS_ENABLED=false
PARTYOPS_DATA_DIR='$TEST_ROOT/version-mismatch-data'
EOF
set +e
PARTYOPS_DESKTOP_HEALTH_TIMEOUT_SECONDS=2 "$RUNTIME/desktop-launcher.sh"
version_mismatch_status=$?
set -e
[[ "$version_mismatch_status" -eq 2 ]]
grep -q '\[RUNTIME_VERSION_MISMATCH\]' "$CONFIG_ROOT/startup-diagnostic.txt"
[[ ! -e "$OPEN_LOG" ]]
printf '{"status":"ok","app_version":"1.4.5-rc.6"}\n' >"$HEALTH_BODY_FILE"
rm -f "$CONFIG_ROOT/personal.env"

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

# 进程仍在但健康端点不可达时，测试不允许盲等 120 秒；必须按可控截止
# 时间失败，并把桌面日志、主程序日志、PID 和端口合并到稳定诊断文件。
cat >"$CONFIG_ROOT/mode.json" <<'EOF'
{"format_version":1,"mode":"personal"}
EOF
UNHEALTHY_PORT=$((TEST_PORT + 1))
cat >"$CONFIG_ROOT/personal.env" <<EOF
PARTYOPS_MODE=personal
PARTYOPS_PORT=$UNHEALTHY_PORT
PARTYOPS_TLS_ENABLED=false
PARTYOPS_DATA_DIR='$TEST_ROOT/unhealthy-data'
EOF
set +e
PARTYOPS_DESKTOP_HEALTH_TIMEOUT_SECONDS=2 FAKE_START_MARKER=health-timeout \
  "$RUNTIME/desktop-launcher.sh"
timeout_status=$?
set -e
[[ "$timeout_status" -eq 2 ]]
[[ -s "$CONFIG_ROOT/desktop-launch.log" ]]
[[ -s "$CONFIG_ROOT/startup-diagnostic.txt" ]]
grep -q '\[HEALTH_TIMEOUT\]' "$CONFIG_ROOT/desktop-launch.log"
grep -q '模拟主程序启动日志：health-timeout' \
  "$CONFIG_ROOT/startup-diagnostic.txt"
[[ "$(stat -c '%a' "$CONFIG_ROOT/startup-diagnostic.txt")" == "600" ]]

# 主程序已退出必须立即返回 CHILD_EXITED，并保留同一个完整诊断文件。
set +e
PARTYOPS_DESKTOP_HEALTH_TIMEOUT_SECONDS=20 FAKE_START_CHILD_EXIT=1 \
  FAKE_START_MARKER=child-exited "$RUNTIME/desktop-launcher.sh"
child_status=$?
set -e
[[ "$child_status" -eq 2 ]]
grep -q '\[CHILD_EXITED\]' "$CONFIG_ROOT/desktop-launch.log"
grep -q '模拟主程序启动日志：child-exited' \
  "$CONFIG_ROOT/startup-diagnostic.txt"
rm -f "$CONFIG_ROOT/personal.env"

# 配置目录不可创建时也必须在稳定后备目录生成非空诊断，禁止静默退出。
BLOCKED_CONFIG_HOME="$TEST_ROOT/blocked-config-home"
printf 'not-a-directory\n' >"$BLOCKED_CONFIG_HOME"
set +e
XDG_CONFIG_HOME="$BLOCKED_CONFIG_HOME" \
  XDG_STATE_HOME="$TEST_ROOT/fallback-state" \
  "$RUNTIME/desktop-launcher.sh"
blocked_config_status=$?
set -e
[[ "$blocked_config_status" -eq 2 ]]
[[ -s "$TEST_ROOT/fallback-state/partyops/desktop-launch.log" ]]
[[ -s "$TEST_ROOT/fallback-state/partyops/startup-diagnostic.txt" ]]
grep -q '\[CONFIG_DIR_UNAVAILABLE\]' \
  "$TEST_ROOT/fallback-state/partyops/startup-diagnostic.txt"

# 已存在但不能实际创建文件的路径同样不可视为可用。/proc/partyops 无法
# 创建，稳定覆盖“父目录存在、mkdir -p 失败或写探针失败”的边界。
set +e
XDG_CONFIG_HOME="/proc" \
  XDG_STATE_HOME="$TEST_ROOT/proc-config-fallback-state" \
  "$RUNTIME/desktop-launcher.sh"
proc_config_status=$?
set -e
[[ "$proc_config_status" -eq 2 ]]
[[ -s "$TEST_ROOT/proc-config-fallback-state/partyops/desktop-launch.log" ]]
[[ -s "$TEST_ROOT/proc-config-fallback-state/partyops/startup-diagnostic.txt" ]]
grep -q '\[CONFIG_DIR_UNAVAILABLE\]' \
  "$TEST_ROOT/proc-config-fallback-state/partyops/startup-diagnostic.txt"

# 配置与状态目录同时不可用时，最后一级后备必须使用 mktemp 私有随机目录，
# 避免其他本机账号预占 /tmp/partyops-UID 造成诊断再次静默丢失。
BLOCKED_STATE_HOME="$TEST_ROOT/blocked-state-home"
printf 'not-a-directory\n' >"$BLOCKED_STATE_HOME"
TEMP_FALLBACK_ROOT="$TEST_ROOT/private-temp"
mkdir -p "$TEMP_FALLBACK_ROOT"
set +e
XDG_CONFIG_HOME="$BLOCKED_CONFIG_HOME" \
  XDG_STATE_HOME="$BLOCKED_STATE_HOME" \
  TMPDIR="$TEMP_FALLBACK_ROOT" \
  "$RUNTIME/desktop-launcher.sh"
temp_fallback_status=$?
set -e
[[ "$temp_fallback_status" -eq 2 ]]
TEMP_DIAGNOSTIC="$(find "$TEMP_FALLBACK_ROOT" -mindepth 2 -maxdepth 2 \
  -name startup-diagnostic.txt -print -quit)"
[[ -n "$TEMP_DIAGNOSTIC" && -s "$TEMP_DIAGNOSTIC" ]]
[[ "$(stat -c '%a' "$(dirname "$TEMP_DIAGNOSTIC")")" == "700" ]]
grep -q '\[CONFIG_DIR_UNAVAILABLE\]' "$TEMP_DIAGNOSTIC"

# 浏览器关联失败必须返回明确状态并留下中文桌面通知，不允许静默无响应。
rm -f "$CONFIG_ROOT/mode.json"
set +e
FAKE_XDG_FAIL=1 "$RUNTIME/desktop-launcher.sh"
failure_status=$?
set -e
[[ "$failure_status" -eq 3 ]]
grep -q '党建智办启动失败' "$NOTIFY_LOG"
grep -q '系统默认浏览器未能打开' "$CONFIG_ROOT/desktop-launch.log"

# 麒麟桌面会话可能只传入极简环境。启动器必须自行恢复 PATH 与 HOME，
# 并在首次业务动作前创建日志，不能因 `set -u` 静默退出。
MINIMAL_CONFIG="$TEST_ROOT/minimal-config"
mkdir -p "$MINIMAL_CONFIG"
set +e
env -i \
  XDG_CONFIG_HOME="$MINIMAL_CONFIG" \
  XDG_STATE_HOME="$TEST_ROOT/minimal-state" \
  PATH="$BIN_DIR:/usr/bin:/bin" \
  FAKE_XDG_FAIL=1 \
  TEST_ROOT="$TEST_ROOT" TEST_PORT="$TEST_PORT" OPEN_LOG="$OPEN_LOG" \
  NOTIFY_LOG="$NOTIFY_LOG" HEALTH_BODY_FILE="$HEALTH_BODY_FILE" \
  /bin/bash "$RUNTIME/desktop-launcher.sh"
minimal_status=$?
set -e
[[ "$minimal_status" -eq 3 ]]
[[ -s "$MINIMAL_CONFIG/partyops/desktop-launch.log" ]]
grep -q '桌面启动开始' "$MINIMAL_CONFIG/partyops/desktop-launch.log"

printf 'Linux 桌面启动回归通过：首次配置、个人、主机、协同及浏览器失败诊断均正常。\n'
