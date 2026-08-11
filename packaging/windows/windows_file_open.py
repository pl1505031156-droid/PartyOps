"""partyops-file:// Windows 协议处理器，仅打开主机签发的一次性本地路径。"""

from __future__ import annotations

import os
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from app.setup_wizard import load_host_environment


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    parsed = urllib.parse.urlparse(sys.argv[1])
    token = (parsed.path or parsed.netloc).strip("/")
    if parsed.scheme != "partyops-file" or not token or not token.replace("-", "").replace("_", "").isalnum():
        return 2
    data_root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
    config = load_host_environment(data_root / "partyops.env")
    host = config.get("PARTYOPS_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        host = "127.0.0.1"
    port = config.get("PARTYOPS_PORT", "18765")
    scheme = "https" if config.get("PARTYOPS_TLS_ENABLED", "").lower() == "true" else "http"
    context = None
    if scheme == "https":
        ca = data_root / "secrets" / "pki" / "ca.pem"
        if not ca.is_file():
            return 3
        context = ssl.create_default_context(cafile=str(ca))
    request = urllib.request.Request(f"{scheme}://{host}:{port}/api/v1/workspace/open-tokens/{urllib.parse.quote(token)}")
    with urllib.request.urlopen(  # nosec B310 - scheme 固定为 HTTP(S)，host 被强制为回环地址。
        request,
        timeout=10,
        context=context,
    ) as response:
        target = Path(response.read(32 * 1024).decode("utf-8")).resolve(strict=True)
    os.startfile(target)  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
