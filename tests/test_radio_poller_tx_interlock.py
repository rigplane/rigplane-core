import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.capabilities import CAP_ANTENNA, CAP_POWER_CONTROL, CAP_TUNER
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import (
    PttOff,
    ScanStart,
    ScanStop,
    SendCiv,
    SetAntenna1,
    SetPowerstat,
    SetTunerStatus,
)
from rigplane.web.radio_poller import CommandQueue, RadioPoller


_PTT = FieldPath.global_("tx_state", "ptt")


def _radio() -> SimpleNamespace:
    return SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_POWER_CONTROL, CAP_TUNER},
        send_civ=AsyncMock(),
        scan_start=AsyncMock(),
        scan_stop=AsyncMock(),
        set_antenna_1=AsyncMock(),
        set_tuner_status=AsyncMock(),
        set_powerstat=AsyncMock(),
        set_ptt=AsyncMock(),
    )


def _observe_ptt(
    store: StateStore,
    value: bool,
    *,
    observed_at: float | None = None,
    generation: int | None = None,
) -> None:
    observed_at = time.monotonic() if observed_at is None else observed_at
    generation = store.provider_generation if generation is None else generation
    store.apply(
        Observation(
            path=_PTT,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=observed_at,
            max_age=1.0,
            provider_generation=generation,
        )
    )


def _poller() -> tuple[RadioPoller, SimpleNamespace, StateStore]:
    radio, store = _radio(), StateStore()
    store.begin_provider_generation()
    return RadioPoller(radio, CommandQueue(), state_store=store), radio, store


async def _dispatch(poller: RadioPoller, cmd: object) -> None:
    poller._enforce_tx_interlock(cmd)  # type: ignore[arg-type] # noqa: SLF001
    await poller._execute(cmd)  # type: ignore[arg-type] # noqa: SLF001


_BLOCK_CASES = (
    (SendCiv(command=0x1A, data=b"\x01"), "send_civ"),
    (ScanStart(scan_type=1), "scan_start"),
    (SetAntenna1(on=True), "set_antenna_1"),
    (SetTunerStatus(value=1), "set_tuner_status"),
)


@pytest.mark.parametrize(("cmd", "method"), _BLOCK_CASES)
@pytest.mark.parametrize("ptt", (None, True), ids=("unknown", "tx"))
async def test_disruptive_write_is_blocked_before_transport(
    cmd: object, method: str, ptt: bool | None
) -> None:
    poller, radio, store = _poller()
    if ptt is not None:
        _observe_ptt(store, ptt)

    with pytest.raises(CommandError, match="RF state is (unknown|TX)"):
        await _dispatch(poller, cmd)

    getattr(radio, method).assert_not_awaited()


@pytest.mark.parametrize(("cmd", "method"), _BLOCK_CASES)
async def test_disruptive_write_dispatches_once_in_fresh_rx(
    cmd: object, method: str
) -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)

    await _dispatch(poller, cmd)

    getattr(radio, method).assert_awaited_once()


async def test_fresh_rx_preserves_truthful_unsupported_failure() -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)
    del radio.send_civ
    with pytest.raises(CommandError, match="send_civ is not supported"):
        await _dispatch(poller, SendCiv(command=0x1A, data=b"\x01"))


@pytest.mark.parametrize(
    ("cmd", "method"),
    (
        (PttOff(), "set_ptt"),
        (ScanStop(), "scan_stop"),
        (SetPowerstat(on=False), "set_powerstat"),
        (SetTunerStatus(value=0), "set_tuner_status"),
    ),
)
async def test_safety_stop_or_off_is_always_attempted(cmd: object, method: str) -> None:
    poller, radio, _store = _poller()
    poller._current_rf_state = lambda: pytest.fail("stop/off inspected RF state")  # type: ignore[method-assign] # noqa: SLF001
    await _dispatch(poller, cmd)

    getattr(radio, method).assert_awaited_once()


def test_manual_clock_ttl_generation_and_recovery() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    _observe_ptt(store, False, observed_at=clock.now())
    assert store.snapshot().generated_at_monotonic == clock.now()
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001
    clock.advance(0.999)
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001
    clock.advance(0.001)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    clock.advance(0.001)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    old_generation = store.provider_generation
    store.begin_provider_generation()
    _observe_ptt(store, True, observed_at=clock.now(), generation=old_generation)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    _observe_ptt(store, True, observed_at=clock.now())
    assert poller._current_rf_state().value == "tx"  # noqa: SLF001
    clock.advance(0.1)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001
