"""从 PyPI 官方 JSON 获取并校验固定版本源码归档。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


MAX_SOURCE_BYTES = 256 * 1024 * 1024
TRUSTED_FILE_HOSTS = {"files.pythonhosted.org", "pypi.org"}


def download(requirement: str, destination: Path) -> Path:
    """下载一个 ``name==version`` 的唯一 sdist，并按 PyPI 摘要复核。"""

    if requirement.count("==") != 1:
        raise ValueError(f"依赖必须固定为 name==version：{requirement}")
    name, version = (part.strip() for part in requirement.split("==", 1))
    if not name or not version or any(value in name for value in ("/", "\\", "..")):
        raise ValueError(f"依赖名称或版本无效：{requirement}")
    metadata_url = (
        f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/"
        f"{urllib.parse.quote(version, safe='')}/json"
    )
    with urllib.request.urlopen(metadata_url, timeout=30) as response:  # nosec B310 - 元数据地址固定为 PyPI 官方 HTTPS API。
        metadata = json.load(response)
    candidates = [
        item for item in metadata.get("urls", []) if item.get("packagetype") == "sdist"
    ]
    if len(candidates) != 1:
        raise ValueError(f"PyPI sdist 数量异常：{requirement}，实际 {len(candidates)}")
    candidate = candidates[0]
    url = str(candidate.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_FILE_HOSTS:
        raise ValueError(f"PyPI sdist 来源不受信：{url}")
    filename = Path(parsed.path).name
    if not filename or filename != str(candidate.get("filename") or ""):
        raise ValueError("PyPI sdist 文件名与下载地址不一致")
    expected_sha256 = str(candidate.get("digests", {}).get("sha256") or "").lower()
    expected_size = int(candidate.get("size") or 0)
    if len(expected_sha256) != 64 or expected_size <= 0 or expected_size > MAX_SOURCE_BYTES:
        raise ValueError("PyPI sdist 摘要或体积无效")

    destination.mkdir(parents=True, exist_ok=True)
    target = destination / filename
    temporary = destination / f".{filename}.{os.getpid()}.part"
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("xb") as output:  # nosec B310 - 载荷方案与主机已在上方限定为 PyPI 官方 HTTPS。
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("PyPI sdist 超过安全体积上限")
                digest.update(chunk)
                output.write(chunk)
        if total != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError("PyPI sdist 大小或 SHA-256 与官方 JSON 不一致")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    evidence = {
        "package": name,
        "version": version,
        "filename": filename,
        "url": url,
        "size": total,
        "sha256": expected_sha256,
        "upload_time": candidate.get("upload_time_iso_8601"),
    }
    target.with_name(f"{filename}.pypi.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("requirement")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(download(args.requirement, args.destination.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
