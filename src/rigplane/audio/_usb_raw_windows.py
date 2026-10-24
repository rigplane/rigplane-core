"""Unfiltered Windows devnode capture, with a PowerShell fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import platform
import subprocess
from typing import Any, Literal

from rigplane.audio._windows_devnode import DevNodeRecord, present_devnode_records

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawWindows:
    source: Literal["cfgmgr32", "powershell"]
    devnodes: tuple[DevNodeRecord, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "devnodes": [
                {
                    **asdict(node),
                    "hardware_ids": list(node.hardware_ids),
                    "compatible_ids": list(node.compatible_ids),
                }
                for node in self.devnodes
            ],
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> RawWindows:
        if value["source"] not in {"cfgmgr32", "powershell"}:
            raise ValueError("unknown Windows capture source")
        return cls(
            source=value["source"],
            devnodes=tuple(
                DevNodeRecord(
                    **{
                        **node,
                        "hardware_ids": tuple(node["hardware_ids"]),
                        "compatible_ids": tuple(node["compatible_ids"]),
                    }
                )
                for node in value["devnodes"]
            ),
        )


def read_raw_windows(timeout_s: float) -> RawWindows:
    if platform.system() != "Windows":
        raise OSError("USB devnode capture is only available on Windows")
    try:
        return RawWindows("cfgmgr32", tuple(present_devnode_records()))
    except Exception as exc:
        logger.warning(
            "usb-capture: cfgmgr32 failed; falling back to PowerShell: %s", exc
        )

    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new(); "
        "Get-PnpDevice -PresentOnly | ForEach-Object { "
        "$d = $_; $parent = (Get-PnpDeviceProperty -InstanceId $d.InstanceId "
        "-KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data; "
        '"{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}" -f '
        "$d.InstanceId, $parent, $d.Class, $d.FriendlyName, $d.DeviceDesc, "
        "($d.HardwareID -join '|'), ($d.CompatibleID -join '|') }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=timeout_s,
    )
    result.check_returncode()
    records = []
    for line in result.stdout.decode("utf-8-sig").splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 7 or not fields[0]:
            raise ValueError("malformed PowerShell devnode row: expected seven fields")
        records.append(
            DevNodeRecord(
                instance_id=fields[0],
                parent_id=fields[1],
                class_name=fields[2],
                friendly_name=fields[3],
                device_desc=fields[4],
                hardware_ids=tuple(value for value in fields[5].split("|") if value),
                compatible_ids=tuple(value for value in fields[6].split("|") if value),
            )
        )
    if not records:
        raise ValueError("PowerShell returned no present devnodes")
    return RawWindows("powershell", tuple(records))
