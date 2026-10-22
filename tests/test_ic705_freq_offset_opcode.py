"""IC-705 duplex offset: 0x0C reads, 0x0D sends (MOR-3591, evidence MOR-3650)."""

from __future__ import annotations

from rigplane.profiles import get_radio_profile

# Real IC-705 over USB CI-V, 2026-10-21: the request ``FE FE A4 E0 0C FD`` was
# answered ``FE FE E0 A4 0C 00 50 00 FD`` (500 kHz). The same radio NAKed 0D.
_BENCH_READ_REQUEST_OPCODE = (0x0C,)


def test_ic705_reads_duplex_offset_with_0c_and_sends_with_0d() -> None:
    commands = get_radio_profile("IC-705").command_map
    assert commands.get("get_freq_offset") == _BENCH_READ_REQUEST_OPCODE
    assert commands.get("set_freq_offset") == (0x0D,)
