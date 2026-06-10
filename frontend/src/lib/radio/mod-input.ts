/**
 * IC-7610 per-DATA-group MOD-input source helpers (MOR-616).
 *
 * Pure constants and mappers shared by the runtime prop mappers
 * (`lib/runtime/props/panel-props`), the wiring duplicates
 * (`components-v2/wiring/state-adapter` / `command-bus`) and the
 * ModePanel dropdown.
 *
 * Source enum mirrors the radio's DATA OFF/1/2/3 MOD menu items
 * (CI-V `0x1A 05 00 0x91`-`0x94`, see `rigs/ic7610.toml`):
 * 0=MIC, 1=ACC, 2=MIC+ACC, 3=USB, 4=MIC+USB, 5=LAN.
 *
 * Backend contract (T1/MOR-615): the public `state_update` payload carries
 * the camelCase keys below (null until first readback) and the web control
 * handler accepts `set_data_off_mod_input` / `set_data1_mod_input` /
 * `set_data2_mod_input` / `set_data3_mod_input` with `{ source: int }`.
 */

export interface ModInputOption {
  readonly value: number;
  readonly label: string;
}

export const MOD_INPUT_SOURCES: readonly ModInputOption[] = [
  { value: 0, label: 'MIC' },
  { value: 1, label: 'ACC' },
  { value: 2, label: 'MIC+ACC' },
  { value: 3, label: 'USB' },
  { value: 4, label: 'MIC+USB' },
  { value: 5, label: 'LAN' },
];

/** Source value that routes network (web) audio into the modulator. */
export const LAN_MOD_INPUT_SOURCE = 5;

/** camelCase `ServerState` keys of the four DATA-group sources. */
export type ModInputStateKey =
  | 'dataOffModInput'
  | 'data1ModInput'
  | 'data2ModInput'
  | 'data3ModInput';

/** Backend SET-command names, indexable by data_mode group. */
export type ModInputCommand =
  | 'set_data_off_mod_input'
  | 'set_data1_mod_input'
  | 'set_data2_mod_input'
  | 'set_data3_mod_input';

const STATE_KEYS: readonly ModInputStateKey[] = [
  'dataOffModInput',
  'data1ModInput',
  'data2ModInput',
  'data3ModInput',
];

const COMMANDS: readonly ModInputCommand[] = [
  'set_data_off_mod_input',
  'set_data1_mod_input',
  'set_data2_mod_input',
  'set_data3_mod_input',
];

/** Human label for a source value; null when unknown or unread. */
export function modInputSourceLabel(source: number | null | undefined): string | null {
  return MOD_INPUT_SOURCES.find((option) => option.value === source)?.label ?? null;
}

/** `ServerState` key for a receiver's data_mode group (out-of-range → DATA OFF). */
export function modInputStateKey(dataMode: number): ModInputStateKey {
  return STATE_KEYS[dataMode] ?? 'dataOffModInput';
}

/** SET-command name for a receiver's data_mode group (out-of-range → DATA OFF). */
export function modInputCommand(dataMode: number): ModInputCommand {
  return COMMANDS[dataMode] ?? 'set_data_off_mod_input';
}
