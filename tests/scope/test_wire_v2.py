import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedSubscription,
    ScopeTarget,
    ScopeWireFrame,
    Subscription,
    View,
)
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    encode_scope_frame_v2,
    decode_scope_subscription,
    decode_scope_target,
    decode_scope_unsubscribe,
    encode_scope_subscription,
    encode_scope_granted,
    decode_scope_granted,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "scope-wire"


def frame(**changes):
    base = ScopeWireFrame(
        "radio",
        "radio:main",
        0,
        100,
        200,
        110,
        190,
        4,
        b"\x00\x7f\x80\xff",
        False,
        1,
        2,
        123,
    )
    return replace(base, **changes)


def test_roundtrip_and_source_identity():
    original = frame()
    encoded = encode_scope_frame_v2(original)
    assert encoded[0] == 2
    assert decode_scope_frame_v2(encoded) == original
    assert (
        decode_scope_frame_v2(
            encode_scope_frame_v2(frame(source_id="radio:sub"))
        ).source_id
        == "radio:sub"
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(source="unknown"),
        dict(source_id="é"),
        dict(receiver=65535),
        dict(width=3),
        dict(pixels=bytearray(4)),
        dict(out_of_range=1),
        dict(epoch=-1),
        dict(seq=2**32),
        dict(sent_at_ms=True),
        dict(coverage_start_hz=99),
    ],
)
def test_reject_invalid_frames(changes):
    with pytest.raises(ValueError):
        frame(**changes)


def test_reject_malformed_wire():
    encoded = encode_scope_frame_v2(frame())
    for bad in (
        b"",
        encoded[:10],
        encoded[:-1],
        encoded + b"\0",
        b"\x01" + encoded[1:],
    ):
        with pytest.raises(ValueError):
            decode_scope_frame_v2(bad)


def test_control_roundtrip():
    for view in (View(100, 200), FollowView("vfo", 100)):
        request = Subscription("audio:rx", None, view, 1024, 0)
        assert decode_scope_subscription(encode_scope_subscription(request)) == request
    granted = GrantedSubscription(
        "radio:main", 0, View(100, 200), Coverage(110, 190), 1024, 10
    )
    assert decode_scope_granted(encode_scope_granted(granted)) == granted
    with pytest.raises(ValueError):
        decode_scope_subscription(
            '{"type":"subscribe","sourceId":"radio","receiver":0,"view":{"startHz":0,"endHz":100},"width":true,"maxFps":10}'
        )


def test_golden_fixtures_and_standalone_decoder():
    spec = importlib.util.spec_from_file_location(
        "scope_reference_decoder", FIXTURES / "reference_decoder.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = sorted(FIXTURES.glob("*.json"))
    assert len(names) == 8
    for path in names:
        expected = parse_fixture_json(path.read_text())
        wire = path.with_suffix(".bin").read_bytes()
        actual = decode_scope_frame_v2(wire)
        assert actual == expected
        decoded = module.decode(wire)
        reference = ScopeWireFrame(
            decoded["source"],
            decoded["source_id"],
            decoded["receiver"],
            decoded["view_start_hz"],
            decoded["view_end_hz"],
            decoded["coverage_start_hz"],
            decoded["coverage_end_hz"],
            decoded["width"],
            None if decoded["pixels"] is None else bytes(decoded["pixels"]),
            decoded["out_of_range"],
            decoded["epoch"],
            decoded["seq"],
            decoded["sent_at_ms"],
        )
        assert reference == expected
        assert encode_scope_frame_v2(actual) == wire


def test_follow_view_control_matches_r1_contract():
    request = Subscription("radio:main", 0, FollowView("vfo", 5000), 1024, 10)
    assert json.loads(encode_scope_subscription(request))["view"] == {
        "follow": "vfo",
        "spanHz": 5000,
    }
    assert (
        decode_scope_subscription(
            json.dumps(
                {
                    "type": "subscribe",
                    "sourceId": "radio:main",
                    "receiver": 0,
                    "view": {"follow": "vfo", "spanHz": 5000},
                    "width": 1024,
                    "maxFps": 10,
                }
            )
        )
        == request
    )


@pytest.mark.parametrize("offset,value", [(1, 3), (2, 4), (3, 0)])
def test_decoder_rejects_invalid_header_fields(offset, value):
    wire = bytearray(encode_scope_frame_v2(frame()))
    wire[offset] = value
    with pytest.raises(ValueError):
        decode_scope_frame_v2(bytes(wire))


def test_fixture_generator_is_reproducible(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "scope_fixture_generator", FIXTURES / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.generate(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {
        path.name for path in FIXTURES.iterdir() if path.suffix in (".bin", ".json")
    }
    for generated in tmp_path.iterdir():
        assert generated.read_bytes() == (FIXTURES / generated.name).read_bytes()


def test_reference_decoder_rejects_malformed_messages():
    spec = importlib.util.spec_from_file_location(
        "scope_reference_decoder", FIXTURES / "reference_decoder.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    encoded = encode_scope_frame_v2(frame())
    invalid = [b"", encoded[:10], encoded[:-1], encoded + b"\0"]
    for offset, value in ((0, 1), (1, 3), (2, 4), (3, 0), (56, 255)):
        wire = bytearray(encoded)
        wire[offset] = value
        invalid.append(bytes(wire))
    for wire in invalid:
        with pytest.raises(ValueError):
            module.decode(wire)
        with pytest.raises(ValueError):
            decode_scope_frame_v2(wire)


def parse_fixture_json(text: str) -> ScopeWireFrame:
    fields = json.loads(text)
    pixels = fields["pixels"]
    return ScopeWireFrame(
        fields["source"],
        fields["source_id"],
        fields["receiver"],
        fields["view_start_hz"],
        fields["view_end_hz"],
        fields["coverage_start_hz"],
        fields["coverage_end_hz"],
        fields["width"],
        None if pixels is None else bytes(pixels),
        fields["out_of_range"],
        fields["epoch"],
        fields["seq"],
        fields["sent_at_ms"],
    )


def test_targeted_scope_unsubscribe_contract():
    target = ScopeTarget("radio", 1)
    text = '{"type":"unsubscribe","sourceId":"radio","receiver":1}'
    assert decode_scope_unsubscribe(text) == target
    assert decode_scope_target(text) == target
    assert decode_scope_unsubscribe('{"type":"unsubscribe"}') is None
    assert decode_scope_unsubscribe(
        '{"type":"unsubscribe","sourceId":"audio_fft","receiver":null}'
    ) == ScopeTarget("audio_fft", None)


@pytest.mark.parametrize(
    "text",
    [
        '{"type":"unsubscribe","sourceId":"radio"}',
        '{"type":"unsubscribe","receiver":0}',
        '{"type":"unsubscribe","sourceId":"radio","receiver":true}',
        '{"type":"unsubscribe","sourceId":"radio","receiver":0,"extra":1}',
        '{"type":"unsubscribe","sourceId":"","receiver":0}',
    ],
)
def test_invalid_targeted_unsubscribe_is_rejected(text):
    with pytest.raises(ValueError):
        decode_scope_unsubscribe(text)
