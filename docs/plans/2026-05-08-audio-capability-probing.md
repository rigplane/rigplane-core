# Audio Capability Probing Design

Issues: #1479, #1481, #1482, #1483
Date: 2026-05-08

## Scope

This design separates radio-native LAN audio probing from consumer transport
encoding and OS audio-device probing.

Direct stock-radio LAN policy is protocol-specific. For Icom LAN, probe
artifacts test PCM/uLaw conninfo combinations and intentionally exclude Opus:
wfview-server compatibility is a separate transport concern and is not a
priority for radio-native defaults.

USB audio is out of scope for radio-native codec probing. USB-connected radios
expose OS audio devices, so their hardening lives in #1480.

## Boundaries

- Radio-native stream policy: codec/sample-rate/channels sent to the radio or
  accepted by the radio protocol endpoint.
- Probe result: one attempted candidate plus its accepted/rejected/failed state.
- Evidence artifact: machine-readable JSON containing model, profile, transport,
  metadata, candidates, statuses, reasons, and observed payload data.
- Profile update: a later explicit change to `rigs/*.toml` made only from passed
  evidence. Rejected or failed candidates remain evidence but never become
  defaults.

## Foundation

`rigplane.audio.probe` defines:

- `AudioProbeCandidate`
- `AudioProbeResult`
- `AudioProbeArtifact`
- `AudioProbeStatus`
- `build_icomlan_probe_matrix()`
- `classify_icomlan_probe_error()`
- `profile_policy_from_probe_results()`

The initial Icom LAN matrix is conservative:

- RX codecs: PCM 2ch/1ch 16-bit, uLaw 2ch/1ch.
- TX codec: mono PCM 16-bit.
- Sample rates: 48000, 24000, 16000, 8000.
- Opus: excluded for direct stock-radio LAN.

## Follow-Up Work

- #1481 adds the actual Icom LAN probe runner and CLI.
- #1482 records hardware validation artifacts.
- #1483 turns passed artifacts into guarded profile update proposals.
