# daily-report

课程实践项目 —— 智能日报生成器（v1-SDD，规范驱动开发版本）。

自动采集当日工作记录，生成**写实、无空话**的三段式日报初稿，支持邮件推送与 Markdown/Word 导出。

---

## 1. 功能概览

| 能力 | 说明 |
|------|------|
| 多源自动采集 | GitHub 提交、Gitee 提交、飞书任务、Mock 模拟数据，多源**并行**采集（≤3 线程） |
| 三段式生成 | 固定结构「今日完成 / 明日计划 / 风险阻塞」，结构不可变 |
| 按人分组 | 每段内区分「我的工作」与「团队其他成员」，抬头显示本人/团队工作量统计 |
| 岗位话术库 | 研发 / 产品 / 运维 / 销售 4 套措辞，自动匹配岗位 |
| 空话过滤 | 自动删除纯套话行，空话占比 >30% 时给出告警 |
| 禁止编造 | 无任何数据时抛出 `EmptyDataError`；LLM 侧 Prompt 硬约束「无数据只输出 - 无」 |
| 版本留存 | 日报正文 + AI 初稿快照落库，同一人同一天只保留一份 |
| 一键导出 | Markdown / Word，文件名 `日报_{日期}_{作者}.{ext}` |
| 邮件推送 | SMTP 发送，失败按 2s/4s/8s 退避重试 3 次 |
| 全链路日志 | 每步记录耗时与结果，敏感字段（token/secret/授权码）自动脱敏为 `***` |

## 2. 技术栈

Python 3.12+ · uv · SQLite（标准库 sqlite3）· httpx · python-dotenv · python-docx · pytest · ruff

## 3. 目录结构

```
src/daily_report_tool/
├── models.py             # RawRecord / DailyReport / ReportSnapshot + 枚举
├── orchestrator.py       # 主编排：采集→组装→生成→过滤→模板→入库→推送
├── main.py               # 入口 main(date) 与 CLI
├── collectors/           # base / mock / github / gitee / feishu
├── generators/           # assembler / llm / filter / template
├── pushers/              # file（md/docx） / email（SMTP）
├── storage/database.py   # SQLite 持久化
└── utils/                # exceptions / config / logging_setup / retry
specs/                    # proposal.md / design.md / tasks.md（唯一权威依据）
docs/                     # 报告、UML 图、成功标准核对表
logs/                     # 运行日志（*.log 不入库）
data/  output/            # 运行期产物（数据库、导出文件，均不入库）
```

## 4. 快速开始

### 4.1 准备环境

```powershell
uv sync           # 安装依赖（httpx / python-dotenv / python-docx / pytest / ruff）
```

### 4.2 配置凭证

```powershell
Copy-Item .env.example .env      # macOS/Linux: cp .env.example .env
```

按 `.env` 注释填写：代码托管 Token（GitHub 或 Gitee 至少一个）、GitHub/Gitee 仓库、飞书 App ID/Secret、SMTP 账号与授权码、日报作者与岗位。

其中作者显示名与身份识别要这样填（才能正确区分"我的工作"和"团队的工作"）：

```ini
REPORT_AUTHOR=乐天溪                              # 抬头显示的名字（建议用 Gitee 昵称）
REPORT_IDENTITIES=tsunayoshi,乐天溪,Tsunayoshi27  # 你本人的各平台账号，逗号分隔
REPORT_ROLE=dev
```

> `.env` 已在 `.gitignore` 中，**严禁提交**。凭证只从环境变量读取，代码中无任何硬编码。

### 4.3 生成日报

```powershell
uv run python -m daily_report_tool.main                      # 生成当天日报
uv run python -m daily_report_tool.main --date 2026-11-10    # 指定日期
```

生成结果：

| 位置 | 产物 |
|------|------|
| `output/日报_{日期}_{作者}.md` / `.docx` | 可直接提交/归档的日报文件 |
| `data/daily_report.db` | `daily_report` 表（日报正文）+ `report_snapshot` 表（AI 初稿快照） |
| `logs/daily_report.log` | 全链路运行日志（已脱敏） |

## 5. 常见问题

**Q1：`uv run daily-report-tool` 报「应用程序控制策略已阻止此文件」**
本机 AppLocker / 安全软件拦截了 `.venv` 里生成的 exe 启动器（`os error 4551`）。改用模块方式调用：
`uv run python -m daily_report_tool.main`。

**Q2：飞书采集返回 0 条，是不是没配好？**
不是。飞书 `tenant_access_token`（应用身份）按官方文档只能读取「应用自身创建的任务」，读不到员工手工创建的任务。
链路本身是通的（返回 `code=0` 即正常），团队尚未产生飞书任务时列表为空属于预期现象。
读取员工个人任务需员工 OAuth 授权（`user_access_token`），已列为 v1.1 交付项，详见 [specs/design.md](specs/design.md) 的 ADR-007。

**Q3：日志出现「未配置 EMAIL_RECIPIENTS，跳过邮件推送」**
正常。`EMAIL_RECIPIENTS` 为空时只导出文件、不发邮件。

**Q4：日志出现「LLM 已降级为纯模板方案」**
LLM 超时（默认 30s）/ 不可达 / 返回结构不合规时，自动降级为直接拼接记录摘要，日报仍会产出。
默认 `LLM_PROVIDER=mock`；需要真实模型时改为 `ollama` 并启动本地 Ollama。

**Q5：同一天重复运行会不会产生重复日报？**
不会。`UNIQUE(date, author)` + UPSERT 保证一人一天一份，重复运行**只更新内容**：
采集会重新拉取当天全量记录，所以下午补充的工作会自动进入正文；主键与创建时间不变。
版本快照遵循"内容变化才记新版本"——内容没变不重复记录，补充了新工作则新增 `v2`、`v3`…

**Q6：我们团队主要用 Gitee，怎么配？**
在 `.env` 填三项即可：

```ini
GITEE_REPOS=yourname/daily-report    # 多个用英文逗号分隔
GITEE_TOKEN=                         # 私有仓库必填；公开仓库可留空（匿名可读）
# GitHub 相关的 GITHUB_REPOS 可以留空，GitHub 采集会自动跳过
```

`GITHUB_TOKEN` 与 `GITEE_TOKEN` **至少填一个**即可启动。Gitee 与 GitHub 的记录会合并进同一份日报，
`source` 分别标记为 `gitee` / `github`，不会混淆。若两个平台是同一批提交的镜像，系统会按「标题 + 时间戳」
**自动跨源去重**（优先保留 Gitee 归属），不会把同一条工作重复写进日报。

## 6. 规范与文档

- 需求与范围：[specs/proposal.md](specs/proposal.md)
- 架构与接口契约：[specs/design.md](specs/design.md)
- 任务分解与验收标准：[specs/tasks.md](specs/tasks.md)
- 运行与结果生成说明：[docs/运行说明.md](docs/运行说明.md)
- 成功标准核对：[docs/成功标准核对表.md](docs/成功标准核对表.md)

## 7. 分支规则

- `main`：评审通过的正式版本，禁止直接提交；功能走特性分支合并。

## ⚠ 安全提醒

`.env` 密钥文件严禁提交仓库！全部凭证使用环境变量注入；日志中的 token / secret / 授权码由 `SensitiveDataFilter` 自动脱敏。
