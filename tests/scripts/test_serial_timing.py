"""Unit tests for scripts/bench/serial_timing.py (MOR-3839).

The fake serial doubles replay scripted replies with configurable delays;
no real port is ever opened. The IC-705 responder is a minimal stateful
CI-V peer (identity / power / PTT read-write with a switching delay).
"""

from __future__ import annotations

import importlib.util
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "bench" / "serial_timing.py"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_serial_timing", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


st = _load()

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame  # noqa: E402

RADIO_ADDR = 0xA4  # IC-705

KEY_ON = bytes.fromhex("fefea4e01c0001fd")
KEY_OFF = bytes.fromhex("fefea4e01c0000fd")


# ── fake serial ──────────────────────────────────────────────────────────


class VirtualClock:
    """Deterministic clock for the latency tests: ``advance`` moves time
    forward with no real sleeping. ``__call__`` is perf_counter-like
    (seconds), ``ns`` is perf_counter_ns-like.
    """

    def __init__(self, start_s: float = 1000.0) -> None:
        self.now = start_s

    def __call__(self) -> float:
        return self.now

    def advance(self, dt_s: float) -> None:
        self.now += dt_s

    def ns(self) -> int:
        return int(self.now * 1e9)


class FakeSerial:
    """Scripted serial double with real pyserial POSIX semantics.

    ``read(n)`` BLOCKS until n bytes have been buffered or the configured
    ``timeout`` has elapsed, then returns whatever there is (possibly short
    or empty); ``in_waiting`` reports the bytes already buffered. Blocking
    advances the injected clock (real ``time.sleep`` by default, a
    :class:`VirtualClock` for the latency tests) instead of returning early
    the way the old double did -- a short reply must NOT make ``read(64)``
    return before the timeout, because real pyserial does not.
    """

    def __init__(
        self, responder=None, *, clock=time.perf_counter, sleep=time.sleep
    ) -> None:
        self.writes: list[bytes] = []
        self.timeout: float | None = 0.05
        self.dtr = True
        self.rts = True
        self.opened = False
        self.port: str | None = None
        self.baudrate: int | None = None
        self._pending: list[tuple[float, bytes]] = []
        self._buf = bytearray()
        self._responder = responder
        self._clock = clock
        self._sleep = sleep

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def flush(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        data = bytes(data)
        self.writes.append(data)
        if self._responder is not None:
            for delay_s, payload in self._responder(data):
                self._pending.append((self._clock() + delay_s, payload))
        return len(data)

    def _materialize(self) -> None:
        """Move arrived payloads from the schedule into the read buffer."""
        now = self._clock()
        still_pending: list[tuple[float, bytes]] = []
        for available_at, payload in self._pending:
            if available_at <= now:
                self._buf += payload
            else:
                still_pending.append((available_at, payload))
        self._pending = still_pending

    @property
    def in_waiting(self) -> int:
        self._materialize()
        return len(self._buf)

    def read(self, n: int = 1) -> bytes:
        deadline = None if self.timeout is None else self._clock() + self.timeout
        while True:
            self._materialize()
            if len(self._buf) >= n:
                break
            now = self._clock()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                break  # timeout expired: return what there is (maybe short)
            if self._pending:
                until_next = max(0.0, min(at for at, _ in self._pending) - now)
                wait = until_next if remaining is None else min(remaining, until_next)
            elif remaining is None:
                break  # block-forever with nothing coming: cannot hang tests
            else:
                wait = remaining
            self._sleep(wait)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class OrderTrackingSerial(FakeSerial):
    """Records attribute writes and open() ordering for the dtr/rts test."""

    def __init__(self, responder=None) -> None:
        object.__setattr__(self, "log", [])
        super().__init__(responder)

    def __setattr__(self, name, value):
        if name in ("dtr", "rts"):
            self.log.append(("setattr", name, value, self.__dict__.get("opened")))
        super().__setattr__(name, value)

    def open(self) -> None:
        self.log.append(("open", None, None, None))
        super().open()


class FakeSerialModule:
    """Stands in for the pyserial module."""

    def __init__(self, ser: FakeSerial) -> None:
        self._ser = ser

    def Serial(self) -> FakeSerial:  # noqa: N802 - mirrors pyserial's API
        return self._ser


def make_ic705_responder(
    *,
    power_raw: int = 0,
    ptt_switch_s: float = 0.01,
    ptt_off_switch_s: float | None = None,
    reply_delay_s: float = 0.001,
    wrong_identity: bool = False,
):
    """Minimal IC-705 CI-V peer; returns (responder, state).

    ``ptt_off_switch_s`` overrides the read-back delay for the TX->RX
    switch only (defaults to ``ptt_switch_s``), so tests can give the two
    directions different read-back latencies.
    """
    off_switch_s = ptt_switch_s if ptt_off_switch_s is None else ptt_off_switch_s
    state = {"ptt": False, "switched_at": 0.0}

    def reply(command: int, sub: int | None, payload: bytes | None):
        return [
            (
                reply_delay_s,
                build_civ_frame(
                    CONTROLLER_ADDR, RADIO_ADDR, command, sub=sub, data=payload
                ),
            )
        ]

    def respond(req: bytes):
        frame = parse_civ_frame(req)
        cmd, sub, data = frame.command, frame.sub, frame.data
        if (cmd, sub) == (0x19, 0x00):  # identity
            return reply(0x19, 0x00, bytes([0x94 if wrong_identity else RADIO_ADDR]))
        if (cmd, sub) == (0x14, 0x0A):  # RF power
            tens_units = ((power_raw % 100) // 10 << 4) | (power_raw % 10)
            return reply(0x14, 0x0A, bytes([power_raw // 100, tens_units]))
        if (cmd, sub) == (0x14, 0x01):  # AF level
            if data:  # set-to-current: ACK
                return [
                    (reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFB))
                ]
            return reply(0x14, 0x01, bytes([0x01, 0x28]))
        if (cmd, sub) == (0x1C, 0x00):  # PTT
            if data:
                state["ptt"] = data == b"\x01"
                state["switched_at"] = time.perf_counter()
                return [
                    (reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFB))
                ]
            switch_s = ptt_switch_s if state["ptt"] else off_switch_s
            if time.perf_counter() - state["switched_at"] < switch_s:
                # still switching: report the OLD state
                return reply(0x1C, 0x00, b"\x00" if state["ptt"] else b"\x01")
            return reply(0x1C, 0x00, b"\x01" if state["ptt"] else b"\x00")
        if cmd == 0x03:  # freq
            return reply(0x03, None, bytes.fromhex("0040071400"))
        if cmd == 0x04:  # mode
            return reply(0x04, None, bytes([0x01, 0x01]))
        if (cmd, sub) == (0x15, 0x02):  # s-meter
            return reply(0x15, 0x02, bytes([0x00, 0x80]))
        return [(reply_delay_s, build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, 0xFA))]

    return respond, state


def _free_port_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")


def _held_port_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args,
        returncode=0,
        stdout="COMMAND PID USER FD TYPE DEVICE\nfoo 123 moroz 3u CHR /dev/x\n",
        stderr="",
    )


def _civ_row(direction: str, frame: bytes, t: float) -> dict[str, object]:
    return {"dir": direction, "frame": frame.hex(), "t": t}


def _req(command: int, sub: int | None = None, data: bytes | None = None) -> bytes:
    return build_civ_frame(RADIO_ADDR, CONTROLLER_ADDR, command, sub=sub, data=data)


def _rep(command: int, sub: int | None = None, data: bytes | None = None) -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, RADIO_ADDR, command, sub=sub, data=data)


# ── classification ───────────────────────────────────────────────────────


def test_classify_civ_frame_with_profile_signatures() -> None:
    sigs = [
        ("get_freq", 0x03, None, b""),
        ("set_freq", 0x05, None, b""),
        ("get_af_level", 0x14, 0x01, b""),
        ("set_af_level", 0x14, 0x01, b""),
        ("get_acc1_mod_level", 0x1A, 0x05, b"\x00\x64"),
        ("set_acc1_mod_level", 0x1A, 0x05, b"\x00\x64"),
    ]

    def cls(command, sub=None, data=None):
        return st.classify_civ_frame(parse_civ_frame(_req(command, sub, data)), sigs)

    assert cls(0x03) == "read"
    assert cls(0x05, data=bytes.fromhex("0040071400")) == "set"
    assert cls(0x14, 0x01) == "read"
    assert cls(0x14, 0x01, bytes([0x01, 0x28])) == "set"
    # extended-address read: data equals the declared prefix exactly
    assert cls(0x1A, 0x05, bytes([0x00, 0x64])) == "read"
    assert cls(0x1A, 0x05, bytes([0x00, 0x64, 0x80])) == "set"
    assert cls(0x1C, 0x00) == "ptt_read"
    assert cls(0x1C, 0x00, b"\x01") == "ptt_set"
    assert cls(0x1C, 0x00, b"\x02") == "other"
    assert cls(0x26, 0x00, b"\x01") == "other"  # undeclared, carries data


def test_classify_civ_frame_fallback_table() -> None:
    def cls(command, sub=None, data=None):
        return st.classify_civ_frame(parse_civ_frame(_req(command, sub, data)))

    assert cls(0x03) == "read"
    assert cls(0x05, data=bytes.fromhex("0040071400")) == "set"
    assert cls(0x1C, 0x00) == "ptt_read"
    assert cls(0x1C, 0x00, b"\x01") == "ptt_set"
    assert cls(0x99, data=b"\x01") == "other"


def test_classify_cat_line() -> None:
    assert st.classify_cat_line("TX;") == "ptt_read"
    assert st.classify_cat_line("TX1;") == "ptt_set"
    assert st.classify_cat_line("TX0;") == "ptt_set"
    assert st.classify_cat_line("FA;") == "read"
    # Without templates a parameterized line is "other", never silently "set".
    assert st.classify_cat_line("FA00014074000;") == "other"
    assert st.classify_cat_line("SM0;") == "other"
    # With the profile's templates the shape is decided by declaration.
    assert st.classify_cat_line("SM0;", read_templates=["SM{receiver};"]) == "read"
    assert (
        st.classify_cat_line("FA00014074000;", write_templates=["FA{freq:09d};"])
        == "set"
    )


# ── from-recordings pairing ──────────────────────────────────────────────


def test_derive_civ_simple_pair() -> None:
    rows = [
        _civ_row("c2r", _req(0x03), 1.000),
        _civ_row("r2c", _rep(0x03, data=bytes.fromhex("0040071400")), 1.004),
    ]
    result = st.derive_civ_latencies(rows)
    assert result.samples["read"] == [pytest.approx(4.0)]
    assert result.unpaired == 0
    assert result.echoes == 0


def test_derive_civ_set_pair_acked() -> None:
    rows = [
        _civ_row("c2r", _req(0x05, data=bytes.fromhex("0040071400")), 2.000),
        _civ_row("r2c", _rep(0xFB), 2.007),
    ]
    result = st.derive_civ_latencies(rows)
    assert result.samples["set"] == [pytest.approx(7.0)]


def test_derive_civ_skips_echo_and_unsolicited() -> None:
    request = _req(0x03)
    echo = bytes.fromhex(request.hex())  # same bytes, recorded on the r2c leg
    scope_wave = _rep(0x27, 0x00, b"\x00" * 10)  # unsolicited scope data
    rows = [
        _civ_row("c2r", request, 1.000),
        _civ_row("r2c", echo, 1.001),  # IC-7300-style echo of our own frame
        _civ_row("r2c", scope_wave, 1.002),  # scope wave between request and reply
        _civ_row("r2c", _rep(0x03, data=bytes.fromhex("0040071400")), 1.010),
    ]
    result = st.derive_civ_latencies(rows)
    assert result.samples["read"] == [pytest.approx(10.0)]
    assert result.echoes == 1
    assert result.unsolicited == 1
    assert result.unpaired == 0


def test_derive_civ_unsolicited_only_when_idle() -> None:
    rows = [
        _civ_row("r2c", _rep(0x27, 0x00, b"\x00" * 10), 1.0),
        _civ_row("c2r", _req(0x1C, 0x00), 2.0),
        _civ_row("r2c", _rep(0x1C, 0x00, b"\x00"), 2.003),
    ]
    result = st.derive_civ_latencies(rows)
    assert result.samples["ptt_read"] == [pytest.approx(3.0)]
    assert result.unsolicited == 1


def test_derive_cat_pairs() -> None:
    rows = [
        {"dir": "c2r", "line": "TX;", "t": 1.000},
        {"dir": "r2c", "line": "TX0;", "t": 1.009},
        {"dir": "c2r", "line": "FA;", "t": 2.000},
        {"dir": "r2c", "line": "FA00014074000;", "t": 2.009},
        {"dir": "c2r", "line": "TX1;", "t": 3.000},
        {"dir": "r2c", "line": "TX1;", "t": 3.004},  # echo-style ack of the set
    ]
    result = st.derive_cat_latencies(rows)
    assert result.samples["ptt_read"] == [pytest.approx(9.0)]
    assert result.samples["read"] == [pytest.approx(9.0)]
    assert result.samples["ptt_set"] == [pytest.approx(4.0)]


# ── document / summarize ─────────────────────────────────────────────────


def test_document_schema_shape() -> None:
    doc = st.build_document(
        radio_id="ic705",
        protocol="civ",
        port_kind="usb",
        baud=115200,
        samples={"read": [1.0, 2.0, 3.0, 100.0]},
        notes=["note"],
        host="host",
        generated_at="2026-10-28T00:00:00+00:00",
        git_sha="abc123",
    )
    assert set(doc) == {
        "schema",
        "radio_id",
        "protocol",
        "port_kind",
        "baud",
        "host",
        "generated_at",
        "git_sha",
        "classes",
        "notes",
    }
    assert doc["schema"] == "rigplane-serial-timing/1"
    entry = doc["classes"]["read"]
    assert set(entry) == {"n", "samples_ms", "median", "p95", "max"}
    assert entry["n"] == 4
    assert entry["median"] == 2.5
    assert entry["p95"] == 100.0
    assert entry["max"] == 100.0


def test_summarize_prints_table() -> None:
    doc = st.build_document(
        radio_id="ic705",
        protocol="civ",
        port_kind="usb",
        baud=115200,
        samples={"read": [1.0, 2.0, 3.0]},
        notes=[],
        host="h",
        generated_at="g",
        git_sha="s",
    )
    table = st.summarize_documents([doc])
    assert "| class | n | median ms | p95 ms | max ms |" in table
    assert "| read | 3 | 2.0 | 3.0 | 3.0 |" in table
    assert "ic705" in table


def test_main_summarize_reads_files(tmp_path, capsys) -> None:
    doc = st.build_document(
        radio_id="ftx1",
        protocol="cat",
        port_kind="usb",
        baud=38400,
        samples={"read": [5.0]},
        notes=[],
        host="h",
        generated_at="g",
        git_sha="s",
    )
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc))
    assert st.main(["summarize", str(path)]) == 0
    out = capsys.readouterr().out
    assert "ftx1" in out and "read" in out


# ── smoke: real fixtures ─────────────────────────────────────────────────


@pytest.mark.parametrize("radio", ["ic705", "ic7300", "ic7610"])
def test_from_recordings_real_civ_fixtures_smoke(radio: str) -> None:
    fixture = _FIXTURES / radio / "civ_pairs.jsonl"
    printed: list[str] = []
    code = st.main(["from-recordings", "--fixture", str(fixture)], out=printed.append)
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["schema"] == "rigplane-serial-timing/1"
    assert doc["radio_id"] == radio
    assert doc["protocol"] == "civ"
    read = doc["classes"]["read"]
    # Plausible, not exact: real probe latencies live in the 1 ms - 2 s band.
    assert read["n"] > 10
    assert 0.0 < read["median"] < 2000.0
    assert read["median"] <= read["p95"] <= read["max"]


def test_from_recordings_real_cat_fixture_smoke() -> None:
    fixture = _FIXTURES / "ftx1" / "cat_pairs.jsonl"
    printed: list[str] = []
    code = st.main(["from-recordings", "--fixture", str(fixture)], out=printed.append)
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["protocol"] == "cat"
    read = doc["classes"]["read"]
    assert read["n"] > 5
    assert 0.0 < read["median"] < 2000.0


def test_from_recordings_merges_probe_answers() -> None:
    printed: list[str] = []
    code = st.main(
        [
            "from-recordings",
            "--fixture",
            str(_FIXTURES / "ic705" / "civ_pairs.jsonl"),
            "--probe-answers",
            str(_FIXTURES / "ic705" / "probe_answers.json"),
        ],
        out=printed.append,
    )
    assert code == 0
    doc = json.loads(printed[0])
    assert any("probe-answer" in note for note in doc["notes"])


# ── serial open / port-held refusals ─────────────────────────────────────


def test_open_serial_forces_dtr_rts_false_before_open() -> None:
    ser = OrderTrackingSerial()
    ser.log.clear()  # ignore the constructor's own attribute writes
    module = FakeSerialModule(ser)
    st.open_serial_port("/dev/fake", 115200, serial_module=module)
    names = [entry[0] for entry in ser.log]
    assert names == ["setattr", "setattr", "open"]
    attrs = {
        entry[1]: (entry[2], entry[3]) for entry in ser.log if entry[0] == "setattr"
    }
    assert attrs == {"dtr": (False, False), "rts": (False, False)}


def test_capture_refuses_unknown_radio() -> None:
    printed: list[str] = []
    code = st.main(
        ["capture", "rx", "--radio", "nope", "--port", "/dev/x", "--baud", "115200"],
        serial_module=FakeSerialModule(FakeSerial()),
        runner=_free_port_runner,
        out=printed.append,
    )
    assert code == 2
    assert printed == []


def test_capture_refuses_held_port() -> None:
    printed: list[str] = []
    code = st.main(
        ["capture", "rx", "--radio", "ic705", "--port", "/dev/x", "--baud", "115200"],
        serial_module=FakeSerialModule(FakeSerial()),
        runner=_held_port_runner,
        out=printed.append,
    )
    assert code == 2
    assert printed == []


def test_capture_rx_identity_refusal() -> None:
    responder, _state = make_ic705_responder(wrong_identity=True)
    printed: list[str] = []
    code = st.main(
        [
            "capture",
            "rx",
            "--radio",
            "ic705",
            "--port",
            "/dev/x",
            "--baud",
            "115200",
            "--n",
            "1",
        ],
        serial_module=FakeSerialModule(FakeSerial(responder)),
        runner=_free_port_runner,
        out=printed.append,
    )
    assert code == 2
    assert printed == []


# ── capture rx ───────────────────────────────────────────────────────────


def test_rx_capture_writes_exact_bytes_and_classes() -> None:
    responder, _state = make_ic705_responder()
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc = st.run_rx_capture(
        ser=ser, config=config, radio_key="ic705", baud=115200, n=2, pace_s=0.0
    )
    hex_writes = [w.hex() for w in ser.writes]
    # identity check, then per rep: freq, mode, s-meter, ptt read, id,
    # af-level read + set-to-current + read-back
    assert "fefea4e01900fd" in hex_writes
    assert "fefea4e003fd" in hex_writes
    assert "fefea4e01c00fd" in hex_writes
    assert "fefea4e01401fd" in hex_writes
    assert "fefea4e014010128fd" in hex_writes  # writes back exactly what was read
    assert KEY_ON not in [bytes(w) for w in ser.writes]  # rx never keys
    classes = doc["classes"]
    assert classes["ack_read"]["n"] == 12  # (5 probes + 1 af read) * 2 reps
    assert classes["ack_set"]["n"] == 2
    assert classes["frame_read"]["n"] == 12
    assert classes["ptt_read_idle"]["n"] == 2
    assert classes["write_drain"]["n"] == 14  # (5 probes + af read + af set) * 2 reps
    for name in ("ack_read", "ack_set", "frame_read", "write_drain"):
        assert classes[name]["median"] >= 0.0
        assert classes[name]["median"] < 1000.0


# ── capture tx refusals ──────────────────────────────────────────────────


def _tx_main(extra, responder=None, runner=_free_port_runner):
    ser = FakeSerial(responder if responder is not None else make_ic705_responder()[0])
    printed: list[str] = []
    code = st.main(
        [
            "capture",
            "tx",
            "--radio",
            "ic705",
            "--port",
            "/dev/x",
            "--baud",
            "115200",
            *extra,
        ],
        serial_module=FakeSerialModule(ser),
        runner=runner,
        out=printed.append,
    )
    return code, printed, ser


def test_tx_requires_arm_flags() -> None:
    for extra in ([], ["--tx-armed"], ["--dummy-load-confirmed"]):
        code, printed, ser = _tx_main(extra)
        assert code == 2
        assert printed == []
        assert ser.writes == []  # refused before any write
        assert not ser.opened


def test_tx_refuses_non_ic705() -> None:
    responder, _state = make_ic705_responder()
    printed: list[str] = []
    code = st.main(
        [
            "capture",
            "tx",
            "--radio",
            "ic7300",
            "--port",
            "/dev/x",
            "--baud",
            "115200",
            "--tx-armed",
            "--dummy-load-confirmed",
        ],
        serial_module=FakeSerialModule(FakeSerial(responder)),
        runner=_free_port_runner,
        out=printed.append,
    )
    assert code == 2
    assert printed == []


def test_tx_refuses_power_above_profile_minimum() -> None:
    responder, _state = make_ic705_responder(power_raw=5)
    config = st.load_profiles()["ic705"]
    ser = FakeSerial(responder)
    with pytest.raises(st.RefusalError, match="power"):
        st.run_tx_capture(
            ser=ser, config=config, radio_key="ic705", baud=115200, reps=1
        )
    assert KEY_ON not in [bytes(w) for w in ser.writes]  # never keyed


# ── capture tx paths ─────────────────────────────────────────────────────


def _fast_tx_kwargs() -> dict:
    return dict(
        reps=2,
        hold_s=0.02,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )


def test_tx_happy_path_measures_switch_times() -> None:
    responder, state = make_ic705_responder(ptt_switch_s=0.01)
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser, config=config, radio_key="ic705", baud=115200, **_fast_tx_kwargs()
    )
    assert code == 0
    hex_writes = [w.hex() for w in ser.writes]
    assert KEY_ON.hex() in hex_writes
    assert KEY_OFF.hex() in hex_writes
    assert state["ptt"] is False  # left unkeyed
    classes = doc["classes"]
    for name in (
        "write_drain",
        "ack_read",
        "ack_set",
        "ptt_read_idle",
        "ptt_set_ack_on",
        "ptt_set_ack_off",
        "tx_on_switch",
        "tx_off_switch",
        "ack_read_while_switching",
    ):
        assert name in classes, name
    assert classes["tx_on_switch"]["n"] == 2
    assert classes["tx_off_switch"]["n"] == 2
    # switching takes ~10 ms in the fake; sanity-bound, not exact
    assert 1.0 < classes["tx_on_switch"]["median"] < 1000.0
    assert 1.0 < classes["tx_off_switch"]["median"] < 1000.0
    assert doc["schema"] == "rigplane-serial-timing/1"


def test_tx_watchdog_trip_unkeys_and_exits_1() -> None:
    # The fake never finishes switching (10 s), the keyed-time cap is 50 ms:
    # the watchdog trips long before the 1.5 s switch deadline.
    responder, _state = make_ic705_responder(ptt_switch_s=10.0)
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=2,
        hold_s=0.02,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
        cap_keyed_s=0.05,
    )
    assert code == 1
    hex_writes = [w.hex() for w in ser.writes]
    assert KEY_ON.hex() in hex_writes
    assert hex_writes.count(KEY_OFF.hex()) >= 1  # emergency unkey sent
    assert any("ABORTED" in note for note in doc["notes"])
    assert any("emergency unkey" in note for note in doc["notes"])


def test_tx_exception_mid_hold_unkeys_and_exits_1() -> None:
    responder, _state = make_ic705_responder(ptt_switch_s=0.005)
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]

    def exploding_sleep(seconds: float) -> None:
        if seconds >= 0.2:  # the 300 ms hold
            raise RuntimeError("boom mid-hold")
        time.sleep(min(seconds, 0.02))

    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=1,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
        sleep=exploding_sleep,
    )
    assert code == 1
    hex_writes = [w.hex() for w in ser.writes]
    assert KEY_ON.hex() in hex_writes
    assert hex_writes.count(KEY_OFF.hex()) >= 1
    assert any("boom mid-hold" in note for note in doc["notes"])


def test_tx_via_main_end_to_end() -> None:
    code, printed, ser = _tx_main(
        ["--tx-armed", "--dummy-load-confirmed", "--reps", "1"],
    )
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["classes"]["tx_on_switch"]["n"] == 1
    assert KEY_ON.hex() in [w.hex() for w in ser.writes]
    assert not ser.opened  # port closed last


# ── A. TX safety rework (MOR-3839 review) ────────────────────────────────


def _keyed_total_from_notes(notes: list[str]) -> float:
    note = next(n for n in notes if "total keyed time" in n)
    match = re.search(r"([0-9]+\.[0-9]+)\s*s", note)
    assert match is not None, note
    return float(match.group(1))


def test_tx_key_write_without_reply_still_counts_keyed_time() -> None:
    # The key frame is written and keys the radio, but the ACK never comes:
    # keyed time must count from BEFORE the write, not from the reply.
    base_responder, state = make_ic705_responder(ptt_switch_s=0.005)

    def responder(req: bytes):
        frame = parse_civ_frame(req)
        if (frame.command, frame.sub) == (0x1C, 0x00) and frame.data == b"\x01":
            base_responder(req)  # the radio keys...
            return []  # ...but the ACK is lost on the wire
        return base_responder(req)

    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=1,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )
    assert code == 1  # reply timeout => abort path
    hex_writes = [w.hex() for w in ser.writes]
    assert KEY_ON.hex() in hex_writes
    assert hex_writes.count(KEY_OFF.hex()) >= 1  # emergency unkey sent
    assert state["ptt"] is False
    assert _keyed_total_from_notes(doc["notes"]) > 0.0


def test_tx_keyed_time_runs_until_rx_readback() -> None:
    # RX read-back arrives 400 ms after the unkey ACK: the keyed total must
    # include it (keyed time ends at the read-back, not at the ACK).
    responder, _state = make_ic705_responder(ptt_switch_s=0.005, ptt_off_switch_s=0.4)
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=1,
        hold_s=0.01,
        poll_s=0.002,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )
    assert code == 0
    assert _keyed_total_from_notes(doc["notes"]) >= 0.3


def test_open_serial_sets_write_timeout() -> None:
    ser = FakeSerial()
    st.open_serial_port("/dev/fake", 115200, serial_module=FakeSerialModule(ser))
    assert ser.write_timeout == 1.0


def _no_lsof_runner(*args, **kwargs):
    raise FileNotFoundError("lsof")


def test_capture_tx_refuses_when_lsof_unavailable() -> None:
    code, printed, ser = _tx_main(
        ["--tx-armed", "--dummy-load-confirmed", "--reps", "1"],
        runner=_no_lsof_runner,
    )
    assert code == 2  # fail closed
    assert printed == []
    assert ser.writes == []
    assert not ser.opened


def test_capture_rx_keeps_only_a_note_when_lsof_unavailable() -> None:
    responder, _state = make_ic705_responder()
    printed: list[str] = []
    code = st.main(
        [
            "capture",
            "rx",
            "--radio",
            "ic705",
            "--port",
            "/dev/x",
            "--baud",
            "115200",
            "--n",
            "1",
        ],
        serial_module=FakeSerialModule(FakeSerial(responder)),
        runner=_no_lsof_runner,
        out=printed.append,
    )
    assert code == 0  # rx never keys: note, not refusal
    assert json.loads(printed[0])["schema"] == "rigplane-serial-timing/1"


def test_tx_sigterm_during_keyed_write_aborts_and_restores_handler() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    base_responder, state = make_ic705_responder(ptt_switch_s=0.005)

    def responder(req: bytes):
        frame = parse_civ_frame(req)
        if (frame.command, frame.sub) == (0x1C, 0x00) and frame.data == b"\x01":
            handler = signal.getsignal(signal.SIGTERM)
            # installed by _cmd_capture before the first keyed write
            assert callable(handler) and handler is not previous
            base_responder(req)  # the radio keys
            handler(signal.SIGTERM, None)  # deliver SIGTERM the way the OS would
        return base_responder(req)

    ser = FakeSerial(responder)
    printed: list[str] = []
    code = st.main(
        [
            "capture",
            "tx",
            "--radio",
            "ic705",
            "--port",
            "/dev/x",
            "--baud",
            "115200",
            "--tx-armed",
            "--dummy-load-confirmed",
            "--reps",
            "1",
        ],
        serial_module=FakeSerialModule(ser),
        runner=_free_port_runner,
        out=printed.append,
    )
    assert code == 1
    doc = json.loads(printed[0])
    assert any("KeyboardInterrupt" in note for note in doc["notes"])
    hex_writes = [w.hex() for w in ser.writes]
    assert hex_writes.count(KEY_OFF.hex()) >= 1  # emergency unkey sent
    assert state["ptt"] is False
    assert signal.getsignal(signal.SIGTERM) is previous  # restored in finally


def test_tx_records_final_power_readback() -> None:
    responder, _state = make_ic705_responder(power_raw=0)
    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=1,
        hold_s=0.01,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )
    assert code == 0
    assert any("final RF power raw 0" in note for note in doc["notes"])


def test_tx_fails_document_when_power_changed() -> None:
    base_responder, _state = make_ic705_responder(power_raw=0)
    power_reads = {"n": 0}

    def responder(req: bytes):
        frame = parse_civ_frame(req)
        if (frame.command, frame.sub) == (0x14, 0x0A):
            power_reads["n"] += 1
            if power_reads["n"] > 1:  # the re-read after the last rep
                return [
                    (
                        0.001,
                        build_civ_frame(
                            CONTROLLER_ADDR,
                            RADIO_ADDR,
                            0x14,
                            sub=0x0A,
                            data=bytes([0x00, 0x05]),  # raw 5, was 0
                        ),
                    )
                ]
        return base_responder(req)

    ser = FakeSerial(responder)
    config = st.load_profiles()["ic705"]
    doc, code = st.run_tx_capture(
        ser=ser,
        config=config,
        radio_key="ic705",
        baud=115200,
        reps=1,
        hold_s=0.01,
        poll_s=0.001,
        read_offset_s=0.005,
        watchdog_tick_s=0.001,
    )
    assert code == 1  # document failed
    assert any("final RF power raw 5" in note for note in doc["notes"])
    assert any("power changed" in note.lower() for note in doc["notes"])


# ── B. SerialCivRecorder JSONL input ───────────────────────────────────────


def test_from_recordings_accepts_serial_recorder_jsonl(tmp_path) -> None:
    fixture_dir = tmp_path / "ic705"
    fixture_dir.mkdir()
    fixture = fixture_dir / "recorder.jsonl"
    request = _req(0x03)
    reply = _rep(0x03, data=bytes.fromhex("0040071400"))
    rows = [
        # the request frame split across two chunks
        {"t": 1.000, "dir": "c2r", "port": "serial", "hex": request[:3].hex()},
        {"t": 1.001, "dir": "c2r", "port": "serial", "hex": request[3:].hex()},
        {"t": 1.009, "dir": "r2c", "port": "serial", "hex": reply.hex()},
    ]
    fixture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    printed: list[str] = []
    code = st.main(["from-recordings", "--fixture", str(fixture)], out=printed.append)
    assert code == 0
    doc = json.loads(printed[0])
    assert doc["protocol"] == "civ"
    # the split frame carries the t of the chunk that completed it (1.001)
    assert doc["classes"]["read"]["samples_ms"] == [pytest.approx(8.0)]
    assert doc["classes"]["read"]["n"] == 1


# ── C. summarize --timing-toml ────────────────────────────────────────────


def test_timing_toml_mapping_constant() -> None:
    assert [key for key, _sources in st.TIMING_TOML_MAPPING] == [
        "reply_latency_ms",
        "ack_ms_set",
        "ptt_set_ack_ms",
        "write_drain_ms",
        "tx_on_switch_ms",
        "tx_off_switch_ms",
        "readback_ms",
    ]
    assert dict(st.TIMING_TOML_MAPPING) == {
        "reply_latency_ms": ("ack_read",),
        "ack_ms_set": ("ack_set",),
        "ptt_set_ack_ms": ("ptt_set_ack_on", "ptt_set_ack_off"),
        "write_drain_ms": ("write_drain",),
        "tx_on_switch_ms": ("tx_on_switch",),
        "tx_off_switch_ms": ("tx_off_switch",),
        "readback_ms": ("ptt_read_idle",),
    }


def _timing_doc(samples: dict[str, list[float]]) -> dict[str, object]:
    return st.build_document(
        radio_id="ic705",
        protocol="civ",
        port_kind="serial",
        baud=115200,
        samples=samples,
        notes=[],
        host="h",
        generated_at="g",
        git_sha="s",
    )


def test_timing_toml_block_values() -> None:
    doc = _timing_doc(
        {
            "ack_read": [10.0, 12.0, 14.0],
            "ack_set": [20.0],
            "ptt_set_ack_on": [30.0],
            "ptt_set_ack_off": [40.0, 50.0],
            "write_drain": [1.0, 3.0],
            "tx_on_switch": [100.0],
            "tx_off_switch": [200.0],
            "ptt_read_idle": [5.0],
        }
    )
    block = st.timing_toml_block(doc)
    assert "[timing]" in block.splitlines()
    assignments = dict(
        line.split(" = ") for line in block.splitlines() if " = " in line
    )
    assert assignments == {
        "reply_latency_ms": "12.0",
        "ack_ms_set": "20.0",
        "ptt_set_ack_ms": "40.0",  # median of the on+off samples together
        "write_drain_ms": "2.0",
        "tx_on_switch_ms": "100.0",
        "tx_off_switch_ms": "200.0",
        "readback_ms": "5.0",
    }


def test_timing_toml_block_omits_classes_without_samples() -> None:
    block = st.timing_toml_block(_timing_doc({"ack_read": [7.0]}))
    assert "reply_latency_ms = 7.0" in block
    for key in (
        "ack_ms_set",
        "ptt_set_ack_ms",
        "write_drain_ms",
        "tx_on_switch_ms",
        "tx_off_switch_ms",
        "readback_ms",
    ):
        assert key not in block


def test_main_summarize_timing_toml(tmp_path, capsys) -> None:
    doc = _timing_doc({"ack_read": [7.0], "tx_on_switch": [100.0]})
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc))
    assert st.main(["summarize", "--timing-toml", str(path)]) == 0
    out = capsys.readouterr().out
    assert "[timing]" in out
    assert "reply_latency_ms = 7.0" in out
    assert "tx_on_switch_ms = 100.0" in out
    assert "ack_ms_set" not in out


# ── D. latency measurement discipline (MOR-3839 review, live RX capture) ──
#
# The live bench capture measured ~22.6 ms median on BOTH radios while the
# Core-level probes show 3.3 ms for the same command: ``ser.read(64)`` with
# ``timeout=0.02`` only returns once 64 bytes arrived or the 20 ms timeout
# expired, quantising every measured latency to ~20 ms. These tests pin the
# fix: block for the FIRST byte (read(1)), stamp first_ns the moment read(1)
# returns, then drain whatever is already buffered via in_waiting.


def _virtual_serial(responder) -> tuple[FakeSerial, VirtualClock]:
    clock = VirtualClock()
    return FakeSerial(responder, clock=clock, sleep=clock.advance), clock


def test_fake_serial_read_blocks_until_n_bytes_or_timeout() -> None:
    # pyserial POSIX semantics: read(64) with only 3 bytes ever arriving
    # returns those 3 bytes only when the configured timeout has elapsed.
    ser, clock = _virtual_serial(lambda req: [(0.003, b"abc")])
    ser.timeout = 0.020
    t0 = clock.now
    ser.write(b"req")
    assert ser.read(64) == b"abc"
    assert clock.now - t0 == pytest.approx(0.020)
    # read(1) returns the moment ONE byte is buffered, not at the timeout.
    ser2, clock2 = _virtual_serial(lambda req: [(0.003, b"abc")])
    ser2.timeout = 0.020
    ser2.write(b"req")
    assert ser2.read(1) == b"a"
    assert clock2.now == pytest.approx(1000.003)
    assert ser2.in_waiting == 2  # buffered, not yet read


def test_civ_reply_latency_is_not_quantised_to_the_read_timeout() -> None:
    # A reply arriving 3 ms after the write must measure 3 ms +- 1 ms.
    # Before the fix this measured ~20 ms (the read(64) timeout).
    responder, _state = make_ic705_responder(reply_delay_s=0.003)
    ser, clock = _virtual_serial(responder)
    reply = st.civ_transact(ser, _req(0x03), timeout_s=1.0, clock=clock.ns)
    assert reply.ack_ms == pytest.approx(3.0, abs=1.0)
    assert reply.frame_ms == pytest.approx(3.0, abs=1.0)


def test_cat_reply_latency_is_not_quantised_to_the_read_timeout() -> None:
    ser, clock = _virtual_serial(lambda req: [(0.003, b"FA00014074000;")])
    ser.write(b"FA;")
    t1 = clock.ns()
    got = st.read_cat_line(ser, deadline_ns=t1 + 1_000_000_000, clock=clock.ns)
    assert got is not None
    line, first_ns, complete_ns = got
    assert line == "FA00014074000;"
    assert (first_ns - t1) / 1e6 == pytest.approx(3.0, abs=1.0)
    assert (complete_ns - t1) / 1e6 == pytest.approx(3.0, abs=1.0)


def test_civ_multichunk_frame_completes_at_the_second_chunk() -> None:
    # Frame split into two writes 2 ms apart: ack stamps the first chunk,
    # the frame completes at the second chunk's arrival.
    reply_frame = _rep(0x03, data=bytes.fromhex("0040071400"))
    half = len(reply_frame) // 2

    def responder(req: bytes):
        return [(0.003, reply_frame[:half]), (0.005, reply_frame[half:])]

    ser, clock = _virtual_serial(responder)
    reply = st.civ_transact(ser, _req(0x03), timeout_s=1.0, clock=clock.ns)
    assert reply.raw == reply_frame
    assert reply.ack_ms == pytest.approx(3.0, abs=1.0)
    assert reply.frame_ms == pytest.approx(5.0, abs=1.0)


def test_civ_echo_then_reply_still_measures_the_reply() -> None:
    # Echo-enabled link: our own request comes back first (1 ms), the real
    # reply at 4 ms. The echo never marks first_ns.
    request = _req(0x03)
    reply_frame = _rep(0x03, data=bytes.fromhex("0040071400"))

    def responder(req: bytes):
        return [(0.001, req), (0.004, reply_frame)]

    ser, clock = _virtual_serial(responder)
    reply = st.civ_transact(ser, request, timeout_s=1.0, clock=clock.ns)
    assert reply.raw == reply_frame
    assert reply.ack_ms == pytest.approx(4.0, abs=1.0)
    assert reply.frame_ms == pytest.approx(4.0, abs=1.0)
