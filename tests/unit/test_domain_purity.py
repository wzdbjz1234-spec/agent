"""领域层纯净性测试：源码层面校验 domain 不导入禁用框架且不反向导入内部模块。

该测试直接解析 domain 包的源码 AST，而非依赖运行时 import 顺序，从而保证
结果确定且能真正证明“domain 不依赖 FastAPI、PydanticAI、OpenSandbox、
SQLite 或遥测 SDK”这一退出 Gate。
"""

from __future__ import annotations

import ast
from pathlib import Path

import dataharness.domain as domain

# Phase 01 退出 Gate 明确禁止的框架/标准库模块
FORBIDDEN_ROOTS = {"fastapi", "pydantic_ai", "opensandbox", "sqlite3", "opentelemetry"}


def _domain_source_files() -> list[Path]:
    return sorted(Path(domain.__file__).parent.glob("*.py"))


def _import_roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module.split(".")[0]] if node.module else []
    return []


def test_domain_does_not_import_forbidden_modules() -> None:
    for py in _domain_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for root in _import_roots(node):
                assert root not in FORBIDDEN_ROOTS, f"{py.name} 导入了禁用模块 {root}"


def test_domain_does_not_reverse_import_internal_modules() -> None:
    for py in _domain_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("dataharness")
            ):
                assert node.module.startswith("dataharness.domain"), (
                    f"{py.name} 反向导入了内部模块 {node.module}"
                )
