import {
  COMPONENT_KIT_API_VERSION,
  defineComponentKit,
  type DesignLanguageManifest,
  type FiniteControlAppearance,
  type FrequencyRenderer,
  type HostedComponentKitDeclarationV1,
  type HostedFaceComponentV1,
  type HostedFacePresentationV1,
  type InstrumentGroup,
  type LayoutManifest,
  type MeterAppearance,
  type PresentationComponent,
  type PresentationDeclaration,
  type ScalarAppearance,
} from '@rigplane/component-kit-api';
import FaceA from './FaceA.svelte';
import FaceB from './FaceB.svelte';
import FixtureLevelMeter from './FixtureLevelMeter.svelte';
import FixtureSignalMeter from './FixtureSignalMeter.svelte';

const component = (() => ({})) as unknown as PresentationComponent;
const scalarRenderer = (() => ({})) as unknown as NonNullable<ScalarAppearance['hbar']>;
const frequencyRenderer = (() => ({})) as unknown as FrequencyRenderer;
const actionRenderer = ((_internals, { lease }) => {
  void lease.view?.available;
  return {};
}) satisfies FiniteControlAppearance['action'];
const toggleRenderer = ((_internals, { lease }) => {
  void lease.view?.confirmed;
  return {};
}) satisfies FiniteControlAppearance['toggle'];
const choiceRenderer = ((_internals, { lease }) => {
  const option = lease.view?.options[0];
  const request = option === undefined ? undefined : () => lease.invoke(option.value);
  void request;
  return {};
}) satisfies FiniteControlAppearance['choice'];
const meterAppearance = {
  signal: FixtureSignalMeter,
  level: FixtureLevelMeter,
} satisfies MeterAppearance;

const language: DesignLanguageManifest = {
  id: 'fixture-line',
  displayName: 'Fixture Line',
  tokens: {
    typography: { fontFamily: 'sans-serif', weight: 600, fontVariantNumeric: 'tabular-nums' },
    geometry: { radius: '0', borderWidth: '1px' },
    meters: { trackWidth: '1px', segmentGap: '1px' },
    frequency: { digitWeight: 600, rankedGroups: true },
    motion: { durationMs: 0, reducedMotionSafe: true },
    focusRing: '#00ffff',
    rx: { idle: '#777777', active: '#00ffff', tuning: '#ffffff' },
    tx: { idle: '#777777', active: '#ff0000', tuning: '#ffffff' },
  },
  density: { kind: 'clamped', supported: ['comfortable', 'compact'] },
  layoutCompatibility: [
    { layoutId: 'fixture-face-a', compatible: true },
    { layoutId: 'fixture-face-b', compatible: true },
  ],
  renderers: {},
};

const faceALayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'fixture-face-a',
  displayName: 'Fixture Face A',
  zones: [
    { id: 'receiver', surfaces: ['vfo'] },
    { id: 'transmit', surfaces: ['txAux'] },
  ],
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'txAux'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [640] },
  fallbackLayoutId: null,
};

const faceBLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'fixture-face-b',
  displayName: 'Fixture Face B',
  zones: [
    { id: 'transmit', surfaces: ['txAux'] },
    { id: 'receiver', surfaces: ['vfo'] },
  ],
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['txAux', 'vfo'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [720] },
  fallbackLayoutId: null,
};

const group: InstrumentGroup = {
  schemaVersion: 1,
  id: 'fixture-group',
  canvas: { w: 800, h: 480 },
  scaling: { mode: 'fixed-native', minScale: 0.5 },
};

export const reservedPresentation: PresentationDeclaration = {
  id: 'fixture-reserved-presentation',
  loader: async () => component,
  resources: ['hardware-scope'],
};

export const FixtureFaceA: HostedFaceComponentV1 = FaceA;
export const FixtureFaceB: HostedFaceComponentV1 = FaceB;

export const faceAPresentation: HostedFacePresentationV1 = {
  hostMode: 'external-instruments-v1',
  id: 'fixture-face-a',
  layoutId: faceALayout.id,
  loader: async () => FixtureFaceA,
  resources: [],
  appearances: { scalar: 'fixture', frequency: 'fixture', finite: 'fixture', meter: 'fixture' },
};

export const faceBPresentation: HostedFacePresentationV1 = {
  hostMode: 'external-instruments-v1',
  id: 'fixture-face-b',
  layoutId: faceBLayout.id,
  loader: async () => FixtureFaceB,
  resources: [],
  appearances: { scalar: 'fixture', frequency: 'fixture', finite: 'fixture', meter: 'fixture' },
};

export const fixtureKit: HostedComponentKitDeclarationV1 = defineComponentKit({
  apiVersion: COMPONENT_KIT_API_VERSION,
  id: 'external-fixture',
  scalarAppearances: {
    fixture: { name: 'fixture', hbar: scalarRenderer },
  },
  frequencyReadouts: { fixture: frequencyRenderer },
  finiteControlAppearances: {
    fixture: { action: actionRenderer, toggle: toggleRenderer, choice: choiceRenderer },
  },
  meterAppearances: { fixture: meterAppearance },
  designLanguages: [language],
  layouts: [faceALayout, faceBLayout],
  instrumentGroups: [group],
  presentations: [faceAPresentation, faceBPresentation],
});

export default fixtureKit;
