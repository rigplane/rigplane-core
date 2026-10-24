"""Optional capture-backed USB codec data in rig profiles."""

from pathlib import Path

import pytest

from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId
from rigplane.profiles.rig_loader import RigLoadError, load_rig


@pytest.mark.parametrize(
    "rig,vid,pid",
    [("ic7300", "08BB", "2901"), ("ic705", "08BB", "2901"), ("ftx1", "0D8C", "0016")],
)
def test_recorded_usb_codec_loads_and_reaches_profile(rig, vid, pid):
    config = load_rig(Path("rigs") / f"{rig}.toml")
    assert config.usb_identity == UsbAudioIdentity(frozenset({UsbFunctionId(vid, pid)}))
    assert config.to_profile().usb_identity == config.usb_identity


@pytest.mark.parametrize("rig", ["ic7610", "ic9700", "ic7300mk2", "x6200"])
def test_unrecorded_profile_has_no_usb_identity(rig):
    config = load_rig(Path("rigs") / f"{rig}.toml")
    assert config.usb_identity is None
    assert config.to_profile().usb_identity is None


@pytest.mark.parametrize(
    "table",
    [
        "[usb]\nunknown = []",
        '[usb]\naudio_codec = [{vid="08BB", pid="2901", extra=1}]',
        '[usb]\naudio_codec = "08BB:2901"',
        '[usb]\naudio_codec = [{vid="08BB"}]',
        '[usb]\naudio_codec = [{vid=2235, pid="2901"}]',
        '[usb]\naudio_codec = [{vid="08bb", pid="2901"}]',
        "usb = []",
    ],
)
def test_invalid_usb_table_is_rejected(tmp_path, table):
    source = Path("rigs/ic7610.toml").read_text()
    path = tmp_path / "usb.toml"
    path.write_text(table + "\n" + source)
    with pytest.raises(RigLoadError, match="usb"):
        load_rig(path)


def test_empty_optional_codec_list_and_table(tmp_path):
    for table in ("[usb]", "[usb]\naudio_codec = []"):
        path = tmp_path / "empty.toml"
        path.write_text(table + "\n" + Path("rigs/ic7610.toml").read_text())
        config = load_rig(path)
        assert config.usb_identity == UsbAudioIdentity(frozenset())


@pytest.mark.parametrize(
    "vid,pid", [("08bb", "2901"), ("08BB", "xyz0"), ("8BB", "2901"), (2235, "2901")]
)
def test_usb_function_ids_require_four_uppercase_hex_digits(vid, pid):
    with pytest.raises(ValueError):
        UsbFunctionId(vid, pid)
