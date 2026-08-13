"""AnalysisRuntime 的稳定错误分类。"""

from __future__ import annotations


class AnalysisError(RuntimeError):
    """所有分析运行时错误的基类。"""


class AnalysisContextError(AnalysisError):
    """请求引用了错误的 Run、Snapshot、Task 或输入版本。"""


class AnalysisBudgetError(AnalysisError):
    """单步预算或运行时资源预算不满足。"""


class AnalysisCircuitOpenError(AnalysisError):
    """相同规范化请求连续失败达到熔断阈值。"""


class AnalysisIdempotencyError(AnalysisError):
    """相同请求已由另一个运行结果持有，不能重复提交不同副作用。"""
