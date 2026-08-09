import { acknowledgeCommand, beginCommand, cancelPendingCommands, failCommand, type CommandLifecycle } from '$lib/stores/commands.svelte';
import { makeCommandId } from '$lib/types/protocol';
import { getControlSession, onCommandDelivery, onControlSessionTransition, sendCommand } from '$lib/transport/ws-client';

type FieldKind = 'boolean' | 'integer' | 'number' | 'receiver' | 'string' | 'vfo';
type FieldSpec = FieldKind | `${FieldKind}?`;
type IntentSpec = { names: readonly string[]; params: Readonly<Record<string, FieldSpec>> };

const intentSpecs = [
  { names: ['cw_auto_tune', 'memory_write', 'quick_dualwatch', 'quick_split', 'scan_stop', 'vfo_equalize', 'vfo_swap'], params: {} },
  { names: ['memory_clear', 'memory_to_vfo', 'set_memory_mode'], params: { channel: 'integer' } },
  { names: [
    'set_antenna_1', 'set_antenna_2', 'set_compressor', 'set_dial_lock', 'set_dual_watch',
    'set_main_sub_tracking', 'set_monitor', 'set_powerstat', 'set_rit_status', 'set_rit_tx_status',
    'set_rx_antenna_ant1', 'set_rx_antenna_ant2', 'set_scope_during_tx', 'set_scope_hold', 'set_split', 'set_vox',
  ], params: { on: 'boolean' } },
  { names: ['set_auto_notch', 'set_digisel', 'set_ip_plus', 'set_manual_notch', 'set_nb', 'set_nr', 'set_twin_peak'], params: { on: 'boolean', receiver: 'receiver' } },
  { names: [
    'set_anti_vox_gain', 'set_break_in_delay', 'set_compressor_level', 'set_drive_gain', 'set_mic_gain',
    'set_monitor_gain', 'set_nb_depth', 'set_nb_width', 'set_rf_power', 'set_vox_delay', 'set_vox_gain',
  ], params: { level: 'integer' } },
  { names: ['set_af_level'], params: { level: 'number', receiver: 'receiver' } },
  { names: ['set_nb_level', 'set_nr_level', 'set_preamp', 'set_rf_gain', 'set_squelch'], params: { level: 'integer', receiver: 'receiver' } },
  { names: ['set_cw_pitch', 'set_tuner_status'], params: { value: 'integer' } },
  { names: ['set_agc_time_constant', 'set_manual_notch_width', 'set_notch_filter', 'set_pbt_inner', 'set_pbt_outer'], params: { value: 'integer', receiver: 'receiver' } },
  { names: ['set_agc', 'set_apf', 'set_data_mode'], params: { mode: 'integer', receiver: 'receiver' } },
  { names: ['set_data_off_mod_input', 'set_data1_mod_input', 'set_data2_mod_input', 'set_data3_mod_input'], params: { source: 'integer' } },
  { names: ['set_break_in', 'scan_set_resume', 'set_scope_mode', 'speak'], params: { mode: 'integer' } },
  { names: ['scan_set_df_span', 'set_scope_span'], params: { span: 'integer' } },
  { names: ['set_key_speed', 'set_scope_speed'], params: { speed: 'integer' } },
  { names: ['scan_start', 'set_keyer_type'], params: { type: 'integer' } },
  { names: ['set_attenuator'], params: { db: 'integer', receiver: 'receiver' } },
  { names: ['set_band'], params: { band: 'integer' } },
  { names: ['set_dash_ratio'], params: { ratio: 'integer' } },
  { names: ['set_filter'], params: { filter: 'integer', receiver: 'receiver?' } },
  { names: ['set_filter_shape'], params: { shape: 'integer', receiver: 'receiver' } },
  { names: ['set_filter_width'], params: { width: 'integer', receiver: 'receiver?' } },
  { names: ['set_freq'], params: { freq: 'integer', receiver: 'receiver?' } },
  { names: ['set_if_shift'], params: { offset: 'integer', receiver: 'receiver' } },
  { names: ['set_mode'], params: { mode: 'string', filter: 'integer?', receiver: 'receiver?' } },
  { names: ['set_rit_frequency'], params: { freq: 'integer' } },
  { names: ['set_scope_center_type'], params: { center_type: 'integer' } },
  { names: ['set_scope_dual'], params: { dual: 'boolean' } },
  { names: ['set_scope_edge'], params: { edge: 'integer' } },
  { names: ['set_scope_rbw'], params: { rbw: 'integer' } },
  { names: ['set_scope_ref'], params: { ref: 'integer' } },
  { names: ['set_scope_vbw'], params: { narrow: 'boolean' } },
  { names: ['switch_scope_receiver'], params: { receiver: 'receiver' } },
  { names: ['set_vfo'], params: { vfo: 'vfo' } },
] as const satisfies readonly IntentSpec[];

type Spec = (typeof intentSpecs)[number];
type KindValue<K extends FieldKind> = K extends 'boolean' ? boolean : K extends 'receiver' ? 0 | 1
  : K extends 'string' ? string : K extends 'vfo' ? 'A' | 'B' | 'MAIN' | 'SUB' : number;
type RequiredKeys<S extends Readonly<Record<string, FieldSpec>>> = {
  [K in keyof S]-?: S[K] extends `${FieldKind}?` ? never : K
}[keyof S];
type OptionalKeys<S extends Readonly<Record<string, FieldSpec>>> = Exclude<keyof S, RequiredKeys<S>>;
type ParamsFor<S extends Readonly<Record<string, FieldSpec>>> = keyof S extends never ? Record<string, never> :
  { [K in RequiredKeys<S>]: KindValue<Extract<S[K], FieldKind>> }
  & { [K in OptionalKeys<S>]?: S[K] extends `${infer V extends FieldKind}?` ? KindValue<V> : never };
type IntentFromSpec<S extends Spec> = S extends unknown ? {
  [Name in S['names'][number]]: { name: Name; params: ParamsFor<S['params']>; id?: string }
}[S['names'][number]] : never;
export type RadioIntent = IntentFromSpec<Spec>;
export type RadioIntentName = RadioIntent['name'];

const specEntries = intentSpecs.flatMap(({ names, params }) => names.map((name) => [name, params] as const));
export const RADIO_INTENT_NAMES = Object.freeze(specEntries.map(([name]) => name)) as readonly RadioIntentName[];
const specsByName = new Map<string, Readonly<Record<string, FieldSpec>>>(specEntries);

function matchesValue(kind: FieldKind, value: unknown): boolean {
  if (kind === 'integer') return typeof value === 'number' && Number.isSafeInteger(value);
  if (kind === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (kind === 'boolean') return typeof value === 'boolean';
  if (kind === 'receiver') return value === 0 || value === 1;
  if (kind === 'vfo') return value === 'A' || value === 'B' || value === 'MAIN' || value === 'SUB';
  return typeof value === 'string' && value.length > 0;
}

function matchesParams(spec: Readonly<Record<string, FieldSpec>>, params: Record<PropertyKey, unknown>): boolean {
  if (Reflect.ownKeys(params).some((key) => typeof key !== 'string' || !Object.prototype.hasOwnProperty.call(spec, key))) return false;
  return Object.entries(spec).every(([key, field]) => {
    const optional = field.endsWith('?');
    if (!Object.prototype.hasOwnProperty.call(params, key)) return optional;
    return matchesValue((optional ? field.slice(0, -1) : field) as FieldKind, params[key]);
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
  if (typeof intent !== 'object' || intent === null || Array.isArray(intent)) throw new TypeError('Invalid radio intent envelope');
  const candidate = intent as unknown as Record<PropertyKey, unknown>;
  if (Reflect.ownKeys(candidate).some((key) => typeof key !== 'string' || (key !== 'name' && key !== 'params' && key !== 'id'))) {
    throw new TypeError('Invalid radio intent envelope');
  }
  const name = candidate.name;
  if (typeof name !== 'string' || !specsByName.has(name)) throw new TypeError('Only a known non-PTT radio intent may be dispatched');
  const params = candidate.params;
  if (typeof params !== 'object' || params === null || Array.isArray(params)
    || !matchesParams(specsByName.get(name)!, params as Record<PropertyKey, unknown>)
    || (candidate.id !== undefined && (typeof candidate.id !== 'string' || candidate.id.length === 0))) {
    throw new TypeError('Invalid radio intent envelope');
  }
  const id = (candidate.id as string | undefined) ?? makeCommandId();
  const originalEpoch = getControlSession().epoch;
  const lifecycle = beginCommand({ id, name, params: params as Record<string, unknown>, originalEpoch });
  sendCommand(name, params as Record<string, unknown>, id, { optimistic: false });
  return lifecycle;
}
