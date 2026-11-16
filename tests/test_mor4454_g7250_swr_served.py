"""MOR-4454: the G7250 SWR reaches the served ``/api/v1/state``.

Seam test over the real CLI-built G7250 radio on the shared serial fake, the
real acquisition scheduler drained through the real ``RadioPoller``, and the
real HTTP state handler (harness reused from ``tests/test_g7250_local_api.py``).
The state is parsed through the producer's ``ServerStatePublic``. The fake
only answers reads: the PTT register is flipped directly on it, never by a
write frame.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rigplane.web.state_schema import ServerStatePublic
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from test_g7250_local_api import (
    _G7250OperatorFake,
    _http_state,
    _station,
)

_SWR_RAW_BCD = b"\x00\x48"  # raw 48 -> SWR 1.5 (CI-V Reference Rev 1.2 Table 2-4)


class _SwrFake(_G7250OperatorFake):
    """The operator fake plus a keyed-only SWR register (reads only)."""

    def _answer(self, parsed: Any, connection: Any, frame: bytes) -> bytes | None:
        if parsed.command == 0x15 and parsed.sub == 0x12 and not parsed.data:
            data = _SWR_RAW_BCD if self.ptt else b"\x00\x00"
            return build_civ_frame(
                CONTROLLER_ADDR, self._civ_addr, 0x15, sub=0x12, data=data
            )
        return super()._answer(parsed, connection, frame)


async def _drain_until(
    web: Any, radio: Any, poller: Any, host: str, port: int, expected: float | None
) -> ServerStatePublic:
    service = radio._state_freshness_service
    # An explicit deadline, not asyncio.timeout: the poller swallows a
    # cancellation, which would turn a regression into a hang.
    deadline = asyncio.get_running_loop().time() + 10.0
    while True:
        service.tick()
        await poller._send_scheduler_requests()
        raw = await _http_state(host, port)
        state = ServerStatePublic.model_validate(raw)
        if state.swrMeter == expected:
            return state
        assert asyncio.get_running_loop().time() < deadline, (
            f"served swrMeter never became {expected!r}: {state.swrMeter!r}"
        )
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_served_state_carries_calibrated_swr_only_while_keyed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "test_g7250_local_api._G7250OperatorFake", _SwrFake, raising=True
    )
    async with _station(monkeypatch) as (web, radio, fake, _entries, _ex, poller):
        host, port = web._server.sockets[0].getsockname()[:2]
        assert isinstance(fake, _SwrFake)

        rx = ServerStatePublic.model_validate(await _http_state(host, port))
        assert rx.swrMeter is None

        fake._ptt = True  # the radio keys; the poller only ever reads
        tx = await _drain_until(web, radio, poller, host, port, 1.5)
        assert tx.swrMeter == pytest.approx(1.5)
        assert tx.fieldStatus is not None
        assert tx.fieldStatus["swrMeter"].availability == "available"

        fake._ptt = False
        rx = await _drain_until(web, radio, poller, host, port, None)
        assert rx.swrMeter is None
        assert rx.fieldStatus is not None
        assert rx.fieldStatus["swrMeter"].availability == "unavailable"
