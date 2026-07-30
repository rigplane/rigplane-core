"""MOR-1013: PTT-OFF audio teardown must survive a failed unkey.

Bug: ``RadioPoller._execute`` ran ``await radio.set_ptt(False)`` OUTSIDE the
``try`` that stops the TX audio leg and re-arms RX. ``set_ptt`` is a
fire-and-forget CI-V write that can raise (``ConnectionError``,
``TimeoutError``, transport errors), and when it did the entire audio
teardown was skipped: ``stop_tx()`` never ran and RX was never re-armed. That
left the LAN TX audio leg pumping modulation into a rig whose de-key had just
failed — the worst combination available.

Acceptance (MOR-1013): "Provider-native voice TX stops even when OFF fails."

The unkey failure must still reach the caller unchanged: ``_run`` classifies
it (timeout vs connection vs generic) for ``_mark_queued_command_failed`` and
for the queued command's future, so the original exception object — not a
wrapper — has to propagate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.core.capabilities import CAP_AUDIO
from rigplane.profiles import resolve_radio_profile
from rigplane.web.radio_poller import CommandQueue, PttOff, RadioPoller

CallRecord = list[tuple[str, tuple[Any, ...]]]


class _UnkeyFailed(ConnectionError):
    """Transport-style failure raised by ``set_ptt(False)``."""


class _TeardownFailed(RuntimeError):
    """Failure raised by the TX-audio teardown itself."""


def _make_radio(
    calls: CallRecord,
    *,
    audio: bool = True,
    set_ptt_error: BaseException | None = None,
    stop_tx_error: BaseException | None = None,
) -> SimpleNamespace:
    """Hand-written duck-typed radio recording ordered calls with arguments.

    Deliberately not a ``MagicMock``: on Python 3.11 a bare mock satisfies
    ``runtime_checkable`` protocols and would hide capability-gate bugs.
    """

    profile = resolve_radio_profile(model="IC-7610")

    async def set_ptt(state: bool) -> None:
        calls.append(("set_ptt", (state,)))
        if set_ptt_error is not None:
            raise set_ptt_error

    async def stop_tx() -> None:
        calls.append(("stop_tx", ()))
        if stop_tx_error is not None:
            raise stop_tx_error

    async def restart_rx() -> None:
        calls.append(("restart_rx", ()))

    return SimpleNamespace(
        profile=profile,
        model=profile.model,
        capabilities={CAP_AUDIO} if audio else set(),
        set_ptt=set_ptt,
        stop_tx=stop_tx,
        audio_bus=SimpleNamespace(restart_rx=restart_rx),
        audio_duplex_mode="half",
    )


def _make_poller(radio: SimpleNamespace) -> RadioPoller:
    return RadioPoller(radio, CommandQueue())


async def test_ptt_off_stops_tx_audio_when_unkey_raises() -> None:
    """Failed unkey must still stop TX audio and re-arm RX (MOR-1013)."""
    calls: CallRecord = []
    unkey_error = _UnkeyFailed("civ write failed")
    poller = _make_poller(_make_radio(calls, set_ptt_error=unkey_error))

    with pytest.raises(_UnkeyFailed) as excinfo:
        await poller._execute(PttOff())

    # The caller's ``_mark_queued_command_failed`` bookkeeping keys off the
    # exception type/identity — it must arrive unwrapped.
    assert excinfo.value is unkey_error
    assert calls == [
        ("set_ptt", (False,)),
        ("stop_tx", ()),
        ("restart_rx", ()),
    ]


async def test_ptt_off_ordering_unchanged_when_unkey_succeeds() -> None:
    """Happy path keeps the unkey-first ordering the PttOn arm depends on."""
    calls: CallRecord = []
    poller = _make_poller(_make_radio(calls))

    await poller._execute(PttOff())

    assert calls == [
        ("set_ptt", (False,)),
        ("stop_tx", ()),
        ("restart_rx", ()),
    ]


async def test_ptt_off_without_audio_capability_touches_no_audio() -> None:
    """The CAP_AUDIO gate still suppresses the teardown entirely."""
    calls: CallRecord = []
    poller = _make_poller(_make_radio(calls, audio=False))

    await poller._execute(PttOff())

    assert calls == [("set_ptt", (False,))]


async def test_ptt_off_without_audio_capability_still_propagates_unkey() -> None:
    """Gate-off path must not invent audio calls nor swallow the failure."""
    calls: CallRecord = []
    unkey_error = _UnkeyFailed("civ write failed")
    poller = _make_poller(_make_radio(calls, audio=False, set_ptt_error=unkey_error))

    with pytest.raises(_UnkeyFailed) as excinfo:
        await poller._execute(PttOff())

    assert excinfo.value is unkey_error
    assert calls == [("set_ptt", (False,))]


async def test_unkey_failure_wins_over_teardown_failure() -> None:
    """A failing teardown must never mask the failed de-key."""
    calls: CallRecord = []
    unkey_error = _UnkeyFailed("civ write failed")
    poller = _make_poller(
        _make_radio(
            calls,
            set_ptt_error=unkey_error,
            stop_tx_error=_TeardownFailed("stop_tx exploded"),
        )
    )

    with pytest.raises(_UnkeyFailed) as excinfo:
        await poller._execute(PttOff())

    assert excinfo.value is unkey_error
    # ``stop_tx`` raising short-circuits the re-arm — pre-existing behaviour.
    assert calls == [("set_ptt", (False,)), ("stop_tx", ())]


async def test_teardown_failure_alone_is_still_swallowed() -> None:
    """Audio-teardown errors stay non-fatal when the unkey succeeded."""
    calls: CallRecord = []
    poller = _make_poller(
        _make_radio(calls, stop_tx_error=_TeardownFailed("stop_tx exploded"))
    )

    await poller._execute(PttOff())

    assert calls == [("set_ptt", (False,)), ("stop_tx", ())]
