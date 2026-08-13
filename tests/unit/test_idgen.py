"""ID 工厂测试。"""

from __future__ import annotations

from dataharness.idgen import DeterministicIdFactory, UuidIdFactory


def test_uuid_factory_is_prefixed_and_unique() -> None:
    factory = UuidIdFactory()
    first = factory.new("proj")
    second = factory.new("proj")
    assert first.startswith("proj_")
    assert second.startswith("proj_")
    assert first != second


def test_deterministic_factory_is_reproducible() -> None:
    first = DeterministicIdFactory()
    second = DeterministicIdFactory()
    assert [first.new("x") for _ in range(3)] == [second.new("x") for _ in range(3)]


def test_deterministic_factory_is_monotonic() -> None:
    factory = DeterministicIdFactory()
    assert [factory.new() for _ in range(3)] == ["id_000001", "id_000002", "id_000003"]


def test_deterministic_factory_overridable_prefix() -> None:
    factory = DeterministicIdFactory(prefix="task")
    assert factory.new() == "task_000001"
    assert factory.new("run") == "run_000002"
