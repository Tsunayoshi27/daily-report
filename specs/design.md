# 智能日报生成器 — Design

> 版本：v1.0 | 日期：2026-11-09 | 架构师：[待填写]

---

## 1. 系统架构

### 1.1 四层架构总览

本系统采用 **"主编排入口 → 采集层 → 生成层 → 推送层"** 四层架构，数据在各层之间单向流转。

```
                        ┌─────────────────────────────────────────┐
                        │          主编排入口 (Orchestrator)        │
                        │   main() — 流程调度 / 异常处理 / 日志记录  │
                        └──────┬──────────┬──────────┬────────────┘
                               │          │          │
                  ┌────────────┘          │          └────────────┐
                  ▼                       ▼                       ▼
    ┌─────────────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
    │    采集层 (Collection)   │ │ 生成层(Generation)│ │    推送层 (Push)         │
    ├─────────────────────────┤ ├─────────────────┤ ├─────────────────────────┤
    │ • GitHubCollector       │ │ • ReportAssembler│ │ • EmailPusher            │
    │   GitHub 提交记录采集    │ │   日报组装器      │ │   SMTP 邮件推送          │
    │ • FeishuCollector       │ │ • TemplateEngine │ │ • FileExporter           │
    │   飞书任务/消息采集      │ │   三段式模板引擎  │ │   Markdown/Word 导出      │
    │ • MockCollector         │ │ • ContentFilter  │ │                          │
    │   模拟数据(开发/备用)    │ │   空话套话过滤器  │ │                          │
    └───────────┬─────────────┘ └────────┬────────┘ └────────────▲────────────┘
                │                        │                       │
                │   原始数据(RawRecord)   │    结构化日报          │   推送结果
                └───────────────────────►├───────────────────────┘
                                         │
                                         ▼
                            ┌─────────────────────────┐
                            │      数据存储 (SQLite)    │
                            │  日报记录 / 版本快照       │
                            └─────────────────────────┘
```

### 1.2 层间数据流

```
数据源（GitHub/飞书/Mock）
    │
    ▼
采集层 ─── 拉取当日原始工作记录，统一为标准 RawRecord 格式
    │
    ▼
生成层 ─── 组装日报实体 → 套用三段式模板 → 过滤空话套话
    │
    ▼
推送层 ─── 邮件发送 / 文件导出（Markdown、Word）
    │
    ▼
SQLite ─── 持久化日报记录与版本快照
```

**主编排入口**贯穿全流程：负责按顺序调度各层模块、统一异常处理、全链路日志记录。

---

## 2. 模块职责

| 模块 | 所属层 | 一句话职责 |
|------|--------|-----------|
| `Orchestrator` | 主编排入口 | 按"采集→生成→推送"流程调度各模块，统一异常处理和日志 |
| `GitHubCollector` | 采集层 | 通过 GitHub API 拉取指定仓库的当日 commit 记录 |
| `FeishuCollector` | 采集层 | 通过飞书开放平台 API 拉取当日任务/消息 |
| `MockCollector` | 采集层 | 返回模拟数据，用于开发调试和凭证未就绪时的主流程验证 |
| `ReportAssembler` | 生成层 | 将多条 RawRecord 按时间线、项目、类型归类组装为日报实体 |
| `TemplateEngine` | 生成层 | 将日报实体渲染为"今日完成 / 明日计划 / 风险阻塞"三段式文本 |
| `ContentFilter` | 生成层 | 过滤日报中的空话套话，确保内容写实 |
| `EmailPusher` | 推送层 | 通过 SMTP 将日报发送至指定邮箱 |
| `FileExporter` | 推送层 | 将日报导出为 Markdown 或 Word 文件 |

---

## 3. 数据模型

### 3.1 核心实体

#### RawRecord（采集层原始数据）

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `source` | str | 数据来源 | 必填，枚举：`github` / `feishu` / `mock` |
| `timestamp` | datetime | 记录时间 | 必填 |
| `project` | str | 所属项目 | 必填 |
| `title` | str | 记录标题（如 commit message、任务名） | 必填，长度 ≤ 500 |
| `detail` | str | 详细描述 | 可选 |
| `url` | str | 原始链接 | 可选 |
| `author` | str | 作者 | 必填 |

#### DailyReport（日报实体）

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `id` | int | 主键 | 自增 |
| `date` | date | 日报日期 | 必填，唯一（一人一天一份） |
| `author` | str | 日报作者 | 必填 |
| `role` | str | 岗位角色 | 必填，枚举：`dev` / `pm` / `ops` / `sales` |
| `today_done` | str | 今日完成 | 必填，由 ReportAssembler 生成 |
| `tomorrow_plan` | str | 明日计划 | 必填，由 ReportAssembler 生成 |
| `risks` | str | 风险阻塞 | 可选 |
| `ai_draft` | str | AI 初稿全文 | 必填，生成后立即保存 |
| `final_content` | str | 终稿全文 | 可选，人工编辑后保存 |
| `is_submitted` | bool | 是否已提交 | 默认 False |
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
    today_done      TEXT    NOT NULL,
    tomorrow_plan   TEXT    NOT NULL,
    risks           TEXT    DEFAULT '',
    ai_draft        TEXT    NOT NULL,
    final_content   TEXT    DEFAULT '',
    is_submitted    INTEGER DEFAULT 0,
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
│     today_done       │  └───►│     created_at       │   │
│     tomorrow_plan    │  1:N   └──────────────────────┘   │
│     risks            │                                    │
│     ai_draft         │    ┌──────────────────────┐       │
│     final_content    │    │     RawRecord        │       │
│     is_submitted     │    │  (内存对象，不入库)   │       │
│     created_at       │    ├──────────────────────┤       │
│     updated_at       │    │     source           │       │
└──────────────────────┘    │     timestamp        │       │
                            │     project          │       │
                            │     title            │       │
                            │     detail           │       │
                            │     url              │       │
                            │     author           │       │
                            └──────────────────────┘       │
                                                           │
   DailyReport 1 ──── N ReportSnapshot                     │
```

---

## 4. 接口契约

### 4.1 采集层接口

#### `collect_github(token: str, repos: list[str], date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `token`: GitHub PAT（只读权限）；`repos`: 仓库列表，格式 `"owner/repo"`；`date`: 采集日期 |
| **出参** | `list[RawRecord]`，该日期内所有仓库的 commit 记录 |
| **错误码** | `ConnectionError` — GitHub API 不可达；`AuthError` — Token 无效或过期；`RateLimitError` — API 速率限制 |
| **行为约束** | 请求频率 ≤ 1 次/秒，含 3 次指数退避重试（间隔 1s/2s/4s）；Token 仅从环境变量读取 |

#### `collect_feishu(app_id: str, app_secret: str, date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `app_id`: 飞书应用 ID；`app_secret`: 飞书应用密钥；`date`: 采集日期 |
| **出参** | `list[RawRecord]`，该日期内的任务/消息记录 |
| **错误码** | `ConnectionError` — 飞书 API 不可达；`AuthError` — 凭证无效；`TokenExpiredError` — tenant_access_token 过期 |
| **行为约束** | 先获取 tenant_access_token 再调业务接口；Token 缓存至过期前 5 分钟刷新；凭证仅从环境变量读取 |

#### `collect_mock(date: date) -> list[RawRecord]`

| 项 | 说明 |
|----|------|
| **入参** | `date`: 采集日期 |
| **出参** | `list[RawRecord]`，3~5 条模拟工作记录 |
| **错误码** | 无（不依赖外部服务） |
| **行为约束** | 仅用于开发和凭证未就绪时的主流程验证；返回数据须标记 `source="mock"` |

### 4.2 生成层接口

#### `assemble_report(records: list[RawRecord], author: str, role: str) -> DailyReport`

| 项 | 说明 |
|----|------|
| **入参** | `records`: 原始记录列表；`author`: 日报作者；`role`: 岗位角色 |
| **出参** | `DailyReport` 实体 |
| **错误码** | `EmptyDataError` — records 为空且无 mock 数据可用 |
| **行为约束** | 按时间倒序排列记录；自动归类到"今日完成"；识别未完成项归入"明日计划"；识别含 `#blocked` 标签项归入"风险阻塞" |

#### `render_template(report: DailyReport, role: str) -> str`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体；`role`: 岗位角色（匹配话术库） |
| **出参** | `str`，三段式格式化文本 |
| **错误码** | `TemplateError` — 模板文件缺失或格式错误 |
| **行为约束** | 模板结构不可变（三段固定）；措辞按角色匹配话术库 |

#### `filter_content(content: str) -> str`

| 项 | 说明 |
|----|------|
| **入参** | `content`: 待过滤的日报文本 |
| **出参** | `str`，过滤后的文本 |
| **错误码** | 无 |
| **行为约束** | 过滤规则：删除纯虚话行（如"努力工作"、"认真负责"等无具体内容的表述）；空话占比 > 30% 时标记警告 |

### 4.3 推送层接口

#### `push_email(report: DailyReport, smtp_config: dict, recipients: list[str]) -> bool`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体；`smtp_config`: SMTP 配置（host/port/user/auth_code）；`recipients`: 收件人邮箱列表 |
| **出参** | `bool`，发送成功返回 True |
| **错误码** | `ConnectionError` — SMTP 服务器不可达；`AuthError` — 邮箱凭证无效；`SendError` — 发送失败 |
| **行为约束** | 含 3 次重试（间隔 2s/4s/8s）；SMTP 凭证仅从环境变量读取 |

#### `export_file(report: DailyReport, format: str, output_dir: str) -> Path`

| 项 | 说明 |
|----|------|
| **入参** | `report`: 日报实体；`format`: 导出格式 `"md"` 或 `"docx"`；`output_dir`: 输出目录 |
| **出参** | `Path`，导出文件的完整路径 |
| **错误码** | `ValueError` — 不支持的格式；`IOError` — 目录不可写 |
| **行为约束** | 文件名格式：`日报_{date}_{author}.{ext}` |

### 4.4 主编排入口接口

#### `main(date: date = None) -> int`

| 项 | 说明 |
|----|------|
| **入参** | `date`: 可选，默认当天 |
| **出参** | `int`，0 成功 / 1 失败 |
| **错误码** | 汇总各层错误，记录到日志 |
| **行为约束** | 按序执行：采集 → 生成 → 推送；任一步骤失败记录日志后继续尝试后续步骤（尽可能多地完成）；全链路操作留日志 |

---

## 5. 技术选型 ADR

### ADR-001：HTTP 客户端选型

| 项 | 内容 |
|----|------|
| **状态** | 已采纳 |
| **背景** | 系统需要对接 GitHub API 和飞书开放平台 API，需要选择一个 HTTP 客户端库 |
| **备选项** | A. `httpx` — 支持 async/await，连接池，HTTP/2；B. `requests` — 最流行，同步；C. `aiohttp` — 纯异步 |
| **决策** | 选择 **httpx** |
| **理由** | 1) 同时支持同步和异步调用，后期可无缝切换异步；2) 内置连接池和重试机制，减少样板代码；3) API 设计与 `requests` 高度相似，学习成本低；4) 活跃维护，社区健康。否决 `requests` 是因为不支持原生异步，对未来性能扩展不友好；否决 `aiohttp` 是因为 API 差异大且对同步调用场景不友好 |

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

---

## 6. 非功能性约束

### 6.1 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 采集+生成单次耗时 | ≤ 10 秒 | 含 API 调用和 AI 生成 |
| 邮件推送耗时 | ≤ 5 秒 | 含 SMTP 连接和发送 |
| 并发采集支持 | 3 个数据源并行 | 采集层三个 Collector 独立无依赖 |
| 数据库写入 | ≤ 100ms | 单条日报写入 |

### 6.2 安全要求

| 要求 | 实现方式 |
|------|----------|
| 密钥不入库 | 全部凭证通过环境变量注入，`.env` 文件已加入 `.gitignore` |
| 日志脱敏 | 日志输出时自动遮蔽 Token、密码等敏感字段 |
| SQL 参数化 | 全部数据库操作使用参数化查询，禁止字符串拼接 SQL |
| 数据不外传 | 原始业务数据仅本地处理，AI 调用使用私有化部署接口 |

### 6.3 采集频率限制

| 限制项 | 规则 |
|--------|------|
| GitHub API | ≤ 1 次/秒，含速率限制检测 |
| 飞书 API | ≤ 2 次/秒，含 tenant_access_token 缓存 |
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
| 推送渠道可扩展 | 推送层通过统一接口接入，新增推送渠道只需新增一个 Pusher 类 |
| 日志完整 | 全链路操作留日志，包含时间戳、模块名、操作、结果 |
| 版本快照 | 每次编辑自动保存历史版本，可回溯 AI 初稿和各次人工修改 |