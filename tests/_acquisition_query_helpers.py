"""Current-shape-only acquisition query helpers for tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rigplane.core.acquisition_scheduler import IcomCivAcquisitionExecutor

if TYPE_CHECKING:
    from rigplane.web.radio_poller import RadioPoller


AcquisitionQueryCase = tuple[int, int | bytes | None, int | None]


@dataclass(frozen=True)
class CivFrameParts:
    """Representation-neutral meaning of one current acquisition query."""

    command: int
    sub: int | None = None
    data: bytes = b""
    receiver: int | None = None
    selector: int | None = None


def _require_legacy_query(query: AcquisitionQueryCase) -> AcquisitionQueryCase:
    if type(query) is not tuple or len(query) != 3:
        raise TypeError("acquisition query must be an exact legacy three-tuple")
    command, packed_sub, route = query
    if type(command) is not int:
        raise TypeError("legacy query command must be int")
    if packed_sub is not None and type(packed_sub) not in (int, bytes):
        raise TypeError("legacy query packed sub must be int, bytes, or None")
    if route is not None and type(route) is not int:
        raise TypeError("legacy query route must be int or None")
    return query


def acquisition_query(
    command: int,
    *,
    sub: int | None = None,
    data: bytes = b"",
    receiver: int | None = None,
    selector: int | None = None,
) -> AcquisitionQueryCase:
    """Build the legacy tuple while keeping test intent explicit."""
    if selector is not None:
        if (
            command not in (0x25, 0x26)
            or sub is not None
            or data
            or receiver is not None
        ):
            raise ValueError("selector is exclusive to 0x25/0x26 queries")
        return command, None, selector
    if data:
        if sub is None:
            if len(data) != 1:
                raise ValueError("legacy data-only queries require exactly one byte")
            return command, data[0], receiver
        return command, bytes([sub]) + data, receiver
    return command, sub, receiver


def civ_frame_parts(
    query: AcquisitionQueryCase,
) -> CivFrameParts:
    """Expose semantic CI-V frame parts from the legacy tuple."""
    command, packed_sub, route = _require_legacy_query(query)
    if command in (0x25, 0x26):
        if packed_sub is None and route is not None:
            selector = route
        elif type(packed_sub) is int and route is None:
            selector = packed_sub
        else:
            raise ValueError("0x25/0x26 queries require exactly one selector")
        return CivFrameParts(
            command=command,
            data=bytes([selector]),
            selector=selector,
        )
    if isinstance(packed_sub, bytes):
        if not packed_sub:
            raise ValueError("packed legacy sub must not be empty")
        return CivFrameParts(
            command=command,
            sub=packed_sub[0],
            data=packed_sub[1:],
            receiver=route,
        )
    if command == 0x07 and packed_sub is not None:
        return CivFrameParts(command=command, data=bytes([packed_sub]), receiver=route)
    return CivFrameParts(command=command, sub=packed_sub, receiver=route)


def query_command(query: AcquisitionQueryCase) -> int:
    return civ_frame_parts(query).command


def query_receiver(query: AcquisitionQueryCase) -> int | None:
    return civ_frame_parts(query).receiver


def query_selector(query: AcquisitionQueryCase) -> int | None:
    return civ_frame_parts(query).selector


def recording_executor(
    *,
    supports_cmd29: Callable[[int, int | None], bool] | None = None,
) -> tuple[IcomCivAcquisitionExecutor, list[AcquisitionQueryCase]]:
    """Create an executor whose exact current sender records legacy tuples."""
    sent: list[AcquisitionQueryCase] = []

    async def send_query(
        command: int, sub: int | bytes | None, receiver: int | None
    ) -> None:
        sent.append((command, sub, receiver))

    return (
        IcomCivAcquisitionExecutor(send_query, supports_cmd29=supports_cmd29),
        sent,
    )


async def send_state_query(
    poller: RadioPoller,
    query: AcquisitionQueryCase,
) -> None:
    """Send one legacy query through the poller's exact current interface."""
    command, sub, receiver = _require_legacy_query(query)
    await poller._send_one_state_query(command, sub, receiver)  # noqa: SLF001


def assert_acquisition_query_representation_contract() -> None:
    """Pin the current legacy representation until the production migration."""
    assert acquisition_query(0x18) == (0x18, None, None)
    assert acquisition_query(0x16, sub=0x59) == (0x16, 0x59, None)
    assert acquisition_query(0x1A, sub=0x05, data=b"\x01\x91") == (
        0x1A,
        b"\x05\x01\x91",
        None,
    )
    assert acquisition_query(0x07, data=b"\xc2") == (0x07, 0xC2, None)
    assert acquisition_query(0x25, selector=1) == (0x25, None, 1)

    command_data = civ_frame_parts(acquisition_query(0x07, data=b"\xc2"))
    assert command_data.sub is None
    assert command_data.data == b"\xc2"
    for selector_query in (
        acquisition_query(0x25, selector=1),
        acquisition_query(0x25, data=b"\x01"),
    ):
        selector_parts = civ_frame_parts(selector_query)
        assert selector_parts.sub is None
        assert selector_parts.data == b"\x01"
        assert selector_parts.receiver is None
        assert selector_parts.selector == 1

    for invalid in ([0x18, None, None], CivFrameParts(command=0x18)):
        try:
            civ_frame_parts(invalid)  # type: ignore[arg-type]
        except TypeError:
            pass
        else:
            raise AssertionError("helper accepted a non-tuple acquisition query")
