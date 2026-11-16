# G7250 probe capture

The bench Xiegu G7250 answering `rigplane probe G7250` over USB serial
at 115200 baud with default flags, recorded 2026-11-14: 46 reads sent
(33 data, 11 refused, 2 silent), 8 declared reads skipped. The file
predates the `termination` key, so it also pins that old files parse
as `completed`.

It holds only the bench radio's own settings: profile-state answers
(frequency, mode, level readings), the refusal/silence facts of its
command set, and the radio's identity answer. No port path, host name,
user name, or IP address, and the capture label is empty. Customer
captures never go into this repository: this sample comes from our own
bench radio. The redaction gate
(`python -m rigplane_testkit.redaction_gate`) is clean over this file.

## Files

- `probe_usb_20261114.json` — the `rigplane-probe-v1` capture, produced
  by `rigplane probe G7250 --port <usb-serial> --yes --out
  probe_usb_20261114.json` against the bench radio. Never edited by
  hand: it is the replay source of truth.
- `civ_pairs.jsonl` — not stored here; derived on demand (see below).

## Regenerating rows

```bash
python -m rigplane_testkit.probe_fixtures convert \
    --in tests/fixtures/g7250/probe_usb_20261114.json \
    --window probe --out-dir tests/fixtures/g7250
```

This merges the `probe` window into `civ_pairs.jsonl`, replacing that
window's rows. The converter is pinned by
`tests/testkit/test_probe_fixtures.py`, including the round trip that
replays this exact capture through the real `rigplane probe` CLI and
compares commands, classes, and skip reasons.
