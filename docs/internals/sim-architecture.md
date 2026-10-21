# Universal radio simulator: architecture and extension contract (ADR, MOR-3592)

Status: draft for review, 2026-10-21. Epic MOR-3496. Boundary: open-core
candidate (Core testkit; dev-only, not in the published `rigplane` wheel).
Companion document: [`sim-serve-contract.md`](sim-serve-contract.md) (the
`serve` process interface, already implemented for the IC-7610).

## 1. Decision in one paragraph

There is one simulator, not one per radio. A single deterministic **engine**
holds the radio state; a protocol **model** per family (Icom CI-V, Yaesu CAT)
turns wire requests into engine operations and engine state into wire
replies; **adapters** own transport framing and session rules (Icom LAN UDP,
serial over `socket://` TCP and PTY, Yaesu CAT lines). What makes it a given
radio is data: the existing **capability profile** (`rigs/<radio>.toml`,
read-only) plus a new **simulation profile** (`rigplane-sim-profile/1`, below)
plus recorded fixtures. Adding a radio means adding files in that radio's own
folders, not editing shared code. Simulated results are always labelled
`simulated` with a fidelity tier and never count as a hardware PASS.

## 2. Context (what exists, what failed before)

Several independent fakes already exist (`MockIcomRadio` LAN simulator,
`CivSerialFake`, `FakeCatRadio`, `tests/serial_stub.py`,
`tests/fake_rigctld.py`), and the first five keep their own copy of
frequency/mode/PTT state; `tests/civ_fake.py` (`CivRadioFake`) only models
identity and records requests. Earlier attempts (MOR-2010, MOR-2492) were
marked Done in Linear while the older doubles remain in the tree, so this
design does not make retiring them a prerequisite. It is a strangler: each
step ships value on its own, old imports keep working (`tests/mock_server.py`,
`rigplane_testkit.icom.MockIcomRadio`), and no step requires retiring the old
doubles.

## 3. Module layout

New code lives under `rigplane_testkit/sim/`. Nothing below is shipped in the
wheel.

```
rigplane_testkit/sim/
  __main__.py, serve.py      CLI and the serve process (exists, MOR-3593)
  engine/                    state dataclasses, SimEngine, clock, seeded RNG, event queue, trace
  model/                     protocol families: icom_civ.py, yaesu_cat.py (decode -> engine op -> encode)
  profiles/                  loader + schema for rigplane-sim-profile/1; data files <radio>.toml
  adapters/                  icom_lan.py, icom_serial.py (socket:// and PTY), yaesu_cat.py
  audio/                     sources, sinks, backends, air-recording renderer (section 9)
  scenario/                  seeded event schedules and the shared fault vocabulary
  radios/<radio>/            OPTIONAL radio-specific python (only when data cannot express it)
tests/fixtures/<radio>/      recorded data of that radio (owned by that radio's agent)
tests/testkit/test_<radio>_*.py   that radio's conformance tests
```

The existing `rigplane_testkit/icom/`, `serial/`, `yaesu/` packages stay until
their content has moved behind `adapters/` and `model/`; they keep working
meanwhile.

### Dependency rules (enforced by an import-linter contract in a later PR)

The complete matrix of `rigplane` imports under `rigplane_testkit/sim/`
(a blank cell means forbidden):

| `sim/` area | `rigplane.profiles` (resolve a capability profile) | `rigplane.commands` (`parse_civ_frame`, `build_civ_frame`, `CONTROLLER_ADDR`) and `rigplane.core.types.CivFrame` |
| --- | --- | --- |
| `profiles/` (loader) | yes | |
| `serve.py` (gets resolved data from the loader) | | |
| `engine/` | | |
| `model/icom_civ.py`, `adapters/icom_*.py` | | yes |
| `model/yaesu_cat.py`, `adapters/yaesu_cat.py`, `audio/`, `scenario/` | | |

The loader (the only place that resolves a capability profile) hands the
engine and the models plain resolved data (the engine's `Capabilities`
dataclass, the model's command table); `engine/` imports no `rigplane` module
at all. Everything else under `rigplane` (drivers, runtime, state store, web,
rigctld, `CatCommandParser`, `format_command`) is forbidden. The Yaesu parser
and formatter must NOT generate the simulator's replies, because the same code
under test would then pass both sides. A new `rigplane` import under `sim/`
needs a change to this table first; section 11 refers to this table.
3. The simulator's replies come from the model's own codec table and, below
   it, from recorded answers. They are checked against hand-transcribed
   oracles (`tests/golden/wire_oracle/<radio>.toml`) and recordings.
4. Fixtures and profile data are found through explicit paths (constructor or
   `--fixtures`), never `Path(__file__).parents[...]`.
5. `rigplane_testkit.sim` never imports from `tests/`.

## 4. Engine contract (what radio code is written against)

The engine API is **draft until MOR-3599 merges**; the shape below is what the
radio agents should code to, names may get one rename pass in that PR.

```python
@dataclass
class ReceiverState:          # one per receiver (profile receiver_count)
    frequency_hz: int; mode: str; filter: int; data_mode: bool
    # named fields; names and allowed values come from the capability profile:
    levels: dict[str, int]      # ranged integers keyed by the capability profile's control names:
                                # "af_level", "rf_gain", "squelch", "nr_level", ... (no aliases)
    switches: dict[str, bool]   # controls whose declared domain is exactly two states: "nb", "nr", ...
    selectors: dict[str, int]   # controls with a declared set of more than two values:
                                # "preamp" (0/1/2), "agc" (1/2/3), "attenuator" (steps),
                                # "apf" (0..3 where the capability profile declares 0..3), ...
    # Classification rule: each control goes by the domain its capability profile
    # declares (two values -> switches, a value set -> selectors, a range -> levels);
    # the same control name can therefore be a switch on one radio and a selector on
    # another. Keys are the capability profile's control names (`attenuator`, not `att`).

@dataclass
class SimState:
    receivers: list[ReceiverState]
    selected_receiver: int; split: bool; ptt: bool; power_on: bool
    meters: dict[str, int]    # "s", "swr", "alc", "po", "vd" (read-only for the wire)
    extra: dict[str, object]  # radio-specific fields declared in the sim profile [state.extra]

class SimEngine:
    def __init__(self, profile: SimProfile, *, clock: Clock, seed: int) -> None: ...
    state: SimState                                   # read-only view
    def apply(self, op: Op) -> Outcome: ...           # SetFrequency, SetMode, SetLevel, SetSwitch, SetPtt, SelectReceiver, ...
    def read(self, name: str, receiver: int | None = None) -> Outcome: ...
    def schedule(self, at_s: float, event: Event) -> None: ...   # unsolicited changes, faults, power cycle
    def advance(self, dt_s: float) -> list[Event]: ...           # pure; no sleeping, no sockets
    trace: Sequence[Record]
```

`Outcome` is one of `Ok(value)`, `Unsupported` (the capability profile does not
declare the feature), `OutOfRange`, `Refused(reason)` (state forbids it, for
example TX while locked). A protocol model maps these to the radio's own
answer (NAK, `?;`, silence) as the simulation profile says. There is no
outcome that is implicit or silent.

Rules: the engine never owns a `StateStore` (a store asserts freshness about
real readings, and a simulator is not a real reading); same profile + seed +
event schedule gives the same trace; `t` is the only wall-clock field in any
output.

## 5. Per-radio extension contract

A radio is added by its owner with these files; shared files are listed in the
ownership table below and are not part of a radio's own set:

| File | Content | Required |
| --- | --- | --- |
| `rigplane_testkit/sim/profiles/<radio>.toml` | the simulation profile (section 6) | yes |
| `tests/fixtures/<radio>/*.jsonl` + `README.md` | recordings, provenance, trim rule; redaction gate clean | for tier A |
| `tests/golden/wire_oracle/<radio>.toml` | hand-transcribed manual rows (never generated from code) | yes (tier B+) |
| `tests/fixtures/<radio>/conformance_ledger.json` | divergences between simulator and recording; ratchet | for tier A |
| `tests/testkit/test_<radio>_*.py` | radio-specific tests (generic conformance is parametrized by radio) | as needed |
| `rigplane_testkit/sim/radios/<radio>/` | only for behaviour data cannot express (a quirky command); registers through `register(registry)` (below) | optional |

Shared files have exactly one owner each (a change by anyone else goes
through the owner's review, as a PR to that owner's file with the owner as
reviewer):

| Shared area | Owner |
| --- | --- |
| `sim/engine/`, `sim/profiles/` loader and schema, `sim/model/icom_civ.py`, `sim/model/registry.py`, `sim/adapters/icom_*.py`, `sim/serve.py`, `sim/scenario/`, `sim/audio/`, IC-7610 files | engine owner |
| `sim/model/yaesu_cat.py`, `sim/adapters/yaesu_cat.py`, `rigplane_testkit/yaesu/` (CAT recorder, read guard, CAT answer table) | FTX-1 agent |
| `rigplane_testkit/serial/recorder.py` and its `client_filter` hook | FTX-1 agent (PR #4283 introduces the hook); until #4283 merges no one else edits this file, the IC-7300 agent adopts the hook after the merge; later edits need the FTX-1 agent's review |
| `scripts/<radio>_capture/` | that radio's agent (not shared) |
| `tests/testkit/test_usb_radio_conformance.py` (generic conformance, parametrized by radio) | IC-7300 agent |

Extension registration: a radio's optional python package
`rigplane_testkit/sim/radios/<radio>/__init__.py` exposes the single signature
`register(registry: ModelRegistry) -> None`. The package name is the radio slug
(the capability model lowercased with non-alphanumerics removed: `IC-7300`
becomes `ic7300`), and one package serves every simulation profile of that radio
(`ic7300-usb` and any later `ic7300-*` profile). The engine owner's
`model/registry.py` imports that package for the profile's radio if it exists and
passes in a registry with `override(command_name, handler)` (replace the
model's answer for one named command) and `add_state(name, default)`.
`override` takes `handler(request) -> reply`: `request` carries the command
name and its decoded arguments (names from the capability profile's command
map, values already decoded by the model), `reply` is the model family's reply
value; the exact types are frozen in the model PR (MOR-3601 for Icom). An
`override` for a command the capability profile does not declare is an error at
registration. `add_state(name, default)` adds `name` to `SimState.extra`; a
name already declared in the profile's `[state.extra]` with the same default is
accepted, with a different default it is an error (the profile file wins
nowhere: the conflict is reported at load).
A radio PR that needs another shared hook opens or comments on a shared issue
first. A radio PR never edits the IC-7610 files or another radio's folders.
Refusal hooks are scenario events (`refuse`, section 7), not registrations. A
request with no native handler and no recording is `Unsupported` with
`source = "no_recording"`.

Order of precedence for a wire request, per radio: refusal hook, native model
handler (engine-backed), recorded answer table (`[answers]`), explicit
unsupported outcome. This is the rule the IC-7610 simulator already follows
(native data/ACK wins; a native NAK falls to the recording).

## 6. Simulation profile schema `rigplane-sim-profile/1`

TOML. The loader rejects unknown keys, a capability model that does not exist,
contradictory settings, and a fidelity tier without its evidence list. It
references the capability profile by model name and never copies its fields.

```toml
schema = "rigplane-sim-profile/1"

[sim]
id        = "ic7300-usb"
radio     = "IC-7300"            # capability profile model (rigs/ic7300.toml)
fidelity  = "A"                  # A recorded / B manual oracle only / C declarations only
evidence  = ["tests/fixtures/ic7300/civ_pairs.jsonl", "tests/golden/wire_oracle/ic7300.toml"]
gaps      = ["no write command recorded", "scope waves not recorded"]   # shown in banner and report

[transport.serial]               # or [transport.lan] / [transport.yaesu_cat]
baud      = 19200                # as recorded, not as the capability profile's default
echo      = false
# identity = "a4"               # Rule (one): if the capability profile declares
                                 # [identity].expected_ids, `identity` is optional and, when
                                 # present, must be one of them; if it declares none (the IC-705,
                                 # recorded 19 00 -> A4), `identity` is required and must come
                                 # from a recording or documentation, never be invented. A radio
                                 # with no documented id has no simulation profile until one is.

[initial]                        # starting state, all optional
frequency_hz = 14263000
mode = "USB"
filter = 2

[answers]                        # recorded answer tables mounted under the native handlers
recorded = [{ file = "tests/fixtures/ic7300/civ_pairs.jsonl", transport = "usb" }]

[timing]                         # used in real-time mode only; never asserted as a value
reply_latency_ms = 15

[unsolicited]                    # periodic or event-driven pushes the real radio makes
# transceive = false

[faults.defaults]                # radio-specific fault behaviour (hold times, reboot time)
# session_hold_s = 85.0

[state.extra]                    # radio-specific fields not in SimState (name = default)

[audio.rx]                       # native audio of this radio (section 9)
transport = "usb_device"         # lan_stream | usb_device
sample_rate_hz = 48000
channels = 1
sample_format = "s16le"

[scope]                          # native scope of this radio (section 9)
bins = 475
span_hz = 50000
rate_hz = 10
frame = "icom_27_00"             # framing family used by the model
```

Contradiction examples the loader refuses: `echo` set with a non-serial
transport, a `[scope]` section for a radio whose capability profile has no
scope, tier `A` without any recording in `evidence`, a `frequency_hz` outside
the capability profile's range. `[initial]` describes receiver 0 only in v1;
per-receiver initial state (`[initial.receiver1]`) is added with the first dual
receiver profile and extends the schema version if it changes existing keys.

## 7. Adapters and the process interface

Adapters own transport only. `icom_lan` is the extracted LAN part of
`MockIcomRadio` (login, token, ping, CI-V and audio ports, sequence-gap
recovery, fault plan). `icom_serial` and `yaesu_cat` expose real endpoints:
`socket://host:port` TCP (works on every OS where the app accepts a pyserial
URL) and a PTY slave path (macOS and Linux only). Windows serial simulation is
a known gap. Today `serve` accepts `--radio ic7610` only; the target is that
`--radio` takes any simulation profile id (MOR-3594 adds `ic7300` first). Its
startup JSON line, control endpoint and trace are the contract in
`sim-serve-contract.md`. The event vocabulary is shared across radios
(`set_frequency`, `session_drop`, `power_cycle`, `keepalive_silence`,
`lan_drop`, `usb_vanish`, `usb_reappear`, `silence`, `nak_next`, `refuse`, ...). `refuse`
`{command, count}` makes the radio answer the named capability-profile command with
its refusal outcome (NAK, `?;` or silence per profile) for the next `count`
occurrences; `nak_next` is the CI-V-only shorthand for `refuse` with the NAK
outcome. Argument shapes are frozen in the scenario PR (MOR-3608); an event
a radio cannot do returns `unsupported`, never silence.

## 8. Fidelity tiers and honesty

Every banner, trace and report carries `evidence: "simulated"` and the tier.
A = replay against a recording of this exact model and its divergence ledger
passes; B = manual-transcribed oracles only; C = profile declarations only.
A tier is per profile and carries its own `gaps`. A simulated run is never
recorded as a real-radio result; hardware claims stay in the validation matrix.
No RF behaviour is simulated: TX is accepted as state and captured as frames.
Identity bytes are never invented, and there is no "unverified identity" value in
the schema: when neither the capability profile nor a recording or a manual
documents them, the radio has no simulation profile until its owner supplies a
documented source (the rule in section 6).

## 9. Air recordings (MOR-3603): real signals as RX audio and scope

Goal: test runs measure what Pro does with real air (decode rate, AGC, scope
cadence), not with synthetic tones. Principle: the air is radio-independent, so
ONE **master recording** is rendered per radio by that radio's simulation
profile.

### 9.1 Format `rigplane-air/1` (a directory)

```
<name>/
  manifest.json     schema, id, kind (hf_master | vhf_uhf_fm_master | calibration), duration_s,
                    audio {sample_rate_hz, channels, sample_format, file, sha256},
                    scope {frames_file, bins, span_hz, center_hz, rate_hz, sha256},
                    state {frequency_hz, mode, filter, preamp, att, agc} at start,
                    time base: sample 0 == scope frame 0 == slot 0,
                    source {radio, firmware, path (usb|lan), date, operator note},
                    provenance {tool, commit, command}
  audio.wav         RX audio, native format of the source radio, lossless
  scope.jsonl       RAW radio scope frames as the radio emitted them on its own wire (Icom CI-V `27 00` frames from a
                    bridge or LAN recording; for other families the native equivalent), one per line:
                    {"t_samples": N, "frame": "<hex>"}; never the web API's re-framed scope payload
  truth.json        ground truth: for FT8/FT4 slots the reference decode list (jt9/WSJT-X output with slot index),
                    for CW the reference text, for SSB nothing required
```

All timing is expressed on one sample clock (`t_samples` at the audio rate), so
audio and scope cannot drift apart and the engine's deterministic clock can
replay them. `manifest.json` records how the alignment was obtained in
`align`: `sample_exact` (one capture process stamps both streams against the
same audio sample counter), `wall_clock_ms` (two processes, aligned by wall
clock, accuracy about a few ms to tens of ms) or `approximate`. A renderer may
use any of them; the divergence ledger states which one a calibration relied
on, and a `wall_clock_ms` recording is not used to prove scope-to-audio timing. FT8/FT4 recordings start on a slot boundary (15 s / 7.5 s) and
`truth.json` indexes decodes by slot.

### 9.2 Master sets and calibration clips

- **HF master** (recorded once, on the IC-7300 bench radio): at least 5
  slot-aligned minutes of FT8, plus CW and SSB segments, with scope frames and
  truth.
- **VHF/UHF FM master** (separate, 2 m and 70 cm, from the FTX-1 / IC-705; the
  IC-7300 covers 6 m but not 2 m or 70 cm).
- **Calibration clips** (1-2 minutes) from each other radio, recorded on the
  same air or a steady signal: used only to FIT the per-radio conversion and to
  prove it in the divergence ledger (level, passband, AGC, scope amplitude).

### 9.3 Rendering per radio

The simulation profile (`[audio.rx]`, `[scope]`, plus an `[air.render]` section
added with MOR-3603: gain offset dB, passband, AGC model parameters, scope
amplitude mapping) converts the master into what that radio delivers: sample
rate, bit depth and channels (for example the FTX-1 USB codec with RX on the
left channel only and a quiet level), passband and level, scope bin count, span,
rate and framing. Rendering is a pure function (master + profile + seed ->
frames), so a run is reproducible and a render can be diffed.

### 9.4 Storage and privacy

Recordings carry third-party callsigns, so audio files do NOT go into the
public Core repository. They live on private storage (the NAS) and tests
reference them by SHA-256 plus a size; a test that cannot find the file
skips with a clear reason locally and is required in the bench run. CI uses a
small public synthetic sample generated by code (tones, an FT8-like frame
built from a published encoder with a fictional callsign). `manifest.json`
may be public if it contains no callsign; `truth.json` is private with the
audio.

### 9.4a Capture notes

- Observed on the bench Mac mini (IC-705 agent, 2026-10-21): an audio capture
  job started through ssh or `osascript` recorded silence, while the same kind
  of job started with `open -a Terminal job.command` recorded audio; the likely
  cause is the macOS microphone permission, not verified. Start audio capture
  jobs that way there.
- Scope frames come from the radio-side bridge recording (the serial recorder
  or the LAN recorder), not from the web scope endpoint, so the stored frames
  are the radio's own bytes and the renderer can re-time them.
- Capture is RX only: no PTT, no TX, no setting write beyond what a read-only
  capture needs (restore what was touched); the radio is left as found.

### 9.5 Engine and adapter roles

The renderer is an `audio` source/`scope` source plugged into the adapters:
LAN radios stream it through the audio port (codec and rate from the profile),
USB radios expose it through a virtual audio device (spike MOR-3604) while the
CI-V side keeps answering from the engine. TX audio from the client is
captured to a WAV file for inspection; no TX is claimed as recorded.

## 10. Determinism and the clock

The engine has an injectable clock. In deterministic mode `advance(dt)` is the
only way time passes and the same profile + seed + schedule yields the same
trace. Real UDP/TCP scheduling in adapters is not globally deterministic and
is not claimed to be. Replay of air recordings uses the sample clock.

## 11. Packaging and extraction

`rigplane_testkit` stays dev-only. A separately installable CLI would need a
packaging decision (own distribution name, pinned `rigplane` extra for the
profile loader, fixtures shipped separately) that is out of scope here.
Criteria to extract later: no import from `tests/` or repo-relative paths, the
the only `rigplane` dependencies are the matrix in section 3 (profile
resolver, CI-V frame helpers), all fixtures addressed by explicit paths. Licence and provenance of `rigs/*.toml` (some cite wfview
`.rig` sources) and of recordings must be assessed before any publication; no
publication is authorised by this document.

## 12. Migration and ownership

| Step | Owner |
| --- | --- |
| ADR (this), engine skeleton (MOR-3599), profile schema + loader (MOR-3600), IcomCivModel extraction (MOR-3601), serial endpoints + `serve` for any profile (MOR-3594), scenario/fault vocabulary (MOR-3608), air format and renderer (MOR-3603) | engine owner |
| IC-7300 captures, fixtures, conformance (including the generic USB conformance test), simulation profile, HF master air recording | IC-7300 radio agent |
| FTX-1 CAT recorder and read guard, capture, CAT answer table, conformance, Yaesu model and adapter on the engine (MOR-3602), simulation profile | FTX-1 radio agent (the Yaesu files in section 5's table are theirs) |
| IC-705 capture (USB and LAN), personality (MOR-3605), simulation profile | IC-705 radio agent |

Until the engine API merges, radio agents work on what needs no engine:
captures, recorders, fixtures, oracle rows, conformance of the existing fakes,
and a first draft of their simulation profile against section 6. After it
merges they port their personality to the engine.

## 13. Open questions

1. Does the installed Pro app accept a `socket://` serial URL by hand? (PTY
   works on macOS and Linux; Windows serial simulation stays unavailable until
   this is tested.)
2. Exact `[air.render]` parameter set: fitted from the first calibration clips,
   then frozen in the MOR-3603 PR.
3. Whether the Wi-Fi/LAN IC-705 audio codec matches the profile's mono policy
   (needs the Wi-Fi capture).
