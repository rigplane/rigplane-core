"""Optional capture-backed USB codec data in rig profiles."""

from pathlib import Path

import pytest

from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId
from rigplane.profiles.rig_loader import RigLoadError, load_rig


@pytest.mark.parametrize(
    "rig,vid,pid,serial_functions",
    [
        ("ic7300", "08BB", "2901", ()),
        ("ic705", "08BB", "2901", ()),
        ("ftx1", "0D8C", "0016", (("10C4", "EA70"), ("26AA", "0030"))),
    ],
)
def test_recorded_usb_codec_loads_and_reaches_profile(rig, vid, pid, serial_functions):
    config = load_rig(Path("rigs") / f"{rig}.toml")
    assert config.usb_identity.audio_codec == frozenset({UsbFunctionId(vid, pid)})
    assert config.usb_identity.serial_functions == frozenset(
        UsbFunctionId(*function) for function in serial_functions
    )
    assert config.to_profile().usb_identity == config.usb_identity


@pytest.mark.parametrize("rig", ["ic7300", "ic705", "ic7610", "ftx1"])
def test_serial_backend_passes_profile_identity_to_audio_driver(monkeypatch, rig):
    from rigplane.audio.usb_driver import UsbAudioDriver
    from rigplane.audio.backend import FakeAudioBackend
    from rigplane.backends.ic7300 import Ic7300SerialRadio
    from rigplane.backends.ic705 import Ic705SerialRadio
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    monkeypatch.setattr("rigplane.audio.usb_driver.PortAudioBackend", FakeAudioBackend)
    factory = {
        "ic7300": Ic7300SerialRadio,
        "ic705": Ic705SerialRadio,
        "ic7610": Icom7610SerialRadio,
        "ftx1": YaesuCatRadio,
    }[rig]
    radio = factory(device="COM3")
    driver = radio._audio_driver if rig == "ftx1" else radio._serial_audio_driver
    assert isinstance(driver, UsbAudioDriver)
    assert driver._usb_identity == load_rig(Path("rigs") / f"{rig}.toml").usb_identity


@pytest.mark.parametrize("rig", ["ic7610", "ic9700", "ic7300mk2", "x6200"])
def test_unrecorded_profile_has_no_usb_identity(rig):
    config = load_rig(Path("rigs") / f"{rig}.toml")
    assert config.usb_identity is None
    assert config.to_profile().usb_identity is None


@pytest.mark.parametrize("key", ["audio_codec", "serial_functions"])
@pytest.mark.parametrize(
    "value",
    [
        '[{vid="08BB", pid="2901", extra=1}]',
        '"08BB:2901"',
        '["08BB:2901"]',
        '[{vid="08BB"}]',
        '[{vid=2235, pid="2901"}]',
        '[{vid="08bb", pid="2901"}]',
        '[{vid="08BB", pid="xyz0"}]',
    ],
)
def test_invalid_usb_function_lists_are_rejected(tmp_path, key, value):
    test_invalid_usb_table_is_rejected(tmp_path, f"[usb]\n{key} = {value}")


@pytest.mark.parametrize("table", ["[usb]\nunknown = []", "usb = []"])
def test_invalid_usb_table_is_rejected(tmp_path, table):
    source = Path("rigs/ic7610.toml").read_text()
    path = tmp_path / "usb.toml"
    path.write_text(table + "\n" + source)
    with pytest.raises(RigLoadError, match="usb"):
        load_rig(path)


def test_empty_optional_codec_list_and_table(tmp_path):
    for table in ("[usb]", "[usb]\naudio_codec = []", "[usb]\nserial_functions = []"):
        path = tmp_path / "empty.toml"
        path.write_text(table + "\n" + Path("rigs/ic7610.toml").read_text())
        config = load_rig(path)
        assert config.usb_identity == UsbAudioIdentity(frozenset())


def test_serial_functions_can_be_loaded_without_a_codec_and_deduplicate(tmp_path):
    path = tmp_path / "serial-functions.toml"
    path.write_text(
        '[usb]\nserial_functions = [{vid="10C4", pid="EA70"}, '
        '{vid="10C4", pid="EA70"}]\n' + Path("rigs/ic7610.toml").read_text()
    )
    identity = load_rig(path).usb_identity
    assert identity.audio_codec == frozenset()
    assert identity.serial_functions == frozenset({UsbFunctionId("10C4", "EA70")})


@pytest.mark.parametrize(
    "vid,pid", [("08bb", "2901"), ("08BB", "xyz0"), ("8BB", "2901"), (2235, "2901")]
)
def test_usb_function_ids_require_four_uppercase_hex_digits(vid, pid):
    with pytest.raises(ValueError):
        UsbFunctionId(vid, pid)
