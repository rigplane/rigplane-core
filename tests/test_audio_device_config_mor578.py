"""MOR-578 — ``AudioDeviceConfig`` carrier replaces per-keyword audio plumbing.

Falsification pins for a behavior byte-identical refactor: per-device audio
config (``rx_device``/``tx_device``/``sample_rate``/``channels``/``frame_ms``/
``rx_audio_channel``) used to travel as six separate keyword parameters
loader → backend ctor → ``UsbAudioDriver`` → ``AudioBackend.open_rx`` →
``_RxFramer`` → downmix. The carrier bundles them into ONE frozen dataclass.

The pins below were captured by RUNNING the pre-refactor per-keyword path
(origin/main @ 3f08f659) and hardcoding its effective arguments, so they fail
on any drift — not just on config/kwargs divergence:

- the config-built driver must produce the SAME effective ``open_rx`` /
  ``open_tx`` arguments as the per-keyword driver for IC-7610-shaped (mix,
  2 ch), X6200-shaped (mix, channel clamp MOR-238) and FTX-1-shaped (left,
  under-request downmix MOR-504/508) scenarios;
- the stereo→mono downmix (``mix``/``left``/``right``) through ``_RxFramer``
  is byte-identical;
- the rig profiles keep pinning the loader leg (``rigs/*.toml [audio]``);
- the production backend ctors thread ONE carrier into the driver.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable

import pytest

from rigplane.audio.backend import (
    AudioDeviceConfig,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
    FakeRxStream,
    FakeTxStream,
    _RxFramer,
)
from rigplane.audio.usb_driver import UsbAudioDriver

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingBackend(FakeAudioBackend):
    """FakeAudioBackend that records effective open_rx/open_tx arguments."""

    def __init__(self, devices: list[AudioDeviceInfo]) -> None:
        super().__init__(devices)
        self.rx_calls: list[dict[str, object]] = []
        self.tx_calls: list[dict[str, object]] = []

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> FakeRxStream:
        self.rx_calls.append(
            {
                "device": int(device),
                "sample_rate": sample_rate,
                "channels": channels,
                "frame_ms": frame_ms,
                "deliver_channels": deliver_channels,
                "rx_audio_channel": rx_audio_channel,
            }
        )
        return super().open_rx(
            device,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
            deliver_channels=deliver_channels,
            rx_audio_channel=rx_audio_channel,
        )

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> FakeTxStream:
        self.tx_calls.append(
            {
                "device": int(device),
                "sample_rate": sample_rate,
                "channels": channels,
                "frame_ms": frame_ms,
            }
        )
        return super().open_tx(
            device,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )


def _stereo_codec(index: int = 7) -> AudioDeviceInfo:
    """Stereo-native duplex USB CODEC (IC-7610 / FTX-1 shape)."""
    return AudioDeviceInfo(
        id=AudioDeviceId(index),
        name="USB Audio CODEC",
        input_channels=2,
        output_channels=2,
    )


def _mono_capture_codec(index: int = 4) -> AudioDeviceInfo:
    """Mono-capture duplex USB CODEC (X6200 channel-clamp shape, MOR-238)."""
    return AudioDeviceInfo(
        id=AudioDeviceId(index),
        name="USB Audio CODEC",
        input_channels=1,
        output_channels=2,
    )


async def _effective_open_args(
    driver: UsbAudioDriver,
    backend: _RecordingBackend,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    await driver.start_rx(lambda _frame: None)
    await driver.start_tx()
    return backend.rx_calls, backend.tx_calls


# ---------------------------------------------------------------------------
# Carrier shape: fields + defaults mirror the per-keyword plumbing 1:1
# ---------------------------------------------------------------------------


def test_config_defaults_mirror_per_keyword_defaults() -> None:
    """Default carrier == the historical per-keyword defaults, frozen."""
    config = AudioDeviceConfig()
    assert config.rx_device is None
    assert config.tx_device is None
    assert config.sample_rate == 48_000
    assert config.channels == 1
    assert config.frame_ms == 20
    assert config.rx_audio_channel == "mix"
    with pytest.raises(AttributeError):
        config.sample_rate = 24_000  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Loader leg: rigs/*.toml [audio] still pins the per-rig channel selector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rig_file", "expected_channel"),
    [
        ("ic7610.toml", "mix"),
        ("x6200.toml", "mix"),
        ("ftx1.toml", "left"),
    ],
)
def test_rig_profiles_pin_rx_audio_channel(
    rig_file: str,
    expected_channel: str,
) -> None:
    from rigplane.profiles.rig_loader import load_rig

    rig = load_rig(RIGS_DIR / rig_file)
    assert rig.rx_audio_channel == expected_channel


# ---------------------------------------------------------------------------
# Same-args pin: config path == per-keyword path == pre-refactor behavior
# ---------------------------------------------------------------------------

# Effective arguments captured by RUNNING the per-keyword path BEFORE the
# refactor (origin/main @ 3f08f659). Any drift fails the pin.
_SCENARIOS: list[
    tuple[
        str,
        Callable[[], AudioDeviceInfo],
        AudioDeviceConfig,
        dict[str, object],
        dict[str, object],
    ]
] = [
    (
        # IC-7610 serial: default 2-ch LPCM codec, default "mix".
        "ic7610-mix",
        _stereo_codec,
        AudioDeviceConfig(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
        ),
        {
            "device": 7,
            "sample_rate": 48_000,
            "channels": 2,
            "frame_ms": 20,
            "deliver_channels": 2,
            "rx_audio_channel": "mix",
        },
        {"device": 7, "sample_rate": 48_000, "channels": 2, "frame_ms": 20},
    ),
    (
        # X6200: 2-ch request on a mono capture endpoint clamps (MOR-238).
        "x6200-mix-clamp",
        _mono_capture_codec,
        AudioDeviceConfig(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
        ),
        {
            "device": 4,
            "sample_rate": 48_000,
            "channels": 1,
            "frame_ms": 20,
            "deliver_channels": 1,
            "rx_audio_channel": "mix",
        },
        {"device": 4, "sample_rate": 48_000, "channels": 2, "frame_ms": 20},
    ),
    (
        # FTX-1: mono request on a stereo-native device opens native and
        # downmixes (MOR-504); profile selects the LEFT channel (MOR-508).
        "ftx1-left-downmix",
        _stereo_codec,
        AudioDeviceConfig(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            sample_rate=48_000,
            channels=1,
            frame_ms=20,
            rx_audio_channel="left",
        ),
        {
            "device": 7,
            "sample_rate": 48_000,
            "channels": 2,
            "frame_ms": 20,
            "deliver_channels": 1,
            "rx_audio_channel": "left",
        },
        {"device": 7, "sample_rate": 48_000, "channels": 1, "frame_ms": 20},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "make_device", "expected_rx", "expected_tx"),
    [(cfg, dev, rx, tx) for _, dev, cfg, rx, tx in _SCENARIOS],
    ids=[name for name, _, _, _, _ in _SCENARIOS],
)
async def test_config_path_produces_identical_effective_open_args(
    config: AudioDeviceConfig,
    make_device: Callable[[], AudioDeviceInfo],
    expected_rx: dict[str, object],
    expected_tx: dict[str, object],
) -> None:
    """Config-built and per-keyword drivers reach open_rx/open_tx identically."""
    kwargs_backend = _RecordingBackend([make_device()])
    kwargs_driver = UsbAudioDriver(
        rx_device=config.rx_device,
        tx_device=config.tx_device,
        sample_rate=config.sample_rate,
        channels=config.channels,
        frame_ms=config.frame_ms,
        rx_audio_channel=config.rx_audio_channel,
        backend=kwargs_backend,
    )
    kwargs_rx, kwargs_tx = await _effective_open_args(kwargs_driver, kwargs_backend)

    config_backend = _RecordingBackend([make_device()])
    config_driver = UsbAudioDriver(config, backend=config_backend)
    config_rx, config_tx = await _effective_open_args(config_driver, config_backend)

    # The two construction paths are indistinguishable at the backend seam...
    assert config_rx == kwargs_rx
    assert config_tx == kwargs_tx
    # ...and both match the pre-refactor per-keyword behavior exactly.
    assert config_rx == [expected_rx]
    assert config_tx == [expected_tx]


# ---------------------------------------------------------------------------
# Downmix pin: _RxFramer reads fields off the carrier, bytes unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "expected_sample"),
    [("mix", 1500), ("left", 1000), ("right", 2000)],
)
def test_framer_downmix_off_config_is_byte_identical(
    channel: str,
    expected_sample: int,
) -> None:
    """One 20 ms stereo block (L=1000, R=2000) → the pre-refactor mono frame.

    Pre-refactor values: mix → (1000+2000)//2 == 1500, left → 1000,
    right → 2000; one 1920-byte mono fixed frame at 48 kHz / 20 ms.
    """
    framer = _RxFramer(
        config=AudioDeviceConfig(
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
            rx_audio_channel=channel,
        ),
        deliver_channels=1,
    )
    assert framer.frame_bytes == 1920

    frames: list[bytes] = []
    framer.feed(struct.pack("<hh", 1000, 2000) * 960, frames.append)
    assert frames == [struct.pack("<h", expected_sample) * 960]


# ---------------------------------------------------------------------------
# Backend ctor leg: production radios thread ONE carrier into the driver
# ---------------------------------------------------------------------------


def test_icom_serial_ctor_builds_single_config_carrier() -> None:
    """IC-7610 serial backend hands the driver ONE AudioDeviceConfig.

    Pre-refactor keywords: sample_rate=48000 (default), channels=2 (default
    2-ch LPCM codec), frame_ms=20, no device overrides, default "mix".
    """
    from rigplane.backends.icom7610 import Icom7610SerialRadio

    radio = Icom7610SerialRadio(device="/dev/ttyUSB-test")
    assert radio._serial_audio_driver._config == AudioDeviceConfig(
        sample_rate=48_000,
        channels=2,
        frame_ms=20,
    )


def test_yaesu_ctor_threads_profile_channel_into_config_carrier() -> None:
    """FTX-1 backend threads [audio].rx_audio_channel="left" via the carrier."""
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    radio = YaesuCatRadio(device="/dev/cu.fake-ftx1", profile="ftx1")
    assert radio._audio_driver._config == AudioDeviceConfig(
        sample_rate=48_000,
        channels=1,
        frame_ms=20,
        rx_audio_channel="left",
    )
