"""VFO confirmation comes from the radio state, never the request (MOR-3821).

The reference server answered band changes with the requested frequency even
when the radio did not actually retune (owner evidence, 2026-10-30, C0
capture). The confirmation must come from Core's state after the radio
reports the frequency: when the radio keeps another frequency, the client
gets the real one back so it re-syncs. ``observe_store`` wires the
acquisition stand-in the way the real poller lands a write in the store.
"""

from __future__ import annotations

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.tci_adapter.handler import TciAdapterConfig

from tci_adapter_stub import (
    StubIcomRadio,
    StubWattsRadio,
    build_store,
    make_handler,
)


def _loaded_store():  # type: ignore[no-untyped-def]
    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 0.5,
        }
    )


def _CONFIRM_CASES():  # type: ignore[no-untyped-def]
    """Both channel slots on both radio shapes (no per-radio mechanism)."""
    return [
        pytest.param(StubIcomRadio(), 0, 14_075_000, id="icom-a"),
        pytest.param(StubIcomRadio(), 1, 14_076_000, id="icom-b"),
        pytest.param(StubWattsRadio(), 0, 14_075_000, id="yaesu-a"),
        pytest.param(StubWattsRadio(), 1, 14_076_000, id="yaesu-b"),
    ]


@pytest.mark.parametrize("radio,channel,requested", _CONFIRM_CASES())
async def test_ignored_set_answers_actual_frequency(radio, channel, requested) -> None:  # type: ignore[no-untyped-def]
    """The radio acks the set but keeps its frequency: the client re-syncs."""
    radio.ignore_freq_writes = True
    store = _loaded_store()
    radio.observe_store = store
    handler = make_handler(
        radio=radio,
        store=store,
        config=TciAdapterConfig(command_timeout=0.2),
    )
    outcome = await handler.on_command("vfo", ("0", str(channel), str(requested)))
    actual = 14_074_000
    assert outcome.to_sender == (f"vfo:0,{channel},{actual};",)
    assert outcome.to_others == (f"vfo:0,{channel},{actual};",)


@pytest.mark.parametrize("radio,channel,requested", _CONFIRM_CASES())
async def test_taken_set_answers_requested_frequency(radio, channel, requested) -> None:  # type: ignore[no-untyped-def]
    """The radio takes the frequency: the confirmation carries it, to all."""
    store = _loaded_store()
    radio.observe_store = store
    handler = make_handler(
        radio=radio,
        store=store,
        config=TciAdapterConfig(command_timeout=0.2),
    )
    outcome = await handler.on_command("vfo", ("0", str(channel), str(requested)))
    assert outcome.to_sender == (f"vfo:0,{channel},{requested};",)
    assert outcome.to_others == (f"vfo:0,{channel},{requested};",)
