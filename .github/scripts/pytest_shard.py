"""Deterministic test-level sharding for the quick CI matrix (MOR-4373).

Loaded exactly like ``pytest_worker_diagnostics``: the CI step puts this
directory on ``PYTHONPATH`` and passes ``-p pytest_shard``. The option
``--shard I/N`` (1-based) keeps a collected item iff
``zlib.crc32(nodeid.encode("utf-8")) % N == I - 1`` and reports the rest
through ``pytest_deselected``. The assignment depends only on the node id,
so every shard leg — and every pytest-xdist worker inside a leg, which is
just another pytest process collecting the same node ids — computes the
identical partition, and the union of the legs is exactly the unsharded
run. The spec is parsed once in ``pytest_configure`` so a bad value fails
with a clean usage error before any test runs.

The hook is ``trylast``: it runs after pytest's own ``-m``/``-k``
deselection, so ``--shard`` composes with marker selection instead of
resurrecting deselected tests. Without ``--shard`` nothing here changes any
behaviour. There is no report file and no aggregator: the partition is by
construction (a pure function of the node id) and is unit-tested in
``tests/test_pytest_shard.py``.
"""

from __future__ import annotations

import zlib

import pytest

_SHARD: pytest.StashKey[tuple[int, int]] = pytest.StashKey()


def parse_shard_spec(spec: str) -> tuple[int, int]:
    """Parse ``I/N`` into ``(I, N)`` with ``1 <= I <= N``.

    Raises :class:`pytest.UsageError` for anything else, so a misconfigured
    matrix leg fails its own run instead of silently running a wrong slice.
    """
    index_text, separator, total_text = spec.partition("/")
    if not separator:
        raise pytest.UsageError(
            f"--shard must be I/N with 1 <= I <= N, got {spec!r}"
        ) from None
    try:
        index, total = int(index_text), int(total_text)
    except ValueError as exc:
        raise pytest.UsageError(
            f"--shard must be I/N with 1 <= I <= N, got {spec!r}"
        ) from exc
    if total < 1 or not 1 <= index <= total:
        raise pytest.UsageError(
            f"--shard must be I/N with 1 <= I <= N, got {spec!r}"
        ) from None
    return index, total


def shard_assignment(nodeid: str, total: int) -> int:
    """The 1-based shard a node id belongs to (stable across runs and hosts)."""
    return zlib.crc32(nodeid.encode("utf-8")) % total + 1


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("shard")
    group.addoption(
        "--shard",
        action="store",
        default=None,
        metavar="I/N",
        help="Run only the tests of shard I of N (1-based, crc32 of the node id).",
    )


def pytest_configure(config: pytest.Config) -> None:
    spec = config.getoption("shard")
    if spec is not None:
        config.stash[_SHARD] = parse_shard_spec(spec)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    shard = config.stash.get(_SHARD, None)
    if shard is None:
        return
    index, total = shard
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        (kept if shard_assignment(item.nodeid, total) == index else dropped).append(
            item
        )
    items[:] = kept
    if dropped:
        config.hook.pytest_deselected(items=dropped)
