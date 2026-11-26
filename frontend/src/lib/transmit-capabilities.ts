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
