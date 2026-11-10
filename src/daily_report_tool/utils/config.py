"""配置加载与启动校验（design.md ADR-005）。

实现要点：
1. 使用 ``python-dotenv`` 加载 ``.env``，但不覆盖进程中已存在的真实环境变量
   （``override=False``），便于 CI/生产环境用系统环境变量覆盖；
2. 全部凭证只从环境变量读取，绝不硬编码，也不出现在 ``repr`` 中；
3. :meth:`Config.load` 在启动阶段集中校验，缺失或非法配置立即抛 ``ValueError``，
   避免运行到一半才发现凭证没配。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：src/daily_report_tool/utils/config.py 向上四级
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# 岗位角色枚举，与 design.md 3.1 DailyReport.role 一致
VALID_ROLES: frozenset[str] = frozenset({"dev", "pm", "ops", "sales"})
# LLM 后端，见 ADR-004
VALID_LLM_PROVIDERS: frozenset[str] = frozenset({"mock", "ollama"})

# 必填凭证：字段名 -> 可接受的环境变量名（按优先级排列）
_REQUIRED_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "feishu_app_id": ("FEISHU_APP_ID",),
    "feishu_app_secret": ("FEISHU_APP_SECRET",),
    "smtp_host": ("SMTP_HOST",),
    "smtp_user": ("SMTP_USER",),
    "smtp_auth_code": ("SMTP_AUTH_CODE",),
}

# 代码托管源二选一：GitHub 与 Gitee 至少配置一个 Token
_CODE_HOST_TOKEN_KEYS: tuple[str, ...] = ("GITHUB_TOKEN/GITHUB_PAT", "GITEE_TOKEN")

_DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "daily_report.db"
_DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
_DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def _env(*names: str, default: str = "") -> str:
    """按优先级取第一个非空环境变量值。"""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值直接报错。"""
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数，当前值无法解析") from exc


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，非法值直接报错。"""
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是数字，当前值无法解析") from exc


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔型环境变量（1/true/yes/on 视为真）。"""
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    """读取逗号分隔的列表型环境变量。"""
    raw = _env(name)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class Config:
    """运行期配置。

    敏感字段（token / secret / 授权码）标记为 ``repr=False``，
    保证日志或调试打印时不会泄露明文。
    """

    # ---- 必填凭证 ----
    github_token: str = field(default="", repr=False)
    # Gitee Token 可选：公开仓库可匿名读取，私有仓库需在 Gitee「设置 → 私人令牌」生成
    gitee_token: str = field(default="", repr=False)
    feishu_app_id: str = ""
    feishu_app_secret: str = field(default="", repr=False)
    smtp_host: str = ""
    smtp_user: str = ""
    smtp_auth_code: str = field(default="", repr=False)

    # ---- SMTP ----
    smtp_port: int = 465
    smtp_use_ssl: bool = True

    # ---- LLM（ADR-004：默认 Mock，provider="ollama" 切换本地模型）----
    llm_provider: str = "mock"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:7b"
    llm_timeout: float = 30.0

    # ---- 采集目标 ----
    github_repos: tuple[str, ...] = ()
    github_api_base_url: str = "https://api.github.com"
    gitee_repos: tuple[str, ...] = ()
    gitee_api_base_url: str = "https://gitee.com/api/v5"
    feishu_base_url: str = "https://open.feishu.cn"
    feishu_task_path: str = "/open-apis/task/v1/tasks"

    # ---- 日报主体与推送对象 ----
    report_author: str = "未命名员工"
    report_role: str = "dev"
    email_recipients: tuple[str, ...] = ()

    # ---- 存储与输出 ----
    db_path: Path = _DEFAULT_DB_PATH
    output_dir: Path = _DEFAULT_OUTPUT_DIR
    log_dir: Path = _DEFAULT_LOG_DIR
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_file: Path | None = None, require_credentials: bool = True) -> Config:
        """加载 .env 与环境变量，并完成启动校验。

        Args:
            env_file: 指定 .env 路径，默认项目根目录下的 ``.env``。
            require_credentials: 是否强制校验凭证字段。Mock 演示与单元测试
                可传 False，跳过凭证校验但其余校验照常执行。

        Raises:
            ValueError: 存在缺失或非法的配置项。
        """
        load_dotenv(dotenv_path=env_file or DEFAULT_ENV_FILE, override=False)
        config = cls(
            github_token=_env("GITHUB_TOKEN", "GITHUB_PAT"),
            feishu_app_id=_env("FEISHU_APP_ID"),
            feishu_app_secret=_env("FEISHU_APP_SECRET"),
            smtp_host=_env("SMTP_HOST"),
            smtp_user=_env("SMTP_USER"),
            smtp_auth_code=_env("SMTP_AUTH_CODE"),
            smtp_port=_env_int("SMTP_PORT", 465),
            smtp_use_ssl=_env_bool("SMTP_USE_SSL", True),
            llm_provider=_env("LLM_PROVIDER", default="mock").lower(),
            llm_base_url=_env("LLM_BASE_URL", default="http://127.0.0.1:11434"),
            llm_model=_env("LLM_MODEL", default="qwen2.5:7b"),
            llm_timeout=_env_float("LLM_TIMEOUT", 30.0),
            github_repos=_env_list("GITHUB_REPOS"),
            github_api_base_url=_env("GITHUB_API_BASE_URL", default="https://api.github.com"),
            gitee_token=_env("GITEE_TOKEN"),
            gitee_repos=_env_list("GITEE_REPOS"),
            gitee_api_base_url=_env("GITEE_API_BASE_URL", default="https://gitee.com/api/v5"),
            feishu_base_url=_env("FEISHU_BASE_URL", default="https://open.feishu.cn"),
            feishu_task_path=_env("FEISHU_TASK_PATH", default="/open-apis/task/v1/tasks"),
            report_author=_env("REPORT_AUTHOR", default="未命名员工"),
            report_role=_env("REPORT_ROLE", default="dev").lower(),
            email_recipients=_env_list("EMAIL_RECIPIENTS"),
            db_path=Path(_env("DB_PATH", default=str(_DEFAULT_DB_PATH))),
            output_dir=Path(_env("OUTPUT_DIR", default=str(_DEFAULT_OUTPUT_DIR))),
            log_dir=Path(_env("LOG_DIR", default=str(_DEFAULT_LOG_DIR))),
            log_level=_env("LOG_LEVEL", default="INFO").upper(),
        )
        config.validate(require_credentials=require_credentials)
        return config

    def validate(self, require_credentials: bool = True) -> None:
        """校验配置完整性，不合格立即抛错（不返回任何值）。

        Raises:
            ValueError: 列出全部缺失/非法配置项。
        """
        problems: list[str] = []
        if require_credentials:
            problems.extend(
                "/".join(names)
                for attr, names in _REQUIRED_CREDENTIALS.items()
                if not str(getattr(self, attr)).strip()
            )
            # 代码托管源二选一：GitHub 与 Gitee 至少配置一个 Token
            if not (self.github_token.strip() or self.gitee_token.strip()):
                problems.append(" 或 ".join(_CODE_HOST_TOKEN_KEYS) + "（至少填写一个）")
        if not 1 <= self.smtp_port <= 65535:
            problems.append("SMTP_PORT（必须是 1~65535 的整数）")
        if self.llm_provider not in VALID_LLM_PROVIDERS:
            allowed = "/".join(sorted(VALID_LLM_PROVIDERS))
            problems.append(f"LLM_PROVIDER（仅支持 {allowed}）")
        if self.report_role not in VALID_ROLES:
            allowed = "/".join(sorted(VALID_ROLES))
            problems.append(f"REPORT_ROLE（仅支持 {allowed}）")
        if self.llm_timeout <= 0:
            problems.append("LLM_TIMEOUT（必须大于 0）")
        if problems:
            raise ValueError("配置校验未通过：" + "、".join(problems))
