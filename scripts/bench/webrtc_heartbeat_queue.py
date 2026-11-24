"""Measure aiortc's data-channel queue with a virtual link and no radio or sockets.

Use the Pro environment to measure the aiortc wheel shipped on both Tower peers.
Run with `uv run --all-extras python ../rigplane-core/scripts/bench/webrtc_heartbeat_queue.py`
from a Pro checkout with a sibling Core checkout. The Core environment measures
the separately locked upstream aiortc build.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import cast
from unittest.mock import patch

from aiortc import RTCDataChannel, RTCDataChannelParameters, RTCDtlsTransport
from aiortc.rtcsctptransport import RTCSctpTransport

from rigplane.testing.timing import VirtualClock

BURST_FRAMES = 225
FRAME_PERIOD_S = 0.020
PROBE_COUNT = 20
PROBE_PERIOD_S = 0.250
AUDIO_SERVICE_S = 0.050
CONTROL_SERVICE_S = 0.001
TX_FRAME = bytes(960)  # 20 ms of 24 kHz mono PCM16; one SCTP chunk
HEARTBEAT = '{"type":"controller_heartbeat"}'
ACK = '{"type":"controller_heartbeat","generation":1}'


@dataclass(frozen=True)
class QueueSample:
    control_frames: int
    audio_frames: int
    control_buffered_bytes: int
    audio_buffered_bytes: int


@dataclass
class Probe:
    enqueue_s: float
    client_queue: QueueSample
    client_wait_s: float | None = None
    station_queue: QueueSample | None = None
    station_wait_s: float | None = None
    rtt_s: float | None = None


@dataclass(frozen=True)
class DormantDtls:
    state: str = "new"


def new_transport() -> RTCSctpTransport:
    # The substitute transmission below handles DATA directly.
    sctp = RTCSctpTransport(cast(RTCDtlsTransport, DormantDtls()))
    sctp._association_state = sctp.State.ESTABLISHED
    return sctp


def new_channel(
    sctp: RTCSctpTransport, label: str, stream_id: int, *, ordered: bool
) -> RTCDataChannel:
    return RTCDataChannel(
        sctp,
        RTCDataChannelParameters(
            label=label,
            id=stream_id,
            negotiated=True,
            ordered=ordered,
            maxRetransmits=None if ordered else 0,
        ),
        send_open=False,
    )


def snapshot(control: RTCDataChannel, audio: RTCDataChannel) -> QueueSample:
    queue = control.transport._data_channel_queue
    return QueueSample(
        control_frames=sum(channel is control for channel, _, _ in queue),
        audio_frames=sum(channel is audio for channel, _, _ in queue),
        control_buffered_bytes=control.bufferedAmount,
        audio_buffered_bytes=audio.bufferedAmount,
    )


async def measure(clock: VirtualClock, *, audio_present: bool) -> None:
    client, station = new_transport(), new_transport()
    control = new_channel(client, "control", 0, ordered=True)
    tx = new_channel(client, "audio", 2, ordered=False)
    station_control = new_channel(station, "control", 0, ordered=True)
    station_audio = new_channel(station, "audio", 2, ordered=False)
    probes: list[Probe] = []
    awaiting_request: deque[Probe] = deque()
    awaiting_ack: deque[tuple[Probe, float]] = deque()
    delivered: deque[Probe] = deque()
    returning: deque[Probe] = deque()

    async def client_transmit() -> None:
        chunk = client._outbound_queue[0]
        is_control = chunk.stream_id == control.id
        probe = awaiting_request.popleft() if is_control else None
        if probe is not None:
            probe.client_wait_s = clock.now - probe.enqueue_s
        await asyncio.sleep(CONTROL_SERVICE_S if is_control else AUDIO_SERVICE_S)
        client._outbound_queue.popleft()
        if probe is not None:
            delivered.append(probe)
        await station._data_channel_receive(
            chunk.stream_id, chunk.protocol, chunk.user_data
        )

    async def station_transmit() -> None:
        chunk = station._outbound_queue[0]
        probe, queued_s = awaiting_ack.popleft()
        probe.station_wait_s = clock.now - queued_s
        await asyncio.sleep(CONTROL_SERVICE_S)
        station._outbound_queue.popleft()
        returning.append(probe)
        await client._data_channel_receive(
            chunk.stream_id, chunk.protocol, chunk.user_data
        )

    def receive_request(data: object) -> None:
        assert data == HEARTBEAT
        probe = delivered.popleft()
        probe.station_queue = snapshot(station_control, station_audio)
        awaiting_ack.append((probe, clock.now))
        station_control.send(ACK)

    def receive_ack(data: object) -> None:
        assert data == ACK
        probe = returning.popleft()
        probe.rtt_s = clock.now - probe.enqueue_s

    station_control.on("message", receive_request)
    control.on("message", receive_ack)

    async def send_audio() -> None:
        start = clock.now
        for frame in range(BURST_FRAMES):
            await asyncio.sleep(max(0.0, start + frame * FRAME_PERIOD_S - clock.now))
            tx.send(TX_FRAME)

    async def send_probes() -> None:
        start = clock.now
        for index in range(1, PROBE_COUNT + 1):
            await asyncio.sleep(max(0.0, start + index * PROBE_PERIOD_S - clock.now))
            probe = Probe(clock.now, snapshot(control, tx))
            probes.append(probe)
            awaiting_request.append(probe)
            control.send(HEARTBEAT)

    with (
        patch.object(client, "_transmit", client_transmit),
        patch.object(station, "_transmit", station_transmit),
    ):
        tasks = [asyncio.create_task(send_probes())]
        if audio_present:
            tasks.append(asyncio.create_task(send_audio()))
        # Covers the full serial-link service budget and the last scheduled probe.
        await clock.elapse(
            BURST_FRAMES * AUDIO_SERVICE_S + PROBE_COUNT * PROBE_PERIOD_S
        )
        assert all(task.done() for task in tasks)
        await asyncio.gather(*tasks)

    assert len(probes) == PROBE_COUNT
    assert all(
        probe.rtt_s is not None
        and probe.client_wait_s is not None
        and probe.station_wait_s is not None
        for probe in probes
    )
    assert not client._data_channel_queue and not station._data_channel_queue
    print(
        json.dumps(
            {
                "aiortc": version("aiortc"),
                "tx_audio": "present" if audio_present else "gated",
                "audio_service_s": AUDIO_SERVICE_S,
                "max_client_wait_s": max(
                    probe.client_wait_s or 0.0 for probe in probes
                ),
                "max_station_wait_s": max(
                    probe.station_wait_s or 0.0 for probe in probes
                ),
                "max_rtt_s": max(probe.rtt_s or 0.0 for probe in probes),
                "probes": [asdict(probe) for probe in probes],
            }
        )
    )


if __name__ == "__main__":
    for tx_present in (True, False):
        clock = VirtualClock()
        clock.run(lambda: measure(clock, audio_present=tx_present))
