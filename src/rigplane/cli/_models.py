"""``rigplane models`` — list supported radio profiles.

Thin driver over :func:`rigplane.rig_loader.discover_available_rigs`: every
row derives from the loaded profile fields (``rig.id``, ``rig.model``,
``rig.has_lan``, ``rig.protocol_type``) plus
:func:`rigplane.backends.factory.supports_serial_model`. There is exactly
one mechanism per job here — no second radio list, no per-model branches.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from rigplane.backends.factory import supports_serial_model
from rigplane.rig_loader import RigLoadError, discover_available_rigs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rigplane.profiles.rig_loader import RigConfig

__all__ = [
    "add_subparser",
    "backend_for_protocol",
    "connect_command",
    "describe_rig",
    "match_rig",
    "run",
    "supported_models_text",
]

# USB transport label by profile protocol type.
_USB_TRANSPORT: dict[str, str] = {
    "civ": "USB CI-V",
    "yaesu_cat": "USB CAT",
    "kenwood_cat": "USB CAT",
}


def match_rig(rigs: Mapping[str, RigConfig], name: str) -> RigConfig | None:
    """Return the rig matching *name*, or None when unknown.

    Case-insensitive match against the dict key (the ``--model`` value) or
    against ``rig.id`` with ``-``/``_`` spelling normalized.
    """
    wanted = name.lower()
    wanted_id = wanted.replace("-", "_")
    for model, rig in rigs.items():
        if model.lower() == wanted or rig.id.lower() == wanted_id:
            return rig
    return None


def supported_models_text(rigs: Mapping[str, RigConfig]) -> str:
    """Format the sorted ``--model`` values for user-facing messages."""
    return ", ".join(sorted(rigs))


def connect_command(
    model_arg: str,
    *,
    host: str | None = None,
    serial_port: str | None = None,
    serial_baud: int | None = None,
    radio_addr: int | None = None,
) -> str:
    """Format a ready-to-copy ``rigplane ... status`` command line."""
    if host is not None:
        return f"rigplane --model {model_arg} --host {host} status"
    parts = [f"rigplane --model {model_arg} --serial-port {serial_port}"]
    if serial_baud is not None:
        parts.append(f"--serial-baud {serial_baud}")
    if radio_addr is not None:
        parts.append(f"--radio-addr 0x{radio_addr:02X}")
    parts.append("status")
    return " ".join(parts)


def backend_for_protocol(protocol_type: str | None, *, serial_port: bool) -> str | None:
    """Infer the backend name from a profile protocol type.

    ``yaesu_cat`` always routes to ``yaesu-cat``; ``civ`` routes to
    ``serial`` when a serial port is given and to ``lan`` otherwise;
    anything else has no backend.
    """
    if protocol_type == "yaesu_cat":
        return "yaesu-cat"
    if protocol_type == "civ":
        return "serial" if serial_port else "lan"
    return None


def describe_rig(rig: RigConfig) -> dict[str, Any]:
    """Describe one profile as a JSON-serializable entry for ``models``."""
    maker = rig.id.split("_", 1)[0].capitalize()
    protocol = rig.protocol_type
    transports: list[str] = []
    if rig.has_lan:
        transports.append("LAN")
    usb_label = _USB_TRANSPORT.get(protocol)
    if usb_label is not None:
        transports.append(usb_label)
    candidates = (
        backend_for_protocol(protocol, serial_port=False),
        backend_for_protocol(protocol, serial_port=True),
    )
    backends: list[str] = []
    for candidate in candidates:
        if candidate is None or candidate in backends:
            continue
        usable = rig.has_lan if candidate == "lan" else supports_serial_model(rig.model)
        if usable:
            backends.append(candidate)
    return {
        "model": rig.model,
        "id": rig.id,
        "maker": maker,
        "name": f"{maker} {rig.model}",
        "transports": transports,
        "protocol": protocol,
        "backends": backends,
        "profile_only": not backends,
    }


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the top-level ``models`` subparser on ``sub``.

    Typed as ``Any`` because ``argparse._SubParsersAction`` is private and the
    surrounding parser code in ``cli/__init__.py`` follows the same convention.
    """
    p: argparse.ArgumentParser = sub.add_parser(
        "models",
        help="List supported radio models and the exact --model value for each",
    )
    p.add_argument(
        "--json",
        dest="json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit machine-readable JSON instead of the plain table.",
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Driver for ``rigplane models``. Returns a process exit code."""
    from rigplane.cli import _rigs_dir

    try:
        rigs = discover_available_rigs(_rigs_dir())
    except RigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    entries = sorted(
        (describe_rig(rig) for rig in rigs.values()),
        key=lambda entry: (entry["maker"].lower(), entry["model"].lower()),
    )
    if getattr(args, "json", False):
        print(json.dumps({"models": entries}, indent=2))
        return 0
    headers = ("--model", "Radio", "Transports", "Backends", "Also accepted")
    rows: list[tuple[str, str, str, str, str]] = [
        (
            entry["model"],
            entry["name"],
            ", ".join(entry["transports"]),
            ", ".join(entry["backends"])
            if entry["backends"]
            else "profile only, no backend yet",
            entry["id"],
        )
        for entry in entries
    ]
    table = [headers, *rows]
    widths = [max(len(row[index]) for row in table) for index in range(len(headers))]
    for row in table:
        cells = (cell.ljust(widths[index]) for index, cell in enumerate(row))
        print("  ".join(cells).rstrip())
    print("Matching is case-insensitive; the profile id (last column) also works.")
    return 0
