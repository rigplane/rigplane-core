# Transmit capability markers

Core exposes `data-rp-capability` as a public DOM contract for integrations that
identify transmit-affecting controls. Read the attribute on the control or its
nearest marked ancestor; composite roots cover their interactive descendants.
The marker identifies an operation and does not implement a guard or grant
permission. Receivers must tolerate and ignore unknown IDs. Visible labels,
locales and skins are not identifiers. Core's private TypeScript module is not
an external package API.

The vocabulary is defined in
`frontend/src/lib/transmit-capabilities.ts: TRANSMIT_CAPABILITIES`:

| ID | Operation |
| --- | --- |
| `tx-key` | Latch TX on |
| `tx-hold` | Hold PTT / managed TX gesture |
| `tx-tune` | Start antenna tuning, potentially transmitting a carrier |
| `tx-split` | Change transmit VFO routing |
| `vfo-transfer` | Swap or copy VFO state |
| `tx-rit-xit` | Enable, adjust or clear RIT/XIT |
| `power-on` | Turn radio power on |
| `tx-rf-power` | Change RF output power |
| `tx-atu` | Change antenna tuner state |
| `tx-vox` | Enable voice-operated TX |
| `tx-settings` | Change TX input, audio, monitor, VOX or keyer settings |
| `cw-break-in` | Change CW break-in mode or delay |
| `tx-timeout-save` | Apply the managed TX time-out limit |
| `tx-mic-path` | Select the TX microphone processing path |
| `tx-antenna` | Select the transmit antenna port |
| `tx-repeater` | Change TX tone, duplex shift or repeater offset |

`frontend/src/lib/transmit-capabilities.ts: NEVER_GUARDED` records the exclusions:
unkey/emergency receive, latched release, power-off including its confirmation,
and receive-only CW AUTO TUNE. Guards must preserve these operations and any
in-progress PTT release, key-up or cancellation. The phone RF-power editor
opener is unmarked; its ValueControl carries the operation marker. The momentary hold
button remains marked during a hold; a separate unkey control remains available
(`frontend/src/semantic/RxTxSurface.svelte: pttButton`).

`frontend/src/lib/transmit-capabilities.ts: NON_DOM_TX_PATHS` records paths that
a DOM-only guard cannot cover: global split/VFO/RIT/XIT/monitor shortcuts,
Escape passing through an unarmed wheel control, focused Space/Enter PTT,
managed double-tap TX latching, scalar wheel/key/drag/reset callbacks and host
power requests. The Pro guard must handle these separately (it cannot disable
a keystroke by DOM). The default keymap has no PTT, carrier-tune or power
shortcut; its `tune` action steps receive frequency. See
`frontend/src/components-v2/layout/KeyboardHandler.svelte: dispatch`,
`frontend/src/lib/runtime/commands/panel-commands.ts: dispatchKeyboardRadioAction`
and `frontend/src/components-v2/wiring/managed-tx-gesture.ts: createPttHold`.

The keyboard source scan derives action aliases from the handlers shared with
marked controls (`frontend/src/lib/transmit-capabilities.ts: MARKED_CONTROL_HANDLERS`)
and checks default-keymap coverage in NON_DOM_TX_PATHS. The component contract
suite checks markers across layouts and editor controls; the StatusBar power
suite mounts the real LocalExtensionsHost and checks both power confirmations.
