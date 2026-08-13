"""最小 CLI 入口测试：配置校验命令的成功与失败路径。"""

from __future__ import annotations

from pathlib import Path

from dataharness.cli import main


def test_check_default_config_ok(capsys) -> None:
    assert main(["check"]) == 0
    assert "配置校验通过" in capsys.readouterr().out


def test_check_explicit_config_ok(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[model]\nprovider = "anthropic"\n', encoding="utf-8")
    assert main(["check", "--config", str(config)]) == 0
    assert "anthropic" in capsys.readouterr().out


def test_check_invalid_config_fails(tmp_path: Path, capsys) -> None:
    config = tmp_path / "bad.toml"
    config.write_text('[budget]\nmax_analysis_steps = "x"\n', encoding="utf-8")
    assert main(["check", "--config", str(config)]) == 1
    assert "配置校验失败" in capsys.readouterr().err


def test_version_flag() -> None:
    assert main(["--version"]) == 0
