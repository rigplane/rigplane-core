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
    assert config.usb_identity.serial_functions == tuple(
        UsbFunctionId(*function) for function in serial_functions
    )
    assert config.to_profile().usb_identity == config.usb_identity


def test_ic7610_declares_two_identical_serial_functions():
    config = load_rig(Path("rigs/ic7610.toml"))
    assert config.usb_identity.audio_codec == frozenset()
    assert config.usb_identity.serial_functions == (
        UsbFunctionId("10C4", "EA60"),
        UsbFunctionId("10C4", "EA60"),
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


@pytest.mark.parametrize("rig", ["ic9700", "ic7300mk2", "x6200"])
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
    _assert_invalid_usb_table(tmp_path, f"[usb]\n{key} = {value}")


@pytest.mark.parametrize("table", ["[usb]\nunknown = []", "usb = []"])
def test_invalid_usb_table_is_rejected(tmp_path, table):
    _assert_invalid_usb_table(tmp_path, table)


def _write_tmp_rig(tmp_path, table, name):
    # ic9700 references the shared CTCSS catalog, so copy it next to the
    # synthetic rig file before loading.
    (tmp_path / "_ctcss_tables_v1.toml").write_bytes(
        Path("rigs/_ctcss_tables_v1.toml").read_bytes()
    )
    path = tmp_path / name
    # ic9700 carries no [usb] section, so the prepended table is the only one.
    path.write_text(table + "\n" + Path("rigs/ic9700.toml").read_text())
    return path


def _assert_invalid_usb_table(tmp_path, table):
    path = _write_tmp_rig(tmp_path, table, "usb.toml")
    with pytest.raises(RigLoadError, match="usb"):
        load_rig(path)


def test_empty_optional_codec_list_and_table(tmp_path):
    for table in ("[usb]", "[usb]\naudio_codec = []", "[usb]\nserial_functions = []"):
        path = _write_tmp_rig(tmp_path, table, "empty.toml")
        config = load_rig(path)
        assert config.usb_identity == UsbAudioIdentity(frozenset(), ())


def test_serial_functions_keep_multiplicity_while_codec_deduplicates(tmp_path):
    path = _write_tmp_rig(
        tmp_path,
        '[usb]\naudio_codec = [{vid="08BB", pid="2901"}, {vid="08BB", pid="2901"}]\n'
        'serial_functions = [{vid="10C4", pid="EA70"}, '
        '{vid="10C4", pid="EA70"}]',
        "serial-functions.toml",
    )
    identity = load_rig(path).usb_identity
    assert identity.audio_codec == frozenset({UsbFunctionId("08BB", "2901")})
    assert identity.serial_functions == (
        UsbFunctionId("10C4", "EA70"),
        UsbFunctionId("10C4", "EA70"),
    )


@pytest.mark.parametrize(
    "vid,pid", [("08bb", "2901"), ("08BB", "xyz0"), ("8BB", "2901"), (2235, "2901")]
)
def test_usb_function_ids_require_four_uppercase_hex_digits(vid, pid):
    with pytest.raises(ValueError):
        UsbFunctionId(vid, pid)
