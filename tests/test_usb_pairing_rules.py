"""Graph-category clause pins for the shared USB pairing rule."""

from dataclasses import replace
from pathlib import Path
import re

import pytest

from rigplane.audio._portaudio_enum import (
    HostApiRecord,
    PortAudioDeviceRecord,
    PortAudioEnumeration,
)
from rigplane.audio._usb_names import endpoint_name_key, mme_listed_matches
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.audio._usb_snapshot import (
    AudioEndpoint,
    SerialPortRef,
    Unavailable,
    UsbNode,
    UsbSnapshot,
)
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId


def _node(id, parent, kind="device", vid="08BB", pid="2901"):
    return UsbNode(id, parent, kind, vid, pid)


def _record(index, name, inputs=0, outputs=0, hostapi=0):
    return PortAudioDeviceRecord(
        index, name, hostapi, inputs, outputs, 48000.0, 0.01, 0.01, 0.1, 0.1
    )


def _snapshot(names=("Microphone (USB Audio CODEC )", "Speakers (USB Audio CODEC )")):
    return UsbSnapshot(
        "windows",
        (
            _node("root", None, "controller"),
            _node("root-hub", "root", "root_hub"),
            _node("H", "root-hub", "hub"),
            _node("S", "H", vid="10C4", pid="EA60"),
            _node("C", "H"),
        ),
        (SerialPortRef("COM3", "S"),),
        (
            AudioEndpoint("capture", "C", "capture", names[0]),
            AudioEndpoint("render", "C", "render", names[1]),
        ),
        PortAudioEnumeration(
            (HostApiRecord(0, "MME", 2, 1, 5),),
            (_record(1, names[0], inputs=2), _record(5, names[1], outputs=2)),
            0,
            True,
        ),
        (),
    )


def _assert_refused(snapshot, reason, port="COM3", identity=None):
    result = pair(snapshot, port, identity)
    assert isinstance(result, PairRefused)
    assert result.reason == reason
    assert result.evidence
    return result


def _assert_paired(snapshot, port="COM3", indices=(1, 5), identity=None):
    result = pair(snapshot, port, identity)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index) == indices
    return result


def test_s0_available_s1_unique_port_s2_sibling_codec_s5_first_api():
    result = _assert_paired(_snapshot(), "com3")
    assert (result.hostapi, result.rx_id, result.tx_id, result.codec_node) == (
        "MME",
        "capture",
        "render",
        "C",
    )


@pytest.mark.parametrize("part", ["usb", "windows", "serial_ports", "portaudio"])
def test_s0_unavailable_precedes_missing_serial_and_keeps_candidates(part):
    snapshot = replace(_snapshot(), unavailable=(Unavailable(part, "reader failed"),))
    result = _assert_refused(snapshot, "unavailable", "COM999")
    assert len(result.candidates) == 2
    assert not any(candidate.near_radio for candidate in result.candidates)


@pytest.mark.parametrize(
    "ports",
    [
        (),
        (SerialPortRef("COM3", None),),
        (SerialPortRef("COM3", "S"), SerialPortRef("com3", "S")),
    ],
)
def test_s1_port_must_have_exactly_one_reference_with_node(ports):
    _assert_refused(replace(_snapshot(), serial_ports=ports), "serial_not_found")


def test_s1_does_not_match_a_port_prefix():
    _assert_refused(_snapshot(), "serial_not_found", "COM30")


def test_s2_nested_hub_ic7610_shape():
    snapshot = _snapshot()
    nodes = tuple(
        replace(node, parent="nested") if node.id == "C" else node
        for node in snapshot.usb
    )
    _assert_paired(replace(snapshot, usb=nodes + (_node("nested", "H", "hub"),)))


@pytest.mark.parametrize(
    "vid,pid,extra",
    [("08BB", "2901", False), ("0D8C", "0016", True)],
    ids=["ic705", "ftx1"],
)
def test_s2_recorded_sibling_shapes_allow_non_audio_device(vid, pid, extra):
    snapshot = _snapshot()
    nodes = tuple(
        replace(node, vid=vid, pid=pid) if node.id == "C" else node
        for node in snapshot.usb
    )
    if extra:
        nodes += (_node("extra", "H", vid="26AA", pid="0030"),)
    _assert_paired(
        replace(snapshot, usb=nodes),
        identity=UsbAudioIdentity(frozenset({UsbFunctionId(vid, pid)})),
    )


@pytest.mark.parametrize("parent", ["root-hub", "root", None, "absent"])
def test_s2_serial_parent_must_be_non_root_hub(parent):
    snapshot = _snapshot()
    nodes = tuple(
        replace(node, parent=parent) if node.id == "S" else node
        for node in snapshot.usb
    )
    _assert_refused(replace(snapshot, usb=nodes), "containment_unproven")


def test_s2_codec_two_hubs_deeper_is_outside_boundary():
    snapshot = _snapshot()
    nodes = tuple(
        replace(node, parent="deep") if node.id == "C" else node
        for node in snapshot.usb
    )
    nodes += (_node("nested", "H", "hub"), _node("deep", "nested", "hub"))
    _assert_refused(replace(snapshot, usb=nodes), "no_codec")


def test_s2_two_serial_devices_are_ambiguous_but_two_ports_on_one_device_are_not():
    snapshot = _snapshot()
    ports = snapshot.serial_ports + (SerialPortRef("COM4", "S"),)
    _assert_paired(replace(snapshot, serial_ports=ports))
    _assert_refused(
        replace(
            snapshot,
            usb=snapshot.usb + (_node("S2", "H"),),
            serial_ports=ports + (SerialPortRef("COM5", "S2"),),
        ),
        "serial_ambiguous",
    )


def test_s2_two_full_codecs_are_ambiguous_capture_only_webcam_is_not():
    snapshot = _snapshot()
    nodes = snapshot.usb + (_node("webcam", "H"),)
    endpoints = snapshot.endpoints + (
        AudioEndpoint("webcam-in", "webcam", "capture", "Webcam"),
    )
    _assert_paired(replace(snapshot, usb=nodes, endpoints=endpoints))
    endpoints += (AudioEndpoint("webcam-out", "webcam", "render", "Webcam"),)
    _assert_refused(
        replace(snapshot, usb=nodes, endpoints=endpoints), "codec_ambiguous"
    )


def test_s2_capture_only_device_is_not_a_codec():
    snapshot = _snapshot()
    _assert_refused(replace(snapshot, endpoints=snapshot.endpoints[:1]), "no_codec")


@pytest.mark.parametrize(
    "node_id,parent", [("H", "absent"), ("H", "C"), ("root-hub", "H"), ("C", None)]
)
def test_s2_missing_parent_or_cycle_is_incomplete(node_id, parent):
    snapshot = _snapshot()
    nodes = tuple(
        replace(node, parent=parent) if node.id == node_id else node
        for node in snapshot.usb
    )
    _assert_refused(replace(snapshot, usb=nodes), "topology_incomplete")


def test_s2_duplicate_node_ids_are_incomplete():
    snapshot = _snapshot()
    _assert_refused(
        replace(snapshot, usb=snapshot.usb + (replace(snapshot.usb[-1], id="c"),)),
        "topology_incomplete",
    )


def test_s2_endpoint_parent_chain_is_checked():
    snapshot = _snapshot()
    nodes = snapshot.usb + (
        _node("media", "missing", "media"),
        _node("capture", "media", "endpoint"),
    )
    _assert_refused(replace(snapshot, usb=nodes), "topology_incomplete")


@pytest.mark.parametrize(
    "names",
    [
        ("Mikrofon (USB Audio CODEC)", "Lautsprecher (USB Audio CODEC)"),
        ("Mikrofoni", "Kaiuttimet"),
        ("マイク", "スピーカー"),
        ("Microphone (2- USB Audio CODEC)", "Speakers (2- USB Audio CODEC)"),
        ("My renamed input", "My renamed output"),
        ("Line (USB Audio CODEC)", "Line (USB Audio CODEC)"),
    ],
)
def test_s4_localized_prefixed_renamed_and_line_endpoints_pair_on_mme(names):
    _assert_paired(_snapshot(names))


def test_s4_wdm_spacing_normalization_and_reused_product_helper():
    snapshot = _snapshot()
    devices = tuple(
        replace(record, name=record.name.replace(" )", ")"))
        for record in snapshot.portaudio.devices
    )
    _assert_paired(
        replace(snapshot, portaudio=replace(snapshot.portaudio, devices=devices))
    )
    assert endpoint_name_key(" Line ( USB Audio CODEC ) ") == "Line (USB Audio CODEC)"


def test_s4_mme_exactly_31_characters_is_the_only_prefix_match():
    names = (
        "Microphone (A very long audio endpoint name)",
        "Speakers (A very long audio endpoint name)",
    )
    snapshot = _snapshot(names)
    devices = tuple(
        replace(record, name=record.name[:31]) for record in snapshot.portaudio.devices
    )
    cut = replace(snapshot, portaudio=replace(snapshot.portaudio, devices=devices))
    _assert_paired(cut)
    assert mme_listed_matches(names[0][:31], names[0])
    assert not mme_listed_matches(names[0][:30], names[0])
    assert not mme_listed_matches(names[0][:32], names[0])
    assert mme_listed_matches(names[0], names[0])
    _assert_refused(
        replace(
            cut,
            portaudio=replace(
                cut.portaudio, hostapis=(HostApiRecord(0, "Windows WASAPI", 2, 1, 5),)
            ),
        ),
        "endpoint_names",
    )


def test_s4_two_radios_distinct_names_pair_each_port_identical_names_contest():
    first = _snapshot()
    second = _snapshot(
        ("Microphone (2- USB Audio CODEC )", "Speakers (2- USB Audio CODEC )")
    )
    nodes = first.usb + tuple(
        replace(node, id=node.id + "2", parent="root-hub" if node.id == "H" else "H2")
        for node in second.usb
        if node.id in {"H", "S", "C"}
    )
    endpoints = first.endpoints + tuple(
        replace(endpoint, os_id=endpoint.os_id + "2", owner="C2")
        for endpoint in second.endpoints
    )
    devices = first.portaudio.devices + tuple(
        replace(record, index=record.index + 10) for record in second.portaudio.devices
    )
    snapshot = replace(
        first,
        usb=nodes,
        serial_ports=first.serial_ports + (SerialPortRef("COM4", "S2"),),
        endpoints=endpoints,
        portaudio=replace(first.portaudio, devices=devices),
    )
    _assert_paired(snapshot)
    _assert_paired(snapshot, "COM4", (11, 15))
    endpoints = first.endpoints + tuple(
        replace(endpoint, os_id=endpoint.os_id + "2", owner="C2")
        for endpoint in first.endpoints
    )
    contested = _assert_refused(replace(snapshot, endpoints=endpoints), "contested")
    assert contested.wire_reason == "usb_ambiguous"
    assert contested.candidates[0].stable_id == "MME|name:Microphone (USB Audio CODEC )"


def test_s4_virtual_collision_skips_api_and_s5_uses_next_uncontested_api():
    snapshot = _snapshot()
    endpoints = snapshot.endpoints + (
        AudioEndpoint("virtual", "opaque:cable", "capture", snapshot.endpoints[0].name),
        AudioEndpoint("alternate", "C", "capture", "Alternate capture"),
    )
    apis = snapshot.portaudio.hostapis + (
        HostApiRecord(7, "Windows WASAPI", 2, 19, 17),
    )
    devices = snapshot.portaudio.devices + (
        _record(19, "Alternate capture", inputs=2, hostapi=7),
        _record(17, snapshot.endpoints[1].name, outputs=2, hostapi=7),
    )
    snapshot = replace(
        snapshot,
        endpoints=endpoints,
        portaudio=replace(snapshot.portaudio, hostapis=apis, devices=devices),
    )
    assert _assert_paired(snapshot, indices=(19, 17)).hostapi == "Windows WASAPI"
    _assert_refused(
        replace(snapshot, portaudio=replace(snapshot.portaudio, hostapis=apis[:1])),
        "contested",
    )


def test_s5_duplex_record_can_supply_both_directions():
    snapshot = _snapshot(("Duplex", "Duplex"))
    snapshot = replace(
        snapshot,
        portaudio=replace(snapshot.portaudio, devices=(_record(3, "Duplex", 2, 2),)),
    )
    _assert_paired(snapshot, indices=(3, 3))
    result = _assert_refused(snapshot, "serial_not_found", "COM9")
    assert [
        (candidate.direction, candidate.stable_id) for candidate in result.candidates
    ] == [("capture", "MME|capture"), ("render", "MME|render")]


@pytest.mark.parametrize("indices", [(0,), (1,), (0, 0, 1), (0, 1, 1)])
def test_s5_requires_exactly_one_input_and_output(indices):
    snapshot = _snapshot()
    devices = tuple(
        replace(snapshot.portaudio.devices[index], index=position)
        for position, index in enumerate(indices)
    )
    _assert_refused(
        replace(snapshot, portaudio=replace(snapshot.portaudio, devices=devices)),
        "endpoint_names",
    )


def test_s5_no_endpoint_matched_names_produces_no_candidates():
    snapshot = _snapshot()
    devices = tuple(
        replace(record, name="Unmatched mapper")
        for record in snapshot.portaudio.devices
    )
    result = _assert_refused(
        replace(snapshot, portaudio=replace(snapshot.portaudio, devices=devices)),
        "endpoint_names",
    )
    assert result.candidates == ()


def test_s5_partial_first_api_skips_to_first_complete_api_in_enumeration_order():
    snapshot = _snapshot()
    apis = (
        HostApiRecord(7, "First", 1, 10, -1),
        HostApiRecord(5, "Second", 2, 11, 12),
        HostApiRecord(0, "MME", 2, 1, 5),
    )
    devices = snapshot.portaudio.devices + (
        _record(10, snapshot.endpoints[0].name, inputs=2, hostapi=7),
        _record(11, snapshot.endpoints[0].name, inputs=2, hostapi=5),
        _record(12, snapshot.endpoints[1].name, outputs=2, hostapi=5),
    )
    result = _assert_paired(
        replace(
            snapshot,
            portaudio=replace(snapshot.portaudio, hostapis=apis, devices=devices),
        ),
        indices=(11, 12),
    )
    assert result.hostapi == "Second"


def test_s5_candidates_use_first_matched_api_and_near_radio_before_index():
    snapshot = _snapshot()
    endpoints = snapshot.endpoints + (
        AudioEndpoint("foreign", "opaque:cable", "capture", "Virtual"),
    )
    apis = (
        (HostApiRecord(9, "Empty", 1, 0, -1),)
        + snapshot.portaudio.hostapis
        + (HostApiRecord(3, "Later", 1, 30, -1),)
    )
    devices = (
        (_record(0, "Mapper", inputs=2, hostapi=9), _record(0, "Virtual", inputs=2))
        + snapshot.portaudio.devices
        + (_record(30, snapshot.endpoints[0].name, inputs=2, hostapi=3),)
    )
    result = _assert_refused(
        replace(
            snapshot,
            endpoints=endpoints,
            portaudio=replace(snapshot.portaudio, hostapis=apis, devices=devices),
        ),
        "codec_identity_mismatch",
        identity=UsbAudioIdentity(frozenset({UsbFunctionId("FFFF", "0000")})),
    )
    assert [
        (candidate.portaudio_index, candidate.near_radio)
        for candidate in result.candidates
    ] == [(1, True), (5, True), (0, False)]
    assert result.candidates[-1].stable_id == "MME|foreign"


def test_s2_residual_risk_shared_hub_foreign_headset_without_identity_pairs():
    # Accepted risk (a+b+c): absent radio codec, shared hub, one foreign
    # in+out device, and no second serial device to contradict containment.
    snapshot = _snapshot(("Headset microphone", "Headset speakers"))
    nodes = tuple(
        replace(node, vid="FFFF", pid="0001") if node.id == "C" else node
        for node in snapshot.usb
    )
    snapshot = replace(snapshot, usb=nodes)
    _assert_paired(snapshot)
    _assert_paired(snapshot, identity=UsbAudioIdentity(frozenset()))
    _assert_refused(
        snapshot,
        "codec_identity_mismatch",
        identity=UsbAudioIdentity(frozenset({UsbFunctionId("08BB", "2901")})),
    )


def test_s0_through_s5_each_rule_anchor_exists_exactly_once():
    source = Path("src/rigplane/audio/_usb_pairing.py").read_text()
    assert sorted(re.findall(r"# rule: (S[0-5])\b", source)) == [
        "S0",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
    ]
