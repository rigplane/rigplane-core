"""Tests for CLI async command handlers using mocked IcomRadio."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import wave
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import rigplane.cli as cli_module
from _caps import FULL_ICOM_CAPS
from rigplane.cli import (
    _cmd_audio_caps,
    _cmd_audio_loopback,
    _cmd_audio_rx,
    _cmd_audio_tx,
    _cmd_cw,
    _cmd_freq,
    _cmd_meter,
    _cmd_mode,
    _cmd_power,
    _cmd_ptt,
    _cmd_scope,
    _cmd_status,
    _armed_for_ptt_hold,
    _PTT_HOLD_SIGNALS,
    _ptt_hold,
    _PttArm,
    _run,
    main,
)
from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import (
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.exceptions import TimeoutError as IcomTimeout
from rigplane.runtime.radio import IcomRadio as RuntimeIcomRadio


# ---------------------------------------------------------------------------
# Mock radio fixture
# ---------------------------------------------------------------------------


def _add_capability_protocols(radio: MagicMock) -> MagicMock:
    """Add minimal attributes so mock satisfies capability tag checks.

    All attrs must be explicitly set; Python 3.12+ runtime_checkable Protocol uses
    inspect.getattr_static which bypasses MagicMock.__getattr__.
    """
    # Capability tags — superset of all tags the mock supports
    radio.capabilities = set(FULL_ICOM_CAPS)
    # AudioCapable
    radio.audio_bus = MagicMock()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.get_audio_stats = AsyncMock(return_value={})
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    # ScopeCapable
    radio.enable_scope = AsyncMock()
    if not hasattr(radio, "disable_scope"):
        radio.disable_scope = AsyncMock()
    radio.on_scope_data = MagicMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.set_scope_during_tx = AsyncMock()
    radio.set_scope_center_type = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    # AdvancedControlCapable
    radio.send_cw_text = AsyncMock()
    radio.stop_cw_text = AsyncMock()
    radio.set_attenuator = AsyncMock()
    radio.set_attenuator_level = AsyncMock()
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.set_preamp = AsyncMock()
    radio.get_preamp = AsyncMock(return_value=0)
    radio.set_antenna_1 = AsyncMock()
    radio.set_antenna_2 = AsyncMock()
    radio.set_rx_antenna_ant1 = AsyncMock()
    radio.set_rx_antenna_ant2 = AsyncMock()
    radio.get_antenna_1 = AsyncMock(return_value=0)
    radio.get_antenna_2 = AsyncMock(return_value=0)
    radio.get_rx_antenna_ant1 = AsyncMock(return_value=0)
    radio.get_rx_antenna_ant2 = AsyncMock(return_value=0)
    radio.set_system_date = AsyncMock()
    radio.get_system_date = AsyncMock(return_value=(2026, 1, 1))
    radio.set_system_time = AsyncMock()
    radio.get_system_time = AsyncMock(return_value=(0, 0))
    radio.set_dual_watch = AsyncMock()
    radio.get_dual_watch = AsyncMock(return_value=False)
    radio.set_tuner_status = AsyncMock()
    radio.get_tuner_status = AsyncMock(return_value=False)
    radio.set_acc1_mod_level = AsyncMock()
    radio.set_usb_mod_level = AsyncMock()
    radio.set_lan_mod_level = AsyncMock()
    radio.set_compressor = AsyncMock()
    radio.set_nb = AsyncMock()
    radio.set_nr = AsyncMock()
    radio.set_ip_plus = AsyncMock()
    radio.set_digisel = AsyncMock()
    radio.set_filter = AsyncMock()
    return radio


@pytest.fixture
def mock_radio():
    radio = AsyncMock()
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.set_freq = AsyncMock()
    radio.get_mode = AsyncMock(return_value=("USB", None))
    radio.set_mode = AsyncMock()
    radio.get_rf_power = AsyncMock(return_value=128)
    radio.set_rf_power = AsyncMock()
    radio.get_powerstat = AsyncMock(return_value=True)
    radio.get_s_meter = AsyncMock(return_value=120)
    radio.get_swr = AsyncMock(return_value=50)
    radio.get_alc_meter = AsyncMock(return_value=30)
    radio.get_comp_meter = AsyncMock(return_value=0)
    radio.get_id_meter = AsyncMock(return_value=0)
    radio.get_vd_meter = AsyncMock(return_value=0)
    radio.set_ptt = AsyncMock()
    radio.send_cw_text = AsyncMock()
    radio.set_powerstat = AsyncMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    # Unmanaged on purpose: no backend assembles a managed TX runtime yet
    # (MOR-1016). Left unset, an AsyncMock auto-satisfies ``ManagedTxCapable``
    # on 3.11 and not on 3.12+ (gh-102433), which would make every PTT test in
    # this file interpreter dependent — and the PR gate runs 3.11.
    radio.managed_tx = None
    _add_capability_protocols(radio)
    return radio


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    @pytest.mark.asyncio
    async def test_status_text(self, mock_radio, capsys) -> None:
        args = Namespace(json=False)
        rc = await _cmd_status(mock_radio, args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "14,074,000" in out
        assert "USB" in out

    @pytest.mark.asyncio
    async def test_status_json(self, mock_radio, capsys) -> None:
        args = Namespace(json=True)
        rc = await _cmd_status(mock_radio, args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["frequency_hz"] == 14_074_000
        assert data["mode"] == "USB"
        assert data["s_meter"] == 120
        assert data["power"] == 128


# ---------------------------------------------------------------------------
# _cmd_audio_caps
# ---------------------------------------------------------------------------


class TestCmdAudioCaps:
    @pytest.mark.asyncio
    async def test_audio_caps_text(self, capsys) -> None:
        args = Namespace(json=False)
        rc = await _cmd_audio_caps(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Supported codecs:" in out
        assert "Defaults:" in out
        assert "PCM_2CH_16BIT" in out
        assert "48000" in out

    @pytest.mark.asyncio
    async def test_audio_caps_json(self, capsys) -> None:
        args = Namespace(json=True)
        rc = await _cmd_audio_caps(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["default_codec"]["name"] == "PCM_2CH_16BIT"
        assert data["default_sample_rate_hz"] == 48000
        assert data["default_channels"] == 2
        assert any(c["name"] == "OPUS_1CH" for c in data["supported_codecs"])

    @pytest.mark.asyncio
    async def test_audio_caps_json_with_stats(self, capsys) -> None:
        args = Namespace(json=True, stats=True)
        runtime_stats = {
            "active": False,
            "state": "idle",
            "rx_packets_received": 4,
            "rx_packets_delivered": 3,
            "tx_packets_sent": 0,
            "packets_lost": 1,
            "packet_loss_percent": 25.0,
            "reorder_depth_ema_ms": 8.0,
            "jitter_max_ms": 20.0,
            "underrun_count": 1,
            "overrun_count": 0,
            "estimated_latency_ms": 0.0,
            "jitter_buffer_depth_packets": 5,
            "jitter_buffer_pending_packets": 0,
            "duplicates_dropped": 0,
            "stale_packets_dropped": 0,
            "out_of_order_packets": 2,
        }
        rc = await _cmd_audio_caps(args, runtime_stats=runtime_stats)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["runtime_stats"]["packets_lost"] == 1
        assert data["runtime_stats"]["packet_loss_percent"] == 25.0

    @pytest.mark.asyncio
    async def test_audio_caps_text_with_stats(self, capsys) -> None:
        args = Namespace(json=False, stats=True)
        runtime_stats = {
            "active": True,
            "state": "receiving",
            "rx_packets_received": 10,
            "rx_packets_delivered": 9,
            "tx_packets_sent": 0,
            "packets_lost": 1,
            "packet_loss_percent": 10.0,
            "reorder_depth_ema_ms": 2.5,
            "jitter_max_ms": 20.0,
            "underrun_count": 1,
            "overrun_count": 0,
            "estimated_latency_ms": 100.0,
            "jitter_buffer_depth_packets": 5,
            "jitter_buffer_pending_packets": 1,
            "duplicates_dropped": 0,
            "stale_packets_dropped": 0,
            "out_of_order_packets": 1,
        }
        rc = await _cmd_audio_caps(args, runtime_stats=runtime_stats)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Runtime stats:" in out
        assert "packet_loss_percent: 10.000" in out


# ---------------------------------------------------------------------------
# _cmd_audio_rx / _cmd_audio_tx / _cmd_audio_loopback
# ---------------------------------------------------------------------------


class TestCmdAudioCli:
    @pytest.mark.asyncio
    async def test_audio_rx_smoke(self, mock_radio, tmp_path, capsys) -> None:
        frame = b"\x01\x02" * 960

        async def _start_rx(cb, **_kwargs) -> None:
            cb(frame)
            cb(None)

        mock_radio.start_audio_rx_pcm = AsyncMock(side_effect=_start_rx)

        out_file = tmp_path / "rx.wav"
        args = Namespace(
            output_file=str(out_file),
            seconds=0.01,
            sample_rate=48000,
            channels=1,
            json=True,
            stats=False,
        )
        rc = await _cmd_audio_rx(mock_radio, args)

        assert rc == 0
        mock_radio.start_audio_rx_pcm.assert_awaited_once()
        mock_radio.stop_audio_rx_pcm.assert_awaited_once()
        with wave.open(str(out_file), "rb") as wf:
            assert wf.getframerate() == 48000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getnframes() > 0
        data = json.loads(capsys.readouterr().out)
        assert data["command"] == "audio-rx"
        assert data["bytes_written"] > 0

    @pytest.mark.asyncio
    async def test_audio_tx_smoke(self, mock_radio, tmp_path, capsys) -> None:
        in_file = tmp_path / "tx.wav"
        with wave.open(str(in_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x01\x02" * 960)

        args = Namespace(
            input_file=str(in_file),
            sample_rate=48000,
            channels=1,
            json=True,
            stats=False,
        )
        rc = await _cmd_audio_tx(mock_radio, args)

        assert rc == 0
        mock_radio.start_audio_tx_pcm.assert_awaited_once()
        mock_radio.stop_audio_tx_pcm.assert_awaited_once()
        assert mock_radio.push_audio_tx_pcm.await_count >= 1
        data = json.loads(capsys.readouterr().out)
        assert data["command"] == "audio-tx"
        assert data["tx_frames"] >= 1

    @pytest.mark.asyncio
    async def test_audio_loopback_smoke(self, mock_radio, capsys) -> None:
        frame = b"\x03\x04" * 960

        async def _start_rx(cb, **_kwargs) -> None:
            cb(frame)
            cb(None)

        mock_radio.start_audio_rx_pcm = AsyncMock(side_effect=_start_rx)

        args = Namespace(
            seconds=0.05,
            sample_rate=48000,
            channels=1,
            json=True,
            stats=False,
        )
        rc = await _cmd_audio_loopback(mock_radio, args)

        assert rc == 0
        mock_radio.start_audio_rx_pcm.assert_awaited_once()
        mock_radio.stop_audio_rx_pcm.assert_awaited_once()
        mock_radio.start_audio_tx_pcm.assert_awaited_once()
        mock_radio.stop_audio_tx_pcm.assert_awaited_once()
        assert mock_radio.push_audio_tx_pcm.await_count >= 1
        data = json.loads(capsys.readouterr().out)
        assert data["command"] == "audio-loopback"
        assert data["tx_frames"] >= 1


# ---------------------------------------------------------------------------
# _cmd_freq
# ---------------------------------------------------------------------------


class TestCmdFreq:
    @pytest.mark.asyncio
    async def test_get_freq_text(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=False)
        rc = await _cmd_freq(mock_radio, args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "14,074,000" in out

    @pytest.mark.asyncio
    async def test_get_freq_json(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=True)
        rc = await _cmd_freq(mock_radio, args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["frequency_hz"] == 14_074_000

    @pytest.mark.asyncio
    async def test_set_freq(self, mock_radio, capsys) -> None:
        args = Namespace(value="7074000", json=False)
        rc = await _cmd_freq(mock_radio, args)
        assert rc == 0
        mock_radio.set_freq.assert_called_once_with(7_074_000)
        out = capsys.readouterr().out
        assert "7,074,000" in out

    @pytest.mark.asyncio
    async def test_set_freq_mhz(self, mock_radio, capsys) -> None:
        args = Namespace(value="14.074m", json=False)
        rc = await _cmd_freq(mock_radio, args)
        assert rc == 0
        mock_radio.set_freq.assert_called_once_with(14_074_000)


# ---------------------------------------------------------------------------
# _cmd_mode
# ---------------------------------------------------------------------------


class TestCmdMode:
    @pytest.mark.asyncio
    async def test_get_mode_text(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=False)
        rc = await _cmd_mode(mock_radio, args)
        assert rc == 0
        assert "USB" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_get_mode_json(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=True)
        rc = await _cmd_mode(mock_radio, args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "USB"

    @pytest.mark.asyncio
    async def test_set_mode(self, mock_radio, capsys) -> None:
        args = Namespace(value="LSB", json=False)
        rc = await _cmd_mode(mock_radio, args)
        assert rc == 0
        mock_radio.set_mode.assert_called_once_with("LSB")


# ---------------------------------------------------------------------------
# _cmd_power
# ---------------------------------------------------------------------------


class TestCmdPower:
    @pytest.mark.asyncio
    async def test_get_power_text(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=False)
        rc = await _cmd_power(mock_radio, args)
        assert rc == 0
        assert "128" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_get_power_json(self, mock_radio, capsys) -> None:
        args = Namespace(value=None, json=True)
        rc = await _cmd_power(mock_radio, args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["power"] == 128

    @pytest.mark.asyncio
    async def test_set_power(self, mock_radio, capsys) -> None:
        args = Namespace(value=200, json=False)
        rc = await _cmd_power(mock_radio, args)
        assert rc == 0
        mock_radio.set_rf_power.assert_called_once_with(200)


# ---------------------------------------------------------------------------
# _cmd_meter
# ---------------------------------------------------------------------------


class TestCmdMeter:
    @pytest.mark.asyncio
    async def test_meter_text(self, mock_radio, capsys) -> None:
        args = Namespace(json=False)
        rc = await _cmd_meter(mock_radio, args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "120" in out  # s_meter
        assert "50" in out  # swr

    @pytest.mark.asyncio
    async def test_meter_json(self, mock_radio, capsys) -> None:
        args = Namespace(json=True)
        rc = await _cmd_meter(mock_radio, args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["s_meter"] == 120
        assert data["swr"] == 50
        assert data["alc"] == 30

    @pytest.mark.asyncio
    async def test_meter_timeout_handled(self, mock_radio, capsys) -> None:
        mock_radio.get_swr = AsyncMock(side_effect=IcomTimeout("timeout"))
        args = Namespace(json=False)
        rc = await _cmd_meter(mock_radio, args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "n/a" in out


# ---------------------------------------------------------------------------
# _cmd_ptt
# ---------------------------------------------------------------------------


class TestCmdPtt:
    @pytest.mark.asyncio
    async def test_ptt_on(self, mock_radio, held, capsys) -> None:
        args = Namespace(state="on", hold_seconds=None)
        rc = await _cmd_ptt(mock_radio, args)
        assert rc == 130
        assert mock_radio.set_ptt.await_args_list == [call(True), call(False)]
        assert "ON" in capsys.readouterr().out

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
    async def test_ptt_off(self, mock_radio, capsys) -> None:
        args = Namespace(state="off")
        rc = await _cmd_ptt(mock_radio, args)
        assert rc == 0
        mock_radio.set_ptt.assert_called_once_with(False)
        assert "OFF" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _cmd_ptt — managed TX ingress (MOR-1170)
# ---------------------------------------------------------------------------

_IDLE_TX_SNAPSHOT = TxSafetySupervisor().snapshot
_ACCEPTED = TxOutcome.ACCEPTED
# Every answer ``request_on`` can give when the caller did *not* get the rig.
_TX_KEY_REJECTIONS = (
    TxOutcome.BUSY,
    TxOutcome.NOT_READY,
    TxOutcome.RADIO_NOT_OFF,
    TxOutcome.RELEASE_PENDING,
    TxOutcome.STALE,
)


class _FakeTxSupervisor:
    """Hand written: a MagicMock satisfies ManagedTxCapable on 3.11, not 3.12+."""

    def __init__(self, on: TxOutcome, off: TxOutcome) -> None:
        self._on, self._off = on, off
        self.calls: list[tuple[bool, TxOwner, TxReleaseReason | None]] = []

    async def request_on(self, owner: TxOwner) -> TxTransition:
        self.calls.append((True, owner, None))
        return TxTransition(self._on, _IDLE_TX_SNAPSHOT)

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        self.calls.append((False, owner, reason))
        return TxTransition(self._off, _IDLE_TX_SNAPSHOT)


class _FakeManagedRadio:
    """Radio whose bare ``set_ptt`` the managed ingress must never reach."""

    def __init__(self, on: TxOutcome = _ACCEPTED, off: TxOutcome = _ACCEPTED) -> None:
        self.bare_writes: list[bool] = []
        self.supervisor = _FakeTxSupervisor(on, off)
        self.managed_tx = self.supervisor

    async def set_ptt(self, on: bool) -> None:
        self.bare_writes.append(on)


def _kinds(sup: _FakeTxSupervisor) -> list[tuple[bool, TxReleaseReason | None]]:
    return [(keyed, reason) for keyed, _owner, reason in sup.calls]


class TestCmdPttManagedIngress:
    @pytest.mark.asyncio
    async def test_shipped_radio_keeps_the_legacy_direct_write(
        self, held, capsys
    ) -> None:
        # No backend assembles a managed runtime yet (MOR-1016 PR2): the radio
        # the CLI ships must reach its own ``set_ptt``, with today's codes and
        # lines. (MOR-1016 PR1 gives CoreRadio a real, inert ``managed_tx``
        # member, so ``isinstance(..., ManagedTxCapable)`` now reads True
        # structurally -- the probe the protocol's own docstring says not to
        # trust. ``bind()`` returning ``None`` is the invariant that matters.)
        shipped = RuntimeIcomRadio("127.0.0.1")
        writes: list[bool] = []

        async def _record(on: bool) -> None:
            writes.append(on)

        assert ManagedTxApi.bind(shipped, TxOwner(TxSource.SDK, "probe")) is None
        shipped.set_ptt = _record

        assert await _cmd_ptt(shipped, Namespace(state="on", hold_seconds=None)) == 130
        assert writes == [True, False]  # the hold keys and unkeys through it
        assert capsys.readouterr().out.splitlines() == ["PTT ON", "PTT OFF"]

        assert await _cmd_ptt(shipped, Namespace(state="off")) == 0
        assert writes == [True, False, False]
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["PTT OFF"]
        assert captured.err == ""  # ``ptt off`` still stays silent and immediate

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", [TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT])
    async def test_managed_ingress_enters_the_supervisor_exactly_once(
        self, outcome: TxOutcome, held, capsys
    ) -> None:
        # IDEMPOTENT means this owner already holds the lease, so the request
        # did land — it is an accepting outcome, not a rejection.
        radio = _FakeManagedRadio(on=outcome, off=outcome)

        assert await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None)) == 130
        assert _kinds(radio.supervisor) == [
            (True, None),
            (False, TxReleaseReason.OPERATOR_RELEASE),  # the hold's own unkey
        ]
        assert capsys.readouterr().out.splitlines() == ["PTT ON", "PTT OFF"]

        assert await _cmd_ptt(radio, Namespace(state="off")) == 0
        assert _kinds(radio.supervisor)[-1] == (False, TxReleaseReason.OPERATOR_RELEASE)
        assert radio.bare_writes == []  # no bypass: the effect path writes
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["PTT OFF"]
        assert captured.err == ""

    @pytest.mark.asyncio
    async def test_tx_owner_is_one_stable_identity_per_process(self, held) -> None:
        first, second = _FakeManagedRadio(), _FakeManagedRadio()

        await _cmd_ptt(first, Namespace(state="on", hold_seconds=None))
        await _cmd_ptt(first, Namespace(state="off"))
        await _cmd_ptt(second, Namespace(state="on", hold_seconds=None))

        calls = first.supervisor.calls + second.supervisor.calls
        owners = [owner for _keyed, owner, _reason in calls]
        assert len(owners) == 5  # each hold keys and releases under one owner
        # ``release_owner`` matches on the owner alone, so a per-request (or
        # per-radio) owner would miss its own lease and could strand the rig
        # keyed until the watchdog fires.
        assert all(owner is owners[0] for owner in owners)
        assert owners[0].source is TxSource.SDK
        assert owners[0].session_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", _TX_KEY_REJECTIONS)
    async def test_a_key_the_supervisor_refused_exits_non_zero(
        self, outcome: TxOutcome, held, capsys
    ) -> None:
        # The supervisor reports refusal by return value, not by raising: a
        # caller that ignores it prints a cheerful "PTT ON" and exits 0 while
        # the rig is not transmitting on its behalf.
        radio = _FakeManagedRadio(on=outcome)

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Error" in captured.err and f"({outcome})" in captured.err
        assert _kinds(radio.supervisor) == [(True, None)]
        assert radio.bare_writes == []

    @pytest.mark.asyncio
    async def test_a_release_the_supervisor_refused_succeeds(self, capsys) -> None:
        # STALE covers both "nothing was keyed" and "another owner holds it".
        # A non-owner cannot force a release by design, so the unkey is best
        # effort: it reports, but it must not fail the command.
        radio = _FakeManagedRadio(off=TxOutcome.STALE)

        rc = await _cmd_ptt(radio, Namespace(state="off"))

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["PTT OFF"]
        assert "Warning" in captured.err and f"({TxOutcome.STALE})" in captured.err
        assert _kinds(radio.supervisor) == [(False, TxReleaseReason.OPERATOR_RELEASE)]
        assert radio.bare_writes == []


# ---------------------------------------------------------------------------
# _cmd_ptt — ``ptt on`` holds the key for as long as it runs (MOR-1184)
# ---------------------------------------------------------------------------


class _FakeShippedRadio:
    """Hand written: unmanaged, like every backend the CLI ships until MOR-1016."""

    managed_tx = None

    def __init__(self, unkey_error: Exception | None = None) -> None:
        self.writes: list[bool] = []
        self._unkey_error = unkey_error

    async def set_ptt(self, on: bool) -> None:
        if not on and self._unkey_error is not None:
            raise self._unkey_error
        self.writes.append(on)


@pytest.fixture
def held(monkeypatch):
    """Drive the hold by hand — no test may block, or wait out a duration."""
    seen: list[float | None] = []

    async def _hold(arm: _PttArm, seconds: float | None) -> bool:
        seen.append(seconds)
        if seconds is not None:
            return False  # the clock ran out
        arm.signum = int(signal.SIGINT)  # only a signal ends an unbounded hold
        return True

    monkeypatch.setattr(cli_module, "_ptt_hold", _hold)
    return seen


class TestCmdPttHold:
    @pytest.mark.asyncio
    async def test_ptt_on_holds_and_unkeys_when_interrupted(self, held, capsys) -> None:
        before = [signal.getsignal(sig) for sig in _PTT_HOLD_SIGNALS]
        radio = _FakeShippedRadio()

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 130  # 128 + SIGINT, as any interrupted command reports
        assert held == [None]  # unbounded: no --for was given
        assert radio.writes == [True, False]  # the keying process is the unkeying one
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["PTT ON", "PTT OFF"]
        assert "Ctrl-C" in captured.err  # the hold says how to end itself
        assert [signal.getsignal(sig) for sig in _PTT_HOLD_SIGNALS] == before

    @pytest.mark.asyncio
    async def test_the_hold_owns_sigint_sigterm_and_sighup(self) -> None:
        # Closing the terminal is the likeliest accidental end to a held
        # transmission, and SIGHUP's default action would leave the rig keyed.
        assert set(_PTT_HOLD_SIGNALS) == {
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGHUP,
        }

    @pytest.mark.asyncio
    async def test_for_bounds_the_hold_and_exits_zero(self, held, capsys) -> None:
        rc = await _cmd_ptt(
            radio := _FakeShippedRadio(), Namespace(state="on", hold_seconds=5.0)
        )

        assert rc == 0  # the duration ran out; nothing went wrong
        assert held == [5.0]  # and it is the duration that was asked for
        assert radio.writes == [True, False]
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["PTT ON", "PTT OFF"]
        assert "5 s" in captured.err  # the deadline is on screen while it holds

    @pytest.mark.asyncio
    async def test_a_refused_key_never_enters_the_hold(self, held, capsys) -> None:
        # Holding on a key the supervisor refused would block on a rig that is
        # not transmitting, then "release" a lease this session never took.
        radio = _FakeManagedRadio(on=TxOutcome.BUSY)

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 1
        assert held == []
        assert _kinds(radio.supervisor) == [(True, None)]  # no release either
        assert capsys.readouterr().out == ""

    @pytest.mark.asyncio
    async def test_a_failed_unkey_is_reported_and_exits_non_zero(
        self, held, capsys
    ) -> None:
        # A silent failed unkey is the worst outcome available here: the rig is
        # transmitting and the operator has been told the command finished.
        radio = _FakeShippedRadio(unkey_error=OSError("transport gone"))

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 1
        captured = capsys.readouterr()
        assert "PTT OFF" not in captured.out
        assert "Error" in captured.err and "transport gone" in captured.err

    @pytest.mark.asyncio
    async def test_a_refused_release_of_this_session_lease_exits_non_zero(
        self, held, capsys
    ) -> None:
        # Unlike a bare ``ptt off``, this session is known to have taken the
        # lease, so a refused release means the rig may still be keyed on it.
        radio = _FakeManagedRadio(off=TxOutcome.STALE)

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err and f"({TxOutcome.STALE})" in captured.err
        assert "PTT OFF" not in captured.out

    @pytest.mark.asyncio
    async def test_the_hold_still_owns_the_signals_while_unkeying(self, held) -> None:
        # Restoring the handlers before the release would let a second Ctrl-C
        # raise KeyboardInterrupt mid-unkey and walk away from a keyed rig.
        radio = _FakeShippedRadio()
        before = signal.getsignal(signal.SIGINT)
        during: list[object] = []
        keyed = radio.set_ptt

        async def _watch(on: bool) -> None:
            if not on:
                during.append(signal.getsignal(signal.SIGINT))
            await keyed(on)

        radio.set_ptt = _watch  # type: ignore[method-assign]

        assert await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None)) == 130
        assert during and during[0] is not before  # the hold's, not the default
        assert signal.getsignal(signal.SIGINT) is before  # and restored after

    @pytest.mark.asyncio
    async def test_the_unkey_runs_when_the_hold_itself_raises(
        self, monkeypatch
    ) -> None:
        async def _boom(stop: asyncio.Event, seconds: float | None) -> bool:
            raise RuntimeError("hold blew up")

        monkeypatch.setattr(cli_module, "_ptt_hold", _boom)
        radio = _FakeShippedRadio()

        with pytest.raises(RuntimeError):
            await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert radio.writes == [True, False]

    @pytest.mark.asyncio
    async def test_the_hold_distinguishes_its_clock_from_its_event(self) -> None:
        arm = _PttArm()
        assert await _ptt_hold(arm, 0.01) is False  # ran out, nobody asked
        arm.stop.set()
        assert await _ptt_hold(arm, None) is True  # unbounded, ended by event
        assert await _ptt_hold(arm, 3600.0) is True  # event beats the clock


# ---------------------------------------------------------------------------
# _cmd_ptt — arm before the key, bound the unkey, report the signal (MOR-1199)
# ---------------------------------------------------------------------------


class TestCmdPttArming:
    @pytest.mark.asyncio
    async def test_a_signal_before_the_key_never_transmits(
        self, held, monkeypatch, capsys
    ) -> None:
        # Arming first only buys anything if the key is then skipped: a
        # process that has already been told to stop must not go on to key a
        # rig it will not be around to unkey.
        @contextlib.contextmanager
        def _already_signalled():
            with _armed_for_ptt_hold() as arm:
                arm.signum = int(signal.SIGTERM)
                yield arm

        monkeypatch.setattr(cli_module, "_armed_for_ptt_hold", _already_signalled)
        radio = _FakeShippedRadio()

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 143  # 128 + SIGTERM
        assert radio.writes == []  # never keyed, so there is nothing to unkey
        assert held == []  # and no hold was entered
        assert capsys.readouterr().out == ""  # nor was anything announced

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "signum, code",
        [(signal.SIGINT, 130), (signal.SIGHUP, 129), (signal.SIGTERM, 143)],
    )
    async def test_the_exit_code_reflects_the_signal_that_ended_the_hold(
        self, signum, code, monkeypatch
    ) -> None:
        # A wrapper that retries on Ctrl-C but obeys a SIGTERM cannot tell the
        # two apart if every ending reports 130.
        async def _hold(arm: _PttArm, seconds: float | None) -> bool:
            arm.signum = int(signum)
            return True

        monkeypatch.setattr(cli_module, "_ptt_hold", _hold)
        radio = _FakeShippedRadio()

        assert await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None)) == code
        assert radio.writes == [True, False]

    @pytest.mark.asyncio
    async def test_a_hanging_unkey_is_bounded_and_reported(
        self, held, monkeypatch, capsys
    ) -> None:
        # The hold still owns the signals across the release, so an unbounded
        # wait on a transport that never answers leaves the operator SIGKILL
        # — which unkeys nothing — as the only way out.
        monkeypatch.setattr(cli_module, "_PTT_RELEASE_TIMEOUT", 0.05)
        radio = _FakeShippedRadio()
        keyed = radio.set_ptt

        async def _hang(on: bool) -> None:
            if not on:
                await asyncio.Event().wait()  # a transport that never answers
            await keyed(on)

        radio.set_ptt = _hang  # type: ignore[method-assign]

        rc = await _cmd_ptt(radio, Namespace(state="on", hold_seconds=None))

        assert rc == 1
        captured = capsys.readouterr()
        assert "PTT OFF" not in captured.out  # it did not claim to have unkeyed
        assert "timed out" in captured.err
        assert "may still be transmitting" in captured.err


_PTT_SIGNAL_CHILD = '''\
import os
import sys

import rigplane.cli as cli

JOURNAL = sys.argv[1]
WIDEN = float(sys.argv[2]) if len(sys.argv) > 2 else None


def note(line):
    with open(JOURNAL, "a") as fh:
        fh.write(line + "\\n")
        fh.flush()
        os.fsync(fh.fileno())


class FakeRadio:
    """Hand written: unmanaged, like every backend the CLI ships today."""

    capabilities = set()
    managed_tx = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def set_ptt(self, on):
        note("key" if on else "unkey")


cli.create_radio = lambda config: FakeRadio()

if WIDEN is not None:  # widen the arm-to-key window so a signal lands inside it
    import contextlib
    import time

    armed = cli._armed_for_ptt_hold

    @contextlib.contextmanager
    def slow_arm():
        with armed() as arm:
            note("armed")
            time.sleep(WIDEN)
            yield arm

    cli._armed_for_ptt_hold = slow_arm

sys.argv = ["rigplane", "--host", "127.0.0.1", "ptt", "on"]
cli.main()
'''


def _signal_a_real_ptt_hold(
    tmp_path, signum: signal.Signals, widen: float | None = None
) -> tuple[int, list[str], list[str]]:
    """Run a real ``rigplane ptt on``, signal it, and report what it did."""
    child = tmp_path / "child.py"
    child.write_text(_PTT_SIGNAL_CHILD)
    journal, err_path = tmp_path / "journal.txt", tmp_path / "err.txt"
    journal.write_text("")
    argv = [sys.executable, str(child), str(journal)]
    argv += [] if widen is None else [str(widen)]
    with err_path.open("w") as err_fh:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=err_fh,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "ICOM_DEBUG": "0"},
        )

        # Handshake, so the signal cannot race process start-up: the hint is
        # printed once the hold owns the signals and the rig is keyed, and
        # the journal's "armed" line once they are owned but before the key.
        def _reached() -> bool:
            if widen is not None:
                return "armed" in journal.read_text()
            return "Ctrl-C" in err_path.read_text()

        deadline = time.monotonic() + 60
        while not _reached():
            assert proc.poll() is None and time.monotonic() < deadline, (
                f"child never reached the hold: {err_path.read_text()}"
            )
            time.sleep(0.02)
        proc.send_signal(signum)
        # PYTHONUNBUFFERED above is load bearing: ``main()`` ends in
        # ``os._exit``, which flushes no stdio, so a piped stdout is lost
        # without it. Pre-existing and not specific to ``ptt``.
        out, _ = proc.communicate(timeout=60)
    return proc.returncode, out.splitlines(), journal.read_text().split()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_a_real_signal_unkeys_the_rig(signum, tmp_path) -> None:
    # ``main()`` ends in ``os._exit`` and force-exits about a second after a
    # signal, so a handler that merely exists is no evidence that it is
    # installed or that the unkey survives. Send the real signal to a real
    # ``main()`` and read back what the radio saw.
    rc, out, journal = _signal_a_real_ptt_hold(tmp_path, signum)

    assert journal == ["key", "unkey"]
    assert out == ["PTT ON", "PTT OFF"]
    assert rc == 128 + signum  # 130 / 143 / 129, the code a shell reports


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_a_signal_before_the_key_leaves_the_rig_unkeyed(signum, tmp_path) -> None:
    # The window between owning the signals and issuing the key is real, and
    # in it the process is interruptible while the rig is not yet keyed. It is
    # widened here because unwidened it is too short to hit on purpose — and a
    # rig keyed by a process on its way out is what the ordering prevents.
    #
    # All three signals, because SIGINT alone cannot tell arming-first from
    # arming-after-the-key: unhandled, it raises KeyboardInterrupt, which
    # unwinds ahead of the key into ``main()``'s own 130 — the same journal,
    # the same output, the same code. Unhandled SIGHUP kills the process and
    # unhandled SIGTERM lands in ``main()``'s handler, so only these two prove
    # the hold's handlers were already installed when the signal arrived.
    rc, out, journal = _signal_a_real_ptt_hold(tmp_path, signum, widen=1.0)

    assert journal == ["armed"]  # it never keyed, so there was nothing to unkey
    assert out == []  # and it never announced a transmission
    assert rc == 128 + signum


# ---------------------------------------------------------------------------
# _cmd_cw
# ---------------------------------------------------------------------------


class TestCmdCw:
    @pytest.mark.asyncio
    async def test_cw(self, mock_radio, capsys) -> None:
        args = Namespace(text="CQ DE KN4KYD")
        rc = await _cmd_cw(mock_radio, args)
        assert rc == 0
        mock_radio.send_cw_text.assert_called_once_with("CQ DE KN4KYD")
        assert "CQ DE KN4KYD" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _run error handling
# ---------------------------------------------------------------------------


class TestRunErrorHandling:
    @pytest.mark.asyncio
    async def test_run_audio_caps_does_not_connect(self, capsys) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=5.0,
            command="audio",
            audio_command="caps",
            json=True,
        )
        rc = await _run(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["default_channels"] == 2

    @pytest.mark.asyncio
    async def test_run_audio_caps_with_stats_connects(self, capsys) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=5.0,
            command="audio",
            audio_command="caps",
            json=True,
            stats=True,
        )
        runtime_stats = {
            "active": False,
            "state": "idle",
            "rx_packets_received": 2,
            "rx_packets_delivered": 2,
            "tx_packets_sent": 0,
            "packets_lost": 0,
            "packet_loss_percent": 0.0,
            "reorder_depth_ema_ms": 0.0,
            "jitter_max_ms": 0.0,
            "underrun_count": 0,
            "overrun_count": 0,
            "estimated_latency_ms": 0.0,
            "jitter_buffer_depth_packets": 5,
            "jitter_buffer_pending_packets": 0,
            "duplicates_dropped": 0,
            "stale_packets_dropped": 0,
            "out_of_order_packets": 0,
        }
        radio = AsyncMock()
        radio.__aenter__.return_value = radio
        radio.__aexit__.return_value = None
        radio.start_audio_rx_opus = AsyncMock()
        radio.stop_audio_rx_opus = AsyncMock()
        _add_capability_protocols(radio)
        radio.get_audio_stats = AsyncMock(return_value=runtime_stats)
        with patch("rigplane.cli.create_radio", return_value=radio) as mock_create:
            with patch("rigplane.cli.asyncio.sleep", new=AsyncMock()):
                rc = await _run(args)
        assert rc == 0
        mock_create.assert_called_once()
        radio.start_audio_rx_opus.assert_awaited_once()
        radio.stop_audio_rx_opus.assert_awaited_once()
        data = json.loads(capsys.readouterr().out)
        assert data["runtime_stats"]["rx_packets_received"] == 2

    @pytest.mark.asyncio
    async def test_run_exception(self, capsys) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=0.1,
            command="status",
            json=False,
        )
        # Mock create_radio to raise immediately instead of attempting real network connect
        mock_radio = MagicMock()
        mock_radio.__aenter__ = AsyncMock(
            side_effect=ConnectionError("test connection failed")
        )
        mock_radio.__aexit__ = AsyncMock(return_value=None)
        with patch("rigplane.cli.create_radio", return_value=mock_radio):
            rc = await _run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error" in err

    @pytest.mark.asyncio
    async def test_run_audio_rx_routes_to_handler(self) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=5.0,
            command="audio",
            audio_command="rx",
            output_file="rx.wav",
            seconds=1.0,
            sample_rate=48000,
            channels=1,
            json=False,
            stats=False,
        )
        radio = AsyncMock()
        radio.__aenter__.return_value = radio
        radio.__aexit__.return_value = None
        with patch("rigplane.cli.create_radio", return_value=radio):
            with patch("rigplane.cli._cmd_audio_rx", new_callable=AsyncMock) as cmd:
                cmd.return_value = 0
                rc = await _run(args)
        assert rc == 0
        cmd.assert_awaited_once_with(radio, args)

    @pytest.mark.asyncio
    async def test_run_audio_tx_routes_to_handler(self) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=5.0,
            command="audio",
            audio_command="tx",
            input_file="tx.wav",
            sample_rate=48000,
            channels=1,
            json=False,
            stats=False,
        )
        radio = AsyncMock()
        radio.__aenter__.return_value = radio
        radio.__aexit__.return_value = None
        with patch("rigplane.cli.create_radio", return_value=radio):
            with patch("rigplane.cli._cmd_audio_tx", new_callable=AsyncMock) as cmd:
                cmd.return_value = 0
                rc = await _run(args)
        assert rc == 0
        cmd.assert_awaited_once_with(radio, args)

    @pytest.mark.asyncio
    async def test_run_audio_loopback_routes_to_handler(self) -> None:
        args = Namespace(
            host="192.168.1.100",
            control_port=50001,
            user="",
            password="",
            timeout=5.0,
            command="audio",
            audio_command="loopback",
            seconds=1.0,
            sample_rate=48000,
            channels=1,
            json=False,
            stats=False,
        )
        radio = AsyncMock()
        radio.__aenter__.return_value = radio
        radio.__aexit__.return_value = None
        with patch("rigplane.cli.create_radio", return_value=radio):
            with patch(
                "rigplane.cli._cmd_audio_loopback",
                new_callable=AsyncMock,
            ) as cmd:
                cmd.return_value = 0
                rc = await _run(args)
        assert rc == 0
        cmd.assert_awaited_once_with(radio, args)


# ---------------------------------------------------------------------------
# _cmd_scope
# ---------------------------------------------------------------------------


class TestCmdScope:
    @pytest.mark.asyncio
    async def test_rejects_invalid_frames(self, mock_radio, capsys) -> None:
        args = Namespace(
            frames=0,
            width=800,
            capture_timeout=None,
            spectrum_only=False,
            json=True,
            output="scope.png",
            theme="classic",
        )
        rc = await _cmd_scope(mock_radio, args)
        assert rc == 1
        assert "--frames must be >= 1" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_rejects_invalid_width(self, mock_radio, capsys) -> None:
        args = Namespace(
            frames=1,
            width=10,
            capture_timeout=None,
            spectrum_only=True,
            json=True,
            output="scope.png",
            theme="classic",
        )
        rc = await _cmd_scope(mock_radio, args)
        assert rc == 1
        assert "--width must be >= 64" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_rejects_non_positive_timeout(self, mock_radio, capsys) -> None:
        args = Namespace(
            frames=1,
            width=800,
            capture_timeout=0.0,
            spectrum_only=True,
            json=True,
            output="scope.png",
            theme="classic",
        )
        rc = await _cmd_scope(mock_radio, args)
        assert rc == 1
        assert "--capture-timeout must be > 0" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() entrypoint
# ---------------------------------------------------------------------------


class TestMain:
    def test_no_command_prints_help(self, capsys) -> None:
        with patch("sys.argv", ["rigplane"]):
            # main with no command should print help and exit 0
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    def test_discover_command(self) -> None:
        # Just verify it doesn't crash on import/parse
        with patch("sys.argv", ["rigplane", "discover"]):
            # Discover will try UDP broadcast — mock socket
            with patch(
                "rigplane.cli._cmd_discover",
                new_callable=lambda: lambda: AsyncMock(return_value=0),
            ):
                pass  # We just test parsing works
