"""Direct unit tests for ``RadioPoller.teardown_unkey_permitted`` (MOR-1885).

MOR-1878 added the gate as a safety seat deciding whether an automated
session teardown may enqueue its own ``ptt_off``. Until now it was covered
only indirectly, through the end-to-end rows in
``tests/test_web_mod_input_restore.py::TestKeyerAttributedTeardown`` (which
drive the gate through ``ControlHandler``/``CommandQueue`` plumbing). This
file pins the gate's own decision table directly, plus the one caller-side
default (MOR-1878's ``try/except -> permitted=True``) that consumes it.

Test-only ticket: no production code changes.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue, PttOff, PttOn, RadioPoller


def _poller(
    *,
    ptt_on_error: Exception | None = None,
    ptt_off_error: Exception | None = None,
) -> tuple[RadioPoller, MagicMock]:
    """Build a RadioPoller wired to an unmanaged radio (the MOR-1878 path).

    ``ptt_on_error``/``ptt_off_error``, when given, are raised on the
    matching ``set_ptt(True)``/``set_ptt(False)`` write only, so a test can
    key the radio successfully and then observe a single failing write (or
    vice versa) in isolation.
    """
    radio = MagicMock()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.capabilities = set(radio.profile.capabilities) - {"audio"}
    # Unmanaged: None reads unmanaged on every interpreter, unlike a bare
    # Mock (runtime-checkable protocols differ 3.11 vs 3.12+, gh-102433).
    radio.managed_tx = None

    async def set_ptt(on: bool) -> None:
        error = ptt_on_error if on else ptt_off_error
        if error is not None:
            raise error

    radio.set_ptt = AsyncMock(side_effect=set_ptt)
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    return poller, radio


def _observe_rx(poller: RadioPoller) -> None:
    """Apply a fresh PTT=False observation, matching a real RX readback."""
    store = poller._state_store
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=5.0,
            provider_generation=store.provider_generation,
        )
    )


def test_no_recorded_keyer_is_permitted() -> None:
    poller, _radio = _poller()

    assert poller.teardown_unkey_permitted("websocket", "ws-b") is True


async def test_recorded_keyer_same_session_is_permitted() -> None:
    poller, _radio = _poller()
    await poller._execute(PttOn(), source="websocket", session_id="ws-a")

    assert poller.teardown_unkey_permitted("websocket", "ws-a") is True


async def test_recorded_keyer_different_session_rf_not_rx_is_not_permitted() -> None:
    poller, _radio = _poller()
    await poller._execute(PttOn(), source="websocket", session_id="ws-a")

    # No RF observation has landed: _current_rf_state resolves UNKNOWN, not
    # RX, so the foreign session's teardown must be withheld.
    assert poller.teardown_unkey_permitted("websocket", "ws-b") is False


async def test_observed_rx_permits_and_voids_the_record_latch() -> None:
    poller, _radio = _poller()
    await poller._execute(PttOn(), source="websocket", session_id="ws-a")
    _observe_rx(poller)

    assert poller.teardown_unkey_permitted("websocket", "ws-b") is True
    assert poller._last_keyer is None

    # Latch: once voided the record must never be consulted again, so a
    # later staleness (RF reverting to UNKNOWN) cannot resurrect the
    # withhold. Proven by never touching the resolver on the second call —
    # if the void were missing, the still-live keyer would force a second
    # `_current_rf_state()` lookup here.
    poller._current_rf_state = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("resolver must not be consulted after void")
    )
    assert poller.teardown_unkey_permitted("websocket", "ws-b") is True


async def test_failed_unmanaged_ptt_off_write_still_clears_the_record() -> None:
    poller, _radio = _poller(ptt_off_error=RuntimeError("boom"))
    await poller._execute(PttOn(), source="websocket", session_id="ws-a")
    assert poller._last_keyer == ("websocket", "ws-a")

    raised: Exception | None = None
    try:
        await poller._execute(PttOff(), source="websocket", session_id="ws-a")
    except RuntimeError as exc:
        raised = exc
    assert raised is not None, "the raising unkey write must propagate"

    # finally invariant: cleared on the ATTEMPT, not on success, so the
    # next teardown (even from a different session) is free to send OFF.
    assert poller._last_keyer is None
    assert poller.teardown_unkey_permitted("websocket", "ws-b") is True


async def test_failed_unmanaged_ptt_on_write_leaves_no_phantom_record() -> None:
    """Record placement after the write: a key that never reached the rig
    must not arm the withhold against every other session."""
    poller, _radio = _poller(ptt_on_error=RuntimeError("key never reached the rig"))

    raised: Exception | None = None
    try:
        await poller._execute(PttOn(), source="websocket", session_id="ws-a")
    except RuntimeError as exc:
        raised = exc
    assert raised is not None, "the raising key-on write must propagate"

    assert poller._last_keyer is None
    assert poller.teardown_unkey_permitted("websocket", "ws-b") is True


async def test_record_is_per_poller_instance_no_leakage() -> None:
    poller_a, _radio_a = _poller()
    poller_b, _radio_b = _poller()

    await poller_a._execute(PttOn(), source="websocket", session_id="ws-a")

    # Probe poller_b with a THIRD session id, distinct from "ws-a" (the one
    # poller_a just keyed with). Reusing "ws-a" here would be vacuous: it
    # returns True under BOTH per-instance semantics (poller_b recorded
    # nothing, so the no-recorded-keyer branch fires) AND under a leaked
    # shared record (poller_b would see keyer == ("websocket", "ws-a"),
    # matching the very session_id being asked about, so the
    # `keyer == (source, session_id)` branch also returns True). Only a
    # session id that differs from the recorded keyer's discriminates: it
    # stays True on isolation (still no recorded keyer) but flips to False
    # if state leaked (a live foreign-session record would withhold it).
    assert poller_b.teardown_unkey_permitted("websocket", "ws-z") is True
    # poller_a, meanwhile, still withholds against a foreign session.
    assert poller_a.teardown_unkey_permitted("websocket", "ws-b") is False


def test_control_handler_defaults_to_permitted_when_gate_raises() -> None:
    """MOR-1878's try/except -> permitted=True default at the caller seat
    (``ControlHandler._release_ptt_on_teardown``), the one consumer of this
    gate. Every failure of the consultation — resolver included — must fall
    through to the unkey: dropping a transmission is recoverable, a stuck
    transmitter is not."""
    poller, radio = _poller()
    poller.teardown_unkey_permitted = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("resolver exploded")
    )
    command_queue = CommandQueue()
    server = SimpleNamespace(command_queue=command_queue, _radio_poller=poller)
    handler = ControlHandler(
        ws=MagicMock(),
        radio=radio,
        server_version="test",
        radio_model="IC-7610",
        server=server,
        session_id="ws-a",
    )

    handler._release_ptt_on_teardown()

    assert command_queue.drain() == [PttOff()]
