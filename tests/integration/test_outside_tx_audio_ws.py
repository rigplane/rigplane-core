"""MOR-3764: outside-only TX audio (``0x12``) through the real audio WebSocket.

The in-process pins (``tests/test_managed_tx_outside_tx_authorization.py``)
build ``AudioHandler`` directly; this file drives the same rule end to end:
a WebSocket client on the real ``/api/v1/audio`` upgrade of a ``WebServer``
bound to 127.0.0.1:0, over the MOR-3538 arena
(``test_multi_session_tx_lanes._Arena`` / ``_open_mic`` →
``test_tx_under_fault_matrix._stand_up_stack``: the real ``WebServer`` +
``RigctldServer`` + managed TX authority + ``IcomRadio`` over the
``MockIcomRadio`` LAN simulator). The radio's recorded pushes are read at
the simulator's TX sink (``sim.audio_tx_frames``).

Pinned: a ``0x12`` frame from a local controller ticket reaches the radio
only while the radio is observed keyed OUTSIDE Core (FOREIGN TX, intent
``rx``); from the moment a direct rigctld client keys (owner
``rigctld-client-N``) a ``0x12`` frame is dropped, while a ``0x11`` frame
still flows — the default lane is unchanged.

Not pinned here: a keying started while an admitted ``0x12`` push is in
flight waits for the push. The simulator's audio sink acknowledges every
push immediately and offers no fault hook that holds one open, so the
interleaving cannot be produced through the socket; the in-process
atomicity tests pin that guarantee.

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.mock_integration

from rigplane.runtime.tx_interlock import RfState  # noqa: E402
from rigplane.web.protocol import (  # noqa: E402
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
    MSG_TYPE_AUDIO_TX_OUTSIDE,
)

from test_multi_session_tx_lanes import _Arena, _open_mic  # noqa: E402
from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEY_DEADLINE_S,
    _LEASE_BUDGET_S,
    _wait_ptt,
    _wait_released,
    _wait_rf,
    _WsKeyClient,
)

# Distinct frame sizes tell the simulator's TX sink whose audio it received.
_OUTSIDE_SAMPLES = 300  # a 0x12 frame lands as a 600-byte payload
_DEFAULT_SAMPLES = 480  # a 0x11 frame lands as a 960-byte payload


def _pcm_tx_frame(msg_type: int, samples: int) -> bytes:
    return (
        bytes([msg_type, AUDIO_CODEC_PCM16])
        + b"\x00" * (AUDIO_HEADER_SIZE - 2)
        + b"\x10\x00" * samples
    )


def _air_sizes(arena: _Arena, mark: int = 0) -> set[int]:
    return {frame.payload_len for frame in arena.sim.audio_tx_frames[mark:]}


async def _wait_air_sizes(arena: _Arena, expected: set[int], mark: int = 0) -> None:
    """Wait until exactly ``expected`` new payload sizes sit in the TX sink."""
    end = time.monotonic() + _GATE_TIMEOUT_S
    while _air_sizes(arena, mark) != expected:
        assert time.monotonic() < end, (
            f"TX sink never saw {expected}: {_air_sizes(arena, mark)}"
        )
        await asyncio.sleep(0.05)


async def _flush_mic(mic: _WsKeyClient) -> None:
    """Round-trip ``audio_start`` on the mic's own connection.

    The audio handler answers a control message only after every earlier
    frame on the connection was fully handled, so once the reply lands a
    binary frame sent before the flush has reached its verdict (pushed or
    dropped) — the negative pin below needs no settle sleep.
    """
    await mic.send({"type": "audio_start", "direction": "tx"})
    await mic._until(lambda message: message.get("type") == "audio_tx_format")  # noqa: SLF001


async def test_outside_only_tx_audio_flows_only_while_keyed_outside_core(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path, enable_audio=True) as arena:
        session = await arena.session()
        pro = await _open_mic(arena, session.key)  # a local controller ticket
        sessionless = await _open_mic(arena, None)
        sim = arena.sim

        # The operator keys the radio itself (no Core client, no wire write):
        # a FOREIGN TX with intent rx. The outside-only frame is admitted.
        sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        await pro.send_binary(
            _pcm_tx_frame(MSG_TYPE_AUDIO_TX_OUTSIDE, _OUTSIDE_SAMPLES)
        )
        await _wait_air_sizes(arena, {_OUTSIDE_SAMPLES * 2})

        # The operator lets go; a direct rigctld client keys, Core-managed.
        sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        rig = await arena.tcp()
        assert await rig.key()
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        holder = arena.holder()
        assert holder is not None and holder.startswith("rigctld-client-"), holder

        # From the moment the Core client keys, a 0x12 frame is refused...
        mark = len(sim.audio_tx_frames)
        await pro.send_binary(
            _pcm_tx_frame(MSG_TYPE_AUDIO_TX_OUTSIDE, _OUTSIDE_SAMPLES)
        )
        await _flush_mic(pro)  # the refused frame reached its verdict
        assert _OUTSIDE_SAMPLES * 2 not in _air_sizes(arena, mark), _air_sizes(
            arena, mark
        )
        # ...while a 0x11 frame on the default lane is unchanged and flows.
        await sessionless.send_binary(
            _pcm_tx_frame(MSG_TYPE_AUDIO_TX, _DEFAULT_SAMPLES)
        )
        await _wait_air_sizes(arena, {_DEFAULT_SAMPLES * 2}, mark)

        assert await arena.tcp_line(rig, "T 0") == "RPRT 0\n"
        await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        await _wait_released(arena.stack.composition)
