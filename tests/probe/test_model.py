"""Probe file model: JSON contract for ``rigplane-probe-v1`` (MOR-4464 PR A)."""

from __future__ import annotations

import json

import pytest

from rigplane.probe.model import (
    SKIP_REASONS,
    CommandRow,
    IdentityReport,
    ProbeFile,
    RadioInfo,
    SkippedRead,
    Snapshot,
    ToolInfo,
    UnsolicitedRow,
)

SCHEMA = "rigplane-probe-v1"


def _sample() -> ProbeFile:
    return ProbeFile(
        tool=ToolInfo(
            rigplane_version="3.1.2",
            python="3.11.1",
            os="darwin",
            created_utc="2026-11-14T12:00:00+00:00",
        ),
        mode="read",
        radio=RadioInfo(
            profile="FT-991A",
            profile_sha256="ab" * 32,
            protocol="yaesu_cat",
            port_kind="usb",
            baud=38400,
            identity_reported=IdentityReport(raw="ID0670;", parsed="0670"),
            identity_matches_profile=True,
        ),
        private_reads_included=False,
        label="bench",
        commands=(
            CommandRow(
                name="get_freq",
                template="FA;",
                request_hex="46413b",
                request_text="FA;",
                reply_hex="464131343037343030303b",
                reply_text="FA14074000;",
                outcome="data",
                latency_ms=3.1,
                expected="present",
                note="",
            ),
            CommandRow(
                name="get_keyer_speed",
                template="KS;",
                request_hex="4b533b",
                request_text="KS;",
                reply_hex=None,
                reply_text=None,
                outcome="silent",
                latency_ms=1000.0,
                expected="absent",
                note="",
            ),
        ),
        skipped=(
            SkippedRead(
                name="get_memory_contents",
                request_hex=None,
                reason="private",
                detail="stored memory contents need --include-private",
            ),
        ),
        unsolicited=(UnsolicitedRow(t_ms=12.5, hex="41493b", text="AI;"),),
        snapshot=Snapshot(
            before={"get_freq": "FA14074000;"},
            after={"get_freq": "FA14074000;"},
            changed=[],
        ),
        checks=(),
    )


def test_round_trip_preserves_every_field() -> None:
    first = _sample()
    second = ProbeFile.from_json(first.to_json())
    assert second == first
    assert second.schema == SCHEMA


def test_json_shape_has_hex_and_text_per_row() -> None:
    document = json.loads(_sample().to_json())
    assert document["schema"] == SCHEMA
    assert document["mode"] == "read"
    assert document["checks"] == []
    row = document["commands"][0]
    assert row["class"] == "data"
    assert row["request_hex"] == "46413b"
    assert row["request_text"] == "FA;"
    assert row["reply_hex"] == "464131343037343030303b"
    assert row["reply_text"] == "FA14074000;"
    assert "attempts" not in row
    assert document["radio"]["profile_sha256"] == "ab" * 32
    assert document["unsolicited"][0] == {"t_ms": 12.5, "hex": "41493b", "text": "AI;"}
    assert document["snapshot"] == {
        "before": {"get_freq": "FA14074000;"},
        "after": {"get_freq": "FA14074000;"},
        "changed": [],
    }
    assert document["commands"][0]["expected"] == "present"
    assert document["commands"][1]["expected"] == "absent"
    assert document["skipped"] == [
        {
            "name": "get_memory_contents",
            "request_hex": None,
            "reason": "private",
            "detail": "stored memory contents need --include-private",
        }
    ]


def test_unknown_major_version_is_refused() -> None:
    document = json.loads(_sample().to_json())
    document["schema"] = "rigplane-probe-v2"
    with pytest.raises(ValueError, match="unsupported"):
        ProbeFile.from_json(json.dumps(document))


def test_missing_field_is_refused() -> None:
    document = json.loads(_sample().to_json())
    del document["radio"]["profile_sha256"]
    with pytest.raises(ValueError, match="profile_sha256"):
        ProbeFile.from_json(json.dumps(document))


def test_wrong_field_type_is_refused() -> None:
    document = json.loads(_sample().to_json())
    document["private_reads_included"] = "no"
    with pytest.raises(ValueError, match="private_reads_included"):
        ProbeFile.from_json(json.dumps(document))


def test_wrong_command_class_is_refused() -> None:
    document = json.loads(_sample().to_json())
    document["commands"][0]["class"] = "maybe"
    with pytest.raises(ValueError, match="class"):
        ProbeFile.from_json(json.dumps(document))


def test_skip_reasons_are_a_closed_enum() -> None:
    assert SKIP_REASONS == frozenset(
        {
            "unsafe_family",
            "private",
            "response_code",
            "no_unambiguous_bytes",
            "run_aborted",
        }
    )


def test_unknown_skip_reason_is_refused() -> None:
    import json

    document = json.loads(_sample().to_json())
    document["skipped"][0]["reason"] = "maybe-later"
    with pytest.raises(ValueError, match="reason"):
        ProbeFile.from_json(json.dumps(document))


def test_termination_round_trips_and_validates() -> None:
    import json

    from rigplane.probe.model import TERMINATION_STATUSES, TerminationInfo

    assert TERMINATION_STATUSES == frozenset(
        {"completed", "silence_stop", "transport_error", "cancelled", "restore_failed"}
    )
    assert _sample().termination == TerminationInfo("completed")
    document = json.loads(_sample().to_json())
    assert document["termination"] == {"status": "completed", "detail": ""}
    document["termination"] = {
        "status": "silence_stop",
        "detail": "The radio stopped answering.",
    }
    assert ProbeFile.from_json(json.dumps(document)).termination == (
        TerminationInfo(status="silence_stop", detail="The radio stopped answering.")
    )
    document["termination"] = {"status": "gave-up", "detail": ""}
    with pytest.raises(ValueError, match="termination.status"):
        ProbeFile.from_json(json.dumps(document))


def test_unknown_expected_is_refused() -> None:
    import json

    document = json.loads(_sample().to_json())
    document["commands"][0]["expected"] = "maybe"
    with pytest.raises(ValueError, match="expected"):
        ProbeFile.from_json(json.dumps(document))


def test_file_never_carries_port_host_or_user() -> None:
    text = _sample().to_json()
    for secret in ("COM5", "/dev/tty", "192.168", "hostname", "username"):
        assert secret not in text
