"""Chat-first API 验收：普通消息不创建 Task/Run，只保存可见对话消息。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from dataharness.api import ApiService, create_app
from dataharness.config import PathsConfig, Settings
from dataharness.conversations import ConversationAgentService
from dataharness.domain import ProjectId
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
from dataharness.storage import PrivacyConnectionFactory


@dataclass
class FakeChatProvider:
    calls: list[str] = field(default_factory=list)
    use_tool: bool = False
    use_job: bool = False

    def complete(self, request: str) -> str:
        self.calls.append(request)
        if self.use_tool and len(self.calls) == 1:
            return '{"tool_call":{"name":"chat_list_project_files","args":{}}}'
        if self.use_job and len(self.calls) == 1:
            return (
                '{"tool_call":{"name":"chat_start_analysis_job",'
                '"args":{"reason":"需要隔离计算"}}}'
            )
        return "这是一个自然语言回答，不是模型 JSON。"


def _client(tmp_path: Path) -> tuple[TestClient, ApiService, FakeChatProvider]:
    settings = Settings(paths=PathsConfig(runtime_data_root=tmp_path / "runtime-data"))
    service = ApiService.from_settings(settings)
    provider = FakeChatProvider()
    privacy = PlaceholderStore(
        PrivacyConnectionFactory(
            settings.paths.privacy_root or settings.paths.runtime_data_root / "privacy",
            settings.paths.runtime_db,
        )
    )
    service.conversation_agent = ConversationAgentService(
        service.store,
        service.corpus,
        service.workspace,
        ModelGateway(provider, PrivacyPolicy(privacy)),
        analysis_job_launcher=service.launch_analysis_job,
    )
    return TestClient(create_app(service)), service, provider


def test_chat_turn_is_not_a_task_and_visible_messages_are_optional(tmp_path: Path) -> None:
    client, service, provider = _client(tmp_path)
    project = client.post("/projects", json={"name": "chat"}).json()
    session = client.post(
        f"/projects/{project['id']}/sessions", json={"label": "分析对话"}
    ).json()

    response = client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/messages",
        json={"content": "请用一句话回答", "persist": True},
    )
    assert response.status_code == 200
    assert response.json()["assistant"]["content"] == "这是一个自然语言回答，不是模型 JSON。"
    assert client.get(f"/projects/{project['id']}/tasks").json() == []
    saved = client.get(
        f"/projects/{project['id']}/sessions/{session['id']}/messages"
    ).json()
    assert [item["role"] for item in saved] == ["user", "assistant"]
    assert provider.calls

    transient = client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/messages",
        json={"content": "不要保存这一轮", "persist": False},
    )
    assert transient.status_code == 200
    saved_after = client.get(
        f"/projects/{project['id']}/sessions/{session['id']}/messages"
    ).json()
    assert len(saved_after) == 2
    with service.store.unit_of_work() as uow:
        assert uow.repo.list_tasks_for_project(ProjectId(project["id"])) == ()


def test_chat_agent_can_choose_a_local_data_tool_without_creating_a_task(tmp_path: Path) -> None:
    client, service, provider = _client(tmp_path)
    provider.use_tool = True
    project = client.post("/projects", json={"name": "tool-chat"}).json()
    client.post(
        f"/projects/{project['id']}/files",
        content=b"name\nalice\n",
        headers={"x-file-name": "names.csv", "content-type": "application/octet-stream"},
    )
    session = client.post(
        f"/projects/{project['id']}/sessions", json={"label": "本地检索"}
    ).json()

    response = client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/messages",
        json={"content": "请列出项目文件", "persist": False},
    )
    assert response.status_code == 200
    assert response.json()["assistant"]["content"] == "这是一个自然语言回答，不是模型 JSON。"
    assert len(provider.calls) == 2
    assert client.get(f"/projects/{project['id']}/tasks").json() == []
    assert response.json()["snapshot_id"] is None
    with service.store.unit_of_work() as uow:
        assert uow.repo.list_tasks_for_project(ProjectId(project["id"])) == ()


def test_chat_agent_can_explicitly_upgrade_to_an_analysis_job(tmp_path: Path) -> None:
    client, service, provider = _client(tmp_path)
    provider.use_job = True
    project = client.post("/projects", json={"name": "job-chat"}).json()
    session = client.post(
        f"/projects/{project['id']}/sessions", json={"label": "长程分析"}
    ).json()

    response = client.post(
        f"/projects/{project['id']}/sessions/{session['id']}/messages",
        json={"content": "请运行一次隔离分析", "persist": False},
    )
    assert response.status_code == 200
    job = response.json()["analysis_job"]
    assert job["task_id"]
    assert job["run_id"]
    assert job["snapshot_id"]
    assert client.get(f"/projects/{project['id']}/tasks").json()[0]["id"] == job["task_id"]
    with service.store.unit_of_work() as uow:
        assert len(uow.repo.list_tasks_for_project(ProjectId(project["id"]))) == 1
