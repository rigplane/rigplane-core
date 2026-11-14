# `probe` layer

## Charter

Read-only radio probing (`rigplane probe`): enumerate every read command a
rig profile declares, send each once over USB serial, and record the
radio's exact replies into a `rigplane-probe-v1` file. The package owns the
file contract (`model.py`), the per-protocol read enumeration and
allow-list (`readlist.py`), the outcome classes (`classify.py`), the paced
single-flight runner (`runner.py`), and the customer-facing text
(`render.py`). The CLI (`cli/_probe.py`) only wires arguments, prompts,
and the serial transport.

## Public API

`model.ProbeFile.to_json` / `from_json`, `readlist.build_read_list`,
`classify.classify_cat` / `classify_civ`, `runner.run_probe`,
`render.*_message` / `*_screen` / `*_summary` helpers.

## Allowed dependencies

`core`, `commands`, `profiles`, `backends` (transport open only, via the
CLI). Nothing imports `probe` except `cli`; test doubles in
`rigplane_testkit` may import its pure matchers.

## Forbidden patterns

- Any write to the radio: no transmit, tuner, memory-write, CW, or
  power on/off command is ever enumerated or sent, in any mode.
- Importing `rigplane_testkit`: the package stands on production types.
- Business logic in `render.py`: pure string functions only, pinned by
  exact-string tests.
