"""MOR-3251: the TX-under-fault matrix on the Icom LAN simulator.

The production stack — ``WebServer`` + ``RigctldServer`` + managed TX
authority (``ManagedTxComposition``, the same composition root the CLI's
``web``/``station`` commands install) + an IC-7610 ``IcomRadio`` over the
``MockIcomRadio`` LAN transport, ``stateful_ptt=True`` — is keyed through
one of the two real ingress lanes and then a fault is injected on the
simulator (or the keying client). Every cell asserts the TX-safety
contract:

1. the simulator's PTT returns to 0 within a budget;
2. unkey ownership: the authority's lease ends released (intent RX, no
   owed release, no pending effect) and nothing re-keys the radio after
   recovery — PTT stays 0 across a settle window.

Lanes: (W) the WebSocket managed-PTT lane — a raw WebSocket control
session sending ``ptt_on`` (``control.py: ControlHandler._enqueue_managed_ptt``
→ ``WebServer.enqueue_managed_positive_tx``); (R) rigctld ``T 1`` — in the
default bundled configuration (``cli/__init__.py: _cmd_web`` constructs
``RigctldServer`` with ``managed_tx_authority=``) this keys MANAGED, through
the same authority with owner ``rigctld-client-N``.

The real IC-7610 behaviour for an active PTT when its LAN session ends is
UNRECORDED (TX is never actuated on the bench), so every simulator fault
cell runs under both ``ptt_on_session_loss`` values.

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Make tests/ importable from tests/integration/.
sys.path.insert(0, str(Path(__file__).parent.parent))

# All tests in this module use MockIcomRadio and require no real hardware.
pytestmark = pytest.mark.mock_integration

from rigplane.core.state_pipeline_contracts import FieldPath  # noqa: E402
from rigplane.core.tx_observation import OBSERVED_PTT_PATH  # noqa: E402
from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.rigctld.contract import RigctldConfig  # noqa: E402
from rigplane.rigctld.server import RigctldServer  # noqa: E402
from rigplane.runtime._civ_rx import (  # noqa: E402
    _CIV_DATA_WATCHDOG_TIMEOUT,
    _SILENT_OPENCLOSE_DEADLINE,
)
from rigplane.runtime.managed_tx_authority import (  # noqa: E402
    ATTEMPT_TIMEOUT_S,
    RETRY_DELAY_S,
)
from rigplane.runtime.managed_tx_composition import (  # noqa: E402
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore  # noqa: E402
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind  # noqa: E402
from rigplane.runtime.tx_interlock import RfState, resolve_rf_state  # noqa: E402
from rigplane.web.protocol import (  # noqa: E402
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane.web.web_startup import (  # noqa: E402
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
from rigplane_testkit.icom import MockIcomRadio  # noqa: E402

from _perf_helpers import fast_connect  # noqa: E402
from test_web_server import (  # noqa: E402
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
    _ws_skip_handshake,
)

# ---------------------------------------------------------------------------
# Knobs the test itself owns (fault shapes), each justified at its use
# ---------------------------------------------------------------------------

# Client-side detection speed: the IcomRadio control watchdog. Existing
# simulator-event tests use this value (tests/testkit/test_simulator_events.py).
_WATCHDOG_TIMEOUT_S = 1.5
_RECONNECT_DELAY_S = 0.2
_RECONNECT_MAX_DELAY_S = 0.5

# The simulator holds a LAN session this long without client pings; 2.0 s
# keeps the radio's own timer from racing the client's control pings during
# a keepalive-silence window (tests/testkit/test_simulator_events.py).
_KEEPALIVE_HOLD_S = 2.0

# Fault shapes.
_REBOOT_AT_S = 0.1
_REBOOT_DOWN_FOR_S = 3.0
_REBOOT_BUSY_FOR_S = 0.5
_SILENCE_FOR_S = _WATCHDOG_TIMEOUT_S + 1.5
# The patient OpenClose window for a silent drop, shortened from
# _OPENCLOSE_DEADLINE the way tests/testkit/test_reconnect_storm.py does,
# so the no-notice drop is detected in test time.
_PATIENCE_S = 10.0

# Software TOT (seconds) written into the composition's config file with
# the production store (ManagedTxTotConfigStore). The stall cells use a
# short TOT because a stalled-but-answering radio leaves the TOT as the
# only force-off; every other cell keeps the TOT beyond its budget so the
# reconnect-driven release is the mechanism the cell pins.
_STALL_TOT_S = 3.0
_LINK_FAULT_TOT_S = 30.0

# Recovery slack for one reconnect round (login + conninfo + OpenClose +
# startup reads) under runner load — the same allowance
# tests/testkit/test_reconnect_storm.py (_RECOVERY_S) uses.
_ONE_RECOVERY_ROUND_S = 10.0

# Detection + recovery + release budget per fault family.
_KEY_DEADLINE_S = ATTEMPT_TIMEOUT_S + RETRY_DELAY_S + 2.0
_LEASE_BUDGET_S = ATTEMPT_TIMEOUT_S + RETRY_DELAY_S + 2.0
_TOT_BUDGET_S = _STALL_TOT_S + ATTEMPT_TIMEOUT_S + 2.0
_NOTICE_DROP_BUDGET_S = (
    _RECONNECT_MAX_DELAY_S + _ONE_RECOVERY_ROUND_S + RETRY_DELAY_S + ATTEMPT_TIMEOUT_S
)
_SILENT_DROP_BUDGET_S = (
    2 * 1.5 * _CIV_DATA_WATCHDOG_TIMEOUT
    + _PATIENCE_S
    + _SILENT_OPENCLOSE_DEADLINE
    + _ONE_RECOVERY_ROUND_S
    + RETRY_DELAY_S
    + ATTEMPT_TIMEOUT_S
)
_REBOOT_BUDGET_S = (
    _REBOOT_AT_S
    + _REBOOT_DOWN_FOR_S
    + _REBOOT_BUSY_FOR_S
    + _ONE_RECOVERY_ROUND_S
    + RETRY_DELAY_S
    + ATTEMPT_TIMEOUT_S
)

# After the release and the reconnect, PTT must stay 0 for a window that
# covers two release-retry cadences (managed_tx_authority.py:
# RETRY_DELAY_S) plus one force-off attempt bound.
_SETTLE_S = 2 * RETRY_DELAY_S + ATTEMPT_TIMEOUT_S

# Grace for the release DEBT to clear. The force-receive's confirmation is
# deliberately distrustful (runtime/radio.py: CoreRadio.actuate, MOR-2860):
# an ACK/NAK another write left unclaimed within one ``_civ_get_timeout``
# (radio.py: ``min(timeout, 2.0)``) makes the attempt UNCERTAIN and the
# authority retries it after RETRY_DELAY_S. The simulator NAKs the
# profile's declared reads it does not model, which keeps that ambiguity
# window refreshed under load, so the debt clears a few retries later than
# the radio-side unkey: the grace spans several retry cadences.
_RELEASE_DEBT_GRACE_S = ATTEMPT_TIMEOUT_S + 5 * RETRY_DELAY_S

_GATE_TIMEOUT_S = 20.0  # tests/test_mor2557_web_rigctld_shared_seat.py


# ---------------------------------------------------------------------------
# Keying clients
# ---------------------------------------------------------------------------


class _WsKeyClient:
    """A raw WebSocket control session that keys via ``ptt_on``.

    With ``controller_key`` it is the primary channel of that multi-client
    controller session (MOR-3534); without, a sessionless client.
    """

    def __init__(
        self,
        host: str,
        port: int,
        controller_key: str | None = None,
        path: str = "/api/v1/ws",
    ) -> None:
        self._host = host
        self._port = port
        self.controller_key = controller_key
        self._path = path
        self._writer: Any = None
        self._reader: Any = None

    async def connect(self) -> None:
        if self.controller_key is None:
            self._reader, self._writer, _ = await _ws_connect(
                self._host, self._port, self._path
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            self._writer.write(
                (
                    f"GET {self._path} HTTP/1.1\r\nHost: {self._host}:{self._port}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Sec-WebSocket-Protocol: rigplane-controller-v1.{self.controller_key}"
                    "\r\n\r\n"
                ).encode()
            )
            await self._writer.drain()
            response = await asyncio.wait_for(
                self._reader.readuntil(b"\r\n\r\n"), timeout=5.0
            )
            assert response.startswith(b"HTTP/1.1 101 "), response[:200]
        if self._path.startswith("/api/v1/ws"):
            await _ws_skip_handshake(self._reader)

    async def _until(self, matches: Any) -> dict[str, Any]:
        while True:
            opcode, payload = await _ws_recv_frame(self._reader, timeout=10.0)
            if opcode != 0x1:
                continue  # RX audio frames on the audio channel
            message = json.loads(payload)
            if matches(message):
                return message

    async def command(
        self, command_id: str, name: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert self._writer is not None and self._reader is not None
        await _ws_send_text(
            self._writer,
            json.dumps(
                {"type": "cmd", "id": command_id, "name": name, "params": params or {}}
            ),
        )
        return await self._until(
            lambda message: (
                message.get("type") == "response" and message.get("id") == command_id
            )
        )

    async def key(self, command_id: str) -> bool:
        return bool((await self.command(command_id, "ptt_on")).get("ok"))

    async def heartbeat(self) -> None:
        await _ws_send_text(self._writer, json.dumps({"type": "controller_heartbeat"}))
        await self._until(lambda message: message.get("type") == "controller_heartbeat")

    async def send(self, message: dict[str, Any]) -> None:
        await _ws_send_text(self._writer, json.dumps(message))

    async def rigctl(self, lines: str) -> str:
        """One rigctld command over the rigctl channel; its reply (MOR-3536)."""
        await _ws_send_text(self._writer, lines)
        opcode, payload = await _ws_recv_frame(self._reader, timeout=10.0)
        assert opcode == 0x1, (opcode, payload)
        return payload.decode()

    async def send_binary(self, payload: bytes) -> None:
        mask = b"\xde\xad\xbe\xef"
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._writer.write(
            struct.pack("!BBH", 0x82, 0x80 | 126, len(payload)) + mask + masked
        )
        await self._writer.drain()

    def kill(self) -> None:
        if self._writer is not None:
            self._writer.close()

    async def close(self) -> None:
        self.kill()


class _RigctldKeyClient:
    """A rigctld TCP client that keys via ``T 1``."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._writer: Any = None
        self._reader: Any = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )

    async def key(self) -> bool:
        assert self._writer is not None and self._reader is not None
        self._writer.write(b"T 1\n")
        await self._writer.drain()
        line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
        return line.strip() == b"RPRT 0"

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()


# ---------------------------------------------------------------------------
# The stack
# ---------------------------------------------------------------------------


@dataclass
class _Stack:
    sim: MockIcomRadio
    radio: IcomRadio
    composition: ManagedTxComposition
    web: WebServer
    rigctld: RigctldServer
    rigctld_port: int


async def _stand_up_stack(
    tmp_path: Path,
    *,
    loss: str,
    tot_seconds: float,
    unsolicited: bool,
    sim_options: dict[str, Any] | None = None,
    web_max_clients: int | None = None,
) -> _Stack:
    """Build the production stack over the simulator, in the CLI's order."""
    config_path = tmp_path / "managed-tx.json"
    # Written through the production store so the file's shape is exactly
    # what ManagedTxTotConfigStore loads in ManagedTxComposition.
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(tot_seconds)

    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=_KEEPALIVE_HOLD_S,
        stateful_ptt=True,
        ptt_on_session_loss=loss,
        unsolicited_civ=unsolicited,
        **(sim_options or {}),
    )
    await sim.start()

    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=_RECONNECT_DELAY_S,
        reconnect_max_delay=_RECONNECT_MAX_DELAY_S,
        watchdog_timeout=_WATCHDOG_TIMEOUT_S,
    )
    composition = ManagedTxComposition(radio, config_path=config_path)
    install_managed_tx_composition(radio, composition)
    with fast_connect():
        await radio.connect()

    web = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            **({} if web_max_clients is None else {"max_clients": web_max_clients}),
        ),
    )
    prepare_managed_tx_observation_generation(web)
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)

    rigctld = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        allow_not_ready=True,
        managed_tx_authority=composition.authority,
        command_queue=web.command_queue,
        command_service=web.command_service,
    )
    await rigctld.start()
    web.attach_rigctl_channel(rigctld.open_channel)
    await asyncio.wait_for(web.start(), timeout=_GATE_TIMEOUT_S)
    rigctld_port = rigctld._server.sockets[0].getsockname()[1]  # noqa: SLF001
    return _Stack(sim, radio, composition, web, rigctld, rigctld_port)


async def _tear_down_stack(stack: _Stack) -> None:
    with contextlib.suppress(Exception):
        await stack.rigctld.stop()
    with contextlib.suppress(Exception):
        await stack.web.stop()
    with contextlib.suppress(Exception):
        with fast_connect():
            await asyncio.wait_for(stack.radio.disconnect(), 10.0)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(
            stack.composition.shutdown(termination=asyncio.Event()), 5.0
        )
    with contextlib.suppress(Exception):
        await stack.sim.stop()


# ---------------------------------------------------------------------------
# Cell plumbing
# ---------------------------------------------------------------------------


@dataclass
class _Cell:
    lane: str  # "ws" | "rigctld"
    fault: str
    loss: str  # "hold" | "release"

    timeline: list[str] = field(default_factory=list, init=False)

    @property
    def id(self) -> str:
        return f"{self.lane}-{self.fault}-{self.loss}"

    def note(self, text: str) -> None:
        self.timeline.append(text)


_BUDGETS = {
    "ws_kill": _LEASE_BUDGET_S,
    "lease_loss": _LEASE_BUDGET_S,
    "stall": _TOT_BUDGET_S,
    "silence": _TOT_BUDGET_S,
    "drop_notice": _NOTICE_DROP_BUDGET_S,
    "drop_silent": _SILENT_DROP_BUDGET_S,
    "reboot": _REBOOT_BUDGET_S,
}

_WS_FAULTS = (
    "stall",
    "drop_notice",
    "drop_silent",
    "silence",
    "reboot",
    "ws_kill",
    "lease_loss",
)
_RIGCTLD_FAULTS = ("stall", "drop_notice", "drop_silent", "silence", "reboot")

# Faults that keep every CI-V query answered, so the client-side link
# never breaks and the software TOT is the only unkey path: the autonomous
# stream stopping (stall) and the ping path going silent (silence).
_TOT_FAULTS = ("stall", "silence")

# Faults that end the radio session and force a client recovery round.
_LINK_FAULTS = ("drop_notice", "drop_silent", "reboot")


def _cells() -> list[pytest.param]:
    params = []
    for fault in _WS_FAULTS:
        for loss in ("hold", "release"):
            params.append(pytest.param("ws", fault, loss, id=f"ws-{fault}-{loss}"))
    for fault in _RIGCTLD_FAULTS:
        for loss in ("hold", "release"):
            params.append(
                pytest.param("rigctld", fault, loss, id=f"rigctld-{fault}-{loss}")
            )
    return params


async def _wait_ptt(sim: MockIcomRadio, expected: bool, deadline_s: float) -> float:
    """Wait until the simulated PTT equals ``expected``; return the elapsed."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if sim.ptt_on is expected:
            return deadline_s - (end - time.monotonic())
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"simulator PTT stayed {not expected} for {deadline_s:.1f}s; "
        f"transitions={sim.ptt_transitions}"
    )


async def _wait_recovered(
    sim: MockIcomRadio, radio: IcomRadio, old_token: int, deadline_s: float
) -> float:
    """Wait until the client re-claimed the session on the rotated token.

    ``radio.connected`` alone is not recovery evidence: a soft recovery
    round keeps it true. The composite is the one
    tests/testkit/test_reconnect_storm.py waits for — the event fired
    (token rotated), the client is connected, and its token matches the
    simulator's again.
    """
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if (
            sim.token != old_token and radio.connected and radio._token == sim.token  # noqa: SLF001
        ):
            return deadline_s - (end - time.monotonic())
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"radio did not recover within {deadline_s:.1f}s "
        f"(sim token rotated={sim.token != old_token}, connected={radio.connected})"
    )


async def _wait_ptt_observed(web: WebServer, deadline_s: float) -> None:
    """Wait until the poller has observed the PTT field at least once.

    The startup gate (``WebConfig.await_initial_state``) is off because the
    simulator models a narrower CI-V vocabulary than the profile's declared
    startup reads (split ``0x0F`` NAKs, for one); keying instead waits for
    the one observation the TX paths consume — a fresh ``1C 00`` readback.
    """
    path = FieldPath.global_("tx_state", "ptt")
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            web.command_state_store.snapshot().field(path)
            return
        except KeyError:
            await asyncio.sleep(0.05)
    raise AssertionError(f"poller never observed {path} within {deadline_s:.1f}s")


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("lane", "fault", "loss"), _cells())
async def test_tx_under_fault_matrix(
    lane: str,
    fault: str,
    loss: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cell = _Cell(lane, fault, loss)
    budget = _BUDGETS[fault]
    if fault == "drop_silent":
        # Patient OpenClose window shortened for test time, as
        # tests/testkit/test_reconnect_storm.py does for the same fault.
        monkeypatch.setattr("rigplane.runtime._civ_rx._OPENCLOSE_DEADLINE", _PATIENCE_S)
    if fault == "reboot":
        # A post-reboot login inside the busy window must not pause one
        # whole _STATUS_REJECT_COOLDOWN (30 s); cap it to the busy window
        # (tests/testkit/test_reconnect_storm.py does the same).
        monkeypatch.setattr(
            "rigplane.runtime._control_phase.ControlPhaseRuntime"
            "._STATUS_REJECT_COOLDOWN",
            _REBOOT_BUSY_FOR_S,
        )

    stack = await _stand_up_stack(
        tmp_path,
        loss=loss,
        tot_seconds=_STALL_TOT_S if fault in _TOT_FAULTS else _LINK_FAULT_TOT_S,
        unsolicited=fault == "stall",
    )
    client: _WsKeyClient | _RigctldKeyClient
    try:
        sim, radio, composition = stack.sim, stack.radio, stack.composition
        if lane == "ws":
            client = _WsKeyClient("127.0.0.1", stack.web.port)
        else:
            client = _RigctldKeyClient("127.0.0.1", stack.rigctld_port)
        await client.connect()
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)

        # Key through the lane, then prove the lease is held by it.
        keyed = await (client.key("cell-key") if lane == "ws" else client.key())
        assert keyed, f"{cell.id}: the lane refused the key"
        key_elapsed = await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        cell.note(f"keyed in {key_elapsed:.2f}s")
        old_token = sim.token
        projection = await composition.authority.snapshot()
        owner = projection.state.intent.owner_token
        assert projection.state.intent.kind is ManagedTxIntentKind.PTT
        assert isinstance(owner, str) and bool(owner), (
            f"{cell.id}: no lease owner held the key"
        )
        if lane == "rigctld":
            assert owner.startswith("rigctld-client-"), owner

        # Inject the fault.
        if fault == "stall":
            sim.stall_for(_STALL_TOT_S + _TOT_BUDGET_S)
        elif fault == "drop_notice":
            sim.drop_session(at=0.05, notice=True)
        elif fault == "drop_silent":
            sim.drop_session(at=0.05, notice=False)
        elif fault == "silence":
            sim.keepalive_silence(at=0.05, for_seconds=_SILENCE_FOR_S)
        elif fault == "reboot":
            sim.reboot(
                at=_REBOOT_AT_S,
                down_for=_REBOOT_DOWN_FOR_S,
                busy_for=_REBOOT_BUSY_FOR_S,
                notice=True,
            )
        elif fault == "ws_kill":
            client.kill()  # close the socket without an unkey
        elif fault == "lease_loss":
            # The lease lives in ManagedTxAuthority's intent (ptt(owner));
            # it is lost through owner_disconnect — the same call the
            # WebSocket teardown and the rigctld session handback make.
            # Invoked directly here to separate lease loss from socket
            # death.
            loss_task = asyncio.ensure_future(
                composition.authority.owner_disconnect(owner)
            )
            loss_task.add_done_callback(lambda task: task.exception())

        # (1) The simulator's PTT is 0 within the budget.
        ptt0_elapsed = await _wait_ptt(sim, False, budget)
        cell.note(f"ptt=0 in {ptt0_elapsed:.2f}s after fault")

        if fault in _LINK_FAULTS:
            # (2) Recovery: the session is re-claimed against the rotated
            # token (the same composite tests/testkit/test_reconnect_storm.py
            # waits for — ``connected`` alone stays true across a soft
            # recovery round, so it is not recovery evidence).
            recovered = await _wait_recovered(sim, radio, old_token, budget)
            cell.note(f"recovered in {recovered:.2f}s after fault")
        t_settled = time.monotonic()

        # …and nothing re-keys the radio across the settle window.
        settle_end = time.monotonic() + _SETTLE_S
        while time.monotonic() < settle_end:
            assert not sim.ptt_on, (
                f"{cell.id}: radio re-keyed after recovery; writes={sim.ptt_set_writes}"
            )
            await asyncio.sleep(0.05)
        rekeys = [
            write for write in sim.ptt_set_writes if write[1] and write[0] > t_settled
        ]
        assert not rekeys, f"{cell.id}: PTT-on writes after the fault: {rekeys}"

        # (2) Unkey ownership: the authority's lease ended released. The
        # lease itself (intent/owner) flips at the force-off submission;
        # the release DEBT clears when a retry confirms the unkey — see
        # _RELEASE_DEBT_GRACE_S for why that can trail the radio-side
        # unkey under this simulator's NAK vocabulary.
        deadline = time.monotonic() + _RELEASE_DEBT_GRACE_S
        while True:
            projection = await composition.authority.snapshot()
            state = projection.state
            if (
                state.intent.kind is ManagedTxIntentKind.RX
                and not state.release_required
                and state.pending_effect is None
            ):
                break
            if time.monotonic() >= deadline:
                assert state.intent.kind is ManagedTxIntentKind.RX, (
                    f"{cell.id}: lease still held by {state.intent.owner_token!r}"
                )
                assert not state.release_required, (
                    f"{cell.id}: release still owed after "
                    f"{_RELEASE_DEBT_GRACE_S:.1f}s; transitions="
                    f"{sim.ptt_transitions}"
                )
                assert state.pending_effect is None, (
                    f"{cell.id}: effect still pending after "
                    f"{_RELEASE_DEBT_GRACE_S:.1f}s"
                )
                break
            await asyncio.sleep(0.1)
        cell.note("lease released, debt cleared")

        print(f"[{cell.id}] {'; '.join(cell.timeline)}")
    finally:
        with contextlib.suppress(Exception):
            await client.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# MOR-2855: the published managed TX document during a silent session loss
# ---------------------------------------------------------------------------


async def test_silent_drop_keeps_the_published_managed_tx_document_honest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The published document the UI's TX look is built from stays honest.

    The ``ws``-``drop_silent``-``hold`` scenario without the matrix's own
    end-state assertions: while the simulator stays keyed, every sampled
    ``GET /api/v1/managed-transmit`` document must never claim "nothing is
    transmitting and nothing is owed", and once the silence outlives the PTT
    field's max age the observation must age out — an ``on`` after that can
    only come from a NEW post-fault observation, never from the stale
    pre-fault reading.
    """
    # Patient OpenClose window shortened for test time, as the matrix cell
    # for the same fault does.
    monkeypatch.setattr("rigplane.runtime._civ_rx._OPENCLOSE_DEADLINE", _PATIENCE_S)
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=_LINK_FAULT_TOT_S, unsolicited=False
    )
    client = _WsKeyClient("127.0.0.1", stack.web.port)
    try:
        sim, web, composition = stack.sim, stack.web, stack.composition
        await client.connect()
        await _wait_ptt_observed(web, _GATE_TIMEOUT_S)
        assert await client.key("pin-key")
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)

        observed_ptt = web.command_state_store.snapshot().field(OBSERVED_PTT_PATH)
        assert observed_ptt.max_age is not None and observed_ptt.max_age > 0
        # Baseline: the last PTT observation before the fault. Any "on" the
        # document reports later must be backed by an observation NEWER than
        # this one — the stale pre-fault reading must never stand in as TX
        # evidence once the link went silent.
        last_observed_before = observed_ptt.last_observed_monotonic
        stale_after_s = float(observed_ptt.max_age) + 0.5

        fault_at = time.monotonic()
        sim.drop_session(at=0.05, notice=False)

        deadline = time.monotonic() + _SILENT_DROP_BUDGET_S + _RELEASE_DEBT_GRACE_S
        samples = 0
        while True:
            await asyncio.sleep(0.1)
            status, document = await _http_json(
                web.port, "GET", "/api/v1/managed-transmit"
            )
            assert status == 200, document
            samples += 1
            managed = document["managedTransmit"]
            assert managed["status"] == "available", document
            observed = document["txObservation"]["observedPtt"]
            intent_kind = managed["intent"]["kind"]
            owed = managed["releaseRequired"]
            if sim.ptt_on:
                # The radio is still keyed: the document may not claim a
                # clean RX (nothing transmitting, nothing owed).
                assert not (
                    observed == "off" and owed is False and intent_kind == "rx"
                ), f"clean RX claimed while the simulator is keyed: {document}"
            if time.monotonic() - fault_at > stale_after_s and observed == "on":
                field = web.command_state_store.snapshot().field(OBSERVED_PTT_PATH)
                assert field.last_observed_monotonic > last_observed_before, (
                    f"stale 'on' published after the field's max age: {document}"
                )
            state = (await composition.authority.snapshot()).state
            if (
                not sim.ptt_on
                and state.intent.kind is ManagedTxIntentKind.RX
                and not state.release_required
                and state.pending_effect is None
            ):
                break
            assert time.monotonic() < deadline, (
                f"authority never settled after the silent drop: {state}"
            )
        print(f"[ws-drop_silent-hold-pin] settled after {samples} document samples")
    finally:
        with contextlib.suppress(Exception):
            await client.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# MOR-3534: multi-session lanes — one TX holder, many clients
# ---------------------------------------------------------------------------
#
# Each lane opens two controller sessions over the real HTTP route and attaches
# each one's primary control WebSocket. The controller's clock is frozen (a
# fake clock) so a session expires only when a lane advances it.


class _FrozenClock:
    def __init__(self) -> None:
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now


async def _http_json(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        payload = b"" if body is None else json.dumps(body).encode()
        session = "" if key is None else f"X-RigPlane-Controller: {key}\r\n"
        writer.write(
            (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\n{session}"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
        status = int(head.split(b" ", 2)[1])
        length = re.search(rb"(?i)content-length:\s*(\d+)", head)
        data = (
            await asyncio.wait_for(reader.readexactly(int(length.group(1))), 10.0)
            if length
            else b""
        )
        return status, json.loads(data or b"{}")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


_MULTI_SESSION_LANES = (
    "simultaneous",
    "holder_silent",
    "non_holder_refused",
    "sessionless_vs_session",
    "audio_refused",
    "eviction",
)

# CW text, VOX enable and TUNE: the TX starts a non-holder may not issue.
_TX_STARTS = (
    ("send_cw_text", {"text": "TEST"}),
    ("set_vox", {"on": True}),
    ("set_tuner_status", {"value": 2}),
    ("set_break_in", {"mode": 1}),  # MOR-3537: break-in arms CW keying
)

# One browser PCM16 TX frame: the web audio wire header and 480 samples.
_PCM_TX_FRAME = (
    bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
    + b"\x00" * (AUDIO_HEADER_SIZE - 2)
    + b"\x10\x00" * 480
)
_AUDIO_FRAMES = 10
_AUDIO_SETTLE_S = 0.5


@pytest.mark.parametrize("lane", _MULTI_SESSION_LANES)
async def test_multi_session_tx_lanes(lane: str, tmp_path: Path) -> None:
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=_LINK_FAULT_TOT_S, unsolicited=False
    )
    clients: list[_WsKeyClient] = []
    try:
        sim, web, authority = stack.sim, stack.web, stack.composition.authority
        controller = web._controller  # noqa: SLF001
        clock = _FrozenClock()
        controller._clock = clock  # noqa: SLF001
        port = web.port

        async def client(
            key: str | None = None, path: str = "/api/v1/ws"
        ) -> _WsKeyClient:
            ws = _WsKeyClient("127.0.0.1", port, key, path)
            await ws.connect()
            clients.append(ws)
            return ws

        async def session() -> _WsKeyClient:
            status, grant = await _http_json(
                port, "POST", "/api/v1/controller", {"kind": "pro"}
            )
            assert status == 201, grant
            return await client(grant["controller_key"])

        async def released() -> None:
            # The unkey's release debt can trail the radio-side unkey by a
            # few retry cadences (see _RELEASE_DEBT_GRACE_S); keying again
            # before it clears is refused by design.
            deadline = time.monotonic() + _RELEASE_DEBT_GRACE_S
            while True:
                state = (await authority.snapshot()).state
                if (
                    state.intent.kind is ManagedTxIntentKind.RX
                    and not state.release_required
                ):
                    return
                assert time.monotonic() < deadline, f"{lane}: release owed: {state}"
                await asyncio.sleep(0.1)

        a, b = await session(), await session()
        await _wait_ptt_observed(web, _GATE_TIMEOUT_S)

        if lane == "simultaneous":
            results = await asyncio.gather(a.key("a-key"), b.key("b-key"))
            assert sorted(results) == [False, True], results
            holder_client = a if results[0] else b
        else:
            assert await a.key("a-key")
            holder_client = a
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        holder = authority.tx_holder_nowait()
        assert holder is not None and holder.startswith("controller-session-"), holder
        keyed_writes = [write for write in sim.ptt_set_writes if write[1]]

        if lane == "holder_silent":
            # b renews; a stays silent until the clock passes its deadline.
            clock.now += 4.0
            await b.heartbeat()
            clock.now += 3.0
            budget = controller.HEARTBEAT_MS / 1000 + _LEASE_BUDGET_S
            await _wait_ptt(sim, False, budget)
            await b.heartbeat()  # the other session is untouched
        elif lane == "non_holder_refused":
            for name, params in _TX_STARTS:
                response = await b.command(f"b-{name}", name, params)
                assert response.get("ok") is False, response
                assert response.get("message") == "controller_tx_unsupported", response
            assert authority.tx_holder_nowait() == holder
            assert (await holder_client.command("unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        elif lane == "sessionless_vs_session":
            sessionless = await client()
            assert not await sessionless.key("s-key")
            on = {"operation": "transmit_on"}
            path = "/api/v1/managed-transmit/command"
            assert (await _http_json(port, "POST", path, on))[0] == 409
            assert (await _http_json(port, "POST", path, on, b.controller_key))[
                0
            ] == 409
            assert authority.tx_holder_nowait() == holder
            assert (await holder_client.command("unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
            await released()
            # With no holder a sessionless client keys exactly as before.
            assert await sessionless.key("s-key-free")
            await _wait_ptt(sim, True, _KEY_DEADLINE_S)
            assert (await sessionless.command("s-unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        elif lane == "audio_refused":
            # The simulator's LAN audio stream does not open in this stack,
            # so the verdict is read at the server's TX audio gate: every
            # browser TX frame passes it before it can reach the radio.
            verdicts: list[tuple[str | None, bool]] = []
            gate = web._controller_audio_gate  # noqa: SLF001

            async def recorded(ticket: Any, source: object) -> bool:
                verdict = await gate(ticket, source)
                verdicts.append((ticket.tx_owner, verdict))
                return verdict

            web._controller_audio_gate = recorded  # type: ignore[method-assign]  # noqa: SLF001
            mics = {}
            for name, owner in (("holder", holder_client), ("other", b)):
                mics[name] = await client(owner.controller_key, "/api/v1/audio")
                await mics[name].send({"type": "audio_start", "direction": "tx"})
                await mics[name]._until(
                    lambda message: message.get("type") == "audio_tx_format"
                )
            for name in ("other", "holder"):
                for _ in range(_AUDIO_FRAMES):
                    await mics[name].send_binary(_PCM_TX_FRAME)
            end = time.monotonic() + _AUDIO_SETTLE_S * 4
            while len(verdicts) < 2 * _AUDIO_FRAMES and time.monotonic() < end:
                await asyncio.sleep(0.05)
            assert authority.tx_holder_nowait() == holder
            assert {verdict for owner, verdict in verdicts if owner != holder} == {
                False
            }, verdicts
            assert any(owner == holder and verdict for owner, verdict in verdicts)
            assert (await holder_client.command("unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        elif lane == "eviction":
            # Ten control channels in the holder's own session, nine in
            # another session and ten sessionless ones, all from one IP.
            auxiliary = "/api/v1/ws?controller_role=auxiliary"
            for _ in range(9):
                await client(holder_client.controller_key, auxiliary)
            for _ in range(8):
                await client(b.controller_key, auxiliary)
            for _ in range(10):
                await client()
            await holder_client.heartbeat()
            await b.heartbeat()
            assert sim.ptt_on and authority.tx_holder_nowait() == holder
            assert (await holder_client.command("unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        else:
            assert (await holder_client.command("unkey", "ptt_off")).get("ok")
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)

        if lane != "sessionless_vs_session":
            # Nobody but the holder ever keyed the radio.
            assert [w for w in sim.ptt_set_writes if w[1]] == keyed_writes
        await released()
    finally:
        for ws in clients:
            with contextlib.suppress(Exception):
                await ws.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# MOR-3537: a TX no owner keyed — the operator's own mic or PTT at the radio
# ---------------------------------------------------------------------------


async def _wait_rf(web: WebServer, expected: RfState, deadline_s: float) -> None:
    """Wait until the poller's PTT readback resolves to ``expected``."""
    end = time.monotonic() + deadline_s
    while resolve_rf_state(web.command_state_store.snapshot()) is not expected:
        assert time.monotonic() < end, f"radio never observed {expected} PTT"
        await asyncio.sleep(0.05)


async def _wait_released(composition: ManagedTxComposition) -> None:
    """Wait until Core holds and owes no TX (see _RELEASE_DEBT_GRACE_S)."""
    end = time.monotonic() + _RELEASE_DEBT_GRACE_S
    while True:
        state = (await composition.authority.snapshot()).state
        if state.intent.kind is ManagedTxIntentKind.RX and not state.release_required:
            return
        assert time.monotonic() < end, f"release still owed: {state}"
        await asyncio.sleep(0.1)


async def test_a_tx_no_owner_keyed_is_never_keyed_over_and_force_off_unkeys_it(
    tmp_path: Path,
) -> None:
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=_LINK_FAULT_TOT_S, unsolicited=False
    )
    clients: list[_WsKeyClient] = []
    rig = _RigctldKeyClient("127.0.0.1", stack.rigctld_port)
    try:
        sim, web = stack.sim, stack.web
        port = web.port
        # A frozen controller clock: the session never expires mid-test.
        web._controller._clock = _FrozenClock()  # noqa: SLF001
        status, grant = await _http_json(
            port, "POST", "/api/v1/controller", {"kind": "pro"}
        )
        assert status == 201, grant
        key = grant["controller_key"]
        for ws in (
            _WsKeyClient("127.0.0.1", port, key),
            _WsKeyClient("127.0.0.1", port),
        ):
            await ws.connect()
            clients.append(ws)
        session, sessionless = clients
        await rig.connect()
        await _wait_ptt_observed(web, _GATE_TIMEOUT_S)

        writes = len(sim.ptt_set_writes)
        # The operator keys the radio's own mic: no session, no wire write.
        sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
        await _wait_rf(web, RfState.TX, _GATE_TIMEOUT_S)

        for ws, command_id in ((session, "s-key"), (sessionless, "x-key")):
            response = await ws.command(command_id, "ptt_on")
            assert response.get("ok") is False, response
            assert response.get("message") == "radio_tx_unowned", response
            # Nor do CW text, VOX, TUNE or break-in key over it.
            for name, params in _TX_STARTS:
                response = await ws.command(f"{command_id}-{name}", name, params)
                assert response.get("ok") is False, response
                assert response.get("message") == "radio_tx_unowned", response
        assert rig._writer is not None and rig._reader is not None
        for line in (b"T 1\n", b"U VOX 1\n"):
            rig._writer.write(line)
            await rig._writer.drain()
            reply = await asyncio.wait_for(rig._reader.readline(), 10.0)
            assert reply == b"RPRT -9\n", (line, reply)
        path = "/api/v1/managed-transmit/command"
        on = {"operation": "transmit_on"}
        assert await _http_json(port, "POST", path, on, key) == (
            409,
            {"error": "radio_tx_unowned"},
        )
        # The refusals sent nothing to the radio.
        assert sim.ptt_on and sim.ptt_set_writes[writes:] == []
        # MOR-3561: the emergency release is never gated by who keyed; it
        # unkeys even the operator's TX.
        off = await _http_json(port, "POST", path, {"operation": "force_off"})
        assert off[0] == 202, off
        await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        assert sim.ptt_set_writes[writes][1] is False

        # With the radio back on RX, keying works again.
        await _wait_rf(web, RfState.RX, _GATE_TIMEOUT_S)
        await _wait_released(stack.composition)
        assert await session.key("s-key-free")
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        assert (await session.command("s-unkey", "ptt_off")).get("ok")
        await _wait_ptt(sim, False, _LEASE_BUDGET_S)
    finally:
        for ws in clients:
            with contextlib.suppress(Exception):
                await ws.close()
        await rig.close()
        await _tear_down_stack(stack)


async def test_core_tx_the_radio_kept_after_an_acked_unkey_still_gets_unkeyed(
    tmp_path: Path,
) -> None:
    """MOR-3561: the radio ACKs Core's unkey but stays keyed."""
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=_LINK_FAULT_TOT_S, unsolicited=False
    )
    clients: list[_WsKeyClient] = []
    try:
        sim, web = stack.sim, stack.web
        port = web.port
        # A frozen controller clock: the session never expires mid-test.
        web._controller._clock = _FrozenClock()  # noqa: SLF001
        status, grant = await _http_json(
            port, "POST", "/api/v1/controller", {"kind": "pro"}
        )
        assert status == 201, grant
        session = _WsKeyClient("127.0.0.1", port, grant["controller_key"])
        await session.connect()
        clients.append(session)
        await _wait_ptt_observed(web, _GATE_TIMEOUT_S)
        assert await session.key("key")
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)

        # From here the radio ACKs every de-key write but stays keyed.
        honour = sim._set_simulated_ptt  # noqa: SLF001

        def acked_but_kept(on: bool, *, wire_set: bool) -> None:
            if wire_set and not on:
                sim.ptt_set_writes.append((time.monotonic(), False))
                return
            honour(on, wire_set=wire_set)

        sim._set_simulated_ptt = acked_but_kept  # type: ignore[method-assign]  # noqa: SLF001
        assert (await session.command("unkey", "ptt_off")).get("ok")
        await _wait_released(stack.composition)
        released_at = time.monotonic()
        # The next poll reads TX with no RX since Core's unkey: Core's own TX,
        # not foreign. Nothing keys over it.
        ptt = FieldPath.global_("tx_state", "ptt")
        end = released_at + _GATE_TIMEOUT_S
        while True:
            snapshot = web.command_state_store.snapshot()
            if (
                resolve_rf_state(snapshot) is RfState.TX
                and snapshot.field(ptt).last_observed_monotonic > released_at
            ):
                break
            assert time.monotonic() < end, "no poll after the unkey read TX"
            await asyncio.sleep(0.05)
        for name, params in (("ptt_on", {}), *_TX_STARTS):
            response = await session.command(f"kept-{name}", name, params)
            assert response.get("ok") is False, response
            assert response.get("message") == "radio_tx_unreleased", response
        assert sim.ptt_on

        # The radio honours de-keys again; force_off is not blocked by any of
        # those refusals and sends the unkey.
        sim._set_simulated_ptt = honour  # type: ignore[method-assign]  # noqa: SLF001
        writes = len(sim.ptt_set_writes)
        path = "/api/v1/managed-transmit/command"
        off = await _http_json(port, "POST", path, {"operation": "force_off"})
        assert off[0] == 202, off
        await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        assert sim.ptt_set_writes[writes][1] is False
    finally:
        for ws in clients:
            with contextlib.suppress(Exception):
                await ws.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# MOR-3536: the rigctl channel over WebSocket, bound to the client's session
# ---------------------------------------------------------------------------
#
# rigctld lines sent over /api/v1/rigctl key as the session the channel joined:
# that session's audio is admitted, another session's T 1 is refused while it
# holds, and the WebSocket closing (or the session ending on a silent link)
# releases the TX.

_RIGCTL_LANES = ("keys_as_session", "ws_close_releases", "session_expiry_releases")


@pytest.mark.parametrize("lane", _RIGCTL_LANES)
async def test_rigctl_ws_lanes(lane: str, tmp_path: Path) -> None:
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=_LINK_FAULT_TOT_S, unsolicited=False
    )
    clients: list[_WsKeyClient] = []
    try:
        sim, web, authority = stack.sim, stack.web, stack.composition.authority
        controller = web._controller  # noqa: SLF001
        clock = _FrozenClock()
        controller._clock = clock  # noqa: SLF001
        port = web.port

        async def client(key: str | None, path: str) -> _WsKeyClient:
            ws = _WsKeyClient("127.0.0.1", port, key, path)
            await ws.connect()
            clients.append(ws)
            return ws

        async def session() -> tuple[_WsKeyClient, _WsKeyClient, str]:
            status, grant = await _http_json(
                port, "POST", "/api/v1/controller", {"kind": "pro"}
            )
            assert status == 201, grant
            key = grant["controller_key"]
            primary = await client(key, "/api/v1/ws")
            owner = next(
                owner
                for owner, entry in controller._sessions.items()  # noqa: SLF001
                if entry.key == key
            )
            return primary, await client(key, "/api/v1/rigctl"), owner

        # A sessionless rigctl channel is refused before the upgrade.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            (
                f"GET /api/v1/rigctl HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
        writer.close()
        assert head.startswith(b"HTTP/1.1 403 "), head

        (a, a_cat, a_owner), (b, b_cat, b_owner) = await session(), await session()
        await _wait_ptt_observed(web, _GATE_TIMEOUT_S)

        assert await a_cat.rigctl("T 1\n") == "RPRT 0\n"
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        assert authority.tx_holder_nowait() == a_owner
        keyed_writes = [write for write in sim.ptt_set_writes if write[1]]

        if lane == "keys_as_session":
            # Another session keys neither over rigctl nor over its control WS.
            assert await b_cat.rigctl("T 1\n") == "RPRT -9\n"
            assert not await b.key("b-key")
            assert authority.tx_holder_nowait() == a_owner
            # The holder session's own TX audio passes the gate; b's does not.
            verdicts: list[tuple[str | None, bool]] = []
            gate = web._controller_audio_gate  # noqa: SLF001

            async def recorded(ticket: Any, source: object) -> bool:
                verdict = await gate(ticket, source)
                verdicts.append((ticket.tx_owner, verdict))
                return verdict

            web._controller_audio_gate = recorded  # type: ignore[method-assign]  # noqa: SLF001
            for owner_client in (b, a):
                mic = await client(owner_client.controller_key, "/api/v1/audio")
                await mic.send({"type": "audio_start", "direction": "tx"})
                await mic._until(lambda m: m.get("type") == "audio_tx_format")
                for _ in range(_AUDIO_FRAMES):
                    await mic.send_binary(_PCM_TX_FRAME)
            end = time.monotonic() + _AUDIO_SETTLE_S * 4
            while len(verdicts) < 2 * _AUDIO_FRAMES and time.monotonic() < end:
                await asyncio.sleep(0.05)
            assert {v for owner, v in verdicts if owner == b_owner} == {False}, verdicts
            assert any(owner == a_owner and v for owner, v in verdicts), verdicts
            assert await a_cat.rigctl("T 0\n") == "RPRT 0\n"
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
        elif lane == "ws_close_releases":
            await a_cat.close()
            await _wait_ptt(sim, False, _LEASE_BUDGET_S)
            await a.heartbeat()  # only the channel closed; the session lives
        else:
            # Half-open: a's channels stay open but nothing reaches the server;
            # b renews while the clock passes a's deadline.
            clock.now += 4.0
            await b.heartbeat()
            clock.now += 3.0
            await _wait_ptt(
                sim, False, controller.HEARTBEAT_MS / 1000 + _LEASE_BUDGET_S
            )
            await b.heartbeat()

        # Nobody but the holder session ever keyed the radio.
        assert [w for w in sim.ptt_set_writes if w[1]] == keyed_writes
        deadline = time.monotonic() + _RELEASE_DEBT_GRACE_S
        while authority.tx_holder_nowait() is not None:
            assert time.monotonic() < deadline, f"{lane}: TX still held"
            await asyncio.sleep(0.1)
    finally:
        for ws in clients:
            with contextlib.suppress(Exception):
                await ws.close()
        await _tear_down_stack(stack)
