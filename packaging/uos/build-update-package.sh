#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS="$ROOT/artifacts"
VERSION="${PARTYOPS_VERSION:-1.4.2}"
OUT="$ARTIFACTS/partyops_${VERSION}.partyops-update"
WORK="$(mktemp -d "$ROOT/.build-uos/update.XXXXXX")"
trap 'rm -r -- "$WORK"' EXIT

[[ -n "${PARTYOPS_UPDATE_PRIVATE_KEY_FILE:-}" ]] || {
  echo "正式更新包必须签名，请设置 PARTYOPS_UPDATE_PRIVATE_KEY_FILE。" >&2
  exit 2
}
[[ -f "$PARTYOPS_UPDATE_PRIVATE_KEY_FILE" ]] || {
  echo "签名私钥文件不存在：$PARTYOPS_UPDATE_PRIVATE_KEY_FILE" >&2
  exit 2
}

for arch in amd64 arm64; do
  file="$ARTIFACTS/partyops_${VERSION}_${arch}.deb"
  [[ -f "$file" ]] || { echo "缺少 $file，请先分别构建两种架构 Debian 包。" >&2; exit 2; }
  cp -- "$file" "$WORK/"
done
windows_file="$ARTIFACTS/PartyOps_${VERSION}_windows_amd64.exe"
[[ -f "$windows_file" ]] || { echo "缺少 $windows_file，请先在 Windows x64 构建安装器。" >&2; exit 2; }
cp -- "$windows_file" "$WORK/"

PYTHON="${PYTHON_BIN:-$(command -v python3)}"
"$PYTHON" - "$WORK" "$VERSION" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
artifacts = {}
for path in sorted([*root.glob("*.deb"), *root.glob("*.exe")]):
    artifacts[path.name] = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
manifest = {
    "format": "partyops-update",
    "format_version": 2,
    "version": version,
    "min_version": "1.3.4",
    "schema_revision": "0017",
    "release_title": "真文件共享与本地智能发布收口",
    "architecture_artifacts": {
        "amd64": f"partyops_{version}_amd64.deb",
        "arm64": f"partyops_{version}_arm64.deb",
    },
    "platform_artifacts": {
        "uos": {
            "amd64": f"partyops_{version}_amd64.deb",
            "arm64": f"partyops_{version}_arm64.deb",
        },
        "windows": {
            "amd64": f"PartyOps_{version}_windows_amd64.exe",
        },
    },
    "artifacts": artifacts,
    "release_notes": [
        "协同机普通用户可通过系统选择器发布、管理本机真实目录并设置团队或指定成员范围",
        "单文件、多选与文件夹 ZIP 支持浏览器另存为及当前协同机断点接收，设备间继续由主机校验中转",
        "主机与协同机界面统一读取运行上下文和有效能力，管理员入口对普通用户隐藏并在直达时拒绝",
        "中文向量与本地 LLM 改为独立签名模型包，BGE 使用清单定义的 CLS 池化并按目录授权正文索引",
        "数据库升级到 0017，支持从 1.3.4 和 1.4.0 候选版直接升级并在失败时恢复升级前备份",
        "统一包同时包含 UOS amd64、UOS arm64 和 Windows x64 安装制品",
    ],
    "signature": "",
}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
(root / "RELEASE-NOTES.txt").write_text(
    f"党建智办 {version} 原位更新包\n"
    "导入系统设置后由受限更新服务执行备份、迁移、健康检查和失败回滚。\n",
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
unsigned = dict(manifest)
unsigned.pop("signature", None)
public_key = base64.b64encode(
    key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
).decode("ascii")
unsigned["public_key"] = public_key
canonical = json.dumps(
    unsigned,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
manifest["signature"] = base64.b64encode(key.sign(canonical)).decode("ascii")
manifest["public_key"] = public_key
path.write_text(
    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
PY
if [[ -f "$ROOT/packaging/uos/update-public-key.txt" ]]; then
  "$PYTHON" - "$WORK/manifest.json" "$ROOT/packaging/uos/update-public-key.txt" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").strip()
if manifest.get("public_key") != expected:
    raise SystemExit("签名私钥与安装包内置公钥不匹配，拒绝生成更新包")
PY
fi

(cd "$WORK" && zip -q -r "$OUT" .)
"$PYTHON" "$ROOT/scripts/validate-partyops-update.py" \
  "$OUT" \
  --public-key "$ROOT/packaging/uos/update-public-key.txt" \
  --expected-version "$VERSION"
(cd "$ARTIFACTS" && sha256sum "$(basename "$OUT")" > "partyops_${VERSION}.partyops-update.sha256")
if [[ -f "$ROOT/docs/党建智办-${VERSION}-更新说明.txt" ]]; then
  cp -- "$ROOT/docs/党建智办-${VERSION}-更新说明.txt" "$ARTIFACTS/党建智办-${VERSION}-更新说明.txt"
fi
echo "更新包已生成：$OUT"
