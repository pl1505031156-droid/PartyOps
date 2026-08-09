"""1.2.0 工作整合域的兼容路由组合器。

具体实现按领域拆分；保留本模块是为了让既有 ``main.py`` 和外部扩展无需
在 1.2.0 升级时同步更改导入路径。
"""

from fastapi import APIRouter

from . import appearance, calendar, guidance, recurrence_extensions, relations, today


router = APIRouter()
router.include_router(today.router)
router.include_router(calendar.router)
router.include_router(relations.router)
router.include_router(recurrence_extensions.router)
router.include_router(guidance.router)
router.include_router(appearance.router)
