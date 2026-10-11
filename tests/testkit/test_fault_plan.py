"""Unit tests for the FaultPlan decision layer (no sockets, no event loop).

Every test feeds packets through ``FaultPlan.decide`` directly and asserts
on the returned ``(delay_s, payload)`` emission lists. Randomness is seeded,
so all expectations here are exact and reproducible.
"""

from rigplane_testkit.icom.faults import FaultPlan, FaultRule

P = b"\x01\x02\x03\x04\x05\x06\x07\x08"  # a reusable 8-byte packet


def _plan(**rule_kwargs: object) -> FaultPlan:
    """A plan with one civ/to_client rule and the other streams untouched."""
    return FaultPlan(civ_to_client=FaultRule(**rule_kwargs))  # type: ignore[arg-type]


def _decide_all(plan: FaultPlan, packets: list[bytes], now: float = 0.0):
    return [plan.decide("civ", "to_client", p, now=now) for p in packets]


def test_default_plan_passes_through_untouched() -> None:
    plan = FaultPlan()
    assert plan.is_noop("control", "to_client")
    assert plan.is_noop("control", "from_client")
    assert plan.is_noop("civ", "to_client")
    assert plan.is_noop("civ", "from_client")
    for port in ("control", "civ"):
        for direction in ("to_client", "from_client"):
            assert plan.decide(port, direction, P) == [(0.0, P)]  # type: ignore[arg-type]


def test_rule_routing_per_port_and_direction() -> None:
    plan = FaultPlan()
    assert plan.rule("control", "to_client") is plan.control_to_client
    assert plan.rule("control", "from_client") is plan.control_from_client
    assert plan.rule("civ", "to_client") is plan.civ_to_client
    assert plan.rule("civ", "from_client") is plan.civ_from_client


def test_is_noop_only_for_configured_stream() -> None:
    plan = _plan(drop_probability=0.5)
    assert not plan.is_noop("civ", "to_client")
    assert plan.is_noop("civ", "from_client")
    assert plan.is_noop("control", "to_client")


def test_drop_probability_one_drops_everything() -> None:
    plan = _plan(drop_probability=1.0)
    results = _decide_all(plan, [P, P, P])
    assert results == [[], [], []]
    assert plan.civ_to_client.dropped == 3
    assert plan.civ_to_client.matched == 3


def test_drop_every_nth() -> None:
    plan = _plan(drop_every=3)
    results = _decide_all(plan, [P] * 7)
    assert [r == [] for r in results] == [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
    ]


def test_seeded_decisions_are_deterministic() -> None:
    kwargs = dict(
        drop_probability=0.3,
        duplicate_probability=0.4,
        delay_s=0.01,
        jitter_s=0.005,
        corrupt_probability=0.5,
        corrupt_bytes=2,
        truncate_probability=0.2,
        truncate_to=4,
    )
    packets = [bytes([i]) * 16 for i in range(200)]
    first = _decide_all(_plan(**kwargs), packets)
    second = _decide_all(_plan(**kwargs), packets)
    assert first == second
    # And the mixture actually produced faults.
    assert any(r == [] for r in first)
    assert any(r is not None and len(r) == 2 for r in first)


def test_different_seeds_diverge() -> None:
    packets = [P] * 8
    one = _decide_all(
        FaultPlan(civ_to_client=FaultRule(drop_probability=0.5), seed=1), packets
    )
    two = _decide_all(
        FaultPlan(civ_to_client=FaultRule(drop_probability=0.5), seed=2), packets
    )
    assert one != two


def test_duplicate_sends_two_copies_with_delay() -> None:
    plan = _plan(duplicate_probability=1.0, duplicate_delay_s=0.05)
    (result,) = _decide_all(plan, [P])
    assert result == [(0.0, P), (0.05, P)]


def test_delay_fixed_and_jitter_bounded() -> None:
    plan = _plan(delay_s=0.1)
    (result,) = _decide_all(plan, [P])
    assert result == [(0.1, P)]

    jittered = _plan(delay_s=0.1, jitter_s=0.2)
    for emissions in _decide_all(jittered, [P] * 20):
        assert emissions is not None
        (delay, _payload) = emissions[0]
        assert 0.1 <= delay <= 0.3


def test_corrupt_flips_exactly_n_distinct_bytes() -> None:
    plan = _plan(corrupt_probability=1.0, corrupt_bytes=3)
    packet = bytes(range(16))
    (result,) = _decide_all(plan, [packet])
    (delay, payload) = result[0]
    assert delay == 0.0
    assert len(payload) == len(packet)
    differing = [i for i, (a, b) in enumerate(zip(packet, payload)) if a != b]
    assert len(differing) == 3
    for i in differing:
        assert payload[i] == packet[i] ^ 0xFF


def test_truncate_cuts_to_k_bytes() -> None:
    plan = _plan(truncate_probability=1.0, truncate_to=5)
    (result,) = _decide_all(plan, [P])
    assert result == [(0.0, P[:5])]


def test_reorder_holds_then_flushes_shuffled() -> None:
    plan = _plan(reorder_window=3)  # default seed 0xC0FFEE
    packets = [b"a", b"b", b"c"]
    results = _decide_all(plan, packets)
    assert results[0] is None  # held
    assert results[1] is None  # held
    batch = results[2]
    assert batch is not None
    assert sorted(p for _d, p in batch) == sorted(packets)  # nothing lost
    assert [p for _d, p in batch] != packets  # and actually permuted


def test_max_packets_bound_releases_stream() -> None:
    plan = _plan(drop_probability=1.0, max_packets=2)
    results = _decide_all(plan, [P, P, P])
    assert results[0] == []
    assert results[1] == []
    assert results[2] == [(0.0, P)]  # bound exhausted: passthrough


def test_duration_bound_releases_stream() -> None:
    plan = _plan(drop_probability=1.0, duration_s=1.0)
    assert plan.decide("civ", "to_client", P, now=100.0) == []  # window starts
    assert plan.decide("civ", "to_client", P, now=100.9) == []
    assert plan.decide("civ", "to_client", P, now=101.5) == [(0.0, P)]
