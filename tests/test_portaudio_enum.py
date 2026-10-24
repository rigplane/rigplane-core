from dataclasses import FrozenInstanceError, asdict
import json
from types import SimpleNamespace

import pytest

from rigplane.audio._portaudio_enum import (
    PortAudioEnumeration,
    read_portaudio_enumeration,
)


def test_enumeration_records_every_field_and_roundtrips() -> None:
    hostapi = {
        "name": "Windows WASAPI",
        "devices": [0],
        "default_input_device": 0,
        "default_output_device": -1,
    }
    device = {
        "name": "USB Audio CODEC",
        "hostapi": 0,
        "max_input_channels": 2,
        "max_output_channels": 0,
        "default_samplerate": 48000.0,
        "default_low_input_latency": 0.01,
        "default_low_output_latency": 0.02,
        "default_high_input_latency": 0.1,
        "default_high_output_latency": 0.2,
    }
    sd = SimpleNamespace(
        query_hostapis=lambda: [hostapi], query_devices=lambda: [device]
    )
    result = read_portaudio_enumeration(sd)
    assert asdict(result.hostapis[0]) == {
        "index": 0,
        "name": "Windows WASAPI",
        "device_count": 1,
        "default_input": 0,
        "default_output": -1,
    }
    assert asdict(result.devices[0]) == {"index": 0, **device}
    assert result.generation == 0 and result.fresh is True
    assert (
        PortAudioEnumeration.from_json_dict(
            json.loads(json.dumps(result.to_json_dict()))
        )
        == result
    )
    assert not hasattr(result.devices[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.fresh = False
