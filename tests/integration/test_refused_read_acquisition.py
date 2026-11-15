"""A read the radio refuses (NAK) is answered, not missing (MOR-4058).

Real radios refuse some declared reads: the bench IC-705 answers ``1C 01``
(tuner status) with a bare ``FA`` at every start
(``tests/fixtures/ic705/civ_pairs.jsonl``), and the IC-7610 USB recording has
no answer for ``1C 01``, ``1C 03`` or ``07 C2``, which its fake refuses. The
refusal used to be dropped: the startup gate waited its 10 s non-critical
deadline for the refused path, and the polling seats logged an
``acquisition_request_timeout`` for it on every cadence.

The seam is real end to end: the serial fake on a PTY, the production serial
backend, its commander and CI-V request tracker, the shared acquisition
scheduler, and the web or rigctld seat on top.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import pytest

from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import _observed_paths
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from testkit.ic705_fakes import RADIO_ADDR, ic705_lan_fake, ic705_usb_fake
from testkit.ic7610_fakes import ic7610_usb_fake

pytestmark = [
    pytest.mark.mock_integration,
    pytest.mark.skipif(not pty_supported(), reason="requires a PTY"),
]

TUNER_STATUS = FieldPath.global_("operator_controls", "tuner_status")

_SERIAL_RADIOS = {
    "IC-705": (ic705_usb_fake, Ic705SerialRadio),
    "IC-7610": (ic7610_usb_fake, Icom7610SerialRadio),
}


class _LanLink:
    """The recorded IC-705 LAN personality behind the production LAN backend."""

    def __init__(self) -> None:
        self.sim = ic705_lan_fake()

    async def stop(self) -> None:
        await self.sim.stop()


async def _connected_lan():
    link = _LanLink()
    await link.sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=link.sim.control_port,
        username="refusal",
        password="refusal",
        timeout=2.0,
        model="IC-705",
        radio_addr=RADIO_ADDR,
    )
    try:
        await asyncio.wait_for(radio.connect(), 30)
    except BaseException:
        await link.stop()
        raise
    return link, radio


async def _connected(model: str):
    make_fake, radio_cls = _SERIAL_RADIOS[model]
    fake = make_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = radio_cls(device=endpoint.device, _enumerate_serial_ports_fn=lambda: [])
    try:
        await asyncio.wait_for(radio.connect(), 30)
    except BaseException:
        await endpoint.stop()
        raise
    return fake, endpoint, radio


def _gated_web(radio, model: str) -> WebServer:
    return WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            radio_model=model,
            await_initial_state=True,
        ),
    )


@pytest.mark.parametrize("link", ["usb", "lan"])
async def test_ic705_refused_tuner_read_does_not_hold_the_startup_gate(
    link: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The bench IC-705 refuses ``1C 01`` on both links: open well under 10 s."""
    if link == "usb":
        fake, endpoint, radio = await _connected("IC-705")
    else:
        endpoint, radio = await _connected_lan()
        fake = None
    web = _gated_web(radio, "IC-705")
    try:
        started = time.monotonic()
        with caplog.at_level(logging.INFO):
            await asyncio.wait_for(web.start(), 30)
        opened_after = time.monotonic() - started
        if fake is not None:
            # Not vacuous: the radio was asked for the tuner status.
            assert any((f.command, f.sub) == (0x1C, 0x01) for f in fake.commands)
        messages = [record.getMessage() for record in caplog.records]
        assert not [m for m in messages if "no answer for" in m], messages
        assert opened_after < 5.0, f"listener opened after {opened_after:.1f}s"
        assert any(
            "refused" in m and str(TUNER_STATUS) in m
            for m in messages
            if m.startswith("acquisition:")
        ), "the refused path is named once at INFO"
        assert radio._acquisition_scheduler.consecutive_refusals(TUNER_STATUS) > 0
    finally:
        await web.stop()
        await radio.disconnect()
        await endpoint.stop()


async def test_refused_tx_target_read_no_longer_fails_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The IC-7610 USB recording has no ``1C 03`` (TX target) answer, so its
    fake refuses it. MOR-4433: the TX target is not safety-critical, so a
    refused TX-target read no longer fails startup — the gate opens with it
    unobserved, and the refusal is still counted, not dropped."""
    fake, endpoint, radio = await _connected("IC-7610")
    web = _gated_web(radio, "IC-7610")
    try:
        with caplog.at_level(logging.INFO):
            await asyncio.wait_for(web.start(), 30)
        # Not vacuous: the radio was asked for the TX target.
        assert any((f.command, f.sub) == (0x1C, 0x03) for f in fake.commands)
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "refused" in m and "global.tx_state.tx_target" in m
            for m in messages
            if m.startswith("acquisition:")
        ), "the refused path is named once at INFO"
        assert (
            radio._acquisition_scheduler.consecutive_refusals(
                FieldPath.global_("tx_state", "tx_target")
            )
            > 0
        )
        assert await _served_tx_target(web) == {
            "status": "unknown",
            "reason": "not-observed",
        }
    finally:
        await web.stop()
        await radio.disconnect()
        await endpoint.stop()


async def _http_get_json(port: int, path: str) -> tuple[int, Any]:
    """GET *path* from the test listener over a raw socket, parsed as JSON."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), 5.0)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(body)


async def _served_tx_target(web: WebServer) -> Any:
    """The ``txTarget`` the running listener serves on ``/api/v1/state``."""
    port = int(web._server.sockets[0].getsockname()[1])  # noqa: SLF001
    status, body = await _http_get_json(port, "/api/v1/state")
    assert status == 200
    return body["txTarget"]


async def test_silent_tx_target_read_does_not_hold_the_startup_gate() -> None:
    """MOR-4433 (Icom shape): a silent ``1C 03`` neither aborts nor starves.

    The fake answers everything except the TX-target read — which stays
    silent instead of NAKed — through the production serial backend and
    poller. PTT and split still arrive, the gate opens with the TX target
    unobserved, the silence is counted as timeouts (not dropped), and the
    real HTTP state reports the TX target unknown.
    """
    fake, endpoint, radio = await _connected("IC-7610")
    real_answer = fake._answer  # noqa: SLF001

    def _answer_all_but_tx_target(parsed, connection, frame):
        if parsed.command == 0x1C and parsed.sub == 0x03:
            return None  # the radio stays silent on 1C 03; the rest answers
        return real_answer(parsed, connection, frame)

    fake._answer = _answer_all_but_tx_target  # noqa: SLF001
    web = _gated_web(radio, "IC-7610")
    tx_target = FieldPath.global_("tx_state", "tx_target")
    try:
        await asyncio.wait_for(web.start(), 30)
        # Not vacuous: the radio was asked for the TX target — and stayed
        # silent, so the request timed out instead of being refused.
        assert any((f.command, f.sub) == (0x1C, 0x03) for f in fake.commands)
        await await_until(
            lambda: (
                radio._acquisition_scheduler.consecutive_request_timeouts(tx_target) > 0
            ),
            timeout=15.0,
            description="silent 1C 03 request timeout",
        )
        assert radio._acquisition_scheduler.consecutive_refusals(tx_target) == 0
        scheduler = radio._acquisition_scheduler
        assert scheduler.startup_defect is None
        # Profile-spelled observed paths (the store spells receivers
        # ``0``/``1``; ``_observed_paths`` resolves the alias).
        observed = _observed_paths(web, scheduler)
        assert tx_target not in observed
        for path in (
            FieldPath.active("main", "freq_mode", "freq_hz"),
            FieldPath.active("main", "freq_mode", "mode"),
            FieldPath.global_("tx_state", "ptt"),
            FieldPath.global_("tx_state", "split"),
        ):
            assert path in observed, path
        assert await _served_tx_target(web) == {
            "status": "unknown",
            "reason": "not-observed",
        }
    finally:
        await web.stop()
        await radio.disconnect()
        await endpoint.stop()


@pytest.mark.parametrize(
    ("path_name", "command", "sub"),
    [("ptt", 0x1C, 0x00), ("split", 0x0F, None)],
)
async def test_refused_ptt_or_split_read_opens_with_tx_unknown(
    caplog: pytest.LogCaptureFixture, path_name: str, command: int, sub: int | None
) -> None:
    """MOR-4456: a refused PTT/split read is unavailable, not fatal.

    PTT (``1C 00``) and split (``0F``) left the critical set: a NAKed read
    still opens — the path is answered-but-unavailable at INFO, no defect
    is recorded, TX is served unknown, and no gate WARNING names it. The
    test queues enough ``refuse_next`` NAKs for the initial fetch plus
    the re-reads.
    """
    fake, endpoint, radio = await _connected("IC-7610")
    for _ in range(30):
        fake.refuse_next(command)
    web = _gated_web(radio, "IC-7610")
    path = FieldPath.global_("tx_state", path_name)
    try:
        with caplog.at_level(logging.INFO):
            await asyncio.wait_for(web.start(), 30)
        # Not vacuous: the radio was asked for the refused read.
        assert any((f.command, f.sub) == (command, sub) for f in fake.commands)
        assert radio._acquisition_scheduler.consecutive_refusals(path) > 0
        assert radio._acquisition_scheduler.startup_defect is None
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "refused" in message and str(path) in message
            for message in messages
            if message.startswith("acquisition:")
        ), "the refused path is named once at INFO"
        assert not any(
            record.levelno >= logging.WARNING and str(path) in record.getMessage()
            for record in caplog.records
        ), "an unavailable path lapses no deadline WARNING"
        status, body = await _http_get_json(
            int(web._server.sockets[0].getsockname()[1]),  # noqa: SL001
            "/api/v1/state",
        )
        assert status == 200
        # Null leaf + unobserved fieldStatus (the MOR-2425 nulling rule).
        assert body[path_name] is None
        assert body["fieldStatus"][path_name]["observed"] is False
    finally:
        await web.stop()
        await radio.disconnect()
        await endpoint.stop()


@pytest.mark.parametrize("model", sorted(_SERIAL_RADIOS))
async def test_refused_reads_never_count_as_timeouts_while_writes_stay_fast(
    model: str,
) -> None:
    fake, endpoint, radio = await _connected(model)
    server = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        scheduler = radio._acquisition_scheduler
        window_end = time.monotonic() + 15.0
        latencies = []
        target = fake.frequency_hz
        while time.monotonic() < window_end:
            target += 100
            sent = time.monotonic()
            writer.write(f"F {target}\n".encode())
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), 5) == b"RPRT 0\n"
            while fake.frequency_hz != target:
                await asyncio.sleep(0.005)
            latencies.append(time.monotonic() - sent)
            await asyncio.sleep(1.0)
        # Not vacuous: the refused reads were polled during the window.
        refusals = scheduler.diagnostics()["failureCountByReason"]
        assert refusals.get("acquisition_request_refused", 0) > 0, refusals
        assert refusals.get("acquisition_request_timeout", 0) == 0, refusals
        assert max(latencies) < 1.0, latencies
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await server.stop()
        await radio.disconnect()
        await endpoint.stop()
