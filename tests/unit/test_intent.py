"""轻量提交意图路由测试。"""

from dataharness.agent.intent import PromptIntent, classify_prompt


def test_short_greeting_is_casual() -> None:
    assert classify_prompt("你好！") is PromptIntent.CASUAL


def test_greeting_with_analysis_request_is_not_casual() -> None:
    assert classify_prompt("你好，请分析这个项目") is PromptIntent.ANALYSIS
