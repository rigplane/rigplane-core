from dataclasses import FrozenInstanceError, fields

import pytest

from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedView,
    NativeFrame,
    ScopeWireFrame,
    SourceCapabilities,
    Subscription,
    View,
)


def test_frozen_views_and_native_contract():
    view = View(100, 200)
    assert GrantedView(view, Coverage(110, 190)).view == view
    assert NativeFrame(None, Coverage(100, 200), b"\x01").epoch == 0
    assert FollowView("vfo", 100).span_hz == 100
    with pytest.raises(FrozenInstanceError):
        view.start_hz = 3
    assert [f.name for f in fields(ScopeWireFrame)] == [
        "source",
        "source_id",
        "receiver",
        "view_start_hz",
        "view_end_hz",
        "coverage_start_hz",
        "coverage_end_hz",
        "width",
        "pixels",
        "out_of_range",
        "epoch",
        "seq",
        "sent_at_ms",
    ]


@pytest.mark.parametrize(
    "start,end",
    [(True, 3), (0, float("nan")), (0, float("inf")), (2, 2), (3, 2), ("1", 2)],
)
def test_invalid_view(start, end):
    with pytest.raises(ValueError):
        View(start, end)


@pytest.mark.parametrize(
    "changes",
    [
        dict(width=0),
        dict(width=True),
        dict(max_fps=-1),
        dict(max_fps=float("nan")),
        dict(receiver=True),
        dict(source_id=""),
        dict(source_id="é"),
        dict(view={}),
    ],
)
def test_invalid_subscription(changes):
    values = dict(
        source_id="radio:main", receiver=0, view=View(100, 200), width=32, max_fps=10
    )
    with pytest.raises(ValueError):
        Subscription(**(values | changes))


def test_capability_contract():
    caps = SourceCapabilities(
        (0, 1), ("fixed", "center", "free"), (100, 200), 512, 30, "native"
    )
    assert caps.native_width == 512
    with pytest.raises(ValueError):
        SourceCapabilities((0,), ("fixed",), (), 0, 30, "native")


def test_capabilities_do_not_offer_unused_edge_limits():
    assert "edge_limits_hz" not in {f.name for f in fields(SourceCapabilities)}


def test_native_widths_resolve_only_matching_receiver_with_scalar_fallback():
    caps = SourceCapabilities(
        (0, 1), ("center",), (), None, 30, "observed", ((0, 475),)
    )
    assert caps.native_width_for(0) == 475
    assert caps.native_width_for(1) is None
    declared = SourceCapabilities(
        (0, 1), ("center",), (), 689, 30, "declared", ((0, 475),)
    )
    assert declared.native_width_for(0) == 475 and declared.native_width_for(1) == 689
    with pytest.raises(ValueError, match="receiver native width"):
        SourceCapabilities(
            (0, 1), ("center",), (), None, 30, "observed", ((0, 475), (0, 689))
        )
