#!/usr/bin/env python
"""从 SBOM 查询 OSV 漏洞库，生成可审计的漏洞扫描证据。

用法：
    python scan_vulns.py <sbom.json> <output.json>

- sbom.json：docker scout sbom 输出的 JSON（artifacts 数组，含 purl）。
- 输出：包含每个受影响包的漏洞列表、汇总与扫描元数据的 JSON。
- 仅使用 OSV 官方 API（grype/trivy 同源数据），不依赖容器扫描镜像。

退出码：0 = 扫描完成（无论是否有漏洞）；非 0 = 扫描本身失败。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter
from datetime import UTC, datetime

_ECOSYSTEM_BY_PURL = {
    "pypi": "PyPI",
    "deb": "Debian",
    "apk": "Alpine",
    "golang": "Go",
    "npm": "npm",
    "gem": "RubyGems",
    "cargo": "crates.io",
    "maven": "Maven",
    "nuget": "NuGet",
    "composer": "Packagist",
    "swift": "SwiftURL",
    "rpm": "Rocky Linux",
}

_OSV_URL = "https://api.osv.dev/v1/querybatch"


def _parse_purl(purl: str) -> dict[str, str] | None:
    """极简 purl 解析：pkg:TYPE/NAMESPACE/NAME@VERSION?qualifiers。"""
    if not purl.startswith("pkg:"):
        return None
    body = purl[4:]
    qualifiers: dict[str, str] = {}
    if "?" in body:
        body, _, raw_q = body.partition("?")
        for item in raw_q.split("&"):
            if "=" in item:
                key, _, value = item.partition("=")
                qualifiers[key] = value
    version = None
    if "@" in body:
        body, _, version = body.rpartition("@")
    parts = [part for part in body.split("/") if part]
    if not parts:
        return None
    package_type = parts[0]
    name = parts[-1]
    namespace = "/".join(parts[1:-1]) if len(parts) > 2 else None
    from urllib.parse import unquote

    return {
        "type": package_type,
        "namespace": unquote(namespace) if namespace else None,
        "name": unquote(name),
        "version": unquote(version) if version else None,
        "qualifiers": qualifiers,
    }


def _osv_package(parsed: dict[str, str]) -> dict[str, str] | None:
    """把 purl 映射为 OSV package；无法映射的返回 None。"""
    ecosystem = _ECOSYSTEM_BY_PURL.get(parsed["type"])
    if ecosystem is None or not parsed["version"]:
        return None
    name = parsed["name"]
    if parsed["namespace"]:
        name = f"{parsed['namespace']}/{name}"
    if ecosystem == "Debian":
        os_version = parsed.get("qualifiers", {}).get("os_version", "")
        if os_version:
            return {"ecosystem": f"Debian:{os_version}", "name": name}
        return {"ecosystem": "Debian", "name": name}
    return {"ecosystem": ecosystem, "name": name}


def _query_osv(queries: list[dict[str, str]]) -> list[list[dict[str, object]]]:
    """批量查询 OSV；网络或服务失败直接抛错（扫描失败不等于无漏洞）。"""
    payload = json.dumps({"queries": [{"package": q} for q in queries]}).encode("utf-8")
    request = urllib.request.Request(
        _OSV_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)
    results = data.get("results", [])
    if len(results) != len(queries):
        raise RuntimeError(f"OSV 返回 {len(results)} 条结果，期望 {len(queries)} 条")
    return [result.get("vulns", []) for result in results]


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <sbom.json> <output.json>", file=sys.stderr)
        return 2
    sbom_path, output_path = sys.argv[1], sys.argv[2]
    with open(sbom_path, encoding="utf-8") as handle:
        sbom = json.load(handle)
    artifacts = sbom.get("artifacts", [])
    if not artifacts:
        raise SystemExit("SBOM 中没有 artifacts，无法扫描")

    queries: list[dict[str, str]] = []
    packages: list[dict[str, str]] = []
    skipped: list[str] = []
    for artifact in artifacts:
        purl = artifact.get("purl") or ""
        parsed = _parse_purl(purl)
        if parsed is None:
            skipped.append(artifact.get("name", purl))
            continue
        package = _osv_package(parsed)
        if package is None:
            skipped.append(purl)
            continue
        queries.append(package)
        packages.append({"artifact": artifact.get("name", ""), "purl": purl, "osv": package})

    findings: list[dict[str, object]] = []
    batch_size = 500
    for offset in range(0, len(queries), batch_size):
        batch = queries[offset : offset + batch_size]
        results = _query_osv(batch)
        for package, vulns in zip(packages[offset : offset + batch_size], results, strict=True):
            if vulns:
                findings.append(
                    {
                        "package": package,
                        "vulnerabilities": [
                            {
                                "id": vuln.get("id"),
                                "summary": vuln.get("summary") or vuln.get("details", "")[:200],
                                "aliases": vuln.get("aliases", []),
                                "severity": [
                                    {"type": s.get("type"), "score": s.get("score")}
                                    for s in vuln.get("severity", [])
                                ],
                            }
                            for vuln in vulns
                        ],
                    }
                )

    by_ecosystem: Counter[str] = Counter()
    vuln_ids: list[str] = []
    for finding in findings:
        ecosystem = finding["package"]["osv"]["ecosystem"]
        by_ecosystem[ecosystem] += len(finding["vulnerabilities"])
        vuln_ids.extend(v["id"] for v in finding["vulnerabilities"])

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_sbom": sbom_path,
        "scanner": "scan_vulns.py + OSV API (https://api.osv.dev/v1/querybatch)",
        "packages_queried": len(queries),
        "packages_unmapped": len(skipped),
        "affected_packages": len(findings),
        "vulnerabilities_total": sum(len(f["vulnerabilities"]) for f in findings),
        "vulnerabilities_by_ecosystem": dict(by_ecosystem),
        "vulnerability_ids": sorted(vuln_ids),
        "findings": findings,
        "skipped_purls": skipped,
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(
        f"扫描完成：{len(queries)} 个包（{len(skipped)} 个无法映射），"
        f"{len(findings)} 个受影响包，共 {len(vuln_ids)} 个漏洞。"
    )
    if vuln_ids:
        print("漏洞 ID：", ", ".join(sorted(vuln_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
