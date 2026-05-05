# Mini RFC: Audio API v1 (PCM-first + explicit low-level API)

Status: Draft (for approval)

Related issues: #1, #2, #3, #4, #5, #6, #7, #8, #10  
Already in progress: #9 via PR #11

## 1) Problem
Current audio surface mixes low-level Opus-oriented operations and user-facing PCM workflows.
This makes API naming ambiguous, CLI scope unclear, and recovery/testing behavior under-specified.

## 2) Goals
- Provide a **PCM-first high-level API** for common workflows.
- Keep a clear **explicit low-level Opus API** for advanced users.
- Define deterministic defaults and capability introspection.
- Add runtime stats and predictable recovery semantics.
- Preserve backward compatibility with a deprecation window.

## 3) Non-goals
- No protocol redesign.
- No breaking changes in one release.
- No broad refactor outside audio/CLI/test/doc scope.

## 4) Decisions

### D1. API split: explicit low-level vs high-level

#### Low-level (Opus, explicit naming)
- `start_audio_rx_opus(callback, *, jitter_depth=5)`
- `stop_audio_rx_opus()`
- `start_audio_tx_opus()`
- `push_audio_tx_opus(opus_bytes)`
- `stop_audio_tx_opus()`

#### High-level (PCM)
- `start_audio_rx_pcm(callback, *, sample_rate=48000, channels=1, frame_ms=20)`
- `stop_audio_rx_pcm()`
- `start_audio_tx_pcm(*, sample_rate=48000, channels=1, frame_ms=20)`
- `push_audio_tx_pcm(pcm_bytes)`
- `stop_audio_tx_pcm()`

### D2. Backward compatibility + deprecation
- Keep current methods (`start_audio_rx`, `start_audio_tx`, `push_audio_tx`, etc.) as aliases to low-level behavior.
- Emit `DeprecationWarning` with replacement hints.
- Deprecation window: two minor releases.
- Remove ambiguous aliases at next major.

### D3. Internal transcoder layer
- Add internal PCM<->Opus transcoder abstraction.
- Use `opuslib` when available (`pip install rigplane[audio]`).
- If missing, high-level PCM APIs raise actionable error:
  `Audio codec backend unavailable; install rigplane[audio]`.

### D4. Capabilities model + deterministic defaults
Add `AudioCaps` model and API:
- `get_audio_caps()` returns:
  - supported codecs
  - supported sample rates
  - supported channel counts
  - default codec/rate/channels

CLI:
- `rigplane audio caps [--json]`

Default selection (deterministic):
1. Prefer Opus mono 48k
2. Else Opus stereo 48k
3. Else best PCM mode
4. If no valid combo -> clear validation error

### D5. Runtime stats contract
Add `get_audio_stats()` shape (JSON-friendly):
- `packet_loss_pct` (0..100)
- `jitter_ms`
- `underruns`
- `overruns`
- `est_latency_ms`
- `rx_packets`, `tx_packets`

Stats availability: during active stream and for a short terminal window after stop.

### D6. Recovery model
Optional auto-recover after reconnect:
- config: `auto_recover=True`, `recover_max_attempts=5`
- state events: `recovering`, `recovered`, `failed`
- single active stream invariant (no duplicate RX/TX tasks)

### D7. CLI scope
Add `rigplane audio` command group:
- `audio rx --out rx.wav --seconds 10`
- `audio tx --in tx.wav`
- `audio loopback --seconds 10`
- `audio caps`

Common flags:
- `--sample-rate`, `--channels`, `--json`, `--stats`

## 5) Delivery plan (by issue dependency)

### Phase A (foundation)
- #1 Transcoder layer
- #10 Naming + deprecation map

### Phase B (public APIs)
- #2 RX high-level PCM API
- #3 TX high-level PCM API
- #8 Capability introspection + defaults

### Phase C (CLI + observability)
- #4 CLI audio subcommands
- #6 Runtime stats API + `--stats`

### Phase D (resilience + CI)
- #7 Auto-recover behavior
- #5 E2E + CI stabilization

## 6) Test strategy
- Unit tests for transcoder adapters and validation.
- Integration tests for RX/TX/loopback (normal + reconnect).
- CLI smoke tests for all new subcommands.
- CI split:
  - Fast smoke on each PR
  - Heavier integration profile on schedule/manual

## 7) Risks
- Opus backend differences across platforms.
- Reconnect race conditions in async tasks.
- Stats drift if metric units are not fixed in docs/tests.

Mitigation:
- strict typed errors,
- state machine invariants,
- unit-tested metric contract and fixtures.

## 8) Acceptance mapping
- #1/#2/#3: high-level PCM APIs operational and tested
- #4: CLI audio workflow commands work end-to-end
- #5: CI smoke/integration split stable
- #6: `get_audio_stats()` + CLI stats output
- #7: reconnect recovery behavior deterministic
- #8: `audio caps` + safe defaults
- #10: naming map + deprecation warnings + migration notes

## 9) Open questions for maintainer approval
1. Deprecation window length: exactly 2 minor releases OK?
2. Keep current ambiguous names as low-level aliases or add hard warnings immediately?
3. Should `--stats` print periodic stream stats (live) or end-of-run summary by default?
4. Is `opuslib` optional dependency acceptable as the default high-level backend?

## 10) DSP pipeline + PCM tap gate on Opus-native radios (issue #762)

**Behavior:** the web audio broadcaster's DSP pipeline (noise gate,
limiter, etc.) and the PCM tap registry (used by the FFT / waterfall
scope and audio analyzers) both operate on decoded PCM16.  When the
radio's native audio codec is Opus (IC-705 and any future Opus-only
model), the broadcaster passes the Opus frame through without
decoding, so DSP and taps **do not run**.

**Why we don't decode + re-encode on the hot path:** Opus re-encode
would introduce quality loss on every frame.  Users of IC-705 have
not reported needing DSP or scope through the web UI, so the gate
is documented rather than closed.

**Observability:** the broadcaster emits a one-shot `WARNING` log
entry when it detects an active DSP pipeline on an Opus-native
codec — fires at `set_dsp_pipeline()` or at `_refresh_codec_state()`
(in case the codec flips mid-stream), whichever happens first.

**Upgrade path if demand arrives:** issue #762 §"Option A" — decode
Opus once in `_relay_loop`, run DSP + feed taps on the PCM buffer,
then re-encode before fan-out.  `_audio_transcoder.PcmOpusTranscoder`
already exists and can be reused.  Quality loss is negligible for
AM/FM ham audio but non-zero on SSB; flag behind a config toggle if
implemented.

## 11) LAN MAIN/SUB audio routing (epic #787)

**Wire format.**  On dual-RX radios the LAN audio stream is **stereo
PCM16 with L=MAIN and R=SUB** whenever a 2-channel codec is
negotiated.  `_DEFAULT_CODEC_PREFERENCE` in `types.py` leads with
`PCM_2CH_16BIT` (0x10); single-RX firmware downgrades to mono during
handshake, and the broadcaster's `_refresh_codec_state` reads the
negotiated codec back so downstream logic tracks reality rather than
the requested codec.

**Phones L/R Mix is always OFF.**  CI-V `0x1A 05 00 72` is a boolean
toggle — `0x00` = Mix OFF (separated stereo), `0x01` = Mix ON (summed
to both channels).  The backend keeps it locked at `0x00` via two
paths:

- `AudioHandler._handle_audio_config` — every `audio_config` WS
  message emits `0x00`, independent of the `split_stereo` payload.
- `AudioBroadcaster._apply_phones_mix_off` — fires once on every
  relay start (guarded on `receiver_count >= 2`) so the LAN stream
  begins in separated-stereo state regardless of the radio's prior
  menu state.  Errors are swallowed so start-up continues on radios
  where the command is unsupported.

If Mix were ON the radio would pre-sum MAIN + SUB before transmission
and the frontend graph could no longer isolate a single receiver —
`focus=main` and `focus=sub` would both play the summed signal.

**`focus` and `split_stereo` live on the frontend.**  The WebAudio
graph in `frontend/src/lib/audio/rx-player.ts` routes the stereo
pair through `ChannelSplitter(2) → GainNode×2 → StereoPanner×2 →
destination`:

| `focus` | `split_stereo` | L gain | R gain | L pan | R pan |
|---------|----------------|--------|--------|-------|-------|
| main    | false          | 1.0    | 0.0    | 0     | 0     |
| main    | true           | 1.0    | 0.0    | −1    | +1    |
| sub     | false          | 0.0    | 1.0    | 0     | 0     |
| sub     | true           | 0.0    | 1.0    | −1    | +1    |
| both    | false          | 1.0    | 1.0    | 0     | 0     |
| both    | true           | 1.0    | 1.0    | −1    | +1    |

Panning depends **only** on `split_stereo` (see
`rx-player.ts::_applyGraphState`, lines 247-257) — when on,
`mainPanner` hard-pans to −1 and `subPanner` to +1 regardless of
`focus`.  For single-receiver focus (`focus=main` or `focus=sub`) the
opposing gain is already 0 so the pan on the silenced channel is
inaudible, but the setting still applies to the active channel — e.g.
`focus=main` + `split_stereo=true` routes MAIN to the left ear only.

Per-channel dB sliders (`mainGainDb` / `subGainDb`) multiply into the
L/R gains respectively.

**Why the WS message still exists.**  `audio_config` is retained as
a bidirectional echo channel so the client can persist its `focus` +
`split_stereo` choice and the backend confirms via `applied: true`.
The CI-V round-trip it used to drive (the broken `_PHONES_LR_MIX`
dict pre-#788) is gone — the message now only raises the
broadcaster's `_codec_stale` flag so a mid-stream codec/channel
change triggers a fresh `_refresh_codec_state` pass (issue #766).

**Historical context.**  Revisions before #788 sent `0x02` / `0x03`
on this sub-command for `focus=sub` / `focus=both` — values the radio
silently ignored per the CI-V reference (only `{0x00, 0x01}` valid).
#788 briefly tied the byte to `split_stereo`; #792 corrected that to
the lock-at-`0x00` contract above.  The dead-code mapping is
documented here rather than in a deleted source comment so future
contributors don't rediscover the same trap.
