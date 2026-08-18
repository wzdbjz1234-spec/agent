"""Wolfram Skill 的 Sandbox-only 工具边界测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from dataharness.agent.tools import run_wolfram
from dataharness.domain import TaskId
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
from dataharness.storage import PrivacyConnectionFactory


@dataclass
class FakeProvider:
    requests: list[str] = field(default_factory=list)

    def complete(self, request: str) -> str:
        self.requests.append(request)
        return "ok"


@dataclass
class FakeAnalysis:
    codes: list[str] = field(default_factory=list)

    async def execute_python(self, code: str, *, timeout_seconds: int, budget_units: int):
        self.codes.append(code)
        return {"status": "SUCCEEDED", "timeout_seconds": timeout_seconds}


@pytest.mark.asyncio
async def test_wolfram_expression_is_only_wrapped_for_sandbox_execution(tmp_path: Path) -> None:
    provider = FakeProvider()
    gateway = ModelGateway(
            provider,
            PrivacyPolicy(
                PlaceholderStore(
                    PrivacyConnectionFactory(tmp_path / "privacy", tmp_path / "runtime.db")
                )
        ),
    )
    analysis = FakeAnalysis()
    deps = SimpleNamespace(
        task_id=TaskId("task"),
        run_id=None,
        gateway=gateway,
        analysis=analysis,
    )

    result = await run_wolfram(SimpleNamespace(deps=deps), "Integrate[x, x]")

    assert "wolframscript" in analysis.codes[0]
    assert "Integrate[x, x]" in analysis.codes[0]
    assert result.startswith('{"status": "SUCCEEDED"')
