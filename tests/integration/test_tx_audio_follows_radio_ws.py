"""MOR-3921: TX audio follows the radio, whoever keyed it, through the real audio WebSocket.

Owner decision 2026-10-30: client TX audio goes on air whenever the radio
reports transmit — keyed by RigPlane, a footswitch, the hand mic or another
program. There is no own-versus-foreign classification and no second frame
type: the retired ``0x12`` ("TX outside-only") frame is dropped fail-closed.
RigPlane itself still refuses to key while the radio already transmits.

Drives a WebSocket client on the real ``/api/v1/audio`` upgrade of a
``WebServer`` bound to 127.0.0.1:0, over the MOR-3538 arena
(``test_multi_session_tx_lanes._Arena`` / ``_open_mic`` →
``test_tx_under_fault_matrix._stand_up_stack``: the real ``WebServer`` +
``RigctldServer`` + managed TX authority + ``IcomRadio`` over the
``MockIcomRadio`` LAN simulator). The radio's recorded pushes are read at
the simulator's TX sink (``sim.audio_tx_frames``).

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
)

from test_multi_session_tx_lanes import _CAT_REFUSED, _Arena, _open_mic  # noqa: E402
from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEY_DEADLINE_S,
    _LEASE_BUDGET_S,
    _wait_ptt,
    _wait_released,
    _wait_rf,
    _WsKeyClient,
)

# The retired "TX outside-only" frame type. Kept here only to prove an old
# client's frame is dropped; Core no longer defines it.
_RETIRED_TX_OUTSIDE = 0x12

# Distinct frame sizes tell the simulator's TX sink whose audio it received.
_RETIRED_SAMPLES = 300  # a 0x12 frame would land as a 600-byte payload
_PRO_SAMPLES = 400  # Pro's 0x11 frame lands as an 800-byte payload
_DEFAULT_SAMPLES = 480  # a sessionless 0x11 frame lands as a 960-byte payload


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
    dropped) — the negative pins below need no settle sleep.
    """
    await mic.send({"type": "audio_start", "direction": "tx"})
    await mic._until(lambda message: message.get("type") == "audio_tx_format")  # noqa: SLF001


async def test_tx_audio_follows_the_radio_whoever_keyed_it(tmp_path: Path) -> None:
    async with _Arena(tmp_path, enable_audio=True) as arena:
        session = await arena.session()
        pro = await _open_mic(arena, session.key)  # a local controller ticket
        sessionless = await _open_mic(arena, None)
        sim = arena.sim

        # Nothing transmits: client audio is held.
        mark = len(sim.audio_tx_frames)
        await pro.send_binary(_pcm_tx_frame(MSG_TYPE_AUDIO_TX, _PRO_SAMPLES))
        await _flush_mic(pro)
        assert _air_sizes(arena, mark) == set()

        # The operator keys the radio itself (footswitch, hand mic, another
        # program: no Core client, no wire write). The radio reports TX, so
        # plain 0x11 client audio goes on air.
        sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        mark = len(sim.audio_tx_frames)
        await pro.send_binary(_pcm_tx_frame(MSG_TYPE_AUDIO_TX, _PRO_SAMPLES))
        await _wait_air_sizes(arena, {_PRO_SAMPLES * 2}, mark)

        # The retired 0x12 frame is dropped fail-closed, even now.
        mark = len(sim.audio_tx_frames)
        await pro.send_binary(_pcm_tx_frame(_RETIRED_TX_OUTSIDE, _RETIRED_SAMPLES))
        await _flush_mic(pro)
        assert _air_sizes(arena, mark) == set()

        # RigPlane refuses to key while the radio already transmits.
        rig = await arena.tcp()
        writes = len(sim.ptt_set_writes)
        assert await arena.tcp_line(rig, "T 1") == _CAT_REFUSED
        assert sim.ptt_set_writes[writes:] == [] and arena.holder() is None

        # The operator lets go; a direct rigctld client keys, Core-managed.
        sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        assert await rig.key()
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        holder = arena.holder()
        assert holder is not None and holder.startswith("rigctld-client-"), holder

        # Keyed by RigPlane: 0x11 audio flows; 0x12 stays dropped.
        mark = len(sim.audio_tx_frames)
        await sessionless.send_binary(
            _pcm_tx_frame(_RETIRED_TX_OUTSIDE, _RETIRED_SAMPLES)
        )
        await sessionless.send_binary(
            _pcm_tx_frame(MSG_TYPE_AUDIO_TX, _DEFAULT_SAMPLES)
        )
        await _wait_air_sizes(arena, {_DEFAULT_SAMPLES * 2}, mark)

        assert await arena.tcp_line(rig, "T 0") == "RPRT 0\n"
        await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        await _wait_released(arena.stack.composition)
