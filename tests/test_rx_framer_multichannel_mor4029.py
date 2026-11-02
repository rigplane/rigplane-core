"""Regression tests for MOR-4029 — RX from a >2-channel capture device.

PipeWire's ``pulse`` alias advertises 32 input channels, BlackHole 16, and
4/6/8-channel interfaces exist. When such a device is under-requested, the
driver's channel clamp opens the OS stream at the device-NATIVE count and the
stream must software-downmix N open channels to the delivered layout. The
framer downmix only fired for open == 2 / deliver == 1 (the MOR-504
stereo-to-mono case), so N > 2 interleaved PCM passed through UNCHANGED as if
it were the delivered layout — garbage RX audio on every radio behind such a
device.

These tests pin the ONE shared downmix mechanism generalized to
``open_channels (N >= 2) -> deliver_channels (1 or 2)``:

- the FIRST TWO device channels are L/R (the extra virtual-cable/interface
  channels carry no radio audio; averaging all N would only attenuate);
- deliver == 1: mono via ``rx_audio_channel`` (mix/left/right keep their
  MOR-508 meaning; N == 2 stays byte-identical to the historical behavior,
  pinned unchanged by the MOR-504/508/578 suites);
- deliver == 2 and N > 2: keep channels 0 and 1 interleaved, drop the rest.

No radio-specific branch: the seam tests drive the generic driver config with
the two rig-config shapes (mono deliver = the PCM_1CH radios, stereo deliver =
the PCM_2CH radios) against 32- and 16-channel devices, plus a regression
through the real ``/api/v1/audio`` WebSocket on two radio models that asserts
the negotiated mono format and the exact downmixed payload.
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import asdict
from typing import Any

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_web_server import (
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio import usb_driver
from rigplane.audio._portaudio_enum import (
    HostApiRecord,
    PortAudioDeviceRecord,
    read_portaudio_enumeration,
)
from rigplane.audio.backend import (
    AudioDeviceConfig,
    PortAudioBackend,
    _RxFramer,
)
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.core.types import AudioCodec
from rigplane.profiles import get_radio_profile
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT


def _nchannel_block(open_channels: int, count: int, *, start: int = 0) -> bytes:
    """Interleaved s16le block: ``count`` frames of ``open_channels`` channels.

    Channel 0 = ``1000 + t`` and channel 1 = ``2000 + t`` (a ramp, so dropped,
    duplicated or swapped samples are caught); channel k >= 2 = ``1000 * (k+1)``
    constant (a distinct known value per channel, so an implementation that
    averages or keeps the wrong channels is caught).
    """
    out = bytearray()
    for t in range(start, start + count):
        for ch in range(open_channels):
            value = 1000 * (ch + 1) + t if ch < 2 else 1000 * (ch + 1)
            out += struct.pack("<h", value)
    return bytes(out)


def _mono_frame(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


# ---------------------------------------------------------------------------
# Framer unit: N-channel open -> delivered layout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("open_channels", [3, 6, 16, 32])
def test_framer_downmixes_n_channels_to_mono(open_channels: int) -> None:
    """deliver=1: mix/left/right collapse device channels 0 and 1 of N.

    One 960-frame N-channel block (20 ms @ 48 kHz) must deliver exactly one
    1920-byte mono fixed frame built from channels 0/1 only — channels 2..N-1
    dropped, never averaged in.
    """
    for channel, base in (("mix", 1500), ("left", 1000), ("right", 2000)):
        framer = _RxFramer(
            config=AudioDeviceConfig(
                sample_rate=48_000,
                channels=open_channels,
                frame_ms=20,
                rx_audio_channel=channel,
            ),
            deliver_channels=1,
        )
        assert framer.frame_bytes == 1920
        frames: list[bytes] = []
        framer.feed(_nchannel_block(open_channels, 960), frames.append)
        expected = _mono_frame([base + t for t in range(960)])
        assert frames == [expected]


def test_framer_downmixes_16_channels_to_stereo_deliver() -> None:
    """deliver=2 on N=16: keep device channels 0 and 1 interleaved, drop rest."""
    framer = _RxFramer(
        config=AudioDeviceConfig(sample_rate=48_000, channels=16, frame_ms=20),
        deliver_channels=2,
    )
    assert framer.frame_bytes == 3840
    frames: list[bytes] = []
    framer.feed(_nchannel_block(16, 960), frames.append)
    expected = b"".join(struct.pack("<hh", 1000 + t, 2000 + t) for t in range(960))
    assert len(expected) == 3840
    assert frames == [expected]


def test_framer_rechunks_after_downmix_across_odd_blocks() -> None:
    """N-channel blocks that do not divide a 20 ms frame still yield whole ones.

    The downmix collapses each block first; the accumulator then re-chunks the
    delivered-layout stream across feeds, so 500 + 460 frames of 32-channel
    audio deliver exactly one whole 960-sample mono frame with a continuous
    ramp (no dropped or duplicated samples at the block seam).
    """
    framer = _RxFramer(
        config=AudioDeviceConfig(sample_rate=48_000, channels=32, frame_ms=20),
        deliver_channels=1,
    )
    frames: list[bytes] = []
    framer.feed(_nchannel_block(32, 500), frames.append)
    assert frames == []  # 500 mono samples < 960 — not a whole frame yet
    framer.feed(_nchannel_block(32, 460, start=500), frames.append)
    expected = _mono_frame([1500 + t for t in range(960)])
    assert frames == [expected]


def test_downmix_drops_trailing_partial_n_channel_frame() -> None:
    """Trailing bytes that do not form a whole N-channel frame are dropped."""
    from rigplane.audio.backend import _downmix_stereo_to_mono_s16le

    # N=3: one whole frame (ch0=10, ch1=20, ch2=3000) + 2 dangling samples.
    pcm = struct.pack("<hhh", 10, 20, 3000) + b"\x01\x02"
    mono = _downmix_stereo_to_mono_s16le(pcm, open_channels=3)
    assert mono == struct.pack("<h", (10 + 20) // 2)


# ---------------------------------------------------------------------------
# Seam: UsbAudioDriver -> PortAudioBackend -> fake sounddevice
# ---------------------------------------------------------------------------


class _FakeIndata:
    """Minimal stand-in for the numpy capture buffer PortAudio hands over."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self) -> bytes:
        return self._payload


@pytest.fixture(autouse=True)
def _no_coreaudio_uid_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep device enumeration off real CoreAudio (macOS test hosts)."""
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})


def _device_record(
    index: int, name: str, *, inputs: int, outputs: int
) -> PortAudioDeviceRecord:
    """One typed enumeration record for the fake sounddevice."""
    return PortAudioDeviceRecord(
        index=index,
        name=name,
        hostapi=0,
        max_input_channels=inputs,
        max_output_channels=outputs,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        default_high_input_latency=0.1,
        default_high_output_latency=0.1,
    )


def _fake_sd_builder(max_input_channels: int) -> tuple[type, dict[str, Any]]:
    """A fake sounddevice module + the dict its InputStream opens record.

    The ``query_devices()`` dicts are not handwritten: each is
    ``dataclasses.asdict()`` of the typed producer contract
    :class:`PortAudioDeviceRecord` — the same serialization the backend's
    ``list_devices()`` reads and ``read_portaudio_enumeration`` parses —
    so the seam tests cannot drift from the real enumeration shape.
    """
    records = [
        _device_record(0, "pulse", inputs=max_input_channels, outputs=0),
        _device_record(1, "pulse-out", inputs=0, outputs=2),
    ]
    devices = [asdict(record) for record in records]
    # The host API reply is derived from the typed producer contract too:
    # sounddevice reports a "devices" index list where HostApiRecord keeps
    # the count, and the fake is checked back through the real parser below.
    host_record = HostApiRecord(
        index=0,
        name="ALSA",
        device_count=len(records),
        default_input=0,
        default_output=1,
    )
    hostapis = [
        {
            "name": host_record.name,
            "devices": list(range(host_record.device_count)),
            "default_input_device": host_record.default_input,
            "default_output_device": host_record.default_output,
        }
    ]
    captured: dict[str, Any] = {}

    class FakeInputStream:
        def __init__(self, **kw: Any) -> None:
            captured["channels"] = kw["channels"]
            captured["callback"] = kw["callback"]

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeSd:
        InputStream = FakeInputStream

        @staticmethod
        def query_devices(index: int | None = None) -> Any:
            return devices if index is None else devices[index]

        @staticmethod
        def query_hostapis(index: int | None = None) -> Any:
            return hostapis if index is None else hostapis[index]

        @staticmethod
        def check_input_settings(**_kw: Any) -> None:
            return None

        @staticmethod
        def check_output_settings(**_kw: Any) -> None:
            return None

    enumeration = read_portaudio_enumeration(FakeSd)
    assert enumeration.hostapis == (host_record,)
    assert enumeration.devices == tuple(records)
    return FakeSd, captured


@pytest.mark.asyncio
async def test_driver_mono_request_on_32ch_device_delivers_mono_frames() -> None:
    """Seam: mono deliver (PCM_1CH rig shape) on a 32-channel device.

    The driver must open the InputStream at the device-native 32 ch and the
    consumer must receive correct 960-sample mono 20 ms frames downmixed from
    device channels 0/1 — never the raw 32-channel interleaved bytes.
    """
    fake_sd, captured = _fake_sd_builder(32)
    backend = PortAudioBackend(dependency_loader=lambda: (fake_sd, object()))
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    received: list[bytes] = []

    await driver.start_rx(received.append, channels=1)

    assert captured["channels"] == 32  # opened at the device-native count
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    assert contract.rx.channels == 1
    assert contract.rx.open_channels == 32
    assert contract.rx.effective_open_channels == 32
    assert contract.rx.channel_source == "device-native"

    cb = captured["callback"]
    assert callable(cb)
    cb(_FakeIndata(_nchannel_block(32, 960)), 960, None, None)  # type: ignore[operator]

    expected = _mono_frame([1500 + t for t in range(960)])
    assert len(expected) == 1920  # mono fixed frame, not 61440 raw bytes
    assert received == [expected]

    await driver.stop_rx()


@pytest.mark.asyncio
async def test_driver_stereo_request_on_16ch_device_delivers_first_two() -> None:
    """Seam: stereo deliver (PCM_2CH rig shape) on a 16-channel device.

    The consumer must receive 20 ms frames with device channels 0 and 1
    interleaved (3840 bytes), with channels 2..15 dropped.
    """
    fake_sd, captured = _fake_sd_builder(16)
    backend = PortAudioBackend(dependency_loader=lambda: (fake_sd, object()))
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    received: list[bytes] = []

    await driver.start_rx(received.append, channels=2)

    assert captured["channels"] == 16
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    assert contract.rx.channels == 2
    assert contract.rx.open_channels == 16
    assert contract.rx.effective_open_channels == 16

    cb = captured["callback"]
    assert callable(cb)
    cb(_FakeIndata(_nchannel_block(16, 960)), 960, None, None)  # type: ignore[operator]

    expected = b"".join(struct.pack("<hh", 1000 + t, 2000 + t) for t in range(960))
    assert len(expected) == 3840
    assert received == [expected]

    await driver.stop_rx()


# ---------------------------------------------------------------------------
# WebSocket regression: radio -> audio session -> real /api/v1/audio -> client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("radio_cls", "model", "model_id"),
    [(Ic7300SerialRadio, "IC-7300", 0x94), (Ic705SerialRadio, "IC-705", 0xA4)],
    ids=["ic7300", "ic705"],
)
@pytest.mark.parametrize(
    "open_channels", [32, 16], ids=["pulse-32ch", "blackhole-16ch"]
)
@pytest.mark.asyncio
async def test_rx_nchannel_downmix_reaches_browser_over_audio_websocket(
    monkeypatch: pytest.MonkeyPatch,
    radio_cls: type,
    model: str,
    model_id: int,
    open_channels: int,
) -> None:
    """User-visible MOR-4029 path: a 32/16-channel capture downmixed to the browser.

    Only ``sounddevice`` is faked (its enumeration comes from
    :class:`PortAudioDeviceRecord`, as above); the radio, driver, real
    ``PortAudioBackend`` framer, audio session, and the ``/api/v1/audio``
    WebSocket handler are all real. The client sends ``audio_start``, the
    server negotiates mono PCM16 @ 48 kHz, and the test drives the fake
    InputStream callback with one N-channel interleaved block carrying
    identifiable per-channel values, then asserts the AUDIO_RX payload is the
    exact mono downmix of device channels 0/1.
    """
    fake_sd, captured = _fake_sd_builder(open_channels)
    backend = PortAudioBackend(dependency_loader=lambda: (fake_sd, object()))
    monkeypatch.setattr(
        usb_driver, "PortAudioBackend", lambda *args, **kwargs: backend, raising=True
    )
    radio = radio_cls(
        device="COM3",
        profile=get_radio_profile(model),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        rx_device="0",
        tx_device="1",
        civ_link=_FakeSerialCivLink(model_id=model_id),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        # Bind the real HTTP/WS dispatcher without the unrelated CAT poller.
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        audio_format = None
        async with asyncio.timeout(5):
            while audio_format is None:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                if message["type"] == "audio_format":
                    audio_format = message
        assert audio_format == {
            "type": "audio_format",
            "codec": "pcm16",
            "sample_rate": 48_000,
            "channels": 1,
            "frame_ms": 20,
        }

        driver = radio._serial_audio_driver
        assert captured["channels"] == open_channels  # opened device-native
        contract = driver.usb_audio_contract
        assert contract is not None and contract.rx is not None
        assert contract.rx.channels == 1
        assert contract.rx.open_channels == open_channels

        cb = captured["callback"]
        assert callable(cb)
        cb(  # type: ignore[operator]
            _FakeIndata(_nchannel_block(open_channels, 960)), 960, None, None
        )

        opcode, frame = await _ws_recv_frame(reader, timeout=5)
        assert opcode == WS_OP_BINARY
        assert frame[:2] == bytes([MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16])
        expected = _mono_frame([1500 + t for t in range(960)])
        assert frame[AUDIO_HEADER_SIZE:] == expected
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()
