"""依赖方向检查器测试：正例（现有代码通过）与负例（合成违规被标记）。"""

from __future__ import annotations

from pathlib import Path

import dataharness
from dataharness.tooling.dependency_check import (
    ImportRules,
    check_imports,
    dataharness_rules,
)

PACKAGE_ROOT = Path(dataharness.__file__).parent


def test_dataharness_passes_dependency_check() -> None:
    violations = check_imports(PACKAGE_ROOT, dataharness_rules())
    assert violations == []


def test_flags_forbidden_framework(tmp_path: Path) -> None:
    src = tmp_path / "fakepkg"
    src.mkdir()
    (src / "mod.py").write_text("import fastapi\n", encoding="utf-8")
    rules = ImportRules(forbidden_roots={"fakepkg": frozenset({"fastapi"})})
    violations = check_imports(src, rules)
    assert len(violations) == 1
    assert violations[0].imported == "fastapi"


def test_flags_forbidden_framework_from_import(tmp_path: Path) -> None:
    src = tmp_path / "fakepkg"
    src.mkdir()
    (src / "mod.py").write_text("from sqlite3 import connect\n", encoding="utf-8")
    rules = ImportRules(forbidden_roots={"fakepkg": frozenset({"sqlite3"})})
    violations = check_imports(src, rules)
    assert len(violations) == 1
    assert violations[0].imported == "sqlite3"


def test_flags_reverse_internal_import(tmp_path: Path) -> None:
    src = tmp_path / "fakepkg"
    src.mkdir()
    (src / "domain.py").write_text("from fakepkg.api import routes\n", encoding="utf-8")
    rules = ImportRules(forbidden_internal={"fakepkg.domain": frozenset({"fakepkg.api"})})
    violations = check_imports(src, rules)
    assert len(violations) == 1
    assert violations[0].imported == "fakepkg.api"


def test_no_violation_without_matching_rules(tmp_path: Path) -> None:
    src = tmp_path / "fakepkg"
    src.mkdir()
    (src / "mod.py").write_text("import os\n", encoding="utf-8")
    assert check_imports(src, ImportRules()) == []


def test_relative_imports_are_ignored(tmp_path: Path) -> None:
    src = tmp_path / "fakepkg"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod.py").write_text("from . import sibling\n", encoding="utf-8")
    rules = ImportRules(forbidden_roots={"fakepkg": frozenset({"fakepkg"})})
    assert check_imports(src, rules) == []
