"""ICE gathering policy for the WebRTC transport.

Default answerer posture is host-only with a hard total bound: VPN/tunnel
and bridge interfaces are excluded from host-candidate enumeration unless
the operator opts in, and gathering never runs longer than
``GATHER_TIMEOUT_S`` — candidates found by then are used and the SDP answer
is produced from them.

Operating-system signals (one per OS, read where the kernel exposes them):

* Linux: entries under ``/sys/class/net/<if>/`` — a ``device`` entry means
  the interface has backing hardware (physical); ``type`` of ``65534`` or a
  ``tun_flags`` entry means a tunnel device; a ``bridge`` or ``brif`` entry
  means a bridge. The default route is read from ``/proc/net/route``.
* macOS: the ``IFF_POINTOPOINT`` (``0x10``) flag from ``SIOCGIFFLAGS`` means
  a point-to-point (tunnel/VPN) interface; otherwise an interface listed by
  ``networksetup -listallhardwareports`` is physical.
* Windows: the interface-type code (RFC 2863, as reported by the IP_ADAPTER
  table): 6/71 are physical Ethernet/Wi-Fi, 131/53 are tunnel/prop-virtual.

When classification is unknown — the signal is missing, unreadable, or the
platform offers no source for it — the address is kept (treated like
``PHYSICAL``): an unclassifiable local address must never silently drop a
working host path. Addresses on the default route are always kept regardless
of kind, so the answer never loses the interface that reaches the peer.

The policy reaches aioice through the single installer
:func:`install_ice_gathering_policy`, which patches
``aioice.ice.get_host_addresses`` (address allow-list) and caps
``Connection.get_component_candidates``' STUN/TURN wait at
``GATHER_TIMEOUT_S``. If aioice ever gains an upstream address-filter
parameter, this monkey-patching installer should be replaced by that
parameter.
"""

from __future__ import annotations

import contextlib
import enum
import importlib
import os
import socket
import struct
import subprocess
import sys
from collections.abc import Container, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "GATHER_TIMEOUT_S",
    "IFF_POINTOPOINT",
    "IF_TYPE_ETHERNET_CSMACD",
    "IF_TYPE_IEEE80211",
    "IF_TYPE_PROPVIRTUAL",
    "IF_TYPE_TUNNEL",
    "AddressInfo",
    "IceGatheringPolicy",
    "IceServer",
    "IfKind",
    "classify_linux_interface",
    "classify_macos_interface",
    "classify_windows_interface",
    "install_ice_gathering_policy",
    "parse_ice_servers",
]

#: Total bound for one answerer-side ICE gather: candidates found by then
#: are used and the answer is produced from them.
GATHER_TIMEOUT_S: Final = 1.5

#: ``IFF_POINTOPOINT`` — route-point flag: a tunnel/VPN interface on macOS.
IFF_POINTOPOINT: Final = 0x10

#: Ethernet (RFC 2863): a physical interface on Windows.
IF_TYPE_ETHERNET_CSMACD: Final = 6
#: Wi-Fi (RFC 2863): a physical interface on Windows.
IF_TYPE_IEEE80211: Final = 71
#: Tunnel (RFC 2863): a VPN/tunnel interface on Windows.
IF_TYPE_TUNNEL: Final = 131
#: Proprietary virtual (RFC 2863): treated as a tunnel on Windows.
IF_TYPE_PROPVIRTUAL: Final = 53


class IfKind(enum.Enum):
    """What an interface is, as far as the gathering policy cares."""

    PHYSICAL = "physical"
    BRIDGE = "bridge"
    TUNNEL = "tunnel"
    UNKNOWN = "unknown"


@dataclass
class AddressInfo:
    """One local address plus the policy inputs derived for it."""

    ip: str
    ifname: str
    kind: IfKind = IfKind.UNKNOWN
    is_default_route: bool = False


@dataclass
class IceServer:
    """One STUN/TURN server entry from the offer POST body."""

    urls: list[str]
    username: str | None = None
    credential: str | None = None

    @classmethod
    def from_json(cls, value: object) -> IceServer:
        """Build an entry from a JSON-decoded object.

        Raises :class:`ValueError` on a non-dict entry, a missing/non-list/
        empty ``urls`` list, non-string URL entries, or non-string
        ``username``/``credential`` values.
        """
        if not isinstance(value, dict):
            raise ValueError(f"ice server entry must be an object: {value!r}")
        urls_raw: object = value.get("urls")
        if not isinstance(urls_raw, list) or not urls_raw:
            raise ValueError(f"ice server entry needs a non-empty urls list: {value!r}")
        for url in urls_raw:
            if not isinstance(url, str):
                raise ValueError(
                    f"ice server urls entries must be strings: {value!r}"
                )
        username: str | None = None
        raw_username: object = value.get("username")
        if raw_username is not None:
            if not isinstance(raw_username, str):
                raise ValueError(
                    f"ice server username must be a string: {value!r}"
                )
            username = raw_username
        credential: str | None = None
        raw_credential: object = value.get("credential")
        if raw_credential is not None:
            if not isinstance(raw_credential, str):
                raise ValueError(
                    f"ice server credential must be a string: {value!r}"
                )
            credential = raw_credential
        return cls(urls=list(urls_raw), username=username, credential=credential)


def parse_ice_servers(value: object) -> list[IceServer]:
    """Parse the ``ice_servers`` field of the offer POST body.

    ``None`` means the client sent nothing and yields ``[]``. A list yields
    one :class:`IceServer` per entry. Anything else raises
    :class:`ValueError`.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"ice_servers must be a list or null: {value!r}")
    return [IceServer.from_json(entry) for entry in value]


def classify_linux_interface(name: str, sysfs: Mapping[str, str | None]) -> IfKind:
    """Classify a Linux interface from ``/sys/class/net/<if>/`` entries.

    Only entry *presence* matters, except for ``type`` whose content is
    compared: ``type`` of ``65534`` is a tunnel device. Precedence is
    tunnel signals first, then bridge signals, then physical.
    """
    del name
    if "tun_flags" in sysfs or sysfs.get("type") == "65534":
        return IfKind.TUNNEL
    if "bridge" in sysfs or "brif" in sysfs:
        return IfKind.BRIDGE
    if "device" in sysfs:
        return IfKind.PHYSICAL
    return IfKind.UNKNOWN


def classify_macos_interface(
    name: str, iff_flags: int, hardware_ports: Container[str]
) -> IfKind:
    """Classify a macOS interface from its ``SIOCGIFFLAGS`` flags.

    A set ``IFF_POINTOPOINT`` bit means a tunnel/VPN interface; otherwise
    membership in the ``networksetup`` hardware-port list means physical.
    """
    if iff_flags & IFF_POINTOPOINT:
        return IfKind.TUNNEL
    if name in hardware_ports:
        return IfKind.PHYSICAL
    return IfKind.UNKNOWN


def classify_windows_interface(iftype: int) -> IfKind:
    """Classify a Windows interface from its RFC 2863 interface-type code."""
    if iftype in (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211):
        return IfKind.PHYSICAL
    if iftype in (IF_TYPE_TUNNEL, IF_TYPE_PROPVIRTUAL):
        return IfKind.TUNNEL
    return IfKind.UNKNOWN


@dataclass
class IceGatheringPolicy:
    """Which local addresses may become host candidates.

    Default is host-only: tunnels are excluded unless ``include_vpn`` is
    set, bridges are always excluded, and physical/unclassifiable addresses
    are kept. The default route's addresses are always kept regardless of
    kind. Selection preserves input order and deduplicates IPs.
    """

    include_vpn: bool = False

    def select(self, candidates: Sequence[AddressInfo]) -> list[str]:
        """Return the allowed IPs for ``candidates``, in order, deduped."""
        allowed: list[str] = []
        for candidate in candidates:
            if candidate.ip in allowed:
                continue
            if candidate.is_default_route:
                allowed.append(candidate.ip)
                continue
            if candidate.kind is IfKind.TUNNEL and not self.include_vpn:
                continue
            if candidate.kind is IfKind.BRIDGE:
                continue
            allowed.append(candidate.ip)
        return allowed


def _adapter_addresses() -> list[tuple[str, list[str]]]:
    """List ``(ifname, [ip, ...])`` pairs via ``ifaddr`` (an aioice dependency).

    Falls back to ``socket.getaddrinfo`` when ``ifaddr`` is unavailable; the
    fallback cannot name the interface, so those addresses classify UNKNOWN
    (kept by the policy) unless they sit on a detected default route.
    """
    try:
        ifaddr: Any = importlib.import_module("ifaddr")
        pairs: list[tuple[str, list[str]]] = []
        for adapter in ifaddr.get_adapters():
            ips: list[str] = []
            for ip in adapter.ips:
                raw = ip.ip
                if isinstance(raw, str):
                    addr = raw
                elif isinstance(raw, tuple) and raw and isinstance(raw[0], str):
                    addr = raw[0]
                else:
                    continue
                if addr not in ips:
                    ips.append(addr)
            pairs.append((str(adapter.nice_name), ips))
        return pairs
    except Exception:
        return _fallback_addresses()


def _fallback_addresses() -> list[tuple[str, list[str]]]:
    """Best-effort address list without ``ifaddr`` (all UNKNOWN kind)."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        return []
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        if isinstance(addr, str) and addr not in ips:
            ips.append(addr)
    if not ips:
        return []
    return [(socket.gethostname(), ips)]


def _read_sysfs_entries(ifname: str) -> dict[str, str | None]:
    """Read the classifying ``/sys/class/net/<if>/`` entries by presence."""
    entries: dict[str, str | None] = {}
    base = os.path.join("/sys/class/net", ifname)
    if not os.path.isdir(base):
        return entries
    for key in ("device", "type", "tun_flags", "bridge", "brif"):
        if os.path.lexists(os.path.join(base, key)):
            try:
                with open(
                    os.path.join(base, key), encoding="utf-8"
                ) as handle:
                    entries[key] = handle.read().strip()
            except OSError:
                # Presence alone classifies everything but the "type"
                # content check; a value we cannot read degrades to that.
                entries[key] = None
    return entries


def _linux_default_route_ifnames() -> set[str]:
    """Interfaces holding the default route, from ``/proc/net/route``."""
    ifnames: set[str] = set()
    try:
        with open("/proc/net/route", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ifnames
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "00000000":
            ifnames.add(parts[0])
    return ifnames


def _enumerate_linux() -> list[AddressInfo]:
    """Enumerate local addresses with sysfs classification."""
    default_routes = _linux_default_route_ifnames()
    infos: list[AddressInfo] = []
    for ifname, ips in _adapter_addresses():
        kind = classify_linux_interface(ifname, _read_sysfs_entries(ifname))
        for ip in ips:
            infos.append(
                AddressInfo(
                    ip=ip,
                    ifname=ifname,
                    kind=kind,
                    is_default_route=ifname in default_routes,
                )
            )
    return infos


def _macos_hardware_ports() -> set[str]:
    """Interface names from ``networksetup -listallhardwareports``."""
    try:
        completed = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    ports: set[str] = set()
    for line in completed.stdout.splitlines():
        if line.startswith("Device: "):
            ports.add(line[len("Device: "):].strip())
    return ports


def _macos_default_route_ifname() -> str:
    """Interface holding the default route, via ``route -n get default``."""
    try:
        completed = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("interface:"):
            return stripped[len("interface:"):].strip()
    return ""


def _macos_iff_flags(ifname: str) -> int:
    """Interface flags via ``SIOCGIFFLAGS``; 0 when unreadable."""
    try:
        import fcntl  # noqa: PLC0415  # absent on Windows; lazy import

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with probe:
            request = struct.pack("16sH", ifname.encode("utf-8")[:15], 0)
            if sys.platform == "darwin":
                ioctl_code = 0xC0206911
            else:  # pragma: no cover - same helper on other Unixes
                ioctl_code = 0x8913
            response = fcntl.ioctl(probe.fileno(), ioctl_code, request)
            return int(struct.unpack("16sH", response[:18])[1])
    except (OSError, ImportError, struct.error):
        return 0


def _enumerate_macos() -> list[AddressInfo]:
    """Enumerate local addresses with POINTOPOINT/hardware-port signals."""
    ports = _macos_hardware_ports()
    default_route = _macos_default_route_ifname()
    infos: list[AddressInfo] = []
    for ifname, ips in _adapter_addresses():
        kind = classify_macos_interface(ifname, _macos_iff_flags(ifname), ports)
        for ip in ips:
            infos.append(
                AddressInfo(
                    ip=ip,
                    ifname=ifname,
                    kind=kind,
                    is_default_route=ifname == default_route,
                )
            )
    return infos


def _enumerate_windows() -> list[AddressInfo]:
    """Enumerate local addresses without an IfType source (all UNKNOWN).

    The classifier's Windows signal is the RFC 2863 interface-type code;
    this host offers no scriptable source for it, so every address is
    UNKNOWN — and UNKNOWN addresses are kept by the policy.
    """
    infos: list[AddressInfo] = []
    for ifname, ips in _adapter_addresses():
        for ip in ips:
            infos.append(AddressInfo(ip=ip, ifname=ifname))
    return infos


def _enumerate_local_addresses() -> list[AddressInfo]:
    """Enumerate local addresses with per-OS classification."""
    if sys.platform.startswith("linux"):
        return _enumerate_linux()
    if sys.platform == "darwin":
        return _enumerate_macos()
    if sys.platform == "win32":
        return _enumerate_windows()
    return [
        AddressInfo(ip=ip, ifname=ifname)
        for ifname, ips in _adapter_addresses()
        for ip in ips
    ]


@contextlib.contextmanager
def install_ice_gathering_policy(
    policy: IceGatheringPolicy | None = None,
) -> Iterator[None]:
    """Apply ``policy`` to aioice for the enclosed gathering, then undo it.

    Patches ``aioice.ice.get_host_addresses`` to drop policy-excluded
    addresses and caps ``Connection.get_component_candidates``' STUN/TURN
    wait at ``GATHER_TIMEOUT_S`` so one unresponsive server cannot stall the
    answer past the total bound. Both patches are restored afterwards.
    """
    active = policy if policy is not None else IceGatheringPolicy()
    aioice_ice: Any = importlib.import_module("aioice.ice")
    original_addresses = aioice_ice.get_host_addresses
    original_component = aioice_ice.Connection.get_component_candidates

    def _filtered_host_addresses(
        use_ipv4: bool = True, use_ipv6: bool = True
    ) -> list[str]:
        addresses: list[str] = original_addresses(use_ipv4, use_ipv6)
        try:
            infos = _enumerate_local_addresses()
        except Exception:
            return addresses
        allowed = set(active.select(infos))
        known = {info.ip for info in infos}
        # Addresses the enumerator did not see are kept: never drop an
        # address we could not classify.
        return [addr for addr in addresses if addr not in known or addr in allowed]

    async def _bounded_component_candidates(
        self: Any, component: int, addresses: list[str], timeout: int = 5
    ) -> Any:
        bound = min(float(timeout), GATHER_TIMEOUT_S)
        return await original_component(self, component, addresses, timeout=bound)

    aioice_ice.get_host_addresses = _filtered_host_addresses
    aioice_ice.Connection.get_component_candidates = _bounded_component_candidates
    try:
        yield
    finally:
        aioice_ice.get_host_addresses = original_addresses
        aioice_ice.Connection.get_component_candidates = original_component

