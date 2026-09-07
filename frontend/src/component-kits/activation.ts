import {
  COMPONENT_KIT_API_VERSION,
  type FiniteControlAppearance,
  type FrequencyRenderer,
  type HostedFaceComponentV1,
  type LayoutManifest,
  type MeterAppearance,
  type ScalarAppearance,
} from '../../component-kit-api/src/index';
import { skins as builtInScalarAppearances } from '../components-v2/controls/value-control/skins';
import {
  commitExternalPresentationBatch,
  isPresentationIdReserved,
  prepareExternalPresentationBatch,
  type ExternalPresentationRecord,
} from '../skins/registry';
import {
  getLayout,
  registerLayouts,
  validateLayoutManifest,
} from '../presentation/layouts/contract';

export interface ComponentKitSelection {
  readonly scalarAppearance?: string;
  readonly frequencyReadout?: string;
  readonly finiteControlAppearance?: string;
  readonly meterAppearance?: string;
  readonly presentation?: string;
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
  readonly selectedPresentation?: string;
}

interface PreparedActivation {
  readonly snapshot: ActiveSnapshot;
  readonly layouts: readonly LayoutManifest[];
  readonly presentations: readonly ExternalPresentationRecord[];
}

const EMPTY_SNAPSHOT: ActiveSnapshot = Object.freeze({
  scalarAppearances: new Map(),
  frequencyReadouts: new Map(),
  finiteControlAppearances: new Map(),
  meterAppearances: new Map(),
});
const CONFIG_KEYS = ['kits', 'selection'] as const;
const SELECTION_KEYS = [
  'scalarAppearance', 'frequencyReadout', 'finiteControlAppearance', 'meterAppearance', 'presentation',
] as const;
const KIT_KEYS = [
  'apiVersion', 'id', 'scalarAppearances', 'frequencyReadouts', 'finiteControlAppearances',
  'meterAppearances', 'designLanguages', 'layouts', 'instrumentGroups', 'presentations',
] as const;
const APPEARANCE_KEYS = ['name', 'knob', 'hbar', 'bipolar', 'discrete'] as const;
const FINITE_APPEARANCE_KEYS = ['action', 'toggle', 'choice'] as const;
const METER_APPEARANCE_KEYS = ['signal', 'level'] as const;
const UNSUPPORTED_SECTIONS = ['designLanguages', 'instrumentGroups'] as const;
const LAYOUT_KEYS = [
  'schemaVersion', 'id', 'displayName', 'zones', 'compatibleTopologies',
  'requiredSemanticSurfaces', 'stageSizing', 'fallbackLayoutId',
] as const;
const ZONE_KEYS = ['id', 'surfaces', 'group'] as const;
const PRESENTATION_KEYS = [
  'hostMode', 'id', 'layoutId', 'loader', 'resources', 'appearances',
] as const;
const FACE_APPEARANCE_KEYS = ['scalar', 'frequency', 'finite', 'meter'] as const;
const PRESENTATION_RESOURCES = ['hardware-scope', 'audio-fft'] as const;
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

function requireExactRecord(
  value: unknown,
  owner: string,
  allowed: readonly string[],
  required: readonly string[] = allowed,
): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    throw new ComponentKitActivationError(`${owner} must be an object.`);
  }
  ownDataEntries(value, owner, allowed);
  for (const key of required) {
    if (!hasOwn(value, key)) throw new ComponentKitActivationError(`${owner} is missing "${key}".`);
  }
  return value;
}

/** Read an exact dense Array without invoking authored getters. */
function readDataArray(value: unknown, owner: string): unknown[] {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) {
    throw new ComponentKitActivationError(`${owner} must be an array.`);
  }
  const allowed = new Set<PropertyKey>(['length']);
  for (let index = 0; index < value.length; index++) allowed.add(String(index));
  for (const key of Reflect.ownKeys(value)) {
    if (!allowed.has(key)) throw new ComponentKitActivationError(`${owner} has unknown property "${String(key)}".`);
  }
  const copy: unknown[] = [];
  for (let index = 0; index < value.length; index++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (descriptor === undefined || !descriptor.enumerable || !('value' in descriptor)) {
      throw new ComponentKitActivationError(
        `${owner} property "${index}" must be an enumerable data property.`,
      );
    }
    copy.push(descriptor.value);
  }
  return copy;
}

function readStringArray(value: unknown, owner: string): string[] {
  const copy = readDataArray(value, owner);
  if (copy.some((entry) => typeof entry !== 'string')) {
    throw new ComponentKitActivationError(`${owner} entries must be strings.`);
  }
  return copy as string[];
}

function readNumberArray(value: unknown, owner: string): number[] {
  const copy = readDataArray(value, owner);
  if (copy.some((entry) => typeof entry !== 'number')) {
    throw new ComponentKitActivationError(`${owner} entries must be numbers.`);
  }
  return copy as number[];
}

function copyLayout(value: unknown, owner: string): LayoutManifest {
  const manifest = requireExactRecord(value, owner, LAYOUT_KEYS);
  const id = requireId(manifest.id, owner);
  if (typeof manifest.schemaVersion !== 'number') {
    throw new ComponentKitActivationError(`Layout "${id}" schemaVersion must be a number.`);
  }
  if (typeof manifest.displayName !== 'string' || manifest.displayName.trim() === '') {
    throw new ComponentKitActivationError(`Layout "${id}" must have a non-empty displayName.`);
  }
  const zones = readDataArray(manifest.zones, `Layout "${id}" zones`).map((zoneValue, index) => {
    const zone = requireExactRecord(
      zoneValue, `Layout "${id}" zone ${index}`, ZONE_KEYS, ['id', 'surfaces'],
    );
    const zoneId = requireId(zone.id, `Layout "${id}" zone ${index}`);
    if (zone.group !== undefined && (typeof zone.group !== 'string' || zone.group.trim() === '')) {
      throw new ComponentKitActivationError(`Layout "${id}" zone "${zoneId}" group must be a non-empty string.`);
    }
    return Object.freeze({
      id: zoneId,
      surfaces: Object.freeze(readStringArray(zone.surfaces, `Layout "${id}" zone "${zoneId}" surfaces`)),
      ...(zone.group === undefined ? {} : { group: zone.group }),
    });
  });
  const compatibleTopologies = Object.freeze(
    readStringArray(manifest.compatibleTopologies, `Layout "${id}" compatibleTopologies`),
  );
  const requiredSemanticSurfaces = Object.freeze(
    readStringArray(manifest.requiredSemanticSurfaces, `Layout "${id}" requiredSemanticSurfaces`),
  );
  const sizingValue = requireExactRecord(
    manifest.stageSizing,
    `Layout "${id}" stageSizing`,
    manifest.stageSizing !== null
      && typeof manifest.stageSizing === 'object'
      && Object.getOwnPropertyDescriptor(manifest.stageSizing, 'mode')?.value === 'fluid'
      ? ['mode', 'responsiveBreakpoints']
      : ['mode', 'nativeW', 'nativeH', 'minScale'],
  );
  if (sizingValue.mode !== 'fluid' && sizingValue.mode !== 'fixed-native') {
    throw new ComponentKitActivationError(
      `Layout "${id}" stageSizing mode must be "fluid" or "fixed-native".`,
    );
  }
  if (sizingValue.mode === 'fixed-native'
    && [sizingValue.nativeW, sizingValue.nativeH, sizingValue.minScale]
      .some((entry) => typeof entry !== 'number')) {
    throw new ComponentKitActivationError(`Layout "${id}" fixed sizing entries must be numbers.`);
  }
  const stageSizing = sizingValue.mode === 'fluid'
    ? Object.freeze({
        mode: 'fluid' as const,
        responsiveBreakpoints: Object.freeze(
          readNumberArray(sizingValue.responsiveBreakpoints, `Layout "${id}" responsiveBreakpoints`),
        ),
      })
    : Object.freeze({
        mode: sizingValue.mode,
        nativeW: sizingValue.nativeW,
        nativeH: sizingValue.nativeH,
        minScale: sizingValue.minScale,
      });
  if (manifest.fallbackLayoutId !== null) requireId(manifest.fallbackLayoutId, `Layout "${id}" fallbackLayoutId`);
  const copied = Object.freeze({
    schemaVersion: manifest.schemaVersion,
    id,
    displayName: manifest.displayName,
    zones: Object.freeze(zones),
    compatibleTopologies,
    requiredSemanticSurfaces,
    stageSizing,
    fallbackLayoutId: manifest.fallbackLayoutId,
  }) as unknown as LayoutManifest;
  try {
    validateLayoutManifest(copied);
  } catch (error) {
    throw new ComponentKitActivationError(error instanceof Error ? error.message : String(error));
  }
  return copied;
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

function prepareActivation(declarations: readonly unknown[], selectionValue: unknown): PreparedActivation {
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
  const authoredLayouts: Array<{ owner: string; value: unknown }> = [];
  const authoredPresentations: Array<{ owner: string; value: unknown }> = [];

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

    if (value.layouts !== undefined) {
      for (const layout of readDataArray(value.layouts, `Component kit "${id}" layouts`)) {
        authoredLayouts.push({ owner: `Component kit "${id}" layout`, value: layout });
      }
    }
    if (value.presentations !== undefined) {
      for (const presentation of readDataArray(
        value.presentations, `Component kit "${id}" presentations`,
      )) {
        authoredPresentations.push({
          owner: `Component kit "${id}" presentation`, value: presentation,
        });
      }
    }
  }

  const layouts: LayoutManifest[] = [];
  const layoutIds = new Set<string>();
  for (const authored of authoredLayouts) {
    const layout = copyLayout(authored.value, authored.owner);
    if (layoutIds.has(layout.id)) {
      throw new ComponentKitActivationError(`Duplicate layout id "${layout.id}".`);
    }
    if (getLayout(layout.id) !== undefined || isPresentationIdReserved(layout.id)) {
      throw new ComponentKitActivationError(`Layout id "${layout.id}" is registered or reserved.`);
    }
    layoutIds.add(layout.id);
    layouts.push(layout);
  }

  const presentations: ExternalPresentationRecord[] = [];
  const presentationIds = new Set<string>();
  for (const authored of authoredPresentations) {
    const presentation = requireExactRecord(authored.value, authored.owner, PRESENTATION_KEYS);
    const id = requireId(presentation.id, authored.owner);
    if (presentation.hostMode !== 'external-instruments-v1') {
      throw new ComponentKitActivationError(
        `Presentation "${id}" hostMode must be "external-instruments-v1".`,
      );
    }
    const layoutId = requireId(presentation.layoutId, `Presentation "${id}" layoutId`);
    if (!layoutIds.has(layoutId)) {
      throw new ComponentKitActivationError(
        `Presentation "${id}" layout "${layoutId}" is not in the activated batch.`,
      );
    }
    if (typeof presentation.loader !== 'function') {
      throw new ComponentKitActivationError(`Presentation "${id}" loader must be a function.`);
    }
    const resourceValues = readDataArray(presentation.resources, `Presentation "${id}" resources`);
    const resources = resourceValues.map((resource) => {
      if (typeof resource !== 'string' || !PRESENTATION_RESOURCES.includes(resource as never)) {
        throw new ComponentKitActivationError(`Presentation "${id}" has unsupported resource "${String(resource)}".`);
      }
      return resource as (typeof PRESENTATION_RESOURCES)[number];
    });
    if (new Set(resources).size !== resources.length) {
      throw new ComponentKitActivationError(`Presentation "${id}" has a duplicate resource.`);
    }
    const appearanceIds = requireExactRecord(
      presentation.appearances, `Presentation "${id}" appearances`, FACE_APPEARANCE_KEYS,
    );
    const scalarId = requireId(appearanceIds.scalar, `Presentation "${id}" scalar appearance`);
    const frequencyId = requireId(appearanceIds.frequency, `Presentation "${id}" frequency appearance`);
    const finiteId = requireId(appearanceIds.finite, `Presentation "${id}" finite appearance`);
    const meterId = requireId(appearanceIds.meter, `Presentation "${id}" meter appearance`);
    const scalar = scalarAppearances.get(scalarId);
    const frequency = frequencyReadouts.get(frequencyId);
    const finite = finiteControlAppearances.get(finiteId);
    const meter = meterAppearances.get(meterId);
    if (scalar === undefined) throw new ComponentKitActivationError(`Presentation "${id}" scalar appearance "${scalarId}" is not registered.`);
    if (frequency === undefined) throw new ComponentKitActivationError(`Presentation "${id}" frequency appearance "${frequencyId}" is not registered.`);
    if (finite === undefined) throw new ComponentKitActivationError(`Presentation "${id}" finite appearance "${finiteId}" is not registered.`);
    if (meter === undefined) throw new ComponentKitActivationError(`Presentation "${id}" meter appearance "${meterId}" is not registered.`);
    if (presentationIds.has(id)) throw new ComponentKitActivationError(`Duplicate presentation id "${id}".`);
    presentationIds.add(id);
    presentations.push(Object.freeze({
      id,
      kind: 'external-instruments-v1',
      layoutId,
      loader: presentation.loader as () => Promise<HostedFaceComponentV1>,
      resources: Object.freeze(resources),
      appearances: Object.freeze({ scalar, frequency, finite, meter }),
    }));
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
  if (selection.presentation !== undefined && !presentationIds.has(selection.presentation)) {
    throw new ComponentKitActivationError(
      `Selected external presentation "${selection.presentation}" is not registered.`,
    );
  }
  return Object.freeze({
    snapshot: Object.freeze({
      scalarAppearances,
      frequencyReadouts,
      finiteControlAppearances,
      meterAppearances,
      selectedScalarAppearance: selection.scalarAppearance,
      selectedFrequencyReadout: selection.frequencyReadout,
      selectedFiniteControlAppearance: selection.finiteControlAppearance,
      selectedMeterAppearance: selection.meterAppearance,
      selectedPresentation: selection.presentation,
    }),
    layouts: Object.freeze(layouts),
    presentations: Object.freeze(presentations),
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
    const kitLoaders = readDataArray(configValue.kits, 'Component-kit configuration kits');
    if (kitLoaders.some((load) => typeof load !== 'function')) {
      throw new ComponentKitActivationError('Component-kit configuration kits must be an array of loaders.');
    }
    const modules = await Promise.all(kitLoaders.map((load) => (load as () => Promise<unknown>)()));
    const declarations = modules.map((module, index) => {
      if (!isPlainRecord(module) || !hasOwn(module, 'default')) {
        throw new ComponentKitActivationError(`Component-kit loader ${index} must return a module with a default export.`);
      }
      return module.default;
    });
    const prepared = prepareActivation(declarations, configValue.selection);
    const catalogBatch = prepareExternalPresentationBatch(prepared.presentations);
    registerLayouts(prepared.layouts);
    commitExternalPresentationBatch(catalogBatch);
    snapshot = prepared.snapshot;
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

export function getSelectedPresentationId(): string | undefined {
  return snapshot.selectedPresentation;
}
