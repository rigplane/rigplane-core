"""Frozen v2.11.1 RadioProfile property consumers (MOR-2336 PY1/PY2)."""

from dataclasses import replace

import pytest

from rigplane.profiles import RadioProfile, resolve_radio_profile


@pytest.mark.parametrize("operation", ["swap", "equal"])
@pytest.mark.parametrize("receiver_count,vfo_scheme", [(1, "ab"), (2, "main_sub")])
@pytest.mark.parametrize(
    "main_sub,ab,expected",
    [
        (0xB0, None, 0xB0),
        (None, 0xB1, 0xB1),
        (0xB0, 0xB1, 0xB0),
        (None, None, None),
        (0, 0xB1, 0xB1),
        (0, None, None),
        (None, 0, 0),
        (0, 0, 0),
    ],
)
def test_released_property_selection(
    operation: str,
    receiver_count: int,
    vfo_scheme: str,
    main_sub: int | None,
    ab: int | None,
    expected: int | None,
) -> None:
    profile = RadioProfile(
        id="compatibility-consumer",
        model="Compatibility consumer",
        civ_addr=0x94,
        receiver_count=receiver_count,
        capabilities=frozenset(),
        cmd29_routes=frozenset(),
        vfo_scheme=vfo_scheme,
        swap_main_sub_code=main_sub,
        swap_ab_code=ab,
        equal_main_sub_code=ab,
        equal_ab_code=main_sub,
    )

    if operation == "swap":
        assert profile.vfo_swap_code == expected
        return
    equal_profile = replace(
        profile,
        equal_main_sub_code=main_sub,
        equal_ab_code=ab,
        swap_main_sub_code=ab,
        swap_ab_code=main_sub,
    )
    assert equal_profile.vfo_equal_code == expected


@pytest.mark.parametrize(
    "model,swap,equal",
    [("IC-7300", 0xB0, 0xA0), ("IC-7610", 0xB0, 0xB1), ("FTX-1", None, None)],
)
def test_released_consumer_on_current_shipped_profile(
    model: str, swap: int | None, equal: int | None
) -> None:
    profile = resolve_radio_profile(model=model)

    assert profile.vfo_swap_code == swap
    assert profile.vfo_equal_code == equal
    updated = replace(
        profile,
        swap_main_sub_code=0x12,
        equal_main_sub_code=0x34,
    )
    assert updated.vfo_swap_code == 0x12
    assert updated.vfo_equal_code == 0x34
    assert profile.vfo_swap_code == swap
    assert profile.vfo_equal_code == equal
