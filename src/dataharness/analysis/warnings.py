"""分析结果的轻量数据质量告警。

这些告警只描述可观察的数据形态异常，不替代统计检验，也不直接把结论判为错误。
Host VerificationService 会把告警保留在验证结果中，并按策略把 Finding 晋级为 WARNING。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DataWarningKind(StrEnum):
    """V1 需要统一表达的五类数据质量告警。"""

    ROW_COUNT_ANOMALY = "ROW_COUNT_ANOMALY"
    JOIN_EXPLOSION = "JOIN_EXPLOSION"
    MISSING_VALUES = "MISSING_VALUES"
    TYPE_CONVERSION = "TYPE_CONVERSION"
    DUPLICATE_VALUES = "DUPLICATE_VALUES"


class DataWarning(BaseModel):
    """不包含原始数据的结构化告警；details 只允许元数据和数值。"""

    model_config = ConfigDict(frozen=True)

    kind: DataWarningKind
    message: str
    metrics: dict[str, int | float]


class DataWarningDetector:
    """根据 Sandbox 返回的统计元数据生成有界告警。

    Sandbox 只返回数值统计，检测器不接触 stdout、原始行或列值，因此告警路径不会
    绕过隐私出口。不同 runner 可以提供不同的统计键；未知键会被安全忽略。
    """

    @staticmethod
    def detect(statistics: Mapping[str, int | float]) -> tuple[DataWarning, ...]:
        warnings: list[DataWarning] = []

        rows = statistics.get("rows")
        expected_rows = statistics.get("expected_rows")
        if (
            isinstance(rows, (int, float))
            and isinstance(expected_rows, (int, float))
            and expected_rows > 0
            and (rows < expected_rows * 0.5 or rows > expected_rows * 2)
        ):
            warnings.append(
                DataWarning(
                    kind=DataWarningKind.ROW_COUNT_ANOMALY,
                    message="结果行数相对预期发生显著变化",
                    metrics={"rows": rows, "expected_rows": expected_rows},
                )
            )

        input_rows = statistics.get("input_rows")
        join_rows = statistics.get("join_rows")
        if (
            isinstance(input_rows, (int, float))
            and isinstance(join_rows, (int, float))
            and input_rows > 0
            and join_rows > input_rows * 10
        ):
            warnings.append(
                DataWarning(
                    kind=DataWarningKind.JOIN_EXPLOSION,
                    message="Join 输出行数超过输入规模十倍",
                    metrics={"input_rows": input_rows, "join_rows": join_rows},
                )
            )

        missing = _first_number(statistics, "missing_values", "null_values", "null_count")
        if missing is not None and missing > 0:
            warnings.append(
                DataWarning(
                    kind=DataWarningKind.MISSING_VALUES,
                    message="结果中存在缺失值",
                    metrics={"missing_values": missing},
                )
            )

        conversions = _first_number(
            statistics, "type_conversion_failures", "conversion_failures", "type_conversions"
        )
        if conversions is not None and conversions > 0:
            warnings.append(
                DataWarning(
                    kind=DataWarningKind.TYPE_CONVERSION,
                    message="存在类型转换异常或失败",
                    metrics={"conversion_failures": conversions},
                )
            )

        duplicates = _first_number(statistics, "duplicate_values", "duplicate_rows", "duplicates")
        if duplicates is not None and duplicates > 0:
            warnings.append(
                DataWarning(
                    kind=DataWarningKind.DUPLICATE_VALUES,
                    message="结果中存在重复值或重复行",
                    metrics={"duplicates": duplicates},
                )
            )
        return tuple(warnings)


def _first_number(values: Mapping[str, int | float], *keys: str) -> int | float | None:
    """从 runner 的兼容键中取第一个数值，避免把未知结构当成业务事实。"""
    for key in keys:
        value = values.get(key)
        if isinstance(value, (int, float)):
            return value
    return None
