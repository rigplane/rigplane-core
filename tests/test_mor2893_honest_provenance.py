"""MOR-2893: radio-confirmed provenance must come from a parsed radio answer.

A field may carry ``command_response`` or ``poll_response`` only when the
radio actually answered something this code parsed. The scan family
(CI-V 0x0E, SET-only on Icom) can never be radio-confirmed, so its seed at
connect and its fire-and-forget command echoes must carry an explicitly
unconfirmed source instead — same for the public-API sync facade's
expected-value echo after fire-and-forget CI-V setters.

Silent-link legs reuse the never-answering fake serial CI-V link from
``test_icom7610_serial_radio.py``; the answering leg answers exactly one
set command (the IC-7610 power-off 0x18 0x00) with a directed 0xFB ACK,
the same shape the fake already uses for the managed-TX writes (MOR-2860).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    ScanSetResume,
    ScanStart,
    ScanStop,
)

from test_icom7610_serial_radio import _FakeSerialCivLink, _wait_until
from test_radio_poller_coverage import _make_radio

_FORBIDDEN_SOURCES = frozenset({"command_response", "poll_response"})


@pytest.fixture(autouse=True)
def _no_real_serial_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same hermeticity guard as ``test_icom7610_serial_radio``: the synthetic
    device path must never fall through to real OS serial enumeration or a
    real CI-V identity probe, regardless of what hardware is attached."""
    monkeypatch.setattr(
        _IcomSerialRadioBase,
        "_default_enumerate_serial_ports",
        lambda self: [],
    )

    async def _no_probe(self: object, port: str) -> int | None:
        raise AssertionError(
            f"unexpected real CI-V identity probe attempted on {port!r}"
        )

    monkeypatch.setattr(_IcomSerialRadioBase, "_default_civ_identity_probe", _no_probe)


def _radio_confirmed_fields(store: StateStore) -> list[str]:
    """Every store field whose source claims the radio answered."""

    return [
        f"{field.source.source} {field.path}"
        for field in store.snapshot().fields
        if field.source.source in _FORBIDDEN_SOURCES
    ]


class _PowerOffAckCivLink(_FakeSerialCivLink):
    """Silent except one directed 0xFB ACK to the power-off set (0x18 0x00)."""

    async def send(self, frame: bytes) -> None:
        payload = bytes(frame)
        if payload[4:-1] == b"\x18\x00":
            self.queue_response(
                bytes((0xFE, 0xFE, payload[3], payload[2], 0xFB, 0xFD))
            )
        await super().send(frame)


@pytest.mark.asyncio
async def test_silent_serial_startup_writes_no_radio_confirmed_source() -> None:
    """The MOR-2893 evidence path: a server on a link that never answers.

    RadioPoller's one-time startup section seeds ``scanning`` /
    ``scan_resume_mode`` (and reads the NB/MOD controls) without a single
    byte from the radio — after it, no field in the store the public state
    reads may claim ``command_response`` or ``poll_response``."""
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link, timeout=0.1)
    await radio.connect()
    store = radio.state_store
    poller = RadioPoller(
        radio, CommandQueue(), radio_state=radio._radio_state, state_store=store
    )

    # The startup section of RadioPoller._run(), verbatim order.
    await poller._fetch_nb_controls()  # noqa: SLF001
    await poller._fetch_mod_inputs()  # noqa: SLF001
    poller._seed_scan_facts_at_connect()  # noqa: SLF001

    assert _radio_confirmed_fields(store) == []
    scanning = store.snapshot().field(FieldPath.global_("slow_state", "scanning"))
    assert scanning.value is False
    assert scanning.source.source == "local_reconcile"
    resume = store.snapshot().field(
        FieldPath.global_("slow_state", "scan_resume_mode")
    )
    assert resume.source.source == "local_reconcile"

    await radio.disconnect()


@pytest.mark.asyncio
async def test_scan_command_echoes_write_no_radio_confirmed_source() -> None:
    """ScanStart/ScanStop/ScanSetResume are fire-and-forget (CI-V 0x0E is
    SET-only): their echoes can never be a radio answer. Values must still
    land (the UI bootstrap and command lifecycle depend on them) — only the
    provenance changes."""
    radio = _make_radio(active="MAIN", model="IC-7300")
    store = StateStore()
    poller = RadioPoller(
        radio, CommandQueue(), radio_state=RadioState(), state_store=store
    )

    await poller._execute(ScanStart(scan_type=0x01))  # noqa: SLF001
    await poller._execute(ScanStop())  # noqa: SLF001
    await poller._execute(ScanSetResume(mode=0xD2))  # noqa: SLF001

    assert _radio_confirmed_fields(store) == []
    snapshot = store.snapshot()
    assert snapshot.field(FieldPath.global_("slow_state", "scanning")).value is False
    assert snapshot.field(FieldPath.global_("slow_state", "scan_type")).value == 0
    assert (
        snapshot.field(FieldPath.global_("slow_state", "scan_resume_mode")).value
        == 0x02
    )


@pytest.mark.asyncio
async def test_answering_link_power_off_ack_records_command_response() -> None:
    """Regression pin for the honest direction: a parsed directed 0xFB ACK to
    the power-off set still records ``command_response`` on the same store."""
    link = _PowerOffAckCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link, timeout=0.1)
    await radio.connect()
    assert await _wait_until(lambda: radio.connected, timeout_s=5.0)

    await radio.set_powerstat(False)

    field = radio.state_store.snapshot().field(
        FieldPath.global_("tx_state", "power_on")
    )
    assert field.value is False
    assert field.source.source == "command_response"

    await radio.disconnect()


def test_public_api_sync_set_records_local_reconcile_not_command_response() -> None:
    """The sync facade's setters are fire-and-forget CI-V writes: the expected
    value they echo into the facade's local store was never answered by the
    radio, so it must not claim ``command_response``."""
    from rigplane.runtime.sync import IcomRadio as SyncIcomRadio

    sync_radio = SyncIcomRadio("192.0.2.1")
    sync_radio._radio.set_freq = AsyncMock()  # noqa: SLF001
    try:
        sync_radio.set_freq(7_050_000)

        field = sync_radio._state_store.snapshot().field(  # noqa: SLF001
            FieldPath.receiver("0", "freq_mode", "freq_hz")
        )
        assert field.value == 7_050_000
        assert field.source.source == "local_reconcile"
    finally:
        sync_radio._loop.close()  # noqa: SLF001
