/** Public Core DOM contract for transmit-affecting controls (MOR-4837).
 * The public contract is the `data-rp-capability` DOM attribute, not a private
 * TypeScript import from this package. External consumers must ignore unknown
 * IDs. Read the attribute on a control or its nearest composite-control root.
 * IDs describe operations, independent of visible text, locale and skin.
 * A composite root covers its interactive descendants, including renderers.
 */
export const CAPABILITY_ATTRIBUTE = 'data-rp-capability';
// Button.data prefixes keys with `data-`.
export const CAPABILITY_DATA_KEY = 'rp-capability';

export const TRANSMIT_CAPABILITIES = {
  txKey: { id: 'tx-key', reason: 'Latch the transmitter on.' },
  txHold: { id: 'tx-hold', reason: 'Push/hold to talk, including gesture-based TX latching.' },
  tune: { id: 'tx-tune', reason: 'Start antenna tuning; a carrier may be transmitted.' },
  split: { id: 'tx-split', reason: 'Change the transmit VFO routing.' },
  vfoTransfer: { id: 'vfo-transfer', reason: 'Swap or copy VFO state, including the transmit VFO.' },
  ritXit: { id: 'tx-rit-xit', reason: 'Enable, adjust or clear RIT/XIT offsets, including the TX offset.' },
  powerOn: { id: 'power-on', reason: 'Turn radio power on.' },
  rfPower: { id: 'tx-rf-power', reason: 'Change RF transmit output power.' },
  atu: { id: 'tx-atu', reason: 'Change the antenna tuner state.' },
  vox: { id: 'tx-vox', reason: 'Enable voice-operated transmission.' },
  txSettings: { id: 'tx-settings', reason: 'Change TX modulation input, audio/gain, compressor, monitor, VOX or keyer settings.' },
  cwBreakIn: { id: 'cw-break-in', reason: 'Change CW break-in mode or delay; break-in can key TX.' },
  txTimeoutSave: { id: 'tx-timeout-save', reason: 'Apply or disable the managed transmit time-out limit.' },
  txMicPath: { id: 'tx-mic-path', reason: 'Switch the TX microphone capture/processing path for the next capture.' },
  txAntenna: { id: 'tx-antenna', reason: 'Select the transmit antenna port.' },
  repeater: { id: 'tx-repeater', reason: 'Change transmit tone, duplex shift or repeater offset.' },
} as const;

export type TransmitCapability =
  (typeof TRANSMIT_CAPABILITIES)[keyof typeof TRANSMIT_CAPABILITIES]['id'];

export const NEVER_GUARDED = {
  powerOff: { reason: 'Power-off must remain reachable to stop a transmitter, including its confirmation.' },
  cwAutoTune: { reason: 'CW AUTO TUNE corrects the RX frequency and does not key the transmitter.' },
  unkey: { reason: 'Unkey is never gated: emergency receive/release must remain reachable.' },
} as const;

/** Non-DOM action inventory, not permission to bypass a policy:
 * the Pro guard must handle these separately (it cannot disable a keystroke by DOM).
 * Preserve key-up, pointer release, cancellation and emergency unkey paths.
 * KeyboardHandler/default keymap have no PTT, carrier-tune or power shortcut;
 * their `tune` action is receive-frequency stepping. PTT keys are handled by
 * createPttHold on the focused control, outside the global shortcut map.
 */
export const NON_DOM_TX_PATHS = {
  cycle_data_mode: { reason: 'The d shortcut changes the DATA setting through the same handler as the marked DATA buttons.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'cycle_data_mode':" },
  ] },
  toggle_split: { reason: 'The s shortcut changes transmit VFO routing.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'toggle_split':" },
  ] },
  vfo_equalize: { reason: 'The = shortcut copies VFO state, including the transmit VFO.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'vfo_equalize':" },
  ] },
  vfo_swap: { reason: 'A configurable shortcut can swap the transmit VFO; not bound in the default keymap.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'vfo_swap':" },
  ] },
  toggle_rit: { reason: 'The r shortcut changes the RIT/XIT control family.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'toggle_rit':" },
  ] },
  toggle_xit: { reason: 'The x shortcut enables or disables the transmit offset.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'toggle_xit':" },
  ] },
  clear_rit_xit: { reason: 'Escape clears the shared offset; an unarmed wheel control lets Escape reach the global shortcut.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'clear_rit_xit':" },
    { file: 'components-v2/controls/value-control/wheel-control.ts', symbol: "if (event.key === 'Escape')" },
  ] },
  toggle_monitor: { reason: 'The k shortcut changes the same TX monitor setting as the marked control.', triggers: [
    { file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'toggle_monitor':" },
  ] },
  ptt_on: { reason: 'Focused Space/Enter or a pointer hold starts PTT; its later release must remain usable.', triggers: [
    { file: 'components-v2/wiring/managed-tx-gesture.ts', symbol: 'const isPttKey' },
    { file: 'components-v2/wiring/managed-tx-gesture.ts', symbol: 'commands.pttOn();' },
  ] },
  transmit_on: { reason: 'The managed double-tap gesture latches TX without another button click.', triggers: [
    { file: 'components-v2/wiring/managed-tx-gesture.ts', symbol: 'commands.transmitOn();' },
  ] },
  scalar_step: { reason: 'Armed wheel, renderer keys, pointer drags and reset gestures can change marked TX scalar values through the control lease.', triggers: [
    { file: 'components-v2/controls/value-control/wheel-control.ts', symbol: 'options.lease.wheel(' },
    { file: 'components-v2/controls/value-control/KnobRenderer.svelte', symbol: 'lease.key(' },
    { file: 'components-v2/controls/value-control/KnobRenderer.svelte', symbol: 'lease.pointer(' },
    { file: 'components-v2/controls/value-control/KnobRenderer.svelte', symbol: 'lease.reset();' },
  ] },
  power_on: { reason: 'The extension host API can request power-on confirmation; the confirm operation carries power-on.', triggers: [
    { file: 'lib/local-extensions/LocalExtensionsHost.svelte', symbol: 'setRadioPower: requestPower' },
  ] },
  extension_commands: { reason: 'Local extensions can send transmit-affecting commands through sendCommand or dispatchCommand and the typed intent facade, without a DOM control.', triggers: [
    { file: 'lib/local-extensions/host-api.ts', symbol: 'sendCommand(name, params) {' },
    { file: 'lib/local-extensions/host-api.ts', symbol: 'dispatchCommand(name, params) {' },
    { file: 'lib/local-extensions/host-api.ts', symbol: 'dispatchCommand: dispatchThroughIntentFacade' },
  ] },
} as const;
