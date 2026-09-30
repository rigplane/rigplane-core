"""MOR-3064: connect-time identity gate for the Yaesu CAT backend.

Pins the ``ID;`` gate in :class:`rigplane.backends.yaesu_cat.radio.YaesuCatRadio`:
every connect probes the radio's model ID before any IF seeding, ``connected``
never latches on a bare transport-open, silence/malformed answers hold the
open port under a bounded re-read, a wrong radio records
``identity_mismatch``, and only POWER ON passes while the hold owns the port.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import (
    CatTimeoutError,
    CatTransportError,
)
from rigplane.core.radio_protocol import (
    RadioIdentity,
    RadioIdentityStatus,
    serial_identity_hold,
)
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.rig_loader import load_rig

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
# Bench-measured FTX-1 IF; frame (see tests/test_ftx1_radio.py,
# TestGetIfStatus.MEASURED_FRAME) — used verbatim so the seed assertions
# ride on a frame the parser is already known to accept.
_IF_FRAME = "IF00000014228000+000000200003"


class FakeCatTransport:
    """Scripted stand-in for ``YaesuCatTransport`` (no serial I/O).

    ``answers`` are consumed one per ``query`` in order; an exhausted list
    answers with ``CatTimeoutError`` (a silent radio). An ``Exception`` entry
    is raised instead of returned. ``on_probe`` runs before each ``ID;``
    answer resolves, so a test can mutate the radio mid-probe.
    """

    def __init__(
        self,
        answers: Sequence[str | Exception] = (),
        *,
        open_port: bool = True,
    ) -> None:
        self.connected = False
        self.open_port = open_port
        self.connect_calls = 0
        self.close_calls = 0
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.answers: list[str | Exception] = list(answers)
        self.probe_gate: asyncio.Event | None = None
        self.on_probe: Callable[[], None] | None = None

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.open_port:
            self.connected = True

    async def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    async def query(self, command: str, **_: object) -> str:
        self.queries.append(command)
        if not self.connected:
            raise CatTransportError("Transport not connected")
        if command == "ID;":
            if self.on_probe is not None:
                self.on_probe()
            if self.probe_gate is not None:
                await self.probe_gate.wait()
        if not self.answers:
            raise CatTimeoutError(f"Read timeout waiting for {command!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def write(self, command: str, **_: object) -> None:
        self.writes.append(command)
        if not self.connected:
            raise CatTransportError("Transport not connected")


def make_radio(
    *,
    expected_ids: tuple[str, ...] = (),
    answers: Sequence[str | Exception] = (),
    open_port: bool = True,
) -> tuple[YaesuCatRadio, FakeCatTransport]:
    """Build a real ``YaesuCatRadio`` over a scripted fake transport.

    ``expected_ids`` always overrides the profile's ``[identity]`` block so
    the pins keep their meaning when real profiles gain expected IDs.
    """
    config = replace(
        load_rig(_RIGS_DIR / "ftx1.toml"), expected_identity_ids=expected_ids
    )
    driver = SimpleNamespace(stop_rx=AsyncMock(), stop_tx=AsyncMock())
    radio = YaesuCatRadio("/dev/fake-cat", profile=config, audio_driver=driver)
    transport = FakeCatTransport(answers, open_port=open_port)
    radio._transport = transport
    return radio, transport


def _fast_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(YaesuCatRadio, "_IDENTITY_REREAD_BACKOFF_S", (0.01,))
    monkeypatch.setattr(YaesuCatRadio, "_IDENTITY_REREAD_STEADY_S", 0.01)


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


# ---------------------------------------------------------------------------
# Identity statuses
# ---------------------------------------------------------------------------


async def test_identity_is_none_before_any_connect() -> None:
    radio, _transport = make_radio()
    assert radio.connection_identity is None


async def test_verified_answer_completes_the_connect_and_probes_before_if() -> None:
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID0840", _IF_FRAME])
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    assert identity.expected_model == "FTX-1"
    assert identity.answered_id == "0840"
    assert identity.answered_model == "FTX-1"
    assert radio.connected is True
    assert radio.radio_ready is True
    # The ID probe runs BEFORE the IF seed (MOR-3064 gate order).
    assert transport.queries[:2] == ["ID;", "IF;"]


async def test_answer_without_expected_metadata_stays_unverified() -> None:
    radio, transport = make_radio(answers=["ID0840", _IF_FRAME])
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "0840"
    assert identity.answered_model is None
    assert radio.connected is True
    assert transport.queries[:2] == ["ID;", "IF;"]


async def test_profile_without_identity_read_connects_unverified() -> None:
    config = load_rig(_RIGS_DIR / "ftx1.toml")
    del config.commands["get_id"]
    radio = YaesuCatRadio(
        "/dev/fake-cat",
        profile=config,
        audio_driver=SimpleNamespace(stop_rx=AsyncMock(), stop_tx=AsyncMock()),
    )
    transport = FakeCatTransport([_IF_FRAME])
    radio._transport = transport

    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id is None
    # No ID probe was possible; the IF seed is the first command.
    assert transport.queries[0] == "IF;"
    assert radio.connected is True


async def test_wrong_radio_answer_records_identity_mismatch_and_holds() -> None:
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID1234"])
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.IDENTITY_MISMATCH
    assert identity.answered_id == "1234"
    assert radio.connected is False
    assert radio.radio_ready is False
    # No IF seeding, no writes of any kind for the wrong radio.
    assert "IF;" not in transport.queries
    assert transport.writes == []
    with pytest.raises(RadioConnectionError):
        await radio.set_freq(14_074_000)
    with pytest.raises(RadioConnectionError):
        await radio.get_freq()
    assert transport.writes == []
    assert transport.queries == ["ID;"]
    await radio.disconnect()


async def test_silent_radio_holds_no_response_without_false_connected() -> None:
    radio, transport = make_radio(expected_ids=("0840",))
    await radio.connect()  # must not raise on silence

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.NO_RESPONSE
    assert radio.connected is False
    assert radio.radio_ready is False
    assert transport.connect_calls == 1  # port stays open under the hold
    assert "IF;" not in transport.queries
    assert transport.writes == []
    task = radio._identity_reread_task
    assert task is not None and not task.done()
    await radio.disconnect()


async def test_malformed_id_answer_reads_as_no_response() -> None:
    # "ID08;" fails the profile's ``ID{model:04d};`` parse template.
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID08"])
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.NO_RESPONSE
    assert identity.answered_id is None
    assert radio.connected is False
    await radio.disconnect()


async def test_transport_error_on_live_port_holds_no_response() -> None:
    radio, transport = make_radio(
        expected_ids=("0840",), answers=[CatTransportError("write failed: EIO")]
    )
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.NO_RESPONSE
    assert radio.connected is False
    assert transport.connected is True  # the live port is held, not reopened
    await radio.disconnect()


async def test_probe_with_dead_transport_raises_connection_error() -> None:
    radio, transport = make_radio(expected_ids=("0840",), open_port=False)
    with pytest.raises(RadioConnectionError):
        await radio.connect()

    assert radio.connection_identity is None
    assert radio.connected is False


# ---------------------------------------------------------------------------
# Gate mechanics: CHECKING reset, generation fence, re-read, disconnect
# ---------------------------------------------------------------------------


async def test_preopened_transport_still_requires_the_identity_query() -> None:
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID0840", _IF_FRAME])
    transport.connected = True

    await radio.connect()

    assert transport.queries == ["ID;", "IF;"]
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.VERIFIED


@pytest.mark.parametrize("answers", [[], ["ID1234"]])
async def test_disconnect_does_not_release_commands_before_port_close(
    answers: list[str],
) -> None:
    radio, transport = make_radio(expected_ids=("0840",), answers=answers)
    await radio.connect()
    stopping = asyncio.Event()
    release = asyncio.Event()

    async def stop_rx() -> None:
        stopping.set()
        await release.wait()

    radio._audio_driver.stop_rx = stop_rx
    task = asyncio.create_task(radio.disconnect())
    await stopping.wait()
    try:
        assert transport.connected
        assert not radio.connected
        with pytest.raises(RadioConnectionError):
            await radio.set_freq(14_074_000)
        assert transport.writes == []
    finally:
        release.set()
        await task


async def test_connect_resets_identity_to_checking_with_fresh_generation() -> None:
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID0840", _IF_FRAME])
    transport.probe_gate = asyncio.Event()
    generation_before = radio._identity_generation

    task = asyncio.create_task(radio.connect())
    assert await _wait_until(lambda: transport.queries == ["ID;"])

    assert radio._identity_generation == generation_before + 1
    gate_identity = radio.connection_identity
    assert gate_identity is not None
    assert gate_identity.status is RadioIdentityStatus.CHECKING
    assert radio.connected is False

    transport.probe_gate.set()
    await task
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.VERIFIED


async def test_reread_reprobes_the_held_port_and_seeds_state_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE

    transport.answers.append("ID0840")
    assert await _wait_until(
        lambda: (
            radio.connection_identity is not None
            and radio.connection_identity.status is RadioIdentityStatus.VERIFIED
        )
    )
    await asyncio.sleep(0.05)  # let any stray double completion run

    # Same port: the re-read never reopens the serial port.
    assert transport.connect_calls == 1
    assert transport.queries.count("ID;") >= 2  # gate probe plus re-reads
    # State initialization (IF seeding) runs exactly once per generation.
    assert transport.queries.count("IF;") == 1
    assert radio.connected is True

    await radio.connect()  # idempotent while verified and connected
    assert transport.queries.count("IF;") == 1


async def test_mismatch_hold_reread_picks_up_the_expected_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID1234"])
    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.IDENTITY_MISMATCH

    transport.answers.append("ID0840")
    assert await _wait_until(
        lambda: (
            radio.connection_identity is not None
            and radio.connection_identity.status is RadioIdentityStatus.VERIFIED
        )
    )
    assert transport.connect_calls == 1  # no reopen while the wrong radio held it
    assert transport.queries.count("IF;") == 1
    assert radio.connected is True


async def test_disconnect_cancels_the_reread_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    await radio.connect()
    task = radio._identity_reread_task
    assert task is not None and not task.done()

    await radio.disconnect()
    await asyncio.sleep(0)  # let the cancellation land

    assert task.done()  # the loop swallows CancelledError like the Icom gate
    assert radio.connection_identity is None
    assert transport.close_calls == 1


async def test_stale_generation_task_exits_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model=radio.model
    )
    radio._identity_generation = 5
    radio._start_identity_reread()
    task = radio._identity_reread_task
    assert task is not None

    # A new link happened elsewhere: the old hold's completion is fenced out.
    radio._identity_generation = 6
    transport.answers.append("ID0840")

    assert await _wait_until(lambda: task.done())
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
    assert transport.queries == []


async def test_answer_that_outlives_its_generation_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    transport.connected = True  # hand-built hold: the port is live
    transport.answers.append("ID0840")

    def replug_during_probe() -> None:
        radio._identity_generation += 1  # disconnect/replug raced the probe

    transport.on_probe = replug_during_probe
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model=radio.model
    )
    radio._start_identity_reread()
    task = radio._identity_reread_task
    assert task is not None

    assert await _wait_until(lambda: task.done())
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
    # The stale answer never seeded state on the new generation.
    assert "IF;" not in transport.queries


# ---------------------------------------------------------------------------
# Narrow POWER ON allowance (MOR-3064)
# ---------------------------------------------------------------------------


async def test_power_on_during_no_response_hold_writes_ps1_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE

    await radio.set_powerstat(True)

    assert transport.writes == ["PS1;"]
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
    await radio.disconnect()


async def test_power_off_during_no_response_hold_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",))
    await radio.connect()

    with pytest.raises(RadioConnectionError):
        await radio.set_powerstat(False)
    assert transport.writes == []
    await radio.disconnect()


async def test_power_on_for_a_wrong_radio_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID1234"])
    await radio.connect()
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.IDENTITY_MISMATCH

    with pytest.raises(RadioConnectionError):
        await radio.set_powerstat(True)
    assert transport.writes == []
    await radio.disconnect()


async def test_set_powerstat_takes_the_normal_path_once_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, transport = make_radio(expected_ids=("0840",), answers=["ID0840", _IF_FRAME])
    await radio.connect()

    await radio.set_powerstat(True)

    assert transport.writes == ["PS1;"]
    assert radio.connection_identity is not None
    assert radio.connection_identity.status is RadioIdentityStatus.VERIFIED


# ---------------------------------------------------------------------------
# Shared hold predicate / composition surface
# ---------------------------------------------------------------------------


async def test_serial_identity_hold_predicate_reads_the_yaesu_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_reread(monkeypatch)
    radio, _transport = make_radio(expected_ids=("0840",))
    assert serial_identity_hold(radio) is None  # identity None: no hold

    await radio.connect()
    assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE

    _transport.answers.append("ID0840")
    assert await _wait_until(
        lambda: (
            radio.connection_identity is not None
            and radio.connection_identity.status is RadioIdentityStatus.VERIFIED
        )
    )
    assert radio.connected is True
    assert serial_identity_hold(radio) is None  # answered: the hold is gone
