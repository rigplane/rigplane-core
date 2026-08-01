"""The managed-TX ingress gate: which ingress may key a managed radio.

One question, asked identically by every ingress: does this request carry an
identity the supervisor can hold a lease against? Web asks it here, rigctld
(MOR-1014) asks the same helpers, and the CLI/SDK (MOR-1190) will — rather than
grow a third copy of the two-step supervisor read, the duplication MOR-1198
exists to remove, and the one three call sites already got wrong in three
different ways (MOR-1187, MOR-1193, MOR-1196).

It lives in ``runtime`` because ``runtime`` sits below ``backends`` and below
both UI servers, so every one of those callers can reach it while it reaches
nothing but ``core``. Putting it in ``web`` would have made rigctld's copy the
third one.

**The gate is deliberately one-sided: it NEVER refuses an unkey.** Refusing a
key costs an operator one denied transmission, which is recoverable and
visible. Refusing an unkey leaves a transmitter on the air that nobody can take
off — the same asymmetry ``_refuse_key_from_gone_session`` is built on. Nothing
here is consulted from an unkey path, and nothing here should ever be.
"""

from __future__ import annotations

from inspect import getattr_static
from typing import cast

from rigplane.core.radio_protocol import (
    ManagedTxApi,
    ManagedTxCapable,
    ManagedTxSupervisor,
)
from rigplane.core.state_pipeline_contracts import CommandSource
from rigplane.core.tx_safety import TxOwner, TxSource

__all__ = ["bind_managed_tx", "refuse_key_without_owner", "resolve_supervisor"]


def resolve_supervisor(radio: object) -> ManagedTxSupervisor | None:
    """Read ``radio``'s managed TX supervisor; ``None`` when it is unmanaged.

    The owner-free half of :meth:`ManagedTxApi.bind`, for callers asking only
    *whether* a radio is managed — and the canonical copy of that two-step read
    (MOR-1198).

    Both steps carry their own discipline. ``getattr_static`` settles absence
    without running the accessor, so the single explicit read below is the only
    backend code this touches, and **its failures propagate**: a raising
    ``managed_tx`` is a broken accessor, not a positive finding of "unmanaged".
    Collapsing the pair into ``getattr(radio, "managed_tx", None)`` is the bug
    :class:`ManagedTxCapable` documents — the default absorbs an
    ``AttributeError`` raised *inside* the property and hands a managed rig to
    the unsupervised legacy write. ``isinstance`` cannot draw the line either:
    3.11 probes a non-callable protocol member with ``hasattr`` (gh-102433).

    ``None`` at either step — no such member, or a backend publishing one that
    holds ``None`` — is a positive determination that the radio is unmanaged.
    """
    if getattr_static(radio, "managed_tx", None) is None:
        return None  # no such member, or a backend that publishes none
    return cast(ManagedTxCapable, radio).managed_tx


_STABLE_OWNER_SOURCES: dict[CommandSource, TxSource] = {
    "websocket": TxSource.WEBSOCKET,
    "rigctld": TxSource.RIGCTLD,
}


def _stable_owner(source: CommandSource, session_id: str | None) -> TxOwner | None:
    """The ingress identity a lease can be held against, or ``None``.

    Two ingresses qualify, and for one reason: the id names a *connection* that
    something is obliged to tear down, so a lease taken under it is releasable.

    A websocket control session carries its long-lived
    ``ControlHandler._session_id``, not the throwaway the shared executor mints
    per request, which owner-matched ``release_owner`` would miss — stranding
    the rig keyed. A rigctld connection carries the ``rigctld-client-N`` id its
    TCP server mints once per accepted socket and releases on that socket's
    teardown, however the socket ends (MOR-1014).

    Every other ingress has no teardown hook — HTTP params may even carry a
    ``session_id``, and nothing would ever hand its lease back — so its lease
    would be unreleasable and it gets no owner here.
    """
    tx_source = _STABLE_OWNER_SOURCES.get(source)
    if tx_source is None or not session_id:
        return None
    return TxOwner(tx_source, session_id)


def bind_managed_tx(
    radio: object, source: CommandSource, session_id: str | None
) -> ManagedTxApi | None:
    """Bind this ingress's managed TX facade; ``None`` if it cannot hold one.

    ``None`` covers two different findings that the caller must not conflate:
    an ingress with no stable owner (checked first, so the common path runs no
    backend code at all), and an unmanaged radio. On a managed rig the first is
    exactly the case :func:`refuse_key_without_owner` answers, because falling
    through to the raw ``set_ptt`` write there would key a managed rig with no
    lease, no owner and no watchdog.
    """
    owner = _stable_owner(source, session_id)
    if owner is None:
        return None
    return ManagedTxApi.bind(radio, owner)


def refuse_key_without_owner(
    radio: object, source: CommandSource, session_id: str | None
) -> bool:
    """Must this ingress be refused the KEY? Never asked about an unkey.

    ``True`` only where both halves hold: the radio publishes a supervisor, and
    the request carries no identity that supervisor could release later. Either
    half alone is not a refusal — an owned ingress keys through the supervisor,
    and an unmanaged rig keeps the legacy write every shipped backend still
    uses.

    The owner test comes first so a managed websocket key never pays for the
    supervisor read, and a broken accessor cannot turn a perfectly keyable
    session into an error. When the read does happen it happens under
    :func:`resolve_supervisor`'s discipline: a failure propagates rather than
    reading as "unmanaged", so a broken backend denies the key instead of
    silently granting an unsupervised one.

    Callers must ask this only on the key path. An unkey refused for lacking an
    owner strands a keyed transmitter, which is strictly worse than the
    unsupervised de-key it would be preventing.
    """
    if _stable_owner(source, session_id) is not None:
        return False
    return resolve_supervisor(radio) is not None
