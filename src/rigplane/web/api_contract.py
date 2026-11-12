"""Stable Web API surface for managed supervisors and Pro clients."""

from __future__ import annotations

from typing import Final, Literal, NotRequired, TypedDict

WEB_API_CONTRACT_VERSION: Final[int] = 1


class HttpEndpoint(TypedDict):
    method: str
    path: str
    purpose: str
    auth: str


class WebSocketRoute(TypedDict):
    path: str
    purpose: str
    auth: str
    availability: str


class ResponseFieldContract(TypedDict):
    required: tuple[str, ...]
    result_values: NotRequired[tuple[str, ...]]
    # Optional-but-stable fields: present only when the active profile
    # declares them (MOR-3694's scope REF range). Listed so the stable
    # surface documents them without demanding them from every radio.
    optional: NotRequired[tuple[str, ...]]


class CivIngressPayload(TypedDict):
    """``GET /api/v1/runtime`` ``civIngress``: inbound CI-V order counters of
    a LAN radio's CI-V transport (MOR-4205), ``null`` without one.

    ``ordered`` is the transport's ``ordered_ingress``; the counters are its
    ``rx_order_stats`` (``rigplane.core.transport.RxOrderStats``).
    """

    ordered: bool
    reordered: int
    duplicate: int
    lost: int
    late: int
    reanchored: int


class ConnectionIdentityRecord(TypedDict):
    """The wire ``connection.identity`` object of ``GET /api/v1/info`` and
    the public state payload (MOR-3071, pushed on the control WS by
    MOR-4279).

    Serialized from the typed :class:`rigplane.core.radio_protocol.
    RadioIdentity` record by ``WebServer._connection_identity_payload`` —
    the one producer both surfaces share. ``expectedModel`` /
    ``answeredModel`` / ``answeredId`` are ``None`` while unknown.
    """

    status: Literal[
        "checking", "verified", "identity_mismatch", "no_response", "unverified"
    ]
    expectedModel: str | None
    answeredModel: str | None
    answeredId: str | None


class ConnectionSessionRecord(TypedDict):
    """The wire ``connection.session`` object of ``GET /api/v1/info`` and
    the public state payload (MOR-3391 part 2, pushed by MOR-4279).

    Always present: ``busy`` false and ``retryInSeconds`` null outside a
    start-up busy hold. Produced by ``_startup_busy_session_payload`` from
    ``WebServer._startup_busy_hold``.
    """

    busy: bool
    retryInSeconds: float | None


class AudioDeviceCandidateRecord(TypedDict):
    """One offered audio device of an ``audioReceive`` RX-start failure."""

    stableId: str
    displayName: str
    direction: str
    hostApi: str
    portAudioIndex: int
    nearRadio: bool


class AudioReceiveFailureRecord(TypedDict):
    """The latest RX-start failure Core retained (schema version 1)."""

    stage: str
    code: str
    reason: str
    holder: NotRequired[str]
    candidates: NotRequired[list[AudioDeviceCandidateRecord]]


class AudioReceiveRecord(TypedDict):
    """The wire ``audioReceive`` object of ``GET /api/v1/info`` and the
    public state payload (pushed on the control WS by MOR-4279).

    Produced by ``AudioBroadcaster.rx_start_status`` — the one producer
    both surfaces share. ``failure`` is ``None`` while the last RX start
    succeeded (or no attempt was made).
    """

    schemaVersion: int
    failure: AudioReceiveFailureRecord | None


class ManagedTxIntentRecord(TypedDict):
    """The ``managedTransmit.intent`` object of the managed-transmit
    document. ``owner`` is present only for a PTT intent."""

    kind: Literal["rx", "transmit", "ptt"]
    owner: NotRequired[str]


class ManagedTxActuationRecord(TypedDict):
    """The ``managedTransmit.lastActuation`` object."""

    operation: str
    result: str
    attemptId: str


class ManagedTxAbortErrorRecord(TypedDict):
    """One ``managedTransmit.abortErrors`` entry."""

    operation: str
    error: str


class ManagedTxTotRecord(TypedDict):
    """The ``managedTransmit.tot`` object."""

    configuredSeconds: float | None
    active: bool
    remainingMs: int | None
    expiresAt: str | None


class ManagedTransmitBlockRecord(TypedDict):
    """The ``managedTransmit`` block of the managed-transmit document.

    ``status`` is ``"unavailable"`` with a ``reason`` when no authority is
    composed; every other key belongs to the ``"available"`` shape.
    """

    status: Literal["available", "unavailable"]
    reason: NotRequired[Literal["authority_not_composed"]]
    intent: NotRequired[ManagedTxIntentRecord]
    releaseRequired: NotRequired[bool]
    lastError: NotRequired[str | None]
    lastActuation: NotRequired[ManagedTxActuationRecord | None]
    abortErrors: NotRequired[list[ManagedTxAbortErrorRecord]]
    tot: NotRequired[ManagedTxTotRecord]


class TxObservationRecord(TypedDict):
    """The ``txObservation`` block: the canonical observed-PTT evidence,
    independent of the managed authority (``core.tx_observation``)."""

    observedPtt: Literal["on", "off", "unknown"]


class ManagedTransmitDocument(TypedDict):
    """The wire document of ``GET /api/v1/managed-transmit`` and of the
    control-WS ``managed_transmit_changed`` event (MOR-4279).

    Serialized from the one producer ``WebServer._managed_tx_document``
    (``web/managed_tx_view.py: build_managed_tx_view``); the HTTP endpoint
    and the WS event carry the same record. Timestamps are UTC
    millisecond ISO-8601 strings ending in ``Z``.
    """

    schemaVersion: int
    sampledAt: str
    managedTransmit: ManagedTransmitBlockRecord
    txObservation: TxObservationRecord


STABLE_HTTP_ENDPOINTS: Final[tuple[HttpEndpoint, ...]] = (
    {
        "method": "GET",
        "path": "/healthz",
        "purpose": "process liveness",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/readyz",
        "purpose": "station readiness",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/runtime",
        "purpose": "managed runtime status",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/station",
        "purpose": "friendly station-server status",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/info",
        "purpose": "runtime model and capability summary",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/state",
        "purpose": "canonical station state snapshot",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/capabilities",
        "purpose": "profile-backed capability matrix",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/audio/analysis",
        "purpose": "audio analyzer snapshot when active",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/managed-transmit",
        "purpose": "managed transmit authority snapshot",
        "auth": "none",
    },
    {
        "method": "POST",
        "path": "/api/v1/managed-transmit/command",
        "purpose": "submit latched transmit or force-off intent",
        "auth": "none",
    },
    {
        "method": "PUT",
        "path": "/api/v1/managed-transmit/tot",
        "purpose": "update managed transmit time-out",
        "auth": "none",
    },
    {
        "method": "GET",
        "path": "/api/v1/bridge",
        "purpose": "audio bridge status",
        "auth": "none",
    },
    {
        "method": "POST",
        "path": "/api/v1/bridge",
        "purpose": "start audio bridge",
        "auth": "none",
    },
    {
        "method": "PATCH",
        "path": "/api/v1/bridge",
        "purpose": "adjust running audio bridge gains",
        "auth": "none",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/bridge",
        "purpose": "stop audio bridge",
        "auth": "none",
    },
    {
        "method": "POST",
        "path": "/api/v1/commands",
        "purpose": "enqueue one structured radio command",
        "auth": "none",
    },
    {
        "method": "POST",
        "path": "/api/v1/commands/batch",
        "purpose": "apply ordered stateless radio command batch",
        "auth": "none",
    },
)

STABLE_WEBSOCKET_ROUTES: Final[tuple[WebSocketRoute, ...]] = (
    {
        "path": "/api/v1/ws",
        "purpose": "control events and commands",
        "auth": "none",
        "availability": "always",
    },
    {
        "path": "/api/v1/scope",
        "purpose": "hardware or audio FFT spectrum stream",
        "auth": "none",
        "availability": "when scope or audio FFT is available",
    },
    {
        "path": "/api/v1/audio",
        "purpose": "audio control and media frames",
        "auth": "none",
        "availability": "when radio/audio backend supports audio",
    },
)

RESPONSE_FIELD_CONTRACTS: Final[dict[str, ResponseFieldContract]] = {
    "/healthz": {"required": ("status", "pid", "version")},
    "/readyz": {"required": ("status", "radioReady")},
    "/api/v1/runtime": {
        "required": (
            "pid",
            "uptimeSeconds",
            "version",
            "bind",
            "logPath",
            "authRequired",
            "backend",
            "radio",
            "station",
            "rigctld",
            "bridge",
            "process",
            "lastError",
            # MOR-4205: ``CivIngressPayload`` or null.
            "civIngress",
        )
    },
    "/api/v1/station": {
        "required": (
            "schema",
            "service",
            "kind",
            "version",
            "displayName",
            "baseUrl",
            "healthUrl",
            "readinessUrl",
            "runtimeUrl",
            "station",
            "radio",
        )
    },
    "/api/v1/info": {
        "required": (
            "server",
            "version",
            "proto",
            "radio",
            "model",
            "capabilities",
            "connection",
        )
    },
    "/api/v1/state": {"required": ("revision", "updatedAt")},
    "/api/v1/capabilities": {
        "required": (
            "model",
            "capabilities",
            "receivers",
            "modes",
            "modeFamilies",
            "filters",
            "audioConfig",
            "webrtc",
            "cwText",
        ),
        # MOR-3694: the profile's declared `[scope]` REF range, snake_case
        # as in the profile. Present only when the active profile declares
        # it; clients fail closed (disable REF stepping) without it.
        "optional": ("ref_min_db", "ref_max_db", "ref_step_db"),
    },
    "/api/v1/managed-transmit": {
        "required": (
            "schemaVersion",
            "sampledAt",
            "managedTransmit",
            "txObservation",
        )
    },
    "/api/v1/managed-transmit/command": {
        "required": ("ok", "operation", "result"),
        # HTTP 202 pending retains force_off debt until a provider rearms.
        "result_values": ("accepted", "pending", "rejected"),
    },
    "/api/v1/managed-transmit/tot": {
        "required": (
            "schemaVersion",
            "sampledAt",
            "managedTransmit",
            "txObservation",
        )
    },
    "/api/v1/commands": {"required": ("ok", "name", "result")},
    "/api/v1/commands/batch": {"required": ("ok", "results")},
}
