"""MOR-3538: multi-session lanes of the TX fault matrix.

MOR-3534 (several controller sessions, one TX holder, first PTT wins),
MOR-3536 (rigctl over WebSocket bound to a session) and MOR-3537 (a TX no
owner keyed is never keyed over) are pinned here at integration level, on
the stack ``test_tx_under_fault_matrix._stand_up_stack`` builds: the real
``WebServer`` + ``RigctldServer`` + managed TX authority + ``IcomRadio`` over
the ``MockIcomRadio`` LAN simulator. Faked: the radio simulator, the
controller's clock (a session expires only when a test advances it) and the
links (sockets are cut or left silent). Every key, release, refusal and
expiry runs through the production paths.

Each test is named for a failure path of the MOR-2866 remote-access plan:
``path1`` simultaneous keying, ``path2`` link cut / half-open / slow-loris,
``path3`` latched MOX holder lost, ``path4`` a refused client's audio,
``path5`` CW/VOX/TUNE/break-in from a non-holder, ``path6`` the operator's
own PTT against remote PTT, ``path7`` reconnect storm, ``path11`` old-version
sessionless client, ``path12`` more clients than the caps, ``path13`` the
WSJT-X path (rigctl over WS ``T 1`` plus audio).

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.mock_integration

from rigplane.core.state_pipeline_contracts import FieldPath  # noqa: E402
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind  # noqa: E402
from rigplane.runtime.tx_interlock import RfState, resolve_rf_state  # noqa: E402
from rigplane.web.protocol import (  # noqa: E402
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)

from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEY_DEADLINE_S,
    _LEASE_BUDGET_S,
    _LINK_FAULT_TOT_S,
    _TX_STARTS,
    _FrozenClock,
    _http_json,
    _RigctldKeyClient,
    _Stack,
    _stand_up_stack,
    _tear_down_stack,
    _wait_ptt,
    _wait_ptt_observed,
    _wait_released,
    _wait_rf,
    _WsKeyClient,
)
from test_web_server import _ws_recv_frame, _ws_send_text  # noqa: E402

_AUX = "/api/v1/ws?controller_role=auxiliary"
_MANAGED = "/api/v1/managed-transmit/command"
_TRANSMIT_ON = {"operation": "transmit_on"}
_CAT_OK = "RPRT 0\n"
_CAT_REFUSED = "RPRT -9\n"


@dataclass
class _Session:
    """One controller session: its primary control WebSocket and TX owner."""

    primary: _WsKeyClient
    key: str
    owner: str
    cat: _WsKeyClient | None = None  # its rigctl channel, when opened

    async def cat_key(self) -> bool:
        assert self.cat is not None
        return await self.cat.rigctl("T 1\n") == _CAT_OK


class _Arena:
    """The production stack with a frozen controller clock and tracked clients."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        enable_audio: bool = False,
        web_max_clients: int | None = None,
    ) -> None:
        self._tmp_path = tmp_path
        self._enable_audio = enable_audio
        self._web_max_clients = web_max_clients
        self._clients: list[_WsKeyClient] = []
        self._rigs: list[_RigctldKeyClient] = []

    async def __aenter__(self) -> _Arena:
        self.stack: _Stack = await _stand_up_stack(
            self._tmp_path,
            loss="hold",
            tot_seconds=_LINK_FAULT_TOT_S,
            unsolicited=False,
            sim_options={
                "enable_audio": self._enable_audio,
                "capture_civ_frames": True,
            },
            web_max_clients=self._web_max_clients,
        )
        self.sim = self.stack.sim
        self.web = self.stack.web
        self.authority = self.stack.composition.authority
        self.controller = self.web._controller  # noqa: SLF001
        self.clock = _FrozenClock()
        self.controller._clock = self.clock  # noqa: SLF001
        self.port = self.web.port
        await _wait_ptt_observed(self.web, _GATE_TIMEOUT_S)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for ws in self._clients:
            with contextlib.suppress(Exception):
                await ws.close()
        for rig in self._rigs:
            await rig.close()
        await _tear_down_stack(self.stack)

    async def ws(
        self, key: str | None = None, path: str = "/api/v1/ws"
    ) -> _WsKeyClient:
        ws = _WsKeyClient("127.0.0.1", self.port, key, path)
        await ws.connect()
        self._clients.append(ws)
        return ws

    async def grant(self) -> tuple[str, str]:
        """A session with no channel at all: its key and TX owner."""
        status, grant = await _http_json(
            self.port, "POST", "/api/v1/controller", {"kind": "pro"}
        )
        assert status == 201, grant
        key = grant["controller_key"]
        owner = next(
            owner
            for owner, entry in self.controller._sessions.items()  # noqa: SLF001
            if entry.key == key
        )
        return key, owner

    async def session(self, *, cat: bool = False) -> _Session:
        key, owner = await self.grant()
        session = _Session(await self.ws(key), key, owner)
        if cat:
            session.cat = await self.ws(key, "/api/v1/rigctl")
        return session

    async def tcp(self) -> _RigctldKeyClient:
        rig = _RigctldKeyClient("127.0.0.1", self.stack.rigctld_port)
        await rig.connect()
        self._rigs.append(rig)
        return rig

    async def tcp_line(self, rig: _RigctldKeyClient, line: str) -> str:
        assert rig._writer is not None and rig._reader is not None  # noqa: SLF001
        rig._writer.write(line.encode() + b"\n")  # noqa: SLF001
        await rig._writer.drain()  # noqa: SLF001
        return (await asyncio.wait_for(rig._reader.readline(), 10.0)).decode()  # noqa: SLF001

    def holder(self) -> str | None:
        return self.authority.tx_holder_nowait()

    async def released(self) -> None:
        await _wait_released(self.stack.composition)
        assert self.holder() is None

    def on_writes(self) -> list[tuple[float, bool]]:
        return [write for write in self.sim.ptt_set_writes if write[1]]

    async def unkey(self, session: _Session) -> None:
        assert (await session.primary.command("unkey", "ptt_off")).get("ok")
        await _wait_ptt(self.sim, False, _LEASE_BUDGET_S)
        await self.released()


def _tx_start_writes(frames: list[bytes]) -> list[bytes]:
    """The CI-V frames among ``frames`` that start a TX: CW text (``17``), the
    VOX and break-in set (``16 46`` / ``16 47`` with a data byte) and the tuner
    set (``1C 01`` with a data byte)."""
    found = []
    for frame in frames:
        body = frame[4:-1]
        if not body:
            continue
        cmd, rest = body[0], body[1:]
        if cmd == 0x17 or (
            len(rest) >= 2
            and (
                cmd == 0x16 and rest[0] in (0x46, 0x47) or cmd == 0x1C and rest[0] == 1
            )
        ):
            found.append(frame)
    return found


def _masked_text_frame(text: str) -> bytes:
    payload = text.encode()
    mask = b"\xde\xad\xbe\xef"
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return struct.pack("!BBH", 0x81, 0x80 | 126, len(payload)) + mask + masked


def _pcm_tx_frame(samples: int) -> bytes:
    return (
        bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
        + b"\x00" * (AUDIO_HEADER_SIZE - 2)
        + b"\x10\x00" * samples
    )


# ---------------------------------------------------------------------------
# Path 1: two sessions key in the same millisecond -> exactly one is applied
# ---------------------------------------------------------------------------


async def test_path1_clients_keying_in_the_same_instant_apply_exactly_one(
    tmp_path: Path,
) -> None:
    """Three sessions (one over its rigctl channel), a sessionless WebSocket
    and a TCP rigctld client key at once, three rounds; one key per round."""
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        c = await arena.session(cat=True)
        sessionless, tcp = await arena.ws(), await arena.tcp()
        lanes = {
            "session-a": (lambda: a.primary.key("a"), a.owner),
            "session-b": (lambda: b.primary.key("b"), b.owner),
            "session-c-rigctl": (c.cat_key, c.owner),
            "sessionless-ws": (lambda: sessionless.key("s"), "websocket-"),
            "tcp-rigctld": (tcp.key, "rigctld-client-"),
        }
        unkey = {
            "session-a": lambda: a.primary.command("u", "ptt_off"),
            "session-b": lambda: b.primary.command("u", "ptt_off"),
            "session-c-rigctl": lambda: c.cat.rigctl("T 0\n"),
            "sessionless-ws": lambda: sessionless.command("u", "ptt_off"),
            "tcp-rigctld": lambda: arena.tcp_line(tcp, "T 0"),
        }
        for keyed_before in range(3):
            outcomes = await asyncio.gather(*(key() for key, _ in lanes.values()))
            winners = [name for name, ok in zip(lanes, outcomes) if ok]
            assert len(winners) == 1, dict(zip(lanes, outcomes))
            await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
            holder, expected = arena.holder(), lanes[winners[0]][1]
            assert holder is not None and holder.startswith(expected), (holder, winners)
            assert len(arena.on_writes()) == keyed_before + 1
            # No loser's PTT off, on any lane, is the holder's release.
            for name, release in unkey.items():
                if name == winners[0]:
                    continue
                reply = await release()
                if isinstance(reply, dict):  # a WebSocket control lane
                    assert reply.get("ok") is False, (name, reply)
            assert arena.holder() == holder and arena.sim.ptt_on
            await unkey[winners[0]]()
            await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
            await arena.released()


# ---------------------------------------------------------------------------
# Path 2: link cut mid-TX — cut, half-open (no FIN), slow-loris
# ---------------------------------------------------------------------------


async def test_path2_a_cut_holder_releases_its_whole_session_and_b_keys_next(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        aux = await arena.ws(a.key, _AUX)
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert arena.holder() == a.owner
        a.primary.kill()  # the holder's primary link is cut mid-TX
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        # The session is fenced as a whole: its auxiliary channel is closed.
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError, EOFError)):
            await aux._until(lambda _m: False)  # noqa: SLF001
        await b.primary.heartbeat()  # the other session is untouched
        assert await b.primary.key("b")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert arena.holder() == b.owner
        await arena.unkey(b)
        assert len(arena.on_writes()) == 2


async def test_path2_slow_loris_holder_and_a_chatty_auxiliary_do_not_renew_the_lease(
    tmp_path: Path,
) -> None:
    """The holder dribbles one heartbeat frame a byte at a time, never finishing
    it, and its auxiliary channel talks: neither renews the session lease."""
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        aux = await arena.ws(a.key, _AUX)
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        frame = _masked_text_frame('{"type":"controller_heartbeat"' + " " * 200 + "}")
        writer = a.primary._writer  # noqa: SLF001
        writer.write(frame[:6])  # header and mask, no payload yet
        await writer.drain()
        arena.clock.now += 4.0
        await b.primary.heartbeat()
        arena.clock.now += 3.0  # past a's deadline, inside b's renewed one
        dribbled = 6
        end = time.monotonic() + _LEASE_BUDGET_S + arena.controller.HEARTBEAT_MS / 1000
        while arena.sim.ptt_on:
            assert time.monotonic() < end, "slow-loris holder kept the lease"
            with contextlib.suppress(Exception):  # the server closes the socket
                if dribbled < len(frame) - 1:
                    writer.write(frame[dribbled : dribbled + 1])
                    dribbled += 1
                    await writer.drain()
                await aux.send({"type": "controller_heartbeat"})
            await asyncio.sleep(0.1)
        await arena.released()
        await b.primary.heartbeat()
        assert len(arena.on_writes()) == 1


# ---------------------------------------------------------------------------
# Path 3: a latched MOX holder disappears -> released
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loss", ["channel_cut", "expiry", "delete"])
async def test_path3_latched_mox_holder_loss_releases(
    loss: str, tmp_path: Path
) -> None:
    """The latched holder is lost by its channel being cut, or (a session with
    no channel at all, so that nothing but the session's end can release it)
    by its lease expiring or by it being deleted."""
    async with _Arena(tmp_path) as arena:
        b = await arena.session()
        cut = await arena.session() if loss == "channel_cut" else None
        key, owner = (cut.key, cut.owner) if cut else await arena.grant()
        port = arena.port
        assert (await _http_json(port, "POST", _MANAGED, _TRANSMIT_ON, key))[0] == 202
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        state = (await arena.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.TRANSMIT
        assert arena.holder() == owner
        # Another session neither latches nor keys over it.
        assert (await _http_json(port, "POST", _MANAGED, _TRANSMIT_ON, b.key))[0] == 409
        assert not await b.primary.key("b")
        if cut:
            cut.primary.kill()
        elif loss == "expiry":
            arena.clock.now += 4.0
            await b.primary.heartbeat()
            arena.clock.now += 3.0
        else:
            assert (await _http_json(port, "DELETE", "/api/v1/controller", key=key))[
                0
            ] == 200
        wait = arena.controller.HEARTBEAT_MS / 1000 + _LEASE_BUDGET_S
        await _wait_ptt(arena.sim, False, wait)
        await arena.released()
        state = (await arena.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        # Free again: b latches, and its own release ends it.
        assert (await _http_json(port, "POST", _MANAGED, _TRANSMIT_ON, b.key))[0] == 202
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert (await _http_json(port, "DELETE", "/api/v1/controller", key=b.key))[
            0
        ] == 200
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        assert len(arena.on_writes()) == 2


# ---------------------------------------------------------------------------
# Path 4: a refused client's audio never reaches the air
# ---------------------------------------------------------------------------

_AUDIO = "/api/v1/audio"
# Distinct frame sizes tell the simulator's TX sink whose audio it received.
_HOLDER_SAMPLES, _REFUSED_SAMPLES, _SESSIONLESS_SAMPLES = 480, 200, 100


async def _open_mic(arena: _Arena, key: str | None) -> _WsKeyClient:
    mic = await arena.ws(key, _AUDIO)
    await mic.send({"type": "audio_start", "direction": "tx"})
    await mic._until(lambda message: message.get("type") == "audio_tx_format")  # noqa: SLF001
    return mic


async def _stream(
    arena: _Arena, mics: dict[int, _WsKeyClient], frames: int = 10
) -> None:
    for _ in range(frames):
        for samples, mic in mics.items():
            await mic.send_binary(_pcm_tx_frame(samples))
    await asyncio.sleep(0.5)


def _air_sizes(arena: _Arena) -> set[int]:
    return {frame.payload_len for frame in arena.sim.audio_tx_frames}


async def _wait_air(arena: _Arena) -> None:
    """Wait until the holder's audio has reached the simulator's TX sink."""
    end = time.monotonic() + _GATE_TIMEOUT_S
    while not arena.sim.audio_tx_frames:
        assert time.monotonic() < end, "the holder's audio never reached the air"
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)  # the other clients' frames had the same chance


async def test_path4_a_refused_clients_audio_never_reaches_the_air(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path, enable_audio=True) as arena:
        a, b = await arena.session(), await arena.session()
        mics = {
            _HOLDER_SAMPLES: await _open_mic(arena, a.key),
            _REFUSED_SAMPLES: await _open_mic(arena, b.key),
            _SESSIONLESS_SAMPLES: await _open_mic(arena, None),
        }
        # Nobody holds the transmitter: no client's audio goes out.
        await _stream(arena, mics)
        assert not arena.sim.audio_tx_frames
        # b is refused the key; a keys. Only a's frames reach the air.
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert not await b.primary.key("b")
        await _stream(arena, mics)
        await _wait_air(arena)
        assert _air_sizes(arena) == {_HOLDER_SAMPLES * 2}
        # a unkeys: once the poller reads RX again, nothing goes out for
        # anybody (the audio gate also opens on a fresh observed TX).
        await arena.unkey(a)
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        before = len(arena.sim.audio_tx_frames)
        await _stream(arena, mics)
        assert len(arena.sim.audio_tx_frames) == before


# ---------------------------------------------------------------------------
# Path 5: CW / VOX / TUNE / break-in from a non-holder during TX are refused
# ---------------------------------------------------------------------------


async def _tx_start_on_wire(
    arena: _Arena, mark: int, *, seen: bool, what: str = ""
) -> None:
    """Wait for a TX-start write after ``mark`` to appear (``seen``), or prove
    over a settle window that none does."""
    end = time.monotonic() + (5.0 if seen else 0.7)
    while time.monotonic() < end:
        if _tx_start_writes(arena.sim.civ_frames_received[mark:]):
            assert seen, _tx_start_writes(arena.sim.civ_frames_received[mark:])
            return
        await asyncio.sleep(0.05)
    assert not seen, (what, [f.hex() for f in arena.sim.civ_frames_received[mark:]])


async def test_path5_tx_starts_from_a_non_holder_are_refused_on_every_surface(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a = await arena.session()
        b = await arena.session(cat=True)
        legacy, tcp = await arena.ws(), await arena.tcp()
        undo = {
            "set_vox": {"on": False},
            "set_tuner_status": {"value": 0},
            "set_break_in": {"mode": 0},
        }
        # Positive control: with nobody holding, each start reaches the wire,
        # so the wire check below can see a start that got through.
        for name, params in _TX_STARTS:
            mark = len(arena.sim.civ_frames_received)
            response = await b.primary.command(f"free-{name}", name, params)
            # The simulator NAKs CW text; the frame still reaches its wire.
            assert response.get("ok") is True or name == "send_cw_text", response
            await _tx_start_on_wire(arena, mark, seen=True, what=name)
            if name in undo:
                assert (await b.primary.command("undo", name, undo[name])).get("ok")
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        mark = len(arena.sim.civ_frames_received)
        for name, params in _TX_STARTS:
            for who, ws in (("session", b.primary), ("sessionless", legacy)):
                response = await ws.command(f"{who}-{name}", name, params)
                assert response.get("ok") is False, (who, name, response)
                assert response.get("message") == "controller_tx_unsupported", response
            body = {"name": name, "params": params}
            for key in (b.key, None):
                status, _ = await _http_json(
                    arena.port, "POST", "/api/v1/commands", body, key
                )
                assert status in (403, 409), (name, key, status)
        for line in ("U VOX 1", "T 1"):
            assert await b.cat.rigctl(line + "\n") == _CAT_REFUSED, line
            assert await arena.tcp_line(tcp, line) == _CAT_REFUSED, line
        await _tx_start_on_wire(arena, mark, seen=False)
        assert arena.holder() == a.owner and arena.sim.ptt_on
        await arena.unkey(a)
        # Free again: b's start is no longer refused.
        assert (await b.primary.command("free", "set_vox", {"on": True})).get("ok")
        assert (await b.primary.command("undo", "set_vox", {"on": False})).get("ok")


# ---------------------------------------------------------------------------
# Path 6: the operator's own PTT (station side) against remote PTT
# ---------------------------------------------------------------------------


async def _wait_tx_read_after_unkey(arena: _Arena) -> None:
    """Wait for a poll that read TX after Core's unkey was acknowledged."""
    since = time.monotonic()
    ptt = FieldPath.global_("tx_state", "ptt")
    end = since + _GATE_TIMEOUT_S
    while True:
        snapshot = arena.web.command_state_store.snapshot()
        if (
            resolve_rf_state(snapshot) is RfState.TX
            and snapshot.field(ptt).last_observed_monotonic > since
        ):
            return
        assert time.monotonic() < end, "no poll after the unkey read TX"
        await asyncio.sleep(0.05)


async def test_path6_operator_ptt_refuses_every_remote_surface_then_one_key_wins(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        c = await arena.session(cat=True)
        legacy, tcp = await arena.ws(), await arena.tcp()
        writes, mark = len(arena.sim.ptt_set_writes), len(arena.sim.civ_frames_received)
        # The operator keys the radio's own mic: no session, no wire write.
        arena.sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        for ws in (a.primary, b.primary, legacy):
            response = await ws.command("key", "ptt_on")
            assert (response.get("ok"), response.get("message")) == (
                False,
                "radio_tx_unowned",
            ), response
        for name, params in _TX_STARTS:
            response = await b.primary.command(f"b-{name}", name, params)
            assert response.get("message") == "radio_tx_unowned", response
        for line in ("T 1\n", "U VOX 1\n"):
            assert await c.cat.rigctl(line) == _CAT_REFUSED
            assert await arena.tcp_line(tcp, line.strip()) == _CAT_REFUSED
        status, body = await _http_json(
            arena.port, "POST", _MANAGED, _TRANSMIT_ON, a.key
        )
        assert (status, body) == (409, {"error": "radio_tx_unowned"})
        await _tx_start_on_wire(arena, mark, seen=False)
        assert arena.sim.ptt_set_writes[writes:] == [] and arena.holder() is None
        # The operator lets go; two sessions then key at once: one wins.
        arena.sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        outcomes = await asyncio.gather(a.primary.key("a"), b.primary.key("b"))
        assert sorted(outcomes) == [False, True], outcomes
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        winner = a if outcomes[0] else b
        assert arena.holder() == winner.owner and len(arena.on_writes()) == 1
        await arena.unkey(winner)


async def test_path6_operator_mic_held_through_a_remote_unkey_blocks_until_it_lets_go(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        legacy, tcp = await arena.ws(), await arena.tcp()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        # The operator holds the mic too: from here the radio ACKs every
        # de-key write but stays keyed (the simulator's PTT is the radio).
        honour = arena.sim._set_simulated_ptt  # noqa: SLF001

        def acked_but_kept(on: bool, *, wire_set: bool) -> None:
            if wire_set and not on:
                arena.sim.ptt_set_writes.append((time.monotonic(), False))
                return
            honour(on, wire_set=wire_set)

        arena.sim._set_simulated_ptt = acked_but_kept  # type: ignore[method-assign]  # noqa: SLF001
        assert (await a.primary.command("unkey", "ptt_off")).get("ok")
        await _wait_released(arena.stack.composition)
        await _wait_tx_read_after_unkey(arena)
        writes = len(arena.sim.ptt_set_writes)
        for ws in (a.primary, b.primary, legacy):
            response = await ws.command("key", "ptt_on")
            assert response.get("message") == "radio_tx_unreleased", response
        assert await arena.tcp_line(tcp, "T 1") == _CAT_REFUSED
        assert arena.sim.ptt_set_writes[writes:] == []
        # The operator lets go: the radio reads RX and the next key is applied.
        arena.sim._set_simulated_ptt = honour  # type: ignore[method-assign]  # noqa: SLF001
        arena.sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        assert await b.primary.key("b")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert arena.holder() == b.owner
        await arena.unkey(b)


# ---------------------------------------------------------------------------
# Path 7: reconnect storm (20 reconnects/s) -> no leaked session, no PTT replay
# ---------------------------------------------------------------------------

_STORM_TICK_S = 0.05  # 20 reconnects per second
_STORM_TICKS = 60


async def _ticks(count: int) -> Any:
    """Yield ``0..count-1`` on a fixed schedule, one per ``_STORM_TICK_S``."""
    start = time.monotonic()
    for i in range(count):
        yield i
        await asyncio.sleep(
            max(0.0, start + (i + 1) * _STORM_TICK_S - time.monotonic())
        )


async def test_path7_reconnect_storm_leaks_no_session_and_replays_no_ptt(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        keyed, tasks_before = arena.on_writes(), len(arena.web._client_tasks)  # noqa: SLF001

        async def new_session(i: int) -> None:
            status, grant = await _http_json(
                arena.port, "POST", "/api/v1/controller", {"kind": "pro"}
            )
            assert status == 201, grant
            ws = _WsKeyClient("127.0.0.1", arena.port, grant["controller_key"])
            await ws.connect()
            if i % 2:
                ws.kill()  # the link just goes away
            else:
                await _http_json(
                    arena.port,
                    "DELETE",
                    "/api/v1/controller",
                    key=grant["controller_key"],
                )
                ws.kill()

        async def holder_audio_channel() -> None:
            ws = _WsKeyClient("127.0.0.1", arena.port, a.key, _AUDIO)
            await ws.connect()
            ws.kill()

        storm = []
        async for i in _ticks(_STORM_TICKS):
            storm.append(asyncio.ensure_future(new_session(i)))
            if i % 3 == 0:
                storm.append(asyncio.ensure_future(holder_audio_channel()))
        await asyncio.gather(*storm)

        end = time.monotonic() + 5.0
        while (
            set(arena.controller._sessions) != {a.owner, b.owner}  # noqa: SLF001
            or len(arena.web._client_tasks) > tasks_before  # noqa: SLF001
        ):
            assert time.monotonic() < end, (
                f"leaked: sessions={len(arena.controller._sessions)} "  # noqa: SLF001
                f"tasks={len(arena.web._client_tasks)} (was {tasks_before})"  # noqa: SLF001
            )
            await asyncio.sleep(0.1)
        await arena.controller.settle()
        scopes = {scope for scope, _ in arena.web._conn_manager._connections}  # noqa: SLF001
        assert scopes == {a.owner, b.owner}, scopes
        assert len(arena.controller._sessions[a.owner].channels) == 1  # noqa: SLF001
        # Nothing was replayed: the one key stands, the holder is unchanged.
        assert arena.on_writes() == keyed and arena.sim.ptt_on
        assert arena.holder() == a.owner
        await a.primary.heartbeat()
        await b.primary.heartbeat()
        await arena.unkey(a)


async def test_path7_closing_an_auxiliary_control_channel_keeps_the_holders_tx(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a = await arena.session()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        (await arena.ws(a.key, _AUX)).kill()
        # Wait for the release to show (or the lease budget, which is longer
        # than one force-off attempt, to pass) instead of sleeping a guess.
        end = time.monotonic() + _LEASE_BUDGET_S
        while arena.sim.ptt_on and time.monotonic() < end:
            await asyncio.sleep(0.05)
        assert arena.sim.ptt_on and arena.holder() == a.owner
        await a.primary.heartbeat()  # the session itself is alive throughout


async def test_path7_closing_the_auxiliary_channel_that_keyed_releases_the_tx(
    tmp_path: Path,
) -> None:
    # MOR-3562: a channel's close still releases the TX that channel keyed.
    async with _Arena(tmp_path) as arena:
        a = await arena.session()
        aux = await arena.ws(a.key, _AUX)
        assert await aux.key("a-aux")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert arena.holder() == a.owner
        aux.kill()
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        await a.primary.heartbeat()  # only the channel closed; the session lives
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await arena.unkey(a)


async def test_path7_closing_the_holders_rigctl_channel_keeps_the_tx_it_did_not_key(
    tmp_path: Path,
) -> None:
    # MOR-3562: the rigctl channel's close releases only a TX its own T 1 keyed.
    async with _Arena(tmp_path) as arena:
        a = await arena.session(cat=True)
        assert (await a.cat.rigctl("f\n")).strip().isdigit()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        a.cat.kill()
        # rigctld drops the owner only once the channel's teardown has run.
        end = time.monotonic() + _LEASE_BUDGET_S
        while a.owner in arena.stack.rigctld._channel_owners:  # noqa: SLF001
            assert time.monotonic() < end, "the rigctl channel never retired"
            await asyncio.sleep(0.05)
        assert arena.sim.ptt_on and arena.holder() == a.owner
        await a.primary.heartbeat()
        await arena.unkey(a)


async def test_path7_stale_key_reconnect_storm_never_replays_the_released_ptt(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(), await arena.session()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        a.primary.kill()
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        writes = list(arena.sim.ptt_set_writes)

        async def reconnect(path: str) -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", arena.port)
            try:
                writer.write(
                    (
                        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{arena.port}\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Protocol: rigplane-controller-v1.{a.key}"
                        "\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
            finally:
                writer.close()

        heads = []
        async for i in _ticks(_STORM_TICKS):
            path = ("/api/v1/ws", "/api/v1/rigctl", _AUX)[i % 3]
            heads.append(asyncio.ensure_future(reconnect(path)))
        refused = await asyncio.gather(*heads)
        assert all(head.startswith(b"HTTP/1.1 403 ") for head in refused), refused[:2]
        status, body = await _http_json(
            arena.port, "POST", _MANAGED, _TRANSMIT_ON, a.key
        )
        assert (status, body) == (403, {"error": "controller_invalid"})
        assert a.owner not in arena.controller._sessions  # noqa: SLF001
        assert arena.sim.ptt_set_writes == writes and not arena.sim.ptt_on
        await b.primary.heartbeat()
        assert await b.primary.key("b")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await arena.unkey(b)


# ---------------------------------------------------------------------------
# Path 11: an old-version client without a session
# ---------------------------------------------------------------------------


async def test_path11_old_version_client_reads_always_and_keys_only_when_free(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a = await arena.session()
        old, tcp = await arena.ws(), await arena.tcp()

        async def reads_work() -> None:
            assert (await _http_json(arena.port, "GET", "/api/v1/state"))[0] == 200
            assert (await arena.tcp_line(tcp, "f")).strip().isdigit()
            await old.send({"type": "ping"})  # the open channel stays served

        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await reads_work()
        assert not await old.key("old") and not await tcp.key()
        assert (await _http_json(arena.port, "POST", _MANAGED, _TRANSMIT_ON))[0] == 409
        assert arena.holder() == a.owner
        await arena.unkey(a)
        # Free: the old client keys as before and holds against the sessions.
        assert await old.key("old")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        holder = arena.holder()
        assert holder is not None and holder.startswith("websocket-"), holder
        await reads_work()
        assert not await a.primary.key("a2")
        assert (await _http_json(arena.port, "POST", _MANAGED, _TRANSMIT_ON, a.key))[
            0
        ] == 409
        for name, params in _TX_STARTS:
            response = await a.primary.command(f"a-{name}", name, params)
            assert response.get("message") == "controller_tx_unsupported", response
        assert arena.holder() == holder and len(arena.on_writes()) == 2
        # Its link dies: released, and the session keys.
        old.kill()
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        assert await a.primary.key("a3")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await arena.unkey(a)


# ---------------------------------------------------------------------------
# Path 12: more clients than the caps
# ---------------------------------------------------------------------------
#
# Channel caps per session, with the holder session never evicted, are pinned
# by test_multi_session_tx_lanes[eviction] in test_tx_under_fault_matrix.py.


async def test_path12_rigctld_client_cap_refuses_newcomers_and_keeps_the_holder(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path) as arena:
        a, b = await arena.session(cat=True), await arena.session()
        assert await a.cat_key()
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        cap = arena.stack.rigctld._config.max_clients  # noqa: SLF001
        rigs = [await arena.tcp() for _ in range(cap - 1)]  # a's channel is the cap-th
        # At the cap a TCP newcomer and b's rigctl channel are both turned away.
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", arena.stack.rigctld_port
        )
        assert await asyncio.wait_for(reader.read(), 5.0) == b""
        writer.close()
        b_cat = await arena.ws(b.key, "/api/v1/rigctl")
        opcode, payload = await _ws_recv_frame(b_cat._reader, timeout=5.0)  # noqa: SLF001
        assert opcode == 0x8 and payload[:2] == struct.pack("!H", 1013), payload
        # None of it evicted the holder, whose channel still answers.
        assert arena.holder() == a.owner and arena.sim.ptt_on
        assert await a.cat.rigctl("f\n")
        # A TCP client leaves; b's channel is served now (and still cannot key).
        await rigs.pop().close()
        end = time.monotonic() + 5.0
        while True:  # the freed slot opens once the server retires that client
            b_cat = await arena.ws(b.key, "/api/v1/rigctl")
            await _ws_send_text(b_cat._writer, "T 1\n")  # noqa: SLF001
            opcode, payload = await _ws_recv_frame(b_cat._reader, timeout=5.0)  # noqa: SLF001
            if opcode == 0x1:
                assert payload.decode() == _CAT_REFUSED
                break
            assert time.monotonic() < end, (opcode, payload)
            await asyncio.sleep(0.1)
        assert arena.holder() == a.owner
        assert await a.cat.rigctl("T 0\n") == _CAT_OK
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()


async def test_path12_web_connection_cap_does_not_stop_the_holder_lease_ending(
    tmp_path: Path,
) -> None:
    cap = 8
    async with _Arena(tmp_path, web_max_clients=cap) as arena:
        a, b = await arena.session(), await arena.session()
        assert await a.primary.key("a")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        for _ in range(cap - 2):  # fill the web server to its cap
            await arena.ws()
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            await _http_json(arena.port, "GET", "/api/v1/state")  # turned away
        # The holder's own open WebSocket is not evicted; it still unkeys.
        assert arena.holder() == a.owner and arena.sim.ptt_on
        await b.primary.heartbeat()
        # At the cap, the lease of a silent holder still ends on its deadline.
        arena.clock.now += 4.0
        await b.primary.heartbeat()
        arena.clock.now += 3.0
        await _wait_ptt(
            arena.sim, False, arena.controller.HEARTBEAT_MS / 1000 + _LEASE_BUDGET_S
        )
        await arena.released()
        assert len(arena.on_writes()) == 1


# ---------------------------------------------------------------------------
# Path 13: the WSJT-X path — rigctl over WS ``T 1`` plus cable audio
# ---------------------------------------------------------------------------


async def test_path13_wsjtx_path_second_client_is_refused_on_rigctl_audio_and_tcp(
    tmp_path: Path,
) -> None:
    async with _Arena(tmp_path, enable_audio=True) as arena:
        a, b = await arena.session(cat=True), await arena.session(cat=True)
        tcp = await arena.tcp()
        mics = {
            _HOLDER_SAMPLES: await _open_mic(arena, a.key),
            _REFUSED_SAMPLES: await _open_mic(arena, b.key),
        }
        assert await a.cat_key()
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        assert arena.holder() == a.owner
        # The second client is refused on every surface it has, and its
        # unkeys are not the holder's release.
        assert await b.cat.rigctl("T 1\n") == _CAT_REFUSED
        assert not await b.primary.key("b") and not await tcp.key()
        await b.cat.rigctl("T 0\n")
        await arena.tcp_line(tcp, "T 0")
        await _stream(arena, mics)
        await _wait_air(arena)
        assert arena.holder() == a.owner and arena.sim.ptt_on
        assert _air_sizes(arena) == {_HOLDER_SAMPLES * 2}
        assert await a.cat.rigctl("T 0\n") == _CAT_OK
        await _wait_ptt(arena.sim, False, _LEASE_BUDGET_S)
        await arena.released()
        assert len(arena.on_writes()) == 1
