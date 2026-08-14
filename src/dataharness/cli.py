"""最小命令行入口。

V1 阶段只提供本地运维命令，不接入真实模型或公网。``check`` 子命令用于加载并
校验本地配置；``serve`` 子命令只绑定本机回环地址，公网认证、TLS 与多租户不属于 V1。
"""

from __future__ import annotations

import argparse
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
    print(f"api_key_env        : {settings.model.api_key_env}")
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
            uvicorn.run(
                create_app(ApiService.from_settings(settings)), host=args.host, port=args.port
            )
        except Exception as exc:  # noqa: BLE001 — CLI 边界统一映射为错误退出码
            print(f"API 启动失败: {type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    parser.print_help()
    return 0
