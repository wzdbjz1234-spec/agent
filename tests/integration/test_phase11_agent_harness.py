"""Phase 11：真实 Handler 装配、prompt 载荷、Worker 收口与 SSE 回放。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataharness.api import ApiService, create_app
from dataharness.config import PathsConfig, SandboxConfig, Settings
from dataharness.domain import ProjectId, SnapshotId, TaskId
from dataharness.providers.sandbox import FakeSandboxProvider
from dataharness.storage import PrivacyConnectionFactory
from dataharness.worker import build_local_worker


@dataclass
class ScriptedCloud:
    """不访问公网的 OpenAI-compatible CloudModelProvider fake。"""

    responses: list[str]
    calls: list[str] = field(default_factory=list)

    def complete(self, request: str) -> str:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("模型调用次数超出脚本")
        return self.responses.pop(0)


def _service(tmp_path: Path) -> tuple[ApiService, TestClient]:
    settings = Settings(paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"))
    service = ApiService.from_settings(settings)
    return service, TestClient(create_app(service))


@pytest.mark.asyncio
async def test_phase11_prompt_worker_agent_and_sse_replay(tmp_path: Path) -> None:
    """HTTP 问题经 Workspace 进入独立 Worker，Agent 至少使用一个项目工具。"""
    service, client = _service(tmp_path)
    project_id = client.post("/projects", json={"name": "phase11"}).json()["id"]
    imported = client.post(
        f"/projects/{project_id}/files",
        content=b"name\nalpha\n",
        headers={"x-file-name": "data.csv", "content-type": "application/octet-stream"},
    )
    assert imported.status_code == 201
    snapshot = service.corpus.create_snapshot(ProjectId(project_id))
    cloud = ScriptedCloud(
        [
            '{"tool_call":{"name":"list_project_files","args":{}}}',
            '{"status":"COMPLETED","answer":"已检查项目文件","references":[],"unresolved_issues":[]}',
        ]
    )
    settings = Settings(
        paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"),
        sandbox=SandboxConfig(image_digest="sha256:" + "a" * 64),
    )
    task_payload = client.post(
        f"/projects/{project_id}/tasks",
        json={"project_snapshot_id": str(snapshot.id), "prompt": "请检查项目文件"},
    )
    assert task_payload.status_code == 201
    task_id = task_payload.json()["task"]["id"]
    assert task_payload.json()["task"]["prompt_ref"].endswith(":state:PROMPT.json")
    assert service.workspace is not None
    runtime_bytes = service.store.factory.path.read_bytes()
    assert "请检查项目文件".encode() not in runtime_bytes

    worker = build_local_worker(
        settings,
        service,
        owner="phase11-worker",
        sandbox_provider=FakeSandboxProvider(),
        cloud_provider=cloud,
    )
    result = await worker.run_once()
    assert result is not None
    assert str(result.status) == "SUCCEEDED"
    assert len(cloud.calls) >= 2
    assert all('"tools"' in request for request in cloud.calls)
    task = service.get_task(task_id)
    assert str(task.status) == "COMPLETED"
    checkpoint = service.runs.latest_checkpoint(result.id)
    assert checkpoint is not None
    assert checkpoint.project_snapshot_id == SnapshotId(snapshot.id)
    # 只读项目工具不会触发隔离执行环境；Sandbox 只在执行型工具首次调用时创建。
    assert checkpoint.sandbox_id is None

    events = client.get(f"/tasks/{task_id}/events").json()
    assert any(item["event_type"] == "AGENT_STARTED" for item in events)
    assert any(item["event_type"] == "AGENT_COMPLETED" for item in events)
    stream = client.get(f"/tasks/{task_id}/events/stream")
    assert stream.status_code == 200
    assert "event: AGENT_COMPLETED" in stream.text
    last_id = max(item["id"] for item in events)
    replay = client.get(f"/tasks/{task_id}/events/stream", params={"after": last_id})
    assert replay.status_code == 200
    assert replay.text == ""


@pytest.mark.asyncio
async def test_phase11_casual_prompt_uses_the_same_agent_loop(tmp_path: Path) -> None:
    """问候也走同一个自然语言 Agent，不由固定白名单绕过模型。"""
    service, client = _service(tmp_path)
    project_id = client.post("/projects", json={"name": "casual"}).json()["id"]
    snapshot = service.corpus.create_snapshot(ProjectId(project_id))
    task_payload = client.post(
        f"/projects/{project_id}/tasks",
        json={"project_snapshot_id": str(snapshot.id), "prompt": "你好！"},
    )
    task_id = task_payload.json()["task"]["id"]
    sandbox = FakeSandboxProvider()
    cloud = ScriptedCloud(["你好！我是 DataHarness，可以帮你分析当前项目中的数据。"])
    settings = Settings(
        paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"),
        sandbox=SandboxConfig(image_digest="sha256:" + "a" * 64),
    )

    worker = build_local_worker(
        settings,
        service,
        owner="casual-worker",
        sandbox_provider=sandbox,
        cloud_provider=cloud,
    )
    result = await worker.run_once()

    assert result is not None
    assert str(result.status) == "SUCCEEDED"
    assert not sandbox._leases
    assert cloud.calls
    answer = client.get(f"/tasks/{task_id}/answer").json()
    assert answer["task_status"] == "COMPLETED"
    assert "你好" in answer["answer"]


@pytest.mark.asyncio
async def test_phase11_execution_tool_creates_sandbox_lazily(tmp_path: Path) -> None:
    """模型真正调用执行工具后才创建 Sandbox，并在 Run 收口时清理。"""
    service, client = _service(tmp_path)
    project_id = client.post("/projects", json={"name": "lazy-execution"}).json()["id"]
    snapshot = service.corpus.create_snapshot(ProjectId(project_id))
    task_payload = client.post(
        f"/projects/{project_id}/tasks",
        json={"project_snapshot_id": str(snapshot.id), "prompt": "请运行一个简单计算"},
    )
    task_id = task_payload.json()["task"]["id"]
    cloud = ScriptedCloud(
        [
            '{"tool_call":{"name":"execute_python","args":{"code":"print(1)"}}}',
            '{"status":"COMPLETED","answer":"计算完成","references":[],"unresolved_issues":[]}',
        ]
    )
    sandbox = FakeSandboxProvider()
    settings = Settings(
        paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"),
        sandbox=SandboxConfig(image_digest="sha256:" + "b" * 64),
    )
    worker = build_local_worker(
        settings,
        service,
        owner="lazy-execution-worker",
        sandbox_provider=sandbox,
        cloud_provider=cloud,
    )

    result = await worker.run_once()

    assert result is not None
    assert str(result.status) == "SUCCEEDED"
    assert sandbox._sequence == 1
    assert not sandbox._leases
    checkpoint = service.runs.latest_checkpoint(result.id)
    assert checkpoint is not None
    assert checkpoint.sandbox_id is not None
    assert client.get(f"/tasks/{task_id}/answer").json()["answer"] == "计算完成"


def test_phase11_session_scope_and_chart_gate(tmp_path: Path) -> None:
    """Session 历史按 Project 过滤，图表规范拒绝外链和未登记 Dataset。"""
    from dataharness.analysis import ChartSpecError, validate_vega_lite_spec
    from dataharness.capabilities.memory import MemoryCapability
    from dataharness.domain import ProjectId, RunId
    from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
    from dataharness.providers.memory import FakeHistoryStore

    service, client = _service(tmp_path)
    first = client.post("/projects", json={"name": "one"}).json()["id"]
    second = client.post("/projects", json={"name": "two"}).json()["id"]

    # 使用不产生网络请求的 fake gateway；历史作用域是 MemoryCapability 的职责。
    class Echo:
        def complete(self, request: str) -> str:
            return request

    gateway = ModelGateway(
        Echo(),
        PrivacyPolicy(
            PlaceholderStore(
                PrivacyConnectionFactory(tmp_path / "privacy", service.store.factory.path)
            )
        ),
    )
    store = FakeHistoryStore()
    one = MemoryCapability(store, gateway, project_id=ProjectId(first))
    two = MemoryCapability(store, gateway, project_id=ProjectId(second))
    one.remember(
        task_id=TaskId("t1"),
        run_id=RunId("r1"),
        text="same report",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    two.remember(
        task_id=TaskId("t2"),
        run_id=RunId("r2"),
        text="same report",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(one.search("report")) == 1
    assert one.search("report")[0].entry.project_id == ProjectId(first)
    spec = {
        "mark": "bar",
        "data": {"dataset_id": "dataset-1", "content_hash": "hash-1"},
        "encoding": {"x": {"field": "name", "type": "nominal"}},
    }
    assert validate_vega_lite_spec(spec, "dataset-1", "hash-1") == spec
    with pytest.raises(ChartSpecError):
        validate_vega_lite_spec(
            {**spec, "data": {"url": "https://evil.test"}}, "dataset-1", "hash-1"
        )
