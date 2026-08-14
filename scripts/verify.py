"""本地统一验证脚本。

依次执行锁文件一致性、格式、lint、类型检查和单元测试，任一失败即非零退出。
用法：``uv run python scripts/verify.py``（在仓库根目录执行）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMMANDS: list[tuple[str, list[str]]] = [
    ("uv lock --check", ["uv", "lock", "--check"]),
    ("release evidence structure", [sys.executable, "scripts/release_check.py"]),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."]),
    ("ruff check", ["uv", "run", "ruff", "check", "."]),
    ("pyright", ["uv", "run", "pyright"]),
    ("pytest", ["uv", "run", "pytest", "-q"]),
]


def main() -> int:
    """执行全部检查并返回退出码（0 表示全部通过）。"""
    failed: list[str] = []
    for name, command in COMMANDS:
        print(f"==> {name}")
        result = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            failed.append(name)
    if failed:
        print(f"\n失败的检查：{', '.join(failed)}", file=sys.stderr)
        return 1
    print("\n全部检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
