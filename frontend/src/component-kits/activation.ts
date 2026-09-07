import {
  COMPONENT_KIT_API_VERSION,
  type FiniteControlAppearance,
  type FrequencyRenderer,
  type MeterAppearance,
  type ScalarAppearance,
} from '../../component-kit-api/src/index';
import { skins as builtInScalarAppearances } from '../components-v2/controls/value-control/skins';

export interface ComponentKitSelection {
  readonly scalarAppearance?: string;
  readonly frequencyReadout?: string;
  readonly finiteControlAppearance?: string;
  readonly meterAppearance?: string;
}

export interface ComponentKitHostConfig {
  readonly kits: readonly (() => Promise<{ default: unknown }>)[];
  readonly selection?: ComponentKitSelection;
}

interface ActiveSnapshot {
  readonly scalarAppearances: ReadonlyMap<string, ScalarAppearance>;
  readonly frequencyReadouts: ReadonlyMap<string, FrequencyRenderer>;
  readonly finiteControlAppearances: ReadonlyMap<string, FiniteControlAppearance>;
  readonly meterAppearances: ReadonlyMap<string, MeterAppearance>;
  readonly selectedScalarAppearance?: string;
  readonly selectedFrequencyReadout?: string;
  readonly selectedFiniteControlAppearance?: string;
  readonly selectedMeterAppearance?: string;
}

const EMPTY_SNAPSHOT: ActiveSnapshot = Object.freeze({
  scalarAppearances: new Map(),
  frequencyReadouts: new Map(),
  finiteControlAppearances: new Map(),
  meterAppearances: new Map(),
});
const CONFIG_KEYS = ['kits', 'selection'] as const;
const SELECTION_KEYS = [
  'scalarAppearance', 'frequencyReadout', 'finiteControlAppearance', 'meterAppearance',
] as const;
const KIT_KEYS = [
  'apiVersion', 'id', 'scalarAppearances', 'frequencyReadouts', 'finiteControlAppearances',
  'meterAppearances', 'designLanguages', 'layouts', 'instrumentGroups', 'presentations',
] as const;
const APPEARANCE_KEYS = ['name', 'knob', 'hbar', 'bipolar', 'discrete'] as const;
const FINITE_APPEARANCE_KEYS = ['action', 'toggle', 'choice'] as const;
const METER_APPEARANCE_KEYS = ['signal', 'level'] as const;
const UNSUPPORTED_SECTIONS = [
  'designLanguages', 'layouts', 'instrumentGroups', 'presentations',
] as const;
const hasOwn = (value: object, key: PropertyKey): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

let snapshot = EMPTY_SNAPSHOT;
let phase: 'idle' | 'activating' | 'active' = 'idle';

export class ComponentKitActivationError extends Error {}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function ownDataEntries(
  value: Record<string, unknown>,
  owner: string,
  allowed?: readonly string[],
): [string, unknown][] {
  const entries: [string, unknown][] = [];
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== 'string' || (allowed !== undefined && !allowed.includes(key))) {
      throw new ComponentKitActivationError(`${owner} has unknown property "${String(key)}".`);
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !descriptor.enumerable || !('value' in descriptor)) {
      throw new ComponentKitActivationError(
        `${owner} property "${key}" must be an enumerable data property.`,
      );
    }
    entries.push([key, descriptor.value]);
  }
  return entries;
}

function requireId(value: unknown, owner: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new ComponentKitActivationError(`${owner} must have a non-empty id.`);
  }
  return value;
}

function copyAppearance(value: unknown, id: string): ScalarAppearance {
  if (!isPlainRecord(value)) {
    throw new ComponentKitActivationError(`Scalar appearance "${id}" must be an object.`);
  }
  ownDataEntries(value, `Scalar appearance "${id}"`, APPEARANCE_KEYS);
  if (typeof value.name !== 'string' || value.name.trim() === '') {
    throw new ComponentKitActivationError(`Scalar appearance "${id}" must have a non-empty name.`);
  }
  for (const slot of APPEARANCE_KEYS.slice(1)) {
    if (value[slot] !== undefined && typeof value[slot] !== 'function') {
      throw new ComponentKitActivationError(`Scalar appearance "${id}" member "${slot}" must be a component.`);
    }
  }
  return Object.freeze({ ...value }) as unknown as ScalarAppearance;
}

function copyFiniteAppearance(value: unknown, id: string): FiniteControlAppearance {
  if (!isPlainRecord(value)) {
    throw new ComponentKitActivationError(`Finite control appearance "${id}" must be an object.`);
  }
  ownDataEntries(value, `Finite control appearance "${id}"`, FINITE_APPEARANCE_KEYS);
  for (const slot of FINITE_APPEARANCE_KEYS) {
    if (typeof value[slot] !== 'function') {
      throw new ComponentKitActivationError(
        `Finite control appearance "${id}" member "${slot}" must be a component.`,
      );
    }
  }
  return Object.freeze({ ...value }) as unknown as FiniteControlAppearance;
}

function copyMeterAppearance(value: unknown, id: string): MeterAppearance {
  if (!isPlainRecord(value)) {
    throw new ComponentKitActivationError(`Meter appearance "${id}" must be an object.`);
  }
  ownDataEntries(value, `Meter appearance "${id}"`, METER_APPEARANCE_KEYS);
  for (const slot of METER_APPEARANCE_KEYS) {
    if (typeof value[slot] !== 'function') {
      throw new ComponentKitActivationError(
        `Meter appearance "${id}" member "${slot}" must be a component.`,
      );
    }
  }
  return Object.freeze({ ...value }) as unknown as MeterAppearance;
}

function readSelection(value: unknown): ComponentKitSelection {
  if (value === undefined) return {};
  if (!isPlainRecord(value)) {
    throw new ComponentKitActivationError('Component-kit selection must be an object.');
  }
  ownDataEntries(value, 'Component-kit selection', SELECTION_KEYS);
  for (const key of SELECTION_KEYS) {
    if (value[key] !== undefined && (typeof value[key] !== 'string' || value[key].trim() === '')) {
      throw new ComponentKitActivationError(`Component-kit selection "${key}" must be a non-empty string.`);
    }
  }
  return { ...value } as ComponentKitSelection;
}

function prepareSnapshot(declarations: readonly unknown[], selectionValue: unknown): ActiveSnapshot {
  const scalarAppearances = new Map<string, ScalarAppearance>();
  const scalarOwners = new Map<string, string>();
  for (const [id, appearance] of ownDataEntries(builtInScalarAppearances, 'Built-in scalar appearances')) {
    scalarAppearances.set(id, copyAppearance(appearance, id));
    scalarOwners.set(id, 'built-in scalar appearances');
  }
  const frequencyReadouts = new Map<string, FrequencyRenderer>();
  const frequencyOwners = new Map<string, string>();
  const finiteControlAppearances = new Map<string, FiniteControlAppearance>();
  const finiteAppearanceOwners = new Map<string, string>();
  const meterAppearances = new Map<string, MeterAppearance>();
  const meterAppearanceOwners = new Map<string, string>();
  const kitIds = new Set<string>();

  for (const value of declarations) {
    if (!isPlainRecord(value)) {
      throw new ComponentKitActivationError('A component-kit module did not export an object.');
    }
    ownDataEntries(value, 'Component kit', KIT_KEYS);
    const id = requireId(value.id, 'Component kit');
    if (value.apiVersion !== COMPONENT_KIT_API_VERSION) {
      throw new ComponentKitActivationError(`Component kit "${id}" requires unsupported API version ${String(value.apiVersion)}.`);
    }
    if (kitIds.has(id)) throw new ComponentKitActivationError(`Duplicate component-kit id "${id}".`);
    kitIds.add(id);

    for (const section of UNSUPPORTED_SECTIONS) {
      if (hasOwn(value, section)) {
        throw new ComponentKitActivationError(`Component kit "${id}" declares unsupported property "${section}".`);
      }
    }

    if (value.scalarAppearances !== undefined) {
      if (!isPlainRecord(value.scalarAppearances)) {
        throw new ComponentKitActivationError(`Component kit "${id}" scalarAppearances must be a record.`);
      }
      for (const [appearanceId, appearance] of ownDataEntries(
        value.scalarAppearances,
        `Component kit "${id}" scalarAppearances`,
      )) {
        requireId(appearanceId, `Component kit "${id}" scalar appearance`);
        const owner = scalarOwners.get(appearanceId);
        if (owner) {
          throw new ComponentKitActivationError(`Duplicate scalar appearance "${appearanceId}" conflicts with ${owner}.`);
        }
        scalarAppearances.set(appearanceId, copyAppearance(appearance, appearanceId));
        scalarOwners.set(appearanceId, `component kit "${id}"`);
      }
    }

    if (value.frequencyReadouts !== undefined) {
      if (!isPlainRecord(value.frequencyReadouts)) {
        throw new ComponentKitActivationError(`Component kit "${id}" frequencyReadouts must be a record.`);
      }
      for (const [readoutId, readout] of ownDataEntries(
        value.frequencyReadouts,
        `Component kit "${id}" frequencyReadouts`,
      )) {
        requireId(readoutId, `Component kit "${id}" frequency readout`);
        if (frequencyOwners.has(readoutId)) {
          throw new ComponentKitActivationError(`Duplicate frequency readout "${readoutId}" conflicts with ${frequencyOwners.get(readoutId)}.`);
        }
        if (typeof readout !== 'function') {
          throw new ComponentKitActivationError(`Frequency readout "${readoutId}" must be a component.`);
        }
        frequencyReadouts.set(readoutId, readout as FrequencyRenderer);
        frequencyOwners.set(readoutId, `component kit "${id}"`);
      }
    }

    if (value.finiteControlAppearances !== undefined) {
      if (!isPlainRecord(value.finiteControlAppearances)) {
        throw new ComponentKitActivationError(
          `Component kit "${id}" finiteControlAppearances must be a record.`,
        );
      }
      for (const [appearanceId, appearance] of ownDataEntries(
        value.finiteControlAppearances,
        `Component kit "${id}" finiteControlAppearances`,
      )) {
        requireId(appearanceId, `Component kit "${id}" finite control appearance`);
        if (finiteAppearanceOwners.has(appearanceId)) {
          throw new ComponentKitActivationError(
            `Duplicate finite control appearance "${appearanceId}" conflicts with ${finiteAppearanceOwners.get(appearanceId)}.`,
          );
        }
        finiteControlAppearances.set(
          appearanceId,
          copyFiniteAppearance(appearance, appearanceId),
        );
        finiteAppearanceOwners.set(appearanceId, `component kit "${id}"`);
      }
    }

    if (value.meterAppearances !== undefined) {
      if (!isPlainRecord(value.meterAppearances)) {
        throw new ComponentKitActivationError(
          `Component kit "${id}" meterAppearances must be a record.`,
        );
      }
      for (const [appearanceId, appearance] of ownDataEntries(
        value.meterAppearances,
        `Component kit "${id}" meterAppearances`,
      )) {
        requireId(appearanceId, `Component kit "${id}" meter appearance`);
        if (meterAppearanceOwners.has(appearanceId)) {
          throw new ComponentKitActivationError(
            `Duplicate meter appearance "${appearanceId}" conflicts with ${meterAppearanceOwners.get(appearanceId)}.`,
          );
        }
        meterAppearances.set(appearanceId, copyMeterAppearance(appearance, appearanceId));
        meterAppearanceOwners.set(appearanceId, `component kit "${id}"`);
      }
    }
  }

  const selection = readSelection(selectionValue);
  if (selection.scalarAppearance !== undefined && !scalarAppearances.has(selection.scalarAppearance)) {
    throw new ComponentKitActivationError(`Selected scalar appearance "${selection.scalarAppearance}" is not registered.`);
  }
  if (selection.frequencyReadout !== undefined && !frequencyReadouts.has(selection.frequencyReadout)) {
    throw new ComponentKitActivationError(`Selected frequency readout "${selection.frequencyReadout}" is not registered.`);
  }
  if (selection.finiteControlAppearance !== undefined
    && !finiteControlAppearances.has(selection.finiteControlAppearance)) {
    throw new ComponentKitActivationError(
      `Selected finite control appearance "${selection.finiteControlAppearance}" is not registered.`,
    );
  }
  if (selection.meterAppearance !== undefined
    && !meterAppearances.has(selection.meterAppearance)) {
    throw new ComponentKitActivationError(
      `Selected meter appearance "${selection.meterAppearance}" is not registered.`,
    );
  }
  return Object.freeze({
    scalarAppearances,
    frequencyReadouts,
    finiteControlAppearances,
    meterAppearances,
    selectedScalarAppearance: selection.scalarAppearance,
    selectedFrequencyReadout: selection.frequencyReadout,
    selectedFiniteControlAppearance: selection.finiteControlAppearance,
    selectedMeterAppearance: selection.meterAppearance,
  });
}

export async function activateComponentKits(configValue: unknown): Promise<void> {
  if (phase !== 'idle') throw new ComponentKitActivationError('Component kits are already activated.');
  phase = 'activating';
  try {
    if (!isPlainRecord(configValue)) {
      throw new ComponentKitActivationError('Component-kit configuration must be an object.');
    }
    ownDataEntries(configValue, 'Component-kit configuration', CONFIG_KEYS);
    if (!Array.isArray(configValue.kits) || configValue.kits.some((load) => typeof load !== 'function')) {
      throw new ComponentKitActivationError('Component-kit configuration kits must be an array of loaders.');
    }
    const modules = await Promise.all(configValue.kits.map((load) => load()));
    const declarations = modules.map((module, index) => {
      if (!isPlainRecord(module) || !hasOwn(module, 'default')) {
        throw new ComponentKitActivationError(`Component-kit loader ${index} must return a module with a default export.`);
      }
      return module.default;
    });
    const prepared = prepareSnapshot(declarations, configValue.selection);
    snapshot = prepared;
    phase = 'active';
  } catch (error) {
    phase = 'idle';
    throw error;
  }
}

export function getSelectedScalarAppearance(): ScalarAppearance | undefined {
  return snapshot.selectedScalarAppearance === undefined
    ? undefined
    : snapshot.scalarAppearances.get(snapshot.selectedScalarAppearance);
}

export function getSelectedFrequencyReadout(): FrequencyRenderer | undefined {
  return snapshot.selectedFrequencyReadout === undefined
    ? undefined
    : snapshot.frequencyReadouts.get(snapshot.selectedFrequencyReadout);
}

export function getSelectedFiniteControlAppearance(): FiniteControlAppearance | undefined {
  return snapshot.selectedFiniteControlAppearance === undefined
    ? undefined
    : snapshot.finiteControlAppearances.get(snapshot.selectedFiniteControlAppearance);
}

export function getSelectedMeterAppearance(): MeterAppearance | undefined {
  return snapshot.selectedMeterAppearance === undefined
    ? undefined
    : snapshot.meterAppearances.get(snapshot.selectedMeterAppearance);
}
