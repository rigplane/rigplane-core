"""Managed-TX release retry cadence across a silent serial link-down (MOR-3300).

The MOR-3211 silent-link fixture with managed TX keyed: the real
``Ic7300SerialRadio`` behind ``CivSerialFake`` (production ``SerialCivLink``
opener seam, a managed-TX composition, fast watchdog constants, a
fire-and-forget poller — the shape of
``test_serial_silent_link.py``) takes managed TX ON through the authority,
the fake then stays silent long enough for the silence path to declare
link-down, and the test observes the authority for a fixed 10 s window of
continued silence.

MOR-3300 observed, in the field run behind MOR-3211, one
``managed TX release attempt`` warning per second with no observed bound
while the radio link was down. The desired bound asserted here: no release
attempt is logged while the provider is unavailable
(``provider_generation is None``), and the window holds at most one attempt
per provider generation that becomes available inside it, plus the single
attempt the link-down park itself owes on the generation that was live when
the window opened.

The window also records, for the characterization handback: every attempt
log line with a monotonic stamp, ``snapshot_nowait().provider_generation``
sampled every 0.1 s, and the generation plus whether
``radio._civ_transport`` is None at the moment link-down reaches audio
teardown — the candidate explanations for an unbounded cadence are a park
that found no transport to retire (MOR-3300 hypothesis A) and a provider
re-armed on a silent-but-ready session (hypothesis B).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands.commander import Priority
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind, ManagedTxOutcome

from rigplane_testkit.serial import CivSerialFake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened

# Cadence knobs and the silence-limit budget, as in test_serial_silent_link.py
# (the production formula _serial_link_down_silence_limit_s: two answer
# windows plus one watchdog tick).
_ANSWER_WINDOW_S = 0.2
_WATCHDOG_TICK_S = 0.02
_SILENCE_LIMIT_S = 2 * _ANSWER_WINDOW_S + _WATCHDOG_TICK_S
_SCHEDULING_SLACK_S = 0.2
_LINK_DOWN_BUDGET_S = _SILENCE_LIMIT_S + 2 * _WATCHDOG_TICK_S + _SCHEDULING_SLACK_S

# The observation window: fixed 10 s of continued silence, provider
# generation sampled every 0.1 s on the monotonic clock.
_WINDOW_S = 10.0
_SAMPLE_INTERVAL_S = 0.1

# Shutdown budgets, scaled down from cli: _MANAGED_TX_SHUTDOWN_DRAIN_S and
# _MANAGED_TX_SHUTDOWN_SETTLE_S in _shutdown_managed_tx_composition, as in
# test_serial_silent_link.py; the disconnect bound is the task's 5 s.
_SHUTDOWN_DRAIN_S = 0.5
_SHUTDOWN_SETTLE_S = 2.0
_DISCONNECT_BUDGET_S = 5.0

_AUTHORITY_LOGGER = "rigplane.runtime.managed_tx_authority"
_ATTEMPT_PREFIX = "managed TX release attempt"
_ATTEMPT_ID = re.compile(r"^managed TX release attempt (\d+):")

observation = logging.getLogger(__name__)
observation.setLevel(logging.INFO)


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _poll_loop(radio: Ic7300SerialRadio, stop: asyncio.Event) -> None:
    """A live poller: fire-and-forget frequency reads, errors ignored.

    Each read registers a keyed response sink and enqueues a real CI-V
    ``0x03`` for the wire, the call ``web/radio_poller.py: RadioPoller._civ``
    makes for background polls; the outstanding sinks are what the silence
    evidence path (``_serial_civ_timeout_evidence_crossed_threshold``) needs
    to declare link-down.
    """
    while not stop.is_set():
        try:
            await radio.send_civ(
                0x03,
                wait_response=False,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
        except Exception:
            pass
        await asyncio.sleep(0.02)


class _AttemptRecorder(logging.Handler):
    """Stamp authority release-attempt records with the monotonic clock."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[tuple[float, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(_ATTEMPT_PREFIX):
            self.records.append((time.monotonic(), message))


def _attempt_id(message: str) -> int | None:
    match = _ATTEMPT_ID.match(message)
    return int(match.group(1)) if match is not None else None


def _generation_timeline(
    samples: list[tuple[float, int | None]], window_start: float
) -> list[tuple[float, int | None]]:
    timeline: list[tuple[float, int | None]] = []
    previous: int | None = object()  # type: ignore[assignment]
    for stamp, generation in samples:
        if generation != previous:
            timeline.append((round(stamp - window_start, 2), generation))
            previous = generation
    return timeline


async def test_release_retry_cadence_bounded_across_silent_link_down(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = CivSerialFake()
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._SERIAL_WATCHDOG_INTERVAL_S = _WATCHDOG_TICK_S
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)

    await radio.connect()
    assert radio.connected
    assert (await composition.authority.snapshot()).provider_generation is not None

    # Managed TX ON through the authority; the fake ACKs the PTT write
    # (rigplane_testkit/serial/civ_serial.py answers 0x1C), so the intent
    # must be TRANSMIT with the fake actually keyed.
    assert await composition.authority.transmit_on() is ManagedTxOutcome.ACCEPTED
    assert fake.ptt is True, "the fake did not ACK the managed PTT write"
    assert (
        composition.authority.snapshot_nowait().state.intent.kind
        is ManagedTxIntentKind.TRANSMIT
    ), "the authority did not hold the TRANSMIT intent"

    # Record the authority's provider generation and whether the radio still
    # holds a CI-V transport at the moment link-down moves on to audio
    # teardown, which _declare_serial_link_down does only after its park
    # has returned.
    teardown_samples: list[tuple[int | None, bool]] = []
    stop_audio = radio._stop_serial_audio_driver

    async def _recording_stop_audio() -> None:
        teardown_samples.append(
            (
                composition.authority.snapshot_nowait().provider_generation,
                radio._civ_transport is None,
            )
        )
        await stop_audio()

    radio._stop_serial_audio_driver = _recording_stop_audio  # type: ignore[method-assign]

    recorder = _AttemptRecorder()
    authority_logger = logging.getLogger(_AUTHORITY_LOGGER)
    authority_logger.addHandler(recorder)
    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_loop(radio, stop))
    try:
        assert await _wait_until(
            lambda: any(frame.command == 0x1C for frame in fake.commands),
            timeout_s=2.0,
        ), "the managed TX ON never reached the wire"

        with caplog.at_level(
            logging.ERROR, logger="rigplane.backends._icom_serial_base"
        ):
            fake.silence(60.0)
            assert await _wait_until(
                lambda: radio.conn_state is not RadioConnectionState.CONNECTED,
                timeout_s=_LINK_DOWN_BUDGET_S + 0.5,
            ), "silent link with polls outstanding never declared link-down"

        # The observation window: 10 s of continued silence, the provider
        # generation sampled every 0.1 s on the monotonic clock.
        samples: list[tuple[float, int | None]] = [
            (
                time.monotonic(),
                composition.authority.snapshot_nowait().provider_generation,
            )
        ]
        window_start = samples[0][0]
        window_end = window_start + _WINDOW_S
        with caplog.at_level(logging.INFO, logger=_AUTHORITY_LOGGER):
            while True:
                now = time.monotonic()
                if now >= window_end:
                    break
                await asyncio.sleep(min(_SAMPLE_INTERVAL_S, window_end - now))
                samples.append(
                    (
                        time.monotonic(),
                        composition.authority.snapshot_nowait().provider_generation,
                    )
                )

        window_attempts = [
            (stamp, message)
            for stamp, message in recorder.records
            if window_start <= stamp <= window_end
        ]
        attempt_ids = sorted(
            {
                attempt_id
                for attempt_id in (
                    _attempt_id(message) for _, message in window_attempts
                )
                if attempt_id is not None
            }
        )
        timeline = _generation_timeline(samples, window_start)
        # A provider generation "became available in the window" when its
        # first observed sample comes after the window's opening sample, so
        # the generation that was already live at window start does not
        # widen the bound.
        first_seen_index: dict[int, int] = {}
        for index, (_, generation) in enumerate(samples):
            if generation is not None and generation not in first_seen_index:
                first_seen_index[generation] = index
        generations_available_in_window = {
            generation for generation, index in first_seen_index.items() if index >= 1
        }
        link_down_notes = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR and "link-down" in record.getMessage()
        ]

        # Desired bound, part 1 (MOR-3300): no release attempt while the
        # provider is unavailable. Each in-window attempt is judged against
        # the latest generation sample at or before its stamp.
        for stamp, message in window_attempts:
            prior = [gen for at, gen in samples if at <= stamp]
            assert prior and prior[-1] is not None, (
                f"release attempt logged while the provider was sampled "
                f"unavailable: {message!r} at window+"
                f"{stamp - window_start:.2f}s"
            )

        # Desired bound, part 2 (MOR-3300): at most the park's one attempt on
        # the generation live when the window opened, plus one per provider
        # generation that became available inside the window.
        assert attempt_ids, (
            "the link-down park logged no release attempt in the window; the "
            f"scenario produced no pending release (timeline {timeline})"
        )
        allowed = 1 + len(generations_available_in_window)
        assert len(attempt_ids) <= allowed, (
            f"{len(attempt_ids)} release attempts in the {_WINDOW_S:.0f}s "
            f"window (ids {attempt_ids}), over the desired bound of "
            f"{allowed} (1 park attempt + "
            f"{len(generations_available_in_window)} generation(s) that "
            f"became available); generation timeline {timeline}"
        )

        assert len(teardown_samples) == 1, (
            f"link-down did not reach audio teardown exactly once: {teardown_samples}"
        )
        evidence_lines = [message for _, message in window_attempts]
        shown = (
            evidence_lines
            if len(evidence_lines) <= 6
            else evidence_lines[:3] + evidence_lines[-3:]
        )
        observation.info(
            "MOR-3300 window: attempts=%d ids=%s generation_timeline=%s "
            "audio_teardown_sample=%s link_down_notes=%d attempt_lines=%s",
            len(attempt_ids),
            attempt_ids,
            timeline,
            teardown_samples[0],
            len(link_down_notes),
            shown,
        )
    finally:
        authority_logger.removeHandler(recorder)
        stop.set()
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
        # Teardown order as production runs it (cli: _cmd_web drives the
        # command inside ``async with session``; ``_ManagedTxRadioSession.
        # __aexit__`` awaits ``_shutdown_managed_tx_composition`` before the
        # radio's own ``__aexit__`` disconnects it): stop the poller, shut
        # the composition down, then disconnect the radio.
        termination = asyncio.Event()
        shutdown = asyncio.create_task(composition.shutdown(termination))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(shutdown), timeout=_SHUTDOWN_DRAIN_S
            )
        except TimeoutError:
            termination.set()
            result = await asyncio.wait_for(
                asyncio.shield(shutdown), timeout=_SHUTDOWN_SETTLE_S
            )
        assert result is ShutdownResult.TERMINATED
        await asyncio.wait_for(radio.disconnect(), timeout=_DISCONNECT_BUDGET_S)
