"""The keystone test for MOR-2006 Steps 5..N of
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` (§4, §6, §7).

Plan §7: "the dangerous half is requests moved, replies not: reads fail on
the 46 class-A rows and keep working everywhere else, which looks like an
intermittent radio rather than a bug." A getter whose request now goes
through the profile's own map but whose reply matcher still expects the
old, hardcoded command/sub bytes silently mismatches on exactly the
commands this migration fixes, and nowhere else.

This test makes that state impossible to ship. For every CI-V profile and
every matcher-backed getter registered in ``MATCHER_BACKED_GETTERS`` below,
it captures the ``(command, sub, prefix)`` ``runtime/radio.py: CoreRadio``
actually hands to ``_get_bcd_level``/``_get_bool_value`` when parsing a
reply, and compares it against the shape independently derived from the
same profile's ``CommandMap`` entry via
``commands/_frame.py: decode_wire_tuple`` -- the same decoder
``commands/bound.py: BoundCommands.expect`` and the request-building
``_build_from_map`` both resolve through. The two must always agree.

No transport, connection or event I/O is needed for any radio backend:
``_get_bcd_level``/``_get_bool_value`` are monkeypatched at the class level
to record their keyword arguments and return a dummy value immediately,
short-circuiting before either method would touch a socket.

Written to extend, not to be rewritten: add a ``_GetterSpec`` to
``MATCHER_BACKED_GETTERS`` (§6 population 1) as later modules migrate, and
this file exercises it against every CI-V profile automatically.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from rigplane.commands._frame import decode_wire_tuple
from rigplane.runtime.radio import CoreRadio

from test_profile_command_binding import _civ_rig_configs


@dataclasses.dataclass(frozen=True)
class _GetterSpec:
    """One matcher-backed getter.

    ``method`` is both the ``CoreRadio`` method name and the ``CommandMap``
    key it resolves -- true for every getter registered so far because
    each one's builder exposes a literal key equal to its own name (the
    first case in plan §3.1's four-way classification of how a builder
    exposes its command-map key; none of config.py's getters is the
    fourth case, ``commands/speech.py: get_speech``, whose key is a
    function of the map). ``parser`` is the ``CoreRadio`` method the
    getter parses its reply through -- ``_get_bcd_level`` for a BCD level
    or index, ``_get_bool_value`` for a boolean.
    """

    method: str
    parser: str


# commands/config.py's matcher-backed getters (MOR-2006 Steps 5..N, module
# 1). Extend this tuple module by module as later steps migrate their own
# getters -- everything below runs for every entry, against every CI-V
# profile, with no further change required.
MATCHER_BACKED_GETTERS: tuple[_GetterSpec, ...] = (
    _GetterSpec("get_acc1_mod_level", "_get_bcd_level"),
    _GetterSpec("get_usb_mod_level", "_get_bcd_level"),
    _GetterSpec("get_lan_mod_level", "_get_bcd_level"),
    _GetterSpec("get_data_off_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data1_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data2_mod_input", "_get_bcd_level"),
    _GetterSpec("get_data3_mod_input", "_get_bcd_level"),
    _GetterSpec("get_civ_transceive", "_get_bool_value"),
    _GetterSpec("get_civ_output_ant", "_get_bool_value"),
)


def _cases() -> list[tuple[str, _GetterSpec]]:
    return [
        (model, spec)
        for model in sorted(_civ_rig_configs())
        for spec in MATCHER_BACKED_GETTERS
    ]


def _case_id(case: tuple[str, _GetterSpec]) -> str:
    model, spec = case
    return f"{model}-{spec.method}"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=[_case_id(c) for c in _cases()])
async def test_reply_shape_matches_request_shape(
    case: tuple[str, _GetterSpec], monkeypatch: pytest.MonkeyPatch
) -> None:
    model, spec = case
    config = _civ_rig_configs()[model]
    profile = config.to_profile()
    cmd_map = profile.command_map
    assert cmd_map is not None, f"{model}: to_profile() produced no command_map"
    if not cmd_map.has(spec.method):
        pytest.skip(f"{model} does not declare {spec.method}")

    expected_shape = decode_wire_tuple(cmd_map.get(spec.method))

    captured: dict[str, Any] = {}

    async def _fake_parser(
        self: CoreRadio,
        civ: bytes,
        *,
        key: str,
        command: int,
        sub: int | None,
        prefix: bytes = b"",
        **kwargs: Any,
    ) -> Any:
        captured["shape"] = (command, sub, prefix)
        return 0

    monkeypatch.setattr(CoreRadio, spec.parser, _fake_parser)
    radio = CoreRadio("198.51.100.1", profile=profile)
    await getattr(radio, spec.method)()

    assert "shape" in captured, (
        f"{model}:{spec.method} never reached CoreRadio.{spec.parser}"
    )
    assert captured["shape"] == expected_shape, (
        f"{model}:{spec.method} parsed its reply against {captured['shape']}, "
        f"but the request the same call built goes to {expected_shape} -- "
        "the reply matcher was not migrated with the request."
    )
