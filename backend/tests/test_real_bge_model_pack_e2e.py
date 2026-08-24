"""可选的真实 BGE 模型包导入与推理验收。

默认测试矩阵不下载或携带模型。发布人员显式提供已审核的 ONNX、tokenizer
与许可证后，本用例使用隔离临时信任根构建模型包，再走 PartyOps 正式接口
完成导入、启用和真实推理；临时签名包不得作为生产资产发布。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import db_runtime
from app.local_ai import EmbeddingRuntime
from app.models import AIModelPack


ROOT = Path(__file__).resolve().parents[2]


def _required_file(environment_name: str) -> Path:
    value = os.environ.get(environment_name, "").strip()
    if not value:
        pytest.skip(f"未设置 {environment_name}，跳过真实模型发布验收")
    path = Path(value).resolve()
    if not path.is_file():
        pytest.fail(f"{environment_name} 指向的文件不存在：{path}")
    return path


def test_real_bge_pack_import_activate_and_infer(
    client: TestClient,
    admin: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = _required_file("PARTYOPS_REAL_BGE_ONNX")
    tokenizer = _required_file("PARTYOPS_REAL_BGE_TOKENIZER")
    license_file = _required_file("PARTYOPS_REAL_BGE_LICENSE")

    private_key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "test-only-private-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key_path.chmod(0o600)
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    public_key_path = tmp_path / "update-public-key.txt"
    public_key_path.write_text(public_key + "\n", encoding="ascii")
    monkeypatch.setattr(get_settings(), "model_pack_public_key", public_key)

    output = tmp_path / "bge-small-zh-v1.5-test.partyops-modelpack"
    command = [
        sys.executable,
        str(ROOT / "packaging" / "uos" / "build-model-pack.py"),
        "--embedding",
        str(model),
        "--tokenizer",
        str(tokenizer),
        "--license",
        str(license_file),
        "--private-key",
        str(private_key_path),
        "--public-key",
        str(public_key_path),
        "--version",
        "1.5.0-test",
        "--model-id",
        "bge-small-zh-v1.5-onnx-fp32",
        "--name",
        "BGE Small 中文语义检索（真实发布验收）",
        "--estimated-memory-mb",
        "640",
        "--min-memory-mb",
        "2048",
        "--recommended-memory-mb",
        "4096",
        "--measured-peak-memory-mb",
        "640",
        "--model-source",
        "https://huggingface.co/BAAI/bge-small-zh-v1.5",
        "--license-name",
        "MIT",
        "--output",
        str(output),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert output.is_file() and output.stat().st_size > 80 * 1024 * 1024

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format_version"] == 2
    assert manifest["public_key"] == public_key
    assert manifest["components"]["embedding"]["dimension"] == 512

    with output.open("rb") as handle:
        imported = client.post(
            "/api/v1/admin/ai/model-packs",
            files={
                "file": (
                    output.name,
                    handle,
                    "application/octet-stream",
                )
            },
        )
    assert imported.status_code == 201, imported.text
    pack_id = imported.json()["id"]
    activated = client.post(
        f"/api/v1/admin/ai/model-packs/{pack_id}/activate?capability=embedding"
    )
    assert activated.status_code == 200, activated.text

    with db_runtime.session_factory() as db:
        pack = db.get(AIModelPack, pack_id)
        assert pack is not None
        vectors = EmbeddingRuntime().encode(
            pack,
            ["支部党员大会会议记录", "支部党员大会会议记录", "本周食堂菜单"],
        )
    decoded = [np.frombuffer(item, dtype=np.float32) for item in vectors]
    assert all(item.shape == (512,) for item in decoded)
    assert all(np.isfinite(item).all() for item in decoded)
    assert all(abs(float(np.linalg.norm(item)) - 1.0) < 1e-5 for item in decoded)
    assert float(np.dot(decoded[0], decoded[1])) > 0.99999
    assert float(np.dot(decoded[0], decoded[2])) < 0.95
