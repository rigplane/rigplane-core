"""IC-7300 air capture driver: the write gate and a rehearsal on the fake."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from rigplane_testkit.serial import CivSerialFake

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = REPO_ROOT / "scripts" / "ic7300_capture" / "air_capture.py"
    spec = importlib.util.spec_from_file_location("ic7300_air_capture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


air = _load()


@pytest.mark.parametrize(
    "body",
    [
        "050000074100",  # 14.074 MHz... as 5 BCD bytes
        "0601",  # USB
        "060301",  # CW, filter 1
        "26000100" + "01",  # selected VFO: USB, data on, filter
        "1a060101",  # data mode on, filter 1
        "2715000000500000",  # scope span
        "271400",  # scope mode centre
        "160202",  # preamp 2
        "160200",  # preamp off
    ],
)
def test_tuning_writes_are_allowed(body: str) -> None:
    assert air.write_allowed(bytes.fromhex(body)), body


@pytest.mark.parametrize(
    "body",
    [
        "1c0001",  # PTT
        "1c0101",  # tuner
        "180001",  # power
        "17ff",  # CW stop / send
        "0e01",  # scan
        "07d1",  # VFO select
        "050000000000" + "00",  # frequency with a stray byte
        "05ffffffffff",  # not BCD
        "050000000000",  # 0 Hz: outside the HF/6 m range
        "0610",  # not a mode
        "1a0500010099",  # menu write
        "1a060201",  # data-mode value out of range
        "14010128",  # AF level
    ],
)
def test_everything_else_is_refused(body: str) -> None:
    assert not air.write_allowed(bytes.fromhex(body)), body


class _StubAudio:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> dict:
        self.stopped = True
        self.path.write_bytes(b"RIFF")
        return {"format": "stub"}


async def test_clip_on_the_fake_tunes_records_and_restores(tmp_path) -> None:
    fake = CivSerialFake(echo=False, frequency_hz=7_100_000, mode=0x00)
    args = argparse.Namespace(
        clip="ft8",
        device="fake-tty",
        out_dir=str(tmp_path),
        baud=115200,
        radio_addr=0x94,
        freq_hz=14_074_000,
        mode="USB",
        data=False,
        seconds=0.3,
        slot_s=0,
        start_at=None,
        preamp=None,
        scope_span=None,
        no_scope=True,
        no_audio=False,
    )
    audios: list[_StubAudio] = []

    def factory(path: Path) -> _StubAudio:
        audios.append(_StubAudio(path))
        return audios[-1]

    code = await air.run_clip(args, opener=fake.opener, audio_factory=factory)
    assert code == 0
    assert fake.frequency_hz == 7_100_000 and fake.mode == 0x00  # restored
    assert audios[0].started and audios[0].stopped
    manifest = json.loads(next(tmp_path.glob("*.manifest.json")).read_text())
    assert manifest["restored_identical"] is True
    assert manifest["state_before"]["freq_hz"] == 7_100_000
    assert manifest["state_tuned"]["freq_hz"] == 14_074_000
    bodies = [w["body"] for w in manifest["writes_through_bridge"]]
    assert any(b.startswith("05") for b in bodies)  # tune
    assert manifest["files"].keys() >= {audios[0].path.name}


async def test_start_at_overrides_the_slot_wait_and_is_recorded(tmp_path) -> None:
    """``--start-at`` must set the audio start, not the next 15 s slot boundary."""
    fake = CivSerialFake(echo=False, frequency_hz=7_100_000, mode=0x00)
    target = datetime.now(timezone.utc) + timedelta(seconds=2.5)
    args = argparse.Namespace(
        clip="ft8",
        device="fake-tty",
        out_dir=str(tmp_path),
        baud=115200,
        radio_addr=0x94,
        freq_hz=14_074_000,
        mode="USB",
        data=False,
        seconds=0.2,
        slot_s=15,  # would wait for the next :00/:15/:30/:45 without start_at
        start_at=target.isoformat(),
        preamp=None,
        scope_span=None,
        no_scope=True,
        no_audio=False,
    )
    audios: list[_StubAudio] = []

    def factory(path: Path) -> _StubAudio:
        audios.append(_StubAudio(path))
        return audios[-1]

    assert await air.run_clip(args, opener=fake.opener, audio_factory=factory) == 0
    manifest = json.loads(next(tmp_path.glob("*.manifest.json")).read_text())
    started = datetime.fromisoformat(manifest["slot_start_wall_utc"])
    assert abs((started - target).total_seconds()) < 0.01


class _FlakyRadio:
    """Records the restore calls; the preamp restore fails."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def set_preamp(self, level):
        self.calls.append("preamp")
        raise TimeoutError("CI-V response timed out")

    async def set_mode(self, mode, filt):
        self.calls.append("mode")

    async def set_data_mode(self, on):
        self.calls.append("data_mode")

    async def set_freq(self, hz):
        self.calls.append("freq")


async def test_a_failed_restore_step_does_not_stop_the_others() -> None:
    radio = _FlakyRadio()
    before = {"freq_hz": 14_263_000, "mode": "USB", "filter": 2, "data_mode": False}
    errors = await air._restore(radio, before, tuned_preamp=0)
    assert radio.calls == ["preamp", "mode", "data_mode", "freq"]
    assert len(errors) == 1 and errors[0].startswith("preamp: TimeoutError")
