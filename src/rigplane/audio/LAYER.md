# `audio` layer

## Charter

End-to-end audio subsystem, organised around a per-radio `AudioSession`
(epic MOR-562; full design in
`docs/plans/2026-06-09-target-audio-architecture.md`): session state
machine, audio bus, `AudioBackend` Protocol + PortAudio/Fake
implementations, codecs (PCM / Opus / μ-law), transcoder, audio bridge,
FFT scope (panadapter), and platform USB audio resolution. `audio.route`
resolves the radio audio route used by WSJT-X DATA policy; it must not
treat the local loopback bridge as proof of LAN audio. The
`audio.backend` and `audio.dsp` submodule paths are part of the
rigplane-pro contract and **must remain stable**.

## Components and ownership

- **`audio/session.py` — `AudioSession`** (one per radio, lazily owned
  by the radio as `radio.audio_session`, `runtime/radio.py`). The sole
  arbiter of the radio audio lifecycle: a demand-driven
  IDLE ⇄ RX_ONLY ⇄ RX_TX state machine (plus RECOVERING/FAILED)
  reconciled under one asyncio lock (`_apply`). Consumers declare
  demand — `subscribe_rx(name)` / `acquire_tx(owner)` → refcounted
  `TxLease` — and never call radio
  `start_rx`/`stop_rx`/`start_tx`/`stop_tx` themselves. Arming order is
  transport-owned via the additive `audio_setup_order` descriptor
  (`"rx_first"` / `"tx_first"` / `"atomic"`, read with `getattr`,
  defaulting to `"rx_first"`); teardown always drops TX before RX. A
  ~1 s health watchdog reads the bus heartbeat; ~3 s of RX silence ⇒
  RECOVERING + a local `AudioSessionEvent` (listeners only — no
  telemetry). `reestablish()` re-arms the session's *live* demand after
  a transport reconnect (called by `runtime/_audio_recovery.py`).
- **`audio/bus.py` — `AudioBus`**: RX fan-out to bounded
  per-subscriber queues. The first-subscriber/last-unsubscribe refcount
  IS the session's RX demand. Exposes the `last_rx_frame_monotonic`
  heartbeat and hosts the `rx.pcm` tap stage (`STAGE_RX_PCM`, a
  `dsp.TapRegistry`). RX-start failures raise to the demanding
  subscriber — never swallowed — with the failed subscriber rolled
  back.
- **`audio/bridge.py` — `AudioBridge`**: pure session consumer +
  device-side loopback pump (BlackHole / PipeWire null-sink / VB-Cable
  → WSJT-X/fldigi). Declares RX demand and a TX lease on
  `radio.audio_session`; owns no radio-side ordering (the old
  `rx_first` branch is gone) and no radio-side recovery — only
  loopback-device reconnect. The bridge output is the lossless
  digital-decode path (ADR tenet T1a): no lossy codec may ever sit on
  it (conformance-pinned in
  `tests/contracts/test_adaptive_egress_conformance.py`).
- **`audio/fft_scope.py` — `AudioFftScope`**: panadapter frames from
  PCM, fed from the web broadcaster's `rx.post_dsp` tap stage
  (`STAGE_RX_POST_DSP`).
- **Device/wire tier — `audio/backend.py`, `audio/usb_driver.py`,
  `audio/lan_stream.py`**: `AudioBackend` Protocol +
  PortAudio/Fake implementations (`FakeAudioBackend` grows
  `strict_device_exclusive` for order-sensitive tests), the frozen
  `AudioDeviceConfig` carrier (one object threaded
  loader→backend→driver→framer), `UsbAudioDriver` (device selection +
  duplex policy), LAN UDP framing + jitter buffer. Typed lifecycle
  errors (`AudioAlreadyStartedError`, `AudioNotStartedError`) live in
  `usb_driver.py`.

Edge note: per-client egress encoding, `audio_format` negotiation, and
the flag-gated adaptive PCM16↔Opus controller live at the web edge
(`web/handlers/audio.py`), downstream of this layer. The spine through
bus/taps/bridge stays uncompressed — lossy codecs exist only at the
client edge, and never on the bridge/digital path.

## Public API

`audio/__init__.py` is split between an eager block (LAN audio wire
types used by transport/radio/sync at import time) and a PEP 562
`__getattr__` that lazy-loads the heavier abstractions:

- **Eager (LAN audio)**: `AudioPacket`, `AudioStream`, `AudioState`,
  `AudioStats`, `JitterBuffer`, `build_audio_packet`,
  `parse_audio_packet`, `AUDIO_HEADER_SIZE`, `MAX_AUDIO_PAYLOAD`,
  `RX_IDENT_0xA0`, `TX_IDENT`.
- **Lazy — backend**: `AudioBackend`, `AudioDeviceConfig`,
  `AudioDeviceId`, `AudioDeviceInfo`, `RxStream`, `TxStream`,
  `PortAudioBackend`, `FakeAudioBackend`, `FakeRxStream`,
  `FakeTxStream`.
- **Lazy — config**: `AudioConfig`, `load_audio_config`,
  `save_audio_config`.
- **Lazy — DSP**: `DspPipeline`, `DspStage`, `Limiter`, `NoiseGate`,
  `RmsNormalizer`.
- **Lazy — resample**: `PcmResampler`, `SampleRateNegotiation`,
  `negotiate_sample_rate`.
- **Lazy — USB**: `UsbAudioDriver`, `UsbAudioDevice`,
  `list_usb_audio_devices`, `select_usb_audio_devices`,
  plus driver-lifecycle/selection exceptions.
- **Direct submodule — `audio.session`**: `AudioSession`,
  `AudioSessionState`, `AudioSessionEvent`, `RxSubscription`,
  `TxLease` (not re-exported from `audio/__init__.py`; reach the
  session via `radio.audio_session`).
- **Direct submodule — `audio.bus`**: `AudioBus`, `AudioSubscription`,
  `STAGE_RX_PCM`, `STAGE_RX_POST_DSP` (the named tap-stage scheme).
- **Direct submodule — `audio.route`**: the route resolver and
  route-derived rigctld WSJT-X DATA policy.

The `AudioBackend` Protocol is Tier 2 (best-effort; lazily exposed via
PEP 562) — see #1275.

## Allowed dependencies

`core`, `scope`, `dsp` (plan §3 matrix row `audio`). Both sibling edges
are single-direction: `audio → scope` is used by `audio/fft_scope.py`
to assemble panadapter frames from PCM; `audio → dsp` is used by
`audio/bus.py` for the `TapRegistry` tap stages. No `runtime`, no
`commands`, no transport caller. Anything in this layer that needs a
radio reference takes it as a duck-typed `AudioTransport`-shaped object
(`core.radio_protocol.AudioTransport`) — exactly as `AudioSession` and
`AudioBus` do — so the import matrix is untouched.

## Forbidden patterns

- `from rigplane.runtime` / `from rigplane.commands` — audio is a leaf
  that runtime composes; the inverse direction is forbidden.
- Calling radio `start_rx`/`stop_rx`/`start_tx`/`stop_tx` from a
  consumer. Declare demand on `radio.audio_session` instead; the
  session owns the ordering and the recovery. (The poller PTT path is
  the one legacy exception until MOR-554 lands.)
- Lossy codecs on the bridge/digital path (tenet T1a) or anywhere on
  the spine. Egress encoding belongs to the web edge only.
- Single-slot callbacks on shared paths. Use the named tap stages
  (`TapRegistry`) — see MOR-241/MOR-506 history.
- Eager imports of `numpy`, `sounddevice`, `opuslib`, or `pyaudio` at
  module top level. Use `core._optional_deps._require_*()` and gate at
  call time. The eager block in `audio/__init__.py` is intentionally
  limited to wire-protocol types.
- One-off mocks in audio tests. Use `FakeAudioBackend` only (per
  CLAUDE.md project rule); order-sensitive lifecycle tests use
  `strict_device_exclusive=True` and the shared stateful radio stubs.

## Common operations

- **Add a new audio consumer** → `await radio.audio_session.subscribe_rx(name)`
  for RX frames, `await radio.audio_session.acquire_tx(owner)` for a TX
  lease; release both. Passive observation → register on a named tap
  stage instead of subscribing.
- **Add a new radio audio transport** → implement the neutral
  `AudioTransport` surface (`core.radio_protocol`) + the additive
  `audio_setup_order` descriptor; the session, bus, bridge, scope, and
  web egress are transport-blind. Extend
  `tests/contracts/test_audio_lifecycle_conformance.py` to cover it.
- **Add a new codec** → add to `audio/_codecs.py` plus a Python ID
  constant in `core.types.AudioCodec`; verify wire-format roundtrip in
  `tests/test_audio_codecs*.py`.
- **Add an `AudioBackend` implementation** → conform to the Protocol
  in `audio/backend.py`; add a probe under `tests/test_audio_backend*.py`
  using the existing fixture set (no one-off mocks).
- **Change the LAN audio packet layout** → wire format in
  `audio/lan_stream.py`; update `AUDIO_HEADER_SIZE` and the parser/
  builder; cross-check with wfview reference and the PCM/Opus codec ID
  matrix (`docs/api/public-api-surface.md`).
- **Touch `audio/usb_driver.py`** → keep platform paths gated behind
  `_require_*()`; macOS uses `audio/_macos_uid.py` for IORegistry
  matching.

## See also

- `docs/plans/2026-06-09-target-audio-architecture.md` — the universal
  audio path ADR (design, tenets T1–T6, migration status as-built).
- `docs/plans/2026-04-29-modularization-plan.md` §1.2 ("audio.backend
  and audio.dsp paths are the rigplane-pro contract"), §2.2, §3.
- `docs/api/public-api-surface.md` — Tier 2 surface for AudioBackend.
- `audio/backend.py` — the stable Protocol definition.
- `tests/contracts/test_audio_lifecycle_conformance.py` +
  `tests/contracts/test_audio_transport_conformance.py` +
  `tests/contracts/test_adaptive_egress_conformance.py` — the lifecycle,
  protocol-surface, and T1a pins.
- `tests/test_audio_*.py` — coverage; `FakeAudioBackend` lives in
  `audio/backend.py`.
