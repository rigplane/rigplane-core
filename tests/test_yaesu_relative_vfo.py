"""Native split-role CAT writes keep MAIN RX intact; no selector writes."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import (
    CatCommandRejected,
    CatTimeoutError,
)
from rigplane.core.exceptions import CommandError, CommandRejectedError
from rigplane.core.types import Mode


class _Refusal(Exception):
    """Sentinel before_set refusal; must reach the caller unchanged."""


class RelativePeer:
    def __init__(self):
        self.vs = 0
        self.modes = {0: "2", 1: "1"}
        self.freqs = {0: 14_074_000, 1: 14_080_000}
        self.filters = {0: "03", 1: "19"}
        self.writes = []
        self.queries = []
        self.reject = None
        self.ignore = None
        self.garble = {}
        self.fail_query = {}
        self.vs_script = None
        self.hold_query = None
        self.allowed_external = set()

    async def query(self, frame, **kwargs):
        self.queries.append(frame)
        if frame in self.fail_query:
            raise self.fail_query[frame]
        if self.hold_query is not None and self.hold_query[0] == frame:
            gate = self.hold_query[1]
            self.hold_query = None
            await gate.wait()
        if frame in self.garble:
            return self.garble[frame]
        if frame == "VS;":
            if self.vs_script:
                return f"VS{self.vs_script.pop(0)}"
            return f"VS{self.vs}"
        if frame in ("MD0;", "MD1;"):
            return frame[:3] + self.modes[int(frame[2])]
        if frame in ("FA;", "FB;"):
            return f"{frame[:2]}{self.freqs[0 if frame == 'FA;' else 1]:09d}"
        if frame in ("SH0;", "SH1;"):
            return frame[:3] + "0" + self.filters[int(frame[2])]
        raise AssertionError(frame)

    async def write(self, frame, **kwargs):
        if kwargs.get("is_current") is not None:
            assert kwargs["is_current"]()
        if frame in self.allowed_external:
            self.writes.append(frame)
            return
        self.writes.append(frame)
        if frame == self.reject:
            raise CatCommandRejected("?;")
        if frame == self.ignore:
            return
        if frame.startswith(("MD0", "MD1")):
            self.modes[int(frame[2])] = frame[3]
        elif frame.startswith(("FA", "FB")):
            self.freqs[0 if frame.startswith("FA") else 1] = int(frame[2:-1])
        else:
            raise AssertionError(f"selector, swap, filter or RF write: {frame}")


def relative_radio():
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    peer = RelativePeer()
    radio._transport.query = AsyncMock(side_effect=peer.query)
    radio._transport.write = AsyncMock(side_effect=peer.write)
    return radio, peer


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,receiver", [(True, 0), (False, 1)])
async def test_relative_read_uses_native_receiver_without_selection(selected, receiver):
    radio, peer = relative_radio()
    peer.modes[receiver] = "C"
    snapshot = deepcopy((peer.freqs, peer.modes, peer.filters, peer.vs))
    observed = await radio.read_relative_vfo(selected=selected)
    assert observed.freq_hz == peer.freqs[receiver]
    assert observed.mode == "USB"
    assert observed.data_mode == 1
    assert observed.filter_num is None
    assert (peer.freqs, peer.modes, peer.filters, peer.vs) == snapshot
    assert peer.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,packet,code",
    [(Mode.USB, True, "C"), (Mode.LSB, True, "8"), (Mode.USB, False, "2")],
)
async def test_relative_mode_writes_only_sub_and_confirms(mode, packet, code):
    radio, peer = relative_radio()
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    filters = dict(peer.filters)
    observed = await radio.write_relative_vfo_mode(mode, selected=False, data=packet)
    assert observed.mode == mode.name
    assert observed.data_mode == int(packet)
    assert peer.modes[1] == code
    assert peer.writes == [f"MD1{code};"]
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main
    assert peer.filters == filters
    assert peer.vs == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["reject", "ignore"])
async def test_relative_packet_failure_is_not_success_and_preserves_main(behavior):
    radio, peer = relative_radio()
    setattr(peer, behavior, "MD1C;")
    snapshot = deepcopy((peer.freqs, peer.modes, peer.filters, peer.vs))
    with pytest.raises(CommandRejectedError if behavior == "reject" else CommandError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
    assert peer.writes == ["MD1C;"]
    assert (peer.freqs, peer.modes, peer.filters, peer.vs) == snapshot


@pytest.mark.asyncio
async def test_relative_frequency_confirms_sub_preserving_main():
    radio, peer = relative_radio()
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    events = []
    observed = await radio.write_relative_vfo_frequency(
        14_080_500,
        selected=False,
        before_set=lambda: events.append("before"),
        after_ack=lambda: events.append("ack"),
        after_readback=lambda: events.append("readback"),
    )
    assert observed == peer.freqs[1] == 14_080_500
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main
    assert peer.writes == ["FB014080500;"]
    assert events[0] == "before" and events[-2:] == ["ack", "readback"]
    assert peer.vs == 0


@pytest.mark.asyncio
async def test_unsupported_icom_filter_bank_refuses_before_write():
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(
            Mode.USB, selected=False, data=True, filter_width=2
        )
    assert peer.writes == []


# -- Omitted DATA is preserved from the addressed receiver -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected,receiver,current,code,packet",
    [
        (False, 1, "C", "C", 1),
        (False, 1, "2", "2", 0),
        (True, 0, "C", "C", 1),
    ],
)
async def test_relative_mode_write_preserves_omitted_data(
    selected, receiver, current, code, packet
):
    radio, peer = relative_radio()
    peer.modes[receiver] = current
    observed = await radio.write_relative_vfo_mode(Mode.USB, selected=selected)
    assert observed.mode == "USB"
    assert observed.data_mode == packet
    assert peer.writes == [f"MD{receiver}{code};"]
    assert observed.filter_num is None


# -- Frequency write failures: NAK and silent no-op ---------------------------


@pytest.mark.asyncio
async def test_relative_frequency_nak_stops_before_success_callbacks():
    radio, peer = relative_radio()
    peer.reject = "FB014080500;"
    events = []
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    with pytest.raises(CommandRejectedError):
        await radio.write_relative_vfo_frequency(
            14_080_500,
            selected=False,
            before_set=lambda: events.append("before"),
            after_ack=lambda: events.append("ack"),
            after_readback=lambda: events.append("readback"),
        )
    assert events == ["before", "before"]
    assert peer.writes == ["FB014080500;"]
    assert peer.freqs[1] == 14_080_000
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main


@pytest.mark.asyncio
async def test_relative_frequency_noop_fails_confirm_without_success_callbacks():
    radio, peer = relative_radio()
    peer.ignore = "FB014080500;"
    events = []
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    with pytest.raises(CommandError) as excinfo:
        await radio.write_relative_vfo_frequency(
            14_080_500,
            selected=False,
            before_set=lambda: events.append("before"),
            after_ack=lambda: events.append("ack"),
            after_readback=lambda: events.append("readback"),
        )
    assert not isinstance(excinfo.value, CommandRejectedError)
    assert events == ["before", "before", "ack"]
    assert peer.writes == ["FB014080500;"]
    assert peer.freqs[1] == 14_080_000
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main


# -- before_set refusal stops before any SET ---------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["mode", "frequency"])
async def test_relative_before_set_refusal_prevents_any_set(api):
    radio, peer = relative_radio()

    def refuse():
        raise _Refusal("stale target")

    if api == "mode":
        call = radio.write_relative_vfo_mode(
            Mode.USB, selected=False, data=True, before_set=refuse
        )
    else:
        call = radio.write_relative_vfo_frequency(
            14_080_500, selected=False, before_set=refuse
        )
    with pytest.raises(_Refusal):
        await call
    assert peer.writes == []


# -- Invalid inputs are refused before any CAT I/O ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [1, None, "main"])
async def test_relative_read_rejects_non_boolean_selection(selected):
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.read_relative_vfo(selected=selected)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_frequency_rejects_non_boolean_selection():
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_frequency(14_080_500, selected=1)
    assert peer.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, 99, 0x06])
async def test_relative_mode_rejects_unusable_mode_values(mode):
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(mode, selected=False)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_mode_rejects_non_boolean_data():
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=1)
    assert peer.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("freq", [True, 14_080_500.5, 1_000, 500_000_001])
async def test_relative_frequency_rejects_out_of_band_values(freq):
    radio, peer = relative_radio()
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_frequency(freq, selected=False)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_refuses_profile_without_shared_dual_rx():
    radio, peer = relative_radio()
    radio._config = replace(radio._config, vfo_scheme="ab")
    with pytest.raises(CommandError):
        await radio.read_relative_vfo(selected=True)
    assert peer.queries == []
    assert peer.writes == []


# -- SUB selected (VS1): refuse without changing the selector ----------------


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["read", "mode", "frequency"])
async def test_relative_refuses_when_sub_selected_without_touching_selector(api):
    radio, peer = relative_radio()
    peer.vs = 1
    with pytest.raises(CommandError):
        if api == "read":
            await radio.read_relative_vfo(selected=True)
        elif api == "mode":
            await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
        else:
            await radio.write_relative_vfo_frequency(14_080_500, selected=False)
    assert peer.writes == []
    assert peer.vs == 1


# -- Malformed native readbacks map to the CommandError family ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame,garbled",
    [("FA;", "FA01407"), ("MD0;", "MD0G"), ("VS;", "VSY")],
)
async def test_relative_read_maps_malformed_native_readback_to_command_error(
    frame, garbled
):
    radio, peer = relative_radio()
    peer.garble[frame] = garbled
    with pytest.raises(CommandError) as excinfo:
        await radio.read_relative_vfo(selected=True)
    assert not isinstance(excinfo.value, CommandRejectedError)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_read_maps_radio_nak_to_command_rejected():
    radio, peer = relative_radio()
    peer.fail_query["VS;"] = CatCommandRejected("?;", command="VS;")
    with pytest.raises(CommandRejectedError):
        await radio.read_relative_vfo(selected=True)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_read_leaves_transport_failures_unmapped():
    radio, peer = relative_radio()
    peer.fail_query["FA;"] = CatTimeoutError("read timed out", command="FA;")
    with pytest.raises(CatTimeoutError):
        await radio.read_relative_vfo(selected=True)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_mode_write_maps_malformed_verification_readback():
    radio, peer = relative_radio()
    peer.garble["MD1;"] = "MD1Z"
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    with pytest.raises(CommandError) as excinfo:
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
    assert not isinstance(excinfo.value, CommandRejectedError)
    assert peer.writes == ["MD1C;"]
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,frame", [(False, "MD1;"), (True, "MD0;")])
async def test_relative_mode_write_maps_radio_nak_verification_to_command_rejected(
    selected, frame
):
    radio, peer = relative_radio()
    peer.fail_query[frame] = CatCommandRejected("?;", command=frame)
    addressed = 0 if selected else 1
    opposite = 1 - addressed
    untouched = (peer.freqs[opposite], peer.modes[opposite], peer.filters[opposite])
    with pytest.raises(CommandRejectedError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=selected, data=True)
    assert peer.writes == [f"MD{addressed}C;"]
    assert (peer.freqs[opposite], peer.modes[opposite], peer.filters[opposite]) == (
        untouched
    )
    assert peer.vs == 0


@pytest.mark.asyncio
async def test_relative_mode_write_leaves_verification_transport_failure_unmapped():
    radio, peer = relative_radio()
    peer.fail_query["MD1;"] = CatTimeoutError("read timed out", command="MD1;")
    with pytest.raises(CatTimeoutError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
    assert peer.writes == ["MD1C;"]


@pytest.mark.asyncio
async def test_relative_mode_write_maps_malformed_data_probe_readback():
    radio, peer = relative_radio()
    peer.garble["MD1;"] = "MD1Z"
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False)
    assert peer.writes == []


@pytest.mark.asyncio
async def test_relative_frequency_maps_malformed_confirm_readback():
    radio, peer = relative_radio()
    peer.garble["FB;"] = "FB0014"
    main = (peer.freqs[0], peer.modes[0], peer.filters[0])
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_frequency(14_080_500, selected=False)
    assert peer.writes == ["FB014080500;"]
    assert (peer.freqs[0], peer.modes[0], peer.filters[0]) == main


# -- VS changes during a relative operation refuse it -------------------------


@pytest.mark.asyncio
async def test_relative_read_detects_selection_change_during_snapshot():
    radio, peer = relative_radio()
    peer.vs_script = ["0", "1"]
    snapshot = deepcopy((peer.freqs, peer.modes, peer.filters, peer.vs))
    with pytest.raises(CommandError):
        await radio.read_relative_vfo(selected=False)
    assert peer.writes == []
    assert (peer.freqs, peer.modes, peer.filters, peer.vs) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["mode", "frequency"])
async def test_relative_write_refuses_when_selection_changed_before_set(api):
    radio, peer = relative_radio()
    peer.vs_script = ["0", "1"]
    with pytest.raises(CommandError):
        if api == "mode":
            await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
        else:
            await radio.write_relative_vfo_frequency(14_080_500, selected=False)
    assert peer.writes == []


# -- Event-controlled serialization against competing VFO writers ------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "competitor,frame",
    [
        (lambda radio: radio.set_vfo_select(1), "VS1;"),
        (lambda radio: radio.set_freq(14_100_000, receiver=1), "FB014100000;"),
        (lambda radio: radio.swap_main_sub(), "SV;"),
        (lambda radio: radio.equalize_main_sub(), "AB;"),
    ],
    ids=["selector", "sub-frequency", "swap", "equalize"],
)
async def test_relative_frequency_write_serializes_against_vfo_writers(
    competitor, frame
):
    radio, peer = relative_radio()
    peer.allowed_external = {frame}
    entered = asyncio.Event()
    hold = asyncio.Event()
    peer.hold_query = ("FB;", hold)

    async def relative_write():
        return await radio.write_relative_vfo_frequency(
            14_080_500, selected=False, before_set=entered.set
        )

    task = asyncio.create_task(relative_write())
    await entered.wait()
    other = asyncio.create_task(competitor(radio))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not other.done()
    hold.set()
    await task
    await other
    assert peer.writes == ["FB014080500;", frame]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "competitor,frame",
    [
        (lambda radio: radio.set_vfo_select(1), "VS1;"),
        (lambda radio: radio.swap_main_sub(), "SV;"),
    ],
    ids=["selector", "swap"],
)
async def test_relative_mode_write_serializes_against_vfo_writers(competitor, frame):
    radio, peer = relative_radio()
    peer.allowed_external = {frame}
    entered = asyncio.Event()
    hold = asyncio.Event()
    peer.hold_query = ("MD1;", hold)

    async def relative_write():
        return await radio.write_relative_vfo_mode(
            Mode.USB, selected=False, data=True, before_set=entered.set
        )

    task = asyncio.create_task(relative_write())
    await entered.wait()
    other = asyncio.create_task(competitor(radio))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not other.done()
    hold.set()
    await task
    await other
    assert peer.writes == ["MD1C;", frame]
