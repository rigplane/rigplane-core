# Core Issue Draft: Build automated audio pipeline harness for WSJT-X/LAN/USB routing

## Context

Debugging the IC-7610 TX audio bridge required repeated manual steps across
machines: start rigplane, start WSJT-X, press Tune, listen locally, watch the
radio waterfall, inspect logs, and infer whether the failure was PortAudio,
bridge capture, codec negotiation, rigctld packet mode, DATA source selection,
or LAN audio TX payload format.

That workflow is too slow and too fragile for future audio work. We need an
automated harness that exercises the whole pipeline with synthetic audio and
observable sinks.

Related route-policy issue: rigplane/rigplane-core#1446.

## Goal

Create a layered audio pipeline test harness that can run without a human
pressing WSJT-X Tune, while still allowing optional hardware-assisted validation
against real radios.

## Proposed Test Layers

### Layer 1: Pure in-process pipeline

- Synthetic PCM source, e.g. 1 kHz tone frames.
- Fake bridge input/output devices.
- Fake radio audio sink that records pushed TX payloads.
- Assertions:
  - non-zero peak/RMS reaches bridge TX capture path;
  - expected frame count and continuity;
  - PCM TX stays raw PCM when negotiated codec is `PCM_1CH_16BIT`;
  - Opus encode is used only when negotiated TX codec is Opus;
  - no silence is introduced by downmix/resampling/chunking.

### Layer 2: rigctld + bridge integration

- Run embedded rigctld against fake radio/backend.
- Replay WSJT-X-like rigctl commands: `M PKTUSB`, `T 1`, `T 0`.
- Feed synthetic audio during the TX window.
- Assertions:
  - PTT transitions occur;
  - packet mode maps to the route-derived DATA policy;
  - DATA1 MOD is not touched automatically;
  - TX audio payload continues for the whole TX window.

### Layer 3: OS audio backend smoke

- Use PortAudio/sounddevice with a deterministic local virtual device when
  available, or a fake backend in CI.
- Feed synthetic tone into the configured TX device.
- Capture through the same backend path used by `AudioBridge`.
- Assertions:
  - device selection uses the requested RX/TX devices;
  - capture is non-zero under the same process/env as rigplane;
  - no regression to all-zero capture when RX output is also open.

### Layer 4: hardware-assisted validation

- Optional test profile for real IC-7610 LAN at a configured host.
- No WSJT-X UI dependency: drive rigctld commands and synthetic audio directly.
- Capture logs and radio-side ACK/state where possible.
- Assertions:
  - route policy selects DATA2/LAN for direct LAN multi-DATA radio;
  - raw PCM TX produces continuous radio TX audio symptoms;
  - no broad intermittent burst pattern from Opus-as-PCM mismatch.

## Requirements

- Tests must run through `uv`.
- CI-safe layers must not require radio hardware, microphone permissions, or
  BlackHole/VB-Cable.
- Hardware/OS layers must be opt-in through environment variables and skip with
  clear reasons.
- The harness must produce compact diagnostics: route, codec, frame count, RMS,
  peak, drops, selected devices, and rigctld DATA policy.
- The harness must not depend on the WSJT-X GUI. WSJT-X behavior should be
  represented by rigctl command replay and synthetic audio generation.

## Acceptance Criteria

- A developer can run one command to validate the in-process TX audio pipeline.
- A developer can run one command to validate rigctld packet-mode + TX audio
  bridge behavior without a radio.
- Optional OS/hardware tests are documented and skipped unless explicitly
  enabled.
- The previous IC-7610 failure modes would have been caught by automated tests:
  - PCM negotiated but Opus bytes sent;
  - DATA1 selected for direct LAN WSJT-X bridge;
  - DATA1 MOD rewritten by profile apply/restore;
  - PortAudio bridge TX capture reads all-zero frames.

## Notes

This is open-core scope because it validates generic audio routing, codec
contract, rigctld behavior, and backend-independent diagnostics. Pro can consume
the same harness for packaged desktop smoke tests.
