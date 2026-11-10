# 智能日报生成器 — Tasks

> 版本：v1.0 | 日期：2026-11-10 | 架构师、需求分析师：[穆妮莎·巴克拉木、李佳遥]

---

## 1. 任务分解说明

本任务清单依据 [design.md](design.md) v1.1 的模块职责与接口契约拆分，覆盖 v1 完整交付范围：**项目结构 → 共享基础层 → 数据模型 → 采集层 → 生成层 → 推送层 → 主编排 → 集成测试**。

任务粒度遵循"每个任务可独立验收"原则，每个任务包含：任务名 / 依赖 / 涉及文件 / 验收标准（Definition of Done）。

---

## 2. 任务依赖图

```
T1 项目结构
   │
   ├──► T2 共享基础层（异常/配置/数据库/日志/重试）
   │        │
   ├──► T3 数据模型（RawRecord/DailyReport/ReportSnapshot）
   │        │
   │        ├──► T4 采集层（Collector基类 → Mock/GitHub/Feishu）
   │        │        │
   │        ├──► T5 生成层（Assembler → LLMGenerator → ContentFilter → TemplateEngine）
   │        │        │
   │        └──► T6 推送层（FileExporter / EmailPusher）
   │                 │
   │                 └──► T7 主编排入口（Orchestrator + main）
   │                          │
   └──────────────────────────┴──► T8 集成测试 + 成功标准核对
```

---

## 3. 任务清单

### T1 项目结构搭建

| 项 | 内容 |
|----|------|
| **依赖** | 无 |
| **涉及文件** | `src/daily_report_tool/` 各子包目录、`pyproject.toml` |
| **任务内容** | 建立包结构：`src/daily_report_tool/{collectors,generators,pushers,storage,utils}/`；补充 `pyproject.toml` dependencies（httpx、python-dotenv、python-docx、pytest）；重构现有 `main.py` / `__init__.py`（移除第一周环境验证脚本，改为业务入口占位） |

**验收标准：**
- [ ] `pyproject.toml` dependencies 含 httpx / python-dotenv / python-docx / pytest
- [ ] 现有 `main.py` 环境验证脚本已移除，`__init__.py` 无 `print` 占位
- [ ] `uv run python -c "import daily_report_tool"` 无报错
- [ ] `uv run ruff check .` 通过

---

### T2 共享基础层

| 项 | 内容 |
|----|------|
| **依赖** | T1 |
| **涉及文件** | `src/daily_report_tool/utils/{exceptions.py, config.py, logging_setup.py, retry.py}`、`src/daily_report_tool/storage/database.py` |

**子任务：**

| 编号 | 子任务 | 内容 |
|------|--------|------|
| T2.1 | 异常类定义 | `ConnectionError` / `AuthError` / `RateLimitError` / `TokenExpiredError` / `LLMTimeoutError` / `LLMConnectionError` / `EmptyResponseError` / `EmptyDataError` / `TemplateError` / `SendError` |
| T2.2 | Config 数据类 | 用 `python-dotenv` 加载 `.env`，启动时校验必填字段，缺失即报错；字段含 `GITHUB_TOKEN` / `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / SMTP 配置 / `LLM_PROVIDER` |
| T2.3 | Database 封装 | 基于 `sqlite3` 封装 `save_report` / `save_snapshot` / `get_report` / `get_snapshots` / `update_report`，全部参数化查询，建表 SQL 与 design.md 3.2 一致 |
| T2.4 | 日志脱敏 Filter | logging 自定义 Filter，匹配敏感字段名（token/secret/password/auth_code）自动替换为 `***` |
| T2.5 | 重试工具 | 通用指数退避重试装饰器（默认 3 次，间隔可配置） |

**验收标准：**
- [ ] Config 缺失必填字段时启动报错
- [ ] Database 执行 CRUD 无 SQL 注入风险（参数化）
- [ ] 日志中含 `token` 的记录被脱敏为 `***`
- [ ] 重试装饰器在第 3 次失败后抛出原异常

---

### T3 数据模型

| 项 | 内容 |
|----|------|
| **依赖** | T1 |
| **涉及文件** | `src/daily_report_tool/models.py` |

**任务内容：** 定义三个数据类：
- `RawRecord`（`source`/`timestamp`/`project`/`title`/`detail`/`status`/`url`/`author`）
- `DailyReport`（`id`/`date`/`author`/`role`/`content`/`is_submitted`/`created_at`/`updated_at`）
- `ReportSnapshot`（`id`/`report_id`/`version`/`content`/`created_at`）

**验收标准：**
- [ ] 字段与 design.md 3.1 完全一致，`status` 枚举为 `done`/`pending`/`blocked`
- [ ] `role` 枚举为 `dev`/`pm`/`ops`/`sales`

---

### T4 采集层

| 项 | 内容 |
|----|------|
| **依赖** | T2, T3 |
| **涉及文件** | `src/daily_report_tool/collectors/{base.py, mock.py, github.py, feishu.py}` |

**子任务：**

| 编号 | 子任务 | 接口 | 内容 |
|------|--------|------|------|
| T4.1 | Collector 抽象基类 | `collect(date) -> list[RawRecord]` | 定义统一采集接口 |
| T4.2 | MockCollector | `collect_mock(date)` | 返回 3~5 条 `source="mock"` 的模拟记录 |
| T4.3 | GitHubCollector | `collect_github(repos, date)` | httpx 调 GitHub API，`status` 默认 `done`，频率 ≤1次/秒，3次指数退避重试 |
| T4.4 | FeishuCollector | `collect_feishu(date)` | 先取 tenant_access_token 再调业务接口，Token 缓存至过期前 5 分钟，`status` 按任务状态映射 |

**验收标准：**
- [ ] 三个 Collector 均实现 `collect(date) -> list[RawRecord]` 统一接口
- [ ] MockCollector 无外部依赖，返回 `source="mock"`
- [ ] GitHub/Feishu 凭证从 Config 注入，不硬编码
- [ ] API 调用含重试逻辑

---

### T5 生成层

| 项 | 内容 |
|----|------|
| **依赖** | T3 |
| **涉及文件** | `src/daily_report_tool/generators/{assembler.py, llm.py, filter.py, template.py}` |

**子任务：**

| 编号 | 子任务 | 接口 | 内容 |
|------|--------|------|------|
| T5.1 | ReportAssembler | `assemble_report(records, author, role) -> dict` | 按 `status` 分组为 `{done, pending, blocked}`；records 为空抛 `EmptyDataError` |
| T5.2 | LLMGenerator | `generate_report(records_by_status, author, role) -> str` | 默认 Mock provider 返回记录摘要；预留 `provider="ollama"`；超时 30 秒降级纯模板；Prompt 约束"无数据禁编造" |
| T5.3 | ContentFilter | `filter_content(content) -> tuple[str, list[str]]` | 删除纯虚话行；空话占比 >30% 记警告 |
| T5.4 | TemplateEngine | `render_template(report, role) -> str` | 套三段式模板，按 `role` 匹配话术库，加标题/作者/日期 |

**验收标准：**
- [ ] 生成层执行顺序：Assembler → LLMGenerator → ContentFilter → TemplateEngine
- [ ] `assemble_report` 空数据抛 `EmptyDataError`
- [ ] `generate_report` 无数据源时输出"今日无记录"，不编造内容
- [ ] `filter_content` 返回 `(过滤文本, 警告列表)` 二元组
- [ ] 模板三段结构固定（今日完成/明日计划/风险阻塞）

---

### T6 推送层

| 项 | 内容 |
|----|------|
| **依赖** | T3 |
| **涉及文件** | `src/daily_report_tool/pushers/{file.py, email.py}` |

**子任务：**

| 编号 | 子任务 | 接口 | 内容 |
|------|--------|------|------|
| T6.1 | FileExporter | `export_file(report, format) -> Path` | 导出 `md`（直接写文本）或 `docx`（python-docx），文件名 `日报_{date}_{author}.{ext}` |
| T6.2 | EmailPusher | `push_email(report, recipients) -> bool` | SMTP 发送，含 3 次重试（间隔 2s/4s/8s） |

**验收标准：**
- [ ] 导出文件命名符合 `日报_{date}_{author}.{ext}`
- [ ] 不支持的格式抛 `ValueError`
- [ ] SMTP 凭证从 Config 注入，不硬编码

---

### T7 主编排入口

| 项 | 内容 |
|----|------|
| **依赖** | T4, T5, T6 |
| **涉及文件** | `src/daily_report_tool/orchestrator.py`、`src/daily_report_tool/main.py` |

**任务内容：** `Orchestrator.run(date=None) -> DailyReport` 按序编排全流程：
```
采集（3数据源并行）→ 组装 → LLM生成 → 过滤 → 模板渲染 → 入库 → 推送
```
任一步骤失败记录日志后降级（如 LLM 超时用纯模板），尽可能产出日报。

**验收标准：**
- [ ] 3 个采集源用 `ThreadPoolExecutor`（最多 3 线程）并行
- [ ] 失败步骤不阻断后续步骤
- [ ] 全链路操作留日志
- [ ] `main()` 返回 `DailyReport`，失败 raise 异常

---

### T8 集成测试 + 成功标准核对

| 项 | 内容 |
|----|------|
| **依赖** | T7 |
| **涉及文件** | `tests/test_integration.py`、`docs/成功标准核对表.md`、`pyproject.toml`（pytest 配置 `testpaths`） |

**任务内容：**
1. 端到端测试：Mock 数据 → 采集 → 生成 → 推送（导出文件）→ 入库，全流程跑通
2. 逐条核对 proposal 第 5 节成功标准中 v1 可验证项

**验收标准：**
- [ ] `uv run pytest` 全绿
- [ ] Mock 模式下全流程可运行（无外部依赖）
- [ ] 成功标准核对表逐条填写"已满足 / 部分满足 / 不适用（v1.1）"

---

## 4. 任务完成度追踪

| 任务 | 状态 | 完成日期 |
|------|:----:|:--------:|
| T1 项目结构 | ⬜ 未开始 | — |
| T2 共享基础层 | ⬜ 未开始 | — |
| T3 数据模型 | ⬜ 未开始 | — |
| T4 采集层 | ⬜ 未开始 | — |
| T5 生成层 | ⬜ 未开始 | — |
| T6 推送层 | ⬜ 未开始 | — |
| T7 主编排入口 | ⬜ 未开始 | — |
| T8 集成测试 | ⬜ 未开始 | — |

---

> 本文件与 design.md v1.1 严格对应，任务粒度与接口契约一一映射。v1.1 延期项（企业微信/Jira/管理端/OA/手动输入界面）不纳入本清单。