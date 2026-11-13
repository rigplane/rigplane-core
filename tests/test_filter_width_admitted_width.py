"""set_filter_width responses expose the profile-admitted width (MOR-2533)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.handlers.control import ControlHandler

ROOT = Path(__file__).resolve().parents[1]
NARROW_FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object, **_metadata: object) -> None:
        self.items.append(item)

    def put_ordered(self, item: object, **_metadata: object) -> None:
        self.items.append(item)


class _Ws:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


def _store_with_mode(
    mode: str, receiver_id: str = "main", *, stale: bool = False
) -> StateStore:
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.active(receiver_id, "freq_mode", "mode"),
            value=mode,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    if stale:
        store.mark_stale_due(now=5.1)
    return store


def _narrow_fixture_profile():
    """The single-RX fixture as the web handler sees it (a RadioProfile)."""
    return load_rig(NARROW_FIXTURE).to_profile()


def _handler(
    *, state_store: StateStore | None, profile=None
) -> tuple[ControlHandler, _Ws, _QueueRecorder]:
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue, command_state_store=state_store)
    ws = _Ws()
    profile = profile if profile is not None else resolve_radio_profile(model="FTX-1")
    radio = SimpleNamespace(
        capabilities=set(profile.capabilities),
        profile=profile,
        model=profile.model,
        supports_command=MagicMock(return_value=True),
    )
    handler = ControlHandler(ws, radio, "test", profile.model, server=server)
    return handler, ws, queue


async def _dispatch_width(handler: ControlHandler, width: int) -> dict[str, object]:
    await handler._dispatch_command(  # noqa: SLF001
        "c1", "set_filter_width", {"width": width, "receiver": 0}
    )
    assert handler._ws.frames, "no response frame was sent"  # noqa: SLF001
    frame = handler._ws.frames[-1]  # noqa: SLF001
    assert frame["type"] == "response"
    assert frame["ok"] is True
    return frame


async def test_set_filter_width_response_exports_admitted_width() -> None:
    # FTX-1 SSB table (rigs/ftx1.toml [filters.width.SSB]): the entry nearest
    # to 2350 is 2400 (50 Hz away; 2250 is 100 Hz away).
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {
        "width": 2350,
        "receiver": 0,
        "admitted_width": 2400,
    }


async def test_set_filter_width_on_table_value_admits_the_request() -> None:
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))

    frame = await _dispatch_width(handler, 2400)

    assert frame["result"] == {"width": 2400, "receiver": 0, "admitted_width": 2400}


async def test_set_filter_width_omits_admitted_width_without_an_observed_mode() -> None:
    handler, _ws, _queue = _handler(state_store=None)

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_omits_admitted_width_when_observed_mode_is_stale() -> (
    None
):
    handler, _ws, _queue = _handler(state_store=_store_with_mode("CW-L", stale=True))

    frame = await _dispatch_width(handler, 1950)

    assert frame["result"] == {"width": 1950, "receiver": 0}


async def test_set_filter_width_omits_non_integer_profile_admission() -> None:
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))
    profile = MagicMock()
    profile.admitted_filter_width.return_value = "2400"
    handler._radio.profile = profile  # type: ignore[union-attr]  # noqa: SLF001

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_omits_admitted_width_for_a_table_less_mode() -> None:
    # C4FM-DN is a live FTX-1 mode with no [filters.width] entry, so
    # RadioProfile.resolve_filter_rule returns None for it.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("C4FM-DN"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_omits_admitted_width_for_a_fixed_width_mode() -> None:
    # AM is a fixed-width FTX-1 row (rigs/ftx1.toml [filters.width.AM]); the
    # backend refuses the write there, so there is no admission to export.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("AM"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_admission_uses_the_requested_receiver_mode() -> None:
    # SUB observed in CW-L (table tops out at 4000 with 2400 present, but the
    # nearest entry to 2350 is 2400 (50 Hz away; 2000 is 350 Hz away);
    # receiver=1 must read the sub-mode row.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("CW-L", "sub"))
    await handler._dispatch_command(  # noqa: SLF001
        "c1", "set_filter_width", {"width": 2350, "receiver": 1}
    )
    frame = handler._ws.frames[-1]  # noqa: SLF001

    assert frame["result"] == {
        "width": 2350,
        "receiver": 1,
        "admitted_width": 2400,
    }


def _store_with_narrow(mode: str, narrow: bool) -> StateStore:
    """Fresh mode plus a freshly observed narrow flag (MOR-4434 P2)."""
    store = _store_with_mode(mode)
    store.apply(
        Observation(
            path=FieldPath.receiver("main", "operator_toggles", "narrow"),
            value=narrow,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    return store


def _store_with_stale_narrow(mode: str, narrow: bool) -> StateStore:
    """Fresh mode with a stale narrow reading: only the narrow mark decays."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.receiver("main", "operator_toggles", "narrow"),
            value=narrow,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    store.mark_stale_due(now=5.1)
    store.apply(
        Observation(
            path=FieldPath.active("main", "freq_mode", "mode"),
            value=mode,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=5.1,
            max_age=5.0,
        )
    )
    return store


async def test_set_filter_width_narrow_variant_true_maps_narrow_table() -> None:
    # Fixture SSB narrow table is [1500, 1800, 2000]: with NARROW freshly
    # on, 1800 admits to itself through the narrow table.
    handler, _ws, _queue = _handler(
        state_store=_store_with_narrow("USB", True),
        profile=_narrow_fixture_profile(),
    )

    frame = await _dispatch_width(handler, 1800)

    assert frame["result"] == {"width": 1800, "receiver": 0, "admitted_width": 1800}


async def test_set_filter_width_narrow_variant_false_maps_wide_table() -> None:
    # Freshly observed NARROW-off keeps today's wide-table mapping: 1800 is
    # not a wide entry ([2400, 2600, 3000, 3200, 4000]), nearest is 2400.
    handler, _ws, _queue = _handler(
        state_store=_store_with_narrow("USB", False),
        profile=_narrow_fixture_profile(),
    )

    frame = await _dispatch_width(handler, 1800)

    assert frame["result"] == {"width": 1800, "receiver": 0, "admitted_width": 2400}


async def test_set_filter_width_narrow_variant_unknown_narrow_omits_hint() -> None:
    # Mode observed, narrow never observed: neither table is trustworthy, so
    # the response carries no admitted_width (MOR-4434 P2).
    handler, _ws, _queue = _handler(
        state_store=_store_with_mode("USB"), profile=_narrow_fixture_profile()
    )

    frame = await _dispatch_width(handler, 1800)

    assert frame["result"] == {"width": 1800, "receiver": 0}


async def test_set_filter_width_narrow_variant_stale_narrow_omits_hint() -> None:
    # Fresh mode with a stale narrow reading omits the hint the same way.
    handler, _ws, _queue = _handler(
        state_store=_store_with_stale_narrow("USB", True),
        profile=_narrow_fixture_profile(),
    )

    frame = await _dispatch_width(handler, 1800)

    assert frame["result"] == {"width": 1800, "receiver": 0}


async def test_set_filter_width_without_narrow_variant_keeps_hint() -> None:
    # FTX-1 declares no narrow variant anywhere: an unknown narrow state
    # keeps today's wide-table hint byte-for-byte (no P2 behavior change).
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {
        "width": 2350,
        "receiver": 0,
        "admitted_width": 2400,
    }


@pytest.mark.parametrize(
    ("model", "mode", "width", "expected"),
    [
        ("FTX-1", "USB", 2350, 2400),
        ("TX-500", "USB", 2350, None),
        ("X6100", "USB", 2350, None),
        ("IC-7300", "USB", 2350, None),
    ],
)
def test_shipped_profiles_keep_todays_admission(
    model: str, mode: str, width: int, expected: int | None
) -> None:
    """MOR-4434: no shipped profile declares a narrow variant, so the P2
    omission changes none of their admissions. FTX-1 (table) admits 2400;
    TX-500/X6100 declare no width mapping and IC-7300 is segmented, so
    those admit nothing — each exactly as before."""
    profile = resolve_radio_profile(model=model)

    assert profile.admitted_filter_width(mode, width) == expected
