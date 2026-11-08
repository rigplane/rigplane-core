import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedSubscription,
    ScopeWireFrame,
    Subscription,
    View,
)
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    encode_scope_frame_v2,
    decode_scope_subscription,
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
        expected = json.loads(path.read_text())
        wire = path.with_suffix(".bin").read_bytes()
        actual = decode_scope_frame_v2(wire)
        mapping = asdict(actual)
        mapping["pixels"] = list(actual.pixels) if actual.pixels is not None else None
        assert mapping == expected
        assert module.decode(wire) == expected
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
