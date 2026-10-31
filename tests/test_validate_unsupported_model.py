"""MOR-2096: ``rigplane validate`` reports a refused model, never a traceback.

The radio factory deliberately refuses serial models it has no backend for
(MOR-174: ``test_create_radio_serial_unknown_model_raises``). X6100 and
TX-500 are both such profiles today; both are pinned so that routing one of
them cannot make the guard look fixed while it is missing. The refusal itself
is not under test here and must stay.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rigplane.cli import _build_parser, _validate
from rigplane.validation.runner import HARDWARE_OPT_IN_ENV


def _validate_args(model: str, provider: str) -> Any:
    argv = [
        "--model",
        model,
        "--backend",
        "serial",
        "--serial-port",
        "/dev/ttyNOPE",
        "validate",
        "--hardware",
        "--allow-hardware",
        "--read-only",
        "--assume-yes",
        "--provider",
        provider,
        "--json",
    ]
    return _build_parser().parse_args(argv)


@pytest.mark.parametrize("model", ["X6100", "TX-500"])
def test_native_validate_unsupported_model_is_an_error_not_a_traceback(
    model: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(HARDWARE_OPT_IN_ENV, "1")
    rc = _validate.run(_validate_args(model, "native"))
    captured = capsys.readouterr()
    assert rc == 3
    assert "Error: cannot create the radio backend: Unsupported serial model" in (
        captured.err
    )
    assert "Traceback" not in captured.out + captured.err
    artifact = json.loads(captured.out)
    check = artifact["levels"][0]["checks"][0]
    assert check["check_id"] == "discovery.identify"
    assert check["status"] == "fail"
    assert check["failure_domain"] == "transport"
    assert artifact["metadata"]["provider"] == "native"


def test_hamlib_validate_unsupported_model_is_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(HARDWARE_OPT_IN_ENV, "1")
    rc = _validate.run(_validate_args("X6100", "hamlib"))
    captured = capsys.readouterr()
    assert rc == 3
    assert "Error: cannot create the native radio backend:" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out)["metadata"]["provider"] == "hamlib"


def test_both_providers_still_return_an_artifact_when_the_native_leg_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(HARDWARE_OPT_IN_ENV, "1")
    rc = _validate.run(_validate_args("X6100", "both"))
    captured = capsys.readouterr()
    assert rc == 3
    assert "Traceback" not in captured.out + captured.err
    artifact = json.loads(captured.out)
    assert artifact["metadata"]["comparison"]["other_provider"] == "hamlib"
    assert "sys" not in captured.err  # keep sys imported only for clarity
