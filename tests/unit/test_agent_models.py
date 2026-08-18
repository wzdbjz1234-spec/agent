"""自然语言 Agent 运行结果测试。"""

from dataharness.agent.models import AgentTextOutput


def test_agent_output_is_plain_text() -> None:
    output = AgentTextOutput("需要补充数据范围")

    assert output.text == "需要补充数据范围"
    # 迁移期读取属性不会改变模型的自然文本协议。
    assert output.answer == output.text
    assert output.status == "COMPLETED"
    assert output.references == ()
