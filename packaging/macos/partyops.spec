# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs


root = Path(SPECPATH).parents[1]
backend = root / "backend"
frontend = root / "frontend" / "dist" / "client"
icon_path = Path(os.environ["PARTYOPS_MACOS_ICON"]).resolve()
target_arch = os.environ["PARTYOPS_MACOS_TARGET_ARCH"]

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

ai_datas = []
ai_binaries = []
ai_hidden = []
for package in ("numpy", "tokenizers"):
    datas, binaries, hidden = collect_all(package)
    ai_datas += datas
    ai_binaries += binaries
    ai_hidden += hidden
ai_datas += collect_data_files("onnxruntime", include_py_files=True)
ai_binaries += collect_dynamic_libs("onnxruntime")
ai_hidden += ["onnxruntime"]

host_analysis = Analysis(
    [str(root / "packaging" / "uos" / "entrypoint.py")],
    pathex=[str(backend)],
    binaries=ai_binaries,
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
    target_arch=target_arch,
    debug=False,
    strip=False,
    upx=False,
    console=True,
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
    [],
    exclude_binaries=True,
    name="partyops-client",
    target_arch=target_arch,
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
    [],
    exclude_binaries=True,
    name="partyops-wizard",
    target_arch=target_arch,
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

agent_analysis = Analysis(
    [str(root / "packaging" / "macos" / "launch_agent_entrypoint.py")],
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
agent_pyz = PYZ(agent_analysis.pure)
agent_exe = EXE(
    agent_pyz,
    agent_analysis.scripts,
    [],
    exclude_binaries=True,
    name="partyops-launch-agent",
    target_arch=target_arch,
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

updater_analysis = Analysis(
    [str(root / "packaging" / "macos" / "updater_entrypoint.py")],
    pathex=[str(backend)],
    binaries=ai_binaries,
    datas=[
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
updater_pyz = PYZ(updater_analysis.pure)
# 更新 helper 必须在替换 PartyOps.app 后继续执行健康检查与回滚，因此使用
# 独立 onefile；其签名仍作为顶层 app 的嵌套代码逐项验证。
updater_exe = EXE(
    updater_pyz,
    updater_analysis.scripts,
    updater_analysis.binaries,
    updater_analysis.datas,
    [],
    name="partyops-updater",
    target_arch=target_arch,
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

launcher_analysis = Analysis(
    [str(root / "packaging" / "macos" / "launcher.py")],
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
launcher_pyz = PYZ(launcher_analysis.pure)
launcher_exe = EXE(
    launcher_pyz,
    launcher_analysis.scripts,
    [],
    exclude_binaries=True,
    name="PartyOps",
    target_arch=target_arch,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon_path),
)

runtime_collect = COLLECT(
    launcher_exe,
    host_exe,
    client_exe,
    wizard_exe,
    agent_exe,
    updater_exe,
    launcher_analysis.binaries,
    launcher_analysis.datas,
    host_analysis.binaries,
    host_analysis.datas,
    client_analysis.binaries,
    client_analysis.datas,
    wizard_analysis.binaries,
    wizard_analysis.datas,
    agent_analysis.binaries,
    agent_analysis.datas,
    strip=False,
    upx=False,
    name="PartyOps",
)

app = BUNDLE(
    runtime_collect,
    name="PartyOps.app",
    icon=str(icon_path),
    bundle_identifier="cn.partyops.desktop",
    version="1.4.3-rc.8",
    info_plist={
        "CFBundleDisplayName": "党建智办 PartyOps",
        "CFBundleShortVersionString": "1.4.3-rc.8",
        "CFBundleVersion": "1.4.3.8",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright © 2026 PartyOps Contributors",
    },
)
