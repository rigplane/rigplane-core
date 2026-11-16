"""The narrow/wide ``set_filter_width`` seam document (MOR-4434).

``frontend/src/lib/transport/__tests__/fixtures/narrow-width-seam.json``
is the ONE shared document for the exchange: per-state observed values,
requests, real handler replies (incl. ``admitted_width``), and — for
observed states — the CAT wire plus readback. (The rule lives in the
producer-pinned ``fixture-narrow-capabilities.json``, not duplicated here.)
``test_committed_seam_document_is_current`` is the drift check (like the
state-wire goldens); ``test_seam_exchange_values`` pins the semantic
contract the browser specs consume.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.handlers.control import ControlHandler
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"
SIM_FIXTURE = ROOT / "rigplane_testkit/sim/profiles/yaesu-single-rx-fixture.toml"
SEAM_PATH = (
    ROOT / "frontend/src/lib/transport/__tests__/fixtures/narrow-width-seam.json"
)
_RENDER_COMMAND = "see tests/web/test_narrow_width_seam.py::render_seam_document"

_NARROW_SOURCE = SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat")


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


def _store_with_narrow(mode: str, narrow: bool | None) -> StateStore:
    """Fresh mode plus a freshly observed narrow flag (or none)."""
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.active("main", "freq_mode", "mode"),
            value=mode,
            source=_NARROW_SOURCE,
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    if narrow is not None:
        store.apply(
            Observation(
                path=FieldPath.receiver("main", "operator_toggles", "narrow"),
                value=narrow,
                source=_NARROW_SOURCE,
                timestamp_monotonic=0.0,
                max_age=5.0,
            )
        )
    return store


async def _dispatch_width(profile, store: StateStore, width: int) -> dict[str, object]:
    """The real handler reply for one ``set_filter_width`` request."""
    queue: _QueueRecorder = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue, command_state_store=store)
    ws = _Ws()
    radio = SimpleNamespace(
        capabilities=set(profile.capabilities),
        profile=profile,
        model=profile.model,
        supports_command=MagicMock(return_value=True),
    )
    handler = ControlHandler(ws, radio, "test", profile.model, server=server)
    await handler._dispatch_command(  # noqa: SLF001
        "c1", "set_filter_width", {"width": width, "receiver": 0}
    )
    assert handler._ws.frames, "no response frame was sent"  # noqa: SLF001
    frame = handler._ws.frames[-1]  # noqa: SLF001
    assert frame["type"] == "response"
    assert frame["ok"] is True
    return frame["result"]  # type: ignore[return-value]


async def _drive_case(narrow: bool, width: int) -> tuple[list[str], int | None]:
    """One observed state through the real CAT transport on a fresh sim."""
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=FIXTURE)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line: str):
        wire.append(line)
        return handle(line)

    async def open_port(*args: object, **kwargs: object):
        return await fake.open_serial_connection()

    with (
        mock.patch.object(fake.model, "handle_with_outcome", logged),
        mock.patch("rigplane.backends.yaesu_cat.transport.open_serial_port", open_port),
    ):
        radio = YaesuCatRadio("/dev/fixture", profile=load_rig(FIXTURE))
        await radio._transport.connect()
        try:
            await radio.set_mode("USB")
            await radio.set_narrow(narrow)
            await radio.set_filter_width(width)
            readback = await radio.get_filter_width()
        finally:
            await radio._transport.close()
    return wire, readback


def _render_case(
    profile,
    narrow: bool | None,
    *,
    state_width: int,
    request_width: int,
    drive: bool,
) -> dict[str, object]:
    """One exchange: observed state, request, handler reply, sim evidence."""
    case: dict[str, object] = {
        "state": {"mode": "USB", "narrow": narrow, "filterWidth": state_width},
        "request": {"width": request_width, "receiver": 0},
        "reply": asyncio.run(
            _dispatch_width(profile, _store_with_narrow("USB", narrow), request_width)
        ),
    }
    if drive:
        assert narrow is not None
        wire, readback = asyncio.run(_drive_case(narrow, request_width))
        case["readbackHz"] = readback
        case["wire"] = wire
    return case


def render_seam_document() -> str:
    """Canonical JSON for the committed seam document."""
    profile = load_rig(FIXTURE).to_profile()
    doc = {
        "cases": {
            # Alphabetical: the canonical dumps sort every key.
            "narrow": _render_case(
                profile, True, state_width=1500, request_width=1500, drive=True
            ),
            "unknown": _render_case(
                profile, None, state_width=2600, request_width=1500, drive=False
            ),
            "wide": _render_case(
                profile, False, state_width=2600, request_width=2600, drive=True
            ),
        },
        "format": 1,
        "profile": "tests/fixtures/rigs/yaesu_single_rx_fixture.toml",
        "simProfile": "rigplane_testkit/sim/profiles/yaesu-single-rx-fixture.toml",
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def test_committed_seam_document_is_current() -> None:
    # Fails on any producer drift (reply, admission, wire, readback, table).
    committed = SEAM_PATH.read_text(encoding="utf-8")
    assert committed == render_seam_document(), (
        f"narrow-width-seam.json is stale; {_RENDER_COMMAND}"
    )


def test_seam_exchange_values() -> None:
    # Fails if the handler stops attaching the admission hint
    # (control.py::_with_admitted_width) or the sim stops rendering the
    # loaded SH shape (_sh_code_digits/_width_code).
    """The document's semantic contract, independent of its byte layout."""
    doc = json.loads(SEAM_PATH.read_text(encoding="utf-8"))
    wide = doc["cases"]["wide"]
    assert wide["state"] == {"mode": "USB", "narrow": False, "filterWidth": 2600}
    assert wide["request"] == {"width": 2600, "receiver": 0}
    assert wide["reply"] == {"width": 2600, "receiver": 0, "admitted_width": 2600}
    assert wide["readbackHz"] == 2600
    assert "SH0001;" in wide["wire"]

    narrow = doc["cases"]["narrow"]
    assert narrow["state"] == {"mode": "USB", "narrow": True, "filterWidth": 1500}
    assert narrow["request"] == {"width": 1500, "receiver": 0}
    assert narrow["reply"] == {
        "width": 1500,
        "receiver": 0,
        "admitted_width": 1500,
    }
    assert narrow["readbackHz"] == 1500
    assert "SH0000;" in narrow["wire"]

    unknown = doc["cases"]["unknown"]
    assert unknown["state"] == {"mode": "USB", "narrow": None, "filterWidth": 2600}
    assert unknown["request"] == {"width": 1500, "receiver": 0}
    # Unknown narrow omits the hint: neither table is trustworthy.
    assert unknown["reply"] == {"width": 1500, "receiver": 0}
    assert "wire" not in unknown
    assert "readbackHz" not in unknown
