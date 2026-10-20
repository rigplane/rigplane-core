"""Census: every CI-V ingress observation path carries a finite TTL or "never".

MOR-1815 residual: freshness TTLs used to live in a hardcoded code map, so an
observed field with no table entry entered the StateStore with ``max_age=None``
and never decayed to STALE. ``runtime/_civ_rx._observation_max_age`` now
prefers the profile's own ``field_policies``/pollable-class TTL and keeps the
table as a fallback, but a path the profile neither declares nor polls and
missing from the table still stamps ``None``. This test enumerates that
residual set: for every CI-V profile in ``rigs/`` (loaded through the real
``rig_loader.discover_rigs`` loader) and every observation path the CI-V
ingress can produce, ``_observation_max_age`` must return a positive finite
number, unless the profile explicitly declares ``freshness_ttl_seconds =
"never"`` for that path (those fields intentionally never decay). A pollable
path whose acquisition class resolves to a ``None`` TTL is also accepted:
MOR-2615 deliberately stamps no expiry for the SETTING/MENU classes, pinned
by ``test_state_acquisition_policy.py:
test_civ_observation_stamp_is_the_policy_ttl_on_every_pollable_path`` — that
``None`` comes from the polled class policy, not from the missing fallback
this census hunts.

The path universe is built from the decode tables ``_civ_rx`` itself reads
(``_OBSERVABLE_CMD14_FIELDS``, ``_OBSERVABLE_CMD15_FIELDS``,
``_OBSERVABLE_CMD16_FIELDS``, ``_OBSERVABLE_CMD16_VALUE_FIELDS``,
``_OBSERVABLE_CMD1B_FIELDS``, ``_CTL_MEM_OBSERVED_GETTERS``) plus the
``FieldPath`` constructions in ``_civ_rx._observations_from_frame``'s inline
branches, each cited below. ``OBSERVED_PTT_PATH`` is covered through its
``_observation`` remap onto ``global.tx_state.ptt``. Slot-override spellings
(``vfo_slot``) resolve through the same ``(scope, family, name)`` table key
and the same profile lookup shape as the ``active`` spellings covered here.
"""

from __future__ import annotations

import math
from pathlib import Path

from rigplane.core.acquisition_scheduler import provider_uses_civ_acquisition
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.rig_loader import discover_rigs
from rigplane.runtime._civ_rx import (
    _CMD14_CW_PITCH_FIELD,
    _CMD14_KEY_SPEED_FIELD,
    _CMD15_DCD_FIELD,
    _CTL_MEM_OBSERVED_GETTERS,
    _OBSERVABLE_CMD14_FIELDS,
    _OBSERVABLE_CMD15_FIELDS,
    _OBSERVABLE_CMD16_FIELDS,
    _OBSERVABLE_CMD16_VALUE_FIELDS,
    _OBSERVABLE_CMD1B_FIELDS,
    _observation_max_age,
    _profile_path_for_observation,
)

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

_RECEIVERS = ("0", "1")


def _receiver_paths() -> list[FieldPath]:
    """Receiver-scoped ingress paths from the decode tables ``_civ_rx`` reads."""
    specs: list[tuple[str, str, str]] = []
    specs.extend(_OBSERVABLE_CMD14_FIELDS.values())
    specs.append(_CMD15_DCD_FIELD)
    specs.extend(_OBSERVABLE_CMD15_FIELDS.values())
    specs.extend(_OBSERVABLE_CMD16_FIELDS.values())
    specs.extend(spec for spec, _decode in _OBSERVABLE_CMD16_VALUE_FIELDS.values())
    specs.extend(_OBSERVABLE_CMD1B_FIELDS.values())
    paths: list[FieldPath] = []
    for scope, family, name in specs:
        if scope != "receiver":
            continue
        for receiver_id in _RECEIVERS:
            paths.append(FieldPath.receiver(receiver_id, family, name))
    return paths


def _freq_mode_paths() -> list[FieldPath]:
    """``freq_mode`` ingress spellings from ``_civ_rx._observations_from_frame``.

    Covers the ``0x00``/``0x03`` freq, ``0x01``/``0x04`` mode/filter, ``0x25``
    /``0x26`` relative-VFO, ``0x1A 0x03`` filter-width and ``0x1A 0x06``
    data-mode branches (plain ``active`` and relative-VFO ``unselected``).
    """
    names = ("freq_hz", "mode", "filter_num", "filter_width", "data_mode")
    paths: list[FieldPath] = []
    for receiver_id in _RECEIVERS:
        for name in names:
            paths.append(FieldPath.active(receiver_id, "freq_mode", name))
        paths.append(FieldPath.unselected(receiver_id, "freq_mode", "freq_hz"))
        paths.append(FieldPath.unselected(receiver_id, "freq_mode", "mode"))
    return paths


def _global_paths() -> list[FieldPath]:
    """Global ingress paths: decode tables plus inline branch constructions."""
    specs: list[tuple[str, str, str]] = []
    specs.extend(
        spec for spec in _OBSERVABLE_CMD14_FIELDS.values() if spec[0] == "global"
    )
    specs.append(_CMD14_CW_PITCH_FIELD)
    specs.append(_CMD14_KEY_SPEED_FIELD)
    specs.extend(
        spec for spec in _OBSERVABLE_CMD15_FIELDS.values() if spec[0] == "global"
    )
    specs.extend(
        spec for spec in _OBSERVABLE_CMD16_FIELDS.values() if spec[0] == "global"
    )
    specs.extend(
        spec
        for spec, _decode in _OBSERVABLE_CMD16_VALUE_FIELDS.values()
        if spec[0] == "global"
    )
    # Inline ``FieldPath`` constructions in ``_observations_from_frame``:
    # 0x11 attenuator; 0x12 antenna selection and per-connector RX-ANT;
    # 0x1A/0x05 ctl-mem menu levels (getter names resolved through the
    # profile reverse index); 0x16 tone-squelch type derivation; 0x18
    # power; 0x1C PTT / tuner status / TX target; 0x07 active receiver
    # and dual-watch; 0x0F split; 0x10 tuning step; 0x21 RIT leaves.
    specs.extend(
        [
            ("global", "operator_controls", "tx_antenna"),
            ("global", "slow_state", "rx_antenna_1"),
            ("global", "slow_state", "rx_antenna_2"),
            ("global", "tx_state", "power_on"),
            ("global", "tx_state", "ptt"),
            ("global", "operator_controls", "tuner_status"),
            ("global", "tx_state", "tx_target"),
            ("global", "slow_state", "active"),
            ("global", "tx_state", "dual_watch"),
            ("global", "tx_state", "split"),
            ("global", "slow_state", "tuning_step"),
            ("global", "operator_controls", "rit_freq"),
            ("global", "tx_state", "rit_on"),
            ("global", "tx_state", "rit_tx"),
        ]
    )
    paths = [FieldPath.global_(family, name) for _scope, family, name in specs]
    for _getter, (ctl_path, _width) in _CTL_MEM_OBSERVED_GETTERS.items():
        paths.append(ctl_path)
    return paths


def _receiver_inline_paths() -> list[FieldPath]:
    """Receiver-scoped inline constructions: 0x11 att, 0x16 tone-squelch
    type, 0x1A/0x04 AGC time constant, 0x0F repeater shift."""
    names = (
        ("operator_controls", "att"),
        ("operator_controls", "tone_squelch_type"),
        ("operator_controls", "agc_time_constant"),
        ("operator_controls", "repeater_shift"),
    )
    return [
        FieldPath.receiver(receiver_id, family, name)
        for receiver_id in _RECEIVERS
        for family, name in names
    ]


def _scope_control_paths() -> list[FieldPath]:
    """``0x27`` scope leaves from ``_civ_rx._scope_control_observations``."""
    names = (
        "receiver",
        "dual",
        "mode",
        "span",
        "edge",
        "hold",
        "ref_db",
        "speed",
        "during_tx",
        "center_type",
        "vbw_narrow",
        "fixed_edge",
        "rbw",
    )
    return [FieldPath.scope_control("display", name) for name in names]


def _ingress_paths() -> list[FieldPath]:
    seen: dict[str, FieldPath] = {}
    for path in (
        _receiver_paths()
        + _freq_mode_paths()
        + _global_paths()
        + _receiver_inline_paths()
        + _scope_control_paths()
    ):
        seen.setdefault(str(path), path)
    return sorted(seen.values(), key=str)


def _none_reason(acquisition: object, path: FieldPath) -> str | None:
    """Why a ``None`` stamp for ``path`` is deliberate, if it is.

    Returns ``"explicit-never"`` when the profile declares
    ``freshness_ttl_seconds = "never"`` for the path (checked at the profile
    spelling ``_civ_rx._profile_path_for_observation`` resolves — the
    ``0``/``main`` receiver alias the ingress actually consults — as well as
    the stored spelling itself), ``"class-no-expiry"`` when the path is
    pollable and its resolved class policy carries the MOR-2615 deliberate
    ``None`` TTL, else ``None`` (the MOR-1815 residual: no TTL anywhere).
    """
    field_policies = getattr(acquisition, "field_policies", {})
    candidates = {path, _profile_path_for_observation(acquisition, path)}
    if any(
        candidate in field_policies
        and field_policies[candidate].freshness_ttl_seconds is None
        for candidate in candidates
    ):
        return "explicit-never"
    capability_for = getattr(acquisition, "capability_for", None)
    policy_for = getattr(acquisition, "policy_for", None)
    if callable(capability_for) and callable(policy_for):
        for candidate in candidates:
            try:
                if not capability_for(candidate).can_poll:
                    continue
            except AttributeError:
                continue
            if policy_for(candidate).freshness_ttl_seconds is None:
                return "class-no-expiry"
    return None


def test_every_civ_observation_path_has_a_ttl_or_an_explicit_never() -> None:
    """Census over CI-V profiles x ingress paths (MOR-1815 residual)."""
    paths = _ingress_paths()
    assert paths, "the ingress path universe must not be empty"
    checked_models: list[str] = []
    failures: list[str] = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        profile = rig.to_profile()
        acquisition = profile.state_acquisition
        if acquisition is None or not provider_uses_civ_acquisition(
            acquisition.provider
        ):
            continue
        checked_models.append(model)
        for path in paths:
            ttl = _observation_max_age(profile, path)
            if (
                isinstance(ttl, (int, float))
                and not isinstance(ttl, bool)
                and math.isfinite(ttl)
                and ttl > 0
            ):
                continue
            if ttl is None and _none_reason(acquisition, path) is not None:
                continue
            failures.append(f"{model}: {path} stamps {ttl!r}")
    assert checked_models, "no CI-V profile found under rigs/"
    assert not failures, (
        'CI-V ingress paths with no finite TTL and no explicit "never":\n'
        + "\n".join(failures)
    )
