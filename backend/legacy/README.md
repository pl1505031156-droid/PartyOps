# Windows 7 Legacy 构建链

该目录只服务于 Python 3.8 的 Windows 7 SP1 x64/x86 制品。主线仍使用
Python 3.11，Legacy 锁文件和原生轮子不得复制进主线 wheelhouse。

`requirements-windows7-*.in` 固定可解析的上游基线。正式构建还必须提供
`security-backports.json` 要求的 PartyOps 安全回移轮子及每项证据；缺少任一
字段时验证器返回非零，安装器不得生成。

x64 运行档位为 `legacy-full`，包含语义重排和本地 LLM；x86 为
`legacy-core`，保留主机、协同、数据库、文件、档案、备份和 OCR，明确不含
语义重排与本地 LLM。

构建入口为 `packaging/windows/build-windows7.ps1`。它要求 Python Software
Foundation 官方完整 CPython 3.8.10（必须包含 Tcl/Tk 图形运行时）、一次性
全新 wheelhouse 和安全回移证据目录。x86 会自动选用仓库内固定哈希的 SQLite
3.53.4 x86 DLL 与 Win7 x86 静态 Tesseract；x64 使用各自的 amd64 运行时。
安装包生成前会扫描冻结目录内全部 EXE/DLL/PYD，只接受 PE 子系统 6.1 及以下，
并拒绝已知仅 Windows 8/10 提供的导入 API。缺少 `_tkinter`、Tcl/Tk 资源、
中文 OCR、SQLite 或任一架构闭包时均不得生成可发布安装包。
