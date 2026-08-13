"""凭据与常见 PII 的本地确定性检测器。

本模块只给出 V1 的规则型 best-effort 检测；它不调用云服务，也不宣称能够识别所有
敏感信息。命中位置仅在 Privacy DB 内部用于建立占位符，外部审计只能使用种类和数量。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SecretKind(StrEnum):
    """V1 明确阻断的凭据类别。"""

    PASSWORD = "PASSWORD"
    API_TOKEN = "API_TOKEN"
    PRIVATE_KEY = "PRIVATE_KEY"
    COOKIE = "COOKIE"
    CONNECTION_STRING = "CONNECTION_STRING"


class PIIKind(StrEnum):
    """V1 默认进行可逆占位的 PII 类别。"""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    BANK_CARD = "BANK_CARD"
    NATIONAL_ID = "NATIONAL_ID"


@dataclass(frozen=True, slots=True)
class SensitiveMatch:
    """一条不携带原始值的敏感内容定位结果。"""

    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class CustomPIIRule:
    """用户显式配置的 PII 规则；``kind`` 会成为占位符的类型部分。"""

    kind: str
    pattern: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", self.kind):
            raise ValueError("自定义 PII 类型必须是大写字母、数字或下划线")
        re.compile(self.pattern)


def _disjoint(matches: list[SensitiveMatch]) -> tuple[SensitiveMatch, ...]:
    """按左到右保留最长命中，避免替换时出现重叠和重复占位。"""
    selected: list[SensitiveMatch] = []
    for item in sorted(matches, key=lambda match: (match.start, -(match.end - match.start))):
        if not selected or item.start >= selected[-1].end:
            selected.append(item)
    return tuple(selected)


class SecretDetector:
    """针对明确凭据语法的本地规则检测器。

    规则刻意偏保守：普通业务文本不会因为看似姓名或地址而被阻断；但一旦出现密码、
    令牌、私钥、Cookie 或连接串，整个云端请求必须在 Provider 调用前失败。
    """

    _RULES: tuple[tuple[SecretKind, re.Pattern[str]], ...] = (
        (
            SecretKind.PRIVATE_KEY,
            re.compile(
                r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----[\s\S]*?"
                r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----"
            ),
        ),
        (
            SecretKind.CONNECTION_STRING,
            re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb|redis|amqp)://[^\s'\"]+", re.I),
        ),
        (
            SecretKind.PASSWORD,
            re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*(?:'[^']+'|\"[^\"]+\"|[^\s,;]+)", re.I),
        ),
        (
            SecretKind.API_TOKEN,
            re.compile(
                r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*(?:bearer\s+)?(?:'[^']+'|\"[^\"]+\"|[^\s,;]+)",
                re.I,
            ),
        ),
        (
            SecretKind.COOKIE,
            re.compile(r"\b(?:set-cookie|cookie)\s*:\s*[^\r\n]+", re.I),
        ),
        (
            SecretKind.API_TOKEN,
            re.compile(
                r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
            ),
        ),
    )

    def scan(self, text: str) -> tuple[SensitiveMatch, ...]:
        """返回全部不重叠凭据位置，不向调用方暴露命中的明文。"""
        return _disjoint(
            [
                SensitiveMatch(kind=kind.value, start=match.start(), end=match.end())
                for kind, pattern in self._RULES
                for match in pattern.finditer(text)
            ]
        )


def _luhn_valid(value: str) -> bool:
    """银行卡候选值通过 Luhn 校验后才被占位，降低长数字误报。"""
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        total += digit if index % 2 == 0 else (digit * 2 - 9 if digit > 4 else digit * 2)
    return total % 10 == 0


class PIIDetector:
    """邮箱、电话、银行卡、身份证及显式自定义规则的本地检测器。"""

    _EMAIL = re.compile(
        r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
    )
    _PHONE = re.compile(
        r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)"
        r"|(?<!\d)(?:\+?1[- .]?)?(?:\(?\d{3}\)?[- .]?)\d{3}[- .]?\d{4}(?!\d)"
    )
    _BANK_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
    _NATIONAL_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?![\dA-Za-z])")

    def __init__(self, custom_rules: tuple[CustomPIIRule, ...] = ()) -> None:
        self._custom_rules = tuple((rule.kind, re.compile(rule.pattern)) for rule in custom_rules)

    def scan(self, text: str) -> tuple[SensitiveMatch, ...]:
        """返回不重叠 PII 定位；自定义规则与默认规则使用相同占位流程。"""
        matches = [
            *(
                SensitiveMatch(PIIKind.EMAIL.value, item.start(), item.end())
                for item in self._EMAIL.finditer(text)
            ),
            *(
                SensitiveMatch(PIIKind.PHONE.value, item.start(), item.end())
                for item in self._PHONE.finditer(text)
            ),
            *(
                SensitiveMatch(PIIKind.BANK_CARD.value, item.start(), item.end())
                for item in self._BANK_CARD.finditer(text)
                if _luhn_valid(item.group())
            ),
            *(
                SensitiveMatch(PIIKind.NATIONAL_ID.value, item.start(), item.end())
                for item in self._NATIONAL_ID.finditer(text)
            ),
        ]
        matches.extend(
            SensitiveMatch(kind, item.start(), item.end())
            for kind, pattern in self._custom_rules
            for item in pattern.finditer(text)
        )
        return _disjoint(matches)
