"""DataHarness V1 发布物自检。

该脚本只检查可从仓库和本地构建证据目录复核的事实，不替代 Docker、SBOM 或漏洞扫描
工具。使用 ``--require-image`` 时，缺失任一真实镜像证据会以非零状态退出。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_ROOT = REPO_ROOT / "sandbox-images" / "secure-analysis" / "build-evidence"
REQUIRED_FILES = (
    "AGENT.md",
    "ARCHITECTURE.md",
    "README.md",
    "dataharness.example.toml",
    "NOTICE",
    "pyproject.toml",
    "uv.lock",
    "doc/V1_OPERATIONS.md",
    "sandbox-images/secure-analysis/requirements.lock",
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _check_required_files() -> list[str]:
    """检查清单中的源码、锁文件、配置样例和运维文档。"""
    return [name for name in REQUIRED_FILES if not (REPO_ROOT / name).is_file()]


def _check_image_evidence() -> list[str]:
    """校验真实构建证据的最小结构，不读取或伪造扫描结论。"""
    failures: list[str] = []
    digest_path = EVIDENCE_ROOT / "image-digest.txt"
    if not digest_path.is_file():
        return ["缺少 secure-analysis/build-evidence/image-digest.txt"]
    digest = digest_path.read_text(encoding="utf-8").strip()
    if not _DIGEST.fullmatch(digest):
        failures.append("镜像 digest 不是 sha256:<64 位小写十六进制>")
    sbom_path = EVIDENCE_ROOT / "sbom.spdx.json"
    if not sbom_path.is_file():
        failures.append("缺少 SBOM 证据 sbom.spdx.json")
    else:
        try:
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failures.append("SBOM 不是有效 JSON")
        else:
            # Docker Scout 导出的 JSON 使用 artifacts/descriptor，而标准 SPDX
            # 文档使用 packages/spdxVersion；两种格式都必须包含实际清单内容。
            is_spdx = sbom.get("spdxVersion") is not None and bool(sbom.get("packages"))
            is_docker_scout = bool(sbom.get("source")) and bool(sbom.get("artifacts"))
            if not (is_spdx or is_docker_scout):
                failures.append("SBOM 缺少标准 SPDX packages 或 Docker Scout artifacts")
    scan_path = EVIDENCE_ROOT / "vuln-scan.json"
    if not scan_path.is_file():
        failures.append("缺少漏洞扫描证据 vuln-scan.json")
    else:
        try:
            scan = json.loads(scan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failures.append("漏洞扫描证据不是有效 JSON")
        else:
            if not scan.get("scanner") or "findings" not in scan:
                failures.append("漏洞扫描证据缺少 scanner 或 findings")
    return failures


def main(argv: list[str] | None = None) -> int:
    """执行发布前自检；返回 0 表示所有要求已满足。"""
    parser = argparse.ArgumentParser(description="DataHarness V1 release evidence check")
    parser.add_argument(
        "--require-image",
        action="store_true",
        help="同时要求本地 secure-analysis 镜像 digest、SBOM 和漏洞扫描证据",
    )
    args = parser.parse_args(argv)

    failures = _check_required_files()
    if args.require_image:
        failures.extend(_check_image_evidence())
    if failures:
        print("发布自检失败：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("发布自检通过。")
    if not args.require_image:
        print("提示：使用 --require-image 检查本地镜像、SBOM 和漏洞扫描证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
