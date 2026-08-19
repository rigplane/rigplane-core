"""MOR-1904: the key-down bound for a rigctld-issued key on an unmanaged radio.

A client that keys a serial/USB Icom and then dies leaves the transmitter up
with no bound anywhere in the product: ``_IcomSerialRadioBase`` arms no
supervisor, and ``release_session_tx`` deliberately writes nothing on an
unmanaged radio at socket close. This pins the bound that closes that hole and,
just as important, the conditions under which it must stay silent — a blind
timeout would take somebody else's over down.

**Vacuity trap, read before adding a test here.** ``StateStore.mark_stale_due``
is the store's *sole* freshness-decay entry point: "If nothing calls this,
fields never go STALE". A test that only advances a ``FreshnessClock`` leaves
the field FRESH forever and can pass without ever reaching the branch it claims
to cover. Any test asserting decay must call ``store.mark_stale_due(now=...)``
explicitly; any test asserting a *fresh* RX must seed an ``Observation`` whose
``max_age`` is wider than the elapsed wall time.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld import handler as handler_mod
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime import tx_interlock
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from test_web_recovery_durable_off import _Provider

# Test-scale stand-in for BACKEND_MAX_KEY_DOWN_SECONDS, wide enough that a
# loaded loop cannot straddle the two deadlines the re-key test discriminates.
_BOUND, _TICK = 0.3, 0.03
_S1, _S2 = "rigctld-client-1", "rigctld-client-2"
_CLOCK = 10.0
_PTT = FieldPath.global_("tx_state", "ptt")


class _Radio:
    """Unmanaged radio logging every legacy PTT write. Not a ``MagicMock``: one
    answers the ``managed_tx`` probe, so the unmanaged path could never be
    shown to stay unmanaged. The member is absent unless a test assigns it."""

    def __init__(self, *, fail_on: bool | None = None) -> None:
        self.profile = resolve_radio_profile(model="IC-7300")
        self.capabilities: set[str] = set()
        self.writes: list[bool] = []
        self._fail_on = fail_on

    async def set_ptt(self, on: bool) -> None:
        self.writes.append(on)
        if on is self._fail_on:
            raise ConnectionError("PTT write did not reach the rig")


@dataclass(slots=True)
class _Seat:
    radio: _Radio
    store: StateStore
    handler: RigctldHandler

    async def route(self, on: bool, session: str = _S1) -> None:
        await self.handler._route_ptt(on, session_id=session)  # noqa: SLF001

    @property
    def armed(self) -> bool:
        return self.handler._key_down_backstop_task is not None  # noqa: SLF001

    @property
    def rf(self) -> tx_interlock.RfState:
        return self.handler._resolve_rigctld_rf_state()  # noqa: SLF001


_Make = Callable[..., _Seat]


def _observe(store: StateStore, ptt: bool) -> None:
    """Seed a FRESH ``global.tx_state.ptt``; the manual clock cannot age it."""
    store.apply(
        Observation(
            path=_PTT,
            value=ptt,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=_CLOCK,
            max_age=1.0,
        )
    )


async def _past_the_bound() -> None:
    await asyncio.sleep(_BOUND * 1.6)


def _logged(
    caplog: pytest.LogCaptureFixture, level: int, phrase: str, *, tb: bool = False
) -> bool:
    return any(
        record.levelno == level
        and phrase in record.getMessage()
        and (record.exc_info is not None) is tb
        for record in caplog.records
    )


@pytest.fixture
def seat(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> _Make:
    """Build unmanaged seats on a test-scale bound, and retire their tickers."""
    monkeypatch.setattr(handler_mod, "_KEY_DOWN_BACKSTOP_SECONDS", _BOUND)
    monkeypatch.setattr(handler_mod, "_KEY_DOWN_BACKSTOP_TICK_SECONDS", _TICK)
    caplog.set_level(logging.DEBUG, logger=handler_mod.__name__)
    built: list[_Seat] = []

    def _make(*, ptt: bool | None = None, fail_on: bool | None = None) -> _Seat:
        radio = _Radio(fail_on=fail_on)
        store = StateStore(freshness_clock=FreshnessClock(start=_CLOCK))
        if ptt is not None:
            _observe(store, ptt)
        built.append(
            _Seat(
                radio, store, RigctldHandler(radio, RigctldConfig(), state_store=store)
            )  # type: ignore[arg-type]
        )
        return built[-1]

    yield _make

    for seated in built:
        seated.handler.stop_key_down_backstop()


@pytest.fixture
async def managed_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[RigctldHandler, _Radio]]:
    """A real supervisor over a real effect service — no scripted double."""
    monkeypatch.setattr(handler_mod, "_KEY_DOWN_BACKSTOP_SECONDS", _BOUND)
    monkeypatch.setattr(handler_mod, "_KEY_DOWN_BACKSTOP_TICK_SECONDS", _TICK)
    runtime = ManagedRadioRuntime(
        "mor1904-test",
        service_factory=managed_tx_effect_service,
        provider_lifecycle=_Provider([]),
    )
    await runtime.replace_provider(ready=True)
    await runtime.request_fresh_ptt()  # seeds the OFF ``request_on`` demands
    radio = _Radio()
    radio.managed_tx = runtime  # type: ignore[attr-defined]
    yield RigctldHandler(radio, RigctldConfig()), radio  # type: ignore[arg-type]

    async def _noop() -> None:
        return None

    await runtime.shutdown(release_provider=_noop)


async def test_an_unmanaged_key_that_is_never_unkeyed_is_forced_off(
    seat: _Make, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole ticket: RF still says TX and no unkey ever came."""
    rig = seat(ptt=True)

    await rig.route(True)
    await _past_the_bound()

    assert rig.radio.writes == [True, False]
    assert _logged(caplog, logging.ERROR, "max key-down")


async def test_an_unknown_rf_state_still_fires(seat: _Make) -> None:
    """Profiles with no ``[state_acquisition]`` block have no RF truth at all.
    Voiding on absent evidence would leave exactly those rigs unbounded."""
    rig = seat()

    assert rig.rf is tx_interlock.RfState.UNKNOWN
    await rig.route(True)
    await _past_the_bound()

    assert rig.radio.writes == [True, False]


async def test_the_bound_survives_the_socket_that_keyed_it(seat: _Make) -> None:
    """The central case. ``release_session_tx`` still writes nothing on an
    unmanaged radio — a standing decision — so the bound is what fires."""
    rig = seat(ptt=True)

    await rig.route(True)
    await rig.handler.release_session_tx(_S1)
    assert rig.radio.writes == [True]

    await _past_the_bound()

    assert rig.radio.writes == [True, False]


async def test_a_failed_backstop_unkey_is_logged_and_not_retried(
    seat: _Make, caplog: pytest.LogCaptureFixture
) -> None:
    """One attempt: retrying at a rig that is refusing writes is a second
    failure mode, not a recovery."""
    rig = seat(fail_on=False)

    await rig.route(True)
    await asyncio.sleep(_BOUND * 3)

    assert rig.radio.writes == [True, False]
    assert _logged(caplog, logging.ERROR, "forced unkey failed", tb=True)


async def test_an_observed_rx_voids_the_bound(seat: _Make) -> None:
    """Observed RX is the discriminator the disconnect path lacks: the rig came
    off the air by some other route, so this is no longer our over."""
    rig = seat()

    await rig.route(True)
    _observe(rig.store, False)
    await _past_the_bound()

    assert rig.radio.writes == [True]
    assert not rig.armed


async def test_an_rx_seen_once_stays_voided_after_the_field_goes_unknown(
    seat: _Make,
) -> None:
    """The void latches. RF truth decays, so an implementation that only looks
    at the deadline would find UNKNOWN there and fire on a rig already down."""
    rig = seat()

    await rig.route(True)
    _observe(rig.store, False)
    await asyncio.sleep(_TICK * 4)
    assert not rig.armed

    rig.store.mark_stale_due(now=_CLOCK + 10.0)  # the SOLE decay entry point
    assert rig.rf is tx_interlock.RfState.UNKNOWN
    await _past_the_bound()

    assert rig.radio.writes == [True]


async def test_a_client_unkey_disarms_the_bound(seat: _Make) -> None:
    rig = seat(ptt=True)

    await rig.route(True)
    await rig.route(False)
    assert not rig.armed
    await _past_the_bound()

    assert rig.radio.writes == [True, False]


async def test_a_lost_client_unkey_keeps_the_bound_armed(seat: _Make) -> None:
    """Why the disarm sits BELOW the write and never in a ``finally``: an unkey
    that RAISED never reached the rig, so dropping the bound on it would strand
    a keyed transmitter. Mirrors the web precedent MOR-1220 already pins."""
    rig = seat(ptt=True, fail_on=False)

    await rig.route(True)
    with pytest.raises(ConnectionError):
        await rig.route(False)
    assert rig.armed

    await _past_the_bound()

    assert rig.radio.writes == [True, False, False]


async def test_a_second_sessions_unkey_disarms_the_first_sessions_bound(
    seat: _Make,
) -> None:
    """Either polarity, any session: the rig has one PTT, not one per socket."""
    rig = seat(ptt=True)

    await rig.route(True)
    await rig.route(False, _S2)
    await _past_the_bound()

    assert rig.radio.writes == [True, False]


async def test_a_second_sessions_key_replaces_the_first_sessions_deadline(
    seat: _Make, caplog: pytest.LogCaptureFixture
) -> None:
    """One slot, restart-on-key: a later key supersedes the earlier deadline,
    and the fire must name the session that is actually transmitting."""
    rig = seat(ptt=True)

    await rig.route(True)
    await asyncio.sleep(_BOUND * 0.5)
    await rig.route(True, _S2)

    await asyncio.sleep(_BOUND * 0.75)  # past s1's original deadline
    assert rig.radio.writes == [True, True]

    await asyncio.sleep(_BOUND * 0.75)  # past s2's
    assert rig.radio.writes == [True, True, False]
    assert _logged(caplog, logging.ERROR, _S2)


async def test_a_managed_radio_arms_no_bound(
    managed_seat: tuple[RigctldHandler, _Radio],
) -> None:
    """The supervisor keeps its own watchdog; a second bound behind its back
    would de-key an over it believes it still owns."""
    handler, radio = managed_seat

    await handler._route_ptt(True, session_id=_S1)  # noqa: SLF001
    assert handler._key_down_backstop_task is None  # noqa: SLF001
    await _past_the_bound()

    assert radio.writes == []


async def test_a_key_this_seat_never_issued_arms_no_bound(seat: _Make) -> None:
    """The rig is keyed — by the front panel, or another ingress. A key this
    seat never issued is not its to time out (MOR-1175)."""
    rig = seat(ptt=True)

    assert not rig.armed
    await _past_the_bound()

    assert rig.radio.writes == []


async def test_a_key_write_that_raises_arms_no_bound(seat: _Make) -> None:
    """Arm below the write: a key that never reached the rig started no over."""
    rig = seat(fail_on=True)

    with pytest.raises(ConnectionError):
        await rig.route(True)
    assert not rig.armed
    await _past_the_bound()

    assert rig.radio.writes == [True]


async def test_server_stop_cancels_an_outstanding_bound(
    seat: _Make, caplog: pytest.LogCaptureFixture
) -> None:
    """Shutdown adds no write path (owner question 1): it cancels and says so."""
    rig = seat(ptt=True)
    server = RigctldServer(rig.radio, RigctldConfig(), _handler=rig.handler)  # type: ignore[arg-type]

    await rig.route(True)
    await server.stop()

    assert not rig.armed
    await _past_the_bound()

    assert rig.radio.writes == [True]
    assert _logged(caplog, logging.WARNING, "shutdown cancelled")


def test_the_bound_is_the_shared_backend_maximum() -> None:
    """Imported, never re-spelled — the requirement MOR-1220 made explicit."""
    assert (
        handler_mod._KEY_DOWN_BACKSTOP_SECONDS  # noqa: SLF001
        == BACKEND_MAX_KEY_DOWN_SECONDS
        == 180.0
    )

    source = Path(handler_mod.__file__).read_text(encoding="utf-8")
    assert "_KEY_DOWN_BACKSTOP_SECONDS: float = BACKEND_MAX_KEY_DOWN_SECONDS" in source

    backstop = "".join(
        inspect.getsource(member)
        for member in (
            RigctldHandler._arm_key_down_backstop,  # noqa: SLF001
            RigctldHandler._void_key_down_backstop,  # noqa: SLF001
            RigctldHandler._run_key_down_backstop,  # noqa: SLF001
            RigctldHandler.stop_key_down_backstop,
        )
    )
    assert "180" not in backstop
