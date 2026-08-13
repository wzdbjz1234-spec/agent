"""ID 与内容哈希值对象测试。"""

from __future__ import annotations

import hashlib

from dataharness.domain import compute_content_hash
from dataharness.domain.ids import ProjectId, TaskId


def test_compute_content_hash_equals_sha256_hex() -> None:
    data = b"hello"
    assert compute_content_hash(data) == hashlib.sha256(data).hexdigest()


def test_compute_content_hash_stable_for_same_bytes() -> None:
    assert compute_content_hash(b"abc") == compute_content_hash(b"abc")


def test_compute_content_hash_differs_for_different_bytes() -> None:
    assert compute_content_hash(b"abc") != compute_content_hash(b"abd")


def test_ids_are_string_based_and_compare_equal() -> None:
    assert ProjectId("p1") == "p1"
    assert TaskId("t1") == "t1"
