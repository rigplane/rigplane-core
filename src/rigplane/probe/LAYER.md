# `probe` layer

## Charter

Radio probing (`rigplane probe`): enumerate every read command a rig
profile declares — listed reads and declared-absent reads alike — send
each once over USB serial, and record the radio's exact replies into a
`rigplane-probe-v1` file. Reads deliberately not sent are recorded as
`skipped` with a closed-enum reason. With `--checks`, after the read
pass, the profile's optional write checks run: each changes settings for
a moment and puts them back, only after the person says yes. The package
owns the file contract (`model.py`), the per-protocol read enumeration
and allow-list (`readlist.py`), the outcome classes (`classify.py`), the
paced single-flight runner (`runner.py`), the write-check engine
(`checks.py`) and its journal (`journal.py`), and the customer-facing
text (`render.py`). The CLI (`cli/_probe.py`) only wires arguments,
prompts, and the serial transport.

## Public API

`model.ProbeFile.to_json` / `from_json`, `readlist.build_read_plan`,
`classify.classify_cat` / `classify_civ`, `runner.run_probe` (its
`checks=` takes a `checks.ChecksRequest`), `journal.journal_path` /
`leftover_journal_line`, `render.*` pure string helpers.

## Allowed dependencies

`core`, `commands`, `profiles`, `backends` (transport open only, via the
CLI). Nothing imports `probe` except `cli`; test doubles in
`rigplane_testkit` may import its pure matchers.

## Forbidden patterns

- A write to the radio outside `checks.py`: the read pass sends reads
  only, and every check frame goes through the engine's single send path
  (`checks._send`), built and judged by `profiles.probe_checks.judged_frame`
  right before the port. No transmit, tuner, memory-channel write, CW or
  power on/off command is in that table, so none is ever sent.
- A check write before the person's yes, or a yes taken from `--yes`.
- Importing `rigplane_testkit`: the package stands on production types.
- Business logic in `render.py`: pure string functions only, pinned by
  exact-string tests.
