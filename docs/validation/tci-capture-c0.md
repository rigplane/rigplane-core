# TCI capture C0: how to run a recording session

How to place the recording proxy between a real TCI client and a real TCI
server, what is recorded, and what goes into the repo. The full two-way
capture is evidence kept outside the repo; only client-to-server lines and
a short facts note go in.

## Sessions

- **C0-RX** (RX only, no PTT pressed): answers the connect, VFO/mode/split,
  audio-start and RX-header items.
- **C0-TX** (after the TX safety PR merges): one FT8 cycle at minimum power
  into a dummy load, owner present, with his yes for each run. Answers the
  `trx`, `TX_CHRONO` and TX-header items. Until then those items stay open.

## Run

1. Start the upstream TCI server (for example AetherSDR) on 127.0.0.1:50001.
2. Start the proxy from the repo root, writing outside the repo:
   `python scripts/tci_capture/proxy.py --listen 127.0.0.1:50002
   --upstream 127.0.0.1:50001 --out /tmp/tci-capture --label <client>`.
   The proxy binds loopback by default and warns on any other bind. It
   never originates commands and never alters frames.
3. Point the client at 127.0.0.1:50002 and run the per-client steps below.
4. Stop the proxy (Ctrl-C). Each session leaves one JSON-lines file with
   one record per frame: monotonic and wall timestamps, direction (`c2s`,
   `s2c`), opcode; text frames in full; binary frames as the decoded
   64-byte header fields plus payload length only. Audio payload bytes are
   never recorded. Recording stops past a 256 MiB per-session cap while the
   relay keeps running; the trailer says `truncated: true`.
5. Report: `python scripts/tci_capture/proxy.py --report <capture>.jsonl`.
   Items without the needed events stay `OPEN`.
6. Fixtures: `python scripts/tci_capture/proxy.py --extract-fixtures
   <capture>.jsonl --fixtures-out tests/fixtures/tci/<client>.tci`, then
   review the file by hand before committing. Never commit server output.

## Per-client steps

Connect, change band and mode from the client, switch split on and off,
set modulation, start audio, and record 2 minutes of RX. For C0-TX add one
FT8 cycle at minimum power into a dummy load with the owner present.

- WSJT-X official (>= 3.0.0)
- WSJT-X Improved
- JTDX (reported to crash on non-conforming servers: any deviation in the
  init burst matters, keep the full capture)
- OpsLog
- MSHV

## What is unknown

Client behaviour in the design doc is reported, not observed: no live TCI
session was captured while writing it. The coverage report marks the open
items (`protocol:`/`device:` strings, `vfo` echo timing, `audio_start`
argument count, RX `length`/`channels`/`count` semantics, TX exchange).
Capture C0 turns each reported fact into a recorded transcript.
