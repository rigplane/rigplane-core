"""Tests for rigctld audit logging (audit.py)."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from icom_lan.rigctld.audit import (
    AUDIT_LOGGER_NAME,
    AuditRecord,
    RigctldAuditFormatter,
    log_command,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(audit: AuditRecord) -> logging.LogRecord:
    """Wrap an AuditRecord in a LogRecord as the server does."""
    return logging.LogRecord(
        name=AUDIT_LOGGER_NAME,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=audit,
        args=(),
        exc_info=None,
    )


def _sample_record(**overrides: object) -> AuditRecord:
    defaults: dict[str, object] = dict(
        timestamp="2026-02-26T12:00:00+00:00",
        client_id=1,
        peername="127.0.0.1:12345",
        cmd="f",
        long_cmd="get_freq",
        args=(),
        duration_ms=1.5,
        rprt=0,
        is_set=False,
    )
    defaults.update(overrides)
    return AuditRecord(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AuditRecord
# ---------------------------------------------------------------------------


class TestAuditRecord:
    def test_creation_defaults(self) -> None:
        rec = _sample_record()
        assert rec.client_id == 1
        assert rec.cmd == "f"
        assert rec.long_cmd == "get_freq"
        assert rec.args == ()
        assert rec.rprt == 0
        assert rec.is_set is False
        assert rec.duration_ms == 1.5

    def test_set_command(self) -> None:
        rec = _sample_record(
            client_id=2,
            peername="127.0.0.1:12346",
            cmd="F",
            long_cmd="set_freq",
            args=("14074000",),
            duration_ms=5.0,
            rprt=0,
            is_set=True,
        )
        assert rec.is_set is True
        assert rec.args == ("14074000",)
        assert rec.cmd == "F"

    def test_error_rprt(self) -> None:
        rec = _sample_record(rprt=-5)
        assert rec.rprt == -5

    def test_multi_arg(self) -> None:
        rec = _sample_record(args=("USB", "2400"), long_cmd="set_mode", cmd="M")
        assert rec.args == ("USB", "2400")

    def test_vfo_defaults_to_none(self) -> None:
        """``AuditRecord.vfo`` defaults to ``None`` (bare-form path)."""
        rec = _sample_record()
        assert rec.vfo is None

    def test_vfo_captures_token(self) -> None:
        """``vfo`` captures the leading VFO token under ``chk_vfo=1``."""
        rec = _sample_record(vfo="VFOB")
        assert rec.vfo == "VFOB"


# ---------------------------------------------------------------------------
# RigctldAuditFormatter
# ---------------------------------------------------------------------------


class TestRigctldAuditFormatter:
    def test_output_is_valid_json(self) -> None:
        fmt = RigctldAuditFormatter()
        line = fmt.format(_make_record(_sample_record()))
        data = json.loads(line)  # must not raise
        assert isinstance(data, dict)

    def test_all_fields_present(self) -> None:
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(_sample_record())))
        for key in (
            "timestamp",
            "client_id",
            "peername",
            "cmd",
            "long_cmd",
            "args",
            "vfo",
            "duration_ms",
            "rprt",
            "is_set",
        ):
            assert key in data, f"missing key: {key}"

    def test_field_values_get_command(self) -> None:
        rec = _sample_record()
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))

        assert data["timestamp"] == "2026-02-26T12:00:00+00:00"
        assert data["client_id"] == 1
        assert data["peername"] == "127.0.0.1:12345"
        assert data["cmd"] == "f"
        assert data["long_cmd"] == "get_freq"
        assert data["args"] == []
        assert data["duration_ms"] == 1.5
        assert data["rprt"] == 0
        assert data["is_set"] is False

    def test_args_serialised_as_list(self) -> None:
        rec = _sample_record(
            args=("14074000",), cmd="F", long_cmd="set_freq", is_set=True
        )
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))
        assert data["args"] == ["14074000"]
        assert data["is_set"] is True

    def test_multi_arg_serialised(self) -> None:
        rec = _sample_record(
            args=("USB", "2400"), cmd="M", long_cmd="set_mode", is_set=True
        )
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))
        assert data["args"] == ["USB", "2400"]

    def test_error_rprt_in_output(self) -> None:
        rec = _sample_record(rprt=-5)
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))
        assert data["rprt"] == -5

    def test_output_is_single_line(self) -> None:
        fmt = RigctldAuditFormatter()
        line = fmt.format(_make_record(_sample_record()))
        assert "\n" not in line

    def test_vfo_serialised_when_present(self) -> None:
        """``vfo`` field is included in JSON output under ``chk_vfo=1``."""
        rec = _sample_record(vfo="VFOB", cmd="F", long_cmd="set_freq")
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))
        assert data["vfo"] == "VFOB"

    def test_vfo_serialised_as_null_when_absent(self) -> None:
        """``vfo`` is JSON ``null`` for bare-form (non-VFO-prefixed) commands."""
        rec = _sample_record()
        fmt = RigctldAuditFormatter()
        data = json.loads(fmt.format(_make_record(rec)))
        assert data["vfo"] is None


# ---------------------------------------------------------------------------
# log_command
# ---------------------------------------------------------------------------


class TestLogCommand:
    def test_calls_info_on_audit_logger(self) -> None:
        rec = _sample_record()
        with patch.object(logging.getLogger(AUDIT_LOGGER_NAME), "info") as mock_info:
            log_command(rec)
        mock_info.assert_called_once_with(rec)

    def test_record_reaches_audit_logger_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        rec = _sample_record()
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
            log_command(rec)
        assert len(caplog.records) == 1
        assert caplog.records[0].name == AUDIT_LOGGER_NAME

    def test_record_level_is_info(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _sample_record()
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
            log_command(rec)
        assert caplog.records[0].levelno == logging.INFO

    def test_msg_is_audit_record(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _sample_record()
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
            log_command(rec)
        assert caplog.records[0].msg is rec


# ---------------------------------------------------------------------------
# AUDIT_LOGGER_NAME constant
# ---------------------------------------------------------------------------


class TestAuditLoggerName:
    def test_name_value(self) -> None:
        assert AUDIT_LOGGER_NAME == "icom_lan.rigctld.audit"

    def test_logger_is_separate_from_main(self) -> None:
        audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
        main_logger = logging.getLogger("icom_lan.rigctld.server")
        assert audit_logger is not main_logger
