智能日报生成器 — Design

> 版本：v1.1 | 日期：2026-11-09 | 架构师：[穆妮莎·巴克拉木]

---

## 0. v1 范围界定

根据 proposal 功能清单，v1 阶段聚焦 **员工端单日报生成全流程**（采集→AI生成→推送），以下功能标记为 v1.1：

| 延期项 | 理由 |
|--------|------|
| 企业微信 / Jira 数据源 | v1 优先打通 GitHub + 飞书两条核心链路，v1.1 扩展 |
| 管理端（全员查看 / 风险汇总 / 提交率看板） | v1 先完成单日报闭环，管理端依赖多用户数据积累 |
| OA 办公平台推送 | v1 仅支持邮件 + 文件导出，v1.1 接入 OA |
| 手动补充输入界面 | v1 通过命令行参数/文件输入支持，v1.1 做交互式输入 |
| 飞书用户授权（user_access_token） | v1 的飞书链路已完成鉴权、Token 缓存与采集，但应用身份（tenant_access_token）只能读取「应用自身创建的任务」；要读取员工个人任务需员工 OAuth 授权，v1.1 实现（见 ADR-007） |

---

## 1. 系统架构

### 1.1 四层架构总览

本系统采用 **"主编排入口 → 采集层 → 生成层 → 推送层"** 四层架构，生成层内部串行执行：**LLM生成 → 空话过滤 → 模板套用**。

```
                        ┌──────────────────────────────────────────────────┐
                        │             主编排入口 (Orchestrator)              │
                        │      main() — 流程调度 / 异常处理 / 日志记录       │
                        └────┬────────────┬──────────────┬─────────────────┘
                             │            │              │
                  ┌──────────┘            │              └──────────┐
                  ▼                       ▼                         ▼
    ┌──────────────────────────┐ ┌────────────────────┐ ┌──────────────────────┐
    │   采集层 (Collection)     │ │ 生成层 (Generation) │ │   推送层 (Push)       │
    ├──────────────────────────┤ ├────────────────────┤ ├──────────────────────┤
    │ • GitHubCollector        │ │ ① LLMGenerator     │ │ • EmailPusher         │
    │   GitHub 提交记录采集     │ │   调用私有化LLM     │ │   SMTP 邮件推送       │
    │ • FeishuCollector        │ │   逐条生成日报正文   │ │ • FileExporter        │
    │   飞书任务/消息采集       │ │ ② ContentFilter    │ │   Markdown/Word 导出   │
    │ • MockCollector          │ │   过滤空话套话      │ │                       │
    │   模拟数据(开发/备用)     │ │ ③ TemplateEngine   │ │                       │
    │                          │ │   套用三段式模板    │ │                       │
    └───────────┬──────────────┘ └─────────┬──────────┘ └───────────▲───────────┘
                │                          │                         │
                │   RawRecord 列表         │   DailyReport           │   推送结果
                └─────────────────────────►├─────────────────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │     数据存储 (SQLite)      │
                              │   日报记录 / 版本快照       │
                              └──────────────────────────┘
```

### 1.2 层间数据流（生成层内部顺序）

```
数据源（GitHub/飞书/Mock）
    │
    ▼
采集层 ─── 拉取当日原始工作记录，统一为标准 RawRecord 格式
    │
    ▼
生成层 ─── ① LLMGenerator: 逐条 RawRecord → 自然语言日报段落
             ② ContentFilter: 删除纯虚话行，空话占比 > 30% 时记警告日志
             ③ TemplateEngine: 套三段式模板，按角色匹配话术库
    │
    ▼
推送层 ─── 邮件发送 / 文件导出（Markdown、Word）
    │
    ▼
SQLite ─── 持久化日报记录与版本快照
```

**主编排入口**贯穿全流程：按"采集 → 生成(LLM→过滤→模板) → 推送"顺序调度，统一异常处理，全链路日志。

---

## 2. 模块职责

| 模块 | 所属层 | 一句话职责 |
|------|--------|-----------|
| `Orchestrator` | 主编排入口 | 按"采集→生成→推送"流程调度各模块，统一异常处理和日志 |
| `GitHubCollector` | 采集层 | 通过 GitHub API 拉取指定仓库的当日 commit 记录 |
| `GiteeCollector` | 采集层 | 通过 Gitee OpenAPI v5 拉取指定仓库的当日 commit 记录（私有仓库需 Token，公开仓库可匿名） |
| `FeishuCollector` | 采集层 | 通过飞书开放平台 API 拉取当日任务/消息 |
| `MockCollector` | 采集层 | 返回模拟数据，用于开发调试和凭证未就绪时的主流程验证 |
| `LLMGenerator` | 生成层 | 调用私有化部署 LLM，将 RawRecord 列表生成为自然语言日报正文 |
| `ContentFilter` | 生成层 | 过滤日报中的空话套话，确保内容写实 |
| `TemplateEngine` | 生成层 | 将日报实体渲染为"今日完成 / 明日计划 / 风险阻塞"三段式文本 |
| `EmailPusher` | 推送层 | 通过 SMTP 将日报发送至指定邮箱 |
| `FileExporter` | 推送层 | 将日报导出为 Markdown 或 Word 文件 |

---

## 3. 数据模型

### 3.1 核心实体

#### RawRecord（采集层原始数据）

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `source` | str | 数据来源 | 必填，枚举：`github` / `gitee` / `feishu` / `mock` / `manual` |
| `timestamp` | datetime | 记录时间 | 必填 |
| `project` | str | 所属项目 | 必填 |
| `title` | str | 记录标题（如 commit message、任务名） | 必填，长度 ≤ 500 |
| `detail` | str | 详细描述 | 可选 |
| `status` | str | 完成状态 | 必填，枚举：`done` / `pending` / `blocked` |
| `url` | str | 原始链接 | 可选 |
| `author` | str | 作者 | 必填 |

#### DailyReport（日报实体）

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `id` | int | 主键 | 自增 |
| `date` | date | 日报日期 | 必填，唯一（一人一天一份） |
| `author` | str | 日报作者 | 必填 |
| `role` | str | 岗位角色 | 必填，枚举：`dev` / `pm` / `ops` / `sales` |
| `content` | str | 日报正文（三段式结构化文本） | 必填，LLMGenerator 生成后立即保存 |
| `is_submitted` | bool | 是否已提交 | 默认 False，CHECK(is_submitted IN (0,1)) |
| `created_at` | datetime | 创建时间 | 自动记录 |
| `updated_at` | datetime | 最后修改时间 | 自动更新 |
#### ReportSnapshot（版本快照）

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `id` | int | 主键 | 自增 |
| `report_id` | int | 关联日报 ID | 外键 → DailyReport.id |
| `version` | int | 版本号 | 自增，从 1 开始 |
| `content` | str | 该版本的完整日报正文 | 必填 |
| `created_at` | datetime | 快照时间 | 自动记录 |

### 3.2 SQLite 表结构

```sql
CREATE TABLE IF NOT EXISTS daily_report (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    author          TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK(role IN ('dev', 'pm', 'ops', 'sales')),
    content         TEXT    NOT NULL,
    is_submitted    INTEGER NOT NULL DEFAULT 0 CHECK(is_submitted IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(date, author)
);

CREATE TABLE IF NOT EXISTS report_snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (report_id) REFERENCES daily_report(id),
    UNIQUE(report_id, version)
);
```

### 3.3 ER 图

```
┌──────────────────────┐       ┌──────────────────────┐
│     DailyReport      │       │   ReportSnapshot     │
├──────────────────────┤       ├──────────────────────┤
│ PK  id               │──┐    │ PK  id               │
│     date             │  │    │ FK  report_id        │◄──┐
│     author           │  │    │     version          │   │
│     role             │  │    │     content          │   │
│     content          │  └───►│     created_at       │   │
│     is_submitted     │  1:N   └──────────────────────┘   │
│     created_at       │                                    │
│     updated_at       │    ┌──────────────────────┐       │
└──────────────────────┘    │     RawRecord        │       │
                            │  (内存对象，不入库)   │       │
                            ├──────────────────────┤       │
                            │     source           │       │
                            │     timestamp        │       │
                            │     project          │       │
                            │     title            │       │
                            │     detail           │       │
                            │     status           │       │
                            │     url              │       │
                            │     author           │       │
                            └──────────────────────┘       │
                                                           │
   DailyReport 1 ──── N ReportSnapshot                     │
```

---

## 4. 接口契约

### 4.1 采集层接口

#### `collect_github(repos: list[str], date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `repos`: 仓库列表，格式 `"owner/repo"`；`date`: 采集日期；Token 在 `__init__` 中从环境变量 `GITHUB_TOKEN` 读取 |
| **出参** | `list[RawRecord]`，该日期内所有仓库的 commit 记录；`status` 默认 `done` |
| **错误码** | `ConnectionError` — GitHub API 不可达；`AuthError` — Token 无效或过期；`RateLimitError` — API 速率限制 |
| **行为约束** | 请求频率 ≤ 1 次/秒，含 3 次指数退避重试（间隔 1s/2s/4s） |

#### `collect_gitee(repos: list[str], date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `repos`: 仓库列表，格式 `"owner/repo"`；`date`: 采集日期；Token 在 `__init__` 中从环境变量 `GITEE_TOKEN` 读取（**允许为空**，公开仓库可匿名访问） |
| **出参** | `list[RawRecord]`，该日期内所有仓库的 commit 记录；`status` 默认 `done`；`source` 标记为 `gitee` |
| **错误码** | `ConnectionError` — Gitee API 不可达；`AuthError` — Token 无效或无权访问仓库；`RateLimitError` — API 速率限制 |
| **行为约束** | 鉴权通过 **URL query 参数** `access_token` 传递（Gitee 不支持 GitHub 式的 Authorization 头）；日志层需确保 token 被脱敏为 `access_token=***`；请求频率 ≤ 1 次/秒，含 3 次指数退避重试（间隔 1s/2s/4s） |
| **实现依据** | 见 ADR-008 |

#### `collect_feishu(date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `date`: 采集日期；App ID/Secret 在 `__init__` 中从环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 读取 |
| **出参** | `list[RawRecord]`，该日期内的任务/消息记录；`status` 根据任务状态字段映射为 `done` / `pending` / `blocked` |
| **错误码** | `ConnectionError` — 飞书 API 不可达；`AuthError` — 凭证无效；`TokenExpiredError` — tenant_access_token 过期 |
| **行为约束** | 先获取 tenant_access_token 再调业务接口；Token 缓存至过期前 5 分钟刷新；权限不足（`99991672` 等）与凭证无效归为 `AuthError` 且不重试 |
| **数据范围** | tenant_access_token 只能读取「应用自身创建的任务」，无法读取员工手工创建的任务（平台身份模型限制，见 ADR-007）。因此返回空列表属于正常现象，不得据此判定采集失败 |
| **字段映射** | 标题取 `summary`、描述取 `description`、创建时间取 `create_time`、完成时间取 `complete_time`（`"0"` 表示未完成）：`complete_time` 非 0 → `done`，否则 `pending` |

#### `collect_mock(date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `date`: 采集日期 |
| **出参** | `list[RawRecord]`，3~5 条模拟工作记录 |
| **错误码** | 无（不依赖外部服务） |
| **行为约束** | 仅用于开发和凭证未就绪时的主流程验证；返回数据须标记 `source="mock"` |

### 4.2 生成层接口

#### `assemble_report(records: list[RawRecord], author: str, role: str) -> dict`
| 项 | 说明 |
|----|------|
| **入参** | `records`: 原始记录列表（含 `status` 字段）；`author`: 日报作者；`role`: 岗位角色 |
| **出参** | `dict`，`{"done": [...], "pending": [...], "blocked": [...]}` 三类分组的记录列表 |
| **错误码** | `EmptyDataError` — records 为空且无 mock 数据可用 |
| **行为约束** | 按 `status` 字段分组（done → 今日完成，pending → 明日计划，blocked → 风险阻塞）；非 `blocked` 的 pending 项自动归入明日计划 |

#### `generate_report(records_by_status: dict, author: str, role: str) -> str`

| 项 | 说明 |
|----|------|
| **入参** | `records_by_status`: assemble_report 的输出（done/pending/blocked 三组）；`author`: 日报作者；`role`: 岗位角色 |
| **出参** | `str`，自然语言日报正文（含三段结构） |
| **错误码** | `LLMTimeoutError` — LLM 调用超时（30秒）；`LLMConnectionError` — LLM 服务不可达；`EmptyResponseError` — LLM 返回空内容 |
| **行为约束** | 调用私有化 LLM（详见 ADR-004）；Prompt 中强制约束"无数据源时输出'今日无记录'，禁止编造"；超时30秒后降级为纯模板方案（直接拼接记录摘要） |

#### `filter_content(content: str) -> tuple[str, list[str]]`

| 项 | 说明 |
|----|------|
| **入参** | `content`: 待过滤的日报文本 |
| **出参** | `(str, list[str])`，过滤后的文本 + 警告列表（如 `["空话占比 35%，请检查"]`） |
| **错误码** | 无 |
| **行为约束** | 删除纯虚话行（如"努力工作"、"认真负责"等）；空话占比 > 30% 时在警告列表中记录 |

#### `render_template(report: DailyReport, role: str) -> str`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体（使用 `report.content` 作为正文）；`role`: 岗位角色（匹配话术库） |
| **出参** | `str`，三段式格式化文本（加标题头、作者行、日期行等） |
| **错误码** | `TemplateError` — 模板文件缺失或格式错误 |
| **行为约束** | 模板结构不可变（三段固定）；措辞按角色匹配话术库 |

### 4.3 推送层接口

#### `push_email(report: DailyReport, recipients: list[str]) -> bool`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体；`recipients`: 收件人邮箱列表；SMTP 配置在 `__init__` 中从环境变量读取 |
| **出参** | `bool`，发送成功返回 True |
| **错误码** | `ConnectionError` — SMTP 服务器不可达；`AuthError` — 邮箱凭证无效；`SendError` — 发送失败 |
| **行为约束** | 含 3 次重试（间隔 2s/4s/8s） |

#### `export_file(report: DailyReport, format: str) -> Path`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体；`format`: 导出格式 `"md"` 或 `"docx"`；输出目录在 `__init__` 中配置 |
| **出参** | `Path`，导出文件的完整路径 |
| **错误码** | `ValueError` — 不支持的格式；`IOError` — 目录不可写 |
| **行为约束** | 文件名格式：`日报_{date}_{author}.{ext}` |

### 4.4 主编排入口接口

#### `main(date: date = None) -> DailyReport`

| 项 | 说明 |
|----|------|
| **入参** | `date`: 可选，默认当天 |
| **出参** | `DailyReport`，完整的日报实体；失败时 raise 异常 |
| **错误码** | 各层异常向上透传，统一在日志中记录 |
| **行为约束** | 按序执行：采集 → 组装 → LLM 生成 → 过滤 → 模板渲染 → 推送；任一步骤失败记录日志后尝试降级（如 LLM 超时用纯模板方案），尽可能产出日报 |

---

## 5. 技术选型 ADR

### ADR-001：HTTP 客户端选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 系统需要对接 GitHub API 和飞书开放平台 API，需要选择一个 HTTP 客户端库 |
| **备选项** | A. `httpx` — 支持 async/await，连接池，HTTP/2；B. `requests` — 最流行，同步；C. `aiohttp` — 纯异步 |
| **决策** | 选择 **httpx** |
| **理由** | 1) 同时支持同步和异步调用，v1 用同步模式满足需求，v2 可无缝切换异步扩展并发；2) 内置连接池和超时控制，减少样板代码；3) API 设计与 `requests` 高度相似，学习成本低。否决 `requests` 是因为内置连接池和超时配置不如 httpx 简洁；否决 `aiohttp` 是因为 API 差异大且对同步调用场景不友好 |

### ADR-002：数据库选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 系统需持久化日报记录和版本快照，选择轻量级数据存储方案 |
| **备选项** | A. SQLite（标准库 `sqlite3`）— 零配置、文件型、无需服务进程；B. PostgreSQL — 功能完整、并发强；C. JSON 文件 — 最简单 |
| **决策** | 选择 **SQLite** |
| **理由** | 1) Python 标准库自带，零依赖安装；2) 文件型数据库，适合私有化部署，无需额外服务进程；3) 对日报这种低并发场景性能完全足够；4) proposal 明确要求 SQLite。否决 PostgreSQL 是因为需要额外部署和维护，与"私有化轻量部署"目标冲突；否决 JSON 文件是因为缺乏事务支持和查询能力，版本快照维护困难 |

### ADR-003：日报模板方案选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 日报需支持三段式模板和岗位差异化话术，需要选择模板方案 |
| **备选项** | A. Python 字符串拼接 — 最直接；B. Jinja2 模板引擎 — 功能完整；C. f-string + 自定义模板类 — 轻量 |
| **决策** | 选择 **f-string + 自定义 TemplateEngine 类** |
| **理由** | 1) 三段式模板结构固定，不需要 Jinja2 的复杂逻辑（条件/循环）；2) 减少第三方依赖，安装和部署更简单；3) 通过 TemplateEngine 类封装话术库和格式化逻辑，保留扩展性，后期如需复杂模板可切换到 Jinja2。否决 Jinja2 是因为当前需求简单，引入模板引擎是过度设计；否决纯字符串拼接是因为话术库需要结构化管理和岗位匹配 |

### ADR-004：AI 大模型客户端选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 系统核心功能是将 RawRecord 列表生成自然语言日报，需调用 LLM。proposal 要求支持私有化部署，原始业务数据不上公有云 |
| **备选项** | A. OpenAI API（openai 库）— 效果最好但数据出境；B. Ollama 本地部署 — 免费、数据私密、适合课程演示；C. 模拟 LLM（Mock）— 零依赖，开发阶段使用 |
| **决策** | v1 默认使用 **Mock LLM**（返回格式化记录摘要），同时预留 Ollama 接口 |
| **理由** | 1) Mock LLM 让主流程在无 GPU/无网络环境下也能完整跑通，课程评审时不依赖外部服务；2) 预留 `LLMGenerator` 的 `provider` 参数，传 `"ollama"` 即切换到本地模型；3) 答辩时用 Mock 演示主流程，用 Ollama 演示真实 AI 生成效果。否决 OpenAI API 是因为原始业务数据出境违反 proposal 安全要求 |
### ADR-005：配置管理方案选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 系统涉及 GitHub Token、飞书凭证、SMTP 密码、LLM 地址等多组环境变量，需统一管理 |
| **备选项** | A. `python-dotenv` — 最流行的 .env 加载库；B. `pydantic-settings` — 类型校验强但依赖重；C. 手写 `os.getenv` + 自定义 Config 类 — 零依赖 |
| **决策** | 选择 **python-dotenv + 自定义 Config 数据类** |
| **理由** | 1) python-dotenv 是轻量级依赖（仅一个文件），比 pydantic-settings 简单得多；2) Config 数据类在启动时集中校验必填字段，缺失立即报错而非运行时才发现；3) 所有模块通过依赖注入获取 Config 实例，不直接读环境变量，便于测试。否决 pydantic-settings 是为了控制依赖数量（与 ADR-003 轻量原则一致）；否决纯手写是因为 .env 文件解析的边界情况多，不值得重造轮子 |

### ADR-006：Word 文档导出库选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | `FileExporter` 需将日报导出为 Word 格式（`export_file` 支持 `"docx"`），需要选择一个 docx 生成库 |
| **备选项** | A. `python-docx` — 最成熟的 docx 读写库；B. 直接生成 docx XML — 零依赖但复杂度高；C. `pandoc` 外部工具 — 功能强但引入外部依赖 |
| **决策** | 选择 **python-docx** |
| **理由** | 1) 社区最活跃、文档最全的 Python docx 库；2) API 简洁（`Document()` → `add_paragraph()`），满足日报纯文本导出需求；3) 纯 Python 实现，安装简单。否决直接生成 docx XML 是因为需要手写 OOXML 结构，开发成本远超收益；否决 pandoc 是因为引入外部二进制工具，与"私有化轻量部署"目标冲突 |

### ADR-007：飞书身份模型与用户授权方案

| 项 | 内容 |
|----|------|
| **状态** | v1 采纳应用身份（A）；用户授权（B）延至 v1.1 |
| **背景** | `FeishuCollector` 需要读取员工当日任务，而飞书开放平台提供两套身份：`tenant_access_token`（应用身份）与 `user_access_token`（用户身份） |
| **备选项** | A. 应用身份（tenant_access_token）；B. 用户身份（user_access_token，OAuth 授权码模式）；C. 应用身份 + 由应用先创建任务再读取 |
| **决策** | v1 采用 **方案 A**，方案 B 列入 v1.1 交付项 |
| **理由** | 1) 官方文档明确：tenant_access_token 只能获取「应用自身通过创建任务接口创建的任务」，覆盖不到员工手工创建的任务；2) 方案 C 等于由系统凭空造数据，违反 proposal「无数据源禁止编造」红线，直接否决；3) 方案 B 需在飞书后台配置用户身份权限与重定向 URL、走授权码换取 token、并持久化 refresh_token（长期凭证），且每个使用者需各自授权，与 v1「单员工命令行闭环」目标不匹配，故延后；4) 采用方案 A 后，飞书链路的鉴权、Token 缓存、状态映射、重试与降级均已打通可验证，团队尚未产生任务时返回空列表不视为故障 |
| **v1.1 实施要点** | ①飞书后台增加用户身份权限 `task:task:readonly` 并配置重定向 URL；②新增登录 CLI，用本地回调接收授权码，调用 `POST /open-apis/authen/v2/oauth/token` 换取 token；③`refresh_token` 仅存于 `.env`（严禁入库、严禁入日志），过期前自动刷新；④`FeishuCollector` 增加 user token 模式，`collect(date)` 签名保持不变 |

### ADR-008：代码托管源接入 Gitee

| 项 | 内容 |
|----|------|
| **状态** | 已采纳（v1 内实现，与 GitHub 并列） |
| **背景** | 团队成员日常工作主要在 Gitee 完成，仅接 GitHub 会导致"真实提交记录缺失"，直接影响日报写实度；proposal 3.1 只要求"代码提交记录"这一能力，未限定平台 |
| **备选项** | A. 新增独立 `GiteeCollector`；B. 复用 `GitHubCollector`、把 base_url 指向 Gitee；C. 仅接入 GitHub |
| **决策** | 选择 **方案 A** |
| **理由** | 1) Gitee OpenAPI v5 与 GitHub 的提交列表结构基本同构（`sha` / `commit.message` / `commit.author.date` / `html_url` / `author.login`），解析逻辑可完全复用，新增成本低；2) 方案 B 有三处硬阻碍：鉴权方式不同（Gitee 用 URL query `access_token`，GitHub 用 `Authorization` 头）、记录 `source` 会被错误标为 `github`（数据失真，违反写实原则）、破坏数据模型契约；3) 方案 C 无法覆盖团队真实工作痕迹；4) 符合 design.md 6.5「新增数据源只需新增一个 Collector 类」的扩展约定 |
| **影响与约束** | ①`RawRecord.source` 枚举新增 `gitee`；②凭证校验由"GitHub Token 必填"放宽为"GitHub Token 与 Gitee Token **至少填一个**"，且 Gitee Token 允许为空（公开仓库匿名可读）；③Gitee token 出现在 URL 中，依赖 `SensitiveDataFilter` 完成脱敏（已实测 `access_token=***`）；④采集线程池上限仍为 3（design.md 6.1 不变），未配置的源会立即返回空列表，实际不产生排队等待；⑤若同一批提交在 GitHub 与 Gitee 镜像仓库并存，编排层按「标题 + 时间戳」**跨源去重**，并优先保留 Gitee 归属（团队主战场），避免日报把同一工作写两遍 |

---

## 6. 非功能性约束

### 6.1 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 采集+生成单次耗时 | ≤ 10 秒 | 不含 LLM 推理耗时（LLM 单独计时，超时 30 秒降级） |
| LLM 调用超时 | 30 秒 | 超时后降级为纯模板方案（拼接记录摘要），保证日报总能产出 |
| 邮件推送耗时 | ≤ 5 秒 | 含 SMTP 连接和发送 |
| 并发采集支持 | 3 个数据源并行 | 使用 `concurrent.futures.ThreadPoolExecutor`（最多 3 线程） |
| 数据库写入 | ≤ 100ms | 单条日报写入 |

### 6.2 安全要求

| 要求 | 实现方式 |
|------|----------|
| 密钥不入库 | 全部凭证通过环境变量注入，`.env` 文件加入 `.gitignore` |
| 日志脱敏 | logging 模块注册自定义 Filter，对匹配敏感字段名（token/secret/password/auth_code）的日志记录自动替换为 `***` |
| SQL 参数化 | 全部数据库操作使用参数化查询，禁止字符串拼接 SQL |
| 数据不外传 | 原始业务数据仅本地处理，v1 默认 Mock LLM，Ollama 模式下数据不出本机 |

### 6.3 采集频率限制

| 限制项 | 规则 |
|--------|------|
| GitHub API | ≤ 1 次/秒，含速率限制检测 |
| 飞书 API | ≤ 2 次/秒，含 tenant_access_token 缓存 |
| LLM 调用 | 每次日报生成仅调用 1 次（Prompt 内包含所有记录），30 秒超时 |
| 日报生成频率 | 每人每天仅生成 1 份（`UNIQUE(date, author)` 约束） |
| 重试策略 | 所有外部 API 调用含 3 次指数退避重试 |

### 6.4 兼容性

| 项 | 要求 |
|----|------|
| Python 版本 | ≥ 3.12 |
| 操作系统 | Windows / Linux / macOS |
| Python 包管理器 | uv |
| 静态扫描 | ruff，清零全部告警 |

### 6.5 可维护性

| 要求 | 实现方式 |
|------|----------|
| 数据源可替换 | 采集层通过统一接口 `collect_xxx() -> list[RawRecord]` 接入，新增数据源只需新增一个 Collector 类 |
| LLM 可切换 | `LLMGenerator` 通过 `provider` 参数切换 Mock / Ollama / 未来其他后端 |
| 推送渠道可扩展 | 推送层通过统一接口接入，新增推送渠道只需新增一个 Pusher 类 |
| 日志完整 | 全链路操作留日志，包含时间戳、模块名、操作、结果 |
| 版本快照 | 正文发生变化时自动保存历史版本（内容与最新版相同则不重复记录），可回溯 AI 初稿和各次人工修改 |