#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS="$ROOT/artifacts"
VERSION="1.4.4"
OUT="$ARTIFACTS/partyops_${VERSION}.partyops-update"
WORK="$(mktemp -d "$ROOT/.build-uos/update.XXXXXX")"
cleanup() {
  status=$?
  trap - EXIT
  case "$WORK" in "$ROOT/.build-uos/update."*) rm -rf -- "$WORK" ;; esac
  exit "$status"
}
trap cleanup EXIT

[[ -n "${PARTYOPS_UPDATE_PRIVATE_KEY_FILE:-}" && -f "$PARTYOPS_UPDATE_PRIVATE_KEY_FILE" ]] || {
  echo "正式更新包必须设置有效的 PARTYOPS_UPDATE_PRIVATE_KEY_FILE。" >&2
  exit 2
}

FILES=(
  "PartyOps_1.4.4_windows_amd64.exe"
  "PartyOps_1.4.4_windows7_amd64.exe"
  "PartyOps_1.4.4_windows7_x86.exe"
  "PartyOps_1.4.4_linux_amd64.deb"
  "PartyOps_1.4.4_linux_arm64.deb"
  "PartyOps-1.4.4-1.x86_64.rpm"
  "PartyOps-1.4.4-1.aarch64.rpm"
)
for filename in "${FILES[@]}"; do
  [[ -f "$ARTIFACTS/$filename" ]] || {
    echo "缺少已通过独立门禁的制品：$filename" >&2
    exit 2
  }
  cp -- "$ARTIFACTS/$filename" "$WORK/"
done

PYTHON="${PYTHON_BIN:-$(command -v python3)}"
"$PYTHON" - "$WORK" "$VERSION" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
files = [path for path in root.iterdir() if path.suffix in {".exe", ".deb", ".rpm"}]
artifacts = {
    path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    for path in sorted(files)
}
manifest = {
    "format": "partyops-update",
    "format_version": 3,
    "version": version,
    "min_version": "1.4.3-rc.9",
    "schema_revision": "0020",
    "release_title": "多系统适配与专业级应用内升级",
    "platform_artifacts": {
        "windows": {"amd64": "PartyOps_1.4.4_windows_amd64.exe"},
        "windows7": {
            "amd64": "PartyOps_1.4.4_windows7_amd64.exe",
            "x86": "PartyOps_1.4.4_windows7_x86.exe",
        },
        "linux-deb": {
            "amd64": "PartyOps_1.4.4_linux_amd64.deb",
            "arm64": "PartyOps_1.4.4_linux_arm64.deb",
        },
        "linux-rpm": {
            "amd64": "PartyOps-1.4.4-1.x86_64.rpm",
            "arm64": "PartyOps-1.4.4-1.aarch64.rpm",
        },
    },
    "artifacts": artifacts,
    "release_notes": [
        "新增系统内检查、后台下载和一键原位升级，失败自动回滚且不丢失业务数据",
        "在线更新按当前系统与架构精确下载，不再下载其他平台安装器",
        "旧协同凭据 401/403 停止灾备重试，页面启动与同步彻底解耦并支持管理员重新授权",
        "修复 Windows CHILD_EXITED、Win7 自启动拒绝访问以及 macOS/Linux 双击无响应诊断",
        "新增一次性文件打开授权、PDF 同源预览和 WPS 本机打开降级",
        "新增用户归档恢复、网络与协同设置、会议六步流程、在线结构化文档和发展党员档案",
        "任务改期后立即更新或撤销未读通知，已读历史继续保留审计",
        "新增 Win7 SP1 x64/x86 独立 Legacy 运行时与安全回移门禁",
        "新增麒麟、UOS、deepin 的 DEB 与 openEuler RPM 双架构原生包",
        "安装后自动核对文件、前端、SQLite/FTS5、OCR、智能运行时与健康端点",
    ],
    "signature": "",
}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
(root / "RELEASE-NOTES.txt").write_text(
    "党建智办 PartyOps 1.4.4 原位更新包\n"
    "由签名清单按系统、包格式和架构精确选包；更新失败自动保留数据并回滚程序。\n",
    encoding="utf-8",
)
PY

"$PYTHON" - "$WORK/manifest.json" "$PARTYOPS_UPDATE_PRIVATE_KEY_FILE" <<'PY'
import base64
import json
import pathlib
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

path = pathlib.Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"))
key_data = pathlib.Path(sys.argv[2]).read_bytes()
try:
    key = serialization.load_pem_private_key(key_data, password=None)
except ValueError:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_data.strip()))
if not isinstance(key, Ed25519PrivateKey):
    raise SystemExit("更新签名必须使用 Ed25519 私钥")
public_key = base64.b64encode(
    key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
).decode("ascii")
unsigned = dict(manifest)
unsigned.pop("signature", None)
unsigned["public_key"] = public_key
canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
manifest.update(public_key=public_key, signature=base64.b64encode(key.sign(canonical)).decode("ascii"))
path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY

(cd "$WORK" && zip -q -r "$OUT" .)
"$PYTHON" "$ROOT/scripts/validate-partyops-update.py" "$OUT" \
  --public-key "$ROOT/packaging/uos/update-public-key.txt" \
  --expected-version "$VERSION"
sha256sum "$OUT" >"$OUT.sha256"
echo "format v3 更新包已生成：$OUT"

# 普通用户在线升级下载 format v4 单平台更新包；上面的 format v3 通用包
# 仅保留给管理员离线集中分发，避免一台电脑下载七个平台的安装器。
"$PYTHON" "$ROOT/scripts/build-platform-update-packages.py" \
  --artifacts-dir "$ARTIFACTS" \
  --output-dir "$ARTIFACTS" \
  --private-key "$PARTYOPS_UPDATE_PRIVATE_KEY_FILE" \
  --public-key "$ROOT/packaging/uos/update-public-key.txt"
