"""Phase 12 WebUI 控制面与同源托管组合验收。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dataharness.api import ApiService, create_app
from dataharness.config import PathsConfig, Settings
from dataharness.domain import TaskId


def _client(tmp_path: Path, *, static_dir: Path | None = None) -> TestClient:
    """使用真实 Runtime/Workspace 构造本地 API，不连接模型或外部 Sandbox。"""
    settings = Settings(paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"))
    service = ApiService.from_settings(settings)
    return TestClient(create_app(service, static_dir=static_dir))


def test_webui_supports_archive_task_entry_and_sanitized_diagnostics(tmp_path: Path) -> None:
    """归档必须在 API 层封住后续写入，不能只依赖 WebUI 禁用按钮。"""
    client = _client(tmp_path)
    project = client.post("/projects", json={"name": "phase12"}).json()
    project_id = project["id"]
    assert client.get(f"/projects/{project_id}/tasks").json() == []
    snapshot = client.post(f"/projects/{project_id}/snapshots").json()
    diagnostics = client.get("/diagnostics")
    assert diagnostics.status_code == 200
    assert "api_key" not in diagnostics.text.lower()
    archived = client.post(f"/projects/{project_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    # 直接调用 API 也不能绕过前端的归档禁用状态，避免产生新的 Session/Task/Run 事实。
    assert client.post(f"/projects/{project_id}/snapshots").status_code == 400
    assert (
        client.post(f"/projects/{project_id}/sessions", json={"label": "late"}).status_code == 400
    )
    assert (
        client.post(
            f"/projects/{project_id}/tasks",
            json={"project_snapshot_id": snapshot["id"], "prompt": "不应执行", "session_id": None},
        ).status_code
        == 400
    )


def test_fastapi_serves_built_spa_same_origin_without_node_runtime(tmp_path: Path) -> None:
    """同源构建物由 FastAPI 返回，前端路由刷新也回到 index.html。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><body>DataHarness WebUI</body></html>", encoding="utf-8"
    )
    client = _client(tmp_path, static_dir=dist)
    assert client.get("/").status_code == 200
    spa_response = client.get("/projects/unknown", headers={"accept": "text/html"})
    assert "DataHarness WebUI" in spa_response.text
    # 同一 URL 的 HTML 导航与 JSON API 是两种表示；否则浏览器缓存会把 index.html
    # 复用于前端 fetch，最终表现为 Unexpected token '<'。
    assert spa_response.headers["vary"] == "Accept"
    assert spa_response.headers["cache-control"] == "no-store"
    api_response = client.get("/projects/unknown", headers={"accept": "application/json"})
    assert api_response.status_code == 404
    assert api_response.headers["content-type"].startswith("application/json")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_terminal_task_retry_creates_a_new_task_and_run(tmp_path: Path) -> None:
    """终态 Task 保留审计历史，重试必须转为新的 Task/Run 而不是返回必然失败的 409。"""
    settings = Settings(paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"))
    service = ApiService.from_settings(settings)
    client = TestClient(create_app(service))
    project = client.post("/projects", json={"name": "retry"}).json()
    snapshot = client.post(f"/projects/{project['id']}/snapshots").json()
    created = client.post(
        f"/projects/{project['id']}/tasks",
        json={"project_snapshot_id": snapshot["id"], "prompt": "重试原问题", "session_id": None},
    ).json()
    task_id = created["task"]["id"]
    with service.store.unit_of_work() as uow:
        stored = uow.repo.get_task(TaskId(task_id))
        uow.repo.save_task(stored.value.fail(), stored.version, "TASK_FAILED")

    retried = client.post(f"/tasks/{task_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["task"]["id"] != task_id
    assert retried.json()["run"]["task_id"] == retried.json()["task"]["id"]
