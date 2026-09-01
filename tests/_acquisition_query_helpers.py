"""Strict acquisition-query helpers shared by scheduler-facing tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rigplane.core.acquisition_scheduler import (
    AcquisitionQuery,
    IcomCivAcquisitionExecutor,
)

if TYPE_CHECKING:
    from rigplane.web.radio_poller import RadioPoller


AcquisitionQueryCase = AcquisitionQuery


_SELECTOR_COMMANDS = frozenset((0x25, 0x26))


def _require_byte(name: str, value: int | None, *, optional: bool = True) -> None:
    if value is None:
        if optional:
            return
        raise TypeError(f"{name} must be int")
    if type(value) is not int:
        suffix = " or None" if optional else ""
        raise TypeError(f"{name} must be int{suffix}")
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must fit in one byte")


def _require_query(query: AcquisitionQueryCase) -> AcquisitionQueryCase:
    if type(query) is not AcquisitionQueryCase:
        raise TypeError("acquisition query must be the exact query dataclass")
    return query


def acquisition_query(
    command: int,
    *,
    sub: int | None = None,
    data: bytes = b"",
    receiver: int | None = None,
    selector: int | None = None,
) -> AcquisitionQueryCase:
    """Build one explicit expected query without normalizing its fields."""
    _require_byte("command", command, optional=False)
    _require_byte("sub", sub)
    _require_byte("receiver", receiver)
    _require_byte("selector", selector)
    if type(data) is not bytes:
        raise TypeError("data must be exact bytes")
    if selector is not None:
        if (
            command not in _SELECTOR_COMMANDS
            or sub is not None
            or data
            or receiver is not None
        ):
            raise ValueError("selector is exclusive to 0x25/0x26 query data")
        data = bytes([selector])
    elif command in _SELECTOR_COMMANDS and (
        sub is not None or receiver is not None or len(data) != 1
    ):
        raise ValueError("0x25/0x26 require exactly one selector data byte")
    return AcquisitionQueryCase(
        command=command,
        sub=sub,
        data=data,
        receiver=receiver,
    )


def civ_frame_parts(query: AcquisitionQueryCase) -> AcquisitionQueryCase:
    """Return the already-semantic fields of an exact query dataclass."""
    return _require_query(query)


def query_command(query: AcquisitionQueryCase) -> int:
    return _require_query(query).command


def query_receiver(query: AcquisitionQueryCase) -> int | None:
    return _require_query(query).receiver


def query_selector(query: AcquisitionQueryCase) -> int | None:
    query = _require_query(query)
    if query.command not in _SELECTOR_COMMANDS or len(query.data) != 1:
        return None
    return query.data[0]


def recording_executor(
    *,
    supports_cmd29: Callable[[int, int | None], bool] | None = None,
) -> tuple[IcomCivAcquisitionExecutor, list[AcquisitionQueryCase]]:
    """Create an executor whose exact one-object sender records queries."""
    sent: list[AcquisitionQueryCase] = []

    async def send_query(query: AcquisitionQueryCase) -> None:
        sent.append(_require_query(query))

    return (
        IcomCivAcquisitionExecutor(send_query, supports_cmd29=supports_cmd29),
        sent,
    )


async def send_state_query(
    poller: RadioPoller,
    query: AcquisitionQueryCase,
) -> None:
    """Send one exact query through the poller's one-object interface."""
    await poller._send_one_state_query(_require_query(query))  # noqa: SLF001


def assert_acquisition_query_representation_contract() -> None:
    """Pin the lossless dataclass representation used by all test helpers."""
    assert acquisition_query(0x18) == AcquisitionQueryCase(command=0x18)
    assert acquisition_query(0x16, sub=0x59) == AcquisitionQueryCase(
        command=0x16,
        sub=0x59,
    )
    assert acquisition_query(0x1A, sub=0x05, data=b"\x01\x91") == (
        AcquisitionQueryCase(command=0x1A, sub=0x05, data=b"\x01\x91")
    )
    assert acquisition_query(0x07, data=b"\xc2") == AcquisitionQueryCase(
        command=0x07,
        data=b"\xc2",
    )
    assert acquisition_query(0x25, selector=1) == AcquisitionQueryCase(
        command=0x25,
        data=b"\x01",
    )
    assert acquisition_query(
        0x1A,
        sub=0x05,
        data=b"\x01\x91",
        receiver=0,
    ) == AcquisitionQueryCase(
        command=0x1A,
        sub=0x05,
        data=b"\x01\x91",
        receiver=0,
    )

    rejected = (
        lambda: acquisition_query(None),  # type: ignore[arg-type]
        lambda: acquisition_query(0x25, sub=1),
        lambda: acquisition_query(0x25, receiver=1),
        lambda: acquisition_query(0x25, data=b"\x01", selector=1),
        lambda: acquisition_query(0x16, selector=1),
        lambda: civ_frame_parts((0x18, None, None)),  # type: ignore[arg-type]
    )
    for invalid in rejected:
        try:
            invalid()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("helper accepted an invalid acquisition query")
