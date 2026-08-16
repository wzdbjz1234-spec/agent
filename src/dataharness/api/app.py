"""FastAPI 本地控制面。

路由本身不访问 SQLite、OpenSandbox、模型 SDK 或 Workspace 路径；所有事实查询和状态迁移
都委托给 :class:`ApiService`。默认只提供本地应用对象，监听地址由 CLI 明确绑定到
``127.0.0.1``，不承诺公网认证、多租户或 Webhook。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from dataharness.providers.observability import ObservationContext
from dataharness.storage import RecordNotFoundError

from .errors import ApiError
from .models import (
    ApiErrorBody,
    ApiErrorResponse,
    CreateProjectRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    RetryTaskRequest,
)
from .services import ApiService, build_default_service


def create_app(
    services: ApiService | None = None,
    *,
    static_dir: Path | None = None,
) -> FastAPI:
    """创建可注入服务的 FastAPI 实例，并可选挂载同源 WebUI 构建产物。"""
    service = services or build_default_service()
    app = FastAPI(title="DataHarness", version="0.1.0", docs_url="/docs", redoc_url=None)
    app.state.services = service
    resolved_static_dir = static_dir.resolve() if static_dir is not None else None
    static_index = (
        resolved_static_dir / "index.html"
        if resolved_static_dir is not None and (resolved_static_dir / "index.html").is_file()
        else None
    )

    @app.middleware("http")
    async def serve_spa_navigation(request: Request, call_next):
        """浏览器直接刷新前端路由时返回 SPA shell，同时保留 JSON API 路由优先级。"""
        accepts_html = "text/html" in request.headers.get("accept", "")
        excluded = {"/docs", "/openapi.json", "/healthz", "/readyz", "/diagnostics"}
        if static_index is not None and request.method == "GET" and accepts_html:
            path = request.url.path
            if path not in excluded and not path.startswith("/assets/"):
                # 同一个前端路由 URL 也可能是 JSON API（例如 /projects/{id}）。必须让
                # HTTP 缓存按 Accept 区分两种表示，并禁止缓存 SPA shell；否则刷新导航
                # 缓存的 index.html 会被后续 fetch 当作项目 JSON，造成 HTML 解析错误。
                response = FileResponse(static_index)
                response.headers["Vary"] = "Accept"
                response.headers["Cache-Control"] = "no-store"
                return response
        return await call_next(request)

    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        """只记录状态、耗时和错误分类；请求路径不进入 trace，避免意外携带敏感参数。"""
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as error:
            service.observability.record(
                "http.request",
                ObservationContext(),
                {
                    "status": "ERROR",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error_class": type(error).__name__,
                },
            )
            raise
        service.observability.record(
            "http.request",
            ObservationContext(),
            {
                "status": response.status_code,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ApiErrorResponse(
                error=ApiErrorBody(code=error.code, message=error.message)
            ).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiErrorResponse(
                error=ApiErrorBody(code="INVALID_REQUEST", message="请求参数校验失败")
            ).model_dump(mode="json"),
        )

    @app.exception_handler(RecordNotFoundError)
    async def handle_not_found(_: Request, __: RecordNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ApiErrorResponse(
                error=ApiErrorBody(code="NOT_FOUND", message="请求的资源不存在")
            ).model_dump(mode="json"),
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, __: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ApiErrorResponse(
                error=ApiErrorBody(code="INVALID_OPERATION", message="请求不满足当前状态或边界约束")
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, __: Exception) -> JSONResponse:
        """统一收口未知异常，不把 SQL、路径或第三方 SDK 文本返回给调用方。"""
        return JSONResponse(
            status_code=500,
            content=ApiErrorResponse(
                error=ApiErrorBody(code="INTERNAL_ERROR", message="服务内部错误")
            ).model_dump(mode="json"),
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # SqliteRuntimeStore 构造时已完成迁移；这里不读取底层连接，避免健康检查改写业务状态。
        return {"status": "ready"}

    @app.get("/diagnostics")
    async def diagnostics():
        """提供不含密钥和原始路径清单的本地运行诊断摘要。"""
        return service.diagnostics()

    @app.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: CreateProjectRequest):
        return service.create_project(payload.name).model_dump(mode="json")

    @app.get("/projects")
    async def list_projects():
        return [project.model_dump(mode="json") for project in service.list_projects()]

    @app.get("/projects/{project_id}")
    async def get_project(project_id: str):
        return service.get_project(project_id).model_dump(mode="json")

    @app.post("/projects/{project_id}/archive")
    async def archive_project(project_id: str):
        return service.archive_project(project_id).model_dump(mode="json")

    @app.get("/projects/{project_id}/tasks")
    async def project_tasks(
        project_id: str,
        session_id: Annotated[str | None, Query(min_length=1)] = None,
    ):
        return [
            item.model_dump(mode="json")
            for item in service.list_tasks(project_id, session_id=session_id)
        ]

    @app.post("/projects/{project_id}/snapshots", status_code=status.HTTP_201_CREATED)
    async def create_snapshot(project_id: str):
        return service.create_snapshot(project_id).model_dump(mode="json")

    @app.post("/projects/{project_id}/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(project_id: str, payload: CreateSessionRequest):
        return service.create_session(project_id, payload.label).model_dump(mode="json")

    @app.get("/projects/{project_id}/sessions")
    async def list_sessions(project_id: str):
        return [item.model_dump(mode="json") for item in service.list_sessions(project_id)]

    @app.post("/projects/{project_id}/files", status_code=status.HTTP_201_CREATED)
    async def import_file(
        project_id: str,
        request: Request,
        x_file_name: Annotated[str, Header(min_length=1, max_length=255)],
    ):
        """流式接收文件，先执行大小限制，再把阻塞导入移出事件循环。"""
        max_bytes = service.max_import_bytes
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    raise ApiError(413, "FILE_TOO_LARGE", "文件超过允许的大小上限")
            except ValueError as error:
                raise ApiError(400, "INVALID_CONTENT_LENGTH", "Content-Length 格式无效") from error

        # Body(bytes) 会在进入路由前将整个请求放入内存。这里先有界流式落到短生命周期
        # 临时文件，达到限制立即拒绝，避免超大请求挤占 API/SSE 所在进程的内存。
        descriptor, temporary_name = tempfile.mkstemp(prefix="dataharness-upload-")
        received = 0
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > max_bytes:
                        raise ApiError(413, "FILE_TOO_LARGE", "文件超过允许的大小上限")
                    temporary.write(chunk)
            if received == 0:
                raise ApiError(400, "EMPTY_FILE", "文件内容不能为空")
            # 文件哈希、格式提取和索引都可能较慢；在线程池执行可避免阻塞健康检查、
            # 项目读取和 SSE 推送。临时文件在任务完成前始终由本路由持有。
            version = await run_in_threadpool(
                service.import_file_path, project_id, x_file_name, Path(temporary_name)
            )
            return version.model_dump(mode="json")
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    @app.get("/projects/{project_id}/files")
    async def list_files(project_id: str):
        return [item.model_dump(mode="json") for item in service.list_files(project_id)]

    @app.get("/projects/{project_id}/files/{file_id}/versions")
    async def file_versions(project_id: str, file_id: str):
        return [item.model_dump(mode="json") for item in service.file_versions(project_id, file_id)]

    @app.get("/projects/{project_id}/files/{file_id}/versions/{version_id}/content")
    async def file_content(
        project_id: str,
        file_id: str,
        version_id: str,
        snapshot_id: Annotated[str, Query(min_length=1)],
    ) -> Response:
        data, media_type = service.read_file(project_id, file_id, version_id, snapshot_id)
        return Response(content=data, media_type=media_type)

    @app.get("/projects/{project_id}/search")
    async def search_project(
        project_id: str,
        snapshot_id: Annotated[str, Query(min_length=1)],
        q: Annotated[str, Query(min_length=1, max_length=1000)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return [
            hit.model_dump(mode="json") for hit in service.search(project_id, snapshot_id, q, limit)
        ]

    @app.get("/projects/{project_id}/datasets")
    async def project_datasets(project_id: str):
        values = service.project_resources(project_id, "datasets")
        return [item.model_dump(mode="json") for item in values]

    @app.get("/projects/{project_id}/artifacts")
    async def project_artifacts(project_id: str):
        values = service.project_resources(project_id, "artifacts")
        return [item.model_dump(mode="json") for item in values]

    @app.get("/projects/{project_id}/artifacts/{artifact_id}/content")
    async def artifact_content(project_id: str, artifact_id: str) -> Response:
        data, media_type = service.artifact_content(project_id, artifact_id)
        return Response(content=data, media_type=media_type)

    @app.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(project_id: str, payload: CreateTaskRequest):
        task, run = service.create_task(
            project_id, payload.project_snapshot_id, payload.session_id, payload.prompt
        )
        return {"task": task.model_dump(mode="json"), "run": run.model_dump(mode="json")}

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        return service.get_task(task_id).model_dump(mode="json")

    @app.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        return service.cancel_task(task_id).model_dump(mode="json")

    @app.post("/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        return service.resume_task(task_id).model_dump(mode="json")

    @app.post("/tasks/{task_id}/retry")
    async def retry_task(task_id: str, payload: RetryTaskRequest | None = None):
        task, run = service.retry_task(task_id, payload.project_snapshot_id if payload else None)
        return {"task": task.model_dump(mode="json"), "run": run.model_dump(mode="json")}

    @app.get("/tasks/{task_id}/events")
    async def task_events(task_id: str, after: Annotated[int, Query(ge=0)] = 0):
        return [
            event.model_dump(mode="json") for event in service.task_events(task_id, after_id=after)
        ]

    @app.get("/tasks/{task_id}/events/stream")
    async def task_event_stream(
        task_id: str,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ):
        """以 Runtime 事件 ID 为游标推送可重放 SSE，不把 SSE 当作事实源。"""
        import asyncio
        import json

        cursor = after
        if last_event_id and last_event_id.isdigit():
            cursor = max(cursor, int(last_event_id))

        async def events():
            nonlocal cursor
            # 事件流必须有上界，避免客户端在任务已结束或服务异常时永久占用连接。
            for _ in range(600):
                if await request.is_disconnected():
                    return
                items = service.task_events(task_id, after_id=cursor)
                for item in items:
                    cursor = max(cursor, item.id)
                    yield (
                        f"id: {item.id}\n"
                        f"event: {item.event_type}\n"
                        f"data: {json.dumps(item.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                    ).encode()
                task = service.get_task(task_id)
                if task.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    return
                await asyncio.sleep(0.05)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/tasks/{task_id}/artifacts")
    async def task_artifacts(task_id: str):
        return [
            item.model_dump(mode="json") for item in service.task_resources(task_id, "artifacts")
        ]

    @app.get("/tasks/{task_id}/datasets")
    async def task_datasets(task_id: str):
        return [
            item.model_dump(mode="json") for item in service.task_resources(task_id, "datasets")
        ]

    @app.get("/tasks/{task_id}/findings")
    async def task_findings(task_id: str):
        """返回 Finding 状态和证据引用；原始模型消息不属于 API 回答。"""
        return [item.model_dump(mode="json") for item in service.task_findings(task_id)]

    @app.get("/findings/{finding_id}")
    async def get_finding(finding_id: str):
        return service.get_finding(finding_id).model_dump(mode="json")

    @app.get("/tasks/{task_id}/lineage")
    async def task_lineage(task_id: str):
        return [item.model_dump(mode="json") for item in service.task_lineage(task_id)]

    @app.get("/tasks/{task_id}/answer")
    async def task_answer(task_id: str):
        """返回稳定的最终回答 DTO，包含结论、正式资源、血缘和披露项。"""
        return service.task_answer(task_id).model_dump(mode="json")

    # 挂载必须放在 API 路由注册完成之后：/projects、/tasks、/docs 等接口仍由
    # FastAPI 处理，其他路径才由 StaticFiles 返回 index.html，支持前端路由刷新。
    if static_dir is not None and resolved_static_dir is not None and static_index is not None:
        app.mount("/", StaticFiles(directory=resolved_static_dir, html=True), name="web")

    return app
