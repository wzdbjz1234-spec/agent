"""本地 Skill 注册与完整性校验错误。"""

from __future__ import annotations


class SkillError(RuntimeError):
    """Skill 边界上的基础错误。"""


class SkillNotFoundError(SkillError):
    """请求的 Skill 或脚本不存在。"""


class UnsafeSkillError(SkillError):
    """Skill 目录、文件或名称违反受控边界。"""


class SkillChangedError(SkillError):
    """已激活 Skill 在一次 Run 内发生变化。"""
