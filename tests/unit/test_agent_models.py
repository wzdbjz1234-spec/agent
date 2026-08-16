"""Agent 结构化输出协议的兼容性测试。"""

from __future__ import annotations

from dataharness.agent.models import AgentFinalOutput


def test_waiting_summary_is_normalized_to_user_visible_answer() -> None:
    """模型把 WAITING 说明命名为 summary 时仍保持统一 answer 协议。"""
    output = AgentFinalOutput.model_validate(
        {
            "status": "WAITING",
            "summary": "需要补充数据范围",
            "unresolved_issues": ["缺少时间范围"],
        }
    )

    assert output.answer == "需要补充数据范围"
    assert output.status == "WAITING"


def test_completed_output_still_requires_answer() -> None:
    """兼容分支只处理 summary，不降低 COMPLETED 的必填字段约束。"""
    try:
        AgentFinalOutput.model_validate({"status": "COMPLETED", "summary": "不应作为 answer"})
    except ValueError:
        return
    raise AssertionError("COMPLETED 缺少 answer 时不应通过校验")
