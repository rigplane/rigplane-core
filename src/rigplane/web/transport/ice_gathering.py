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
  a point-to-point (tunnel/VPN) interface; an interface named ``bridge<N>``
  (the Darwin ``if_bridge(4)`` naming convention) is a bridge; otherwise an
  interface listed by ``networksetup -listallhardwareports`` is physical.
* Windows: ``GetAdaptersAddresses`` (``iphlpapi``) yields each adapter's
  ``IfType`` (RFC 2863 / IANA ifType), ``FriendlyName``, ``OperStatus`` and
  unicast addresses; ``GetIfEntry2`` on ``MIB_IF_ROW2`` yields ``Type`` and
  ``InterfaceAndOperStatusFlags.HardwareInterface``. ``IfType`` alone cannot
  separate physical from virtual: Hyper-V/VMware virtual adapters report 6
  like real Ethernet — the OS-provided signal is ``HardwareInterface``.
  Hardware adapters are physical; tunnel/prop-virtual IfTypes (131/53) are
  tunnels; non-hardware adapters with a hardware IfType are virtual-switch
  ports (bridges, skipped by default); a failed per-adapter query degrades
  to unknown. The default-route source address is always kept, which is
  what keeps a LAN bound to a Hyper-V external switch.

When classification is unknown — the signal is missing, unreadable, or the
platform offers no source for it — the address is kept (treated like
``PHYSICAL``): an unclassifiable local address must never silently drop a
working host path. Addresses on the default route are always kept regardless
of kind, so the answer never loses the interface that reaches the peer.

The policy reaches aioice through :func:`install_ice_gathering_policy`,
which installs the two hooks ONCE per process (idempotent; a module-level
flag guards double install; they are never uninstalled): a patched
``aioice.ice.get_host_addresses`` (address allow-list) and a cap on
``Connection.get_component_candidates``' STUN/TURN wait at
``GATHER_TIMEOUT_S``. The hooks read the active policy from a
``contextvars.ContextVar`` that each ``negotiate()`` sets for the duration
of its gathering (token reset in ``finally``), so concurrent negotiations
keep their own policies. When no policy is set in the current context
(e.g. some other aioice user in the process), the hooks behave exactly
like the originals: no filtering and aioice's own timeout. If aioice ever
gains an upstream address-filter parameter, this monkey-patching installer
should be replaced by that parameter.
"""

from __future__ import annotations

import contextlib
import contextvars
import ctypes
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
    "apply_ice_gathering_policy",
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
                raise ValueError(f"ice server urls entries must be strings: {value!r}")
        username: str | None = None
        raw_username: object = value.get("username")
        if raw_username is not None:
            if not isinstance(raw_username, str):
                raise ValueError(f"ice server username must be a string: {value!r}")
            username = raw_username
        credential: str | None = None
        raw_credential: object = value.get("credential")
        if raw_credential is not None:
            if not isinstance(raw_credential, str):
                raise ValueError(f"ice server credential must be a string: {value!r}")
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


def _is_macos_bridge_name(name: str) -> bool:
    """Whether ``name`` follows the Darwin bridge-interface convention.

    Bridge interfaces on macOS are named ``bridge<N>`` (see ``if_bridge(4)``);
    ``networksetup`` even lists ``bridge0`` ("Thunderbolt Bridge") as a
    hardware port, so the name has to win over the hardware-port list.
    """
    suffix = name.removeprefix("bridge")
    return suffix != name and suffix.isdigit()


def classify_macos_interface(
    name: str, iff_flags: int, hardware_ports: Container[str]
) -> IfKind:
    """Classify a macOS interface from flags, name, and hardware ports.

    A set ``IFF_POINTOPOINT`` bit means a tunnel/VPN interface; a
    ``bridge<N>`` name means a bridge (checked before the hardware-port
    list, which contains ``bridge0``); otherwise membership in the
    ``networksetup`` hardware-port list means physical.
    """
    if iff_flags & IFF_POINTOPOINT:
        return IfKind.TUNNEL
    if _is_macos_bridge_name(name):
        return IfKind.BRIDGE
    if name in hardware_ports:
        return IfKind.PHYSICAL
    return IfKind.UNKNOWN


def classify_windows_interface(
    iftype: int, hardware_interface: bool | None = None
) -> IfKind:
    """Classify a Windows interface from its IfType and hardware flag.

    ``hardware_interface`` is ``MIB_IF_ROW2.InterfaceAndOperStatusFlags``
    ``HardwareInterface`` for the adapter (``None`` when ``GetIfEntry2``
    failed for it). A hardware adapter is physical; tunnel/prop-virtual
    IfTypes (131/53) are tunnels even without the flag; a non-hardware
    adapter is a virtual-switch port (bridge) — Hyper-V/VMware virtual
    adapters report IfType 6 like real Ethernet. With no hardware signal
    the IfType alone decides, as before: 6/71 physical, 131/53 tunnel.
    """
    if hardware_interface is True:
        return IfKind.PHYSICAL
    if iftype in (IF_TYPE_TUNNEL, IF_TYPE_PROPVIRTUAL):
        return IfKind.TUNNEL
    if hardware_interface is False:
        return IfKind.BRIDGE
    if iftype in (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211):
        return IfKind.PHYSICAL
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
                with open(os.path.join(base, key), encoding="utf-8") as handle:
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
            ports.add(line[len("Device: ") :].strip())
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
            return stripped[len("interface:") :].strip()
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


#: ``GetAdaptersAddresses`` family selector: all unicast-capable families.
_AF_UNSPEC: Final = 0
#: ``GetAdaptersAddresses`` flag: skip anycast addresses (all ``NULL``).
_GAA_FLAG_SKIP_ANYCAST: Final = 0x2
#: ``GetAdaptersAddresses`` flag: skip multicast addresses (all ``NULL``).
_GAA_FLAG_SKIP_MULTICAST: Final = 0x4
#: ``GetAdaptersAddresses`` flag: skip DNS server addresses (all ``NULL``).
_GAA_FLAG_SKIP_DNS_SERVER: Final = 0x8
#: ``GetAdaptersAddresses`` success status.
_NO_ERROR: Final = 0
#: ``GetAdaptersAddresses`` "grow the buffer and retry" status.
_ERROR_BUFFER_OVERFLOW: Final = 111
#: ``IF_OPER_STATUS`` value for an operational interface (``ifdef.h``).
_IF_OPER_STATUS_UP: Final = 1

#: ``MIB_IF_ROW2.InterfaceAndOperStatusFlags`` bit masks. MSVC assigns C
#: bitfields from the least-significant bit, so the first field declared in
#: ``netioapi.h`` (``HardwareInterface``) is bit 0; the order below follows
#: the ``MIB_IF_ROW2`` member docs on Microsoft Learn (``netioapi.h``).
_HARDWARE_INTERFACE_FLAG: Final = 0x01
_FILTER_INTERFACE_FLAG: Final = 0x02
_CONNECTOR_PRESENT_FLAG: Final = 0x04
_NOT_AUTHENTICATED_FLAG: Final = 0x08
_NOT_MEDIA_CONNECTED_FLAG: Final = 0x10
_PAUSED_FLAG: Final = 0x20
_LOW_POWER_FLAG: Final = 0x40
_END_POINT_INTERFACE_FLAG: Final = 0x80


class _WinSocketAddress(ctypes.Structure):
    """``SOCKET_ADDRESS``: pointer + length only (family read from bytes)."""

    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int32),
    ]


class _WinSockaddrIn(ctypes.Structure):
    """``sockaddr_in`` (IPv4) — same layout ``ifaddr._shared`` decodes."""

    _fields_ = [
        ("sin_family", ctypes.c_uint16),
        ("sin_port", ctypes.c_uint16),
        ("sin_addr", ctypes.c_uint8 * 4),
        ("sin_zero", ctypes.c_uint8 * 8),
    ]


class _WinSockaddrIn6(ctypes.Structure):
    """``sockaddr_in6`` (IPv6) — same layout ``ifaddr._shared`` decodes."""

    _fields_ = [
        ("sin6_family", ctypes.c_uint16),
        ("sin6_port", ctypes.c_uint16),
        ("sin6_flowinfo", ctypes.c_uint32),
        ("sin6_addr", ctypes.c_uint8 * 16),
        ("sin6_scope_id", ctypes.c_uint32),
    ]


class _WinUnicastAddress(ctypes.Structure):
    """``IP_ADAPTER_UNICAST_ADDRESS`` prefix: only what the walk reads.

    ``Next`` is a void pointer (pointer-sized on 32- and 64-bit) to avoid a
    self-referential ``ctypes`` declaration; it is cast back on the walk.
    """

    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("Next", ctypes.c_void_p),
        ("Address", _WinSocketAddress),
    ]


class _WinAdapterAddresses(ctypes.Structure):
    """``IP_ADAPTER_ADDRESSES`` through ``InterfaceLuid``.

    Field order and sizes follow ``ifaddr._win32`` (an aioice dependency)
    for the XP prefix and ``iptypes.h`` after it; every integer uses an
    explicitly-sized ``ctypes`` type so the layout is correct on 64-bit and
    32-bit, and every unread pointer is a void pointer. ``ZoneIndices``
    through ``InterfaceLuid`` exist only to reach the LUID — the
    authoritative key for ``GetIfEntry2`` (``IfIndex``/``Ipv6IfIndex`` can
    differ per family, the LUID cannot).
    """

    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("IfIndex", ctypes.c_uint32),
        ("Next", ctypes.c_void_p),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(_WinUnicastAddress)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_void_p),
        ("Description", ctypes.c_void_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_uint8 * 8),
        ("PhysicalAddressLength", ctypes.c_uint32),
        ("Flags", ctypes.c_uint32),
        ("Mtu", ctypes.c_uint32),
        ("IfType", ctypes.c_uint32),
        ("OperStatus", ctypes.c_int32),
        ("Ipv6IfIndex", ctypes.c_uint32),
        ("ZoneIndices", ctypes.c_uint32 * 16),
        ("FirstPrefix", ctypes.c_void_p),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("FirstWinsServerAddress", ctypes.c_void_p),
        ("FirstGatewayAddress", ctypes.c_void_p),
        ("Ipv4Metric", ctypes.c_uint32),
        ("Ipv6Metric", ctypes.c_uint32),
        ("InterfaceLuid", ctypes.c_uint64),
    ]


class _MibIfRow2(ctypes.Structure):
    """Full ``MIB_IF_ROW2`` (``netioapi.h``).

    ``GetIfEntry2`` fills the whole row, so the declaration must span the
    whole struct — a prefix would leave the API writing past the buffer.
    Unread enums use ``c_uint32``: only their offsets matter here. String
    arrays use ``c_uint16`` (``WCHAR`` is 2 bytes on both 32- and 64-bit
    Windows) so the layout also computes correctly off Windows.
    """

    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", ctypes.c_uint32),
        ("InterfaceGuid", ctypes.c_uint8 * 16),
        ("Alias", ctypes.c_uint16 * 257),
        ("Description", ctypes.c_uint16 * 257),
        ("PhysicalAddressLength", ctypes.c_uint32),
        ("PhysicalAddress", ctypes.c_uint8 * 32),
        ("PermanentPhysicalAddress", ctypes.c_uint8 * 32),
        ("Mtu", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("TunnelType", ctypes.c_uint32),
        ("MediaType", ctypes.c_uint32),
        ("PhysicalMediumType", ctypes.c_uint32),
        ("AccessType", ctypes.c_uint32),
        ("DirectionType", ctypes.c_uint32),
        ("InterfaceAndOperStatusFlags", ctypes.c_uint8),
        ("OperStatus", ctypes.c_int32),
        ("AdminStatus", ctypes.c_int32),
        ("MediaConnectState", ctypes.c_int32),
        ("NetworkGuid", ctypes.c_uint8 * 16),
        ("ConnectionType", ctypes.c_int32),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64),
        ("InUcastPkts", ctypes.c_uint64),
        ("InNUcastPkts", ctypes.c_uint64),
        ("InDiscards", ctypes.c_uint64),
        ("InErrors", ctypes.c_uint64),
        ("InUnknownProtos", ctypes.c_uint64),
        ("InUcastOctets", ctypes.c_uint64),
        ("InMulticastOctets", ctypes.c_uint64),
        ("InBroadcastOctets", ctypes.c_uint64),
        ("OutOctets", ctypes.c_uint64),
        ("OutUcastPkts", ctypes.c_uint64),
        ("OutNUcastPkts", ctypes.c_uint64),
        ("OutDiscards", ctypes.c_uint64),
        ("OutErrors", ctypes.c_uint64),
        ("OutUcastOctets", ctypes.c_uint64),
        ("OutMulticastOctets", ctypes.c_uint64),
        ("OutBroadcastOctets", ctypes.c_uint64),
        ("OutQLen", ctypes.c_uint64),
    ]


@dataclass
class _WindowsAdapter:
    """One adapter row from ``GetAdaptersAddresses``, decoded to values."""

    friendly_name: str
    if_type: int
    oper_status: int
    luid: int
    if_index: int
    ips: list[str]


def _load_iphlpapi() -> Any:
    """Load ``iphlpapi.dll`` with stdcall convention (32-bit safe).

    ``ctypes.WinDLL`` exists only on Windows, so it is resolved via
    ``getattr``: the module must import on every OS and mypy must stay
    clean where the attribute does not exist.
    """
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:  # pragma: no cover - non-Windows hosts never call
        raise OSError("Windows IP Helper API is available on win32 only")
    return win_dll("iphlpapi.dll")


def _windows_sockaddr_to_str(sockaddr: int | None) -> str | None:
    """Decode a ``SOCKADDR`` pointer to text via ``socket.inet_ntop``."""
    if not sockaddr:
        return None
    family = int(ctypes.cast(sockaddr, ctypes.POINTER(ctypes.c_uint16)).contents.value)
    if family == socket.AF_INET:
        sin = ctypes.cast(sockaddr, ctypes.POINTER(_WinSockaddrIn)).contents
        return socket.inet_ntop(socket.AF_INET, bytes(sin.sin_addr))
    if family == socket.AF_INET6:
        sin6 = ctypes.cast(sockaddr, ctypes.POINTER(_WinSockaddrIn6)).contents
        return socket.inet_ntop(socket.AF_INET6, bytes(sin6.sin6_addr))
    return None


def _windows_adapter_entries(iphlpapi: Any) -> list[_WindowsAdapter]:
    """Read every adapter via ``GetAdaptersAddresses`` (grow-and-retry).

    Raises on failure: the gathering hook treats a failed enumeration as
    "classify nothing" and keeps every address.
    """
    size = ctypes.c_uint32(15 * 1024)
    status = _ERROR_BUFFER_OVERFLOW
    buffer = ctypes.create_string_buffer(size.value)
    while status == _ERROR_BUFFER_OVERFLOW:
        buffer = ctypes.create_string_buffer(size.value)
        status = int(
            iphlpapi.GetAdaptersAddresses(
                _AF_UNSPEC,
                _GAA_FLAG_SKIP_ANYCAST
                | _GAA_FLAG_SKIP_MULTICAST
                | _GAA_FLAG_SKIP_DNS_SERVER,
                None,
                buffer,
                ctypes.byref(size),
            )
        )
    if status != _NO_ERROR:
        raise OSError(f"GetAdaptersAddresses failed with status {status}")
    entries: list[_WindowsAdapter] = []
    current: _WinAdapterAddresses | None = _WinAdapterAddresses.from_buffer(buffer)
    while current is not None:
        ips: list[str] = []
        unicast = current.FirstUnicastAddress
        while unicast:
            try:
                ip = _windows_sockaddr_to_str(unicast.contents.Address.lpSockaddr)
            except (OSError, ValueError):
                ip = None
            if ip is not None and ip not in ips:
                ips.append(ip)
            next_ptr = unicast.contents.Next
            unicast = (
                ctypes.cast(next_ptr, ctypes.POINTER(_WinUnicastAddress))
                if next_ptr
                else None
            )
        friendly = current.FriendlyName or ""
        if not friendly and current.AdapterName:
            friendly = current.AdapterName.decode("ascii", "replace")
        entries.append(
            _WindowsAdapter(
                friendly_name=friendly,
                if_type=int(current.IfType),
                oper_status=int(current.OperStatus),
                luid=int(current.InterfaceLuid),
                if_index=int(current.IfIndex),
                ips=ips,
            )
        )
        next_adapter = current.Next
        current = (
            ctypes.cast(next_adapter, ctypes.POINTER(_WinAdapterAddresses)).contents
            if next_adapter
            else None
        )
    return entries


def _windows_hardware_interface(iphlpapi: Any, luid: int, if_index: int) -> bool | None:
    """Read ``HardwareInterface`` for one adapter via ``GetIfEntry2``.

    Returns ``None`` when the query fails: that adapter degrades to
    IfType-only classification (kept by the policy).
    """
    row = _MibIfRow2()
    if luid:
        row.InterfaceLuid = luid
    elif if_index:
        row.InterfaceIndex = if_index
    else:
        return None
    try:
        status = int(iphlpapi.GetIfEntry2(ctypes.byref(row)))
    except Exception:
        return None
    if status != _NO_ERROR:
        return None
    return bool(int(row.InterfaceAndOperStatusFlags) & _HARDWARE_INTERFACE_FLAG)


def _windows_default_route_source_ip() -> str:
    """Source IP of the default route (a UDP connect sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 53))
            return str(probe.getsockname()[0])
    except OSError:
        return ""


def _enumerate_windows() -> list[AddressInfo]:
    """Enumerate local addresses with IfType + hardware-flag signals.

    ``GetAdaptersAddresses`` yields ``IfIndex``/``Ipv6IfIndex`` (or the
    LUID), ``FriendlyName``, ``OperStatus`` and unicast addresses; each Up
    adapter then gets a ``GetIfEntry2`` query for ``MIB_IF_ROW2.Type`` and
    ``InterfaceAndOperStatusFlags``. A failed per-adapter query degrades
    that adapter to IfType-only classification (kept); a failed whole
    enumeration propagates and the hook keeps every address.
    """
    iphlpapi = _load_iphlpapi()
    default_ip = _windows_default_route_source_ip()
    infos: list[AddressInfo] = []
    for entry in _windows_adapter_entries(iphlpapi):
        if entry.oper_status == _IF_OPER_STATUS_UP:
            hardware: bool | None = _windows_hardware_interface(
                iphlpapi, entry.luid, entry.if_index
            )
        else:
            hardware = None
        kind = classify_windows_interface(entry.if_type, hardware)
        for ip in entry.ips:
            infos.append(
                AddressInfo(
                    ip=ip,
                    ifname=entry.friendly_name,
                    kind=kind,
                    is_default_route=bool(default_ip) and ip == default_ip,
                )
            )
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


#: The gathering policy for the current negotiation, or ``None`` outside
#: one. Each ``negotiate()`` sets its own policy for the duration of its
#: gathering; concurrent negotiations are separate tasks, so each keeps its
#: own value. ``None`` means "some other aioice user": the hooks behave
#: exactly like the originals.
_active_policy: contextvars.ContextVar[IceGatheringPolicy | None] = (
    contextvars.ContextVar("rigplane_ice_gathering_policy", default=None)
)

#: Whether the aioice hooks are installed. Set once, never cleared: the
#: hooks are process-wide and stay installed for the process lifetime.
_installed: bool = False


def install_ice_gathering_policy() -> None:
    """Install the two aioice hooks ONCE per process (idempotent).

    Patches ``aioice.ice.get_host_addresses`` to drop addresses excluded by
    the current context's policy and caps
    ``Connection.get_component_candidates``' STUN/TURN wait at
    ``GATHER_TIMEOUT_S`` so one unresponsive server cannot stall the answer
    past the total bound. A second call changes nothing (it never wraps
    twice). The hooks are never uninstalled during normal operation:
    uninstalling around an ``await`` is what let two concurrent
    negotiations interleave patch/restore and leak one negotiation's
    patches over the other's. A no-op when the ``[webrtc]`` extra is
    missing (``negotiate()`` reports that itself).
    """
    global _installed
    if _installed:
        return
    try:
        aioice_ice: Any = importlib.import_module("aioice.ice")
    except ImportError:
        return
    original_addresses = aioice_ice.get_host_addresses
    original_component = aioice_ice.Connection.get_component_candidates

    def _filtered_host_addresses(
        use_ipv4: bool = True, use_ipv6: bool = True
    ) -> list[str]:
        addresses: list[str] = original_addresses(use_ipv4, use_ipv6)
        active = _active_policy.get()
        if active is None:
            return addresses
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
        active = _active_policy.get()
        if active is None:
            return await original_component(self, component, addresses, timeout=timeout)
        bound = min(float(timeout), GATHER_TIMEOUT_S)
        return await original_component(self, component, addresses, timeout=bound)

    aioice_ice.get_host_addresses = _filtered_host_addresses
    aioice_ice.Connection.get_component_candidates = _bounded_component_candidates
    _installed = True


@contextlib.contextmanager
def apply_ice_gathering_policy(
    policy: IceGatheringPolicy | None = None,
) -> Iterator[None]:
    """Set the gathering ``policy`` for the current context, then reset it.

    ``None`` selects the default policy. Only the matching ``negotiate()``
    task sees the value; concurrent negotiations keep their own.
    """
    token = _active_policy.set(policy if policy is not None else IceGatheringPolicy())
    try:
        yield
    finally:
        _active_policy.reset(token)
