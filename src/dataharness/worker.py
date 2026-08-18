"""个人版独立 Worker 的装配入口。

API 进程不持有这个对象。CLI/部署脚本单独调用 :func:`build_local_worker`，因此 API
重启不会取消长任务，且所有 Sandbox 创建仍由 LocalDurableExecutor 的 lease/fencing
边界管理。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dataharness.agent import AgentRunHandler
from dataharness.api import ApiService
from dataharness.config import Settings
from dataharness.domain import Run, SnapshotId, TaskId
from dataharness.orchestration import PolicyDeniedError
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
from dataharness.providers.durable import LocalDurableExecutor
from dataharness.providers.memory import SqliteHistoryStore
from dataharness.providers.model import OpenAICompatibleCloudModelProvider
from dataharness.providers.sandbox import OpenSandboxProvider, SdkOpenSandboxClient
from dataharness.sandbox import SandboxMount, SandboxResources, SandboxSpec
from dataharness.skills import SkillRegistry
from dataharness.storage import PrivacyConnectionFactory


def _mount_resolver(service: ApiService, source_ref: str) -> str:
    """把受控 resource ref 解析成 Host 路径；绝不接受外部输入的裸路径。"""
    workspace = service.workspace
    if workspace is None:
        raise PolicyDeniedError("Workspace 未装配")
    if source_ref.startswith("snapshot:"):
        snapshot_id = source_ref.removeprefix("snapshot:")
        with service.store.unit_of_work() as uow:
            snapshot = uow.repo.get_snapshot(SnapshotId(snapshot_id))
            files = tuple(
                (entry.file_id, entry.file_version_id, uow.repo.get_file(entry.file_id).name)
                for entry in snapshot.ready_entries()
            )
        # Phase 00–10 创建的历史 Snapshot 没有物理视图；首次使用时按不可变事实
        # 追加 materialize，仍然只复制该 Snapshot 的 READY 版本。
        try:
            candidate = workspace.snapshot_path(snapshot.project_id, snapshot.id)
        except FileNotFoundError:
            workspace.materialize_snapshot(snapshot.project_id, snapshot.id, files)
            candidate = workspace.snapshot_path(snapshot.project_id, snapshot.id)
    elif source_ref.startswith("task:"):
        pieces = source_ref.split(":")
        if len(pieces) != 3 or pieces[2] not in {"working", "staging"}:
            raise PolicyDeniedError("Sandbox 挂载引用不在白名单内")
        task_id = pieces[1]
        with service.store.unit_of_work() as uow:
            task = uow.repo.get_task(TaskId(task_id))
        candidate = workspace.root / str(task.value.project_id) / "tasks" / task_id / pieces[2]
    else:
        raise PolicyDeniedError("Sandbox 挂载引用不在白名单内")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(workspace.root):
        raise PolicyDeniedError("Sandbox 挂载路径越出 Workspace 根目录")
    if not resolved.exists() or not resolved.is_dir():
        raise PolicyDeniedError("Sandbox 挂载目录不存在")
    return str(resolved)


def _sandbox_factory(service: ApiService, settings: Settings):
    """构造每个 Run 固定 digest、Snapshot 和 Task 写域的 SandboxSpec。"""
    digest = settings.sandbox.image_digest
    if not digest:
        raise PolicyDeniedError("Sandbox 镜像 digest 未配置")

    def factory(run):
        return SandboxSpec(
            project_id=run.project_id,
            task_id=run.task_id,
            run_id=run.id,
            project_snapshot_id=run.project_snapshot_id,
            runtime=settings.sandbox.runtime,
            image_digest=digest,
            mounts=(
                SandboxMount(
                    source_ref=f"snapshot:{run.project_snapshot_id}",
                    target="/project",
                    read_only=True,
                ),
                SandboxMount(
                    source_ref=f"task:{run.task_id}:working",
                    target="/task/working",
                    read_only=False,
                ),
                SandboxMount(
                    source_ref=f"task:{run.task_id}:staging",
                    target="/task/staging",
                    read_only=False,
                ),
            ),
            resources=SandboxResources(
                cpu_limit=settings.resources.cpu_limit,
                memory_mb=settings.resources.memory_mb,
                disk_mb=settings.resources.disk_mb,
                max_processes=settings.resources.max_processes,
                max_output_bytes=settings.resources.max_output_bytes,
                step_timeout_seconds=settings.resources.step_timeout_seconds,
            ),
        )

    return factory


def build_local_worker(
    settings: Settings,
    service: ApiService,
    *,
    owner: str | None = None,
    sandbox_provider=None,
    cloud_provider=None,
    history_store=None,
    on_run_state: Callable[[Run | None], None] | None = None,
) -> LocalDurableExecutor:
    """构造独立 Worker；测试可注入 fake Cloud/Sandbox，生产默认使用正式 Adapter。"""
    if service.workspace is None or service.bridge is None or service.verification is None:
        raise RuntimeError("Worker 需要完整的 API/Workspace/Verification 装配")
    workspace = service.workspace
    # DeepSeek 提供 OpenAI-compatible Chat Completions；使用同一个窄 Provider，
    # 但保留显式 provider 名称，便于配置和诊断区分实际模型服务。
    supported_providers = {"openai", "openai-compatible", "deepseek"}
    if settings.model.provider.casefold() not in supported_providers and cloud_provider is None:
        raise PolicyDeniedError("当前只支持 OpenAI-compatible 模型 Provider（包括 deepseek）")
    provider = cloud_provider or OpenAICompatibleCloudModelProvider.from_config(settings.model)
    privacy = PlaceholderStore(
        PrivacyConnectionFactory(
            settings.paths.privacy_root or settings.paths.runtime_data_root / "privacy",
            settings.paths.runtime_db,
        )
    )
    gateway = ModelGateway(provider, PrivacyPolicy(privacy))
    sandbox = sandbox_provider
    if sandbox is None:
        client = SdkOpenSandboxClient(
            endpoint=settings.sandbox.endpoint,
            api_key=settings.sandbox.api_key,
            mount_resolver=lambda ref: _mount_resolver(service, ref),
        )
        sandbox = OpenSandboxProvider(client)
    handler = AgentRunHandler(
        service.store,
        service.corpus,
        workspace,
        sandbox,
        gateway,
        SkillRegistry(settings.paths.skills_root or Path("skills")),
        bridge=service.bridge,
        verification=service.verification,
        active_skills=tuple((name, None) for name in settings.skills.active),
        history_store=history_store
        or SqliteHistoryStore(settings.paths.runtime_data_root / "history.db"),
    )
    return LocalDurableExecutor(
        service.store,
        handler,
        owner=owner or f"worker-{os.getpid()}",
        max_retries=settings.budget.max_consecutive_failures,
        lease_duration=timedelta(seconds=30),
        sandbox=sandbox,
        sandbox_spec_factory=_sandbox_factory(service, settings),
        workspace=workspace,
        on_run_state=on_run_state,
    )


@dataclass(slots=True)
class WorkerHealthWriter:
    """写入受控 Worker 心跳文件的最小监督器。

    心跳只包含 PID、生命周期状态、Run/Task 稳定 ID 和时间戳，不包含 prompt、模型回复、
    API Key、Workspace 路径或 Sandbox 输出。文件采用临时文件替换，status.ps1 读取时不会
    看到半截 JSON。
    """

    path: Path
    pid: int
    owner: str
    status: str = "STARTING"
    active_run_id: str | None = None
    active_task_id: str | None = None

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def update(self, *, status: str | None = None, run: Run | None = None) -> None:
        """更新有界健康状态；状态写入失败不能让 Worker 丢失执行机会。"""
        if status is not None:
            self.status = status
        if run is not None:
            self.active_run_id = str(run.id)
            self.active_task_id = str(run.task_id)
        elif status in {"IDLE", "STOPPING", "STOPPED", "FAILED"}:
            self.active_run_id = None
            self.active_task_id = None
        payload = {
            "schema_version": 1,
            "pid": self.pid,
            "owner": self.owner,
            "status": self.status,
            "active_run_id": self.active_run_id,
            "active_task_id": self.active_task_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            temporary.replace(self.path)
        except OSError:
            # 状态文件是诊断派生物，不是 Runtime 事实源；磁盘短暂不可写时仍让
            # Worker 继续遵守 SQLite lease 语义，status.ps1 会报告 HEARTBEAT_UNAVAILABLE。
            with contextlib.suppress(OSError):
                temporary.unlink()


async def run_managed_worker(
    executor: LocalDurableExecutor,
    health: WorkerHealthWriter,
    shutdown_file: Path,
    *,
    heartbeat_seconds: float = 1.0,
) -> None:
    """运行可被 stop.ps1 请求 drain 的 Worker。

    stop.ps1 只创建一个本机 shutdown marker；watcher 看到 marker 后设置 stop_event，
    Worker 不再领取新 Run，当前 Run 仍由 Executor 完成取消/终态收口和 Sandbox 清理。
    这避免用 ``Stop-Process`` 粗暴打断 SQLite lease 或外部执行。
    """
    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds 必须为正")
    stop_event = asyncio.Event()
    health.update(status="STARTING")

    async def watch_shutdown() -> None:
        """轮询 marker；不监听任意网络命令，也不把停止请求写入 Runtime 数据库。"""
        while not stop_event.is_set():
            if shutdown_file.exists():
                health.update(status="STOPPING")
                stop_event.set()
                return
            # 空闲循环不会触发 Run 状态回调；用当前稳定 ID 将心跳从 STARTING
            # 投影为 IDLE，避免 status.ps1 和重复启动把健康 Worker 判为未就绪。
            health.update(status="RUNNING" if health.active_run_id is not None else "IDLE")
            await asyncio.sleep(heartbeat_seconds)

    watcher = asyncio.create_task(watch_shutdown())
    try:
        await executor.run_worker(stop_event)
    except BaseException:
        health.update(status="FAILED")
        raise
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        health.update(status="STOPPED")
