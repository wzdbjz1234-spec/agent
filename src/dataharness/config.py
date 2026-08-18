"""本地配置模型。

定义 DataHarness 运行所需的全部可配置项并用 Pydantic 校验。配置来源为 TOML 文件
（使用 Python 3.12 内置 :mod:`tomllib` 解析，不引入额外解析依赖）。默认值不包含
任何真实凭据，也不依赖公网；本地密钥只从未纳入版本控制的 ``dataharness.local.toml``
读取，绝不进入日志、Runtime DB 或 API 响应。

默认情况下 FastAPI 只监听 ``127.0.0.1``，本模块不配置任何公网暴露项。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PathsConfig(BaseModel):
    """本地持久化路径根。

    ``projects_root`` 与 ``privacy_root`` 缺省时派生自 ``runtime_data_root``；
    Runtime SQLite 固定为 ``runtime_data_root/runtime.db``。
    """

    model_config = ConfigDict(frozen=True)

    runtime_data_root: Path = Field(default_factory=lambda: Path("runtime-data"))
    projects_root: Path | None = None
    privacy_root: Path | None = None
    skills_root: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_defaults(cls, data: object) -> object:
        """缺省子根目录时，从 runtime_data_root 派生。"""
        if isinstance(data, dict):
            root = data.get("runtime_data_root", Path("runtime-data"))
            resolved = dict(data)
            if not resolved.get("projects_root"):
                resolved["projects_root"] = Path(root) / "projects"
            if not resolved.get("privacy_root"):
                resolved["privacy_root"] = Path(root) / "privacy"
            if not resolved.get("skills_root"):
                resolved["skills_root"] = Path("skills")
            return resolved
        return data

    @property
    def runtime_db(self) -> Path:
        """Runtime SQLite 文件路径。"""
        return self.runtime_data_root / "runtime.db"


class ModelProviderConfig(BaseModel):
    """云模型 Provider 配置。

    ``api_key`` 只用于本地配置文件；诊断、日志和持久化业务数据都不暴露它。
    """

    model_config = ConfigDict(frozen=True)

    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str | None = Field(default=None, repr=False)
    base_url: str | None = None
    timeout_seconds: float = 120.0


class SandboxConfig(BaseModel):
    """OpenSandbox 执行配置。

    默认断网、非特权用户、只读根文件系统；``image_digest`` 为空表示待锁定，
    实际创建 Sandbox 前必须由 Provider 校验并锁定 digest。
    """

    model_config = ConfigDict(frozen=True)

    endpoint: str = "http://127.0.0.1:18080"
    runtime: str = "secure-analysis"
    image_digest: str | None = None
    network_enabled: bool = False
    # OpenSandbox 服务端 API Key；为空表示本地自托管服务不启用认证
    api_key: str | None = None

    @model_validator(mode="after")
    def _reject_network(self) -> SandboxConfig:
        """V1 没有运行期网络开关；即使配置文件显式要求也必须在装配前失败。"""
        if self.network_enabled:
            raise ValueError("V1 Sandbox 不允许启用网络")
        return self


class ExtractionConfig(BaseModel):
    """文件导入与本地提取配置。"""

    model_config = ConfigDict(frozen=True)

    max_file_bytes: int = 100 * 1024 * 1024
    supported_formats: tuple[str, ...] = (
        "csv",
        "parquet",
        "xlsx",
        "json",
        "pdf",
        "docx",
        "pptx",
        "md",
        "txt",
    )


class IndexConfig(BaseModel):
    """本地 FTS5/BM25 全文索引配置。"""

    model_config = ConfigDict(frozen=True)

    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    max_snippet_chars: int = 2000


class BudgetConfig(BaseModel):
    """DataHarness 层预算与熔断限制（Step 数、连续失败、Run 总时长）。"""

    model_config = ConfigDict(frozen=True)

    max_analysis_steps: int = 50
    max_consecutive_failures: int = 3
    max_run_duration_seconds: int = 3600


class ResourceLimitsConfig(BaseModel):
    """Sandbox 资源与输出上限。"""

    model_config = ConfigDict(frozen=True)

    cpu_limit: float | None = None
    memory_mb: int = 1024
    disk_mb: int = 2048
    max_processes: int = 32
    max_output_bytes: int = 10 * 1024 * 1024
    step_timeout_seconds: int = 300


class SkillsConfig(BaseModel):
    """管理员显式允许在 Analysis Job 中激活的本地 Skill。"""

    model_config = ConfigDict(frozen=True)

    active: tuple[str, ...] = ()


class Settings(BaseModel):
    """DataHarness 顶层配置。"""

    model_config = ConfigDict(frozen=True)

    paths: PathsConfig = Field(default_factory=PathsConfig)
    model: ModelProviderConfig = Field(default_factory=ModelProviderConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    resources: ResourceLimitsConfig = Field(default_factory=ResourceLimitsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)


def load_settings(path: Path) -> Settings:
    """从 TOML 文件加载并校验配置。

    Args:
        path: 配置文件路径（TOML 格式）。

    Raises:
        FileNotFoundError: 文件不存在时。
        pydantic.ValidationError: 内容不符合配置模型时。
    """
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return Settings.model_validate(data)
