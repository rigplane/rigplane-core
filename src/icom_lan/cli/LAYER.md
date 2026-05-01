# `cli` layer

## Charter

Command-line entrypoints (`icom-lan` / `python -m icom_lan`). Wires CLI
arguments to the `backends.factory.create_radio` assembly path and the
`runtime.IcomRadio` API surface; provides one-shot status/control
subcommands plus `audio` and `discover` helpers. The CLI is a consumer:
no business logic lives here.

## Public API

`cli/__init__.py` exports:

- `main` — argparse-driven entry; dispatches subcommands (`status`,
  `freq`, `mode`, `power`, `meter`, `audio`, `att`, `preamp`, `ptt`,
  `antenna`, `date`, `time`, `levels`, `discover`).
- `check_ports_available` — pre-flight UDP port probe used by
  `web`/`rigctld` integration.

The `icom-lan` console script (`pyproject.toml` `[project.scripts]`)
points at `icom_lan.cli:main`. The `python -m icom_lan` entry uses
`icom_lan/__main__.py` which delegates to the same `main`.

## Allowed dependencies

`core`, `commands`, `profiles`, `audio`, `scope`, `runtime`, `backends`,
`web`, `rigctld` (plan §3 matrix row `cli`). The CLI sits at the top of
the layered stack and may consume any layer below it; nothing depends
on `cli`.

## Forbidden patterns

- Adding business logic the runtime should own. Argparse → factory →
  `IcomRadio` method call → format output. Anything richer belongs in
  `runtime` or `backends`.
- Direct backend instantiation. Use `_build_backend_config(args)` →
  `create_radio(config)` (issue #147; LightRAG memory note: tests must
  patch `icom_lan.cli.create_radio`, not `icom_lan.cli.IcomRadio`).
- Hardware-specific branches. Probe via the Capability Protocols on
  the resolved radio and surface graceful fallbacks.

## Common operations

- **Add a subcommand** → declare argparse subparser in `cli/__init__.py`,
  implement the async handler, register in the dispatch dict; cover
  with `tests/test_cli*.py`. Mock `icom_lan.cli.create_radio` to
  isolate from hardware.
- **Add a new backend flag** → extend `_build_backend_config(args)`;
  the `--backend lan|serial` discriminator + per-backend flags are the
  established pattern; `discover` is LAN-only.
- **Touch the audio CLI subcommand** → `audio caps` /
  `audio rx --out` / `audio tx --in` / `audio loopback` consume
  `audio.AudioStats` and `runtime.IcomRadio` audio APIs; keep the wire
  format stable.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §2.2, §3.
- `cli/__init__.py` module docstring — full subcommand inventory.
- `backends/factory.py` — `create_radio` is the only assembly seam.
- `tests/test_cli*.py`, `tests/test_yaesu_cli_factory.py`.
