"""依赖方向静态检查。

依据架构约定的依赖方向（api -> orchestration -> agent/capabilities/analysis/projects
-> domain + 边界协议 -> providers/storage），在源码层面用 AST 校验：
- ``domain`` 不导入 FastAPI、PydanticAI、OpenSandbox、SQLite、OpenTelemetry 等框架；
- 内部模块不反向导入 ``dataharness.api``。

返回结构化违规列表，供 CI/验证脚本与正负例测试复用。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# domain 禁止导入的框架/标准库顶层根
FORBIDDEN_FRAMEWORK_ROOTS = frozenset(
    {"fastapi", "pydantic_ai", "opensandbox", "sqlite3", "opentelemetry"}
)

# 除 api 自身外，其余内部包均不得反向导入 dataharness.api
INTERNAL_PACKAGES = frozenset(
    {
        "dataharness.agent",
        "dataharness.analysis",
        "dataharness.capabilities",
        "dataharness.domain",
        "dataharness.hooks",
        "dataharness.orchestration",
        "dataharness.privacy",
        "dataharness.projects",
        "dataharness.providers",
        "dataharness.sandbox",
        "dataharness.skills",
        "dataharness.storage",
        "dataharness.workspace",
    }
)


@dataclass(frozen=True)
class ImportViolation:
    """一条依赖方向违规。"""

    file: str
    imported: str
    reason: str


@dataclass(frozen=True)
class ImportRules:
    """依赖方向规则。

    Attributes:
        forbidden_roots: 包前缀 -> 禁止导入的顶层模块根（第三方/标准库）。
        forbidden_internal: 包前缀 -> 禁止导入的内部包前缀（阻止反向依赖）。
    """

    forbidden_roots: Mapping[str, frozenset[str]] = field(default_factory=dict)
    forbidden_internal: Mapping[str, frozenset[str]] = field(default_factory=dict)


def dataharness_rules() -> ImportRules:
    """DataHarness 默认依赖方向规则。"""
    return ImportRules(
        forbidden_roots={"dataharness.domain": FORBIDDEN_FRAMEWORK_ROOTS},
        forbidden_internal={pkg: frozenset({"dataharness.api"}) for pkg in INTERNAL_PACKAGES},
    )


def _matches(package: str, key: str) -> bool:
    """判断包名是否等于某规则键或是其子包。"""
    return package == key or package.startswith(key + ".")


def _package_of(file: Path, source_root: Path) -> str:
    """根据文件相对路径推导其包名（如 ``dataharness.domain``）。"""
    relative = file.relative_to(source_root)
    if relative.name == "__init__.py":
        parts = tuple(relative.parent.parts)
    else:
        parts = tuple(relative.with_suffix("").parts)
    return ".".join((source_root.name,) + parts)


def _collect_imports(file: Path) -> set[str]:
    """返回文件中所有绝对导入的模块全名（跳过相对导入）。"""
    tree = ast.parse(file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def check_imports(source_root: Path, rules: ImportRules) -> list[ImportViolation]:
    """扫描 ``source_root`` 下所有 ``.py`` 文件，返回全部依赖方向违规。"""
    violations: list[ImportViolation] = []
    for file in sorted(source_root.rglob("*.py")):
        package = _package_of(file, source_root)
        for imported in _collect_imports(file):
            for key, forbidden_roots in rules.forbidden_roots.items():
                if _matches(package, key) and imported.split(".")[0] in forbidden_roots:
                    violations.append(
                        ImportViolation(
                            file=str(file),
                            imported=imported,
                            reason=f"包 {package} 导入了禁用框架 {imported}",
                        )
                    )
            for key, forbidden_internal in rules.forbidden_internal.items():
                if not _matches(package, key):
                    continue
                for internal in forbidden_internal:
                    if imported == internal or imported.startswith(internal + "."):
                        violations.append(
                            ImportViolation(
                                file=str(file),
                                imported=imported,
                                reason=f"包 {package} 反向导入了 {internal}",
                            )
                        )
    return violations
