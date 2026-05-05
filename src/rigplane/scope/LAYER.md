# `scope` layer

## Charter

Spectrum/waterfall frame assembly and rendering. Reconstructs complete
scope frames from multi-packet CI-V `0x27/0x00` sequences and renders
them as PNG/canvas-friendly bitmaps. Pure assembly + rendering — no I/O,
no radio interaction; consumers (web/runtime) feed raw payload bytes.

## Public API

`scope/__init__.py` exports:

- `ScopeFrame` — dataclass: `receiver`, `mode`, `start_freq_hz`,
  `end_freq_hz`, `pixels` (0–160 amplitude bytes), `out_of_range`.
- `ScopeAssembler` — feeds CI-V scope packets, emits complete
  `ScopeFrame` objects per receiver. Maintains independent state for
  main/sub on dual-RX rigs. Handles 5-second assembly timeout for
  partial frames.

`scope/render.py` exports rendering helpers (`render_scope_image`, …)
used by web's image endpoints.

## Allowed dependencies

`core` only (plan §3 matrix row `scope`). `scope` is a sibling of
`commands` and `dsp` in the layer matrix — independent of both, enforced
by the `independence-low` contract in `.importlinter`.

## Forbidden patterns

- `from icom_lan.audio` / `from icom_lan.runtime` / `from
  icom_lan.commands`. Sibling/upper-tier imports break the layer.
- `from icom_lan.dsp` — the bottom-tier independence contract bans it.
  PCM-derived spectra live in `audio/fft_scope.py`, which uses `scope`,
  not the reverse.
- Maintaining radio I/O state. The assembler is fed bytes; it does not
  know how they arrive.

## Common operations

- **Tweak assembly timeout** → `_DEFAULT_ASSEMBLY_TIMEOUT` in
  `scope/__init__.py`; tests under `tests/test_scope*.py` check
  partial-frame discard behaviour.
- **Add a render output format** → add a function in `scope/render.py`;
  verify pixel-encoding invariants against existing PNG goldens.
- **Track a new wire-format field in `0x27/0x00`** → extend `ScopeFrame`
  and the sequence-1 decode block in `_ReceiverState.feed`; cross-check
  against wfview's `parseSpectrum()` (icomcommander.cpp:1921).

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §2.2, §3.
- `audio/fft_scope.py` — PCM-derived panadapter (consumes `scope`).
- `tests/test_scope*.py` — frame-assembly coverage incl. timeout/partial
  cases.
- `.importlinter` `independence-low` contract — sibling enforcement.
