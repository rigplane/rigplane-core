---
description: Drive the RigPlane spectrum display from an SDR (RTL-SDR first) — remote SoapySDR quick start, HF direct sampling, ppm correction, IF-tap offsets, span limits, environment variables, and the licensing boundary.
---

# SDR Panadapter Scope

RigPlane can drive the spectrum display (`/api/v1/scope`) from a
software-defined radio instead of the radio's built-in scope: a wide-band
panadapter that follows the VFO as you tune, freezes while you transmit, and
falls back to the hardware scope (or the audio FFT) if the SDR stops producing
frames. The SDR path is built on SoapySDR's `remote` driver — RigPlane never
loads local SDR device drivers itself. An RTL-SDR works today; any
SoapySDR-supported receiver reachable through a SoapySDR server should work.

## Quick start

On the machine with the RTL-SDR, install SoapySDR with the RTL-SDR module
(package names vary by distribution; `soapysdr-module-rtlsdr` on Debian/Ubuntu)
and start the SoapySDR server so it can see the stick. RigPlane always talks to
that server over the `remote` driver — on a Station with the SDR attached, or
on a desktop with the SDR plugged in locally, the server is on loopback:

```bash
rigplane web --host 192.168.1.50 \
  --sdr-device driver=remote,remote=127.0.0.1
```

With `--sdr-device` given, the spectrum display prefers the SDR while its
frames flow. `--scope-source` picks the source explicitly (`auto`, `hardware`,
`sdr`, `audio_fft`); the default `auto` uses the SDR when configured, else the
radio's hardware scope, else the audio FFT.

## RTL-SDR notes

### HF reception (direct sampling)

An RTL-SDR V3 reaches HF through its direct-sampling input; enable it with
`--sdr-setting direct_samp=2`. The V4 stick switches to HF automatically and
needs no setting. Repeat `--sdr-setting` for multiple `KEY=VAL` pairs.

### Frequency correction (ppm)

Every RTL-SDR stick has a small frequency error, in parts per million. Once you
know yours (comparing a known signal against where it appears), correct it with
`--sdr-ppm 23`.

### IF tap vs antenna tap

Tapping a transceiver's IF requires telling RigPlane where the SDR is tuned
relative to the radio VFO, and sometimes flipping the displayed axis:

```bash
--sdr-offset-hz 8830000   # SDR center = VFO + 8.83 MHz (IF frequency)
--sdr-invert              # mirrored IF output
```

With the plain antenna tap the offset stays 0 (the default) and no inversion
is needed.

### Span limit

The display span must fit inside the sampled bandwidth with guard room:
`--sdr-span-hz` may not exceed 0.6 × `--sdr-sample-rate`
(`runtime.py: SPAN_LIMIT_RATIO`). The default span is a quarter of the sample
rate (`controller.py: DEFAULT_SPAN_DIVISOR`); at the default 2.4 Msps that is
600 kHz. Widen the span by raising the sample rate first.

## Status in the Web UI state

The public state payload carries a live `sdr` object
(`state_schema.py: SdrStatusPublic`) with `state` (`disabled`, `starting`,
`streaming`, `reconnecting` — started, but no frames within the stale window
while the source reconnects in the background — or `error`, where `lastError`
carries the message), plus `device`, `sampleRateHz`, `spanHz`, `txFrozen` and
`overflowCount`. `disabled` means no SDR configured or the pipeline not
running.

## Environment variables

Every `--sdr-*` flag has an `RIGPLANE_SDR_*` environment variable fallback
(`env_config.py: get_sdr_env_overrides`). A flag given on the command line wins
over the environment; a set-but-invalid variable stops startup with an error
naming the variable.

| Variable | Maps to flag | Example |
|---|---|---|
| `RIGPLANE_SCOPE_SOURCE` | `--scope-source` | `sdr` |
| `RIGPLANE_SDR_DEVICE` | `--sdr-device` | `driver=remote,remote=127.0.0.1` |
| `RIGPLANE_SDR_SAMPLE_RATE` | `--sdr-sample-rate` | `2400000` |
| `RIGPLANE_SDR_GAIN` | `--sdr-gain` | `32.5` or `auto` |
| `RIGPLANE_SDR_PPM` | `--sdr-ppm` | `23` |
| `RIGPLANE_SDR_OFFSET_HZ` | `--sdr-offset-hz` | `8830000` |
| `RIGPLANE_SDR_SPAN_HZ` | `--sdr-span-hz` | `600000` |
| `RIGPLANE_SDR_INVERT` | `--sdr-invert` | `1` |
| `RIGPLANE_SDR_SETTINGS` | `--sdr-setting` (repeatable) | `direct_samp=2,biastee=true` |

## Licensing boundary (GPL)

SoapySDR's driver modules — `librtlsdr` among them — are GPL-licensed. RigPlane
core keeps them out of its own process: the RTL-SDR stack runs only inside the
SoapySDR server you start next to the hardware, and RigPlane speaks the
BSD-licensed `remote` protocol to it. Before SoapySDR is first imported, RigPlane
restricts the module search path so only the `remote` module can load
(`runtime.py: apply_remote_only_env`); when the remote module cannot be found, a
warning is logged and no restriction is applied.

## Troubleshooting

- **`error` state at startup** — read `lastError` in the `sdr` state object. A
  missing SoapySDR install or an unreachable server is the usual cause.
- **Display stays on the hardware scope / audio FFT** — the `sdr` state shows
  `starting` (server reachable, no frames yet) or `reconnecting` (frames
  stopped; check the SoapySDR server logs). RigPlane falls back automatically
  and returns to the SDR when frames resume.
- **Signal at the wrong frequency** — set `--sdr-ppm`; for an IF tap check
  `--sdr-offset-hz` and `--sdr-invert`.
