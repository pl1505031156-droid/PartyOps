#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/partyops-postinstall.XXXXXX")"
RUNTIME="$TEST_ROOT/runtime"
BIN="$TEST_ROOT/bin"
cleanup() {
  case "$TEST_ROOT" in
    "${TMPDIR:-/tmp}"/partyops-postinstall.*) rm -rf -- "$TEST_ROOT" ;;
    *) printf '拒绝清理异常测试目录：%s\n' "$TEST_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM
mkdir -p "$RUNTIME" "$BIN" "$TEST_ROOT/run/partyops"
cp "$ROOT/packaging/linux/post-install-transaction.sh" "$RUNTIME/"
cp "$ROOT/packaging/linux/post-install-services.sh" "$RUNTIME/"

cat >"$RUNTIME/post-install-selftest.sh" <<'EOF'
#!/usr/bin/env sh
echo "selftest:$1:$2" >>"$PARTYOPS_TEST_CALLS"
[ "${FAIL_SELFTEST:-0}" != "1" ] || {
  echo '[PACKAGE_RUNTIME_SELFTEST_FAILED] 模拟运行时自检失败' >&2
  exit 2
}
EOF
cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env sh
echo "systemctl:$*" >>"$PARTYOPS_TEST_CALLS"
[ "${FAIL_SYSTEMCTL_ACTION:-}" != "${1:-}" ]
EOF
cat >"$BIN/journalctl" <<'EOF'
#!/usr/bin/env sh
echo "journalctl:$*" >>"$PARTYOPS_TEST_CALLS"
exit 0
EOF
chmod 0755 "$RUNTIME"/*.sh "$BIN"/*

export PARTYOPS_PACKAGE_TESTING=1
export PARTYOPS_PACKAGE_TEST_ROOT="$TEST_ROOT"
export PARTYOPS_PACKAGE_TEST_RUNTIME="$RUNTIME"
export PARTYOPS_TEST_CALLS="$TEST_ROOT/calls.log"
export PATH="$BIN:$PATH"

: >"$PARTYOPS_TEST_CALLS"
touch "$TEST_ROOT/run/partyops/restart-after-upgrade"
"$RUNTIME/post-install-transaction.sh" amd64 deb >/dev/null 2>&1
grep -qx 'selftest:amd64:quick' "$PARTYOPS_TEST_CALLS"
grep -q 'systemctl:enable partyops-updater.service' "$PARTYOPS_TEST_CALLS"
grep -q 'systemctl:restart --no-block partyops.service' "$PARTYOPS_TEST_CALLS"
grep -q 'systemctl:start --no-block partyops-install-verify.service' "$PARTYOPS_TEST_CALLS"
[[ ! -e "$TEST_ROOT/run/partyops/restart-after-upgrade" ]]

: >"$PARTYOPS_TEST_CALLS"
set +e
FAIL_SELFTEST=1 "$RUNTIME/post-install-transaction.sh" arm64 deb \
  >"$TEST_ROOT/selftest.out" 2>&1
selftest_status=$?
set -e
[[ "$selftest_status" -eq 2 ]]
grep -q 'PACKAGE_RUNTIME_SELFTEST_FAILED' "$TEST_ROOT/selftest.out"
! grep -q '^systemctl:' "$PARTYOPS_TEST_CALLS"

for action in enable start; do
  : >"$PARTYOPS_TEST_CALLS"
  set +e
  FAIL_SYSTEMCTL_ACTION="$action" "$RUNTIME/post-install-transaction.sh" amd64 deb \
    >"$TEST_ROOT/$action.out" 2>&1
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  grep -q 'PACKAGE_UPDATER_START_FAILED' "$TEST_ROOT/$action.out"
  grep -q 'dpkg --configure -a' "$TEST_ROOT/$action.out"
  grep -q 'systemctl:disable --now partyops-updater.service' "$PARTYOPS_TEST_CALLS"
done

: >"$PARTYOPS_TEST_CALLS"
touch "$TEST_ROOT/run/partyops/restart-after-upgrade"
set +e
FAIL_SYSTEMCTL_ACTION=restart "$RUNTIME/post-install-transaction.sh" arm64 rpm \
  >"$TEST_ROOT/restart.out" 2>&1
restart_status=$?
set -e
[[ "$restart_status" -eq 2 ]]
grep -q 'PACKAGE_HOST_RESTART_FAILED' "$TEST_ROOT/restart.out"
grep -q '重新安装当前 RPM' "$TEST_ROOT/restart.out"
[[ -e "$TEST_ROOT/run/partyops/restart-after-upgrade" ]]

printf 'Linux 安装后事务回归通过：30 秒内快速检查、服务启用、后台验证与失败诊断均正常。\n'
