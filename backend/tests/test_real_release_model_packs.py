"""可选的正式签名模型包导入、激活与真实推理验收。

默认测试矩阵不携带大体积模型。发布机显式提供最终模型包、客户端公钥和
随安装包冻结的 llama-server 后，本用例只使用公钥验签，走正式上传接口
安装包，再执行真实向量生成和两档 GGUF 推理。私钥不参与运行期验收。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from app.config import get_settings
from app.database import db_runtime
from app.local_ai import EmbeddingRuntime, LocalLlmRuntime
from app.models import AIModelPack
from fastapi.testclient import TestClient


def _required_file(environment_name: str) -> Path:
    value = os.environ.get(environment_name, "").strip()
    if not value:
        pytest.skip(f"未设置 {environment_name}，跳过正式模型发布验收")
    path = Path(value).resolve()
    if not path.is_file():
        pytest.fail(f"{environment_name} 指向的文件不存在：{path}")
    return path


def _import_pack(client: TestClient, path: Path) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/admin/ai/model-packs",
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["signature_valid"] is True
    assert payload["status"] == "installed"
    return payload


def test_release_model_packs_import_activate_and_infer(
    client: TestClient,
    admin: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del admin
    bge_pack = _required_file("PARTYOPS_RELEASE_BGE_PACK")
    qwen25_pack = _required_file("PARTYOPS_RELEASE_QWEN25_PACK")
    qwen3_pack = _required_file("PARTYOPS_RELEASE_QWEN3_PACK")
    deepseek_pack = _required_file("PARTYOPS_RELEASE_DEEPSEEK_PACK")
    public_key_path = _required_file("PARTYOPS_RELEASE_MODEL_PUBLIC_KEY")
    llama_server = _required_file("PARTYOPS_RELEASE_LLAMA_SERVER")

    settings = get_settings()
    monkeypatch.setattr(settings, "model_pack_public_key", public_key_path.read_text(encoding="ascii").strip())
    monkeypatch.setattr(settings, "local_ai_port", 18878)
    monkeypatch.setattr(LocalLlmRuntime, "_binary", staticmethod(lambda: str(llama_server)))

    imported_bge = _import_pack(client, bge_pack)
    activated_bge = client.post(
        f"/api/v1/admin/ai/model-packs/{imported_bge['id']}/activate?capability=embedding"
    )
    assert activated_bge.status_code == 200, activated_bge.text
    with db_runtime.session_factory() as db:
        pack = db.get(AIModelPack, imported_bge["id"])
        assert pack is not None
        vectors = EmbeddingRuntime().encode(
            pack,
            ["支部党员大会会议记录", "支部党员大会会议记录", "本周食堂菜单"],
            is_query=True,
        )
    decoded = [np.frombuffer(item, dtype=np.float32) for item in vectors]
    assert all(item.shape == (512,) and np.isfinite(item).all() for item in decoded)
    assert all(abs(float(np.linalg.norm(item)) - 1.0) < 1e-5 for item in decoded)
    assert float(np.dot(decoded[0], decoded[1])) > 0.99999
    assert float(np.dot(decoded[0], decoded[2])) < 0.95

    for path in (qwen25_pack, qwen3_pack, deepseek_pack):
        imported = _import_pack(client, path)
        activated = client.post(
            f"/api/v1/admin/ai/model-packs/{imported['id']}/activate?capability=llm"
        )
        assert activated.status_code == 200, activated.text
        runtime = LocalLlmRuntime()
        try:
            with db_runtime.session_factory() as db:
                pack = db.get(AIModelPack, imported["id"])
                assert pack is not None
                output = runtime.complete(
                    pack,
                    "用一句中文说明资料为什么需要核对来源，并引用来源编号。",
                    ["党务资料发布前应核对发布单位、日期和原始文件。[来源1]"],
                )
            assert output.strip()
        finally:
            runtime.stop()
