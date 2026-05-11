"""Stable Web API surface for managed supervisors and Pro clients."""

from __future__ import annotations

from typing import Final, TypedDict

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
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/station",
        "purpose": "friendly station-server status",
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/info",
        "purpose": "runtime model and capability summary",
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/state",
        "purpose": "canonical station state snapshot",
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/capabilities",
        "purpose": "profile-backed capability matrix",
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/audio/analysis",
        "purpose": "audio analyzer snapshot when active",
        "auth": "bearer",
    },
    {
        "method": "GET",
        "path": "/api/v1/bridge",
        "purpose": "audio bridge status",
        "auth": "bearer",
    },
    {
        "method": "POST",
        "path": "/api/v1/bridge",
        "purpose": "start audio bridge",
        "auth": "bearer",
    },
    {
        "method": "DELETE",
        "path": "/api/v1/bridge",
        "purpose": "stop audio bridge",
        "auth": "bearer",
    },
)

STABLE_WEBSOCKET_ROUTES: Final[tuple[WebSocketRoute, ...]] = (
    {
        "path": "/api/v1/ws",
        "purpose": "control events and commands",
        "auth": "bearer-or-query-token",
        "availability": "always",
    },
    {
        "path": "/api/v1/scope",
        "purpose": "hardware or audio FFT spectrum stream",
        "auth": "bearer-or-query-token",
        "availability": "when scope or audio FFT is available",
    },
    {
        "path": "/api/v1/audio",
        "purpose": "audio control and media frames",
        "auth": "bearer-or-query-token",
        "availability": "when radio/audio backend supports audio",
    },
    {
        "path": "/api/v1/audio-scope",
        "purpose": "audio FFT spectrum stream",
        "auth": "bearer-or-query-token",
        "availability": "when audio FFT is available",
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
            "lastError",
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
            "filters",
            "audioConfig",
            "webrtc",
        )
    },
}
