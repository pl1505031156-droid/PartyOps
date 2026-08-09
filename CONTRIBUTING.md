# 参与 PartyOps 开发

感谢你愿意帮助 PartyOps 变得更可靠、更适合基层协同工作。提交问题或代码时，请勿附带真实单位名称、人员信息、业务文件、设备令牌、私钥、备份或未脱敏日志。

## 提交问题

缺陷报告至少包含：PartyOps 版本、操作系统和架构、主机/协同机角色、管理员/普通用户角色、复现步骤、期望结果、实际结果及已脱敏日志。涉及跨机文件时，还请说明文件类型、大小、共享范围、设备在线状态和选择的交付方式。

功能建议请描述实际工作场景、现有流程的痛点、受影响角色、数据边界和可以验证的完成标准。PartyOps 优先接受能提升稳定性、可审计性和局域网协同效率的改进。

## 开发流程

1. 从 `main` 创建短生命周期分支。
2. 修改前先增加能稳定复现问题的测试。
3. 保持主机数据权威、设备不直连、权限默认拒绝和本地优先原则。
4. 新增依赖时固定版本，记录来源与许可证，并更新 `THIRD_PARTY_NOTICES.md`。
5. 运行 `scripts/test.ps1`；正式发布不得绕过 90% 覆盖率、平台真机和签名门禁。
6. Pull Request 说明根因、变更、迁移、回滚、验证结果和仍未验证的边界。

## 代码约定

- 新增文档、界面文案和必要注释使用简体中文，文件编码为 UTF-8。
- 后端使用 FastAPI、SQLAlchemy 2 和 Alembic；前端使用 Vue 3、TypeScript 和现有设计系统。
- 文件、权限、档案和更新链路必须有失败路径测试；不要使用占位实现或把异常静默吞掉。
- 不提交 `artifacts/`、`output/`、模型权重、QA 数据、缓存、构建目录和本机配置。

## 本地命令

```powershell
# 全量门禁
.\scripts\test.ps1

# 前端快速验证
corepack pnpm --dir frontend run test
corepack pnpm --dir frontend run typecheck
corepack pnpm --dir frontend run build

# 后端快速验证
.\.venv\Scripts\python.exe -m pytest backend\tests
```

提交即表示你有权贡献相关内容，并同意按仓库的 GPL-3.0 许可证发布。
