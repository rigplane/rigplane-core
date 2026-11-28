"""MOR-4481: refused transmit-state reads name the wire command and the cause.

A Yaesu startup where the PTT (``TX;``) and split (``ST;``) reads are
refused: the gate still binds (both left the critical set), but never
silently — the release WARNING names each path with the real wire command
and the ``refused Nx`` cause read off the scheduler counters the backend
filed. Plus unit tests for the filing contract itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.rigctld_client.transport import RigctldCommandError
from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.transport import CatCommandRejected
from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.startup_read_notes import StartupReadNote, record_direct_read_outcome
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.profiles import RadioProfile
from rigplane.radio_state import RadioState
from rigplane.testing.timing import Clock, ManualClock, VirtualClock, virtual_clock
from rigplane.web import web_startup
from rigplane.web.server import WebConfig, WebServer

FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
MODE = FieldPath.active("main", "freq_mode", "mode")
PTT = FieldPath.global_("tx_state", "ptt")
SPLIT = FieldPath.global_("tx_state", "split")


def _tx_profile() -> RadioAcquisitionProfile:
    """Startup-required freq/mode plus the demoted transmit-state pair."""

    paths = (FREQ, MODE, PTT, SPLIT)
    return RadioAcquisitionProfile(
        provider="yaesu_cat",
        capabilities=tuple(FieldCapability(path=path, polling=True) for path in paths),
        field_policies={
            path: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
            for path in paths
        },
    )


class _RefusedTxPoller:
    """Drive the real adapter's medium + TX-control lanes in a loop."""

    def __init__(
        self,
        callback: Callable[[Sequence[Observation]], None],
        radio: object,
        profile: RadioAcquisitionProfile,
        *,
        clock: Clock,
        extra: Callable[[], Sequence[Observation]] | None = None,
    ) -> None:
        self._callback = callback
        self._radio = radio
        self._profile = profile
        self._clock = clock
        self._extra = extra or (lambda: ())

    async def start(self) -> None:
        while True:
            adapter = YaesuObservationAdapter(
                self._radio,  # type: ignore[arg-type]
                profile=self._profile,
                clock=self._clock,
            )
            batch = [
                *await adapter.poll_medium(),
                *await adapter.poll_tx_controls(),
                *self._extra(),
            ]
            self._callback(tuple(batch))
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        return None

    def bind_provider_generation(self, *, capture: object, advance: object) -> None:
        return None


class _RefusedTransmitRadio:
    """Answers freq/mode, refuses the transmit-state reads on the wire."""

    backend_id = "yaesu_cat"
    model = "FAKE-REFUSED-TX"
    capabilities = {"split"}
    connected = control_connected = radio_ready = True

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else ManualClock(start=100.0)
        self.radio_state = RadioState()
        self._state_store = StateStore()
        self._acquisition_scheduler = AcquisitionScheduler(profile=_tx_profile())
        self._poll_warned_fields: set[str] = set()
        self._INITIAL_STATE_GAP_SERIAL = 0.005

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return "USB", 2400

    async def read_transmit_state(self) -> TxStateReading:
        # Production shape: the primitive swallows the `?;` into a
        # failure-tagged reading that still carries the wire command.
        return TxStateReading(value=None, failure="read-error", command="TX;")

    async def read_split(self) -> bool:
        raise CatCommandRejected(
            "Radio rejected command 'ST;' (returned '?;')", command="ST;"
        )

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    def supports_command(self, _command: str) -> bool:
        return False

    def create_observation_poller(
        self, *, callback: Callable[[Sequence[Observation]], None], **_kwargs: object
    ) -> object:
        return _RefusedTxPoller(
            callback,
            self,
            self._acquisition_scheduler._profile,
            clock=self._clock,
        )

    async def __aenter__(self) -> "_RefusedTransmitRadio":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeAsyncServer:
    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _gated_config() -> WebConfig:
    return WebConfig(
        host="127.0.0.1",
        port=0,
        discovery=False,
        await_initial_state=True,
    )


@virtual_clock
@pytest.mark.usefixtures("isolated_eibi_cache")
async def test_yaesu_refused_ptt_and_split_bind_with_named_warning(
    clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refused ``TX;``/``ST;`` bind the server with a naming WARNING.

    Freq/mode answer, PTT and split refuse every cycle: both leave the
    outstanding set as unavailable, the gate releases (no silent release),
    and the release WARNING names each path with its real wire command
    and its ``refused Nx`` cause. No startup defect is recorded.
    """
    clock.scope_to(monkeypatch, web_startup)
    radio = _RefusedTransmitRadio(clock=clock)
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with (
        patch("rigplane.web.web_startup.asyncio.start_server", new=_bind),
        caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"),
    ):
        try:
            await server.start()
            assert binds == ["bind"]
            assert scheduler.startup_defect is None
            assert scheduler.consecutive_refusals(PTT) >= 1
            assert scheduler.consecutive_refusals(SPLIT) >= 1
            assert PTT in scheduler.refused_paths()
            assert SPLIT in scheduler.refused_paths()
            observed = {
                field.path for field in server.command_state_store.snapshot().fields
            }
            assert PTT not in scheduler.unobserved_startup_paths(tuple(observed))
            assert SPLIT not in scheduler.unobserved_startup_paths(tuple(observed))
            assert FREQ in observed
            assert MODE in observed
        finally:
            await server.stop()

    gate_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.web.web_startup"
        and "serving without them" in record.getMessage()
    ]
    assert gate_warnings, "the refused reads must never release the gate silently"
    named = " ".join(gate_warnings)
    assert "global.tx_state.ptt" in named
    assert "global.tx_state.split" in named
    assert "command 'TX;'" in named
    assert "command 'ST;'" in named
    assert "refused" in named


def _note_radio(scheduler: AcquisitionScheduler | None) -> Any:
    radio = SimpleNamespace()
    if scheduler is not None:
        radio._acquisition_scheduler = scheduler
    return radio


def _note_profile() -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(FieldCapability(path=PTT, polling=True),),
        field_policies={
            PTT: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
        },
    )


def test_direct_refusal_files_scheduler_record_and_note() -> None:
    """A refused direct read populates the scheduler refusal record."""

    scheduler = AcquisitionScheduler(profile=_note_profile())
    radio = _note_radio(scheduler)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="ptt", path=PTT, command="t", outcome="refused"),
        now=10.0,
    )
    assert scheduler.consecutive_refusals(PTT) == 1
    assert PTT in scheduler.refused_paths()
    assert PTT not in scheduler.unobserved_startup_paths(())
    assert radio._startup_read_notes[PTT].command == "t"
    refusal = scheduler.diagnostics()["readRefusals"][str(PTT)]
    assert refusal["command"] == "t"


def test_direct_malformed_files_refusal_mark() -> None:
    """A malformed direct read marks the path unavailable without a defect."""

    scheduler = AcquisitionScheduler(profile=_note_profile())
    radio = _note_radio(scheduler)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="ptt", path=PTT, command="TX;", outcome="malformed"),
        now=10.0,
    )
    assert scheduler.consecutive_refusals(PTT) == 1
    assert scheduler.startup_defect is None
    refusal = scheduler.diagnostics()["readRefusals"][str(PTT)]
    assert "invalid reply" in refusal["message"]
    assert radio._startup_read_notes[PTT].outcome == "malformed"


def test_direct_timeout_stashes_evidence_only() -> None:
    """A demoted timeout stashes wire evidence and records nothing.

    Timeouts keep their MOR-4456 containment: no scheduler mark, the path
    stays outstanding, and only the stashed note names the wire command.
    """

    scheduler = AcquisitionScheduler(profile=_note_profile())
    radio = _note_radio(scheduler)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="ptt", path=PTT, command="t", outcome="timeout"),
        now=10.0,
    )
    assert scheduler.consecutive_refusals(PTT) == 0
    assert scheduler.refused_paths() == frozenset()
    assert PTT in scheduler.unobserved_startup_paths(())
    assert radio._startup_read_notes[PTT].command == "t"


def test_direct_outcome_without_scheduler_stashes_only() -> None:
    """Test doubles without a scheduler only collect the wire evidence."""

    radio = _note_radio(None)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="ptt", path=PTT, command="TX;", outcome="refused"),
        now=10.0,
    )
    assert radio._startup_read_notes[PTT].command == "TX;"


def _scope_edge_observation(clock: Clock) -> Observation:
    """A valid observed scope edge (slot 2, fresh) for the P1 regression."""

    return Observation(
        path=FieldPath.scope_control("display", "edge"),
        value=2,
        timestamp_monotonic=clock(),
        source=SourceMetadata(source="poll_response", provider="yaesu_cat"),
    )


class _ScopedRefusedTransmitRadio(_RefusedTransmitRadio):
    """The refused-TX fake carrying a real RadioProfile (P1 regression).

    The store sees a valid scope edge every cycle, so the gate's warning
    path reaches ``scope_fixed_edge_slot`` — which must read THIS profile
    (``receiver_count``, scope ranges), never the scheduler's acquisition
    profile.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock=clock)
        self._profile = RadioProfile(
            id="test-scoped-rig",
            model="FAKE-SCOPED-TX",
            civ_addr=0x94,
            receiver_count=1,
            capabilities=frozenset({"scope"}),
            cmd29_routes=frozenset(),
        )

    def create_observation_poller(
        self, *, callback: Callable[[Sequence[Observation]], None], **_kwargs: object
    ) -> object:
        return _RefusedTxPoller(
            callback,
            self,
            self._acquisition_scheduler._profile,
            clock=self._clock,
            extra=lambda: (_scope_edge_observation(self._clock),),
        )


@virtual_clock
@pytest.mark.usefixtures("isolated_eibi_cache")
async def test_refused_tx_with_scope_edge_binds_with_named_warning(
    clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A valid scope edge plus refused TX reads still binds with a warning.

    Round-2 P1: ``_unwarned_unavailable_paths`` passed the scheduler's
    acquisition profile to ``scope_fixed_edge_slot`` (which needs a
    ``RadioProfile``), so any valid observed edge aborted startup with
    ``AttributeError`` before the listener bound. The gate must bind and
    the release WARNING must still name the refused paths with their real
    commands and causes.
    """
    clock.scope_to(monkeypatch, web_startup)
    radio = _ScopedRefusedTransmitRadio(clock=clock)
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with (
        patch("rigplane.web.web_startup.asyncio.start_server", new=_bind),
        caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"),
    ):
        try:
            await server.start()
            assert binds == ["bind"]
            assert scheduler.startup_defect is None
            assert scheduler.consecutive_refusals(PTT) >= 1
            assert scheduler.consecutive_refusals(SPLIT) >= 1
            observed_paths = {
                field.path for field in server.command_state_store.snapshot().fields
            }
            assert FREQ in observed_paths
            assert FieldPath.scope_control("display", "edge") in observed_paths
            edge = server.command_state_store.snapshot().field(
                FieldPath.scope_control("display", "edge")
            )
            assert edge.value == 2
        finally:
            await server.stop()

    gate_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.web.web_startup"
        and "serving without them" in record.getMessage()
    ]
    assert gate_warnings, "the refused reads must never release the gate silently"
    named = " ".join(gate_warnings)
    assert "global.tx_state.ptt" in named
    assert "global.tx_state.split" in named
    assert "command 'TX;'" in named
    assert "command 'ST;'" in named
    assert "refused" in named


class _RefuseThenAnswerT:
    """A ``t`` transport that refuses once, then answers ``0`` (unkeyed)."""

    def __init__(self) -> None:
        self.queries = 0

    async def query_decoded(
        self,
        command: str,
        *,
        response_lines: int,
        decode: Callable[[list[str]], bool],
        is_current: Callable[[], bool] | None = None,
        urgent: bool = False,
    ) -> bool:
        self.queries += 1
        if self.queries == 1:
            raise RigctldCommandError("t", 1)
        return decode(["0"])


@pytest.mark.asyncio
async def test_rigctld_answered_t_ends_the_refusal_mark() -> None:
    """An answered ``t`` clears the scheduler refusal the RPRT filed.

    Round-2 P2: the success path reset only the radio latch, so the
    scheduler kept the mark — the public state kept reporting PTT
    unavailable after the radio started answering. Refusal, then a valid
    answer: consecutive refusals reset, no refused paths remain, and the
    public ``declared_read_refusals`` projection is empty again.
    """
    transport = _RefuseThenAnswerT()
    radio = RigctldClientRadio(
        host="127.0.0.1", port=1, transport=transport  # type: ignore[arg-type]
    )
    radio._acquisition_scheduler = AcquisitionScheduler(profile=_note_profile())
    scheduler = radio._acquisition_scheduler

    with pytest.raises(RigctldCommandError):
        await radio._read_ptt()
    assert radio._ptt_read_refused is True
    assert scheduler.consecutive_refusals(PTT) == 1
    assert PTT in scheduler.refused_paths()
    assert PTT in scheduler.declared_read_refusals()
    assert transport.queries == 1

    assert await radio._read_ptt() is False
    assert radio._ptt_read_refused is False
    assert scheduler.consecutive_refusals(PTT) == 0
    assert scheduler.refused_paths() == frozenset()
    assert scheduler.declared_read_refusals() == {}
    assert scheduler.startup_defect is None
    assert transport.queries == 2


@pytest.mark.asyncio
async def test_gate_empty_outstanding_with_refused_record_still_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An already-empty gate still warns on the refused record (round-2 P2).

    Freq/mode are already observed, so the gate's outstanding set is empty
    on entry — the terminal-refusal case the brief requires the gate to
    handle. The early return must still emit the gate WARNING naming the
    refused path with its real command and cause, not release silently.
    """
    scheduler = AcquisitionScheduler(profile=_note_profile())
    store = StateStore()
    store.apply(
        Observation(
            path=FREQ,
            value=14_074_000,
            timestamp_monotonic=10.0,
            source=SourceMetadata(source="poll_response", provider="test_provider"),
        )
    )
    radio = _note_radio(scheduler)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="ptt", path=PTT, command="t", outcome="refused"),
        now=10.0,
    )
    server = SimpleNamespace(
        _radio=radio,
        command_state_store=store,
        _config=_gated_config(),
        _served_without_port=False,
    )

    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        await web_startup._await_initial_state_acquisition(server, sweep=False)  # type: ignore[arg-type]

    gate_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.web.web_startup"
        and "serving without them" in record.getMessage()
    ]
    assert gate_warnings, "an empty gate with a refused record must still warn"
    assert "global.tx_state.ptt" in gate_warnings[0]
    assert "command 't'" in gate_warnings[0]
    assert "refused" in gate_warnings[0]


def test_direct_critical_outcome_keeps_startup_defect_routing() -> None:
    """A critical direct read still ends startup through the defect record."""

    profile = RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(FieldCapability(path=FREQ, polling=True),),
        field_policies={
            FREQ: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
        },
    )
    scheduler = AcquisitionScheduler(profile=profile)
    radio = _note_radio(scheduler)
    record_direct_read_outcome(
        radio,
        StartupReadNote(label="freq", path=FREQ, command="FA;", outcome="refused"),
        now=10.0,
    )
    assert scheduler.startup_defect is not None
    assert "FA;" in str(scheduler.startup_defect)
