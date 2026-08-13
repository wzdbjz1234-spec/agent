"""内容哈希值对象。

SHA-256 十六进制摘要用于内容寻址：同一内容始终得到同一哈希，任何字节变化
都会改变哈希。它是 EvidenceGate、IntegrityGate 以及幂等键的事实基础。
"""

from __future__ import annotations

import hashlib

from .ids import ContentHash


def compute_content_hash(data: bytes) -> ContentHash:
    """计算字节内容的 SHA-256 十六进制摘要。"""
    return ContentHash(hashlib.sha256(data).hexdigest())
