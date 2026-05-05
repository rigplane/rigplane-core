# `audio` layer

## Charter

End-to-end audio subsystem: `AudioBackend` Protocol + PortAudio/Fake
implementations, codecs (PCM / Opus / μ-law), transcoder, audio bridge,
audio bus, FFT scope (panadapter), reconnect-time recovery, and platform
USB audio resolution. The `audio.backend` and `audio.dsp` submodule paths
are part of the rigplane-pro contract and **must remain stable**.

## Public API

`audio/__init__.py` is split between an eager block (LAN audio wire
types used by transport/radio/sync at import time) and a PEP 562
`__getattr__` that lazy-loads the heavier abstractions:

- **Eager (LAN audio)**: `AudioPacket`, `AudioStream`, `AudioState`,
  `AudioStats`, `JitterBuffer`, `build_audio_packet`,
  `parse_audio_packet`, `AUDIO_HEADER_SIZE`, `MAX_AUDIO_PAYLOAD`,
  `RX_IDENT_0xA0`, `TX_IDENT`.
- **Lazy — backend**: `AudioBackend`, `AudioDeviceId`,
  `AudioDeviceInfo`, `RxStream`, `TxStream`, `PortAudioBackend`,
  `FakeAudioBackend`, `FakeRxStream`, `FakeTxStream`.
- **Lazy — config**: `AudioConfig`, `load_audio_config`,
  `save_audio_config`.
- **Lazy — DSP**: `DspPipeline`, `DspStage`, `Limiter`, `NoiseGate`,
  `RmsNormalizer`.
- **Lazy — resample**: `PcmResampler`, `SampleRateNegotiation`,
  `negotiate_sample_rate`.
- **Lazy — USB**: `UsbAudioDriver`, `UsbAudioDevice`,
  `list_usb_audio_devices`, `select_usb_audio_devices`,
  plus driver-lifecycle/selection exceptions.

The `AudioBackend` Protocol is Tier 2 (best-effort; lazily exposed via
PEP 562) — see #1275.

## Allowed dependencies

`core`, `scope` (plan §3 matrix row `audio`). The `audio → scope` edge
is single-direction, used by `audio/fft_scope.py` to assemble panadapter
frames from PCM. No `runtime`, no `commands`, no transport caller.

## Forbidden patterns

- `from rigplane.runtime` / `from rigplane.commands` — audio is a leaf
  that runtime composes; the inverse direction is forbidden.
- Eager imports of `numpy`, `sounddevice`, `opuslib`, or `pyaudio` at
  module top level. Use `core._optional_deps._require_*()` and gate at
  call time. The eager block in `audio/__init__.py` is intentionally
  limited to wire-protocol types.
- One-off mocks in audio tests. Use `FakeAudioBackend` only (per
  CLAUDE.md project rule).

## Common operations

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

- `docs/plans/2026-04-29-modularization-plan.md` §1.2 ("audio.backend
  and audio.dsp paths are the rigplane-pro contract"), §2.2, §3.
- `docs/api/public-api-surface.md` — Tier 2 surface for AudioBackend.
- `audio/backend.py` — the stable Protocol definition.
- `tests/test_audio_*.py` — coverage; `FakeAudioBackend` lives in
  `audio/backend.py`.
