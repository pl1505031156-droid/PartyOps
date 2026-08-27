"""党建智办后端服务。"""

# 包初始化先建立 Python 3.8 typing 兼容名称，再允许 FastAPI/Starlette 等
# 安全新版依赖被子模块导入。Python 3.10+ 路径不会修改标准库。
from .compat import install_legacy_hashlib_compat, install_legacy_typing_aliases

install_legacy_hashlib_compat()
install_legacy_typing_aliases()

__version__ = "1.4.5-rc.6"
