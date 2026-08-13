"""领域层纯净性测试。

复用 :mod:`dataharness.tooling.dependency_check` 在源码 AST 层面验证 domain
不导入 FastAPI、PydanticAI、OpenSandbox、SQLite 或遥测 SDK，也不反向导入内部模块。
"""

from __future__ import annotations

from pathlib import Path

import dataharness
from dataharness.tooling.dependency_check import check_imports, dataharness_rules

PACKAGE_ROOT = Path(dataharness.__file__).parent


def test_domain_has_no_dependency_violations() -> None:
    violations = check_imports(PACKAGE_ROOT, dataharness_rules())
    assert violations == []
