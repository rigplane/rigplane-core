import { acknowledgeCommand, beginCommand, cancelPendingCommands, failCommand, type CommandLifecycle } from '$lib/stores/commands.svelte';
import { makeCommandId } from '$lib/types/protocol';
import { getControlSession, onCommandDelivery, onControlSessionTransition, sendCommand } from '$lib/transport/ws-client';

type Empty = Record<string, never>;
type Receiver = 0 | 1;
const empty = ['cw_auto_tune', 'memory_write', 'quick_dualwatch', 'quick_split', 'scan_stop', 'vfo_equalize', 'vfo_swap'] as const;
const channel = ['memory_clear', 'memory_to_vfo', 'set_memory_mode'] as const;
const on = [
  'set_antenna_1', 'set_antenna_2', 'set_compressor', 'set_dial_lock', 'set_dual_watch',
  'set_main_sub_tracking', 'set_monitor', 'set_powerstat', 'set_rit_status', 'set_rit_tx_status',
  'set_rx_antenna_ant1', 'set_rx_antenna_ant2', 'set_scope_during_tx', 'set_scope_hold', 'set_split', 'set_vox',
] as const;
const onReceiver = ['set_auto_notch', 'set_digisel', 'set_ip_plus', 'set_manual_notch', 'set_nb', 'set_nr', 'set_twin_peak'] as const;
const level = [
  'set_anti_vox_gain', 'set_break_in_delay', 'set_compressor_level', 'set_drive_gain', 'set_mic_gain',
  'set_monitor_gain', 'set_nb_depth', 'set_nb_width', 'set_rf_power', 'set_vox_delay', 'set_vox_gain',
] as const;
const levelReceiver = ['set_af_level', 'set_nb_level', 'set_nr_level', 'set_preamp', 'set_rf_gain', 'set_squelch'] as const;
const value = ['set_cw_pitch', 'set_tuner_status'] as const;
const valueReceiver = ['set_agc_time_constant', 'set_manual_notch_width', 'set_notch_filter', 'set_pbt_inner', 'set_pbt_outer'] as const;
const modeReceiver = ['set_agc', 'set_apf', 'set_data_mode'] as const;
const modSource = ['set_data_off_mod_input', 'set_data1_mod_input', 'set_data2_mod_input', 'set_data3_mod_input'] as const;
const numericMode = ['set_break_in', 'scan_set_resume', 'set_scope_mode', 'speak'] as const;
const numericSpan = ['scan_set_df_span', 'set_scope_span'] as const;
const numericSpeed = ['set_key_speed', 'set_scope_speed'] as const;
const numericType = ['scan_start', 'set_keyer_type'] as const;
const custom = [
  ['set_attenuator', 'db:n,receiver:r'], ['set_band', 'band:n'], ['set_dash_ratio', 'ratio:n'],
  ['set_filter', 'filter:n,receiver?:r'], ['set_filter_shape', 'shape:n,receiver:r'],
  ['set_filter_width', 'width:n,receiver?:r'], ['set_freq', 'freq:n,receiver?:r'],
  ['set_if_shift', 'offset:n,receiver:r'], ['set_mode', 'mode:s,filter?:n,receiver?:r'],
  ['set_rit_frequency', 'freq:n'], ['set_scope_center_type', 'center_type:n'],
  ['set_scope_dual', 'dual:b'], ['set_scope_edge', 'edge:n'], ['set_scope_rbw', 'rbw:n'],
  ['set_scope_ref', 'ref:n'], ['set_scope_vbw', 'narrow:b'], ['switch_scope_receiver', 'receiver:r'],
  ['set_vfo', 'vfo:v'],
] as const;
const customNames = custom.map(([name]) => name) as Array<(typeof custom)[number][0]>;
const standardRules: ReadonlyArray<readonly [readonly string[], string]> = [
  [empty, ''], [channel, 'channel:n'], [on, 'on:b'], [onReceiver, 'on:b,receiver:r'],
  [level, 'level:n'], [levelReceiver, 'level:n,receiver:r'], [value, 'value:n'],
  [valueReceiver, 'value:n,receiver:r'], [modeReceiver, 'mode:n,receiver:r'],
  [modSource, 'source:n'], [numericMode, 'mode:n'], [numericSpan, 'span:n'],
  [numericSpeed, 'speed:n'], [numericType, 'type:n'],
];

type Member<T extends readonly string[]> = T[number];
export const RADIO_INTENT_NAMES = Object.freeze([
  ...empty, ...channel, ...on, ...onReceiver, ...level, ...levelReceiver, ...value, ...valueReceiver,
  ...modeReceiver, ...modSource, ...numericMode, ...numericSpan, ...numericSpeed, ...numericType, ...customNames,
] as const);
export type RadioIntentName = (typeof RADIO_INTENT_NAMES)[number];
type RadioIntentParams<Name extends RadioIntentName> =
  Name extends Member<typeof empty> ? Empty :
  Name extends Member<typeof channel> ? { channel: number } :
  Name extends Member<typeof on> ? { on: boolean } :
  Name extends Member<typeof onReceiver> ? { on: boolean; receiver: Receiver } :
  Name extends Member<typeof level> ? { level: number } :
  Name extends Member<typeof levelReceiver> ? { level: number; receiver: Receiver } :
  Name extends Member<typeof value> ? { value: number } :
  Name extends Member<typeof valueReceiver> ? { value: number; receiver: Receiver } :
  Name extends Member<typeof modeReceiver> ? { mode: number; receiver: Receiver } :
  Name extends Member<typeof modSource> ? { source: number } :
  Name extends Member<typeof numericMode> ? { mode: number } :
  Name extends Member<typeof numericSpan> ? { span: number } :
  Name extends Member<typeof numericSpeed> ? { speed: number } :
  Name extends Member<typeof numericType> ? { type: number } :
  Name extends 'set_attenuator' ? { db: number; receiver: Receiver } :
  Name extends 'set_band' ? { band: number } :
  Name extends 'set_dash_ratio' ? { ratio: number } :
  Name extends 'set_filter' ? { filter: number; receiver?: Receiver } :
  Name extends 'set_filter_shape' ? { shape: number; receiver: Receiver } :
  Name extends 'set_filter_width' ? { width: number; receiver?: Receiver } :
  Name extends 'set_freq' ? { freq: number; receiver?: Receiver } :
  Name extends 'set_if_shift' ? { offset: number; receiver: Receiver } :
  Name extends 'set_mode' ? { mode: string; filter?: number; receiver?: Receiver } :
  Name extends 'set_rit_frequency' ? { freq: number } :
  Name extends 'set_scope_center_type' ? { center_type: number } :
  Name extends 'set_scope_dual' ? { dual: boolean } :
  Name extends 'set_scope_edge' ? { edge: number } :
  Name extends 'set_scope_rbw' ? { rbw: number } :
  Name extends 'set_scope_ref' ? { ref: number } :
  Name extends 'set_scope_vbw' ? { narrow: boolean } :
  Name extends 'set_vfo' ? { vfo: 'A' | 'B' | 'MAIN' | 'SUB' } :
  Name extends 'switch_scope_receiver' ? { receiver: Receiver } : never;
export type RadioIntent = { [Name in RadioIntentName]: { name: Name; params: RadioIntentParams<Name>; id?: string } }[RadioIntentName];
const radioIntentNames = new Set<string>(RADIO_INTENT_NAMES);

function matchesParams(name: string, params: Record<string, unknown>): boolean {
  const rule = standardRules.find(([names]) => names.includes(name))?.[1]
    ?? custom.find(([candidate]) => candidate === name)?.[1];
  if (rule === undefined) return false;
  const fields = rule === '' ? [] : rule.split(',');
  const allowed = fields.map((field) => field.split(':')[0].replace('?', ''));
  if (Object.keys(params).some((key) => !allowed.includes(key))) return false;
  return fields.every((field) => {
    const [rawKey, kind] = field.split(':');
    const key = rawKey.replace('?', '');
    if (!Object.prototype.hasOwnProperty.call(params, key)) return rawKey.endsWith('?');
    const value = params[key];
    return kind === 'n' ? typeof value === 'number' && Number.isFinite(value)
      : kind === 'b' ? typeof value === 'boolean'
        : kind === 'r' ? value === 0 || value === 1
          : kind === 'v' ? value === 'A' || value === 'B' || value === 'MAIN' || value === 'SUB'
            : typeof value === 'string' && value.length > 0;
  });
}

onCommandDelivery((event) => {
  if (event.kind === 'transport-sent') return;
  if (event.cancelled) cancelPendingCommands(event.originalEpoch, event.error);
  else if (event.kind === 'ack' || event.kind === 'response-ok') acknowledgeCommand(event.commandId, event.originalEpoch, event.eventEpoch);
  else failCommand(event.commandId, event.originalEpoch, event.eventEpoch, event.error);
});
onControlSessionTransition((transition) => {
  if (transition.state === 'disconnected') cancelPendingCommands(transition.epoch);
});

export function dispatchRadioIntent(intent: RadioIntent): CommandLifecycle {
  const candidate = intent as { name?: unknown; params?: unknown; id?: unknown };
  if (typeof candidate.name !== 'string' || !radioIntentNames.has(candidate.name)) {
    throw new TypeError('Only a known non-PTT radio intent may be dispatched');
  }
  if (typeof candidate.params !== 'object' || candidate.params === null || Array.isArray(candidate.params)) {
    throw new TypeError('Radio intent params must be an object');
  }
  if ((candidate.id !== undefined && (typeof candidate.id !== 'string' || candidate.id.length === 0))
    || !matchesParams(candidate.name, candidate.params as Record<string, unknown>)) {
    throw new TypeError('Invalid radio intent envelope');
  }
  const id = candidate.id ?? makeCommandId();
  const originalEpoch = getControlSession().epoch;
  const lifecycle = beginCommand({ id, name: candidate.name, params: candidate.params as Record<string, unknown>, originalEpoch });
  sendCommand(candidate.name, candidate.params as Record<string, unknown>, id, { optimistic: false });
  return lifecycle;
}
