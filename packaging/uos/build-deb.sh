#!/usr/bin/env bash
set -euo pipefail

# 兼容旧入口；rc.3 起 DEB/RPM 统一由国产 Linux 平台层生成。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/packaging/linux/build-native.sh" deb
