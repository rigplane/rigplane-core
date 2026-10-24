import logging
import platform
import subprocess
from unittest.mock import Mock

import pytest

from rigplane.audio import _usb_raw_windows as raw
from rigplane.audio._windows_devnode import DevNodeRecord


@pytest.fixture(autouse=True)
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")


def test_cfgmgr32_success_never_starts_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = DevNodeRecord("USB\\device", "parent", "USB", "Hub", "desc", (), ())
    monkeypatch.setattr(raw, "present_devnode_records", lambda: [record])
    run = Mock(side_effect=AssertionError("PowerShell must not run"))
    monkeypatch.setattr(raw.subprocess, "run", run)
    result = raw.read_raw_windows(4.0)
    assert result == raw.RawWindows("cfgmgr32", (record,))
    assert raw.RawWindows.from_json_dict(result.to_json_dict()) == result
    run.assert_not_called()


def test_fallback_warns_and_preserves_all_seven_fields(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        raw, "present_devnode_records", Mock(side_effect=OSError("broken"))
    )
    stdout = (
        "USB\\VID_0451&PID_2046\\6&hub\tROOT\tUSB\tGeneric USB Hub\tHub description\t"
        "USB\\VID_0451&PID_2046|USB\\VID_0451&PID_2046&REV_0125\tUSB\\USB20_HUB\r\n"
        "PCI\\controller\t\tUSB\t\tController description\tPCI\\controller\t\r\n"
    ).encode("utf-8")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout, b""))
    monkeypatch.setattr(raw.subprocess, "run", run)
    with caplog.at_level(logging.WARNING):
        result = raw.read_raw_windows(4.0)
    assert result.source == "powershell"
    assert len(result.devnodes) == 2
    hub = result.devnodes[0]
    assert hub.friendly_name == "Generic USB Hub"
    assert hub.device_desc == "Hub description"
    assert hub.hardware_ids == (
        r"USB\VID_0451&PID_2046",
        r"USB\VID_0451&PID_2046&REV_0125",
    )
    assert hub.compatible_ids == (r"USB\USB20_HUB",)
    assert result.devnodes[1].friendly_name == ""
    assert result.devnodes[1].device_desc == "Controller description"
    assert result.devnodes[1].compatible_ids == ()
    assert any(
        r.levelno == logging.WARNING
        and "cfgmgr32" in r.message
        and "PowerShell" in r.message
        for r in caplog.records
    )
    assert run.call_args.kwargs["timeout"] == 4.0
    script = run.call_args.args[0][-1]
    for literal in (
        "Get-PnpDevice -PresentOnly",
        "DEVPKEY_Device_FriendlyName",
        "DEVPKEY_Device_DeviceDesc",
        "DEVPKEY_Device_Parent",
        "HardwareID",
        "CompatibleID",
        "UTF8Encoding",
    ):
        assert literal in script
    assert "$d.DeviceDesc" not in script
    assert "$d.FriendlyName" not in script
    assert "Where-Object" not in script


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("powershell", 4),
        subprocess.CalledProcessError(1, "powershell"),
        FileNotFoundError("powershell"),
    ],
)
def test_powershell_failures_raise(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(
        raw, "present_devnode_records", Mock(side_effect=OSError("broken"))
    )
    monkeypatch.setattr(raw.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        raw.read_raw_windows(4.0)


def test_nonzero_or_malformed_powershell_is_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        raw, "present_devnode_records", Mock(side_effect=OSError("broken"))
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 1, b"", b"error"))
    monkeypatch.setattr(raw.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        raw.read_raw_windows(4.0)
    run.return_value = subprocess.CompletedProcess([], 0, b"truncated\trow", b"")
    with pytest.raises(ValueError):
        raw.read_raw_windows(4.0)


def test_non_windows_raises_before_enumerating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    reader = Mock(side_effect=AssertionError("must not enumerate"))
    monkeypatch.setattr(raw, "present_devnode_records", reader)
    with pytest.raises(OSError, match="Windows"):
        raw.read_raw_windows(4.0)
    reader.assert_not_called()
