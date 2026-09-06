import {
  COMPONENT_KIT_API_VERSION,
  defineComponentKit,
  type ComponentKitDeclaration,
  type DesignLanguageManifest,
  type FiniteControlAppearance,
  type FrequencyRenderer,
  type InstrumentGroup,
  type LayoutManifest,
  type PresentationComponent,
  type PresentationDeclaration,
  type ScalarAppearance,
} from '@rigplane/component-kit-api';

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
  layoutCompatibility: [{ layoutId: 'fixture-layout', compatible: true }],
  renderers: {},
};

const layout: LayoutManifest = {
  schemaVersion: 1,
  id: 'fixture-layout',
  displayName: 'Fixture Layout',
  zones: [{ id: 'primary', surfaces: ['vfo', 'rxTx'] }],
  compatibleTopologies: ['1/single'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [640] },
  fallbackLayoutId: null,
};

const group: InstrumentGroup = {
  schemaVersion: 1,
  id: 'fixture-group',
  canvas: { w: 800, h: 480 },
  scaling: { mode: 'fixed-native', minScale: 0.5 },
};

const presentation: PresentationDeclaration = {
  id: 'fixture-presentation',
  loader: async () => component,
  resources: ['hardware-scope'],
};

export const fixtureKit: ComponentKitDeclaration = defineComponentKit({
  apiVersion: COMPONENT_KIT_API_VERSION,
  id: 'external-fixture',
  scalarAppearances: {
    fixture: { name: 'fixture', hbar: scalarRenderer },
  },
  frequencyReadouts: { fixture: frequencyRenderer },
  finiteControlAppearances: {
    fixture: { action: actionRenderer, toggle: toggleRenderer, choice: choiceRenderer },
  },
  designLanguages: [language],
  layouts: [layout],
  instrumentGroups: [group],
  presentations: [presentation],
});

export default fixtureKit;
