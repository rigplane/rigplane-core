"""``FakeCatRadio`` — a Yaesu CAT TCP fake reachable over pyserial ``socket://``.

Three layers, mirroring the testkit's Icom serial fake split:

* unit tests against the fake's own state machine through a raw TCP client;
* the scripted faults, each on the wire;
* the real ``YaesuCatRadio`` backend (the class ``backends/factory.py``
  resolves for the FTX-1 serial model) driving the fake through the
  production ``open_serial_port`` open with ``device="socket://127.0.0.1:<port>"``
  — no seam, no mock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rigplane.backends.yaesu_cat.parser import CatCommandParser
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import (
    CatGarbledFrameError,
    CatTimeoutError,
)
from rigplane.exceptions import CommandRejectedError
from rigplane.rig_loader import load_rig

from rigplane_testkit.yaesu import FakeCatRadio

_RIGS_DIR = Path(__file__).parents[2] / "rigs"


@pytest.fixture()
async def fake() -> FakeCatRadio:
    radio = FakeCatRadio()
    await radio.start()
    yield radio
    await radio.stop()


class _CatClient:
    """A raw TCP test client speaking ``;``-terminated CAT lines."""

    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self, fake: FakeCatRadio) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", fake.port
        )

    async def send(self, line: str) -> None:
        assert self.writer is not None
        self.writer.write(line.encode("ascii"))
        await self.writer.drain()

    async def read_line(self, timeout: float = 1.0) -> str:
        assert self.reader is not None
        data = await asyncio.wait_for(self.reader.readuntil(b";"), timeout=timeout)
        return data.decode("ascii")[:-1]

    async def ask(self, line: str) -> str:
        """Send one line, return the reply without its ';'."""
        await self.send(line)
        return await self.read_line()

    async def assert_silent(self, timeout: float = 0.2) -> None:
        """No bytes arrive for *timeout* seconds."""
        assert self.reader is not None
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self.reader.read(64), timeout=timeout)

    async def read_raw(self, timeout: float = 1.0) -> bytes:
        assert self.reader is not None
        return await asyncio.wait_for(self.reader.readuntil(b";"), timeout=timeout)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


@pytest.fixture()
async def client(fake: FakeCatRadio) -> _CatClient:
    cat = _CatClient()
    await cat.connect(fake)
    yield cat
    cat.close()


def _config():
    return load_rig(_RIGS_DIR / "ftx1.toml")


# ---------------------------------------------------------------------------
# Unit: identity and the read state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_reply_is_profile_derived(client: _CatClient) -> None:
    expected = f"ID{_config().expected_identity_ids[0]}"
    assert await client.ask("ID;") == expected


@pytest.mark.asyncio
async def test_frequency_state_machine_write_is_silent(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    assert await client.ask("FA;") == "FA014074000"
    await client.send("FA007135000;")  # a SET answers with silence
    await client.assert_silent()
    assert fake.frequency_hz == 7_135_000
    assert fake.commands[-1] == "FA007135000;"
    assert await client.ask("FA;") == "FA007135000"


@pytest.mark.asyncio
async def test_mode_state_machine_unknown_code_rejected(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    assert await client.ask("MD0;") == "MD02"  # USB, ftx1 profile code
    await client.send("MD01;")
    await client.assert_silent()
    assert fake.mode_code == "1"
    assert await client.ask("MD0;") == "MD01"
    assert await client.ask("MD0Z;") == "?"  # not a profile mode code
    assert fake.mode_code == "1"


@pytest.mark.asyncio
async def test_ptt_state_machine_unsettable_token_rejected(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    assert await client.ask("TX;") == "TX0"
    await client.send("TX1;")
    await client.assert_silent()
    assert fake.ptt is True
    assert await client.ask("TX;") == "TX1"
    assert await client.ask("TX2;") == "?"  # read-only self-keyed token
    assert fake.ptt is True


@pytest.mark.asyncio
async def test_power_and_auto_info_state(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    assert await client.ask("PS;") == "PS1"
    await client.send("PS0;")
    await client.assert_silent()
    assert fake.power_on is False
    assert await client.ask("PS;") == "PS0"
    assert await client.ask("AI;") == "AI0"
    await client.send("AI1;")
    await client.assert_silent()
    assert fake.auto_info is True
    assert await client.ask("AI;") == "AI1"


@pytest.mark.asyncio
async def test_if_reply_parses_with_the_production_template(
    client: _CatClient,
) -> None:
    spec = _config().commands["get_if_status"]
    assert spec.parse is not None
    reply = await client.ask("IF;")
    parsed = CatCommandParser(spec.parse).parse(reply + ";")
    assert parsed["freq"] == 14_074_000
    assert parsed["mode"] == "2"
    assert parsed["sign"] == "+"


@pytest.mark.asyncio
async def test_unknown_command_is_rejected(client: _CatClient) -> None:
    assert await client.ask("ZZ;") == "?"


# ---------------------------------------------------------------------------
# Unit: scripted faults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_garble_fault_puts_a_non_printable_byte_on_the_wire(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    fake.garble_next_reply("FA")
    await client.send("FA;")
    data = await client.read_raw()
    assert any(byte < 0x20 or byte > 0x7E for byte in data)
    # One-shot: the next read is clean again.
    assert await client.ask("FA;") == "FA014074000"


@pytest.mark.asyncio
async def test_reject_fault_answers_question_mark(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    fake.reject("FA")
    assert await client.ask("FA;") == "?"
    assert await client.ask("PS;") == "PS1"  # other commands unaffected


@pytest.mark.asyncio
async def test_silence_fault_then_recovery(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    fake.silence(0.4)
    await client.send("FA;")
    await client.assert_silent(timeout=0.3)  # swallowed: not even dispatched
    assert fake.commands == ("FA;",)
    assert fake.frequency_hz == 14_074_000
    await asyncio.sleep(0.25)
    assert await client.ask("FA;") == "FA014074000"


@pytest.mark.asyncio
async def test_echo_fault_echoes_the_request_before_the_answer(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    fake.echo_replies()
    assert await client.ask("FA;") == "FA"  # the echo, first
    assert await client.ask("FA;") == "FA014074000"  # then the answer


@pytest.mark.asyncio
async def test_auto_info_burst_pushes_an_unsolicited_line(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    fake.auto_info_burst("FA")
    assert await client.read_line() == "FA014074000"


@pytest.mark.asyncio
async def test_auto_info_mode_pushes_state_changes(
    client: _CatClient, fake: FakeCatRadio
) -> None:
    await client.send("AI1;")
    await client.assert_silent()
    await client.send("FA007135000;")
    assert await client.read_line() == "FA007135000"  # the AI push
    assert fake.frequency_hz == 7_135_000


# ---------------------------------------------------------------------------
# The real backend through the production socket:// open
# ---------------------------------------------------------------------------


def _radio_for(fake: FakeCatRadio) -> YaesuCatRadio:
    return YaesuCatRadio(device=fake.device_url, profile="ftx1")


@pytest.mark.asyncio
async def test_real_backend_connects_and_round_trips_frequency(fake: FakeCatRadio) -> None:
    radio = _radio_for(fake)
    await radio.connect()
    try:
        assert radio.connected
        assert fake.commands[0] == "ID;"  # the identity gate probes first
        assert "IF;" in fake.commands  # then the connect-time IF seed
        assert await radio.get_freq() == 14_074_000
        await radio.set_freq(7_135_000)
        assert fake.frequency_hz == 7_135_000
        assert await radio.get_freq() == 7_135_000
    finally:
        await radio.disconnect()
    assert not radio.connected


@pytest.mark.asyncio
async def test_reject_fault_surfaces_as_the_backends_typed_rejection(
    fake: FakeCatRadio,
) -> None:
    radio = _radio_for(fake)
    await radio.connect()
    try:
        fake.reject("FA")
        with pytest.raises(CommandRejectedError):
            await radio.set_freq(7_135_000)
        assert fake.frequency_hz == 14_074_000  # refused, not applied
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_silence_fault_drives_the_backend_timeout_path(
    fake: FakeCatRadio,
) -> None:
    radio = _radio_for(fake)
    await radio.connect()
    try:
        fake.silence(1.2)
        with pytest.raises(CatTimeoutError):
            await radio.get_freq()
        await asyncio.sleep(0.3)  # let the silence window lapse
        assert await radio.get_freq() == 14_074_000
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_garbled_fault_surfaces_as_garbled_frame_error(
    fake: FakeCatRadio,
) -> None:
    radio = _radio_for(fake)
    await radio.connect()
    try:
        fake.garble_next_reply("FA")
        with pytest.raises(CatGarbledFrameError):
            await radio.get_freq()
        assert await radio.get_freq() == 14_074_000  # one-shot, then clean
    finally:
        await radio.disconnect()
