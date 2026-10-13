"""Mock UDP radio server for integration testing.

Emulates an Icom IC-7610 over UDP so that transport/radio/CLI tests can run
without real hardware.  Two asyncio datagram servers are always started:

  * control port  – authentication handshake, pings, token renewal
  * CI-V port     – CI-V command / response exchange

plus, only with ``enable_audio=True``, a third one:

  * audio port    – the LAN audio port the status reply advertises
    (RX stream, TX frame sink; ``simulator_audio.AudioPortMixin``)

Usage::

    server = MockIcomRadio()
    await server.start()
    # ... connect IcomRadio to server.control_port ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import struct
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from rigplane_testkit.icom.faults import Direction, FaultPlan, Port
from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.icom.simulator_audio import AudioPortMixin

logger = logging.getLogger(__name__)

# Error codes carried in the status / login response at offset 0x30 (little-endian).
_ERR_OK = 0x00000000
_ERR_AUTH_FAIL = 0xFEFFFFFF  # credential failure (login)
_ERR_PREV_SESSION_ACTIVE = 0xFFFFFFFF  # busy / previous session still held
_ERR_TOKEN_REJECT = 0xFFFFFFFF  # renewal for a token naming no live session

# Token request-type magics carried at pkt[0x15] of a 0x40-byte control packet.
_TOKEN_REMOVE = 0x01  # graceful withdraw — frees the held session immediately
_TOKEN_ACK = 0x02  # initial token acknowledgement
_TOKEN_RENEW = 0x05  # keepalive renewal

# OpenClose magics carried at pkt[0x15] of a 0x16-byte CI-V packet.
_OPENCLOSE_CLOSE = 0x00  # graceful data-port close — frees the held session
_OPENCLOSE_OPEN = 0x04

# Conninfo notice (unsolicited 0x90, radio → client): request-type 0x00 at
# 0x15 marks it radio-originated; the busy u32 at 0x60 = 0 means "session free"
# (recorded IC-7610 layout, bench capture MOR-2625/2627 and recording w2).
_CONNINFO_NOTICE_REQUEST_TYPE = 0x00
# Recorded timing: the notice follows the drop by ~0.1 s (MOR-2625/2627).
_DROP_NOTICE_DELAY_S = 0.1
# Recorded timing (recording w2): after the power-off notice the radio still
# answers discovery and pings for ~0.02 s before going dark.
_POWER_OFF_NOTICE_WINDOW_S = 0.02

# Default accelerated keepalive hold (D7). The IC-7610 holds a dead client's
# session for 83.4-85.9 s (MOR-3391, fresh logins from separate ports); the fast
# suite uses ~0.5 s so a dropped session frees quickly. A near-real value
# (~45-60 s) can be passed for the one slow timing test.
_DEFAULT_KEEPALIVE_HOLD_S = 0.5

# Retransmit-request cadence while a CI-V sequence gap is open: a round
# (the request sent twice) follows a client tracked packet only when the
# previous round is at least 0.075 s old, and otherwise the 0.1 s loop
# repeats it for a silent client (recorded IC-7610, never closer than
# 77 ms apart; ``tests/fixtures/ic7610/retransmit_gaps.jsonl``).
_CIV_GAP_RETRANSMIT_PERIOD_S = 0.1
_CIV_GAP_ROUND_MIN_SPACING_S = 0.075

# How long an unclaimed orphan CI-V stream state survives a session's
# unclean end: IC-7610, MOR-3418, between 126 and 150 s, w43/w44.
ORPHAN_CIV_STATE_EXPIRY_S = 140.0

# Scope wave fixtures (IC-7610 over LAN, MOR-3310): the per-speed frame gap
# is read from ``tests/fixtures/ic7610/scope_cadence.json`` at import — one
# source of truth, never a copied constant — and each streaming receiver's
# template is the first recorded ``27 00`` frame for that receiver in the
# ``scope`` window of ``tests/fixtures/ic7610/civ_pairs.jsonl`` (receiver
# byte 6: 0x00 MAIN, 0x01 SUB), byte for byte.
_SCOPE_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ic7610"
)
_SCOPE_CADENCE = json.loads(
    (_SCOPE_FIXTURE_DIR / "scope_cadence.json").read_text(encoding="utf-8")
)
# Speed code (``27 1A 0r ss``) to wave-frame gap: fast and mid stream every
# recorded median gap; slow streams nothing (not recorded).
_SCOPE_GAP_S: dict[int, float | None] = {
    0x00: _SCOPE_CADENCE["speeds"]["fast"]["gap_median_s"],
    0x01: _SCOPE_CADENCE["speeds"]["mid"]["gap_median_s"],
    0x02: None,
}


def _load_scope_templates() -> dict[int, bytes]:
    """First recorded ``27 00`` wave frame per receiver, byte for byte."""
    templates: dict[int, bytes] = {}
    for line in (
        (_SCOPE_FIXTURE_DIR / "civ_pairs.jsonl").read_text(encoding="utf-8")
    ).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("window") != "scope" or row.get("dir") != "r2c":
            continue
        frame = bytes.fromhex(row["frame"])
        if (
            len(frame) == _SCOPE_CADENCE["frame_len"]
            and len(frame) > 6
            and frame[4] == 0x27
            and frame[5] == 0x00
            and frame[6] not in templates
        ):
            templates[frame[6]] = frame
    assert set(templates) == {0x00, 0x01}, "one template per receiver"
    return templates


_SCOPE_TEMPLATES = _load_scope_templates()

# Scope stream pacing tick: below the fastest recorded gap (SUB fast,
# 0.0344 s) so live emission keeps up; tests take over pacing explicitly.
_SCOPE_TICK_S = 0.005

# ---------------------------------------------------------------------------
# Protocol constants (duplicated here to keep mock self-contained)
# ---------------------------------------------------------------------------

_HEADER_SIZE = 0x10  # 16 bytes: len(4) + type(2) + seq(2) + sentid(4) + rcvdid(4)
_PING_SIZE = 0x15  # 21 bytes
_CIV_HEADER_SIZE = 0x15  # 21 bytes before CI-V frame in DATA packets

# Packet type codes
_PT_DATA = 0x00
_PT_CONTROL = 0x01
_PT_ARE_YOU_THERE = 0x03
_PT_I_AM_HERE = 0x04
_PT_DISCONNECT = 0x05
_PT_ARE_YOU_READY = 0x06
_PT_PING = 0x07

# CI-V addresses
_RADIO_DEFAULT_ADDR = 0x98
_CONTROLLER_ADDR = 0xE0

# CI-V command codes
_CIV_PREAMBLE = b"\xfe\xfe"
_CIV_TERM = b"\xfd"
_CMD_FREQ_GET = 0x03
_CMD_FREQ_SET = 0x05
_CMD_MODE_GET = 0x04
_CMD_MODE_SET = 0x06
_CMD_LEVEL = 0x14
_CMD_METER = 0x15
_CMD_ATT = 0x11
_CMD_PREAMP = 0x16
_CMD_CMD29 = 0x29
_CMD_PTT = 0x1C
_CMD_CTL_MEM_MENU = 0x1A  # 0x1A 0x05 <item-hi> <item-lo> menu reads/writes
_CMD_SELECTED_FREQ = 0x25  # Selected/unselected receiver frequency
_CMD_SELECTED_MODE = 0x26  # Selected/unselected receiver mode
_CMD_VFO_SELECT = 0x07  # MAIN/SUB receiver select (D0/D1) and read (D2)
_CMD_SCOPE = 0x27  # scope panel / wave output / receiver / dual / speed
_CMD_ACK = 0xFB
_CMD_NAK = 0xFA
_SUB_RF_POWER = 0x0A
_SUB_S_METER = 0x02
_SUB_SWR_METER = 0x12
_SUB_ALC_METER = 0x13
_SUB_PREAMP_STATUS = 0x02
_SUB_DIGISEL_STATUS = 0x4E
_SUB_PTT = 0x00

# CI-V 0x1A 05 menu items modelled by the simulator, as
# ``(item, (default, bcd_bytes))``. The four MOD-input sources (0x0091-
# 0x0094, ``rigs/ic7610.toml`` [commands.overrides]: get/set_data*_mod_input)
# are read at connect by ``src/rigplane/web/radio_poller.py``:
# ``_fetch_mod_inputs``; the two NB items (0x0290/0x0291, ``rigs/ic7610.toml``:
# get/set_nb_depth and get/set_nb_width) by ``_fetch_nb_controls`` on radios
# with the "nb" capability (IC-7610 declares it). MOD-input values follow the
# MOD-input source enum 0=MIC .. 5=LAN (``frontend/src/lib/radio/mod-input.ts``):
# DATA OFF answers ACC (1) and the DATA groups answer LAN (5). NB depth is one
# BCD digit 0-9 and NB width two BCD digits 0-255 — the wire shapes
# ``src/rigplane/commands/levels.py`` set_nb_depth / set_nb_width build — and
# both default to 0 like ``src/rigplane/core/radio_state.py``
# (RadioState.nb_depth / nb_width). All defaults are simulator choices, not
# recorded factory settings. Items outside this table still NAK.
_CTL_MEM_MENU_STARTUP_ITEMS: dict[bytes, tuple[int, int]] = {
    b"\x00\x91": (1, 1),  # DATA OFF mod input -> ACC
    b"\x00\x92": (5, 1),  # DATA1 mod input -> LAN
    b"\x00\x93": (5, 1),  # DATA2 mod input -> LAN
    b"\x00\x94": (5, 1),  # DATA3 mod input -> LAN
    b"\x02\x90": (0, 1),  # NB depth (0-9, one BCD digit)
    b"\x02\x91": (0, 2),  # NB width (0-255, two BCD digits)
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bcd_byte(value: int) -> int:
    """Encode 0-99 integer to one BCD byte (e.g. 18 → 0x18)."""
    return ((value // 10) << 4) | (value % 10)


def _level_bcd_encode(value: int) -> bytes:
    """Encode 0-9999 level to 2-byte BCD (e.g. 128 → b'\\x01\\x28')."""
    d = f"{value:04d}"
    return bytes([(int(d[0]) << 4) | int(d[1]), (int(d[2]) << 4) | int(d[3])])


def _bcd_encode_freq(freq_hz: int) -> bytes:
    """Encode frequency in Hz to Icom 5-byte BCD (little-endian)."""
    digits = f"{freq_hz:010d}"
    result = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])
        high = int(digits[9 - 2 * i - 1])
        result[i] = (high << 4) | low
    return bytes(result)


def _bcd_decode_value(data: bytes) -> int:
    """Decode packed BCD digits, most significant pair first (120 → b'\\x01\\x20').

    Same layout as ``src/rigplane/commands/_codec.py``: ``_bcd_decode_value``
    (menu/level values), unlike the little-endian-by-byte frequency codec.
    """
    value = 0
    for byte in data:
        value = value * 100 + (byte >> 4) * 10 + (byte & 0x0F)
    return value


def _bcd_decode_freq(data: bytes) -> int:
    """Decode Icom 5-byte BCD frequency to Hz."""
    freq = 0
    for i in range(len(data)):
        high = (data[i] >> 4) & 0x0F
        low = data[i] & 0x0F
        freq += low * (10 ** (2 * i)) + high * (10 ** (2 * i + 1))
    return freq


def _status_accepted(reply: bytes) -> bool:
    """True when a conninfo status reply claims the session for its client.

    Acceptance is a returned CI-V port with a zero error: the layout
    ``_status_response`` writes (error LE u32 at 0x30, CI-V port BE u16 at
    0x42). Busy rejects and not-ready answers carry port 0.
    """
    if len(reply) < 0x50:
        return False
    error = struct.unpack_from("<I", reply, 0x30)[0]
    civ_port = struct.unpack_from(">H", reply, 0x42)[0]
    return error == _ERR_OK and civ_port != 0


# ---------------------------------------------------------------------------
# asyncio DatagramProtocol
# ---------------------------------------------------------------------------


class _MockProtocol(asyncio.DatagramProtocol):
    """Minimal datagram protocol that routes packets to MockIcomRadio."""

    def __init__(self, owner: "MockIcomRadio", label: str) -> None:
        self._owner = owner
        self._label = label
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._owner._on_packet(data, addr, self._label, self)
        except Exception:
            logger.exception("Mock %s: unhandled error", self._label)

    def error_received(self, exc: Exception) -> None:
        logger.debug("Mock %s UDP error: %s", self._label, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        pass

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is not None and not self._transport.is_closing():
            self._transport.sendto(data, addr)


# ---------------------------------------------------------------------------
# CI-V sequence-gap tracking (recorded IC-7610 behaviour)
# ---------------------------------------------------------------------------


class _CivGapState:
    """Per-session tracking of one CI-V client's tracked sequence numbers.

    ``next_expected`` is None until the first tracked packet, which is
    the OpenClose(open) itself; ``missing`` holds the numbers skipped so
    far and ``held`` the ahead-of-gap packets waiting for the fill, keyed
    by seq as ``(kind, data, addr, sender_id)`` with kind ``"idle"``,
    ``"openclose"`` or ``"data"``.
    The tracker outlives OpenClose(open): a re-sent open never restarts
    it, so a number at or below the last processed one is ignored
    (recorded real IC-7610, MOR-3361). It ends only with the radio
    session — a new accepted login, a conninfo claiming a new session,
    ``drop_session`` or reboot — which clear the whole map.
    """

    def __init__(self, addr: tuple[str, int], sender_id: int) -> None:
        self.addr = addr
        self.sender_id = sender_id
        self.next_expected: int | None = None
        self.missing: set[int] = set()
        self.held: dict[int, tuple[str, bytes, tuple[str, int], int]] = {}
        self.last_round_at: float | None = None
        # True when seeded from the previous session's orphan state
        # (MOR-3418 slice G); only inherited trackers use the w50 knobs.
        self.inherited: bool = False
        # The frontier value inherited at seed; an inherited tracker sends
        # no retransmit requests until one of its packets arrives past it
        # (MOR-3418 fix1, recorded w50 a/b). None for fresh trackers.
        self.inherited_frontier: int | None = None
        # Set once a packet arrives past inherited_frontier; only then do
        # the 0.1 s loop rounds ask for the inherited gap.
        self.inherited_armed: bool = False
        # Fills accepted since the last 0.1 s round (the fill-budget window).
        self.fills_this_round: int = 0
        # Consecutive 0.1 s rounds each missing number went without a fill.
        self.unanswered_rounds: dict[int, int] = {}


def _missing_runs(missing: set[int]) -> list[tuple[int, int]]:
    """Group missing numbers into contiguous ``(first, last)`` runs, sorted."""
    runs: list[tuple[int, int]] = []
    for seq in sorted(missing):
        if runs and seq == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], seq)
        else:
            runs.append((seq, seq))
    return runs


# ---------------------------------------------------------------------------
# MockIcomRadio
# ---------------------------------------------------------------------------


class MockIcomRadio(AudioPortMixin):
    """Asyncio UDP server emulating an IC-7610 for integration testing.

    Handles the full connection lifecycle on two ports, plus the audio
    port when ``enable_audio`` is True:

    * **Control port** – discovery (AYT/IAH), login, token exchange, pings
    * **CI-V port**    – discovery, open/close, CI-V commands, pings
    * **Audio port**   – the LAN audio port the status reply advertises
      (discovery, pings, the recorded RX stream and the client-frame TX
      sink; see :class:`rigplane_testkit.icom.simulator_audio.AudioPortMixin`)

    After calling :meth:`start`, connect an :class:`~rigplane.radio.IcomRadio`
    to ``host:control_port`` and it will go through the full handshake.

    Args:
        host: Bind address for both servers.
        port: Control port hint (0 = OS-assigned).
        civ_port: CI-V port hint (0 = OS-assigned).
        username: Expected username (any value accepted when ``auth_fail`` is False).
        password: Expected password (unused; see ``auth_fail``).
        radio_addr: CI-V address the mock claims to be (default 0x98 = IC-7610).
        lan_civ_echo: Echo every CI-V DATA frame addressed to the radio back
            byte-identical before its reply (recorded IC-7610 LAN behaviour).
            Default True (fidelity); fault-injection tests that mangle the
            reply itself set it False so the plan sees one packet, not two.
        fault_plan: optional :class:`~rigplane_testkit.icom.faults.FaultPlan`
            applying per-port/per-direction drop/duplicate/delay/reorder/
            corrupt/truncate rules on top of the legacy ``drop_rate`` /
            ``response_delay`` / ``stall_for`` knobs, which keep working
            unchanged.
        enable_audio: bind the LAN audio-port listener (default False).
            Off, the simulator behaves as before this listener existed:
            the status reply still advertises an audio port but nothing
            listens there and nothing answers. (Named ``enable_audio``
            because ``audio_port`` is already the bound-port property.)
        stateful_ptt: opt-in stateful PTT (default False keeps the
            pre-hook behaviour byte-for-byte: ``1C 00`` GET always answers
            OFF and every ``1C 00`` SET NAKs — the MOR-1217 note). When
            True, SET ``1C 00 01`` / ``1C 00 00`` is acknowledged FB and
            flips the simulated PTT state (readable as :attr:`ptt_on`),
            GET answers the current state, and any other ``1C 00`` payload
            still NAKs.
        ptt_on_session_loss: what the simulated radio does with an active
            PTT at every session end. ``"release"`` unkeys at that moment;
            ``"hold"`` keeps PTT on until a client unkeys. The real IC-7610
            behaviour is UNRECORDED (TX is never actuated on the bench),
            so tests that depend on it cover both values
            (``tests/integration/test_tx_under_fault_matrix.py``).
        recorded_answers: optional
            :class:`~rigplane_testkit.icom.replay.RecordedAnswerTable`
            answering requests the native CI-V handlers NAK — including
            requests with no native handler — before the final NAK. A
            native data/ACK answer is kept, and the opt-in
            ``refused_civ_commands`` hook still NAKs first, never overridden
            by the table (answer order in ``_dispatch_civ``). Default None
            changes nothing. The LAN CI-V echo (``lan_civ_echo``) is not
            affected by the table.
        capture_civ_frames: record every well-formed inbound CI-V frame
            addressed to the radio in :attr:`civ_frames_received`.
            Default False (no recording, no retention).
        civ_state_survives_unclean_end: keep the CI-V stream state
            (frontier plus gap list) of a session whose hold expires on
            its own as orphan state for the next opener (recorded IC-7610,
            MOR-3418; pinned by
            ``tests/testkit/test_simulator_civ_orphan.py``). Default False
            keeps the previous behaviour. An inherited tracker sends no
            retransmit requests until one of its packets arrives past the
            inherited frontier (recorded w50 a/b). Modelled globally, not
            per IP (unestablished); no expiry of a claimed state is
            modelled (unmeasured).
        orphan_civ_state_expiry_s: how long an unclaimed orphan waits for
            the next opener before it is dropped (default
            ``ORPHAN_CIV_STATE_EXPIRY_S``). A clean logout (owner
            token-remove, disconnect, the stream owner's OpenClose(close))
            and ``reboot`` clear it at once.
        civ_inherited_fills_per_round: fills of missing numbers an
            inherited tracker accepts per 0.1 s round (default 0 =
            unlimited, the #4141 gap model). Extra fills are dropped; the
            loop re-asks and the client resends. From one capture
            (MOR-3418 w50 b: one number per round, in order).
        civ_inherited_drop_unanswered_rounds: consecutive 0.1 s rounds a
            missing number may go without an accepted fill before it is
            dropped from the requested set (default 0 = never, the #4141
            model). From one capture (MOR-3418 w50 b: dropped after
            ~17 rounds).
        clock: monotonic clock the CI-V retransmit-request pacing reads
            (round spacing in :meth:`_civ_gap_gate` and
            :meth:`_civ_gap_send_round`, and the silent-client repeat in
            :meth:`_civ_gap_loop`). Default ``time.monotonic``; tests pass
            a hand-advanced clock to pin the round rate without wall time.
        dual_receiver: opt-in native MAIN/SUB receiver select (default
            False keeps the previous behaviour: ``07 D0``/``D1``/``D2``
            fall through to ``recorded_answers`` or the final NAK and no
            selection state is kept). When True, ``07 D0``/``D1`` swap
            the selected and unselected receiver fields (frequency, mode,
            DATA-mode flag, filter) and ``07 D2`` reads the selection
            (recorded IC-7610 ``vfo_select`` window).
        scope_stream: opt-in native scope wave streaming (default False
            keeps the previous behaviour byte-for-byte: 0x27 answers come
            from ``recorded_answers`` or the final NAK and no wave frames
            are sent). When True, ``27 10`` (panel), ``27 11`` (wave
            output), ``27 12`` (scope receiver), ``27 13`` (single/dual)
            and ``27 1A 0r`` (speed per receiver) are answered natively
            from the recorded arrival state (panel on, output off, dual,
            MAIN, MAIN mid / SUB fast), every other 0x27 payload keeps
            today's path, and while wave output is on each streaming
            receiver's recorded ``27 00`` template is sent on the CI-V
            stream at its speed's gap (recorded IC-7610 ``scope`` window
            and ``tests/fixtures/ic7610/scope_cadence.json``).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        civ_port: int = 0,
        username: str = "testuser",
        password: str = "testpass",
        radio_addr: int = _RADIO_DEFAULT_ADDR,
        *,
        single_owner: bool = False,
        keepalive_hold_s: float = _DEFAULT_KEEPALIVE_HOLD_S,
        drop_rate: float = 0.0,
        lan_civ_echo: bool = True,
        unsolicited_civ: bool = False,
        unsolicited_interval_s: float = 0.1,
        fault_plan: FaultPlan | None = None,
        enable_audio: bool = False,
        stateful_ptt: bool = False,
        ptt_on_session_loss: Literal["hold", "release"] = "hold",
        recorded_answers: RecordedAnswerTable | None = None,
        capture_civ_frames: bool = False,
        civ_state_survives_unclean_end: bool = False,
        orphan_civ_state_expiry_s: float = ORPHAN_CIV_STATE_EXPIRY_S,
        civ_inherited_fills_per_round: int = 0,
        civ_inherited_drop_unanswered_rounds: int = 0,
        clock: Callable[[], float] | None = None,
        dual_receiver: bool = False,
        scope_stream: bool = False,
    ) -> None:
        self._host = host
        self._ctrl_port_hint = port
        self._civ_port_hint = civ_port
        self._username = username
        self._password = password
        self._radio_addr = radio_addr

        # UDP transports (set after start())
        self._ctrl_udp: asyncio.DatagramTransport | None = None
        self._civ_udp: asyncio.DatagramTransport | None = None
        # Protocol objects (set after start()) — the CI-V one is used by the
        # unsolicited loop, the control one by the conninfo notice, so
        # autonomous datagrams go through the same fault-plan funnel as replies.
        self._civ_proto: _MockProtocol | None = None
        self._ctrl_proto: _MockProtocol | None = None

        # Actual bound ports
        self._actual_ctrl_port: int = 0
        self._actual_civ_port: int = 0

        # Connection state (learned during handshake)
        self.radio_id: int = 0xDEADBEEF
        self.token: int = 0x12345678
        self._ctrl_client_id: int = 0
        self._civ_client_id: int = 0
        self._ctrl_seq: int = 1
        self._civ_seq: int = 1

        # Radio state (for CI-V responses)
        # MOR-3064: test-data model-id answered to ``19 00`` (identity read),
        # matching the recorded IC-7610 (answers 19 00 with 98 on LAN and
        # USB) — suites override it per case.
        self.model_id: bytes = b"\x98"
        self._frequency: int = 14_074_000
        self._mode: int = 0x01  # USB
        self._filter: int = 1
        self._power: int = 100  # 0-255 level
        self._s_meter: int = 120  # 0-255
        self._swr: int = 10  # 0-255
        self._alc: int = 0
        self._attenuator: int = 0  # dB (0, 3, 6, ... 45)
        self._preamp: int = 0  # 0=off, 1=PREAMP1, 2=PREAMP2
        self._digisel: int = 0  # 0=off, 1=on

        # Selected/unselected receiver vocabulary (CI-V 0x25/0x26, subcommand
        # 00=selected, 01=unselected). The selected receiver shares the
        # 0x03/0x05/0x04/0x06 state above (plus ``_data_mode`` for the 0x26
        # reply's DATA-mode flag byte); the unselected receiver carries its
        # own fields with the defaults below — a valid fresh-band setup, not
        # a recorded factory state.
        self._data_mode: int = 0  # selected receiver DATA-mode flag: 0=OFF
        self._unselected_frequency: int = 7_074_000  # Hz
        self._unselected_mode: int = 0x01  # USB
        self._unselected_data_mode: int = 0  # 0=OFF
        self._unselected_filter: int = 1  # FIL1
        # Native receiver select (opt-in via dual_receiver): 0 = MAIN is
        # the selected receiver, 1 = SUB is (recorded vfo_select window).
        self._dual_receiver: bool = dual_receiver
        self._selected_receiver: int = 0
        # Native scope wave streaming (opt-in via scope_stream). Initial
        # values are the recorded arrival state (MOR-3310: panel on, wave
        # output off, dual on, scope receiver MAIN, MAIN speed mid, SUB
        # speed fast). With the keyword off this state is never read.
        self._scope_stream: bool = scope_stream
        self._scope_panel: int = 0x01
        self._scope_wave_output: int = 0x00
        self._scope_dual: int = 0x01
        self._scope_receiver: int = 0x00
        self._scope_speed: dict[int, int] = {0x00: 0x01, 0x01: 0x00}
        # Next wave-frame due time per receiver on ``self.clock``; a
        # session end, output off or ``stop`` clears it and the next SET
        # restarts the cadence one gap out.
        self._scope_next_due: dict[int, float] = {}
        self._scope_task: asyncio.Task[None] | None = None
        # Modelling of the 0x1A 05 menu items the startup reader reads;
        # current values (defaults and byte widths live in the table above).
        self._ctl_mem_menu: dict[bytes, int] = {
            item: default
            for item, (default, _width) in _CTL_MEM_MENU_STARTUP_ITEMS.items()
        }

        # Behaviour flags for edge-case tests
        self.auth_fail: bool = False
        self.response_delay: float = 0.0  # extra sleep before sending a response
        # LAN CI-V echo (recorded IC-7610 behaviour): every CI-V DATA frame
        # addressed to the radio is sent back byte-identical before its
        # reply. The production client drops it in ``_route_civ_frame``
        # (``src/rigplane/runtime/_civ_rx.py``: a frame whose sender is not
        # the radio address never routes), so CI-V routing ignores it.
        self.lan_civ_echo: bool = lan_civ_echo

        # ----------------------------------------------------------------
        # Session-lifecycle modelling (additive; all opt-in)
        # ----------------------------------------------------------------
        # When ``single_owner`` is True the radio behaves like a real IC-7610:
        # it tracks ONE owning session keyed by (remote_addr, control my_id).
        # A conninfo from a different owner while a session is held is rejected
        # with civ_port=0 + error=0xFFFFFFFF (previous-session-active). The held
        # session is freed immediately on the session owner's token-remove or
        # the CI-V stream owner's OpenClose(close) — the production client
        # sends OpenClose from its CI-V socket, a different (addr, sender_id)
        # from the control socket's owner — a token-remove or close from any
        # other source is a no-op — and otherwise auto-released after
        # ``keepalive_hold_s`` of silence.
        self.single_owner: bool = single_owner
        self.keepalive_hold_s: float = keepalive_hold_s
        # Audio-port listener switch: default off, when the status reply
        # advertises the dummy port and nothing listens.
        self.enable_audio: bool = enable_audio
        # Owner identity = (remote_addr, my_id); None when no session is held.
        self._owner: tuple[tuple[str, int], int] | None = None
        self._keepalive_timer: asyncio.TimerHandle | None = None
        # Force civ_port=0 (not-ready, NOT a busy reject) until this monotonic
        # deadline — drives CONNECTING -> COOLDOWN -> CONNECTING without a
        # 0xFFFFFFFF reject. 0.0 means "always available".
        self._civ_unavailable_until: float = 0.0

        # Opt-in stateful PTT (MOR-3251): default OFF keeps the pre-hook
        # behaviour byte-for-byte (GET always OFF, every SET NAK). The new
        # handling lives in the native CI-V dispatcher
        # ``_dispatch_civ_native``, which ``_handle_civ_data`` reaches only
        # after the sequence-gap gate and after the LAN echo — recorded
        # IC-7610 order for every request.
        self._stateful_ptt: bool = stateful_ptt
        self._ptt_on_session_loss: Literal["hold", "release"] = ptt_on_session_loss
        self._ptt_state: bool = False
        # Every simulated PTT state change as (monotonic time, new state):
        # wire SETs and the ptt_on_session_loss="release" unkey at session
        # end. Wire SETs are additionally recorded in ``ptt_set_writes`` as
        # (monotonic time, on).
        self.ptt_transitions: list[tuple[float, bool]] = []
        self.ptt_set_writes: list[tuple[float, bool]] = []

        # Fault injection
        self.drop_rate: float = drop_rate  # probability [0,1] of dropping a reply
        self._stall_until: float = 0.0  # suppress unsolicited CI-V until this time
        # Declarative per-port/per-direction fault plan (sits BESIDE the legacy
        # knobs above: it has its own seeded RNG and never touches their state).
        # None = no plan, every stream passes through untouched.
        self.fault_plan: FaultPlan | None = fault_plan
        # Recorded answer table (opt-in, additive): a request the native
        # CI-V handlers NAK — or that has no native handler — is answered
        # from the recorded rows before the final NAK (``_dispatch_civ``).
        # None = no table, the pre-mount behaviour.
        self.recorded_answers: RecordedAnswerTable | None = recorded_answers
        # Opt-in capture of inbound CI-V frames (default off): every
        # well-formed CI-V frame addressed to this radio is appended to
        # ``civ_frames_received`` in arrival order, whether the command is
        # implemented or NAKed. Lets a test assert the exact bytes the
        # production stack put on the wire without re-implementing the
        # command builders.
        self.capture_civ_frames: bool = capture_civ_frames
        self.civ_frames_received: list[bytes] = []
        # Opt-in CI-V refusal hook (default off, additive): (cmd, sub) pairs the
        # simulator answers with a bare NAK (0xFA) instead of dispatching — a
        # radio that refuses a command it would normally take. An entry
        # ``(cmd, None)`` refuses every frame with that command byte; an entry
        # ``(cmd, sub)`` refuses only frames whose first payload byte is
        # ``sub``. The refused frame's state effect never applies.
        self.refused_civ_commands: set[tuple[int, int | None]] = set()
        self.refused_count: int = 0  # CI-V frames answered NAK by the hook
        # Scheduled events (reboot / keepalive silence / drop_session) —
        # absolute deadlines, 0.0 = inactive.
        self._down_until: float = 0.0  # reboot: radio "powered off" until then
        self._busy_until: float = 0.0  # post-reboot busy-reject window
        self._keepalive_silent_until: float = 0.0  # pings received, not answered
        # CI-V standby scenario (test knob, NOT recorded radio behaviour):
        # while True the CI-V port emits nothing for CI-V DATA (no echo, no
        # reply, no retransmit requests) but still advances sequence
        # tracking and consumes held gap packets without processing them,
        # so turning standby off resumes cleanly; discovery,
        # pings and OpenClose still work, and the control port is unaffected.
        self._civ_standby: bool = False
        # Power-off notice window (recording w2): after the 0x90 notice the
        # radio still answers discovery/pings until this deadline, then goes dark.
        self._poweroff_notice_until: float = 0.0
        self.reboot_count: int = 0  # number of simulated reboots started
        # Retransmit-request packets sent on the CI-V port so far (each
        # round sends two while a sequence gap is open).
        self.civ_retransmit_requests: int = 0
        # What each round asked for: ``(monotonic time, runs)`` per
        # contiguous missing run, bounded to the last 4096 rounds. Lets a
        # test tell a client that skips requests apart from the radio pace.
        self.civ_retransmit_request_log: deque[
            tuple[float, tuple[tuple[int, int], ...]]
        ] = deque(maxlen=4096)
        # Orphan CI-V stream state (MOR-3418 slice G): ``(next_expected,
        # missing, died_at)`` kept when a held session's hold expires on
        # its own. None when the knob is off, after a clean end, a reboot,
        # consumption by one opener, or the unclaimed expiry.
        self.civ_state_survives_unclean_end: bool = civ_state_survives_unclean_end
        self.orphan_civ_state_expiry_s: float = orphan_civ_state_expiry_s
        self.civ_inherited_fills_per_round: int = civ_inherited_fills_per_round
        self.civ_inherited_drop_unanswered_rounds: int = (
            civ_inherited_drop_unanswered_rounds
        )
        self._civ_orphan: tuple[int, frozenset[int], float] | None = None
        # Monotonic time of the held session's last keepalive refresh
        # (claim or live-token renewal): the death stamp of an orphan.
        self._keepalive_last_refresh: float = 0.0
        # Tokens issued before a reboot or drop; control packets carrying one
        # are ignored.
        self._retired_tokens: set[int] = set()
        self._rng = random.Random(0xC0FFEE)

        # Autonomous state + unsolicited CI-V streaming
        self.unsolicited_civ: bool = unsolicited_civ
        self.unsolicited_interval_s: float = unsolicited_interval_s
        self._unsolicited_task: asyncio.Task[None] | None = None
        # Last CI-V client (set on OpenClose(open)) — target for unsolicited frames.
        self._civ_owner_addr: tuple[str, int] | None = None
        self._civ_owner_id: int = 0
        # Post-event fence (reboot / drop_session): CI-V data is ignored until
        # a fresh OpenClose(open) — and only the client whose fresh login's
        # own conninfo was accepted after the event may lift the fence with
        # its own open (MOR-3315: a stale client's OpenClose(open) gets no
        # stream).
        self._fence_civ_until_open: bool = False
        # Fence permission is per client, not a shared flag: after the event
        # only the client whose fresh login's own conninfo request was
        # accepted (CI-V port returned) may lift the fence
        # with its own OpenClose(open) — identified by that conninfo's IP
        # plus the CI-V port it declared (the u32 at 0x7C, big-endian —
        # ``core/auth.py``: ``build_conninfo_packet`` packs
        # ``civ_local_port`` there, the bound local port the production
        # client opens its CI-V stream from in
        # ``runtime/_control_phase.py``: ``_connect_once`` /
        # ``soft_reconnect``). A conninfo from any other socket, or one the
        # radio busy-rejects, leaves the permission where it was (MOR-3340).
        # See ``_fence_open_allowed_for`` for the fallback when no port is
        # declared.
        self._fence_login_ip: str | None = None
        self._fence_login_civ_port: int | None = None
        # The reboot fence also ignores CI-V-port pings; the drop_session fence
        # does not (recorded: the CI-V port keeps answering pings after a drop).
        self._fence_blocks_pings: bool = False
        # (addr, sender_id) of the last accepted login — target for the
        # unsolicited conninfo notice when no session is held.
        self._last_login: tuple[tuple[str, int], int] | None = None
        self.unsolicited_sent: int = 0  # count of autonomous frames emitted
        self.scope_frames_sent: int = 0  # wave frames emitted (scope_stream)
        # CI-V sequence-gap tracking, per CI-V client keyed by
        # ((host, port), sender_id): missing client numbers stall
        # tracked-packet processing until the fill arrives, while the radio
        # repeats the retransmit request twice every 0.1 s (recorded IC-7610
        # behaviour, ``tests/fixtures/ic7610/retransmit_gaps.jsonl``).
        # Tracker lifetime is the radio session, not the OpenClose: a
        # re-sent OpenClose(open) never restarts tracking (MOR-3361); the
        # map is cleared only by a new accepted login, a conninfo claiming
        # a new session, ``drop_session`` or reboot.
        self._civ_gap: dict[tuple[tuple[str, int], int], _CivGapState] = {}
        self._civ_gap_task: asyncio.Task[None] | None = None
        # The retransmit-request pacing reads only this clock; every other
        # ``time.monotonic`` use in the simulator keeps the wall clock, so
        # the default is behaviour-identical to a hardcoded call.
        self.clock: Callable[[], float] = clock if clock is not None else time.monotonic
        # Counters / observability for simulator-fidelity tests
        self.busy_rejects: int = 0  # number of 0xFFFFFFFF rejects sent
        self.released_count: int = 0  # number of sessions freed (any path)
        self.last_release_reason: str | None = None
        self._login_count: int = 0  # accepted logins
        self._openclose_open_count: int = 0  # CI-V OpenClose(open) received
        self._audio_init()  # audio-port state (RX streamer, TX sink)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind all three UDP servers and make the ports available."""
        loop = asyncio.get_running_loop()

        ctrl_transport, ctrl_proto = await loop.create_datagram_endpoint(
            lambda: _MockProtocol(self, "ctrl"),
            local_addr=(self._host, self._ctrl_port_hint),
        )
        self._ctrl_udp = ctrl_transport
        self._ctrl_proto = ctrl_proto
        sockname = ctrl_transport.get_extra_info("sockname")
        self._actual_ctrl_port = sockname[1]

        civ_transport, civ_proto = await loop.create_datagram_endpoint(
            lambda: _MockProtocol(self, "civ"),
            local_addr=(self._host, self._civ_port_hint),
        )
        self._civ_udp = civ_transport
        self._civ_proto = civ_proto
        sockname = civ_transport.get_extra_info("sockname")
        self._actual_civ_port = sockname[1]

        # The audio listener binds after the CI-V port so it can try the
        # adjacent port (the status reply advertises the bound port).
        # Off by default: nothing listens and nothing answers, while the
        # status reply still advertises the dummy port as before.
        if self.enable_audio:
            await self._audio_start()

        if self.unsolicited_civ:
            self._unsolicited_task = asyncio.get_running_loop().create_task(
                self._unsolicited_loop()
            )
        if self._scope_stream:
            self._scope_task = asyncio.get_running_loop().create_task(
                self._scope_loop()
            )
        self._civ_gap_task = asyncio.get_running_loop().create_task(
            self._civ_gap_loop()
        )

        logger.debug(
            "MockIcomRadio started — ctrl=%d civ=%d audio=%d",
            self._actual_ctrl_port,
            self._actual_civ_port,
            self._actual_audio_port,
        )

    async def stop(self) -> None:
        """Close all UDP servers and tear down background tasks/timers."""
        await self._audio_stop()
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None
        if self._unsolicited_task is not None:
            self._unsolicited_task.cancel()
            try:
                await self._unsolicited_task
            except (asyncio.CancelledError, Exception):
                pass
            self._unsolicited_task = None
        if self._scope_task is not None:
            self._scope_task.cancel()
            try:
                await self._scope_task
            except (asyncio.CancelledError, Exception):
                pass
            self._scope_task = None
        self._scope_next_due.clear()
        if self._civ_gap_task is not None:
            self._civ_gap_task.cancel()
            try:
                await self._civ_gap_task
            except (asyncio.CancelledError, Exception):
                pass
            self._civ_gap_task = None
        if self._ctrl_udp is not None:
            self._ctrl_udp.close()
            self._ctrl_udp = None
        if self._civ_udp is not None:
            self._civ_udp.close()
            self._civ_udp = None
        self._civ_proto = None
        self._ctrl_proto = None
        logger.debug("MockIcomRadio stopped")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def control_port(self) -> int:
        """Actual bound control port number."""
        return self._actual_ctrl_port

    @property
    def civ_port(self) -> int:
        """Actual bound CI-V port number."""
        return self._actual_civ_port

    @property
    def session_held(self) -> bool:
        """True when a session is currently owned (single-owner mode)."""
        return self._owner is not None

    @property
    def owner(self) -> tuple[tuple[str, int], int] | None:
        """Current owner identity ``((host, port), my_id)`` or ``None``."""
        return self._owner

    @property
    def login_count(self) -> int:
        """Number of accepted logins (auth-fail responses are not counted)."""
        return self._login_count

    @property
    def openclose_open_count(self) -> int:
        """CI-V OpenClose(open) packets received, accepted or fenced."""
        return self._openclose_open_count

    @property
    def civ_gap_open(self) -> bool:
        """True while any client sequence number is missing on the CI-V port."""
        return any(t.missing for t in self._civ_gap.values())

    @property
    def ptt_on(self) -> bool:
        """Simulated PTT state (only meaningful with ``stateful_ptt=True``)."""
        return self._ptt_state

    def _set_simulated_ptt(self, on: bool, *, wire_set: bool) -> None:
        """Flip the simulated PTT state and record the transition."""
        if wire_set:
            self.ptt_set_writes.append((time.monotonic(), on))
        if on != self._ptt_state:
            self.ptt_transitions.append((time.monotonic(), on))
        self._ptt_state = on

    # ------------------------------------------------------------------
    # Session-lifecycle test knobs
    # ------------------------------------------------------------------

    def force_civ_unavailable_for(self, seconds: float) -> None:
        """Force ``civ_port=0`` (not-ready, NOT a busy reject) for a window.

        This drives the client's CONNECTING -> COOLDOWN -> CONNECTING path
        without emitting a ``0xFFFFFFFF`` previous-session-active reject. After
        the window the next conninfo gets a real CI-V port.
        """
        self._civ_unavailable_until = time.monotonic() + max(0.0, seconds)

    def clear_civ_unavailable(self) -> None:
        """Immediately make the CI-V port available again."""
        self._civ_unavailable_until = 0.0

    def stall_for(self, seconds: float) -> None:
        """Stop emitting unsolicited CI-V for ``seconds`` to trip the watchdog."""
        self._stall_until = time.monotonic() + max(0.0, seconds)

    def free_session(self, reason: str = "manual") -> None:
        """Release the held session immediately (test helper).

        Like every other session end (owner token-remove, disconnect,
        OpenClose close, hold expiry, ``drop_session``, reboot) this also
        ends the token epoch, so later renewals with the old token are
        rejected.
        """
        self._release_session(reason)
        self._retire_token()

    def civ_standby(self, on: bool) -> None:
        """Turn the CI-V standby scenario on or off (test helper).

        While on, the CI-V port emits nothing for CI-V DATA — no echo, no
        reply, no retransmit requests — but still advances sequence
        tracking for tracked packets, and the gap drain still consumes
        held packets without processing them, so turning standby off
        resumes cleanly. CI-V-port pings stay answered, and the control
        port keeps answering pings and renewals. This is a simulator
        scenario for tests, NOT recorded radio behaviour — network
        standby is unrecorded.
        """
        self._civ_standby = bool(on)

    def clear_captured_civ_frames(self) -> None:
        """Drop all CI-V frames recorded so far (test helper)."""
        self.civ_frames_received.clear()

    # ------------------------------------------------------------------
    # Scheduled events (reboot / keepalive silence / drop_session)
    # ------------------------------------------------------------------

    def reboot(
        self,
        *,
        at: float,
        down_for: float,
        busy_for: float = 0.5,
        notice: bool = False,
    ) -> None:
        """Schedule a simulated radio reboot.

        At ``at`` seconds from now the radio "powers off" for ``down_for``
        seconds: it answers nothing on either port, forgets the held session
        (``free_session`` path), drops the CI-V owner, rotates its token and
        restarts CI-V sequence numbering. Until the client whose fresh
        login's own conninfo is accepted completes an OpenClose(open) from
        the CI-V port that conninfo declared (see
        ``_fence_open_allowed_for``), CI-V data and CI-V-port pings from
        pre-reboot clients are ignored — a rebooted radio does
        not serve a stale client that never logs in again (MOR-3315);
        control-port pings are still answered. Token
        packets (0x40) other than renewals, and conninfo (0x90),
        carrying a pre-reboot token are ignored as well; renewals
        with a retired token are answered with the recorded reject;
        ``docs/internals/protocol.md`` does not describe what an IC-7610 does
        with a stale token, so "ignored" is this simulator's assumption, not
        a recorded radio behaviour. It then answers conninfo
        requests with a previous-session-active busy reject (``0xFFFFFFFF``)
        for ``busy_for`` seconds — modelled on the single-owner reject in
        ``_conninfo_status`` — after which conninfo is served normally.

        With ``notice=True`` the recorded power-off order (IC-7610 recording
        w2) is replayed before going dark: first the unsolicited 0x90
        conninfo notice (busy=0, "session free") goes to the client's control
        address, then for ~0.02 s the radio still answers
        AreYouThere/AreYouReady on both ports and pings on the control port
        — CI-V-port pings from the pre-reboot client stay fenced, like all
        its CI-V traffic after a reboot — then total silence. The default
        ``False`` keeps the pre-notice behaviour for existing tests.

        Must be called from inside the running event loop.
        """
        loop = asyncio.get_running_loop()
        loop.call_later(
            max(0.0, at),
            self._begin_reboot,
            max(0.0, down_for),
            max(0.0, busy_for),
            notice,
        )

    def _begin_reboot(
        self, down_for: float, busy_for: float, notice: bool = False
    ) -> None:
        if notice:
            # Recorded power-off order (recording w2): the 0x90 notice goes
            # out first, then a ~0.02 s window in which discovery and pings
            # are still answered (gated in ``_on_packet``), then darkness.
            target = self._owner or self._last_login
            if target is not None:
                self._send_conninfo_notice(*target)
            self._poweroff_notice_until = time.monotonic() + _POWER_OFF_NOTICE_WINDOW_S
            asyncio.get_running_loop().call_later(
                _POWER_OFF_NOTICE_WINDOW_S, self._begin_dark, down_for, busy_for
            )
        else:
            self._begin_dark(down_for, busy_for)
        self._release_session("reboot")
        self._civ_owner_addr = None
        self._civ_owner_id = 0
        self._civ_gap.clear()  # the stream epoch ends with the reboot
        self._retire_token()
        self._civ_seq = 1
        # Shut out the pre-reboot clients until the one that logs in afresh
        # opens from the CI-V port its conninfo declared — a rebooted radio
        # does not serve stale clients.
        self._fence_civ_until_open = True
        self._fence_blocks_pings = True
        self._fence_login_ip = None
        self._fence_login_civ_port = None
        self.reboot_count += 1
        logger.debug("Mock: reboot — down %.2fs, then busy %.2fs", down_for, busy_for)

    def _begin_dark(self, down_for: float, busy_for: float) -> None:
        """Start the powered-off window (and the busy window after it)."""
        now = time.monotonic()
        self._down_until = now + down_for
        self._busy_until = self._down_until + busy_for

    def drop_session(self, *, at: float, notice: bool = True) -> None:
        """Schedule a radio-initiated LAN session drop (recorded IC-7610
        behaviour, bench capture MOR-2625/2627).

        At ``at`` seconds from now the radio frees the held session (the same
        ``_release_session`` path ``reboot`` uses), rotates its token, stops
        unsolicited CI-V and fences the dropped client off the CI-V data
        stream: CI-V DATA from that client is ignored while pings on both
        ports are still answered (recorded: the CI-V port keeps answering
        pings) — the fence lifts only for the client whose fresh login's
        own conninfo is accepted, once its OpenClose(open) arrives from the
        CI-V port that conninfo declared (see ``_fence_open_allowed_for``). With
        ``notice=True`` one unsolicited 0x90 conninfo notice (busy=0,
        "session free"; recorded layout in ``_conninfo_notice``) is sent to
        the dropped client's control address ~0.1 s after the drop. The
        notice targets the current session owner, or the last accepted login
        when no session is held; with neither, no notice is sent.

        Must be called from inside the running event loop.
        """
        loop = asyncio.get_running_loop()
        loop.call_later(max(0.0, at), self._begin_drop_session, notice)

    def _begin_drop_session(self, notice: bool) -> None:
        target = self._owner or self._last_login
        self._release_session("drop_session")
        self._civ_owner_addr = None
        self._civ_owner_id = 0
        self._civ_gap.clear()  # the stream epoch ends with the drop
        self._retire_token()
        self._fence_civ_until_open = True
        self._fence_blocks_pings = False  # recorded: pings keep being answered
        # The fence permission resets with the event: only an accepted
        # conninfo from a fresh login re-grants it, and only to that client.
        self._fence_login_ip = None
        self._fence_login_civ_port = None
        if notice and target is not None:
            asyncio.get_running_loop().call_later(
                _DROP_NOTICE_DELAY_S, self._send_conninfo_notice, *target
            )
        logger.debug("Mock: session dropped (notice=%s)", notice)

    def keepalive_silence(self, *, at: float, for_seconds: float) -> None:
        """Schedule a window where keepalives are received but never answered.

        From ``at`` seconds from now, for ``for_seconds`` seconds, ping
        requests on both ports get no reply; control-port pings still
        refresh the held session, and every other packet is answered as
        usual.

        Must be called from inside the running event loop.
        """
        loop = asyncio.get_running_loop()
        loop.call_later(
            max(0.0, at), self._begin_keepalive_silence, max(0.0, for_seconds)
        )

    def _begin_keepalive_silence(self, for_seconds: float) -> None:
        self._keepalive_silent_until = time.monotonic() + for_seconds
        logger.debug("Mock: keepalive silence for %.2fs", for_seconds)

    # ------------------------------------------------------------------
    # Session ownership (single-owner model)
    # ------------------------------------------------------------------

    def _release_session(self, reason: str) -> None:
        """Free the held session and cancel its keepalive timer.

        Ending the radio session also ends the audio stream epoch (every
        path through here: token-remove/logout, CI-V OpenClose(close),
        keepalive timeout, ``drop_session``, reboot).
        """
        if reason != "keepalive_timeout":
            # Any decided end drops a kept orphan: clean logout, explicit
            # ``free_session``, ``drop_session``/reboot. Only the hold
            # expiring on its own keeps it (see ``_expire_hold``).
            self._civ_orphan = None
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None
        if self._scope_stream:
            # A session end stops the scope stream by switching wave
            # output off (simulator choice, unrecorded): a new session
            # starts silent until it enables output itself.
            self._scope_wave_output = 0x00
            self._scope_next_due.clear()
        self._audio_end_session(reason)
        if self._owner is not None:
            logger.debug("Mock: session released (%s) owner=%s", reason, self._owner)
            self._owner = None
            self.released_count += 1
            self.last_release_reason = reason
            # ptt_on_session_loss (opt-in, stateful PTT only): "release"
            # unkeys the simulated radio at the moment the session ends.
            # The real IC-7610 behaviour is UNRECORDED — TX is never
            # actuated on the bench — which is why the TX-under-fault
            # matrix covers both this and "hold".
            if (
                self._stateful_ptt
                and self._ptt_on_session_loss == "release"
                and self._ptt_state
            ):
                self._set_simulated_ptt(False, wire_set=False)

    def _retire_token(self) -> None:
        """End the current token epoch: retire the value, issue a fresh one.

        Renewals carrying the retired value are answered with the recorded
        reject instead of being dropped. Every end of a held session starts
        a new epoch — the session owner's token-remove, control disconnect,
        the CI-V stream owner's OpenClose close, hold expiry,
        ``drop_session`` and reboot — while a bare login never does.
        """
        self._retired_tokens.add(self.token)
        self.token = (self.token + 0x9E3779B9) & 0xFFFFFFFF

    def _renewal_live(self, token: int) -> bool:
        """True when a renewal token names the current token epoch.

        Every end of a held session on the wire retires the token (see
        ``_retire_token``), so equality with the current token is the whole
        check — no session-held guard: the rotation in ``_expire_hold``
        already rejects post-expiry renewals.
        """
        return token == self.token

    def _arm_keepalive(self) -> None:
        """(Re)start the keepalive auto-release timer for the held session."""
        if self._owner is None:
            return
        self._keepalive_last_refresh = time.monotonic()
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
        loop = asyncio.get_running_loop()
        self._keepalive_timer = loop.call_later(
            self.keepalive_hold_s,
            self._expire_hold,
        )

    def _expire_hold(self) -> None:
        """Release an un-renewed session and end its token epoch."""
        if self._owner is None:
            return
        self._keep_civ_orphan()
        self._release_session("keepalive_timeout")
        self._retire_token()

    def _keep_civ_orphan(self) -> None:
        """Keep the dying session's stream state as orphan state.

        Copies the stream owner's tracker (frontier plus gap list),
        stamped with the hold's last refresh. Only with the knob; a
        ``drop_session`` end keeps nothing (unrecorded). The live map is
        left alone: the orphan is a copy, not a move.
        """
        if not self.civ_state_survives_unclean_end:
            return
        tracker = None
        if self._civ_owner_addr is not None:
            tracker = self._civ_gap.get((self._civ_owner_addr, self._civ_owner_id))
        if tracker is None or tracker.next_expected is None:
            self._civ_orphan = None
            return
        self._civ_orphan = (
            tracker.next_expected,
            frozenset(tracker.missing),
            self._keepalive_last_refresh,
        )

    def _seed_tracker_from_orphan(self, key: tuple[tuple[str, int], int]) -> None:
        """Seed a new CI-V identity's tracker from the orphan state.

        A known identity keeps its numbering (MOR-3361). An unknown one
        opening within the expiry inherits frontier and gap list and
        consumes the orphan; past the expiry it starts clean.
        """
        if key in self._civ_gap:
            return
        orphan = self._civ_orphan
        if (
            orphan is not None
            and self.civ_state_survives_unclean_end
            and time.monotonic() - orphan[2] <= self.orphan_civ_state_expiry_s
        ):
            tracker = _CivGapState(key[0], key[1])
            tracker.next_expected = orphan[0]
            tracker.missing = set(orphan[1])
            tracker.inherited = True
            tracker.inherited_frontier = orphan[0]
            self._civ_gap[key] = tracker
            self._civ_orphan = None
            return
        self._civ_orphan = None
        self._civ_gap_ensure(key)

    def _claim_session(self, owner: tuple[tuple[str, int], int]) -> None:
        """Mark ``owner`` as the holder and arm the keepalive timer."""
        if self._owner != owner:
            # A newly claimed session starts sequence tracking over: the
            # previous session's CI-V numbering no longer applies
            # (MOR-3361). A refresh by the current holder keeps it — and
            # with it the audio stream (the recorded soft-reconnect shape).
            self._civ_gap.clear()
            self._audio_end_session("session_reclaimed")
            # A newly claimed session has no CI-V stream owner until its
            # own OpenClose(open): a close from the previous session's
            # stream identity must not end it.
            self._civ_owner_addr = None
            self._civ_owner_id = 0
        self._owner = owner
        self._arm_keepalive()

    def _refresh_keepalive(self, owner: tuple[tuple[str, int], int]) -> None:
        """Re-arm the keepalive timer if ``owner`` is the current holder."""
        if self.single_owner and self._owner == owner:
            self._arm_keepalive()

    def _conninfo_status(self, addr: tuple[str, int], sender_id: int) -> bytes:
        """Ownership-aware status reply for a conninfo (data-port request).

        Single-owner semantics:
          * a FOREIGN owner asking while a session is held -> busy reject
            (civ_port=0, error=0xFFFFFFFF);
          * the CI-V port forced unavailable -> not-ready (civ_port=0, error=0);
          * otherwise claim the session for this owner and return the CI-V port.

        When ``single_owner`` is False this preserves the legacy behaviour
        (always return the real CI-V port) so existing tests stay green.
        """
        # Post-reboot busy window (applies in both ownership modes): the radio
        # is up but not ready to hold a session yet.
        if time.monotonic() < self._busy_until:
            self.busy_rejects += 1
            return self._status_response(
                sender_id, civ_port=0, error=_ERR_PREV_SESSION_ACTIVE
            )

        if not self.single_owner:
            return self._status_response(sender_id)

        owner = (addr, sender_id)

        # Foreign held session -> previous-session-active reject.
        if self._owner is not None and self._owner != owner:
            self.busy_rejects += 1
            return self._status_response(
                sender_id, civ_port=0, error=_ERR_PREV_SESSION_ACTIVE
            )

        # CI-V port forced not-ready (cooldown driver; distinct from busy).
        if time.monotonic() < self._civ_unavailable_until:
            return self._status_response(sender_id, civ_port=0, error=_ERR_OK)

        # Available: claim (or refresh) the session for this owner.
        self._claim_session(owner)
        return self._status_response(sender_id)

    # ------------------------------------------------------------------
    # State setters (for test setup)
    # ------------------------------------------------------------------

    def set_frequency(self, hz: int) -> None:
        """Set the radio's current frequency (Hz)."""
        self._frequency = hz

    def set_mode(self, mode: int) -> None:
        """Set mode byte (e.g. 0x01=USB, 0x00=LSB)."""
        self._mode = mode

    def set_power(self, level: int) -> None:
        """Set RF power level (0-255)."""
        self._power = level

    def set_s_meter(self, value: int) -> None:
        """Set S-meter readback value (0-255)."""
        self._s_meter = value

    def set_swr(self, value: int) -> None:
        """Set SWR meter readback value (0-255)."""
        self._swr = value

    def set_attenuator(self, db: int) -> None:
        """Set attenuator dB value (0, 3, 6, …, 45)."""
        self._attenuator = db

    def set_preamp(self, level: int) -> None:
        """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        self._preamp = level

    def set_digisel(self, on: bool) -> None:
        """Set DIGI-SEL (IP+) state."""
        self._digisel = 1 if on else 0

    # ------------------------------------------------------------------
    # Packet dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _port_of(label: str) -> "Literal['control', 'civ']":
        return "control" if label == "ctrl" else "civ"

    def _send(
        self,
        proto: _MockProtocol,
        pkt: bytes,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        """Single outbound funnel for every radio→client datagram.

        All handler replies go through here. With no ``fault_plan`` (or a
        no-op rule for this stream) this is a plain ``proto.send`` — the
        pre-plan behaviour, byte-for-byte. With a plan, the rule for
        (port, ``to_client``) decides drop/delay/duplicate/corrupt/truncate/
        reorder per packet; delayed emissions are scheduled on the loop.
        """
        if time.monotonic() < self._down_until:
            return  # reboot window: the radio is "powered off"
        plan = self.fault_plan
        port = self._port_of(label)
        if plan is None or plan.is_noop(port, "to_client"):
            proto.send(pkt, addr)
            return
        planned = plan.decide(port, "to_client", pkt)
        if planned is None:
            # Held in the plan's reorder buffer: arm the flush deadline so a
            # short burst is emitted even if the window never fills.
            self._arm_reorder_flush(plan, port, "to_client", proto, addr, label)
            return
        self._deliver_planned(planned, "to_client", proto, addr, label)

    def _deliver_planned(
        self,
        planned: list[tuple[float, bytes]],
        direction: Direction,
        proto: _MockProtocol,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        """Emit the plan's emissions for one packet.

        Delayed emissions re-check the reboot down-window when they fire: a
        packet the plan delayed into a reboot dies with the radio "off"
        instead of being delivered by a powered-down radio.
        """
        loop = asyncio.get_running_loop()
        for delay, payload in planned:
            if delay > 0.0:
                loop.call_later(
                    delay,
                    self._deliver_planned,
                    [(0.0, payload)],
                    direction,
                    proto,
                    addr,
                    label,
                )
            elif time.monotonic() >= self._down_until:
                if direction == "to_client":
                    proto.send(payload, addr)
                else:
                    self._receive(payload, addr, label, proto)

    def _arm_reorder_flush(
        self,
        plan: FaultPlan,
        port: Port,
        direction: Direction,
        proto: _MockProtocol,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        """Schedule the reorder deadline for a stream with a held burst."""
        due = plan.flush_due_in(port, direction)
        if due is not None:
            asyncio.get_running_loop().call_later(
                due, self._flush_reorder, plan, port, direction, proto, addr, label
            )

    def _flush_reorder(
        self,
        plan: FaultPlan,
        port: Port,
        direction: Direction,
        proto: _MockProtocol,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        """Reorder deadline fired: release the held burst through the gates."""
        self._deliver_planned(
            plan.flush(port, direction), direction, proto, addr, label
        )

    def _on_packet(
        self,
        data: bytes,
        addr: tuple[str, int],
        label: str,
        proto: _MockProtocol,
    ) -> None:
        """Entry point for all incoming packets.

        Inbound fault-plan hook: the rule for (port, ``from_client``) can
        drop, delay, duplicate, corrupt, truncate or reorder the datagram
        before the simulator parses it. With no plan (or a no-op rule) the
        packet goes straight to ``_receive`` — the pre-plan behaviour.
        """
        if time.monotonic() < self._down_until:
            return  # reboot window: the radio is "powered off"
        if (
            time.monotonic() < self._poweroff_notice_until
            and not self._poweroff_notice_packet(data)
        ):
            # Recorded power-off window (recording w2): right after the 0x90
            # notice only discovery and pings are still answered.
            return
        if label == "audio":
            # The fault plan covers control/CI-V only (``faults.py``:
            # ``Port``); audio packets go straight to the handler, which
            # applies its own silence gate.
            self._receive(data, addr, label, proto)
            return
        plan = self.fault_plan
        port = self._port_of(label)
        if plan is not None and not plan.is_noop(port, "from_client"):
            planned = plan.decide(port, "from_client", data)
            if planned is None:
                # Held in the reorder buffer: arm the flush deadline (idle).
                self._arm_reorder_flush(plan, port, "from_client", proto, addr, label)
                return
            self._deliver_planned(planned, "from_client", proto, addr, label)
            return
        self._receive(data, addr, label, proto)

    @staticmethod
    def _poweroff_notice_packet(data: bytes) -> bool:
        """True for the packets still answered in the power-off notice window.

        Recorded (IC-7610 recording w2): after the 0x90 notice the radio
        answers AreYouThere with I-Am-Here, AreYouReady with I-Am-Ready and
        one more ping reply before going dark.
        """
        n = len(data)
        if n < _HEADER_SIZE:
            return False
        ptype = struct.unpack_from("<H", data, 4)[0]
        if n == _HEADER_SIZE and ptype in (_PT_ARE_YOU_THERE, _PT_ARE_YOU_READY):
            return True
        return n == _PING_SIZE and ptype == _PT_PING

    def _receive(
        self,
        data: bytes,
        addr: tuple[str, int],
        label: str,
        proto: _MockProtocol,
    ) -> None:
        """Parse and dispatch one inbound datagram (post fault-plan)."""
        if len(data) < _HEADER_SIZE:
            return
        ptype = struct.unpack_from("<H", data, 4)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]
        seq = struct.unpack_from("<H", data, 6)[0]

        if self.response_delay > 0:
            asyncio.get_running_loop().call_later(
                self.response_delay,
                lambda: self._dispatch(data, addr, ptype, sender_id, seq, label, proto),
            )
        else:
            self._dispatch(data, addr, ptype, sender_id, seq, label, proto)

    def _dispatch(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        label: str,
        proto: _MockProtocol,
    ) -> None:
        if label == "ctrl":
            self._ctrl_client_id = sender_id
            self._handle_ctrl(data, addr, ptype, sender_id, seq, proto)
        elif label == "civ":
            self._civ_client_id = sender_id
            self._handle_civ(data, addr, ptype, sender_id, seq, proto)
        elif label == "audio":
            self._handle_audio_packet(data, addr, ptype, sender_id, seq, proto)

    # ------------------------------------------------------------------
    # Control port
    # ------------------------------------------------------------------

    def _handle_ctrl(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        proto: _MockProtocol,
    ) -> None:
        n = len(data)

        # Token renewal (0x40, requesttype=0x05): answered even when the
        # token names no live session — 00000000 for the current token, the
        # recorded ffffffff reject otherwise, exactly one reply. The
        # keepalive refresh is token-keyed: a renewal carrying the live
        # token extends the hold whatever its source (MOR-3349: the recorded
        # fact is about the token, not the socket). Renewal traffic from
        # the holder itself also extends the hold, as before.
        if n == 0x40 and data[0x15] == _TOKEN_RENEW:
            token = struct.unpack_from("<I", data, 0x1C)[0]
            if self._renewal_live(token):
                self._arm_keepalive()
                response = _ERR_OK
            else:
                self._refresh_keepalive((addr, sender_id))
                response = _ERR_TOKEN_REJECT
            self._send(
                proto, self._token_renew_reply(data, sender_id, response), addr, "ctrl"
            )
            return

        # Token packets (other than renewals, answered above) and conninfo
        # carrying a retired token: ignored.
        if n in (0x40, 0x90) and struct.unpack_from("<I", data, 0x1C)[0] in (
            self._retired_tokens
        ):
            return

        # Are You There → I Am Here
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_THERE:
            self._send(proto, self._ctrl_pkt(_PT_I_AM_HERE, 0, sender_id), addr, "ctrl")
            return

        # Are You Ready → echo Are You Ready
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_READY:
            self._send(
                proto, self._ctrl_pkt(_PT_ARE_YOU_READY, 0, sender_id), addr, "ctrl"
            )
            return

        # Ping request → Ping reply (refreshes the keepalive for the owner)
        if n == _PING_SIZE and ptype == _PT_PING and data[0x10] == 0x00:
            self._refresh_keepalive((addr, sender_id))
            if time.monotonic() < self._keepalive_silent_until:
                return  # keepalive_silence window: receive but never reply
            self._send(proto, self._ping_reply(data, sender_id), addr, "ctrl")
            return

        # Login (0x80 bytes)
        if n == 0x80:
            # A login never starts a new token epoch by itself: the previous
            # token is retired only when the previous session actually ends
            # (owner token-remove, disconnect, OpenClose close, hold expiry,
            # drop_session, reboot) — never at login receipt. The radio
            # busy-rejects a second client while a session is held instead
            # of retiring the holder on the second login.
            self._send(proto, self._login_response(data, sender_id), addr, "ctrl")
            if not self.auth_fail:
                self._login_count += 1
                self._last_login = (addr, sender_id)
                # An accepted login starts a new radio session: past
                # CI-V numbering no longer applies (MOR-3361).
                self._civ_gap.clear()
                # The fence permission is NOT granted here: it is recorded
                # when this login's own conninfo request is accepted below
                # (MOR-3340) — otherwise a second login whose conninfo is
                # busy-rejected would still steal the permission from the
                # session owner.
            return

        # Token ack (0x40 bytes, requesttype=0x02) → send GUID conninfo
        if n == 0x40 and data[0x15] == _TOKEN_ACK:
            self._refresh_keepalive((addr, sender_id))
            self._send(proto, self._guid_conninfo(sender_id), addr, "ctrl")
            return

        # Token remove (0x40 bytes, requesttype=0x01) → graceful close: the
        # owner frees the session now and ends the token epoch, so later
        # renewals with it are rejected. In single-owner mode a remove from
        # any other source is a no-op: it neither frees nor retires. In
        # default mode no owner is tracked, so a remove retires only when
        # it carries the live token — a remove naming no session is a
        # no-op (the production client always sends its own token).
        if n == 0x40 and data[0x15] == _TOKEN_REMOVE:
            if self.single_owner:
                if self._owner == (addr, sender_id):
                    self._release_session("token_remove")
                    self._retire_token()
            else:
                # No owned session here (default mode): the token is the
                # credential, not the sender id — the production client
                # derives each socket's id from its local port
                # (``core/transport.py``: ``my_id``), so its control and
                # audio ids differ and a sender-id match never fires for
                # it. A remove carrying the live token retires the epoch
                # and ends the audio stream with it; a remove naming no
                # session is a no-op (main's rule from #4142).
                if struct.unpack_from("<I", data, 0x1C)[0] == self.token:
                    self._retire_token()
                    self._audio_end_session("token_remove")
            return

        # Client conninfo (0x90 bytes, requesttype=0x03) → status with CI-V port
        if n == 0x90 and data[0x15] == 0x03:
            own_conninfo = (addr, sender_id) == self._last_login
            declared_port = (
                struct.unpack_from(">I", data, 0x7C)[0] if own_conninfo else None
            )
            reply = self._conninfo_status(addr, sender_id)
            if own_conninfo and declared_port is not None and _status_accepted(reply):
                # The fresh login's own conninfo was accepted (CI-V port
                # returned): that client — and only that
                # client — may now lift the CI-V fence with its
                # OpenClose(open) (MOR-3315). A conninfo from any other
                # socket, or one that was busy-rejected, leaves the
                # permission where it was (MOR-3340).
                self._fence_login_ip = addr[0]
                self._fence_login_civ_port = declared_port
            self._send(proto, reply, addr, "ctrl")
            return

        # Disconnect → free a held session for this owner and end its token
        # epoch, so later renewals with it are rejected.
        if n == _HEADER_SIZE and ptype == _PT_DISCONNECT:
            if self.single_owner and self._owner == (addr, sender_id):
                self._release_session("disconnect")
                self._retire_token()
            elif not self.single_owner and (addr, sender_id) == self._last_login:
                # Default mode: a disconnect carries no token, so the last
                # login is the identity — the control disconnect of the
                # client that logged in ends the audio stream (the
                # production client sends one per socket on logout,
                # ``core/transport.py``: ``disconnect``). A disconnect from
                # any other identity changes nothing. This is a simulator
                # choice; the recorded rule is the live-token remove above.
                self._audio_end_session("disconnect")
            return

    # ------------------------------------------------------------------
    # CI-V port
    # ------------------------------------------------------------------

    def _civ_fenced(self, addr: tuple[str, int], sender_id: int) -> bool:
        """True when the post-event fence shuts this client out of the CI-V port.

        After a reboot or drop_session only the client that completed a fresh
        OpenClose(open) — tracked in ``_civ_owner_addr`` / ``_civ_owner_id`` —
        is served; CI-V data from anyone else is ignored. Whether the fence
        also ignores pings is decided by ``_fence_blocks_pings`` (True after a
        reboot, False after a drop).
        """
        return self._fence_civ_until_open and (addr, sender_id) != (
            self._civ_owner_addr,
            self._civ_owner_id,
        )

    def _fence_open_allowed_for(self, addr: tuple[str, int]) -> bool:
        """True when an OpenClose(open) from ``addr`` may lift the fence.

        Permission is per client: it belongs to the client whose fresh
        login's own conninfo request was accepted after the event
        (``_fence_login_ip`` / ``_fence_login_civ_port``). A declared port
        of 0 — the client reserved no local port — falls back to the login's
        IP alone, so a single-socket client that logs in afresh is still
        served (and, on a shared IP, any same-IP socket with it). No
        accepted conninfo since the event leaves every open fenced.
        """
        if self._fence_login_ip is None:
            return False  # no conninfo accepted since the event
        if addr[0] != self._fence_login_ip:
            return False
        if self._fence_login_civ_port == 0:
            return True  # no port declared → fall back to the login's IP
        return addr[1] == self._fence_login_civ_port

    def _handle_civ(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        proto: _MockProtocol,
    ) -> None:
        n = len(data)

        # Are You There → I Am Here
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_THERE:
            reply = self._handshake_reply(data, ptype, sender_id)
            assert reply is not None  # matched above
            self._send(proto, reply, addr, "civ")
            return

        # Are You Ready → echo
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_READY:
            reply = self._handshake_reply(data, ptype, sender_id)
            assert reply is not None  # matched above
            self._send(proto, reply, addr, "civ")
            return

        # Ping request → Ping reply
        if n == _PING_SIZE and ptype == _PT_PING and data[0x10] == 0x00:
            if self._civ_fenced(addr, sender_id) and self._fence_blocks_pings:
                return  # post-reboot: stale client until a fresh OpenClose(open)
            if time.monotonic() < self._keepalive_silent_until:
                return  # keepalive_silence window: receive but never reply
            reply = self._handshake_reply(data, ptype, sender_id)
            assert reply is not None  # matched above
            self._send(proto, reply, addr, "civ")
            return

        # OpenClose (0x16 bytes)
        #
        # Real radios typically begin streaming CI-V data soon after an OpenClose(open)
        # which makes the client "radio_ready" quickly. Our tests use startup readiness
        # checks (radio_ready), so we emit a tiny unsolicited CI-V frame on open to mark
        # the stream as active for mock-based integration tests.
        if n == 0x16:
            if data[0x15] == _OPENCLOSE_OPEN:  # open_stream
                self._openclose_open_count += 1
                if self._fence_civ_until_open and not self._fence_open_allowed_for(
                    addr
                ):
                    # After a reboot/drop the fence lifts only for the
                    # client whose fresh login's own conninfo was accepted
                    # (MOR-3315) — a stale client's open gets no stream
                    # unless the accepted client declared port 0, which
                    # falls back to the login's IP alone.
                    return
                self._civ_owner_addr = addr
                self._civ_owner_id = sender_id
                # The open consumes its header sequence number like any
                # tracked packet (the production client sends it through
                # ``send_tracked``), while sequence tracking itself
                # belongs to the radio session: a re-sent open never
                # restarts it (MOR-3361). A new identity instead inherits
                # the previous session's orphan state when one is kept
                # (MOR-3418 slice G).
                self._seed_tracker_from_orphan((addr, sender_id))
                if not self._civ_standby:
                    civ = self._civ_frame(to=0x00, frm=self._radio_addr, cmd=0x00)
                    self._send(proto, self._wrap_civ(civ, sender_id), addr, "civ")
                key = (addr, sender_id)
                if self._civ_gap_gate(
                    key, "openclose", data, addr, sender_id, seq, proto
                ):
                    # The drain runs in standby too: it consumes held
                    # packets without processing them (see
                    # ``_civ_gap_drain``) — only the marker above is
                    # suppressed.
                    self._civ_gap_drain(key, proto)
            elif data[0x15] == _OPENCLOSE_CLOSE:  # graceful data-port close
                # The CI-V stream owner's OpenClose(close) frees the single
                # held session immediately and ends its token epoch — the
                # only retirement under the production logout order (close,
                # then token-remove and disconnect, which find no owner and
                # are no-ops). The stream owner is the CI-V identity whose
                # OpenClose(open) was accepted for the held session — the
                # production client sends OpenClose from its CI-V socket,
                # never from the control socket that owns the session. A
                # close from any other CI-V identity is a no-op, like a
                # foreign token-remove: it neither frees nor retires.
                # Tracking outlives the close: it ends only with a new
                # session, drop_session or reboot (MOR-3361).
                if (
                    self.single_owner
                    and self._owner is not None
                    and (addr, sender_id) == (self._civ_owner_addr, self._civ_owner_id)
                ):
                    self._release_session("openclose_close")
                    self._retire_token()
                # The close is still a tracked packet: its number goes
                # through the gate (held and released in order like any
                # other), without any stream marker. The drain runs in
                # standby too (consume without processing).
                key = (addr, sender_id)
                if self._civ_gap_gate(
                    key, "openclose", data, addr, sender_id, seq, proto
                ):
                    self._civ_gap_drain(key, proto)
            return

        # Disconnect
        if n == _HEADER_SIZE and ptype == _PT_DISCONNECT:
            return

        # Idle keepalive (0x10 type-0): tracked for gap purposes, never
        # answered — like the recorded radio, which keeps its own cadence.
        # The drain runs in standby too (consume without processing).
        if n == _HEADER_SIZE and ptype == _PT_DATA:
            if self._civ_gap_gate(
                (addr, sender_id), "idle", data, addr, sender_id, seq, proto
            ):
                self._civ_gap_drain((addr, sender_id), proto)
            return

        # CI-V data (DATA type, larger than header)
        if ptype == _PT_DATA and n > _CIV_HEADER_SIZE:
            if self._civ_fenced(addr, sender_id):
                return  # post-reboot: stale client until a fresh OpenClose(open)
            key = (addr, sender_id)
            if self._civ_gap_gate(key, "data", data, addr, sender_id, seq, proto):
                # The LAN CI-V echo and the reply go out only when the
                # request is actually processed — held packets stay silent
                # until the fill releases them in sequence order. In
                # standby nothing is processed, but the drain still
                # consumes held packets (without echo or reply), so
                # resuming is clean.
                if not self._civ_standby:
                    self._handle_civ_data(data, addr, sender_id, proto)
                self._civ_gap_drain(key, proto)

    def _handle_civ_data(
        self,
        data: bytes,
        addr: tuple[str, int],
        sender_id: int,
        proto: _MockProtocol,
    ) -> None:
        """Parse CI-V frame from a DATA packet and generate a response."""
        datalen = struct.unpack_from("<H", data, 0x11)[0]
        end = _CIV_HEADER_SIZE + datalen
        if end > len(data):
            return
        civ = data[_CIV_HEADER_SIZE:end]

        if len(civ) < 6:
            return
        if civ[:2] != _CIV_PREAMBLE:
            return
        if civ[-1:] != _CIV_TERM:
            return

        to_addr = civ[2]
        from_addr = civ[3]
        cmd = civ[4]
        payload = civ[5:-1]

        # Only handle commands addressed to this radio
        if to_addr != self._radio_addr:
            return

        if self.capture_civ_frames:
            self.civ_frames_received.append(bytes(civ))

        if self.lan_civ_echo:
            # Recorded IC-7610 LAN behaviour: the request CI-V frame comes
            # back byte-identical immediately before the reply — including
            # requests answered with NAK (``civ_pairs.jsonl`` records CI-V
            # frames only). Through the same outbound funnel as the reply,
            # so a fault plan sees both packets.
            self._send(proto, data, addr, "civ")

        response_civ = self._dispatch_civ(cmd, payload, from_addr)
        if response_civ is not None:
            self._send(proto, self._wrap_civ(response_civ, sender_id), addr, "civ")

    # ------------------------------------------------------------------
    # CI-V sequence-gap tracking (recorded IC-7610 behaviour)
    # ------------------------------------------------------------------

    def _civ_gap_ensure(self, key: tuple[tuple[str, int], int]) -> None:
        """Ensure a gap tracker exists for one CI-V client (OpenClose(open)).

        Never wipes an existing tracker: a re-sent open within the same
        radio session keeps the numbering (MOR-3361). The open itself is
        the first tracked packet: its header sequence number is consumed
        by :meth:`_civ_gap_gate` like any other tracked number.
        """
        if key in self._civ_gap:
            return
        self._civ_gap[key] = _CivGapState(key[0], key[1])
        if len(self._civ_gap) > 32:
            # Bound memory: drop owners with nothing outstanding, oldest first.
            for old in list(self._civ_gap):
                if (
                    old != key
                    and not self._civ_gap[old].missing
                    and not self._civ_gap[old].held
                ):
                    del self._civ_gap[old]
                if len(self._civ_gap) <= 32:
                    break

    def _civ_gap_gate(
        self,
        key: tuple[tuple[str, int], int],
        kind: str,
        data: bytes,
        addr: tuple[str, int],
        sender_id: int,
        seq: int,
        proto: _MockProtocol,
    ) -> bool:
        """Decide whether a tracked packet is processed now.

        Returns True when the caller should process the packet (and then
        drain held packets via :meth:`_civ_gap_drain`). A packet ahead of
        the expected number is held: the numbers in between become missing
        when the gap opens (a still-held number is never re-marked), and a
        held packet draws one retransmit-request round unless the previous
        round is less than 0.075 s old. A packet carrying a missing number
        fills it — including the last one, which drains the held packets
        in sequence order whatever the fill order. A number at or below
        the last processed one is ignored
        — no echo, no reply — whether or not a gap is open (recorded real
        IC-7610, MOR-3361: after a re-sent OpenClose the radio answers
        again only once the client's new numbering passes the last
        processed number). ``kind`` is ``"idle"``, ``"openclose"`` or
        ``"data"``; only ``"data"`` held packets produce replies on drain.
        """
        tracker = self._civ_gap.get(key)
        if tracker is None:
            return True  # stream never opened: pre-gap behaviour
        expected = tracker.next_expected
        if expected is None:
            tracker.next_expected = (seq + 1) & 0xFFFF
            return True
        if (
            tracker.inherited
            and not tracker.inherited_armed
            and tracker.inherited_frontier is not None
            and 1 <= ((seq - tracker.inherited_frontier) & 0xFFFF) <= 0x7FFF
        ):
            # First packet past the inherited frontier (MOR-3418 fix1,
            # recorded w50 b): from here the loop rounds may ask for the
            # inherited gap. Packets at/behind the frontier never arm.
            tracker.inherited_armed = True
        delta = (seq - expected) & 0xFFFF
        if delta == 0:
            # In order — or the fill of the expected number itself.
            tracker.missing.discard(seq)
            tracker.unanswered_rounds.pop(seq, None)
            tracker.next_expected = (expected + 1) & 0xFFFF
            return True
        if 1 <= delta <= 0x7FFF:
            if tracker.missing:
                # Gap already open: hold for later in-order processing. A
                # missing number arriving ahead of the expected one still
                # fills (recorded gap_filled_by_idle: 133 accepted while
                # 131 was expected). The missing set does NOT grow, and a
                # held or already received number is never re-marked as
                # missing: the recorded radio keeps requesting the original
                # gap while later packets arrive (gap_filled_by_idle stays
                # ``[[131, 133]]`` through 134..178).
                is_fill = seq in tracker.missing
                if (
                    is_fill
                    and tracker.inherited
                    and self.civ_inherited_fills_per_round > 0
                    and tracker.fills_this_round >= self.civ_inherited_fills_per_round
                ):
                    # Over budget (MOR-3418 w50 b): drop; the loop re-asks.
                    return False
                tracker.missing.discard(seq)
                if is_fill:
                    tracker.fills_this_round += 1
                    tracker.unanswered_rounds.pop(seq, None)
            else:
                for off in range(delta):
                    opened = (expected + off) & 0xFFFF
                    if opened not in tracker.held:
                        tracker.missing.add(opened)
            if seq not in tracker.held:
                tracker.held[seq] = (kind, data, addr, sender_id)
            if tracker.missing:
                # Still missing numbers — the gap just opened, stayed open
                # or narrowed: answer this tracked packet with one request
                # round, unless the previous round is less than 0.075 s old
                # (recorded rounds never come closer than 77 ms; row 143
                # @1.005 draws no round 3 ms after one). The 0.1 s loop
                # stays as the fallback for a silent client.
                now = self.clock()
                last = tracker.last_round_at
                if last is None or now - last >= _CIV_GAP_ROUND_MIN_SPACING_S:
                    self._civ_gap_send_round(tracker, proto)
            else:
                # The last missing number arrived ahead of the expected
                # one: release everything held, in sequence order.
                self._civ_gap_drain(key, proto)
            return False
        # Behind the frontier: already processed, so ignored — no echo, no
        # reply (MOR-3361). A missing number can never arrive from behind:
        # every member of ``missing`` is ahead of ``next_expected`` (added
        # there by the branch above, removed only by a fill), so the
        # above branches already handled every number the tracker waits
        # for.
        return False

    def _civ_gap_drain(
        self, key: tuple[tuple[str, int], int], proto: _MockProtocol | None
    ) -> None:
        """Process held packets in sequence order once nothing is missing.

        In standby the drain still consumes held packets — popping them and
        advancing past them — but processes nothing (no ``_handle_civ_data``:
        no echo, no reply). Packets held during standby are never answered,
        so the first packet after standby resumes in order with no
        retransmit request.
        """
        tracker = self._civ_gap.get(key)
        if tracker is None or tracker.missing:
            return
        if proto is None:
            proto = self._civ_proto
        standby = self._civ_standby
        while tracker.next_expected in tracker.held:
            expected = tracker.next_expected
            kind, data, addr, sender_id = tracker.held.pop(expected)
            tracker.next_expected = (expected + 1) & 0xFFFF
            if kind == "data" and proto is not None and not standby:
                self._handle_civ_data(data, addr, sender_id, proto)

    def _civ_gap_request(self, tracker: _CivGapState) -> bytes:
        """Build one retransmit-request packet for the current missing set.

        The single form (0x10-byte type-0x01, header seq = the missing
        number) when exactly one number is missing, otherwise one ranges
        packet (header seq 0, then little-endian u16 ``[first, last]``
        pairs, inclusive, one per contiguous missing run).
        """
        if len(tracker.missing) == 1:
            (only,) = tracker.missing
            return self._ctrl_pkt(_PT_CONTROL, only, tracker.sender_id)
        runs = _missing_runs(tracker.missing)
        pkt = bytearray(_HEADER_SIZE + 4 * len(runs))
        struct.pack_into("<I", pkt, 0x00, len(pkt))
        struct.pack_into("<H", pkt, 0x04, _PT_CONTROL)
        struct.pack_into("<H", pkt, 0x06, 0)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, tracker.sender_id)
        off = _HEADER_SIZE
        for first, last in runs:
            struct.pack_into("<HH", pkt, off, first, last)
            off += 4
        return bytes(pkt)

    def _civ_gap_send_round(
        self, tracker: _CivGapState, proto: _MockProtocol | None
    ) -> None:
        """Send the current retransmit request twice to one gapped owner."""
        if self._civ_standby:
            return  # standby: track, never ask — the 0.1 s loop retries later
        if proto is None:
            proto = self._civ_proto
        if proto is None:
            return
        tracker.last_round_at = self.clock()
        self.civ_retransmit_request_log.append(
            (tracker.last_round_at, tuple(_missing_runs(tracker.missing)))
        )
        pkt = self._civ_gap_request(tracker)
        for _ in range(2):
            # Through the same outbound funnel as replies, so a fault plan
            # sees the requests like any other radio-to-client packet.
            self._send(proto, pkt, tracker.addr, "civ")
            self.civ_retransmit_requests += 1

    async def _civ_gap_loop(self) -> None:
        """Repeat the retransmit request twice every 0.1 s per gapped owner.

        A fallback for a silent client: a round goes out only when the
        previous round (packet-triggered or not) is at least 0.1 s old.
        An inherited tracker whose client sent nothing past the inherited
        frontier yet stays silent (MOR-3418 fix1, recorded w50 a).
        Each tick is also the pacing window of an inherited tracker: its
        per-round fill budget resets, and missing numbers with no accepted
        fill for ``civ_inherited_drop_unanswered_rounds`` ticks are dropped
        (one inherited-state capture, MOR-3418 w50 b).
        """
        try:
            while True:
                await asyncio.sleep(_CIV_GAP_RETRANSMIT_PERIOD_S)
                proto = self._civ_proto
                if proto is None:
                    continue
                for tracker in list(self._civ_gap.values()):
                    if tracker.inherited and not tracker.inherited_armed:
                        # No packet past the inherited frontier yet: stay
                        # silent — no fill-budget reset, no drop counting,
                        # no round.
                        continue
                    if tracker.inherited:
                        tracker.fills_this_round = 0
                        drop_after = self.civ_inherited_drop_unanswered_rounds
                        if drop_after > 0 and tracker.missing:
                            counts = tracker.unanswered_rounds
                            for num in tracker.missing:
                                counts[num] = counts.get(num, 0) + 1
                            dropped = {
                                num
                                for num in tracker.missing
                                if counts[num] >= drop_after
                            }
                            if dropped:
                                tracker.missing.difference_update(dropped)
                            for num in list(counts):
                                if num not in tracker.missing:
                                    del counts[num]
                    if tracker.missing:
                        now = self.clock()
                        last = tracker.last_round_at
                        if last is None or now - last >= _CIV_GAP_RETRANSMIT_PERIOD_S:
                            self._civ_gap_send_round(tracker, proto)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock: CI-V gap loop error")

    # ------------------------------------------------------------------
    # Autonomous state + unsolicited CI-V streaming
    # ------------------------------------------------------------------

    async def _unsolicited_loop(self) -> None:
        """Emit unsolicited (transceive-style) CI-V frames on a cadence.

        Drifts the frequency and pushes an unsolicited 0x00 (freq report) frame
        to the last CI-V client, modelling a radio that streams data on its own.
        Suppressed while ``stall_for`` is active so the data watchdog can trip.
        """
        try:
            while True:
                await asyncio.sleep(self.unsolicited_interval_s)
                if self._civ_udp is None:
                    continue
                if time.monotonic() < self._stall_until:
                    continue  # stalled: deliberately starve the watchdog
                if time.monotonic() < self._down_until:
                    continue  # rebooted: the radio is "powered off"
                addr = self._civ_owner_addr
                if addr is None:
                    continue
                # Drift state and broadcast a freq report (transceive style).
                self._frequency += 10
                civ = self._civ_frame(
                    to=0x00,
                    frm=self._radio_addr,
                    cmd=_CMD_FREQ_GET,
                    data=_bcd_encode_freq(self._frequency),
                )
                pkt = self._wrap_civ(civ, self._civ_owner_id)
                if self.drop_rate > 0.0 and self._rng.random() < self.drop_rate:
                    continue
                if self._civ_proto is not None:
                    # Through the same funnel as replies: the fault plan's
                    # civ/to_client rule also applies to autonomous frames.
                    self._send(self._civ_proto, pkt, addr, "civ")
                    self.unsolicited_sent += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock: unsolicited CI-V loop error")

    # ------------------------------------------------------------------
    # CI-V command dispatcher
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Build a CI-V response frame for the given command.

        Answer order: the opt-in refusal hook (:attr:`refused_civ_commands`)
        NAKs first and is never overridden by the recorded table; a native
        handler's data/ACK answer is kept unless it is a NAK; a native NAK —
        and a request with no native handler — falls to
        ``recorded_answers.answer`` with the request frame rebuilt from the
        parsed parts; a miss (or no table mounted) keeps the NAK. A
        controller's own ACK/NAK is never answered, table included.
        """
        to = from_addr  # respond to whoever sent
        frm = self._radio_addr

        # Refusal hook first: a matched (cmd, sub) is answered with a bare
        # NAK before any handler runs, so no state effect applies.
        if self.refused_civ_commands and self._civ_refused(cmd, payload):
            self.refused_count += 1
            return self._civ_nak(to, frm)

        native = self._dispatch_civ_native(cmd, payload, from_addr)
        if native is None or native[4] != _CMD_NAK:
            return native
        if self.recorded_answers is not None:
            recorded = self.recorded_answers.answer(
                self._civ_frame(frm, to, cmd, data=payload)
            )
            if recorded is not None:
                return recorded
        return native

    def _dispatch_civ_native(
        self, cmd: int, payload: bytes, from_addr: int
    ) -> bytes | None:
        """The modelled CI-V handlers; a NAK reply means "no native answer"."""
        to = from_addr  # respond to whoever sent
        frm = self._radio_addr

        # --- Command29 wrapper (ATT / PREAMP / DIGI-SEL) ---
        if cmd == _CMD_CMD29:
            if len(payload) < 2:
                return self._civ_nak(to, frm)
            receiver = payload[0]
            real_cmd = payload[1]
            inner = payload[2:]
            return self._dispatch_cmd29(real_cmd, inner, from_addr, receiver)

        # --- Frequency ---
        if cmd == _CMD_FREQ_GET:  # 0x03 get
            return self._civ_frame(
                to, frm, _CMD_FREQ_GET, data=_bcd_encode_freq(self._frequency)
            )

        if cmd == _CMD_FREQ_SET:  # 0x05 set
            if len(payload) == 5:
                self._frequency = _bcd_decode_freq(payload)
            return self._civ_ack(to, frm)

        # --- Mode ---
        if cmd == _CMD_MODE_GET:  # 0x04 get
            return self._civ_frame(
                to, frm, _CMD_MODE_GET, data=bytes([self._mode, self._filter])
            )

        if cmd == _CMD_MODE_SET:  # 0x06 set
            if payload:
                self._mode = payload[0]
                if len(payload) > 1:
                    self._filter = payload[1]
            return self._civ_ack(to, frm)

        # --- Level (0x14) ---
        if cmd == _CMD_LEVEL:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            rest = payload[1:]
            if sub == _SUB_RF_POWER:  # 0x0A RF power
                if rest:  # set
                    self._power = _bcd_decode_value(rest)
                    return self._civ_ack(to, frm)
                else:  # get
                    return self._civ_frame(
                        to,
                        frm,
                        _CMD_LEVEL,
                        sub=_SUB_RF_POWER,
                        data=_level_bcd_encode(self._power),
                    )
            return self._civ_nak(to, frm)

        # --- Meter (0x15) ---
        if cmd == _CMD_METER:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            if sub == _SUB_S_METER:  # 0x02
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_S_METER,
                    data=_level_bcd_encode(self._s_meter),
                )
            if sub == _SUB_SWR_METER:  # 0x12
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_SWR_METER,
                    data=_level_bcd_encode(self._swr),
                )
            if sub == _SUB_ALC_METER:  # 0x13
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_ALC_METER,
                    data=_level_bcd_encode(self._alc),
                )
            return self._civ_nak(to, frm)

        # --- Transceiver ID (0x19 0x00) — the connect-time identity read ---
        if cmd == 0x19 and payload == b"\x00":
            return self._civ_frame(to, frm, 0x19, sub=0x00, data=self.model_id)

        # --- Selected/Unselected receiver frequency (0x25) ---
        # Payload's first byte is the receiver selector (00=selected,
        # 01=unselected) — a data byte, not a sub-command: 0x25 does not
        # carry one (commands/_frame.py: command_carries_sub), so the GET is
        # one byte and the SET is selector + 5-byte BCD.
        if cmd == _CMD_SELECTED_FREQ:
            if len(payload) == 1 and payload[0] in (0x00, 0x01):  # GET
                selector = payload[0]
                freq = (
                    self._frequency if selector == 0x00 else self._unselected_frequency
                )
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_SELECTED_FREQ,
                    data=bytes([selector]) + _bcd_encode_freq(freq),
                )
            if len(payload) == 6 and payload[0] in (0x00, 0x01):  # SET
                freq = _bcd_decode_freq(payload[1:6])
                if payload[0] == 0x00:
                    self._frequency = freq  # shared with 0x03/0x05
                else:
                    self._unselected_frequency = freq
                return self._civ_ack(to, frm)
            return self._civ_nak(to, frm)

        # --- Selected/Unselected receiver mode (0x26) ---
        # GET replies carry receiver, mode, DATA-mode flag, filter — the
        # layout commands/freq.py: parse_selected_mode_response reads. The
        # selected state is shared with 0x04/0x06 (plus the DATA-mode flag,
        # which 0x06 does not carry).
        if cmd == _CMD_SELECTED_MODE:
            if len(payload) == 1 and payload[0] in (0x00, 0x01):  # GET
                selector = payload[0]
                if selector == 0x00:
                    body = bytes([self._mode, self._data_mode, self._filter])
                else:
                    body = bytes(
                        [
                            self._unselected_mode,
                            self._unselected_data_mode,
                            self._unselected_filter,
                        ]
                    )
                return self._civ_frame(
                    to, frm, _CMD_SELECTED_MODE, data=bytes([selector]) + body
                )
            if len(payload) == 4 and payload[0] in (0x00, 0x01):  # SET
                mode, data_mode, filt = payload[1], payload[2], payload[3]
                if payload[0] == 0x00:
                    self._mode = mode  # shared with 0x04/0x06
                    self._data_mode = data_mode
                    self._filter = filt
                else:
                    self._unselected_mode = mode
                    self._unselected_data_mode = data_mode
                    self._unselected_filter = filt
                return self._civ_ack(to, frm)
            return self._civ_nak(to, frm)

        # --- Receiver select (0x07 D0/D1/D2, recorded vfo_select window) ---
        # D0 selects MAIN, D1 selects SUB (ACK either way; re-selecting the
        # current receiver swaps nothing), bare D2 reads the selection.
        if cmd == _CMD_VFO_SELECT and self._dual_receiver:
            if len(payload) == 1 and payload[0] in (0xD0, 0xD1):
                target = payload[0] - 0xD0  # D0 -> MAIN (0), D1 -> SUB (1)
                if target != self._selected_receiver:
                    self._frequency, self._unselected_frequency = (
                        self._unselected_frequency,
                        self._frequency,
                    )
                    self._mode, self._unselected_mode = (
                        self._unselected_mode,
                        self._mode,
                    )
                    self._data_mode, self._unselected_data_mode = (
                        self._unselected_data_mode,
                        self._data_mode,
                    )
                    self._filter, self._unselected_filter = (
                        self._unselected_filter,
                        self._filter,
                    )
                    self._selected_receiver = target
                return self._civ_ack(to, frm)
            if len(payload) == 1 and payload[0] == 0xD2:
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_VFO_SELECT,
                    data=bytes((0xD2, self._selected_receiver)),
                )

        # --- 0x1A 05 menu items (the startup MOD-input and NB reads) ---
        if cmd == _CMD_CTL_MEM_MENU and payload[:1] == b"\x05":
            rest = payload[1:]
            if len(rest) >= 2 and rest[:2] in self._ctl_mem_menu:
                item = rest[:2]
                width = _CTL_MEM_MENU_STARTUP_ITEMS[item][1]
                if len(rest) == 2:  # GET: item echo + BCD value, item width
                    value = self._ctl_mem_menu[item]
                    encoded = (
                        bytes([_bcd_byte(value)])
                        if width == 1
                        else _level_bcd_encode(value)
                    )
                    return self._civ_frame(
                        to,
                        frm,
                        _CMD_CTL_MEM_MENU,
                        sub=0x05,
                        data=item + encoded,
                    )
                if len(rest) == 2 + width:  # SET: BCD value bytes, item width
                    self._ctl_mem_menu[item] = _bcd_decode_value(rest[2:])
                    return self._civ_ack(to, frm)
            return self._civ_nak(to, frm)

        # --- PTT / Transceiver status (0x1C) ---
        if cmd == _CMD_PTT:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            rest = payload[1:]
            if sub == _SUB_PTT and not rest:  # 0x00 GET only
                # The managed-TX arming probe (MOR-1016) reads PTT once,
                # unmanaged, before any port is captured — and
                # ``request_fresh_ptt`` reads it again right after capture to
                # seed the authoritative observation. Always answer PTT OFF so
                # a rig that can in fact be supervised is recognised as such
                # (MOR-1217); a SET or an unrecognised sub-command NAKs, same
                # as an unimplemented command. With ``stateful_ptt`` the GET
                # instead answers the current simulated state (MOR-3251).
                state = 0x01 if self._ptt_state else 0x00
                return self._civ_frame(
                    to, frm, _CMD_PTT, sub=_SUB_PTT, data=bytes([state])
                )
            if self._stateful_ptt and sub == _SUB_PTT and rest in (b"\x00", b"\x01"):
                # Opt-in stateful PTT (MOR-3251): SET on/off answers FB and
                # flips the simulated state. Reached only after the
                # sequence-gap gate and behind the LAN echo above, like any
                # other CI-V request.
                self._set_simulated_ptt(rest == b"\x01", wire_set=True)
                return self._civ_ack(to, frm)
            return self._civ_nak(to, frm)

        # ACK / NAK from client — ignore
        if cmd in (_CMD_ACK, _CMD_NAK):
            return None

        # --- Scope (0x27) panel / wave output / receiver / dual / speed ---
        if cmd == _CMD_SCOPE and self._scope_stream:
            scope_reply = self._dispatch_scope_native(payload, from_addr)
            if scope_reply is not None:
                return scope_reply

        # Unknown command → NAK
        return self._civ_nak(to, frm)

    def _dispatch_cmd29(
        self, real_cmd: int, inner: bytes, from_addr: int, receiver: int
    ) -> bytes | None:
        """Handle a Command29-prefixed CI-V command.

        For GET responses the reply is wrapped back in Command29 so the client
        can match ``our_cmd = 0x29`` in its response scan.
        For SET responses a plain ACK is returned (client matches 0xFB directly).
        """
        to = from_addr
        frm = self._radio_addr

        # ATT (0x11)
        if real_cmd == _CMD_ATT:
            if inner:  # SET — inner[0] is BCD-encoded dB
                raw = inner[0]
                self._attenuator = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._civ_ack(to, frm)
            # GET — wrap response in Command29
            bcd = _bcd_byte(self._attenuator)
            return self._civ_frame(
                to, frm, _CMD_CMD29, data=bytes([receiver, _CMD_ATT, bcd])
            )

        # PREAMP / DIGI-SEL (0x16)
        if real_cmd == _CMD_PREAMP:
            if not inner:
                return self._civ_nak(to, frm)
            sub = inner[0]
            rest = inner[1:]

            if sub == _SUB_PREAMP_STATUS:  # 0x02
                if rest:  # SET
                    self._preamp = rest[0]
                    return self._civ_ack(to, frm)
                # GET — wrap in Command29
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_PREAMP, _SUB_PREAMP_STATUS, self._preamp]
                    ),
                )

            if sub == _SUB_DIGISEL_STATUS:  # 0x4E
                if rest:  # SET
                    self._digisel = rest[0]
                    return self._civ_ack(to, frm)
                # GET — wrap in Command29
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_PREAMP, _SUB_DIGISEL_STATUS, self._digisel]
                    ),
                )

            return self._civ_nak(to, frm)

        return self._civ_nak(to, frm)

    # ------------------------------------------------------------------
    # Scope wave streaming (opt-in via scope_stream, recorded scope window)
    # ------------------------------------------------------------------

    def _dispatch_scope_native(self, payload: bytes, from_addr: int) -> bytes | None:
        """Native 0x27 scope answers; None keeps today's path.

        Reads/writes without a receiver byte (panel ``10``, wave output
        ``11``, scope receiver ``12``, single/dual ``13``) and the
        per-receiver speed (``1A 0r`` read, ``1A 0r ss`` SET) follow the
        recorded reply shapes of the ``scope`` window of
        ``tests/fixtures/ic7610/civ_pairs.jsonl``; SETs are ACKed. Any
        other 0x27 payload returns None (the recorded table, then NAK).
        """
        to = from_addr
        frm = self._radio_addr
        if not payload:
            return None
        sub, rest = payload[0], payload[1:]
        if sub in (0x10, 0x11, 0x12, 0x13):
            if not rest:
                value = {
                    0x10: self._scope_panel,
                    0x11: self._scope_wave_output,
                    0x12: self._scope_receiver,
                    0x13: self._scope_dual,
                }[sub]
                return self._civ_frame(
                    to, frm, _CMD_SCOPE, sub=sub, data=bytes((value,))
                )
            if len(rest) == 1 and rest[0] in (0x00, 0x01):
                if sub == 0x10:
                    self._scope_panel = rest[0]
                elif sub == 0x11:
                    self._scope_wave_output = rest[0]
                elif sub == 0x12:
                    self._scope_receiver = rest[0]
                else:
                    self._scope_dual = rest[0]
                if sub != 0x10:
                    self._scope_retime()
                return self._civ_ack(to, frm)
            return None
        if sub == 0x1A and rest[:1] in (b"\x00", b"\x01"):
            receiver = rest[0]
            if len(rest) == 1:
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_SCOPE,
                    sub=sub,
                    data=bytes((receiver, self._scope_speed[receiver])),
                )
            if len(rest) == 2 and rest[1] in (0x00, 0x01, 0x02):
                self._scope_speed[receiver] = rest[1]
                self._scope_retime()
                return self._civ_ack(to, frm)
        return None

    def _scope_streaming_receivers(self) -> list[int]:
        """Receivers streaming wave frames right now.

        Dual streams both receivers at their own speeds, single only the
        selected scope receiver; a receiver at speed slow streams nothing.
        """
        if self._scope_wave_output != 0x01:
            return []
        wanted = [0x00, 0x01] if self._scope_dual == 0x01 else [self._scope_receiver]
        out = []
        for receiver in wanted:
            speed = self._scope_speed.get(receiver)
            if speed is not None and _SCOPE_GAP_S.get(speed) is not None:
                out.append(receiver)
        return out

    def _scope_retime(self) -> None:
        """Restart the wave cadence for the receivers streaming right now.

        Runs after every output/dual/receiver/speed SET: receivers that
        stopped streaming lose their due time, newly streaming ones start
        one gap out.
        """
        now = self.clock()
        due: dict[int, float] = {}
        for receiver in self._scope_streaming_receivers():
            gap = _SCOPE_GAP_S.get(self._scope_speed.get(receiver, -1))
            if gap is not None:
                due[receiver] = now + gap
        self._scope_next_due = due

    def pump_scope_stream(self) -> int:
        """Send every wave frame due as of ``self.clock()``; return the count.

        Each streaming receiver's recorded ``27 00`` template goes on the
        CI-V stream to the CI-V owner every gap of its speed
        (``tests/fixtures/ic7610/scope_cadence.json``), catching up when
        the clock jumped. The background loop calls this live; tests on a
        manual clock take over pacing and call it after each clock step.
        Without an opened CI-V stream due times still advance — the radio
        streams whether or not this client opened — but nothing is sent.
        """
        if not self._scope_stream:
            return 0
        now = self.clock()
        proto = self._civ_proto
        addr = self._civ_owner_addr
        sent = 0
        for receiver in self._scope_streaming_receivers():
            gap = _SCOPE_GAP_S.get(self._scope_speed.get(receiver, -1))
            if gap is None:
                continue
            due = self._scope_next_due.get(receiver)
            if due is None:
                due = now + gap
            while due <= now:
                if proto is not None and addr is not None:
                    self._send(
                        proto,
                        self._wrap_civ(_SCOPE_TEMPLATES[receiver], self._civ_owner_id),
                        addr,
                        "civ",
                    )
                    sent += 1
                    self.scope_frames_sent += 1
                due += gap
            self._scope_next_due[receiver] = due
        return sent

    async def _scope_loop(self) -> None:
        """Emit due wave frames every tick while the stream runs."""
        try:
            while True:
                await asyncio.sleep(_SCOPE_TICK_S)
                if self._civ_udp is None:
                    continue
                self.pump_scope_stream()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock: scope stream loop error")

    # ------------------------------------------------------------------
    # CI-V frame builders
    # ------------------------------------------------------------------

    def _civ_refused(self, cmd: int, payload: bytes) -> bool:
        """True when the refusal hook matches this (cmd, payload).

        ``(cmd, None)`` refuses every frame with that command byte;
        ``(cmd, sub)`` refuses only frames whose first payload byte is
        ``sub`` — the sub-command byte for commands that carry one.
        """
        if (cmd, None) in self.refused_civ_commands:
            return True
        return bool(payload) and (cmd, payload[0]) in self.refused_civ_commands

    def _civ_frame(
        self,
        to: int,
        frm: int,
        cmd: int,
        sub: int | None = None,
        data: bytes | None = None,
    ) -> bytes:
        frame = bytearray(_CIV_PREAMBLE)
        frame.append(to)
        frame.append(frm)
        frame.append(cmd)
        if sub is not None:
            frame.append(sub)
        if data:
            frame.extend(data)
        frame.extend(_CIV_TERM)
        return bytes(frame)

    def _civ_ack(self, to: int, frm: int) -> bytes:
        return self._civ_frame(to, frm, _CMD_ACK)

    def _civ_nak(self, to: int, frm: int) -> bytes:
        return self._civ_frame(to, frm, _CMD_NAK)

    def _wrap_civ(self, civ_frame: bytes, client_id: int) -> bytes:
        """Wrap a CI-V frame in a 0x15-byte DATA header for the CI-V port."""
        total = _CIV_HEADER_SIZE + len(civ_frame)
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0x00, total)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<H", pkt, 0x06, self._civ_seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x10] = 0xC1
        struct.pack_into("<H", pkt, 0x11, len(civ_frame))
        struct.pack_into(">H", pkt, 0x13, self._civ_seq)
        pkt[_CIV_HEADER_SIZE:] = civ_frame
        self._civ_seq = (self._civ_seq + 1) & 0xFFFF
        return bytes(pkt)

    @staticmethod
    def _decode_level_bcd(data: bytes) -> int:
        """Decode a 2-byte BCD level (e.g. b'\\x01\\x28' → 128).

        Kept for test-local ``MockIcomRadio`` subclasses (see
        ``tests/integration/test_dsp_levels_integration.py``); the dispatcher
        itself uses the module-level ``_bcd_decode_value``.
        """
        return _bcd_decode_value(data[:2])

    # ------------------------------------------------------------------
    # Control packet builders
    # ------------------------------------------------------------------

    def _handshake_reply(self, data: bytes, ptype: int, sender_id: int) -> bytes | None:
        """Reply bytes for the transport-handshake packets both ports share.

        Are-You-There → I-Am-Here, Are-You-Ready echo, ping-request reply
        (timestamp echoed); None for anything else. The CI-V port and the
        audio port call this with the same packet and each sends the reply
        through its own funnel, so the per-port gates (reboot down-window,
        fence, keepalive/audio silence) stay at the call sites and the
        CI-V paths are unchanged.
        """
        n = len(data)
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_THERE:
            return self._ctrl_pkt(_PT_I_AM_HERE, 0, sender_id)
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_READY:
            return self._ctrl_pkt(_PT_ARE_YOU_READY, 0, sender_id)
        if n == _PING_SIZE and ptype == _PT_PING and data[0x10] == 0x00:
            return self._ping_reply(data, sender_id)
        return None

    def _ctrl_pkt(self, ptype: int, seq: int, client_id: int) -> bytes:
        """Build a 0x10-byte control/discovery packet."""
        pkt = bytearray(_HEADER_SIZE)
        struct.pack_into("<I", pkt, 0x00, _HEADER_SIZE)
        struct.pack_into("<H", pkt, 0x04, ptype)
        struct.pack_into("<H", pkt, 0x06, seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        return bytes(pkt)

    def _ping_reply(self, data: bytes, client_id: int) -> bytes:
        """Build a ping reply echoing the received timestamp."""
        pkt = bytearray(_PING_SIZE)
        struct.pack_into("<I", pkt, 0x00, _PING_SIZE)
        struct.pack_into("<H", pkt, 0x04, _PT_PING)
        seq = struct.unpack_from("<H", data, 6)[0]
        struct.pack_into("<H", pkt, 0x06, seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x10] = 0x01  # reply flag
        pkt[0x11:0x15] = data[0x11:0x15]  # echo timestamp
        return bytes(pkt)

    def _login_response(self, data: bytes, sender_id: int) -> bytes:
        """Build a 0x60-byte login response.

        On auth_fail, sets the error field to 0xFEFFFFFF so that
        ``parse_auth_response`` marks the response as a failure.
        """
        tok_request = struct.unpack_from("<H", data, 0x1A)[0]
        pkt = bytearray(0x60)
        struct.pack_into("<I", pkt, 0x00, 0x60)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        struct.pack_into("<H", pkt, 0x1A, tok_request)
        if self.auth_fail:
            struct.pack_into("<I", pkt, 0x30, 0xFEFFFFFF)
        else:
            struct.pack_into("<I", pkt, 0x1C, self.token)
            # error at 0x30 stays 0x00000000 → success
            pkt[0x40:0x44] = b"FTTH"
        return bytes(pkt)

    def _token_renew_reply(self, data: bytes, sender_id: int, response: int) -> bytes:
        """Build the recorded 0x40 renew answer to a client renew packet.

        Recorded IC-7610 layout (MOR-3349 evidence): type 0, [0x14]=0x02,
        [0x15]=0x05, the request's token echoed at 0x1C, its inner
        sequence echoed at 0x16..0x17, [0x24..0x25]=07 98, sender/receiver
        ids swapped, every other byte in 0x10..0x3f zero except [0x13]=0x30,
        and the response at 0x30..0x33 (``_ERR_OK`` for the held token,
        ``_ERR_TOKEN_REJECT`` otherwise). The header sequence echoes the
        request's; that echo is a simulator choice, not a recorded fact.
        """
        pkt = bytearray(0x40)
        struct.pack_into("<I", pkt, 0x00, 0x40)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<H", pkt, 0x06, struct.unpack_from("<H", data, 0x06)[0])
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        pkt[0x13] = 0x30
        pkt[0x14] = 0x02
        pkt[0x15] = _TOKEN_RENEW
        pkt[0x16:0x18] = data[0x16:0x18]
        struct.pack_into("<I", pkt, 0x1C, struct.unpack_from("<I", data, 0x1C)[0])
        pkt[0x24:0x26] = b"\x07\x98"
        struct.pack_into("<I", pkt, 0x30, response & 0xFFFFFFFF)
        return bytes(pkt)

    def _guid_conninfo(self, sender_id: int) -> bytes:
        """Build a 0x90-byte conninfo that the radio sends after token-ack.

        The client reads the 16-byte GUID from offset 0x20.
        """
        pkt = bytearray(0x90)
        struct.pack_into("<I", pkt, 0x00, 0x90)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        # Fake GUID at 0x20-0x30
        pkt[0x20:0x30] = bytes(range(0x01, 0x11))
        return bytes(pkt)

    def _conninfo_notice(self, client_id: int) -> bytes:
        """Build the unsolicited 0x90 conninfo notice (busy=0, "session free").

        Recorded IC-7610 layout (bench capture MOR-2625/2627 and recording
        w2): request-type 0x00 (radio-originated) at 0x15; every field from
        0x20 on is zero — GUID area 0x20..0x30, device name 0x40..0x60, the
        busy u32 at 0x60 (0 = "free"), client name 0x64..0x74 and client IP
        0x84. The production parser
        ``ControlPhaseRuntime._on_conninfo_notice`` reads the u32 at 0x60 and
        classifies busy=0 as the radio having freed the LAN session.
        """
        pkt = bytearray(0x90)
        struct.pack_into("<I", pkt, 0x00, 0x90)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x15] = _CONNINFO_NOTICE_REQUEST_TYPE
        return bytes(pkt)

    def _send_conninfo_notice(self, addr: tuple[str, int], client_id: int) -> None:
        """Send the 0x90 notice through the same outbound funnel as replies."""
        if self._ctrl_proto is not None:
            self._send(self._ctrl_proto, self._conninfo_notice(client_id), addr, "ctrl")

    def _status_response(
        self,
        sender_id: int,
        *,
        civ_port: int | None = None,
        error: int = _ERR_OK,
    ) -> bytes:
        """Build a 0x50-byte status packet carrying the CI-V port number.

        Layout mirrors ``parse_status_response`` in auth.py (line ~400)
        and ``_build_status`` in test_radio_connect.py:
          offset 0x30 (LE u32) = error code
          offset 0x42 (BE u16) = CIV port
          offset 0x46 (BE u16) = audio port

        Args:
            sender_id: client connection ID to address the reply to.
            civ_port: CI-V port to advertise; ``None`` = the real bound port,
                ``0`` = not-ready / busy reject.
            error: error code written little-endian at 0x30
                (``0xFFFFFFFF`` = previous-session-active).
        """
        pkt = bytearray(0x50)
        struct.pack_into("<I", pkt, 0x00, 0x50)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        struct.pack_into("<I", pkt, 0x30, error & 0xFFFFFFFF)
        port = self._actual_civ_port if civ_port is None else civ_port
        struct.pack_into(">H", pkt, 0x42, port)
        # The audio port is the bound listener when one is up; otherwise
        # the dummy the simulator always advertised (CI-V port + 1, kept
        # within uint16 range): with the listener off nothing answers
        # there, exactly as before the listener existed.
        if port == 0:
            audio_port = 0
        elif self._actual_audio_port:
            audio_port = self._actual_audio_port
        else:
            audio_port = port + 1 if port < 65535 else 65534
        struct.pack_into(">H", pkt, 0x46, audio_port)
        return bytes(pkt)
