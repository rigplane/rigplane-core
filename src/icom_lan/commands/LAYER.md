# `commands` layer

## Charter

CI-V command builders, parsers, and the high-level commander queue.
Encodes/decodes wire bytes (`FE FE <to> <from> <cmd> [<sub>] [<data>...]
FD`); does not own state, does not talk to a transport. Every function
takes bytes/ints and returns bytes/ints — `commands` is reusable in any
context that needs to build or parse a CI-V frame.

## Public API

`commands/__init__.py` re-exports the full builder/parser surface as a
single import target. Highlights:

- **Frame kernel** — `build_civ_frame`, `build_cmd29_frame`,
  `parse_civ_frame`, `parse_ack_nak`, `CONTROLLER_ADDR`,
  `RECEIVER_MAIN`, `RECEIVER_SUB`.
- **Builders / parsers** by domain — `freq.py`, `mode.py`, `levels.py`,
  `meters.py`, `ptt.py`, `vfo.py`, `dsp.py`, `scope.py`, `cw.py`,
  `power.py`, `system.py`, `tone.py`, `memory.py`, `antenna.py`,
  `tx_band.py`, `config.py`, `speech.py`.
- **Commander** — `IcomCommander`, `Priority` (queue + send).
- **Routing** — `CommandMap`, `CommandSpec` (rig-specific overrides).

`__all__` enumerates ~250 names. Other layers import through this front
door (`from icom_lan.commands import set_freq`) — direct sub-module
imports are an internal detail.

## Allowed dependencies

`core` only (plan §3 matrix row `commands`). No `runtime`, no `audio`,
no transport. Builders are pure; the commander queue's coupling to
transport happens in the `runtime` layer that wires it up.

## Forbidden patterns

- `from icom_lan.runtime` — would make builders depend on the commander
  caller; instead, the runtime calls `commands.*`.
- Module-level state (no caches, no registries that mutate at import).
  `CommandMap` is a runtime container constructed by `profiles`.
- Hardcoding rig-specific bytes inside a builder. Rig differences live
  in `commands/command_map.py` overrides applied via `CommandMap`.
- I/O. No sockets, no file reads, no `await`. Everything is synchronous
  byte assembly.

## Common operations

- **Add a CI-V command** → add a builder in the appropriate domain
  module (`freq.py`, `levels.py`, …); add a parser if the radio
  responds with bytes; export both from `__init__.py`'s `__all__`;
  register the wire bytes in `command_map.py` if rig overrides exist.
  Then add a runtime method in `runtime/radio.py` that calls the
  builder via the commander.
- **Override wire bytes for a rig** → extend the rig's TOML
  `[commands]` table; loader maps to a `CommandSpec` injected into the
  per-radio `CommandMap`.
- **Add a `Priority` level** → extend the `Priority` enum in
  `commander.py`; verify ordering invariants in the queue tests.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §2.2, §3.
- `commands/_frame.py` — the CI-V kernel; do not duplicate framing.
- `commands/command_map.py` + `commands/command_spec.py` — TOML-driven
  per-rig overrides.
- `tests/test_commands*.py` — builder/parser roundtrip coverage.
