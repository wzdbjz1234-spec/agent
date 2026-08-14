"""Phase 09 本地 FastAPI 控制面组合验收。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dataharness.api import ApiService, create_app
from dataharness.config import PathsConfig, Settings
from dataharness.domain import ProjectId


def _client(tmp_path: Path) -> tuple[TestClient, ApiService]:
    """使用临时 Runtime/Workspace 构造真实应用服务，不连接公网或真实模型。"""
    settings = Settings(paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"))
    service = ApiService.from_settings(settings)
    return TestClient(create_app(service)), service


def test_api_project_file_task_and_events_flow_is_thin_and_local(tmp_path: Path) -> None:
    """HTTP 只调用应用服务，项目文件、固定 Snapshot 与 Task 事件可被查询。"""
    client, service = _client(tmp_path)

    project_response = client.post("/projects", json={"name": "api-project"})
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    imported = client.post(
        f"/projects/{project_id}/files",
        content=b"name\nalice\n",
        headers={"x-file-name": "names.csv", "content-type": "application/octet-stream"},
    )
    assert imported.status_code == 201
    assert imported.json()["status"] == "READY"

    snapshot = service.corpus.create_snapshot(ProjectId(project_id))
    task_response = client.post(
        f"/projects/{project_id}/tasks",
        json={"project_snapshot_id": str(snapshot.id)},
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["task"]["id"]
    assert task_response.json()["run"]["project_snapshot_id"] == str(snapshot.id)

    assert client.get(f"/tasks/{task_id}").status_code == 200
    events = client.get(f"/tasks/{task_id}/events")
    assert events.status_code == 200
    assert any(item["event_type"] == "TASK_CREATED" for item in events.json())


def test_api_errors_are_unified_and_do_not_echo_storage_details(tmp_path: Path) -> None:
    """不存在资源返回稳定错误 DTO，不泄露 SQL、路径或异常正文。"""
    client, _ = _client(tmp_path)
    response = client.get("/projects/not-found")
    assert response.status_code == 404
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "SELECT" not in response.text
