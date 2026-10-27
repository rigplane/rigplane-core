"""Bundle-wide USB serial redaction and Windows topology pruning."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import re
import secrets
from typing import Any

_USB_SERIAL_PREFIX = r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\"
_USB_SERIAL_ID = re.compile(_USB_SERIAL_PREFIX + r"([^\\&]+)", re.IGNORECASE | re.ASCII)
_SCHEMA_FIELDS = frozenset(
    {
        "schema",
        "session",
        "target",
        "os",
        "profile",
        "source",
        "part",
        "class_name",
        "device_class",
        "interface_class",
    }
)


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value
    )


def _token(key: bytes, value: str, known_serials: frozenset[str] = frozenset()) -> str:
    counter = 0
    while True:
        message = value if counter == 0 else f"{value}\0{counter}"
        digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
        token = "R" + base64.b32encode(digest).decode("ascii")[:10]
        folded = _ascii_lower(token)
        if not any(len(serial) >= 4 and serial in folded for serial in known_serials):
            return token
        counter += 1


def _prune_windows_nodes(
    rows: list[dict[str, Any]], key: bytes, known_serials: frozenset[str]
) -> list[dict[str, Any]]:
    by_id = {_ascii_lower(row.get("instance_id", "")): row for row in rows}
    kept: set[str] = set()

    def keep_chain(instance_id: str, *, owner_only: bool) -> None:
        seen: set[str] = set()
        while instance_id and _ascii_lower(instance_id) not in seen:
            folded = _ascii_lower(instance_id)
            seen.add(folded)
            row = by_id.get(folded)
            if row is not None and _ascii_lower(row.get("class_name", "")) == "net":
                break
            kept.add(folded)
            if row is None or (owner_only and not folded.startswith("swd\\")):
                break
            instance_id = row.get("parent_id", "")

    for row in rows:
        folded = _ascii_lower(row.get("instance_id", ""))
        if _ascii_lower(row.get("class_name", "")) == "net":
            continue
        if folded.startswith("usb\\"):
            keep_chain(row.get("instance_id", ""), owner_only=False)
        elif folded.startswith("swd\\mmdevapi\\"):
            kept.add(folded)
            keep_chain(row.get("parent_id", ""), owner_only=True)

    def public_id(instance_id: str) -> str:
        folded = _ascii_lower(instance_id)
        if folded not in kept:
            return ""
        if folded.startswith("usb\\"):
            return instance_id
        if folded.startswith("swd\\mmdevapi\\"):
            return by_id.get(folded, {"instance_id": instance_id})["instance_id"]
        return "opaque:" + _token(key, folded, known_serials)

    result = []
    for row in rows:
        if _ascii_lower(row.get("instance_id", "")) in kept:
            result.append(
                {
                    **row,
                    "instance_id": public_id(row.get("instance_id", "")),
                    "parent_id": public_id(row.get("parent_id", "")),
                }
            )
    return result


def redact_bundle(
    bundle: dict[str, Any], *, key: bytes | None = None
) -> dict[str, Any]:
    key = secrets.token_bytes(32) if key is None else key
    values: set[str] = set()
    devices: set[str] = set()
    device_serials: dict[str, str] = {}
    uid_locations: set[int | str] = set()

    def collect_devices(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                collect_devices(child)
        elif isinstance(value, dict):
            for child in value.values():
                collect_devices(child)
        elif isinstance(value, str):
            if value.startswith("/dev/"):
                devices.add(value)
            elif value.startswith(("cu.", "tty.", "serial/by-id/")):
                devices.add("/dev/" + value)
            devices.update(
                path.rstrip(".") for path in re.findall(r"/dev/[^\s'\"(),;:]+", value)
            )

    collect_devices(bundle)
    for session in bundle["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            if serial := port.get("serial_number"):
                values.add(serial)
                if device := port.get("device"):
                    device_serials[device] = serial
        for row in raw.get("windows", {}).get("devnodes", []):
            for field in ("instance_id", "parent_id"):
                reference = row.get(field, "")
                if match := _USB_SERIAL_ID.fullmatch(reference):
                    values.add(match[1])
        for part in ("macos", "linux"):
            for row in raw.get(part, {}).get("usb_devices", []):
                if serial := row.get("serial_number"):
                    values.add(serial)
                if isinstance(location := row.get("location_id"), int):
                    uid_locations.add(location)
                if isinstance(devpath := row.get("devpath"), str):
                    uid_locations.add(devpath)
        usb = {
            row.get("registry_id"): row.get("serial_number")
            for row in raw.get("macos", {}).get("usb_devices", [])
        }
        for row in raw.get("macos", {}).get("serial_devices", []):
            if (device := row.get("device")) and (
                serial := usb.get(row.get("usb_registry_id"))
            ):
                device_serials[device] = serial

    known_serials = frozenset(_ascii_lower(value.replace(" ", "_")) for value in values)
    replacements = {
        canonical: _token(key, canonical, known_serials) for canonical in known_serials
    }

    def replace_field(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(_ascii_lower(value.replace(" ", "_")), value)
        return value

    def replace_id(value: str) -> str:
        match = _USB_SERIAL_ID.fullmatch(value)
        if match is None:
            return value
        return value[: match.start(1)] + replace_field(match[1])

    def replace_device(value: str, serial: str | None = None) -> str:
        mac = re.fullmatch(r"(/dev/(?:cu|tty)\.(?:usbserial-|usbmodem))(.*)", value)
        for candidate in sorted({serial} if serial else values, key=len, reverse=True):
            for spelling in {candidate, candidate.replace(" ", "_")}:
                if mac:
                    # A BSD suffix hides a serial only when it equals the serial
                    # exactly, plus at most one trailing interface digit
                    # (macOS "usbserial-<serial><n>"). Anything else, e.g. the
                    # location-derived "usbmodem14201", stays as recorded.
                    exact = re.fullmatch(
                        re.escape(spelling) + r"\d?", mac[2], re.IGNORECASE | re.ASCII
                    )
                    if exact:
                        return (
                            mac[1] + replace_field(candidate) + mac[2][len(spelling) :]
                        )
                if value.startswith("/dev/serial/by-id/"):
                    pattern = re.compile(re.escape(spelling), re.IGNORECASE | re.ASCII)
                    match = re.fullmatch(
                        r"(.+_)" + pattern.pattern + r"(-if\d+.*|$)",
                        value,
                        re.IGNORECASE | re.ASCII,
                    )
                    if match:
                        return match[1] + replace_field(candidate) + match[2]
        return value

    aliases: dict[str, str] = {}
    for device in devices:
        redacted = replace_device(device, device_serials.get(device))
        aliases[device] = redacted
        aliases[device.removeprefix("/dev/")] = redacted.removeprefix("/dev/")

    def redact_topology(value: Any, part: str) -> None:
        if isinstance(value, list):
            for child in value:
                redact_topology(child, part)
        elif isinstance(value, dict):
            for field, child in value.items():
                if field == "serial_number":
                    value[field] = replace_field(child)
                elif field == "device":
                    value[field] = aliases.get(child, child)
                elif part == "macos" and isinstance(child, str):
                    if field in {"registry_path", "parent_path"}:
                        segments = []
                        for segment in child.split("/"):
                            name, sep, location = segment.partition("@")
                            segments.append(replace_field(name) + sep + location)
                        value[field] = "/".join(segments)
                    elif (
                        field == "uid"
                        and child.startswith("AppleUSBAudioEngine:")
                        and child.count(":") >= 2
                    ):
                        prefix, identity, streams = child.rsplit(":", 2)
                        try:
                            location = int(identity, 16)
                        except ValueError:
                            location = None
                        if (
                            identity not in uid_locations
                            and (location is None or location not in uid_locations)
                        ) and _ascii_lower(identity.replace(" ", "_")) in known_serials:
                            value[field] = ":".join(
                                (prefix, replace_field(identity), streams)
                            )
                else:
                    redact_topology(child, part)

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        raw = session.get("raw", {})
        redact_topology(session.get("unavailable", []), "")
        for radio in session.get("radios", []):
            if "serial_port" in radio:
                device = radio["serial_port"]
                radio["serial_port"] = aliases.get(device, device)
        for part in ("macos", "linux"):
            redact_topology(raw.get(part), part)
        for port in raw.get("serial_ports", []):
            if "serial_number" in port:
                port["serial_number"] = replace_field(port["serial_number"])
            for field in ("device", "name"):
                if field in port:
                    port[field] = aliases.get(port[field], port[field])
        windows = raw.get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(
                windows.get("devnodes", []), key, known_serials
            )
            for row in windows["devnodes"]:
                for name in ("instance_id", "parent_id"):
                    row[name] = replace_id(row.get(name, ""))
    serial_pattern = "|".join(
        re.escape(serial).replace("_", "[ _]")
        for serial in sorted(known_serials, key=lambda value: (-len(value), value))
        if len(serial) >= 4
    )
    pattern = re.compile(serial_pattern, re.IGNORECASE | re.ASCII)

    def scrub(value: Any, field: str = "") -> Any:
        if isinstance(value, dict):
            return {name: scrub(child, name) for name, child in value.items()}
        if isinstance(value, list):
            return [scrub(child, field) for child in value]
        if field in _SCHEMA_FIELDS or not isinstance(value, str) or not serial_pattern:
            return value
        locations: list[tuple[int, int]] = []
        if (
            field == "uid"
            and value.startswith("AppleUSBAudioEngine:")
            and value.count(":") >= 2
        ):
            prefix, identity, streams = value.rsplit(":", 2)
            try:
                location = int(identity, 16)
            except ValueError:
                location = None
            if identity in uid_locations or location in uid_locations:
                locations.append((len(prefix) + 1, len(value) - len(streams) - 1))
        if field in {"registry_path", "parent_path"}:
            for match in re.finditer(
                r"(?<=@)[0-9a-f]+(?=/|$)", value, re.IGNORECASE | re.ASCII
            ):
                if int(match[0], 16) in uid_locations:
                    locations.append(match.span())
        if field in {"location", "devpath", "path", "usb_device"}:
            for match in re.finditer(r"(?<=/)[^/:]+(?=[:/]|$)|^[^/:]+$", value):
                if match[0] in uid_locations:
                    locations.append(match.span())
        if field in {"device", "name", "serial_port"}:
            match = re.fullmatch(
                r"(?:/dev/)?(?:cu|tty)\.usbmodem([0-9a-f]+)",
                value,
                re.IGNORECASE | re.ASCII,
            )
            if match and int(match[1], 16) & ~0xF in uid_locations:
                locations.append(match.span(1))

        def replacement(match: re.Match[str]) -> str:
            if any(
                start <= match.start() and match.end() <= end
                for start, end in locations
            ):
                return match[0]
            return replacements[_ascii_lower(match[0].replace(" ", "_"))]

        return pattern.sub(replacement, value)

    return scrub(result)
