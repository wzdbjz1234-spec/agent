"""SdkOpenSandboxClient 的映射与 fail-closed 单元测试（不连接真实服务）。"""

from __future__ import annotations

from typing import Any

import pytest
from opensandbox.exceptions import SandboxException
from opensandbox.models.execd import (
    Execution,
    ExecutionComplete,
    ExecutionError,
    ExecutionLogs,
    OutputMessage,
)
from opensandbox.sandbox import Sandbox as SdkSandbox

from dataharness.domain import ProjectId, RunId, SnapshotId, StepId, TaskId
from dataharness.providers.sandbox.opensandbox_sdk import (
    SdkOpenSandboxClient,
    _execution_status,
    _parse_probe,
)
from dataharness.sandbox import (
    ExecutionKind,
    ExecutionRequest,
    ExecutionStatus,
    SandboxMount,
    SandboxPolicyError,
    SandboxResources,
    SandboxSpec,
    SandboxTimeoutError,
)

DIGEST = "sha256:" + "c" * 64

_PROBE_OK = "\n".join(
    [
        "USER=sandbox",
        "UID=10001",
        "NO_NEW_PRIVS=1",
        "CAP_EFF=0000000000000000",
        "TOUCH_ROOT=denied",
        "NET_PROBE=denied",
        "MOUNT_PROJECT=present",
        "MOUNT_WORKING=present",
        "MOUNT_STAGING=present",
        "WRITE_PROJECT=denied",
        "WRITE_WORKING=ok",
        "WRITE_STAGING=ok",
        "MEM_LIMIT=1073741824",
    ]
)


def _spec() -> SandboxSpec:
    return SandboxSpec(
        project_id=ProjectId("project-1"),
        task_id=TaskId("task-1"),
        run_id=RunId("run-1"),
        project_snapshot_id=SnapshotId("snapshot-1"),
        image_digest=DIGEST,
        mounts=(
            SandboxMount(source_ref="snapshot:snapshot-1", target="/project", read_only=True),
            SandboxMount(source_ref="task:task-1:working", target="/task/working", read_only=False),
            SandboxMount(source_ref="task:task-1:staging", target="/task/staging", read_only=False),
        ),
        resources=SandboxResources(
            memory_mb=1024,
            disk_mb=2048,
            max_output_bytes=1_000_000,
            step_timeout_seconds=30,
        ),
    )


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        step_id=StepId("step-1"),
        kind=ExecutionKind.PYTHON,
        code="print('hello')",
        timeout_seconds=10,
    )


def _client(**kwargs: Any) -> SdkOpenSandboxClient:
    return SdkOpenSandboxClient(
        endpoint="http://localhost:8080",
        mount_resolver=lambda ref: f"C:\\mounts\\{ref.replace(':', '-')}",
        **kwargs,
    )


class _Info:
    """sandbox.get_info() 的轻量返回。"""

    def __init__(self, image_uri: str = f"secure-analysis@{DIGEST}") -> None:
        self.image = type("Image", (), {"uri": image_uri})()
        hex_part = DIGEST.removeprefix("sha256:")
        self.metadata = {
            "dataharness.image_digest": hex_part[:32],
            "dataharness.image_digest_tail": hex_part[32:],
            "dataharness.project_snapshot_id": "snapshot-1",
        }


class _FakeCommands:
    def __init__(self, fake: _FakeSdkSandbox) -> None:
        self._fake = fake

    async def run(self, command: str, *, opts: Any = None, handlers: Any = None) -> Execution:
        return await self._fake.commands_run(command, opts)

    async def interrupt(self, execution_id: str) -> None:
        return await self._fake.commands_interrupt(execution_id)


class _FakeFiles:
    def __init__(self, fake: _FakeSdkSandbox) -> None:
        self._fake = fake

    async def write_files(self, entries: list[Any]) -> None:
        return await self._fake.files_write(entries)

    async def read_file(self, path: str, **kwargs: Any) -> str:
        return await self._fake.files_read(path, **kwargs)

    async def remove_files(self, paths: list[str]) -> None:
        return await self._fake.files_remove(paths)

    async def delete_files(self, paths: list[str]) -> None:
        return await self._fake.files_remove(paths)


class _FakeSdkSandbox:
    """SDK Sandbox 的轻量 fake：只记录调用并返回脚本化结果。"""

    def __init__(self, sandbox_id: str = "sandbox-1") -> None:
        self.id = sandbox_id
        self.commands = _FakeCommands(self)
        self.files = _FakeFiles(self)
        self.info: _Info | None = None
        self.created_args: tuple[Any, ...] | None = None
        self.probe_output = _PROBE_OK
        self.probe_error: Exception | None = None
        self.execution = Execution(
            id="exec-1",
            exit_code=0,
            complete=ExecutionComplete(timestamp=1, execution_time_in_millis=10),
            logs=ExecutionLogs(stdout=[OutputMessage(text="hello\n", timestamp=1)]),
        )
        self.execution_error: Exception | None = None
        self.written: list[dict[str, str]] = []
        self.removed: list[str] = []
        self.interrupted: list[str] = []
        self.destroyed = False
        self.sidecar: str | None = None

    async def get_info(self) -> _Info:
        if self.info is None:
            raise AssertionError("no info scripted")
        return self.info

    async def commands_run(self, command: str, opts: Any) -> Execution:
        if self.probe_error is not None:
            raise self.probe_error
        if "__dataharness_probe__" in command:
            return Execution(
                id="probe-1",
                exit_code=0,
                logs=ExecutionLogs(stdout=[OutputMessage(text=self.probe_output, timestamp=1)]),
            )
        if self.execution_error is not None:
            raise self.execution_error
        return self.execution

    async def commands_interrupt(self, execution_id: str) -> None:
        self.interrupted.append(execution_id)

    async def files_write(self, entries: list[Any]) -> None:
        for entry in entries:
            self.written.append({"path": entry.path, "data": str(entry.data)})

    async def files_read(self, path: str, **kwargs: Any) -> str:
        if self.sidecar is None:
            raise SandboxException("no such file")
        return self.sidecar

    async def files_remove(self, paths: list[str]) -> None:
        self.removed.extend(paths)

    async def destroy(self) -> None:
        self.destroyed = True


@pytest.fixture
def fake_sandbox(monkeypatch: pytest.MonkeyPatch) -> _FakeSdkSandbox:
    fake = _FakeSdkSandbox()

    async def connect(cls: Any, sandbox_id: str, **kwargs: Any) -> _FakeSdkSandbox:
        return fake

    async def create(cls: Any, *args: Any, **kwargs: Any) -> _FakeSdkSandbox:
        if fake.info is None:
            fake.info = _Info()
        fake.created_args = (args, kwargs)
        return fake

    monkeypatch.setattr(SdkSandbox, "connect", classmethod(connect))
    monkeypatch.setattr(SdkSandbox, "create", classmethod(create))
    return fake


async def test_create_sandbox_passes_locked_digest_and_deny_all_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def create(cls: Any, *args: Any, **kwargs: Any) -> _FakeSdkSandbox:
        created = _FakeSdkSandbox()
        created.info = _Info()
        captured["args"] = args
        captured["kwargs"] = kwargs
        return created

    monkeypatch.setattr(SdkSandbox, "create", classmethod(create))
    client = _client()
    sandbox_id = await client.create_sandbox(_spec())

    assert sandbox_id == "sandbox-1"
    assert captured["args"][0] == f"secure-analysis@{DIGEST}"
    kwargs = captured["kwargs"]
    assert kwargs["network_policy"].default_action == "deny"
    assert kwargs["network_policy"].egress == []
    assert kwargs["metadata"]["dataharness.image_digest"] == DIGEST.removeprefix("sha256:")[:32]
    assert (
        kwargs["metadata"]["dataharness.image_digest_tail"] == DIGEST.removeprefix("sha256:")[32:]
    )
    volumes = kwargs["volumes"]
    assert [volume.mount_path for volume in volumes] == [
        "/project",
        "/task/working",
        "/task/staging",
    ]
    assert [volume.read_only for volume in volumes] == [True, False, False]
    assert kwargs["resource"]["memory"] == "1024Mi"


@pytest.mark.asyncio
async def test_inspect_attests_real_probe_facts(fake_sandbox: _FakeSdkSandbox) -> None:
    fake_sandbox.info = _Info()
    client = _client()
    await client.create_sandbox(_spec())
    client._sandboxes.clear()  # 强制走 connect 路径（重连语义）
    attestation = await client.inspect_sandbox("sandbox-1")

    assert attestation.image_digest == DIGEST
    assert attestation.network_enabled is False
    assert attestation.privileged is False
    assert attestation.root_read_only is True
    assert attestation.user == "sandbox"
    assert len(attestation.mounts) == 3


@pytest.mark.asyncio
async def test_inspect_fails_closed_on_digest_drift(fake_sandbox: _FakeSdkSandbox) -> None:
    info = _Info()
    info.metadata = {
        "dataharness.image_digest": "d" * 32,
        "dataharness.image_digest_tail": "d" * 32,
    }
    fake_sandbox.info = info
    client = _client()
    await client.create_sandbox(_spec())
    client._sandboxes.clear()
    with pytest.raises(SandboxPolicyError):
        await client.inspect_sandbox("sandbox-1")


@pytest.mark.asyncio
async def test_inspect_fails_closed_when_probe_reports_network(
    fake_sandbox: _FakeSdkSandbox,
) -> None:
    fake_sandbox.info = _Info()
    fake_sandbox.probe_output = _PROBE_OK.replace("NET_PROBE=denied", "NET_PROBE=connected")
    client = _client()
    await client.create_sandbox(_spec())
    client._sandboxes.clear()
    with pytest.raises(SandboxPolicyError):
        await client.inspect_sandbox("sandbox-1")


@pytest.mark.asyncio
async def test_execute_runs_python_and_maps_result(fake_sandbox: _FakeSdkSandbox) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    result = await client.execute_step("sandbox-1", _request())

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert result.process_id == "exec-1"
    assert fake_sandbox.written[0]["path"] == "/task/working/step-1.py"
    assert fake_sandbox.written[0]["data"] == "print('hello')"


@pytest.mark.asyncio
async def test_execute_sql_uses_builtin_runner(fake_sandbox: _FakeSdkSandbox) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    commands: list[str] = []

    async def run(command: str, opts: Any) -> Execution:
        commands.append(command)
        return fake_sandbox.execution

    fake_sandbox.commands_run = run  # type: ignore[method-assign]
    request = _request().model_copy(update={"kind": ExecutionKind.SQL})
    await client.execute_step("sandbox-1", request)

    assert commands == ["python /usr/local/bin/dataharness-sql-runner.py /task/working/step-1.sql"]


@pytest.mark.asyncio
async def test_execute_timeout_maps_to_timeout_error(
    fake_sandbox: _FakeSdkSandbox,
) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    fake_sandbox.execution_error = SandboxException("command timed out")
    with pytest.raises(SandboxTimeoutError):
        await client.execute_step("sandbox-1", _request())


@pytest.mark.asyncio
async def test_execute_nonzero_exit_maps_to_failed_status(
    fake_sandbox: _FakeSdkSandbox,
) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    fake_sandbox.execution = Execution(
        id="exec-2",
        exit_code=1,
        error=ExecutionError(name="RuntimeError", value="boom", timestamp=1),
        logs=ExecutionLogs(stderr=[OutputMessage(text="boom", timestamp=1)]),
    )
    result = await client.execute_step("sandbox-1", _request())
    assert result.status == ExecutionStatus.FAILED
    assert result.exit_code == 1
    assert result.stderr == "boom"


@pytest.mark.asyncio
async def test_cancel_interrupts_tracked_execution(fake_sandbox: _FakeSdkSandbox) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    await client.execute_step("sandbox-1", _request())
    await client.cancel_step("sandbox-1", "step-1")
    assert fake_sandbox.interrupted == ["exec-1"]


@pytest.mark.asyncio
async def test_cleanup_removes_step_files(fake_sandbox: _FakeSdkSandbox) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    await client.cleanup_step("sandbox-1", "step-1")
    assert "/task/working/step-1.py" in fake_sandbox.removed
    assert "/task/working/step-1.sql" in fake_sandbox.removed


@pytest.mark.asyncio
async def test_terminate_destroys_sandbox(fake_sandbox: _FakeSdkSandbox) -> None:
    client = _client()
    await client.create_sandbox(_spec())
    await client.terminate_sandbox("sandbox-1")
    assert fake_sandbox.destroyed


def test_parse_probe_maps_ok_facts() -> None:
    facts = _parse_probe(_PROBE_OK)
    assert facts["user"] == "sandbox"
    assert facts["no_new_privs"] is True
    assert facts["no_caps"] is True
    assert facts["root_read_only"] is True
    assert facts["network_denied"] is True
    assert facts["project_writable"] is False
    assert facts["working_writable"] is True


def test_parse_probe_missing_keys_fail_closed() -> None:
    facts = _parse_probe("USER=root\n")
    assert facts["no_new_privs"] is False
    assert facts["no_caps"] is False  # CAP_EFF 缺失即无法证明 → fail closed
    assert facts["network_denied"] is False
    assert facts["project_mounted"] is False


def test_execution_status_classification() -> None:
    ok = Execution(id="1", exit_code=0)
    assert _execution_status(ok, 10) == ExecutionStatus.SUCCEEDED
    failed = Execution(id="2", exit_code=2)
    assert _execution_status(failed, 10) == ExecutionStatus.FAILED
    timed = Execution(
        id="3",
        exit_code=137,
        error=ExecutionError(name="Timeout", value="timed out", timestamp=1),
    )
    assert _execution_status(timed, 10) == ExecutionStatus.TIMED_OUT
    cancelled = Execution(
        id="4",
        exit_code=130,
        error=ExecutionError(name="Interrupt", value="cancelled", timestamp=1),
    )
    assert _execution_status(cancelled, 10) == ExecutionStatus.CANCELLED
