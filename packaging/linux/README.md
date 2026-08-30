# PartyOps 国产 Linux 原生包

## glibc 2.17 本地 LLM 运行时

上游 Ubuntu 版 llama.cpp 可能依赖较新的 glibc、libstdc++ 与 OpenSSL，不能
直接放入麒麟、UOS、deepin 或 openEuler 的兼容包。请先在目标架构的
manylinux2014/glibc 2.17 环境运行：

```bash
PARTYOPS_BUILD_ARCH=amd64 bash packaging/linux/build-llama-runtime.sh
```

ARM64 使用相同命令并把架构改为 `arm64`。脚本固定上游标签、提交和源码
SHA-256，以静态 libstdc++/libgcc、关闭 OpenSSL 的方式生成 CPU 运行时，随后
核对 ELF 架构、动态依赖、最高 glibc 符号版本和 `--version`。生成物先进入
`artifacts/tooling/`，审核通过后才允许替换 `vendor/local-ai/<arch>/` 输入。

本目录是麒麟、UOS、deepin 与 openEuler 的共同打包层。DEB/RPM 共用同一份
冻结载荷、systemd 单元、桌面入口和安装后自检；`packaging/uos` 仅保留旧版
兼容入口。

## 公文转换私有运行时

Linux AMD64/ARM64 均固定使用 The Document Foundation 官方 LibreOffice
`25.8.7.2` 归档。`scripts/prepare-libreoffice-linux.sh` 只提取 Writer、Draw、
图形及 XSLT 转换所需 RPM，并移除 Qt/GTK 媒体入口、LibreOfficeKit GTK 前端
和 Java office bean，确保 PartyOps 仅在内嵌排版页面调用无界面转换。

LibreOffice 本体需要比国产系统基线更新的 glibc，因此安装包携带从固定
manylinux_2_34 镜像收集的最小私有 ELF 加载器与依赖闭包。包装器直接执行
包内加载器，不替换系统 glibc，也不把私有库写入全局 `LD_LIBRARY_PATH`；宿主
依赖仍保持 `glibc >= 2.17`。除普通 ELF `NEEDED` 依赖外，NSS 通过 `dlopen`
加载的 softokn/freebl/信任模块及 `.chk` 完整性文件也必须显式进入闭包。
每个文件的来源路径和 SHA-256 写入 `PRIVATE_RUNTIME_LIBS.txt`，原生包构建前
会逐项复核来源版本、架构、哈希、外部界面禁用状态以及首次配置返回码 81 的
单次重试契约。

双架构运行时必须用真实中文 DOCX 同时完成 PDF 和 Word 97 DOC 输出验证；
ARM64 还需运行 `scripts/build-linux-arm64-chroot.sh test-office`，由目标架构
二进制在 glibc 2.17 根文件系统中执行，不能用文件存在检查代替。

正式构建必须在对应架构的 manylinux2014（glibc 2.17）工具链中完成，并先由
`scripts/validate-uos-wheelhouse.py` 验证唯一包名、完整依赖闭包、架构标签、
`cryptography==50.0.0` 和完整本地智能运行时。任何失败都不得生成该架构制品。

Docker 不是构建前置条件。可使用原生 glibc 2.17 主机，或在 WSL 中挂载经
SHA-256 固定的 manylinux2014 根文件系统；ARM64 可由 QEMU `binfmt_misc`
执行同一根文件系统。Docker/Podman 仅是承载该环境的可选方式，容器引擎不可用
时不得降低依赖闭包、ABI、哈希或安装后自检门禁。当前发布构建采用 WSL + QEMU
路径，相关源码、工具链与根文件系统摘要写入发布验收记录。

安装后的包管理器配置阶段会调用 `post-install-selftest.sh`，核对文件清单、
前端资源、SQLite/FTS5、中文 OCR、本地语义、LLM、更新程序以及临时回环健康
端点。失败时服务保持停止，包配置返回非零，业务数据不会被删除。
