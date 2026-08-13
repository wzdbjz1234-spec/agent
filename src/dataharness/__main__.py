"""支持 ``python -m dataharness`` 直接启动 CLI。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
