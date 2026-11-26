/** Public Core DOM contract for transmit-affecting controls (MOR-4837).
 * Source: frontend/src/lib/transmit-capabilities.ts. Consumers read
 * `data-rp-capability` on the control (or its nearest composite-control root).
 * IDs describe operations, independent of visible text, locale and skin.
 * A composite root covers its interactive descendants, including renderers.
 */
export const TRANSMIT_CAPABILITIES = {
  txKey: { id: 'tx-key', reason: 'Latch the transmitter on.' },
  txHold: { id: 'tx-hold', reason: 'Push/hold to talk, including gesture-based TX latching.' },
  tune: { id: 'tune', reason: 'Start antenna tuning; a carrier may be transmitted.' },
  split: { id: 'split', reason: 'Change the transmit VFO routing.' },
  vfoTransfer: { id: 'vfo-transfer', reason: 'Swap or copy VFO state, including the transmit VFO.' },
  ritXit: { id: 'rit-xit', reason: 'Enable, adjust or clear RIT/XIT offsets, including the TX offset.' },
  power: { id: 'power', reason: 'Turn radio power on or off.' },
  rfPower: { id: 'rf-power', reason: 'Change RF transmit output power.' },
  atu: { id: 'atu', reason: 'Change the antenna tuner state.' },
  vox: { id: 'vox', reason: 'Enable voice-operated transmission.' },
  txSettings: { id: 'tx-settings', reason: 'Change TX modulation input, audio/gain, compressor, monitor, VOX or keyer settings.' },
  cwBreakIn: { id: 'cw-break-in', reason: 'Change CW break-in mode or delay; break-in can key TX.' },
  cwAutoTune: { id: 'cw-auto-tune', reason: 'CW AUTO TUNE / RX frequency correction (legacy TUNE label guard).' },
  txAntenna: { id: 'tx-antenna', reason: 'Select the transmit antenna port.' },
  repeater: { id: 'repeater', reason: 'Change transmit tone, duplex shift or repeater offset.' },
} as const;

export type TransmitCapability =
  (typeof TRANSMIT_CAPABILITIES)[keyof typeof TRANSMIT_CAPABILITIES]['id'];

export const NEVER_GUARDED = {
  unkey: { reason: 'Unkey is never gated: emergency receive/release must remain reachable.' },
} as const;
