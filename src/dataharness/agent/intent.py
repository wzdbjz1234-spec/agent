"""Agent 入口的轻量意图路由。

纯问候和简单确认不需要读取 ProjectSnapshot、创建 OpenSandbox 或调用模型工具。
这个模块只做确定性的窄规则判断；任何不在白名单中的文本都继续走完整分析链路，
避免把用户真正的数据问题误判成闲聊。
"""

from __future__ import annotations

import re
from enum import StrEnum


class PromptIntent(StrEnum):
    """提交文本的最小执行意图。"""

    CASUAL = "CASUAL"
    ANALYSIS = "ANALYSIS"


_PUNCTUATION_RE = re.compile(r"[\s\u3000，。！？、,.!?；;：:~～]+")
_CASUAL_PROMPTS = frozenset(
    {
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "hey",
        "早上好",
        "下午好",
        "晚上好",
        "谢谢",
        "感谢",
        "thanks",
        "thankyou",
    }
)


def classify_prompt(prompt: str) -> PromptIntent:
    """把明显的短问候路由到本地回复，其余文本保持分析语义。

    规则会先去除空白和常见标点并小写化英文；只有完整命中白名单才判定为
    ``CASUAL``，例如“你好，帮我分析数据”不会命中，仍然进入分析 Agent。
    """

    normalized = _PUNCTUATION_RE.sub("", prompt.strip().casefold())
    return PromptIntent.CASUAL if normalized in _CASUAL_PROMPTS else PromptIntent.ANALYSIS


def casual_reply(prompt: str) -> str:
    """返回不依赖模型和 Sandbox 的本地问候回复。"""

    del prompt  # 只按白名单意图返回固定文本，不把用户原文重复写入回答。
    return (
        "你好！我是 DataHarness，可以帮你分析当前项目中的文件、数据质量和趋势。"
        "请直接描述想分析的目标。"
    )
