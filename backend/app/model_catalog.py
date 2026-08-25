"""PartyOps 可选本地模型目录与硬件分档规则。"""

from __future__ import annotations

from typing import Any

MODEL_CATALOG_VERSION = 2

# “官网模型包”仅在签名资产完成发布后填写 hosted_url；没有资产时只给
# 官方来源，避免界面产生无法下载或未经验证的链接。
MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "needle2-intent",
        "name": "Needle 2 意图助手",
        "kind": "intent_router",
        "tier": "基础",
        "summary": "把自然语言指令整理成事项预览、字段和提醒建议；所有写操作仍需人工确认。",
        "official_url": "https://huggingface.co/Cactus-Compute/needle2",
        "source_url": "https://github.com/cactus-compute/needle",
        "license": "Apache-2.0",
        "min_memory_mb": 2_048,
        "recommended_memory_mb": 4_096,
        "disk_mb": 128,
        "context_tokens": 512,
        "recommended_threads": 2,
        "recommended_vram_mb": 0,
        "quantization": "原生轻量运行时",
        "delivery": "partyops_pack",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "bge-small-zh-v1.5",
        "name": "BGE Small 中文语义检索",
        "kind": "embedding",
        "tier": "基础",
        "summary": "适合普通办公电脑，增强本地文件、档案和事项的中文语义检索。",
        "official_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5",
        "source_url": "https://huggingface.co/BAAI/bge-small-zh-v1.5",
        "license": "MIT",
        "min_memory_mb": 3_072,
        "recommended_memory_mb": 6_144,
        "disk_mb": 512,
        "context_tokens": 512,
        "recommended_threads": 2,
        "recommended_vram_mb": 0,
        "quantization": "ONNX INT8",
        "delivery": "partyops_pack",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "bge-base-zh-v1.5",
        "name": "BGE Base 中文语义检索",
        "kind": "embedding",
        "tier": "均衡",
        "summary": "检索质量和速度更均衡，适合资料量较大的日常党务工作站。",
        "official_url": "https://huggingface.co/BAAI/bge-base-zh-v1.5",
        "source_url": "https://huggingface.co/BAAI/bge-base-zh-v1.5",
        "license": "MIT",
        "min_memory_mb": 4_096,
        "recommended_memory_mb": 8_192,
        "disk_mb": 1_024,
        "context_tokens": 512,
        "recommended_threads": 4,
        "recommended_vram_mb": 0,
        "quantization": "ONNX INT8",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "bge-large-zh-v1.5",
        "name": "BGE Large 中文语义检索",
        "kind": "embedding",
        "tier": "专业",
        "summary": "面向大规模资料库的高质量语义检索，优先推荐内存充足的主机。",
        "official_url": "https://huggingface.co/BAAI/bge-large-zh-v1.5",
        "source_url": "https://huggingface.co/BAAI/bge-large-zh-v1.5",
        "license": "MIT",
        "min_memory_mb": 8_192,
        "recommended_memory_mb": 16_384,
        "disk_mb": 2_048,
        "context_tokens": 512,
        "recommended_threads": 6,
        "recommended_vram_mb": 0,
        "quantization": "ONNX INT8",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-0.6b-gguf",
        "name": "Qwen3 0.6B 本地草稿",
        "kind": "llm",
        "tier": "基础",
        "summary": "轻量中文草稿、标题改写和操作引导，适合 8GB 内存办公电脑。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 6_144,
        "recommended_memory_mb": 8_192,
        "disk_mb": 1_600,
        "context_tokens": 2_048,
        "recommended_threads": 4,
        "recommended_vram_mb": 2_048,
        "quantization": "GGUF Q4_K_M",
        "delivery": "partyops_pack",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-1.7b-gguf",
        "name": "Qwen3 1.7B 本地助手",
        "kind": "llm",
        "tier": "均衡",
        "summary": "更好的中文理解和结构化草稿能力，适合 16GB 内存电脑。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 10_240,
        "recommended_memory_mb": 16_384,
        "disk_mb": 3_200,
        "context_tokens": 4_096,
        "recommended_threads": 6,
        "recommended_vram_mb": 4_096,
        "quantization": "GGUF Q4_K_M",
        "delivery": "partyops_pack",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-4b-gguf",
        "name": "Qwen3 4B 专业草稿",
        "kind": "llm",
        "tier": "进阶",
        "summary": "适合较复杂的材料梳理、结构化摘要和长段落草拟。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-4B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-4B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 16_384,
        "recommended_memory_mb": 24_576,
        "disk_mb": 6_144,
        "context_tokens": 8_192,
        "recommended_threads": 8,
        "recommended_vram_mb": 6_144,
        "quantization": "GGUF Q4_K_M",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-8b-gguf",
        "name": "Qwen3 8B 高质量助手",
        "kind": "llm",
        "tier": "专业",
        "summary": "面向高配置工作站的高质量中文草稿、归纳和多轮指引。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 24_576,
        "recommended_memory_mb": 32_768,
        "disk_mb": 10_240,
        "context_tokens": 8_192,
        "recommended_threads": 10,
        "recommended_vram_mb": 10_240,
        "quantization": "GGUF Q4_K_M",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-14b-gguf",
        "name": "Qwen3 14B 高端工作站",
        "kind": "llm",
        "tier": "高端",
        "summary": "适合 48GB 以上内存或大显存设备；模型较大，仅提供官方来源与本地服务接入指引。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-14B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-14B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 36_864,
        "recommended_memory_mb": 49_152,
        "disk_mb": 18_432,
        "context_tokens": 16_384,
        "recommended_threads": 12,
        "recommended_vram_mb": 18_432,
        "quantization": "GGUF Q4_K_M",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-30b-a3b-gguf",
        "name": "Qwen3 30B-A3B 专家档",
        "kind": "llm",
        "tier": "旗舰",
        "summary": "面向 64GB 以上高端主机的专家档模型；通过官方运行器提供 OpenAI 兼容本地接口。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 49_152,
        "recommended_memory_mb": 65_536,
        "disk_mb": 36_864,
        "context_tokens": 16_384,
        "recommended_threads": 16,
        "recommended_vram_mb": 24_576,
        "quantization": "GGUF Q4_K_M",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-32b-gguf",
        "name": "Qwen3 32B 旗舰模型",
        "kind": "llm",
        "tier": "旗舰",
        "summary": "最高质量档，适合 64GB 以上内存与专业 GPU；仅提供官方链接和本地服务接入方式。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-32B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-32B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 57_344,
        "recommended_memory_mb": 65_536,
        "disk_mb": 40_960,
        "context_tokens": 16_384,
        "recommended_threads": 16,
        "recommended_vram_mb": 24_576,
        "quantization": "GGUF Q4_K_M",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
    {
        "id": "qwen3-235b-a22b-gguf",
        "name": "Qwen3 235B-A22B 服务器级模型",
        "kind": "llm",
        "tier": "服务器",
        "summary": "面向多 GPU 或超大统一内存主机的最高能力档；只提供 Qwen 官方分片模型与本地服务接入方式。",
        "official_url": "https://huggingface.co/Qwen/Qwen3-235B-A22B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-235B-A22B-GGUF",
        "license": "Apache-2.0",
        "min_memory_mb": 196_608,
        "recommended_memory_mb": 262_144,
        "disk_mb": 184_320,
        "context_tokens": 16_384,
        "recommended_threads": 24,
        "recommended_vram_mb": 163_840,
        "quantization": "GGUF Q4_K_M（官方分片）",
        "delivery": "official",
        "hosted_url": "",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["amd64", "arm64"],
    },
]


def recommend_models(profile: dict[str, object]) -> list[dict[str, Any]]:
    total = int(profile.get("total_memory_mb", 0) or 0)
    available = int(profile.get("available_memory_mb", 0) or 0)
    reserve = int(profile.get("reserved_memory_mb", 2_048) or 2_048)
    disk = int(profile.get("model_disk_free_mb", 0) or 0)
    platform_name = str(profile.get("platform", "unknown"))
    architecture = str(profile.get("architecture", "unknown"))
    gpu_memory = int(profile.get("gpu_memory_mb", 0) or 0)
    gpu_backends = {str(value) for value in profile.get("gpu_backends", []) if value}
    usable_now = max(0, available - min(reserve, available))
    results: list[dict[str, Any]] = []
    for source in MODEL_CATALOG:
        item = dict(source)
        reasons: list[str] = []
        if platform_name not in item["platforms"] or architecture not in item["architectures"]:
            status = "不建议"
            reasons.append("当前系统或处理器架构不在该模型的验证范围内")
        elif disk < int(item["disk_mb"] * 1.25):
            status = "不建议"
            reasons.append("模型目录空间不足，需同时保留下载、安装和 25% 安全余量")
        elif total < int(item["min_memory_mb"]):
            status = "不建议"
            reasons.append("物理内存低于最低要求")
        elif total >= int(item["recommended_memory_mb"]) and usable_now >= max(1_024, int(item["min_memory_mb"]) // 3):
            status = "流畅"
            required_vram = int(item.get("recommended_vram_mb", 0) or 0)
            if required_vram and gpu_memory >= required_vram and gpu_backends.intersection({"cuda", "metal"}):
                reasons.append("内存、磁盘和可用显存均达到推荐值，可优先启用硬件加速")
            else:
                reasons.append("总内存、当前余量和模型磁盘空间均达到推荐值")
        else:
            status = "可用"
            reasons.append("系统将降低线程数、上下文和并发以保留办公余量")
        item["status"] = status
        item["reason"] = "；".join(reasons)
        item["effective_threads"] = min(int(item["recommended_threads"]), max(1, int(profile.get("cpu_cores", 1) or 1) // 2))
        item["effective_context_tokens"] = int(item["context_tokens"]) if status == "流畅" else min(2_048, int(item["context_tokens"]))
        results.append(item)
    order = {"流畅": 0, "可用": 1, "不建议": 2}
    return sorted(results, key=lambda item: (order[item["status"]], item["min_memory_mb"], item["name"]))
