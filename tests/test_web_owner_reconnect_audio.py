"""Web-level MOR-3415 pin: a held RX audio subscription survives an owner
disconnect + connect through the web endpoints.

The runtime-level suite (``tests/test_owner_reconnect_audio_demand.py``)
pins ``CoreRadio._recover_audio_after_connect`` over doubles; the soak
smoke (``tests/test_soak_driver.py``) pins the whole operated stack with
the audio port on. This file pins the web seat in between: the real
simulator stack of ``soak_driver._start_simulator_stack``
(``MockIcomRadio(enable_audio=True)`` + ``IcomRadio`` + ``WebServer``),
an RX subscription held the way the web audio handler holds it
(``web/handlers/audio.py`` routes ``audio_start`` through
``audio_session.subscribe_rx("web-audio")``), and the owner POSTs
``/api/v1/radio/disconnect`` then ``/api/v1/radio/connect`` over real
HTTP. After the connect returns, RX frames must reach the still-held
subscription again and the simulator must have kept sending
(``audio_rx_frames_sent`` grows), which is exactly what the MOR-3415
fix re-establishes.
"""

from __future__ import annotations

import asyncio

import aiohttp

from rigplane.audio import AudioPacket
from rigplane.audio.session import RxSubscription
from scripts.soak import soak_driver


async def _post_radio_control(
    session: aiohttp.ClientSession, base_url: str, action: str
) -> int:
    async with session.post(
        f"{base_url}/api/v1/radio/{action}",
        data=b"",
        timeout=aiohttp.ClientTimeout(total=30.0),
    ) as resp:
        return resp.status


async def _next_frame_or_none(
    sub: RxSubscription, timeout_s: float
) -> AudioPacket | None:
    try:
        return await sub.get(timeout=timeout_s)
    except asyncio.TimeoutError:
        return None


async def test_rx_frames_resume_after_owner_reconnect() -> None:
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sim = stack.sim
        base_url = f"http://127.0.0.1:{stack.web_port}"

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, 10.0) is not None, (
            "no RX frame reached the subscriber before the reconnect"
        )

        async with aiohttp.ClientSession() as session:
            status = await _post_radio_control(session, base_url, "disconnect")
            assert status == 200
            status = await _post_radio_control(session, base_url, "connect")
            assert status == 200

        # Drop frames queued before the reconnect: the next packet must
        # come from the stream the connect re-established, not from a
        # pre-disconnect burst still sitting in the queue.
        while True:
            try:
                sub.get_nowait()
            except asyncio.QueueEmpty:
                break
        sent_before = sim.audio_rx_frames_sent

        packet = await _next_frame_or_none(sub, 20.0)
        assert packet is not None, (
            "RX frames never resumed on the held subscription after the "
            "owner disconnect + connect (MOR-3415)"
        )
        assert sim.audio_rx_frames_sent > sent_before, (
            "simulator stopped sending RX audio after the owner reconnect"
        )

        await sub.release()
    finally:
        await stack.aclose()
