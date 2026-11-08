"""Trusted physical-read evidence for external rigctld liveness."""

import time
from dataclasses import replace

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.exceptions import CommandError


@pytest.mark.realtime(
    reason="physical-read evidence uses host monotonic clock and real TCP"
)
async def test_physical_read_and_identical_cached_reply_have_different_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.backends.rigctld_client import RigctldFreshRead

    physical_reads = []

    def provenance(command, value, generation):
        physical_value, stamp = physical_reads[-1]
        return RigctldFreshRead(command, physical_value, generation, stamp)

    async with FakeRigctldServer() as server:
        response_for = server._response_for

        def physical_response(line, key):
            if key == "f":
                physical_reads.append((server.state.frequency_hz, time.monotonic()))
            return response_for(line, key)

        monkeypatch.setattr(server, "_response_for", physical_response)
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            stamp = radio.last_valid_frame_monotonic
            assert stamp is not None
            server.behavior.malformed_responses["f"] = b"14074000\n"
            assert await radio.get_freq() == 14_074_000
            assert len(physical_reads) == 1
            assert radio.last_valid_frame_monotonic == stamp
            await radio.set_freq(14_074_000)  # ACK cannot refresh evidence.
            assert radio.last_valid_frame_monotonic == stamp
        finally:
            await radio.disconnect()
        assert radio.last_valid_frame_monotonic is None


@pytest.mark.parametrize(
    "mismatch", ["value", "type", "generation", "command", "nan", "future", "past"]
)
@pytest.mark.realtime(
    reason="physical-read evidence uses host monotonic clock and real TCP"
)
async def test_provenance_must_match_decoded_read_and_live_connection(mismatch) -> None:
    from rigplane.backends.rigctld_client import RigctldFreshRead

    def provenance(command, value, generation):
        proof = RigctldFreshRead(command, value, generation, time.monotonic())
        changes = {
            "value": {"value": value + 1},
            "type": {"value": float(value)},
            "generation": {"connection_generation": object()},
            "command": {"command": "t"},
            "nan": {"timestamp_monotonic": float("nan")},
            "future": {"timestamp_monotonic": time.monotonic() + 10},
            "past": {"timestamp_monotonic": 1.0},
        }
        return replace(proof, **changes[mismatch])

    async with FakeRigctldServer() as server:
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            assert radio.last_valid_frame_monotonic is None
        finally:
            await transport.close()


@pytest.mark.parametrize("payload", [b"invalid\n", b"-1\n", b"RPRT -8\n", b"RPRT 0\n"])
async def test_malformed_error_and_ack_do_not_request_provenance(payload) -> None:
    calls = []

    def provenance(*args):
        calls.append(args)
        raise AssertionError("invalid data must not request physical-read evidence")

    async with FakeRigctldServer() as server:
        server.behavior.malformed_responses["f"] = payload
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        try:
            with pytest.raises(CommandError):
                await radio.get_freq()
            assert calls == []
            assert radio.last_valid_frame_monotonic is None
        finally:
            await transport.close()


@pytest.mark.realtime(reason="reconnect proof uses host monotonic clock and real TCP")
async def test_reconnect_rejects_prior_connection_proof_with_new_timestamp() -> None:
    from rigplane.backends.rigctld_client import RigctldFreshRead

    generations = []

    def provenance(command, value, generation):
        generations.append(generation)
        return RigctldFreshRead(command, value, generations[0], time.monotonic())

    async with FakeRigctldServer() as server:
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        try:
            await radio.get_freq()
            assert radio.last_valid_frame_monotonic is not None
            await transport.close()
            await transport.connect()
            await radio.get_freq()
            assert generations[0] is not generations[1]
            assert radio.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_provenance_failure_keeps_ordinary_read_usable() -> None:
    def provenance(*args):
        raise RuntimeError("provider evidence unavailable")

    async with FakeRigctldServer() as server:
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            assert radio.last_valid_frame_monotonic is None
        finally:
            await transport.close()


@pytest.mark.realtime(reason="queued read proof uses host monotonic clock and real TCP")
async def test_queued_read_uses_connection_at_exchange_admission(monkeypatch) -> None:
    import asyncio

    from rigplane.backends.rigctld_client import RigctldFreshRead

    def provenance(command, value, generation):
        return RigctldFreshRead(command, value, generation, time.monotonic())

    async with FakeRigctldServer() as server:
        transport = RigctldTransport(
            host=server.host, port=server.port, fresh_read_provenance=provenance
        )
        radio = RigctldClientRadio(host=server.host, transport=transport)
        await transport.connect()
        queued = asyncio.Event()
        query = transport.query

        async def signal_query(*args, **kwargs):
            queued.set()
            return await query(*args, **kwargs)

        monkeypatch.setattr(transport, "query", signal_query)
        task = None
        try:
            async with transport._exchange():
                task = asyncio.create_task(radio.get_freq())
                await queued.wait()
                await transport.close()
                await transport.connect()
            assert await task == 14_074_000
            assert radio.last_valid_frame_monotonic is not None
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await transport.close()
