"""Validate completed mutation evidence, not merely pytest's nonzero status."""

import sys
import xml.etree.ElementTree as ET

kind, path = sys.argv[1:]
root = ET.parse(path).getroot()
cases = root.findall(".//testcase")
assert len(cases) == 22, len(cases)
assert not root.findall(".//error")
assert not root.findall(".//skipped")
phases = {
    f"test_cancelled_exchange_quarantines_before_close_barrier[{phase}]"
    for phase in (
        "command-read", "query-read", "command-stale", "query-stale",
        "command-resync", "command-write",
    )
}
replacements = {
    f"test_old_exchange_interruption_does_not_retire_replacement[{cause}]"
    for cause in ("cancel", "eof", "oserror", "timeout")
}
real_barriers = {
    f"test_cancelled_lifecycle_preserves_real_stream_close_future[{operation}]"
    for operation in ("close", "connect")
}
expected = {
    "control": set(),
    "positive": set(),
    "m1": phases | real_barriers | {"test_delayed_cancelled_rprt_cannot_complete_next_command"},
    "m2": replacements,
    "m3": phases | real_barriers,
    "m4": phases | replacements,
    "m5": real_barriers,
}[kind]
failed = {case.attrib["name"] for case in cases if case.find("failure") is not None}
assert failed == expected, (kind, failed, expected)
for case in cases:
    failure = case.find("failure")
    if failure is not None:
        message = failure.attrib.get("message", "")
        assert "assert" in message or "DID NOT RAISE" in message, message
        if kind == "m5":
            assert "caller cancellation poisoned shared close Future" in message, message
print(f"{kind}: exact {len(failed)} assertion failures; {len(cases) - len(failed)} controls passed", flush=True)
