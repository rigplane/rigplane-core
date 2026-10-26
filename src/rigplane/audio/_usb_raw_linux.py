"""Read-only USB, tty and ALSA sysfs records for USB captures."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class RawLinux:
    usb_devices: tuple[dict[str, Any], ...] = ()
    serial_devices: tuple[dict[str, Any], ...] = ()
    audio_devices: tuple[dict[str, Any], ...] = ()
    asound_cards: str | None = None
    unavailable: tuple[dict[str, str], ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "usb_devices": list(self.usb_devices),
            "serial_devices": list(self.serial_devices),
            "audio_devices": list(self.audio_devices),
            "asound_cards": self.asound_cards,
            "unavailable": list(self.unavailable),
        }


def _read_asound_cards() -> str:
    return Path("/proc/asound/cards").read_text(encoding="utf-8")


def read_raw_linux(sysfs_root: str = "/sys", timeout_s: float = 5.0) -> RawLinux:
    unavailable: list[dict[str, str]] = []
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        return RawLinux(unavailable=({"part": "linux", "reason": "invalid timeout"},))
    deadline = time.monotonic() + timeout_s
    root = Path(sysfs_root)
    usb: dict[Path, dict[str, Any]] = {}
    interfaces: dict[Path, dict[str, Any]] = {}

    def check_time() -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("capture deadline expired")

    def fail(part: str, exc: Exception) -> None:
        unavailable.append({"part": f"linux:{part}", "reason": type(exc).__name__})

    def public(path: Path) -> str:
        return "/sys/" + path.relative_to(root).as_posix()

    def read(path: Path, *, optional: bool = False) -> str | None:
        check_time()
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            if optional:
                return None
            raise

    def entries(path: Path, part: str) -> list[Path]:
        try:
            check_time()
            return sorted(path.iterdir())
        except Exception as exc:
            fail(part, exc)
            return []

    try:
        root = root.resolve(strict=True)
        for entry in entries(root / "bus/usb/devices", "usb"):
            try:
                check_time()
                node = entry.resolve(strict=True)
                public(node)
                if ":" in entry.name:
                    interfaces[node] = {
                        "path": public(node),
                        "interface_class": read(node / "bInterfaceClass"),
                    }
                else:
                    fields = {
                        "devpath": "devpath",
                        "busnum": "busnum",
                        "vendor_id": "idVendor",
                        "product_id": "idProduct",
                        "product": "product",
                        "manufacturer": "manufacturer",
                        "serial_number": "serial",
                        "device_class": "bDeviceClass",
                    }
                    usb[node] = {
                        "path": public(node),
                        **{
                            field: read(
                                node / attr,
                                optional=field
                                in {"product", "manufacturer", "serial_number"},
                            )
                            for field, attr in fields.items()
                        },
                    }
            except Exception as exc:
                fail("usb", exc)
    except Exception as exc:
        fail("usb", exc)
    for node, row in usb.items():
        row["parent"] = next((public(p) for p in node.parents if p in usb), None)
        row["interfaces"] = [v for p, v in interfaces.items() if p.parent == node]
        row["is_hub"] = row["device_class"] == "09" or any(
            v["interface_class"] == "09" for v in row["interfaces"]
        )
    cards = None
    try:
        check_time()
        cards = _read_asound_cards()
    except Exception as exc:
        fail("asound", exc)
    names = {
        int(n): name
        for n, name in re.findall(
            r"^\s*(\d+)\s+\[[^\]]*\]\s*:\s*[^\n]*? - ([^\n]+)",
            cards or "",
            re.MULTILINE,
        )
    }
    devices: dict[str, list[dict[str, Any]]] = {"tty": [], "sound": []}
    for kind in devices:
        for entry in entries(root / "class" / kind, kind):
            if kind == "sound" and not re.fullmatch(r"card\d+", entry.name):
                continue
            try:
                check_time()
                try:
                    (entry / "device").lstat()
                except FileNotFoundError:
                    continue
                node = (entry / "device").resolve(strict=True)
                chain = (node, *node.parents)
                row = {
                    "device": f"/dev/{entry.name}" if kind == "tty" else entry.name,
                    "path": public(node),
                    "usb_device": next((public(p) for p in chain if p in usb), None),
                    "usb_interface": next(
                        (public(p) for p in chain if p in interfaces), None
                    ),
                }
                if kind == "sound":
                    row["id"] = read(entry / "id", optional=True)
                    row["name"] = names.get(int(entry.name[4:]))
                devices[kind].append(row)
            except Exception as exc:
                fail(kind, exc)
    return RawLinux(
        tuple(usb.values()),
        tuple(devices["tty"]),
        tuple(devices["sound"]),
        cards,
        tuple(unavailable),
    )
