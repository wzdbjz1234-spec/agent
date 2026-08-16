"""配置模型与 TOML 加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dataharness.config import Settings, load_settings


def test_default_paths_derived_from_runtime_root() -> None:
    settings = Settings()
    assert settings.paths.projects_root == settings.paths.runtime_data_root / "projects"
    assert settings.paths.privacy_root == settings.paths.runtime_data_root / "privacy"
    assert settings.paths.runtime_db == settings.paths.runtime_data_root / "runtime.db"


def test_default_sandbox_network_disabled() -> None:
    assert Settings().sandbox.network_enabled is False


def test_sandbox_network_cannot_be_enabled() -> None:
    """配置也不能成为放宽 Sandbox 网络边界的旁路。"""
    with pytest.raises(ValidationError):
        Settings.model_validate({"sandbox": {"network_enabled": True}})


def test_load_settings_from_toml_overrides_and_falls_back(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        (
            '[model]\nprovider = "anthropic"\nmodel = "claude-3"\n'
            'api_key = "synthetic-key"\n[budget]\nmax_analysis_steps = 10\n'
        ),
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.model.provider == "anthropic"
    assert settings.model.model == "claude-3"
    assert settings.model.api_key == "synthetic-key"
    assert settings.budget.max_analysis_steps == 10
    # 未覆盖字段使用默认值
    assert settings.sandbox.network_enabled is False


def test_supported_formats_from_toml_list(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[extraction]\nsupported_formats = ["csv", "pdf"]\n', encoding="utf-8")
    assert load_settings(config).extraction.supported_formats == ("csv", "pdf")


def test_load_settings_invalid_raises(tmp_path: Path) -> None:
    config = tmp_path / "bad.toml"
    config.write_text('[budget]\nmax_analysis_steps = "not-a-number"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(config)


def test_load_settings_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "missing.toml")
