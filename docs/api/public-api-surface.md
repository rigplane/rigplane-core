---
robots: noindex, follow
---

# Public API Surface

This page defines the **officially supported** public API of `rigplane`. Use these exports for stable, documented behavior. Other symbols re-exported from `rigplane` are available for advanced or legacy use but may have looser backward-compatibility guarantees.

## Supported exports (recommended)

### Radio connection and control

| Symbol | Description |
|--------|-------------|
| `create_radio` | Factory for backend-neutral radio instances (LAN or serial). **Preferred entry point.** |
| `Radio` | Protocol for radio control; use with `create_radio()` for type-safe, backend-agnostic code. |
| `IcomRadio` | Legacy LAN-specific class; use for direct IC-7610 LAN control or when migrating from older code. |
| `LanBackendConfig`, `SerialBackendConfig`, `BackendConfig` | Backend configuration for `create_radio()`. |
| `RadioState`, `ReceiverState`, `ScopeControlsState` | State types exposed by the radio. |
| `RadioProfile`, `get_radio_profile`, `resolve_radio_profile` | Model/profile resolution. |
| `RADIOS`, `RadioModel`, `get_civ_addr` | Radio model registry and CI-V address lookup. |

### Capability protocols (for type narrowing)

| Symbol | Description |
|--------|-------------|
| `AudioCapable` | Protocol for radios that support audio streaming. |
| `ScopeCapable` | Protocol for radios that support scope/waterfall. |
| `DualReceiverCapable` | Protocol for dual-receiver (MAIN/SUB) support. |
| `LevelsCapable` | Protocol for setting receiver levels: AF, RF gain, squelch. |
| `MetersCapable` | Protocol for read-only meters: S-meter, SWR, TX power. |
| `PowerControlCapable` | Protocol for power on/off and TX power level control. Includes `native_power_unit: Literal["raw_255", "watts"]` so callers can dispatch wire-level scale (Icom CI-V vs Yaesu CAT) without backend-string discriminators. |
| `StateNotifyCapable` | Protocol for state-change and reconnect callbacks (server integration). |

**Note:** The core `Radio` protocol no longer includes meter, level, power, or state-notify methods; those live on the capability protocols above. Use `isinstance(radio, MetersCapable)` (etc.) before calling them.

### Exceptions

| Symbol | Description |
|--------|-------------|
| `RigplaneError` | Base exception. |
| `ConnectionError`, `AuthenticationError`, `CommandError`, `TimeoutError` | Connection and command errors. When catching timeouts, use `rigplane.exceptions.TimeoutError` explicitly; distinguish from `asyncio.TimeoutError` if needed (see [exceptions](exceptions.md)). |
| `AudioError`, `AudioCodecBackendError`, `AudioFormatError`, `AudioTranscodeError` | Audio-related errors. |

### Sync wrapper and utilities

| Symbol | Description |
|--------|-------------|
| `rigplane.sync.IcomRadio` | Synchronous wrapper; use `from rigplane.sync import IcomRadio` for blocking API. |

### Common types

| Symbol | Description |
|--------|-------------|
| `__version__` | Package version string. |
| `PacketType`, `Mode`, `AudioCodec`, `CivFrame`, `PacketHeader` | Types used in API signatures. |
| `HEADER_SIZE`, `bcd_encode`, `bcd_decode`, `get_audio_capabilities` | Low-level helpers used by supported APIs. |

---

## Advanced / implementation detail

The following are re-exported for power users, scripts, or compatibility. Prefer the supported API above when possible.

- **Transport**: `IcomTransport`, `ConnectionState`, `RadioConnectionState` — connection lifecycle and state.
- **Protocol**: `parse_header`, `serialize_header`, `identify_packet_type` — packet parsing.
- **Auth**: `AuthResponse`, `StatusResponse`, `encode_credentials`, `build_login_packet`, `build_conninfo_packet`, `parse_auth_response`, `parse_status_response` — handshake building/parsing.
- **Commands**: Individual CI-V helpers (`get_frequency`, `set_frequency`, `get_mode`, `set_mode`, scope get/set, etc.), `build_civ_frame`, `parse_civ_frame`, `IC_7610_ADDR`, `CONTROLLER_ADDR`, `RECEIVER_MAIN`, `RECEIVER_SUB` — use when you need direct CI-V encoding or custom command flows.
- **Commander**: `IcomCommander`, `Priority` — command queue and priority (used internally by the radio).
- **Audio**: `AudioPacket`, `AudioState`, `AudioStats`, `AudioStream`, `JitterBuffer`, `AUDIO_HEADER_SIZE` — audio pipeline types.
- **Scope**: `ScopeAssembler`, `ScopeFrame` — scope assembly; scope rendering (`SCOPE_THEMES`, `amplitude_to_color`, `render_scope_image`, etc.) when Pillow is available.

When extending the library or writing integration code, prefer importing from the modules that define these symbols (e.g. `rigplane.commands`, `rigplane.transport`) rather than relying on `rigplane` re-exports, so that future narrowing of the top-level `__all__` does not break your code.

---

## Stability tiers

Effective from **v0.19**. Every public symbol in the package belongs to exactly
one tier. The tier governs the breakage policy, the recommended import path,
and whether the symbol is allowed in production code outside its owning
subsystem.

### Tier 1 — Stable

Public API. Breaking changes require a **major version bump**
(semver-strict). Symbols are loaded eagerly by `rigplane/__init__.py` and
available directly via `from rigplane import …`.

**Backend factory and configs**

- `__version__`
- `create_radio`
- `BackendConfig`, `LanBackendConfig`, `SerialBackendConfig`,
  `YaesuCatBackendConfig`

**Capability protocols (from `rigplane.radio_protocol`)**

- `Radio`
- `LevelsCapable`, `MetersCapable`, `PowerControlCapable`,
  `StateNotifyCapable`
- `AudioCapable`, `CivCommandCapable`, `ModeInfoCapable`, `ScopeCapable`
- `DualReceiverCapable`, `ReceiverBankCapable`, `TransceiverBankCapable`,
  `VfoSlotCapable`
- `StateCacheCapable`, `StatePollable`, `StatePoller`,
  `RigctldRoutable`, `RecoverableConnection`
- `DspControlCapable`, `AntennaControlCapable`, `CwControlCapable`,
  `VoiceControlCapable`
- `SystemControlCapable`, `RepeaterControlCapable`, `AdvancedControlCapable`
- `TransceiverStatusCapable`, `RitXitCapable`, `MemoryCapable`
- `SplitCapable` (new in v0.19)
- `UsbAudioCapable` (new in v0.19)

**Exceptions (from `rigplane.exceptions`)**

- `RigplaneError`, `AudioCodecBackendError`, `AudioError`, `AudioFormatError`
- `AudioTranscodeError`, `AuthenticationError`, `CommandError`
- `ConnectionError`, `TimeoutError`

**Public types (from `rigplane.types`)**

- `Mode`, `AudioCodec`, `BreakInMode` (and the other symbols
  currently re-exported from `rigplane.types`)

**Public state types**

- `RadioState`, `RadioProfile`, `VfoSlotState`, `YaesuStateExtension`

**Frontend extension host API (Pro-facing)**

The `frontend/src/lib/local-extensions/` module exposes a versioned host API
that downstream products (notably rigplane-pro) inject UI extensions through.
It is a stable Pro-facing contract — treat it as tier 1 in this document.

From `frontend/src/lib/local-extensions/host-api.ts`:

- `LOCAL_EXTENSION_HOST_API_VERSION` (numeric constant, currently `1`)
- `RadioStateSubscriber` (type)
- `LocalExtensionHostApiV1` (interface)
- `LocalExtensionHostDependencies` (interface)
- `LocalExtensionRegistration` (interface)
- `LocalExtensionHostWindow` (interface)
- `createLocalExtensionHostApi` (function)
- `createDefaultLocalExtensionHostApi` (function)
- `installLocalExtensionHostApi` (function)

From `frontend/src/lib/local-extensions/manifest.ts`:

- `LOCAL_EXTENSION_MANIFEST_URL` (constant)
- `LOCAL_EXTENSION_MANIFEST_VERSION` (numeric constant)
- `LOCAL_EXTENSION_HOST_API_VERSION` (string version, e.g. `"1.0"`,
  mirrors the numeric constant in `host-api.ts`)
- `LocalExtensionMount` (type)
- `LocalExtensionDescriptor` (interface)
- `LocalExtensionManifest` (interface)
- `LoadManifestOptions` (interface)
- `parseLocalExtensionManifest` (function)
- `loadLocalExtensionManifest` (function)

**Breakage policy.** Additive changes only between major versions. Bump
`LOCAL_EXTENSION_HOST_API_VERSION` (numeric, in `host-api.ts`) on any
breaking change to a function signature or interface shape. The string
version in `manifest.ts` mirrors this and is bumped together. Pro relies
on this contract — coordinate breaking changes through the rigplane
issue tracker before merging.

See also `docs/architecture/open-core-policy.md` (when published as part
of #1276) for the policy framing.

Example (valid):

```python
from rigplane import create_radio, Radio, LanBackendConfig, MetersCapable
```

### Tier 2 — Best-effort

Available via `from rigplane import …`, but loaded lazily through PEP 562
`__getattr__` so they do not pull their subsystem into memory until the name
is actually accessed. Breaking changes require a **CHANGELOG note plus a
minor version bump**. No semver guarantee — these may be reshaped or moved
without a major version.

- `IcomRadio`, `IcomCommander`, `Priority`
- Audio primitives: `audio.backend.AudioBackend`,
  `audio.backend.PortAudioBackend`, `audio.backend.FakeAudioBackend`
- DSP utilities: `audio.dsp.NoiseGate`, `audio.dsp.RmsNormalizer`,
  `audio.dsp.Limiter`, `audio.dsp.DspPipeline`
- Audio configuration and devices: `audio.config.AudioConfig`,
  `audio.usb_driver.UsbAudioDriver` (and similar audio-stream primitives)

Example (valid, lazy-loaded):

```python
from rigplane import IcomRadio, IcomCommander  # tier-2 — works, but no semver guarantee
```

### Tier 3 — Internal

Subject to change without notice. **Not** re-exported from the top-level
package. Importing these from production source outside the owning
subsystem triggers ruff `TID251`. Tests are exempt (see existing
`tests/* per-file-ignores` in `pyproject.toml`).

- `rigplane.web.*` — internal to the web subsystem
- `rigplane.rigctld.*` — internal to the rigctld subsystem
- `rigplane.cli` — internal to the CLI
- `rigplane.radio.IcomRadio` — legacy direct-import path (use tier-2
  re-export from `rigplane` instead)
- Most underscore-prefixed modules (`_connection_state`,
  `_shared_state_runtime`, …)

Example (invalid — flagged by `TID251` outside the owning subsystem):

```python
from rigplane.web.handlers import ControlHandler  # tier-3 — forbidden in production
from rigplane.rigctld.server import RigctldServer  # tier-3 — forbidden in production
```

### Migration policy

- **Promote tier 2 → tier 1.** Cite real-world usage in a PR; the tier-1
  list grows by addition and is reviewed in the open. CHANGELOG entry under
  `### Added`.
- **Demote tier 1 → tier 2.** Requires a major version bump. CHANGELOG
  entry under `### Changed`.
- **Remove a tier-1 symbol.** Requires a major version bump **and** a
  two-minor-release deprecation cycle (`DeprecationWarning` for at least
  two minor releases before removal).
- **Add a new tier-1 symbol.** PR + CHANGELOG entry under `### Added`. The
  symbol must be re-exported from `rigplane/__init__.py` and listed in this
  document.
- **Tier 2 / tier 3 changes.** May happen in any minor release with a
  CHANGELOG note. No deprecation cycle is required, but a one-release
  warning is preferred when a tier-2 symbol is being removed entirely.

---

## Summary

- **Use for new code**: `create_radio`, `Radio`, backend configs, capability protocols, exceptions, `RadioState`, profiles, and `sync.IcomRadio` for blocking use.
- **Use when needed**: Individual commands, transport, auth, and audio/scope types for custom pipelines or debugging.
- **Internal**: Modules and symbols whose names start with `_` (e.g. `_connection_state`, `_shared_state_runtime`) are not part of the public API and may change without notice.
