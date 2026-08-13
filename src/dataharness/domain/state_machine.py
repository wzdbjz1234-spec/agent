"""状态机校验原语。

领域对象的状态迁移统一通过迁移表驱动：每个聚合定义一张
``dict[当前状态, frozenset[允许的下一状态]]`` 迁移表，迁移方法在变更前调用
:func:`check_transition` 校验，非法迁移抛出稳定的
:class:`IllegalStateTransitionError`。这样所有合法/非法边都可以从同一张表推导，
并被表驱动测试与 Hypothesis 性质测试覆盖。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from .errors import IllegalStateTransitionError

# 迁移表：当前状态 -> 允许的下一状态集合
type TransitionTable[S: Enum] = Mapping[S, frozenset[S]]


def check_transition[S: Enum](
    table: TransitionTable[S],
    current: S,
    target: S,
    subject: str,
) -> None:
    """校验 ``current -> target`` 是否为合法迁移，非法时抛出领域错误。

    Args:
        table: 该聚合的迁移表。
        current: 当前状态。
        target: 目标状态。
        subject: 迁移主体名称，用于错误信息。

    Raises:
        IllegalStateTransitionError: 当 target 不在 current 的允许集合中时。
    """
    allowed = table.get(current, frozenset())
    if target not in allowed:
        pretty = ", ".join(str(s) for s in sorted(allowed, key=str))
        raise IllegalStateTransitionError(
            f"{subject} 不允许从 {current} 迁移到 {target}；"
            f"合法迁移：{pretty if pretty else '无（终态）'}"
        )
