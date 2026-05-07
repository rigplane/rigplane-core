# Managed Runtime Packaging Requirements

This document captures the public-core packaging requirements for the Pro-managed
station runtime. Product packaging, signing, installers, licensing, and desktop
supervision remain in `rigplane-pro`; protocol, discovery, audio backend, and
runtime dependency contracts stay here in `rigplane-core`.

## Runtime Dependency Matrix

| Area | Python package | Native/runtime dependency | Notes |
|------|----------------|---------------------------|-------|
| Serial control | `pyserial`, `pyserial-asyncio` | OS USB serial driver and device permissions | Required for USB CI-V and CAT backends. |
| LAN audio codec | `opuslib` | `libopus` discoverable by the dynamic loader | Required for Opus encode/decode paths used by LAN audio and bridge transcoding. |
| Local audio bridge | `sounddevice`, `numpy` | PortAudio | Required for BlackHole, VB-Cable, PipeWire, PulseAudio, and other local loopback devices. |
| macOS USB audio lookup | stdlib `ctypes` | CoreAudio framework | Used to map PortAudio/sounddevice devices to stable CoreAudio UIDs where available. |
| Optional DSP | `scipy` when enabled | platform wheels/native libraries | Not required for the minimum managed-runtime startup contract. |

## Platform Topology

| Platform | Serial/USB control | Local audio topology | Current support posture |
|----------|--------------------|----------------------|-------------------------|
| macOS | `/dev/cu.*` USB serial devices via `pyserial`; user may need device permissions | CoreAudio through PortAudio/sounddevice; BlackHole or Loopback for virtual bridge; direct radio USB audio appears as CoreAudio input/output devices | Best-covered development path. Minimum paid-v1 support should include direct LAN audio, USB serial control, and documented BlackHole/Loopback setup. |
| Windows | COM ports via `pyserial`; radio/vendor driver may be required | PortAudio/sounddevice over WASAPI/MME/DirectSound; VB-Cable is the expected virtual bridge path | Supported target, but USB audio device naming and VB-Cable routing need release smoke before paid-v1. |
| Linux | `/dev/ttyUSB*`/`/dev/ttyACM*`; user may need `dialout`/udev access | PortAudio/sounddevice over ALSA/PulseAudio/PipeWire; PipeWire loopback, PulseAudio null sink, or ALSA `snd-aloop` | Supported target for technical users; paid-v1 should document PipeWire/PulseAudio topology and package native dependencies explicitly. |

## Minimum Viable Paid-v1 Support

This section defines the minimum viable paid-v1 support bar for each platform.

For paid-v1, the managed runtime should be considered supportable when these
paths are green:

| Platform | Minimum viable paid-v1 support |
|----------|--------------------------------|
| macOS | Pro bundle starts managed runtime, discovers LAN and USB serial candidates, reports structured startup/runtime status, and can route WSJT-X audio through LAN direct or a documented BlackHole/Loopback bridge. |
| Windows | Pro bundle starts managed runtime, discovers LAN and COM-port candidates, includes/loads `libopus` and PortAudio/sounddevice successfully, and documents VB-Cable/manual device selection for WSJT-X. |
| Linux | Pro package starts managed runtime, discovers LAN and USB serial candidates when permissions allow, includes/loads `libopus` and PortAudio/sounddevice successfully, and documents PipeWire/PulseAudio loopback setup. |

The core `rigplane --json discover` payload uses `schema: rigplane.discovery.v1`
and includes platform limitation hints for setup wizards. Discovery output must
not include credentials.

## Validation Commands

Core validation that does not require real hardware:

```bash
uv run pytest tests/test_discovery.py tests/test_cli_coverage.py tests/test_audio_pipeline_os_smoke.py -q --tb=short
```

Hardware or OS-assisted validation remains opt-in:

```bash
RIGPLANE_OS_AUDIO_SMOKE=1 uv run pytest tests/test_audio_pipeline_os_smoke.py -q --tb=short
RIGPLANE_HARDWARE_AUDIO=1 uv run pytest tests/hardware/test_ic7610_audio_pipeline.py -q --tb=short
```

## Follow-up issues

- rigplane-core#1464: validate Windows/Linux USB audio discovery smoke coverage.
- rigplane-pro#700: package managed runtime native audio dependencies by platform.
- rigplane-pro#671: harden cross-platform packaging and lifecycle for station runtime.
- rigplane-pro#521 and rigplane-pro#522: validate Windows and Linux release artifacts with virtual audio devices.
