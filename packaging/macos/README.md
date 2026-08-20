# PartyOps 1.4.3-rc.8 macOS 原生构建与验收

macOS 制品不使用 Docker，也不在 Windows/Linux 上交叉冻结。Apple Silicon 和 Intel 必须分别在对应架构的真实 Mac 上构建，防止 Rosetta、Python wheel、OCR 和 llama.cpp 混入错误架构。

## 目标制品

- `PartyOps_1.4.3-rc.8_macos_arm64.pkg`：macOS 11+ Apple Silicon。
- `PartyOps_1.4.3-rc.8_macos_x86_64.pkg`：macOS 11+ Intel。

## 构建前提

1. 与目标架构一致的 Mac，且 `uname -m` 分别为 `arm64` 或 `x86_64`。
2. Xcode Command Line Tools、Python 3.11、`uv`、Node.js 和 Corepack。
3. 当前架构的可审计 OCR 运行时，必须包含 `bin/tesseract` 和 `tessdata/chi_sim.traineddata`。
4. 当前架构的 llama.cpp 运行时，必须包含 `llama-server`。
5. 正式构建需要 Developer ID Application、Developer ID Installer 证书以及 `notarytool` 钥匙串配置。

## 正式构建

```bash
export PARTYOPS_MACOS_OCR_RUNTIME=/absolute/path/to/ocr-runtime
export PARTYOPS_MACOS_LLAMA_RUNTIME=/absolute/path/to/llama-runtime
export PARTYOPS_MACOS_APPLICATION_IDENTITY='Developer ID Application: ...'
export PARTYOPS_MACOS_INSTALLER_IDENTITY='Developer ID Installer: ...'
export PARTYOPS_MACOS_NOTARY_PROFILE='partyops-notary'

./packaging/macos/build-pkg.sh --architecture "$(uname -m)"
```

脚本会重建前端、冻结六个运行入口，扫描 Mach-O 架构与动态依赖，逐层签名 `.app`，生成 Installer 签名 PKG，提交 Apple 公证并 staple 票据。缺少任一证书或公证配置时，正式构建直接失败。

本地调试可显式使用 `--unsigned-development`；输出文件名带 `UNSIGNED-DO-NOT-PUBLISH`，不得上传官网、GitHub 或更新目录。

## 无证书测试候选

仓库提供只允许手动触发的 `.github/workflows/build-macos-rc8.yml`。它分别使用 GitHub 原生 `macos-15` Apple Silicon 与 `macos-15-intel` runner，从锁定源码构建 OCR 和 llama.cpp，再生成逐架构 PKG。任务只上传待人工审核的 workflow artifact，不会自动写入 Release 或官网。

没有 Apple Developer 证书时可使用 `--unsigned-candidate`：应用内所有 Mach-O 使用 ad-hoc 签名，旁边生成机器可读 attestation，明确记录 `developer_id_signed=false`、`notarized=false` 和 `real_device_validation=false`。这种包只用于 rc.8 志愿者真机验证，必须在下载页显著提示“未签名、未公证、未真机验证”，不能称为正式 macOS 版本。

手动触发时必须输入 `BUILD-UNSIGNED-RC8`。构建成功后仍要下载两个 workflow artifact，在本机比对 SHA-256 与 attestation，再人工上传；构建任务没有 Release 写权限。

## 发布前原生验收

构建成功不等于安装验收。每个架构必须在独立的测试 Mac 上执行：

1. `pkgutil --check-signature <pkg>`、`spctl --assess --type install <pkg>` 与 `xcrun stapler validate <pkg>` 全部通过。
2. 双击 PKG 安装，再从 Finder 双击“党建智办 PartyOps”；未配置时必须显示中文向导，不得静默退出。
3. 分别验证个人、主机、协同三种模式，含中文/空格数据路径、登录恢复、TLS、OCR、语义重排和本地 LLM。
4. 从上一个候选版执行系统内更新，故障注入覆盖授权取消、PKG 损坏、版本回读不一致、新版不健康与快照被替换；原应用和数据均必须恢复。
5. 确认 `~/Library/Logs/PartyOps/launcher.log` 和 `runtime-launch.log` 可用且有界轮转，所有用户可见失败为中文。
6. 验证卸载的“仅删程序”与“同时删除本机数据”两条路径；在这两条路径实现并通过前，macOS PKG 不能公开发布。

当前 Windows 开发机只能验证源码、事务编排和构建脚本的静态契约，不能代替上述原生证据。
