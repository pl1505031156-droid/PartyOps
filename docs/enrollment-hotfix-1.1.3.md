# PartyOps 1.1.3：协同电脑入网码 400 修复

## 适用现象

主机每次重新生成入网码后，AMD64 或 ARM64 协同电脑仍提示：

```text
入网码无效或已过期，请在主机重新生成，并点击“复制完整入网码”后粘贴
```

## 已确认原因

旧版终端只删除了普通空格和换行。部分国产浏览器、输入法或剪贴板会把
“一次性入网码”标签、零宽字符、BOM 或全角句点一起复制。主机旧版又直接对
收到的整段文本计算哈希，因此肉眼看起来相同的入网码也无法匹配。该问题与
AMD64、ARM64 架构无关，所以两类电脑会同时失败。

## 修复内容

- 主机和终端使用同一套入网码规范化规则。
- 支持纯入网码、换行、中文标签、零宽字符、BOM 和全角句点。
- 只提取唯一、完整的 89 字符入网码；截断码、两条不同入网码和超长文本仍拒绝。
- 主机对生成的入网码也先规范化再保存哈希，避免生成端和校验端规则漂移。
- 错误响应区分“格式不完整”和“已使用或已过期”，但不记录或泄露入网码。

## 在已经安装 PartyOps 的主机上应用

把以下两个文件放入当前 PartyOps 源码目录：

```text
PartyOps-1.1.3-enrollment-hotfix.zip
PartyOps-1.1.3-enrollment-hotfix.zip.sha256
```

主机执行：

```bash
cd '/data/home/XHX-CXZ-0010/2026年/其他/PartyOps/PartyOps'
sha256sum -c PartyOps-1.1.3-enrollment-hotfix.zip.sha256
unzip -o PartyOps-1.1.3-enrollment-hotfix.zip
grep -n 'normalize_enrollment_code' backend/app/routers/fleet.py
bash install.sh --desktop-user XHX-CXZ-0010 --rebuild
```

该命令是同版本原位修复，不会删除 `/var/lib/partyops`、任务、附件、账号、备份
和已有设备。修复包同时包含首次启动最长等待 180 秒的安装脚本，避免再次把国产
电脑的正常慢启动误报为安装失败。`--rebuild` 很重要：它会忽略目录中上一次生成
的旧 `partyops_1.1.3_*.deb`，从本次修复源码重新构建，避免把旧包再次装回去。

安装结束后，在主机“设备协同中心”重新生成一条入网码。协同电脑仍使用原来的
PartyOps Agent，填写主机地址并粘贴新码即可；主机端修复已经能兼容旧终端上传的
剪贴板文本。不要重复使用旧码，每条码只能成功使用一次且十分钟后失效。

## 验证

协同电脑加入成功后，主机“设备协同中心”应出现该设备并显示“在线”。然后分别
在主机和协同电脑各新建一条测试任务；两边刷新或等待实时事件后都应能看到。

若仍失败，请在主机执行：

```bash
systemctl status partyops --no-pager
journalctl -u partyops -n 120 --no-pager
```

日志可以提供诊断错误码，但不得发送入网码、设备令牌、证书私钥或业务正文。
