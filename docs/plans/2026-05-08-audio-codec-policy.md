# Audio Codec Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce an explicit audio route and codec policy so direct radio LAN streams, local bridges, DSP/taps, and web/app clients each receive the correct codec, sample rate, and channel contract.

**Architecture:** Direct Icom LAN operation is PCM-first. Opus is not a radio-native codec for direct Icom LAN radios such as IC-7610; it may be used later as a server-to-client transport optimization over constrained links. The policy flow is: `RadioProfile audio policy -> effective LAN stream request -> radio-native stream contract -> consumer-specific output contract`.

**Tech Stack:** Python backend, TOML radio profiles, Icom LAN conninfo, existing `AudioCodec`, `AudioBus`, `AudioBroadcaster`, `AudioBridge`, `PcmOpusTranscoder`, frontend WebAudio/WebCodecs RX player.

---

## Context

Issue: `rigplane-core#1467`, "IC-7610 receive audio distortion".

The issue reports two IC-7610 RX symptoms:

- Default sample-rate mismatch: rigplane requests and labels `48000`, while the customer's IC-7610 appears to emit usable stereo PCM16 only up to `16000`.
- Browser PCM playback artifact: raw PCM frames scheduled through `AudioBufferSourceNode.start(when)` produce periodic clicks; sending Opus to the browser avoids that client playback path.

Recent WSJT-X bridge work fixed the TX bridge path by making LAN TX PCM explicit and by routing WSJT-X LAN operation through DATA2/LAN. This plan keeps that lesson: codec/rate/channel decisions must come from route and profile policy, not from local guesses inside a web handler.

## Findings

### Icom direct LAN is PCM-first

Current code defines Icom LAN audio codec bytes in `AudioCodec`:

- `PCM_1CH_16BIT = 0x04`
- `PCM_2CH_16BIT = 0x10`
- `OPUS_1CH = 0x40`
- `OPUS_2CH = 0x41`

The enum docstring states that Opus codecs are available only when the radio reports `connection_type == "WFVIEW"`. The wfview reference code also forces Opus back to LPCM16 when the connection type is not `WFVIEW`.

Decision: direct radio LAN policy must not prefer Opus. Opus is only a potential server-to-client transport codec.

### Current negotiation is mostly a request, not a measured result

`build_conninfo_packet()` writes requested `rxcodec`, `txcodec`, `rxsample`, and `txsample` into the conninfo packet. The radio accepts or rejects the session, but the backend does not receive a separate "actual sample rate" value.

Consequence: if a radio silently caps or mangles an unsupported sample rate, `radio.audio_sample_rate` can remain the requested value even when the wire payload rate differs.

### Current defaults mix global and model-specific concerns

`get_audio_capabilities()` builds a global default codec/rate:

- codec default: `PCM_2CH_16BIT`
- sample-rate default: `ICOM_AUDIO_SAMPLE_RATE`, currently `48000`

Profiles can already pin `codec_preference`, but they cannot express sample-rate policy, per-codec rate caps, or browser transport preferences.

### Consumer contracts are conflated

`AudioBus` distributes radio-native payloads. `AudioBroadcaster` currently maps radio codecs directly to web codecs. For PCM/uLaw radios it emits PCM16 to the browser; for Opus-native streams it emits Opus. This conflates:

- radio-native stream format;
- DSP/tap/bridge PCM contract;
- browser/web-app transport contract.

Decision: keep `AudioBus`, `AudioBridge`, DSP, FFT, and analyzers PCM/native where appropriate. Any PCM-to-Opus conversion for browsers must happen as a consumer-specific web emission policy after PCM taps/DSP.

## Target Contracts

### RadioProfile audio policy

Profiles should be able to describe radio audio behavior without hardcoding model names in handlers.

Proposed profile fields:

```toml
[audio]
codec_preference = ["PCM_2CH_16BIT", "PCM_1CH_16BIT", "ULAW_2CH", "ULAW_1CH"]
tx_codec = "PCM_1CH_16BIT"
default_sample_rate_hz = 16000
supported_sample_rates_hz = [8000, 16000]
sample_rate_by_codec = { PCM_2CH_16BIT = 16000, PCM_1CH_16BIT = 16000 }
browser_rx_transport = "auto"
browser_rx_transcode_to_opus = true
```

`browser_rx_transcode_to_opus = true` means "browser delivery may use Opus". It does not mean "request Opus from the radio".

### Effective LAN stream request

The backend should resolve a concrete request before conninfo:

```python
AudioStreamRequest(
    rx_codec=AudioCodec.PCM_2CH_16BIT,
    tx_codec=AudioCodec.PCM_1CH_16BIT,
    rx_sample_rate_hz=16000,
    tx_sample_rate_hz=16000,
    source="profile-default",
)
```

Rules:

- Explicit CLI/env/API sample-rate override wins.
- Explicit codec override wins if compatible.
- Profile default wins over global default.
- If the requested stereo codec is rejected during conninfo, existing stereo-to-mono fallback remains valid, but it must update the effective contract.

### Radio-native stream contract

After conninfo succeeds, expose the effective radio-native contract:

```python
AudioStreamContract(
    rx_codec=AudioCodec.PCM_2CH_16BIT,
    tx_codec=AudioCodec.PCM_1CH_16BIT,
    rx_sample_rate_hz=16000,
    tx_sample_rate_hz=16000,
    rx_channels=2,
    tx_channels=1,
)
```

This contract should be the source for `radio.audio_codec`, `radio.audio_sample_rate`, bridge sample-rate defaults, broadcaster headers, and diagnostics.

### Consumer-specific output contract

Web/browser delivery may choose a different transport codec:

```python
AudioConsumerContract(
    consumer="web-rx",
    input_codec=AudioCodec.PCM_2CH_16BIT,
    input_sample_rate_hz=16000,
    output_codec="opus",
    output_sample_rate_hz=16000,
    output_channels=2,
    frame_ms=20,
)
```

Bridge/DSP/taps stay PCM/native:

```python
AudioConsumerContract(
    consumer="bridge-rx",
    input_codec=AudioCodec.PCM_2CH_16BIT,
    output_codec="pcm16",
    output_sample_rate_hz=16000,
    output_channels=1,
)
```

## Non-Goals

- Do not request Opus from direct Icom LAN radios by default.
- Do not move Opus transcode into `AudioBus`.
- Do not make `AudioBridge` consume browser/web transport frames.
- Do not accept the reference patch as-is inside `web/handlers/audio.py`.
- Do not change DATA1/DATA2 policy as part of this issue.

## Implementation Tasks

### Task 1: Add profile audio policy model and tests

**Files:**
- Modify: `src/rigplane/profiles/__init__.py`
- Modify: `src/rigplane/profiles/rig_loader.py`
- Modify: `rigs/ic7610.toml`
- Test: `tests/test_rig_loader.py`

- [ ] Add profile fields for `tx_codec`, `default_sample_rate_hz`, `supported_sample_rates_hz`, `sample_rate_by_codec`, `browser_rx_transport`, and `browser_rx_transcode_to_opus`.
- [ ] Validate codec names against `AudioCodec`.
- [ ] Validate sample rates as positive integers from the supported Icom/Opus set: `8000`, `12000`, `16000`, `24000`, `48000`.
- [ ] Add IC-7610 profile defaults: stereo PCM16 RX, mono PCM16 TX, `16000` Hz default, browser Opus transcode allowed.
- [ ] Add regression tests proving existing profiles without new fields still load.

### Task 2: Resolve effective audio stream request before conninfo

**Files:**
- Create or modify: `src/rigplane/audio/route.py`
- Modify: `src/rigplane/runtime/radio.py`
- Modify: `src/rigplane/backends/config.py`
- Test: `tests/test_audio_route.py`
- Test: `tests/test_radio_connect.py`

- [ ] Introduce a small resolver that accepts profile policy plus explicit constructor/config values.
- [ ] Track whether sample-rate and codec were explicit or defaulted.
- [ ] Ensure explicit user overrides win over profile defaults.
- [ ] Ensure IC-7610 LAN default resolves to `PCM_2CH_16BIT` RX, `PCM_1CH_16BIT` TX, `16000` Hz.
- [ ] Ensure non-IC-7610 defaults remain behavior-compatible until their profiles are audited.

### Task 3: Expose radio-native audio stream contract

**Files:**
- Modify: `src/rigplane/runtime/radio.py`
- Modify: `src/rigplane/runtime/_control_phase.py`
- Modify: `src/rigplane/core/radio_protocol.py`
- Test: `tests/test_audio_codec.py`
- Test: `tests/test_radio_connect.py`

- [ ] Replace ad hoc `_audio_codec`, `_audio_tx_codec`, and `_audio_sample_rate` reads with a resolved contract where practical.
- [ ] Keep existing public properties for compatibility.
- [ ] Update stereo-to-mono fallback so the contract reflects the final accepted RX codec.
- [ ] Add tests for conninfo bytes: `rxcodec`, `txcodec`, `rxsample`, `txsample`.

### Task 4: Split web emission contract from radio-native contract

**Files:**
- Modify: `src/rigplane/web/handlers/audio.py`
- Test: `tests/test_web_server.py`
- Test: `tests/test_web_audio_streaming_profile.py`

- [ ] Add an internal web RX emission policy object.
- [ ] Keep PCM taps and DSP before browser transcode.
- [ ] If web policy selects Opus, buffer PCM into legal Opus frame sizes and emit `AUDIO_CODEC_OPUS`.
- [ ] If Opus encoder is unavailable, log once and fall back to PCM16 with correct sample-rate/channels.
- [ ] Add tests proving bridge/taps still receive PCM while browser receives Opus.

### Task 5: Add diagnostics and operator visibility

**Files:**
- Modify: `src/rigplane/diagnostics/contributors/audio.py`
- Modify: `docs/guide/audio-recipes.md`
- Test: `tests/test_diagnostics_contributors_batch2.py`

- [ ] Report requested radio-native codec/rate/channels.
- [ ] Report effective radio-native codec/rate/channels.
- [ ] Report web emission codec/rate/channels.
- [ ] Include whether each value came from explicit user config, profile default, or fallback.

### Task 6: Profile audit follow-up

**Files:**
- Modify later: `rigs/ic705.toml`
- Modify later: `rigs/ic7300.toml`
- Modify later: `rigs/ic9700.toml`
- Modify later: `rigs/ftx1.toml`

- [ ] Audit each supported radio against official docs, wfview profiles, and available hardware data.
- [ ] Fill profile audio policy where known.
- [ ] Leave unknown values absent rather than inventing them.
- [ ] Add a fallback probing or downgrade strategy only where protocol evidence supports it.

## Test Matrix

Required before closing `#1467`:

- `uv run pytest tests/test_rig_loader.py tests/test_audio_route.py tests/test_audio_codec.py tests/test_radio_connect.py -q`
- `uv run pytest tests/test_web_server.py tests/test_web_audio_streaming_profile.py -q`
- `uv run pytest tests/test_audio_bridge.py tests/test_audio_bridge_stereo.py tests/integration/test_rigctld_audio_pipeline.py -q`
- Frontend RX tests if browser code changes: `npm run test -- --run frontend/src/lib/audio/__tests__/rx-player.test.ts`

Hardware validation:

- IC-7610 LAN with no `ICOM_AUDIO_SAMPLE_RATE`: verify conninfo requests `16000` Hz.
- IC-7610 LAN with explicit `ICOM_AUDIO_SAMPLE_RATE=48000`: verify override is respected and diagnostics show explicit source.
- Browser RX: verify no chipmunk playback and no periodic click pattern.
- WSJT-X bridge: verify RX decode still works and LAN TX still uses PCM/DATA2 path.

## Open Questions

- Should web Opus transcode be default-on for IC-7610 immediately, or hidden behind an `auto` policy that can be disabled from CLI/env?
- Should profile `supported_sample_rates_hz` represent documented support, empirically safe support, or both as separate fields?
- Do we want server-to-client Opus as a general low-bandwidth mode for pro/remote deployments, separate from this IC-7610 browser bug?

