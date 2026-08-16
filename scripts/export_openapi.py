"""导出 FastAPI OpenAPI 路由契约，供 WebUI 构建期做接口漂移检查。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from dataharness.api import ApiService, create_app
from dataharness.config import Settings


def main() -> int:
    """在临时 Runtime 目录装配 API，仅输出 schema，不修改正式运行数据。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dataharness-openapi-") as directory:
        settings = Settings.model_validate({"paths": {"runtime_data_root": directory}})
        app = create_app(ApiService.from_settings(settings))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"已导出 OpenAPI：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
