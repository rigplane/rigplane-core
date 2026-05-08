# USB Audio Negotiation Design

Issue: #1480
Date: 2026-05-08

## Scope

USB-connected radios do not negotiate radio-native audio codecs or bitrates.
The radio control session is serial/CAT/CI-V, while audio is an OS audio device
opened through the configured audio backend.

This design hardens the OS audio-device layer without mixing it with LAN
radio-native codec probing.

## Effective Contract

`UsbAudioDriver` now records an effective USB audio contract after RX or TX
streams are opened:

- selected RX/TX device;
- sample rate;
- channels;
- frame duration;
- sample-rate source: `default`, `explicit`, or `fallback`;
- fallback reason when a default rate is unsupported.

The contract is exposed as `usb_audio_contract` and serialized through
diagnostics as `audio/usb_audio`.

## Negotiation Rules

- Automatic defaults may fall back from the requested rate through the ordered
  candidate list: 48000, 24000, 16000, 8000.
- Explicit calls can disable fallback with `allow_sample_rate_fallback=False`.
  In that mode unsupported sample rates fail clearly before opening the stream.
- Device selection remains the existing deterministic rule: explicit overrides,
  macOS serial topology when available, then name/default ranking.

## Diagnostics

The audio diagnostics contributor includes the effective USB audio contract when
the active radio exposes it. This gives support and pro tooling enough detail to
explain which OS audio device/rate/channels were actually used.
