import json
from pathlib import Path
import re

import pytest

from rigplane.audio._usb_capture import _validate_bundle
from rigplane.audio._usb_redact import _USB_SERIAL_ID, redact_bundle

_FIXTURE = Path(__file__).parent / "fixtures/usb_audio/windows/w1-gtr7-ic7300.json"


@pytest.mark.parametrize("redact_again", [False, True])
def test_recorded_capture_valid_and_redacted(redact_again: bool) -> None:
    bundle = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    if redact_again:
        bundle = redact_bundle(bundle, key=b"recorded-fixture-validation")

    assert _validate_bundle(bundle) is None
    assert '"hwid":' not in json.dumps(bundle)
    serials = []
    for session in bundle["sessions"]:
        raw = session["raw"]
        serials.extend(
            port["serial_number"]
            for port in raw["serial_ports"]
            if port["serial_number"]
        )
        for node in raw["windows"]["devnodes"]:
            for field in ("instance_id", "parent_id"):
                if match := _USB_SERIAL_ID.fullmatch(node[field]):
                    serials.append(match[1])

    assert serials
    assert all(re.fullmatch(r"R[A-Z2-7]{10}", serial) for serial in serials)
