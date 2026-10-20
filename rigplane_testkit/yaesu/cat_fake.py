"""A stateful Yaesu CAT fake on a real TCP server, reached through
pyserial's ``socket://`` handler.

Why TCP: the Yaesu serial backend opens its port through
``rigplane.core.serial_open.open_serial_port``, which hands the device
string to ``serial.serial_for_url`` — and pyserial's ``socket://`` URL
handler turns ``device="socket://127.0.0.1:<port>"`` into a plain TCP
client without any production seam. ``FakeCatRadio`` is the server on the
other end: tests hand ``YaesuCatRadio`` the fake's ``device_url`` and the
real backend, transport and parser drive real CAT bytes end to end.

DTR/RTS on the socket handler (checked before relying on this; pyserial
3.5 / pyserial-asyncio 0.6 on the test host): ``open_serial_port`` drives
DTR and RTS low on every open — on a real serial port that is the TX-
safety precaution ``core/serial_open.py`` documents (a radio whose USB
SEND key input is wired to a modem line). pyserial's socket handler
(``serial.urlhandler.protocol_socket.Serial``) accepts those assignments
both while the port is still closed and on the live connection (the
closed-port case was verified with no server listening, so nothing could
be applied), and a TCP socket has no modem lines behind it — so the
production open reaches the fake unchanged and the fake implements no
DTR/RTS behaviour of its own.

Replies are built with the production encoders, never hand-typed byte
strings: every response goes through
``rigplane.backends.yaesu_cat.parser.format_command`` on the loaded
profile's parse templates, and every inbound SET is recognised and
decoded by ``CatCommandParser`` compiled from the same profile's write
templates (``rigs/ftx1.toml`` by default). The connect sequence the real
backend runs — the ``ID;`` identity gate, then the ``IF;`` state seed
(``backends/yaesu_cat/radio.py: YaesuCatRadio.connect``) — is answered
from the profile's ``[identity].expected_ids``.

State: frequency (``FA``), mode (``MD0``), PTT (``TX``), power (``PS``),
auto-information mode (``AI``). Reads answer; writes apply silently, the
way the transport's post-write drain expects a Yaesu set to behave.
Unknown commands and out-of-domain SET values answer ``?;`` — the
rejection ``YaesuCatTransport.query``/``write`` raise on.

Faults, each one method: ``garble_next_reply`` (the next matching reply
carries a byte outside 0x20-0x7E, the ``CatGarbledFrameError`` case),
``reject`` (``?;`` for every command with a given verb), ``silence``
(nothing answers — or applies — for a window), ``echo_replies`` (each
request echoed verbatim before the answer), ``auto_info_burst`` (an
unsolicited info line pushed to every client now),
``auto_info_burst_on_next_request`` (the same lines, pushed on the
next request the fake processes outside a silence window, ahead of its
reply if it has one). Auto-information mode itself pushes the info line
on each state change while on.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from rigplane.backends.yaesu_cat.parser import CatCommandParser, format_command
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig, load_rig

# The repo-root rigs/ directory. ``rigplane.profiles`` exports no
# helper that locates it (its ``__all__`` lists only profile types and
# lookups), and ``rigplane.profiles.rig_loader.discover_available_rigs``
# returns loaded configs — preferring package resources — while still
# taking a fallback directory computed exactly this way; so the fake
# mirrors the production backend's own local computation
# (backends/yaesu_cat/radio.py: ``_RIGS_DIR``).
_RIGS_DIR = Path(__file__).parents[2] / "rigs"

# Commands the fake's personality is built from; every one must exist in
# the loaded profile with the read/parse (get_*) or write (set_*) half it
# names, so a profile that lacks any of them refuses at construction
# instead of failing mid-dispatch.
_REQUIRED_COMMANDS = (
    "get_id",
    "get_freq",
    "set_freq",
    "get_mode",
    "set_mode",
    "get_ptt",
    "set_ptt",
    "get_powerstat",
    "set_powerstat",
    "get_auto_info",
    "set_auto_info",
    "get_if_status",
)

# The measured FTX-1 IF answer carries P1 = "00000"
# (tests/test_ftx1_radio.py: MEASURED_FRAME); the clarifier fields sit at
# their neutral values and the tone/shift selectors read "0".
_IF_CHAN = "00000"
_IF_OFFSET_HZ = 0
_IF_NEUTRAL = "0"

_REJECT = "?;"  # the Yaesu CAT "command not recognized" answer


class FakeCatRadio:
    """A stateful FTX-1-style CAT radio on a local TCP server.

    State changes only through the wire or the explicit test-setup
    property setters. ``start()`` binds 127.0.0.1 on an ephemeral port;
    ``device_url`` is the pyserial ``socket://`` string the production
    open path accepts.
    """

    def __init__(
        self,
        *,
        profile: str = "ftx1",
        frequency_hz: int = 14_074_000,
        mode_code: str = "2",
    ) -> None:
        self._config: RigConfig = load_rig(_RIGS_DIR / f"{profile}.toml")
        if not self._config.expected_identity_ids:
            raise ValueError(
                f"profile {profile!r} declares no [identity].expected_ids; "
                "the ID; answer has nothing to be derived from"
            )
        if not self._config.mode_codes:
            raise ValueError(
                f"profile {profile!r} declares no [modes].codes; mode writes "
                "have no domain to validate against"
            )
        self._mode_codes: frozenset[str] = frozenset(self._config.mode_codes)
        if mode_code not in self._mode_codes:
            raise ValueError(f"mode_code {mode_code!r} is not a {profile!r} mode code")
        missing = [
            name
            for name in _REQUIRED_COMMANDS
            if not isinstance(self._config.commands.get(name), CatCommandSpec)
        ]
        if missing:
            raise ValueError(
                f"profile {profile!r} lacks CAT commands the fake speaks: "
                f"{', '.join(missing)}"
            )

        self._identity_id = self._config.expected_identity_ids[0]
        self._frequency_hz = frequency_hz
        self._mode_code = mode_code
        self._ptt = False
        self._power_on = True
        self._auto_info = False

        self._write_parsers: dict[str, CatCommandParser] = {}
        for name in _REQUIRED_COMMANDS:
            if not name.startswith("set_"):
                continue
            write = self._spec(name).write
            if write is None:
                raise ValueError(f"profile command {name!r} has no write template")
            self._write_parsers[name] = CatCommandParser(write)

        # Fault surface (all off by default).
        self._rejected_verbs: set[str] = set()
        self._garble_verb: str | None = None
        self._silent_until = 0.0
        self._echo = False
        self._pending_burst_verbs: list[str] = []

        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._commands: list[str] = []

    # ------------------------------------------------------------------
    # Identity / state surface
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        """The bound TCP port (``start()`` must have run)."""
        if self._server is None:
            raise RuntimeError("FakeCatRadio is not started")
        return self._server.sockets[0].getsockname()[1]

    @property
    def device_url(self) -> str:
        """The pyserial ``socket://`` URL the production open accepts."""
        return f"socket://127.0.0.1:{self.port}"

    @property
    def frequency_hz(self) -> int:
        return self._frequency_hz

    @frequency_hz.setter
    def frequency_hz(self, value: int) -> None:
        self._frequency_hz = int(value)

    @property
    def mode_code(self) -> str:
        return self._mode_code

    @property
    def ptt(self) -> bool:
        return self._ptt

    @property
    def power_on(self) -> bool:
        return self._power_on

    @property
    def auto_info(self) -> bool:
        return self._auto_info

    @property
    def commands(self) -> tuple[str, ...]:
        """Every decoded inbound line, wire-exact (with its ';')."""
        return tuple(self._commands)

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the TCP server on 127.0.0.1, ephemeral port."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)

    async def stop(self) -> None:
        """Close the server and every connected client."""
        server, self._server = self._server, None
        if server is None:
            return
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        server.close()
        await server.wait_closed()

    # ------------------------------------------------------------------
    # Scriptable faults
    # ------------------------------------------------------------------

    def garble_next_reply(self, verb: str) -> None:
        """Corrupt the next reply to a command with this two-letter verb.

        The reply carries one byte outside 0x20-0x7E but keeps its ';'
        terminator, so the transport's readline classifies it as a
        garbled frame instead of timing out. One-shot.
        """
        self._garble_verb = verb

    def reject(self, verb: str) -> None:
        """Answer ``?;`` to every command with this two-letter verb."""
        self._rejected_verbs.add(verb)

    def silence(self, for_seconds: float) -> None:
        """Answer nothing — and apply nothing — for a window.

        Lines still arrive and are recorded in ``commands``; a mute radio
        neither replies nor changes state.
        """
        self._silent_until = time.monotonic() + max(0.0, for_seconds)

    def echo_replies(self) -> None:
        """Echo every request line verbatim before answering it."""
        self._echo = True

    def auto_info_burst(self, verb: str) -> None:
        """Push the current info line for *verb* to every client, now.

        The unsolicited push AI mode makes a real Yaesu send on state
        change; here it is scriptable on its own.
        """
        self._broadcast(self._info_line(verb))

    def auto_info_burst_on_next_request(self, *verbs: str) -> None:
        """Push the info lines for *verbs* on the next request the fake processes.

        The lines are sent when the next request outside a ``silence``
        window is processed, ahead of its reply if it has one (a SET,
        which gets no reply, also releases the burst), so unsolicited
        auto-information lines sit deterministically after that request
        on the wire.
        """
        if not verbs:
            raise ValueError("no auto-information verbs given")
        for verb in verbs:
            self._info_line(verb)  # refuse an unknown verb at scheduling time
        self._pending_burst_verbs = list(verbs)

    # ------------------------------------------------------------------
    # Server internals
    # ------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.add(writer)
        try:
            while True:
                try:
                    raw = await reader.readuntil(b";")
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                try:
                    line = raw.decode("ascii")
                except UnicodeDecodeError:
                    continue  # garbage on the wire: a real radio ignores it
                self._commands.append(line)
                await self._on_line(line[:-1], writer)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _on_line(self, body: str, writer: asyncio.StreamWriter) -> None:
        if time.monotonic() < self._silent_until:
            return
        if self._pending_burst_verbs:
            verbs, self._pending_burst_verbs = self._pending_burst_verbs, []
            for verb in verbs:
                await self._send(writer, self._info_line(verb).encode("ascii"))
        if self._echo:
            await self._send(writer, (body + ";").encode("ascii"))
        if body[:2] in self._rejected_verbs:
            await self._send(writer, _REJECT.encode("ascii"))
            return
        reply = self._reply_for(body)
        if reply is None:
            return
        data = reply.encode("ascii")
        if body[:2] == self._garble_verb:
            self._garble_verb = None
            data = data[:1] + b"\xff" + data[1:]
        await self._send(writer, data)

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await writer.drain()

    def _broadcast(self, line: str) -> None:
        """Queue *line* for writing to every connected client."""
        data = line.encode("ascii")
        for writer in list(self._clients):
            writer.write(data)

    def _spec(self, name: str) -> CatCommandSpec:
        spec = self._config.commands.get(name)
        assert isinstance(spec, CatCommandSpec)
        return spec

    def _read_cmd(self, name: str) -> str:
        """The wire line the profile's read template sends (without ';')."""
        read = self._spec(name).read
        if read is None:
            raise ValueError(f"profile command {name!r} has no read template")
        return read.rstrip(";")

    def _parse_tpl(self, name: str) -> str:
        parse = self._spec(name).parse
        if parse is None:
            raise ValueError(f"profile command {name!r} has no parse template")
        return parse

    def _match_write(self, name: str, body: str) -> dict[str, Any] | None:
        """Decode *body* against the profile's write template, if it fits."""
        try:
            return self._write_parsers[name].parse(body + ";")
        except ValueError:
            return None

    def _reply_for(self, body: str) -> str | None:
        """The reply for *body* (None = silence), applying any SET it carries."""
        if body == self._read_cmd("get_id"):
            return format_command(
                self._parse_tpl("get_id"), model=int(self._identity_id)
            )
        if body == self._read_cmd("get_freq"):
            return format_command(self._parse_tpl("get_freq"), freq=self._frequency_hz)
        if (fields := self._match_write("set_freq", body)) is not None:
            self._frequency_hz = int(fields["freq"])
            self._push_auto_info("FA")
            return None
        if body == self._read_cmd("get_mode"):
            return format_command(self._parse_tpl("get_mode"), mode=self._mode_code)
        if (fields := self._match_write("set_mode", body)) is not None:
            code = str(fields["mode"])
            if code not in self._mode_codes:
                return _REJECT
            self._mode_code = code
            self._push_auto_info("MD")
            return None
        if body == self._read_cmd("get_ptt"):
            return format_command(
                self._parse_tpl("get_ptt"), state="1" if self._ptt else "0"
            )
        if (fields := self._match_write("set_ptt", body)) is not None:
            if str(fields["state"]) not in ("0", "1"):
                return _REJECT  # the self-keyed token is not settable
            self._ptt = str(fields["state"]) == "1"
            self._push_auto_info("TX")
            return None
        if body == self._read_cmd("get_powerstat"):
            return format_command(
                self._parse_tpl("get_powerstat"), state="1" if self._power_on else "0"
            )
        if (fields := self._match_write("set_powerstat", body)) is not None:
            if str(fields["state"]) not in ("0", "1"):
                return _REJECT
            self._power_on = str(fields["state"]) == "1"
            return None
        if body == self._read_cmd("get_auto_info"):
            return format_command(
                self._parse_tpl("get_auto_info"),
                state="1" if self._auto_info else "0",
            )
        if (fields := self._match_write("set_auto_info", body)) is not None:
            if str(fields["state"]) not in ("0", "1"):
                return _REJECT
            self._auto_info = str(fields["state"]) == "1"
            return None
        if body == self._read_cmd("get_if_status"):
            return format_command(
                self._parse_tpl("get_if_status"),
                chan=_IF_CHAN,
                freq=self._frequency_hz,
                sign="+",
                offset=_IF_OFFSET_HZ,
                rx=_IF_NEUTRAL,
                tx=_IF_NEUTRAL,
                mode=self._mode_code,
                vfo=_IF_NEUTRAL,
                tone=_IF_NEUTRAL,
                shift=_IF_NEUTRAL,
            )
        return _REJECT  # unrecognized command: the Yaesu refusal

    def _info_line(self, verb: str) -> str:
        """The auto-information line for *verb*, wire-exact with its ';'."""
        if verb == "FA":
            return format_command(self._parse_tpl("get_freq"), freq=self._frequency_hz)
        if verb == "MD":
            return format_command(self._parse_tpl("get_mode"), mode=self._mode_code)
        if verb == "TX":
            return format_command(
                self._parse_tpl("get_ptt"), state="1" if self._ptt else "0"
            )
        raise ValueError(f"no auto-information line for verb {verb!r}")

    def _push_auto_info(self, verb: str) -> None:
        """Push the info line for a state change while AI mode is on."""
        if not self._auto_info:
            return
        self._broadcast(self._info_line(verb))


__all__ = ["FakeCatRadio"]
