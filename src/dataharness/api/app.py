"""FastAPI 本地控制面。

路由本身不访问 SQLite、OpenSandbox、模型 SDK 或 Workspace 路径；所有事实查询和状态迁移
都委托给 :class:`ApiService`。默认只提供本地应用对象，监听地址由 CLI 明确绑定到
``127.0.0.1``，不承诺公网认证、多租户或 Webhook。
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Body, FastAPI, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dataharness.providers.observability import ObservationContext
from dataharness.storage import RecordNotFoundError

from .errors import ApiError
from .models import (
    ApiErrorBody,
    ApiErrorResponse,
    CreateProjectRequest,
    CreateTaskRequest,
    RetryTaskRequest,
)
from .services import ApiService, build_default_service


def create_app(services: ApiService | None = None) -> FastAPI:
    """创建可注入 fake 应用服务的 FastAPI 实例，便于不启动外部进程验收。"""
    service = services or build_default_service()
    app = FastAPI(title="DataHarness", version="0.1.0", docs_url="/docs", redoc_url=None)
    app.state.services = service

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

    @app.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: CreateProjectRequest):
        return service.create_project(payload.name).model_dump(mode="json")

    @app.get("/projects")
    async def list_projects():
        return [project.model_dump(mode="json") for project in service.list_projects()]

    @app.get("/projects/{project_id}")
    async def get_project(project_id: str):
        return service.get_project(project_id).model_dump(mode="json")

    @app.post("/projects/{project_id}/files", status_code=status.HTTP_201_CREATED)
    async def import_file(
        project_id: str,
        data: Annotated[bytes, Body(media_type="application/octet-stream")],
        x_file_name: Annotated[str, Header(min_length=1, max_length=255)],
    ):
        return service.import_file_bytes(project_id, x_file_name, data).model_dump(mode="json")

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

    @app.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(project_id: str, payload: CreateTaskRequest):
        task, run = service.create_task(project_id, payload.project_snapshot_id, payload.session_id)
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
    async def task_events(task_id: str):
        return [event.model_dump(mode="json") for event in service.task_events(task_id)]

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

    return app
