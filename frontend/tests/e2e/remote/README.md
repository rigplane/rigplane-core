# Remote/browser E2E harness (C5b PR-0, MOR-4493)

Playwright rows that prove the harness, both browsers and the twice rule
**before** any product PR depends on them. Every row runs in two projects
(`chromium`, `webkit`), each declared twice (`repeatEach: 2`), with no
retries, and the [no-skips reporter](./no-skips-reporter.ts) fails the
whole run when any row is skipped, `fixme`, or missing from the results.

## Rows

| Row | Behaviour |
|---|---|
| L1 | The ordinary UI page served by Core talks only to the station (`page.on('request')` shows no other origin). |
| L2 | Tune in the UI → the simulator's frequency follows; change the frequency on the station side → the UI follows. |
| L3 | Hold PTT in the phone layout → the simulator reports keyed; release → unkeyed within 1 s (read from the simulator, not the UI). **RED on main's fake** — the IC-7610 LAN fake's recorded answer table freezes MOD input at ACC (it does not model the `0x1A 05 00 0x91` MOD-input SET), so the browser voice-TX path never arms and the UI's accepted `ptt_on` never keys the simulator. A raw control-channel `ptt_on` keys the same fake in milliseconds, and the UI's own "Set LAN" fix is applied in-row but the fake cannot take it. Green once the fake models stateful MOD input (rigplane_testkit scope). |
| P0 | A bare page opens an `RTCPeerConnection` with the four data channels to Core's answerer (`POST /api/v1/transport/webrtc/offer`, offer relayed by the Node test) and the `control` channel delivers `control_hello`, validated against `payloads.control_hello` in the wire contract. The bare page is served on the station's origin with every request routed locally: `about:blank` is an insecure context in WebKit, where WebRTC then gathers nothing. |

The station is `tests/e2e/remote_web_server.py`: the real `WebServer` over
the IC-7610 LAN fake (`stateful_ptt=True, ptt_on_session_loss="hold"`),
with the production managed TX composition attached and WebRTC enabled.
Its test sidecar exposes the simulator's observable state — `GET /sim`
(`ptt_on`, `freq_hz`, PTT timeline) and `POST /sim/freq` — so "keyed" and
"frequency" are read from the simulator without any test hook in `src/`.

Fixtures are never hand-written: attach frames come from
`frontend/src/lib/transport/api-channel-golden.json` documents (the four
`attach_<label>` texts with `<SESSION-KEY>` substituted from the controller
API), and `control_hello` is validated with the production
`payloadMatchesContract` against `api-channel-wire.json`. The four channel
labels derive from the golden document set; the ordered/lossy split mirrors
the production factories in `src/rigplane/web/transport/webrtc.py` and is
pinned in-row by reading `ordered`/`maxRetransmits` back from the
negotiated channels.

The microphone stub (the only allowed one) replaces
`navigator.mediaDevices.getUserMedia` in `page.addInitScript` with an
oscillator → `MediaStreamDestination` stream; everything after
`getUserMedia` is the production path, in both browsers.

## Running (Mac mini only — never the laptop)

From the worktree root:

```bash
mini-test --sync-only
ssh moroz@192.168.55.152 'cd ~/validation/mini-test/core-c5b0-e2e/rigplane-core/frontend \
  && npx playwright test -c ./playwright.remote.config.ts'
```

First run in a fresh sync dir also needs, on the mini:

```bash
npm ci && npm run build   # real frontend/dist the rows load
uv sync --all-extras      # aiortc backs the P0 answerer
```

The unit test for the reporter (run on the mini; the root vitest config
only includes `src` test files, so the harness has its own config):

```bash
mini-test --vitest --config tests/e2e/remote/vitest.config.ts
```

The npm script `test:e2e:remote` (frontend) is the short form of the
Playwright invocation.
