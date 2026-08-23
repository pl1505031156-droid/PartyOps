# PartyOps 1.4.5-rc.1 macOS 原生构建与验收

macOS 制品不使用 Docker，也不在 Windows/Linux 上交叉冻结。Apple Silicon 和 Intel 必须分别在对应架构的真实 Mac 上构建，防止 Rosetta、Python wheel、OCR 和 llama.cpp 混入错误架构。

## 目标制品

- `PartyOps_1.4.5-rc.1_macos_arm64.pkg`：macOS 11+ Apple Silicon。
- `PartyOps_1.4.5-rc.1_macos_x86_64.pkg`：macOS 11+ Intel。

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

仓库提供只允许手动触发的 `.github/workflows/build-macos-1.4.5-rc.1.yml`。它分别使用 GitHub 原生 `macos-15` Apple Silicon 与 `macos-15-intel` runner，从锁定源码构建 OCR 和 llama.cpp，再生成逐架构 PKG。任务只上传待人工审核的 workflow artifact，不会自动写入 Release 或官网。

没有 Apple Developer 证书时可使用 `--unsigned-candidate`：应用内所有 Mach-O 使用 ad-hoc 签名，旁边生成机器可读 attestation，明确记录 `developer_id_signed=false`、`notarized=false` 和 `real_device_validation=false`。这种包只能作为 1.4.5-rc.1 未签名公开候选，必须在下载页显著提示“未签名、未公证、未用户真机验证”，不能称为已签名或已通过用户实机验收。

手动触发时必须输入 `BUILD-UNSIGNED-144`。构建成功后仍要下载两个 workflow artifact，在本机比对 SHA-256 与 attestation，再人工上传；构建任务没有 Release 写权限。

PKG 不把 `.app` 目录直接交给 `pkgbuild` 组件分析。PyInstaller 内嵌的 `Python.framework` 会被 Installer 识别为第二个可重定位组件，在部分系统上破坏 Bundle。构建脚本改为携带经过 ZIP 往返校验的原始 App；`postinstall` 完整解包并验证 Bundle ID、主程序权限和代码签名后，才事务式替换 `/Applications/PartyOps.app`。升级失败会恢复旧 App，用户业务数据不参与该事务。

普通 Mac 的 APFS 默认不区分文件名大小写，因此 Bundle 内任何两个入口都不得只靠大小写区分。桌面入口固定为 `partyops-desktop`，核心主程序固定为 `partyops`；原生门禁会拒绝大小写折叠后重名的载荷，避免出现“安装成功但启动器被主程序覆盖”。

## 原生门禁与用户真机验收

自动构建成功不等于用户真机验收。发布前，两个原生 runner 都必须完成 PKG 全新安装、覆盖安装和三类入口自检；公开测试后还需要志愿者在真实日常 Mac 上继续执行：

1. 下载页面显示的 SHA-256 必须与 PKG 完全一致。未签名候选没有 Developer ID 与公证，不能宣称 `spctl` 或 `stapler` 通过。
2. 双击 PKG 安装，再从 Finder 打开“党建智办 PartyOps”；未配置时必须显示中文向导，不得静默退出。
3. 分别验证个人、主机、协同三种模式，含中文/空格数据路径、登录恢复、TLS、OCR、语义重排和本地 LLM。
4. 从上一个候选版执行系统内更新，故障注入覆盖授权取消、PKG 损坏、版本回读不一致、新版不健康与快照被替换；原应用和数据均必须恢复。
5. 确认 Finder 经 LaunchServices 打开 App 后，原生 Mach-O 入口先写入 `~/Library/Logs/PartyOps/launch-probe.log`，随后 Python 桌面启动器写入 `launcher.log`；CI 必须同时观察到两层探针，禁止只运行二进制 `--self-test`。
6. 确认 `launcher.log` 和 `runtime-launch.log` 可用且有界轮转，所有用户可见失败为中文。
7. 验证卸载的“仅删程序”与“同时删除本机数据”两条路径，并把结果作为从公开测试候选升级为稳定版的必要条件。

当前 Windows 开发机只能验证源码、事务编排和构建脚本的静态契约；GitHub 原生 runner 的安装后自检也不能代替 Finder、系统设置与真实业务流程的用户交互证据。
