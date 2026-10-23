"""Profile-driven relative frequency writes with ACK and target readback."""

from dataclasses import replace

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError, CommandRejectedError, TimeoutError
from rigplane.core.radio_protocol import RelativeVfoFrequencyWritable
from rigplane.runtime.radio import IcomRadio


class FakeCivTransport:
    def __init__(self):
        self.frames = []
        self.replies = []

    async def __call__(self, frame, *, wait_response=True, **kwargs):
        assert wait_response is True
        self.frames.append(frame)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(params=["IC-7300", "IC-7300MK2", "IC-705"])
def route(request, monkeypatch):
    radio = IcomRadio("127.0.0.1", model=request.param)
    wire = FakeCivTransport()
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_raw", wire)
    return radio, wire, radio.profile.civ_addr


def response(addr, payload):
    return parse_civ_frame(bytes.fromhex(f"FE FE E0 {addr:02X} {payload} FD"))


@pytest.mark.parametrize("selected", [False, True])
async def test_write_relative_frequency_frame_and_observed_readback(route, selected):
    radio, wire, addr = route
    selector = 0 if selected else 1
    wire.replies = [
        response(addr, "FB"),
        response(addr, f"25 {selector:02X} 00 40 07 14 00"),
    ]
    phases = []
    assert isinstance(radio, RelativeVfoFrequencyWritable)
    observed = await radio.write_relative_vfo_frequency(
        14_075_000,
        selected=selected,
        after_ack=lambda: phases.append(("ack", len(wire.frames))),
        after_readback=lambda: phases.append(("readback", len(wire.frames))),
    )
    assert observed == 14_074_000
    assert phases == [("ack", 1), ("readback", 2)]
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 25 {selector:02X} 00 50 07 14 00 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 {selector:02X} FD"),
    ]


@pytest.mark.parametrize(
    "reply,error",
    [
        ("FA", CommandRejectedError),
        ("25 01 00 40 07 14 00", CommandError),
        (None, CommandError),
        ("timeout", TimeoutError),
    ],
)
async def test_write_relative_frequency_requires_positive_ack(route, reply, error):
    radio, wire, addr = route
    if reply == "timeout":
        wire.replies = [TimeoutError("timeout")]
    else:
        wire.replies = [response(addr, reply) if reply is not None else None]
    phases = []
    with pytest.raises(error):
        await radio.write_relative_vfo_frequency(
            14_074_000, selected=False, after_ack=lambda: phases.append("ack")
        )
    assert len(wire.frames) == 1
    assert phases == []


@pytest.mark.parametrize("selected", [False, True])
async def test_write_relative_frequency_rejects_wrong_selector(route, selected):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, f"25 {1 if selected else 0:02X} 00 40 07 14 00"),
    ]
    with pytest.raises(CommandError, match="selector mismatch"):
        await radio.write_relative_vfo_frequency(14_074_000, selected=selected)


@pytest.mark.parametrize("phase", ["ack", "readback"])
async def test_write_relative_frequency_hook_failure_stops_transaction(route, phase):
    radio, wire, addr = route
    wire.replies = [response(addr, "FB"), None]

    def stale():
        raise CommandError("target changed")

    with pytest.raises(CommandError, match="target changed"):
        await radio.write_relative_vfo_frequency(
            14_074_000,
            selected=False,
            **{f"after_{phase}": stale},
        )
    assert len(wire.frames) == (1 if phase == "ack" else 2)


async def test_write_relative_frequency_no_readback_is_failure(route):
    radio, wire, addr = route
    wire.replies = [response(addr, "FB"), None]
    with pytest.raises(CommandError, match="readback unavailable"):
        await radio.write_relative_vfo_frequency(14_074_000, selected=False)


@pytest.mark.parametrize("missing", ["set_unselected_freq", "get_unselected_freq"])
async def test_write_relative_frequency_missing_command_refuses_before_io(
    route, missing
):
    radio, wire, _ = route
    cmd_map = radio.profile.command_map
    radio._profile = replace(
        radio.profile,
        command_map=CommandMap(
            {key: cmd_map.get(key) for key in cmd_map if key != missing}
        ),
    )
    with pytest.raises(CommandError, match="unavailable"):
        await radio.write_relative_vfo_frequency(14_074_000, selected=False)
    assert wire.frames == []


@pytest.mark.parametrize(
    "changes",
    [
        {"receiver_count": 2},
        {"vfo_scheme": "main_sub"},
        {"vfo_readback": "none"},
        {"command_map": None},
    ],
)
async def test_write_relative_frequency_requires_profile_data(route, changes):
    radio, wire, _ = route
    radio._profile = replace(radio.profile, **changes)
    with pytest.raises(CommandError, match="unavailable"):
        await radio.write_relative_vfo_frequency(14_074_000, selected=False)
    assert wire.frames == []


async def test_write_relative_frequency_does_not_gate_by_profile_id(route):
    radio, wire, addr = route
    radio._profile = replace(radio.profile, id="arbitrary_profile_id")
    wire.replies = [response(addr, "FB"), response(addr, "25 01 00 40 07 14 00")]
    observed = await radio.write_relative_vfo_frequency(14_074_000, selected=False)
    assert observed == 14_074_000
