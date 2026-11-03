# smart‑daily‑report
软件工程职业技能实践课设项目：智能日报生成器

> 系统：采集GitHub提交、飞书任务、飞书群消息，自动生成个人&团队日报，支持邮件、飞书机器人推送，SQLite存储历史。
> 两个实现版本：v1‑SDD（规范驱动开发）、v2‑Superpowers（AI工作流，不复用v1源码）

## 目录说明
- specs/    核心规范文档 proposal.md / design.md / tasks.md
- src/      v1‑SDD版本源代码
- v2/       v2‑Superpowers版本源代码
- docs/     报告、UML图、评审记录
- logs/     AI使用日志

## 分支规则
- main：评审通过的正式版本，禁止直接提交；功能走特性分支合并。

## ⚠安全提醒
.env密钥文件严禁提交仓库！全部使用环境变量注入凭证。
