---
robots: noindex, follow
---

# Audio capture health triage

This note is for agents and maintainers diagnosing TX reverse-path failures
from logs, runtime metrics, or diagnostic bundles.

## Scope

Use this checklist when RigPlane is capturing local PCM and forwarding it
toward radio TX through `AudioBridge`. The goal is to separate:

- PortAudio capture callback overflow or underflow;
- bridge queue drops or backpressure;
- silence or noise-gate suppression;
- downstream TX push or write failures after capture succeeds.

Do not use this doc to infer customer-specific support steps. Keep triage at
the public open-core behavior boundary.

## Deterministic triage order

1. Confirm the bridge is actually running.
   - Check `bridge_state == "running"` and that `rx_frames` or
     `frames_delivered` are moving.
   - If frames never advance, this is not yet a queue-pressure or radio-write
     problem.
2. Check capture callback health first.
   - Read `capture_input_overflows`, `capture_input_underflows`, and
     `capture_callback_status_flags`.
   - If any capture counter is rising, stop here and treat the first fault as
     local capture pressure or device instability.
3. Check bridge queue drops next.
   - Read `tx_overruns`.
   - Non-zero `tx_overruns` with zero capture overflows means RigPlane is
     dropping stale queued frames after capture, to preserve latency.
4. Check silence suppression separately.
   - Read `tx_silence_suppressed`.
   - Rising silence suppression with zero capture overflows means the source is
     below the bridge gate threshold, not that PortAudio lost buffers.
5. Check downstream TX failures only after capture looks healthy.
   - Inspect `write_failures`, `last_error`, raised exceptions, or backend/radio
     TX logs.
   - This is where push/write rejection belongs.
6. Only then inspect mode, DATA policy, route policy, or codec negotiation.
   - Those may block successful TX, but they are later than capture-health and
     queue-pressure faults.

## Field meanings

| Field | Meaning | First interpretation |
| --- | --- | --- |
| `capture_input_overflows` | Active bridge saw input-side callback overflow events | OS/backend capture lost input before bridge queueing |
| `capture_input_underflows` | Active bridge saw input-side callback underflow events | Capture callback ran without enough fresh source samples |
| `capture_callback_status_flags` | Rollup of exact input callback flags | Confirms which callback condition occurred |
| `tx_overruns` | Bridge TX queue evicted stale frames | Capture succeeded; latency protection dropped queued audio later |
| `tx_silence_suppressed` | Frames skipped because peak stayed below gate threshold | Intentional policy, not callback starvation |
| `write_failures` / `last_error` | TX write or push failed after capture | Downstream radio/backend failure |

## Example snapshots

### Healthy capture, silence-gated source

```json
{
  "bridge_state": "running",
  "capture_input_overflows": 0,
  "capture_input_underflows": 0,
  "capture_callback_status_flags": {},
  "tx_overruns": 0,
  "tx_silence_suppressed": 18
}
```

Interpretation: reverse-path capture is healthy. RigPlane is intentionally
dropping near-silent frames. Check source level and gate expectations before
investigating transport.

### Bad capture callback, not a queue drop

```json
{
  "bridge_state": "running",
  "capture_input_overflows": 6,
  "capture_input_underflows": 1,
  "capture_callback_status_flags": {
    "input_overflow": 6,
    "input_underflow": 1
  },
  "tx_overruns": 0,
  "tx_silence_suppressed": 0
}
```

Interpretation: the first failure is at the local capture callback. The bridge
never needed to drop queued frames, and silence gating is irrelevant here. Look
at host scheduling pressure, capture device stability, or callback cadence.

### Queue pressure after healthy capture

```json
{
  "bridge_state": "running",
  "capture_input_overflows": 0,
  "capture_input_underflows": 0,
  "capture_callback_status_flags": {},
  "tx_overruns": 9,
  "tx_silence_suppressed": 0
}
```

Interpretation: capture is healthy. RigPlane is evicting stale queued frames
later in the TX bridge path. Investigate downstream consumption speed, not the
capture device.

## Diagnostic bundle notes

- `audio/audio.json` records contract, device, and bridge-active metadata.
- It does not carry live `capture_input_overflows`, `tx_overruns`, or
  `tx_silence_suppressed` counters by itself.
- When only a diagnostic bundle is available, use it to confirm the selected
  contract and device first, then correlate with runtime logs or separately
  captured bridge stats.

## Test and CI notes

CI coverage for these semantics uses fake audio backends and fake PortAudio
status flags in unit tests such as `tests/test_audio_backend.py`,
`tests/test_audio_bridge.py`, and `tests/test_audio_duplex.py`. No live radio
or hardware validation is required to confirm the documented field meanings.
