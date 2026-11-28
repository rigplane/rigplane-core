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

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.transport import CatCommandRejected
from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.startup_read_notes import StartupReadNote, record_direct_read_outcome
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import FieldPath, Observation
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.radio_state import RadioState
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
    ) -> None:
        self._callback = callback
        self._radio = radio
        self._profile = profile

    async def start(self) -> None:
        while True:
            adapter = YaesuObservationAdapter(
                self._radio,  # type: ignore[arg-type]
                profile=self._profile,
            )
            batch = [
                *await adapter.poll_medium(),
                *await adapter.poll_tx_controls(),
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

    def __init__(self) -> None:
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
        return _RefusedTxPoller(callback, self, self._acquisition_scheduler._profile)

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


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated_eibi_cache")
async def test_yaesu_refused_ptt_and_split_bind_with_named_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refused ``TX;``/``ST;`` bind the server with a naming WARNING.

    Freq/mode answer, PTT and split refuse every cycle: both leave the
    outstanding set as unavailable, the gate releases (no silent release),
    and the release WARNING names each path with its real wire command
    and its ``refused Nx`` cause. No startup defect is recorded.
    """
    radio = _RefusedTransmitRadio()
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
            await asyncio.wait_for(server.start(), timeout=30.0)
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
