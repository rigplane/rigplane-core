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
| `unsolicited` | array | `{t_ms, hex, text}` pushes seen outside any answer slot. |
| `snapshot` | object | `{before, after, changed}` profile state reads around the pass; `changed` lists the names that differ. |
| `checks` | array | Always empty here; reserved for write checks. |

One `commands` row:

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Profile command name (`get_freq`, `cmd29:get_preamp:1`, `unknown:7f`). |
| `template` | string | The profile-declared form (CAT read template, or CI-V body hex). |
| `request_hex` | string | Exact sent bytes, hex. |
| `request_text` | string | CAT line, or empty when the protocol has no text form. |
| `reply_hex` | string or null | Exact reply bytes, hex; null when nothing arrived. |
| `reply_text` | string or null | CAT reply line; null when nothing arrived or the row is not an answer. |
| `class` | string | One outcome class (below). |
| `latency_ms` | number | Time from send to settled answer. |
| `note` | string | Expansion provenance (for example a defaulted field domain). |

Outcome classes, shared by both protocols: `data` (the reply parses
as the answer), `refused` (`?;` / CI-V NAK `0xFA`), `ack_only` (bare
CI-V ACK `0xFB` with no data), `silent` (nothing arrived),
`malformed` (unattributable bytes), `unsolicited` (a push in the
answer slot), `transport_error` (the run ends). Raw bytes are always
kept; the class is a convenience, never the only record.

## Redaction

The file must not contain the port path, hostname, username, or IP
address. Port identity is reduced to `port_kind` (`"usb"`) and `baud`.

## Relation to other formats

The recorder JSONL is a byte-chunk log with no request/reply pairing;
fixture `*_pairs.jsonl` files hold trimmed pairs without silence rows.
The probe file is a paired, classified table: a converter can replay
its `data`/`refused` rows as pairs and its silences as requests with
no reply. Committing a customer capture as a fixture additionally
needs the existing redaction pass before it lands in the repo.
