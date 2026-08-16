"""最小命令行入口。

V1 阶段只提供本地运维命令，不接入真实模型或公网。``check`` 子命令用于加载并
校验本地配置；``serve`` 子命令只绑定本机回环地址，公网认证、TLS 与多租户不属于 V1。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import __version__
from .config import Settings, load_settings


def _print_settings(settings: Settings) -> None:
    """打印经过校验的配置摘要（不包含任何密钥）。"""
    paths = settings.paths
    print(f"runtime_data_root : {paths.runtime_data_root}")
    print(f"projects_root      : {paths.projects_root}")
    print(f"privacy_root       : {paths.privacy_root}")
    print(f"runtime_db         : {paths.runtime_db}")
    print(f"model              : {settings.model.provider}/{settings.model.model}")
    print(f"api_key_configured : {bool(settings.model.api_key)}")
    print(f"sandbox endpoint   : {settings.sandbox.endpoint}")
    print(f"sandbox runtime    : {settings.sandbox.runtime}")
    print(f"sandbox network    : {settings.sandbox.network_enabled}")
    print(f"max_analysis_steps : {settings.budget.max_analysis_steps}")
    print(f"step_timeout_sec   : {settings.resources.step_timeout_seconds}")


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="dataharness",
        description="DataHarness 本地运维入口（V1 不接入公网）",
    )
    parser.add_argument("--version", action="store_true", help="打印版本号")
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="加载并校验本地配置")
    check.add_argument("--config", type=Path, help="TOML 配置文件路径；缺省使用默认配置")

    serve = subparsers.add_parser("serve", help="启动本地 FastAPI 控制面")
    serve.add_argument("--config", type=Path, help="TOML 配置文件路径；缺省使用默认配置")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址；V1 默认只允许本机")
    serve.add_argument("--port", type=int, default=8000, help="监听端口")

    worker = subparsers.add_parser("worker", help="启动独立本地 Agent Worker")
    worker.add_argument("--config", type=Path, help="TOML 配置文件路径；缺省使用默认配置")
    worker.add_argument("--owner", default=f"worker-{os.getpid()}", help="Worker lease owner")
    worker.add_argument("--health-file", type=Path, help="本机监督器使用的 Worker 心跳 JSON 路径")
    worker.add_argument("--shutdown-file", type=Path, help="创建后停止领取新 Run 的本机 marker")
    worker.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=1.0,
        help="Worker 心跳文件更新周期（秒）",
    )

    run = subparsers.add_parser(
        "run",
        aliases=["start"],
        help="前台启动 OpenSandbox、API 和 Worker；Ctrl+C 优雅关闭全部服务",
    )
    run.add_argument("--config", type=Path, help="受管 TOML 配置；缺省读取 setup marker")
    run.add_argument("--sandbox-config", type=Path, help="OpenSandbox server 配置路径")
    run.add_argument("--sandbox-server-version", help="OpenSandbox server 版本覆盖")
    run.add_argument("--api-port", type=int, default=0, help="API 端口；0 表示沿用 setup marker")
    run.add_argument(
        "--sandbox-port", type=int, default=0, help="Sandbox 端口；0 表示沿用 setup marker"
    )
    run.add_argument(
        "--allow-missing-model-key",
        action="store_true",
        help="允许 Worker 以 WAITING 模式启动，仅用于诊断",
    )

    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.command == "check":
        try:
            settings = load_settings(args.config) if args.config else Settings()
        except Exception as exc:  # noqa: BLE001 — CLI 边界统一映射为错误退出码
            print(f"配置校验失败: {exc}", file=sys.stderr)
            return 1
        _print_settings(settings)
        print("配置校验通过。")
        return 0

    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            print("V1 API 默认只允许绑定本机回环地址。", file=sys.stderr)
            return 1
        try:
            import uvicorn

            from .api import ApiService, create_app

            settings = load_settings(args.config) if args.config else Settings()
            # 发布包把前端构建物放在仓库/发布根的 web/dist；不存在时仍可启动纯 API，
            # 便于开发期使用 Vite 代理，最终用户无需 Node.js 才能访问已构建页面。
            web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
            uvicorn.run(
                create_app(
                    ApiService.from_settings(settings),
                    static_dir=web_dist if web_dist.is_dir() else None,
                ),
                host=args.host,
                port=args.port,
                access_log=False,
            )
        except Exception as exc:  # noqa: BLE001 — CLI 边界统一映射为错误退出码
            print(f"API 启动失败: {type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    if args.command == "worker":
        try:
            from .agent.diagnostics import configure_execution_logging
            from .api import ApiService
            from .worker import WorkerHealthWriter, build_local_worker, run_managed_worker

            configure_execution_logging()
            settings = load_settings(args.config) if args.config else Settings()
            service = ApiService.from_settings(settings)
            if (args.health_file is None) != (args.shutdown_file is None):
                raise ValueError("--health-file 与 --shutdown-file 必须同时提供")
            if args.health_file is not None and args.shutdown_file is not None:
                health = WorkerHealthWriter(args.health_file, os.getpid(), args.owner)

                def on_run_state(run):
                    """把当前 Run 投影成无正文的监督状态。"""
                    if run is not None:
                        health.update(status="RUNNING", run=run)
                    elif not args.shutdown_file.exists():
                        health.update(status="IDLE")

                executor = build_local_worker(
                    settings,
                    service,
                    owner=args.owner,
                    on_run_state=on_run_state,
                )
                asyncio.run(
                    run_managed_worker(
                        executor,
                        health,
                        args.shutdown_file,
                        heartbeat_seconds=args.heartbeat_seconds,
                    )
                )
            else:
                executor = build_local_worker(settings, service, owner=args.owner)
                stop_event = asyncio.Event()
                asyncio.run(executor.run_worker(stop_event))
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 — CLI 边界只暴露稳定类型
            print(f"Worker 启动失败: {type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    if args.command in {"run", "start"}:
        try:
            from .launcher import LaunchOptions, LocalRuntime

            root = Path(__file__).resolve().parents[2]
            options = LaunchOptions.from_workspace(
                root,
                config_path=args.config,
                sandbox_config_path=args.sandbox_config,
                sandbox_server_version=args.sandbox_server_version,
                api_port=args.api_port,
                sandbox_port=args.sandbox_port,
                allow_missing_model_key=args.allow_missing_model_key,
            )
            return LocalRuntime(options).run_forever()
        except Exception as exc:  # noqa: BLE001 — CLI 边界只暴露稳定启动诊断
            print(f"前台运行启动失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0
