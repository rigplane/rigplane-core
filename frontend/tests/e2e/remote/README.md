# Remote/browser E2E harness (C5b PR-0, MOR-4493)

Playwright rows that prove the harness, both browsers and the twice rule
**before** any product PR depends on them. Every row runs in two projects
(`chromium`, `webkit`), each declared twice (`repeatEach: 2`), with no
retries, and the [no-skips reporter](./no-skips-reporter.ts) fails the
whole run when any row is skipped, `fixme`, or missing from the results.

## Rows

| Row | Behaviour |
|---|---|
| L1 | The ordinary UI page served by Core talks only to the station: every HTTP request (`page.on('request')`) and every WebSocket (`page.on('websocket')`, ws/wss compared as the http/https station origin) stays on the station origin, and at least one WebSocket is seen. |
| L2 | Tune in the UI → the simulator's frequency follows; change the frequency on the station side → the UI follows. |
| L3 | Hold PTT in the phone layout → the simulator reports keyed; release → unkeyed within 1 s (read from the simulator's own PTT journal, not the UI; the helper requires the final state OFF and an OFF at or after this release). The row also pins MOR-4312: the page's `getUserMedia` request has `echoCancellation`, `noiseSuppression` and `autoGainControl` all `false` (the stub records the constraints), and the simulator's own `tx_audio_signal_frames` counter rises, i.e. the captured tone reached the radio through the real WebSocket and Core. The microphone stub honours the page's audio constraints (rate, channel count) so the page's Opus encoder accepts it; the stub is defined on the `MediaDevices` prototype because WebKit may recreate the `navigator.mediaDevices` wrapper and drop an instance property. |
| L4 | Desktop layout (MOR-3514): with RX audio live, click the desktop PTT key (on a local-mode station it is the latching "Key transmitter" button) → the simulator reports keyed and the stubbed tone reaches it; no receive buffer is scheduled for playback while keyed; click UNKEY → the simulator's journal shows OFF within 1 s and exactly one ON then one OFF for this cycle, and decoded receive buffers reach the page's `AudioContext` again (counted at `AudioBufferSourceNode.start`). |
| L5 | Desktop layout (MOR-3514): the microphone stub refuses (`NotAllowedError`) and the operator clicks PTT with RX audio live → no managed-transmit command POST and no TX `audio_start` leave the page (the traffic witness, installed before navigation, is pinned by positive controls: L5 sees its own `rx` start, L4 its `tx` start and transmit POST), the simulator journal is unchanged, the station's managed TX stays in receive with PTT observed off; the error toast names the cause and the status bar's audio indicator reads failed, still after the toast is dismissed; and the support bundle of a diagnostics preview (read through the production `previewBundle` / `saveBundle` client) carries one more `browser microphone capture rejected: category=NotAllowedError` log line than before the click. The station logs into a fresh `RIGPLANE_LOG_DIR` per run (`playwright.remote.config.ts`). |
| P0 | A bare page opens an `RTCPeerConnection` with the six data channels of the K5 set (`control`, `api`, `scope`, `scope-text`, `audio`, `audio-text`) to Core's answerer (`POST /api/v1/transport/webrtc/offer`, offer relayed by the Node test) and the `control` channel delivers `control_hello`, validated against `payloads.control_hello` in the wire contract. The bare page is served on the station's origin with every request routed locally (`**/*`; the row asserts the fulfilled document, status 200): `about:blank` is an insecure context in WebKit, where WebRTC then gathers nothing. |

The station is `tests/e2e/remote_web_server.py`: the real `WebServer` over
the IC-7610 LAN fake (`stateful_ptt=True, ptt_on_session_loss="hold"`),
with the production managed TX composition attached and WebRTC enabled.
Its test sidecar exposes the simulator's observable state — `GET /sim`
(`ptt_on`, `freq_hz`, `ptt_transitions` taken from the simulator's own
`ptt_transitions` journal) and `POST /sim/freq` — so "keyed" and
"frequency" are read from the simulator without any test hook in `src/`.
`/sim` is the frozen dataclass `SimSnapshot`; `GET /sim/contract` serves its
field annotations and `station.ts` throws if the sidecar and its parser
disagree (`tests/testkit/test_remote_web_server_sidecar.py` pins both sides).
The required matrix (projects x rows x `repeatEach`) lives in
`REQUIRED_MATRIX` in the reporter, which checks it independently of
`--project`, `--grep` and `--repeat-each`.

Fixtures are never hand-written: attach frames come from
`frontend/src/lib/transport/api-channel-golden.json` documents (the four
`attach_<kind>` texts, each sent once on its kind's text-carrying channel, with `<SESSION-KEY>` substituted from the controller
API), and `control_hello` is validated against `api-channel-wire.json` by the
probe's own `payloadMatchesWireContract`, which mirrors the production
`payloadMatchesContract` (that module cannot be imported by Playwright's
Node runtime without a `src/` change). The six channel
labels, their ordering/reliability and which frames each carries come from
`data_channels` in `api-channel-wire.json` (producer:
`rigplane.web.channel_attach.DATA_CHANNELS`) and are pinned in-row by reading
`ordered`/`maxRetransmits` back from the negotiated channels.

The microphone stub (the only allowed one) replaces
`navigator.mediaDevices.getUserMedia` in `page.addInitScript` with an
oscillator → `MediaStreamDestination` stream; everything after
`getUserMedia` is the production path, in both browsers. Its `refuse` mode
(L5) rejects with a `NotAllowedError` DOMException instead.

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

The unit tests of the harness rules (reporter matrix, unkey latency, origin
predicate, attach acks, producer-contract parsers) (run on the mini; the root vitest config
only includes `src` test files, so the harness has its own config):

```bash
mini-test --vitest --config tests/e2e/remote/vitest.config.ts   # harness.test.ts
```

The npm script `test:e2e:remote` (frontend) is the short form of the
Playwright invocation.
