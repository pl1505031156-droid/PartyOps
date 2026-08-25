"""首次入网所需的公开 CA 下载接口；不返回私钥或主机路径。"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import get_settings
from ..pki import ensure_tls_material

router = APIRouter(tags=["bootstrap"])


@router.get("/bootstrap/ca.pem")
def download_internal_ca() -> FileResponse:
    settings = get_settings()
    material = ensure_tls_material(settings)
    return FileResponse(
        material["ca_path"],
        media_type="application/x-pem-file",
        filename="partyops-internal-ca.pem",
        headers={"Cache-Control": "no-store"},
    )
