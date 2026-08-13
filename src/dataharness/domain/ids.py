"""稳定领域标识符类型。

所有领域对象使用 :data:`typing.NewType` 包装的字符串标识符，既避免把裸字符串
互相混淆，又不暴露数据库行、文件路径或第三方 SDK 类型。ID 的具体生成属于
应用/编排层，领域层只接收显式传入的 ID，便于测试使用固定 ID 保持确定性。
"""

from __future__ import annotations

from typing import NewType

# 项目与文件语料
ProjectId = NewType("ProjectId", str)
FileId = NewType("FileId", str)
FileVersionId = NewType("FileVersionId", str)
SnapshotId = NewType("SnapshotId", str)
CoverageReportId = NewType("CoverageReportId", str)

# 会话与任务生命周期
SessionId = NewType("SessionId", str)
TaskId = NewType("TaskId", str)
RunId = NewType("RunId", str)
StepId = NewType("StepId", str)

# 正式资源与结论
DatasetId = NewType("DatasetId", str)
ArtifactId = NewType("ArtifactId", str)
FindingId = NewType("FindingId", str)
LineageId = NewType("LineageId", str)

# 内容哈希值对象
ContentHash = NewType("ContentHash", str)
