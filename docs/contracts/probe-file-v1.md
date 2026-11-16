# Probe File Contract — `rigplane-probe-v1`

**Schema name:** `rigplane-probe-v1`
**Status:** stable, public, open-core contract.
**Last updated:** 2026-11-14

## Purpose

This contract documents the file `rigplane probe` writes: one JSON
document holding every read the run sent and the radio's exact reply to
each. It is the spec that `probe/model.py` (`ProbeFile.to_json`,
`ProbeFile.from_json`) builds against and that any third-party tool
reading a probe capture must implement.

## Versioning

`ProbeFile.from_json` accepts schema major version 1 only. A file whose
schema name carries any other major version is refused with an error;
unknown extra fields inside a version-1 file are ignored.

## Shape

Pretty-printed JSON, one command per block, `jq`-friendly. Top level:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema` | string | Always `rigplane-probe-v1`. |
| `tool` | object | `{rigplane_version, python, os, created_utc}` of the recorder. |
| `mode` | string | `"read"`. The `read+checks` mode is reserved for write checks and not produced here. |
| `radio` | object | `{profile, profile_sha256, protocol, port_kind, baud, identity_reported{raw, parsed}, identity_matches_profile}`. `profile_sha256` is the sha256 of the profile TOML bytes. |
| `private_reads_included` | boolean | Whether memory-content reads were sent. |
| `label` | string | The `--label` text, empty when not given. |
| `commands` | array | One row per sent read, in send order (below). |
| `skipped` | array | One row per declared read deliberately not sent, with a reason (below). |
| `unsolicited` | array | `{t_ms, hex, text}` pushes seen outside any answer slot. |
| `snapshot` | object | `{before, after, changed}` profile state reads around the pass; `changed` lists the names that differ. |
| `termination` | object | `{status, detail}` how the run ended (below). |
| `checks` | array | Always empty here; reserved for write checks. |

One `commands` row:

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Profile command name (`get_freq`, `cmd29:get_preamp:1`, `unknown:7f`). |
| `template` | string | The profile-declared form (CAT read template, or CI-V body hex). |
| `request_hex` | string | The read body the run sent, hex: the CAT line, or the CI-V frame body without address bytes or framing. |
| `request_text` | string | CAT line, or empty when the protocol has no text form. |
| `reply_hex` | string or null | Exact reply bytes, hex; null when nothing arrived. |
| `reply_text` | string or null | CAT reply line; null when nothing arrived or the row is not an answer. |
| `class` | string | One outcome class (below). |
| `latency_ms` | number | Time from send to settled answer. |
| `expected` | string | What the profile declares: `present`, or `absent` for a declared-absent read sent with borrowed bytes. |
| `note` | string | Expansion provenance (for example a defaulted field domain); borrowed reads name their lenders (`bytes from profile(s): X, Y`). |

Outcome classes, shared by both protocols: `data` (the reply parses
as the answer), `refused` (`?;` / CI-V NAK `0xFA`), `ack_only` (bare
CI-V ACK `0xFB` with no data), `silent` (nothing arrived),
`malformed` (unattributable bytes), `unsolicited` (a push in the
answer slot), `transport_error` (the run ends). Raw bytes are always
kept; the class is a convenience, never the only record.

One `skipped` row:

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Profile command name (`get_memory_contents`). |
| `request_hex` | string or null | Withheld bytes, hex; null when no single bytes exist. |
| `reason` | string | One skip reason (below). |
| `detail` | string | One short human sentence. |

Skip reasons, a closed enum the parser validates: `unsafe_family` (a
refused or action-shaped command family is never sent), `private`
(stored memory contents without `--include-private`), `response_code`
(the radio's own NAK/ACK reply bytes `0xFA`/`0xFB`, never requests),
`no_unambiguous_bytes` (a declared-absent read no sibling profile
lends unanimous bytes for), `run_aborted` (a read the run never
attempted because an earlier silence or transport break stopped it).
Every declared read of the profile is either asked or skipped;
nothing is in both, nothing is missing. A stopped run keeps that
promise: reads after the stop are listed as `run_aborted` with the
stop reason in `detail`.

Termination, an object the parser validates: `status` is one of
`completed` (every planned read was sent), `silence_stop` (five
unanswered reads in a row: anywhere while nothing answered yet,
otherwise inside the main pass), `transport_error` (the serial link
broke); `detail` is the plain-words reason shown to the customer
(the silence message, or the phase and error for a transport break).
Files written before this key existed parse as `completed`.

## Redaction

The file must not contain the port path, hostname, username, or IP
address. Port identity is reduced to `port_kind` (`"usb"`) and `baud`.

## Replaying a capture

A probe file converts to fixture `*_pairs.jsonl` rows with
`rigplane_testkit/probe_fixtures.py`:

```bash
python -m rigplane_testkit.probe_fixtures convert \
    --in capture.json --window probe --out-dir DIR [--radio-addr 0xNN]
```

The protocol comes from the file (`radio.protocol`); the CI-V radio
address defaults to the profile named in the file (`radio.profile`,
loaded from the shipped rigs) and `--radio-addr` overrides it. The
converter replaces that window's rows in `DIR/civ_pairs.jsonl` (or
`DIR/cat_pairs.jsonl` for CAT), with sorted keys and one trailing
newline per row, so the same input always converts byte-identically.
The rows mount as `RecordedAnswerTable.from_civ_pairs` /
`RecordedCatAnswerTable.from_cat_pairs` for the serial/CAT fakes.

Outcome to rows, true to the converter and pinned by its unit table:

| Outcome | Rows |
| --- | --- |
| `data` | `c2r` + `r2c` |
| `refused` | `c2r` + `r2c` |
| `ack_only` | `c2r` + `r2c` |
| `silent` | `c2r` alone |
| `malformed` | none |
| `unsolicited` | none |
| `transport_error` | none |

Skipped reads are never rows: only reads the run actually sent become
rows, one per distinct request (first occurrence wins). A silent read
replays as silence on both protocols: the converter emits the bare
request row, `from_cat_pairs` records an overtaken or trailing request
as silence, and `RecordedAnswerTable` records a CI-V request with no
reply row as silence (the serial fake then sends nothing; a request the
rows never saw still gets the NAK fallback).

## Relation to other formats

The recorder JSONL is a byte-chunk log with no request/reply pairing;
fixture `*_pairs.jsonl` files hold trimmed pairs; a request nothing answered
keeps its request row alone.
The probe file is a paired, classified table: a converter can replay
its `data`/`refused` rows as pairs and its silences as requests with
no reply. Committing a customer capture as a fixture additionally
needs the existing redaction pass before it lands in the repo.
