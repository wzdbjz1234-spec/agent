"""Host 侧受控 Vega-Lite ChartArtifact 校验。

前端只会接收通过这里的声明式规范。校验器不执行表达式、不加载 URL、不读取裸路径，
并要求图表显式绑定一个已发布 Dataset 的稳定 ID 与内容 hash。
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from html import escape
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataharness.domain import (
    ArtifactId,
    ContentHash,
    DatasetId,
    ProjectId,
    RunId,
    compute_content_hash,
)


class ChartSpecError(ValueError):
    """图表规范不满足安全或数据绑定约束。"""


class ChartArtifact(BaseModel):
    """已绑定 Dataset 的声明式图表产物元数据。"""

    model_config = ConfigDict(frozen=True)

    id: ArtifactId
    project_id: ProjectId
    run_id: RunId
    dataset_id: DatasetId
    dataset_hash: ContentHash
    spec: dict[str, Any] = Field(min_length=1)
    content_hash: ContentHash

    @model_validator(mode="after")
    def _bounded_spec(self) -> ChartArtifact:
        validate_vega_lite_spec(self.spec, self.dataset_id, self.dataset_hash)
        if self.content_hash != chart_content_hash(self.spec):
            raise ChartSpecError("图表规范 hash 已漂移")
        return self


_URL = re.compile(r"(?:https?|file|javascript|data):", re.IGNORECASE)
_FORBIDDEN_KEYS = frozenset(
    {"url", "href", "html", "iframe", "javascript", "signal", "signals", "expr", "expression"}
)
_ALLOWED_TRANSFORMS = frozenset({"aggregate", "bin", "filter", "fold", "flatten", "timeUnit"})


def _walk_security(value: Any, path: str = "spec") -> None:
    """递归拒绝 URL、脚本和 HTML；键名检查比只检查字符串更稳健。"""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_KEYS:
                raise ChartSpecError(f"图表字段 {path}.{key} 被禁止")
            _walk_security(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_security(item, f"{path}[{index}]")
    elif isinstance(value, str) and _URL.search(value):
        raise ChartSpecError("图表规范不得包含外部 URL、脚本或 data URI")


def validate_vega_lite_spec(
    spec: dict[str, Any],
    dataset_id: DatasetId | str,
    dataset_hash: ContentHash | str,
    *,
    max_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """校验并返回可安全交给前端的 Vega-Lite JSON 规范。"""
    if not isinstance(spec, dict) or not spec:
        raise ChartSpecError("图表规范必须是非空 JSON 对象")
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > max_bytes:
        raise ChartSpecError("图表规范超过大小上限")
    _walk_security(spec)
    data = spec.get("data")
    if not isinstance(data, dict):
        raise ChartSpecError("图表必须使用受控 Dataset 引用")
    if data.get("dataset_id") != str(dataset_id) or data.get("content_hash") != str(dataset_hash):
        raise ChartSpecError("图表 Dataset ID/hash 与已发布资源不一致")
    if "values" in data or "url" in data:
        raise ChartSpecError("图表不得内嵌或外链未登记数据")
    transforms = spec.get("transform", [])
    if not isinstance(transforms, list):
        raise ChartSpecError("图表 transform 必须是数组")
    for transform in transforms:
        if not isinstance(transform, dict) or not set(transform).issubset(_ALLOWED_TRANSFORMS):
            raise ChartSpecError("图表包含不受控的数据变换")
    return spec


def chart_content_hash(spec: dict[str, Any]) -> ContentHash:
    """按规范化 JSON 计算图表内容 hash，供发布前后做漂移检测。"""
    normalized = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return compute_content_hash(normalized.encode("utf-8"))


def build_svg_fallback(title: str, *, width: int = 800, height: int = 450) -> bytes:
    """生成不含脚本、外链或用户 HTML 的静态 SVG 兜底画布。"""
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ChartSpecError("静态图表尺寸超出上限")
    safe_title = escape(title[:200], quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#fff"/>'
        f'<text x="24" y="48" font-family="sans-serif" font-size="22">{safe_title}</text>'
        '<text x="24" y="82" font-family="sans-serif" font-size="14" fill="#666">'
        "Vega-Lite 静态兜底预览</text></svg>"
    ).encode()


def build_png_fallback(*, width: int = 800, height: int = 450) -> bytes:
    """生成纯 Host 的白色 PNG 兜底，避免失败时把 HTML/脚本交给前端。"""
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ChartSpecError("静态图表尺寸超出上限")
    # PNG 每行以过滤器字节开头；固定像素使该 fallback 可重复且不执行外部内容。
    row = b"\x00" + b"\xff\xff\xff" * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
