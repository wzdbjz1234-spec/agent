"""ProjectCorpus 公共返回值；不泄漏解析器或数据库类型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from dataharness.domain import ContentHash, FileId, FileVersionId, ProjectId


class TextChunk(BaseModel):
    """带来源定位的有界提取片段。"""

    model_config = ConfigDict(frozen=True)

    text: str
    locator: dict[str, Any]
    metadata: dict[str, Any] = {}


class ExtractedDocument(BaseModel):
    """与源哈希和提取器版本绑定的可重建提取物。"""

    model_config = ConfigDict(frozen=True)

    source_hash: ContentHash
    media_type: str
    extractor_version: str
    chunks: tuple[TextChunk, ...]


class SearchHit(BaseModel):
    """RELEVANT 检索结果，始终携带实际文件版本与结构定位。"""

    model_config = ConfigDict(frozen=True)

    project_id: ProjectId
    file_id: FileId
    file_version_id: FileVersionId
    content_hash: ContentHash
    file_name: str
    media_type: str
    text: str
    locator: dict[str, Any]
    score: float


class OpenedResource(BaseModel):
    """有界读取的不可变项目输入；调用方看不到宿主路径。"""

    model_config = ConfigDict(frozen=True)

    file_version_id: FileVersionId
    name: str
    media_type: str
    content_hash: ContentHash
    data: bytes
