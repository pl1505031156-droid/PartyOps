# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parents[1]
backend = root / "backend"
frontend = root / "frontend" / "dist" / "client"

common_hidden = [
    "pysqlite3",
    "pysqlite3.dbapi2",
    "pysqlite3._sqlite3",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "cryptography",
    "cryptography.fernet",
    "httpx",
]

# 本地智能依赖在业务代码中按需导入，PyInstaller 无法通过静态分析发现。
# 由规范文件集中收集，避免在业务模块中加入仅为打包服务的强耦合导入。
ai_datas = []
ai_binaries = []
ai_hidden = []
for package in ("numpy", "onnxruntime", "tokenizers"):
    datas, binaries, hidden = collect_all(package)
    ai_datas += datas
    ai_binaries += binaries
    ai_hidden += hidden

host_analysis = Analysis(
    [str(root / "packaging" / "uos" / "entrypoint.py")],
    pathex=[str(backend)],
    binaries=ai_binaries,
    # 除前端与 AI 依赖外，必须把 Alembic 迁移脚本与配置一并打进
    # _internal/。database.py 在冻结环境下从 _internal/alembic 读取迁移
    # 链，缺失会导致首次启动数据库升级失败（历史教训：1.3.3 便携包
    # 曾因此构建冒烟失败）。迁移目录下新增版本文件无需改此清单。
    datas=[
        (str(frontend), "frontend"),
        (str(backend / "alembic"), "alembic"),
        (str(backend / "alembic.ini"), "."),
        *ai_datas,
    ],
    hiddenimports=[*common_hidden, *ai_hidden],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
host_pyz = PYZ(host_analysis.pure)
host_exe = EXE(
    host_pyz,
    host_analysis.scripts,
    [],
    exclude_binaries=True,
    name="partyops",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
host_collect = COLLECT(
    host_exe,
    host_analysis.binaries,
    host_analysis.datas,
    strip=False,
    upx=False,
    name="PartyOps",
)

client_analysis = Analysis(
    [str(root / "packaging" / "uos" / "client_entrypoint.py")],
    pathex=[str(backend)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
client_pyz = PYZ(client_analysis.pure)
client_exe = EXE(
    client_pyz,
    client_analysis.scripts,
    client_analysis.binaries,
    client_analysis.datas,
    [],
    name="partyops-client",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

wizard_analysis = Analysis(
    [str(root / "packaging" / "uos" / "wizard_entrypoint.py")],
    pathex=[str(backend)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
wizard_pyz = PYZ(wizard_analysis.pure)
wizard_exe = EXE(
    wizard_pyz,
    wizard_analysis.scripts,
    wizard_analysis.binaries,
    wizard_analysis.datas,
    [],
    name="partyops-wizard",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

updater_analysis = Analysis(
    [str(root / "packaging" / "uos" / "updater_entrypoint.py")],
    pathex=[str(backend)],
    binaries=[],
    datas=[],
    hiddenimports=common_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
updater_pyz = PYZ(updater_analysis.pure)
updater_exe = EXE(
    updater_pyz,
    updater_analysis.scripts,
    updater_analysis.binaries,
    updater_analysis.datas,
    [],
    name="partyops-updater",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
