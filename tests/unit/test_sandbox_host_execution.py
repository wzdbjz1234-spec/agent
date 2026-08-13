"""静态证明 Sandbox 边界没有生成代码的 Host 执行回退。"""

from __future__ import annotations

import ast
from pathlib import Path

import dataharness


def test_sandbox_boundary_never_imports_or_calls_host_execution_primitives() -> None:
    """协议、Provider 与 fake 只转发不可信 code；禁止 subprocess、shell、exec 和 eval。"""
    roots = (
        Path(dataharness.__file__).parent / "sandbox",
        Path(dataharness.__file__).parent / "providers" / "sandbox",
    )
    forbidden_modules = {"subprocess", "shlex"}
    forbidden_calls = {"exec", "eval", "system", "popen", "run", "Popen"}
    for root in roots:
        for source in root.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(
                        alias.name.split(".")[0] not in forbidden_modules for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in forbidden_modules
                elif isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else None
                    assert name not in forbidden_calls
