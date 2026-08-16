"""前台 Python Supervisor 的配置和启动前置测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataharness.launcher import LaunchError, LaunchOptions, LocalRuntime


def _write_runtime_config(path: Path, *, digest: str | None = "sha256:" + "a" * 64) -> None:
    sandbox_digest = f'image_digest = "{digest}"\n' if digest else ""
    path.write_text(
        "[model]\napi_key = \"synthetic-key\"\n"
        "[sandbox]\nnetwork_enabled = false\n"
        + sandbox_digest,
        encoding="utf-8",
    )


def test_launch_options_use_setup_marker_defaults(tmp_path: Path) -> None:
    state = tmp_path / ".dataharness"
    state.mkdir()
    config = state / "config.toml"
    sandbox = tmp_path / "sandbox.toml"
    _write_runtime_config(config)
    sandbox.write_text("[storage]\n", encoding="utf-8")
    (state / "setup.json").write_text(
        json.dumps(
            {
                "config_path": str(config),
                "sandbox_config_path": str(sandbox),
                "sandbox_server_version": "0.2.2",
                "api_port": 18000,
                "sandbox_port": 18080,
            }
        ),
        encoding="utf-8",
    )

    options = LaunchOptions.from_workspace(tmp_path)

    assert options.config_path == config.resolve()
    assert options.sandbox_config_path == sandbox.resolve()
    assert options.api_port == 18000
    assert options.sandbox_port == 18080


def test_launch_options_fall_back_to_local_config(tmp_path: Path) -> None:
    local_config = tmp_path / "dataharness.local.toml"
    local_config.write_text("[model]\napi_key = \"synthetic-key\"\n", encoding="utf-8")

    options = LaunchOptions.from_workspace(tmp_path)

    assert options.config_path == local_config.resolve()


def test_runtime_rejects_unlocked_sandbox_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    sandbox = tmp_path / "sandbox.toml"
    _write_runtime_config(config, digest=None)
    sandbox.write_text("[storage]\n", encoding="utf-8")
    options = LaunchOptions(
        root=tmp_path,
        config_path=config,
        sandbox_config_path=sandbox,
        sandbox_server_version="0.2.2",
        api_port=18000,
        sandbox_port=18080,
    )

    with pytest.raises(LaunchError, match="image_digest"):
        LocalRuntime(options)
