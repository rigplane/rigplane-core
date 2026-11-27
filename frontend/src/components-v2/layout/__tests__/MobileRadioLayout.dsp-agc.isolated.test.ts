/**
 * MOR-4528 — the phone DSP tab hosts the AGC choice.
 *
 * Every capability and state document here is one the backend produced:
 * `scripts/gen_state_wire_goldens.py` writes them into
 * `src/lib/transport/__tests__/fixtures/state-wire-goldens.json` and
 * `tests/web/test_state_wire_goldens.py` keeps that file current. They
 * enter through the production parsers — `validateCapabilities`, the
 * capabilities store, the real ws-client over a fake socket and the radio
 * store's own state validation — into the real `MobileRadioLayout`, its
 * real `AgcPanel` and the real command path. Only the socket, the
 * capabilities HTTP fetch, the audio manager, the spectrum pane and the
 * icons are stand-ins.
 *
 * Server half of the command seam:
 * `tests/test_g7250_local_api.py::test_agc_slow_writes_03_and_capabilities_serve_slow`
 * sends `set_agc {mode}` through the real local API, sees the handler accept
 * it and then serves `main.agc` equal to the requested mode. This file is
 * the browser half of that exchange: the key press puts `set_agc` on the
 * socket, the handler's `response` frame (shape per `ControlHandler` in
 * `src/rigplane/web/handlers/control.py`) and the next state envelope come
 * back, and the lit key moves only when that envelope arrives.
 *
 * Named `*.isolated.test.ts`: it mocks `http-client`. Components and stores
 * share one module graph for this file; each session resets its store state.
 */
import { describe, it, expect, vi, beforeEach, afterAll, afterEach } from 'vitest';
import { validateCapabilities, type Capabilities } from '$lib/types/capabilities';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import type { ServerState } from '$lib/types/state';
import goldenFile from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import * as phoneModule from '../MobileRadioLayout.svelte';
import * as desktopAgcModule from '../../panels/AgcPanel.svelte';

vi.hoisted(() => { vi.useFakeTimers(); });

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return {
    Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S,
    Mic: S, MicOff: S, Sliders: S, Radio: S,
  };
});
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    onTxAudioDied: () => () => {}, setOperatorNotifier: vi.fn(),
    start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(),
  },
}));
const txHost = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));
const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(), fetchCapabilities }));

type Envelope = Record<string, unknown> & { data: Record<string, unknown> };
interface GoldenEntry {
  /** Served by a server with no radio attached: the profile's tables, no
   *  radio-derived capability tags. */
  capabilities: Record<string, unknown> & { capabilities: string[] };
  /** Served by a server with the radio attached (Icom polled family only). */
  polledSquelchCapabilities?: Record<string, unknown>;
  allObserved?: Envelope;
  allUnobserved?: Envelope;
}
const goldens = goldenFile as unknown as Record<string, GoldenEntry>;

/**
 * The capability document of a connected radio. Where the generator has a
 * radio-backed document it is used as served. Elsewhere the radio-less one
 * is used with the single `agc` tag a connected radio adds (the IC-705's two
 * documents differ in exactly that kind of tag); the profile's AGC table is
 * the served one either way, and the result must pass `validateCapabilities`.
 */
function connectedCapabilities(model: string): Capabilities {
  const entry = goldens[model];
  if (entry.polledSquelchCapabilities) {
    return validateCapabilities(structuredClone(entry.polledSquelchCapabilities));
  }
  const served = structuredClone(entry.capabilities);
  return validateCapabilities({ ...served, capabilities: [...served.capabilities, 'agc'] });
}

/** The AGC keys each radio must offer: its served table, in served order. */
const servedKeys = (model: string): string[] => {
  const { agcModes, agcLabels } = validateCapabilities(structuredClone(goldens[model].capabilities));
  return (agcModes ?? []).map((mode) => (agcLabels ?? {})[String(mode)]);
};

/**
 * The golden observed envelope with the radio link up. The generator has no
 * live radio behind it, so every golden serves a down link (`connection`
 * flags false, `radioHealth` unknown/stalled) and the client (rightly)
 * refuses to send commands. A live link is those served fields at their
 * healthy values; nothing else in the document changes.
 */
function liveEnvelope(model: string): Envelope {
  const envelope = structuredClone(goldens[model].allObserved!);
  const link: Pick<ServerState['connection'], 'rigConnected' | 'radioReady' | 'controlConnected'> = {
    rigConnected: true, radioReady: true, controlConnected: true,
  };
  const health: Pick<NonNullable<ServerState['radioHealth']>, 'radioLink' | 'readiness'> = {
    radioLink: 'connected', readiness: 'ready',
  };
  envelope.data.connection = { ...(envelope.data.connection as object), ...link };
  envelope.data.radioHealth = { ...(envelope.data.radioHealth as object), ...health };
  return envelope;
}

/** The state envelope the server sends once the radio has confirmed `mode`:
 *  the live envelope, one revision later, with that reading. */
function confirmedEnvelope(model: string, mode: number): Envelope {
  const next = liveEnvelope(model);
  for (const key of ['revision', 'stateRevision', 'freshnessRevision', 'observationSeq', 'transportSeq']) {
    next[key] = (next[key] as number) + 1;
    if (typeof next.data[key] === 'number') next.data[key] = (next.data[key] as number) + 1;
  }
  (next.data.main as Record<string, unknown>).agc = mode;
  const status = (next.data.fieldStatus as Record<string, Record<string, unknown>>)['main.agc'];
  status.lastObservedMonotonic = (status.lastObservedMonotonic as number) + 1;
  return next;
}

interface Session {
  socket: MockWebSocket;
  flushSync: typeof import('svelte').flushSync;
  resetCommandLifecycle: () => void;
  /** `set_agc` frames the client has put on the socket so far. */
  agcFrames: () => { id: string; name: string; params: Record<string, unknown> }[];
  deliver: (message: Record<string, unknown>) => void;
  mount: (load: () => Promise<{ default: unknown }>) => Promise<HTMLElement>;
}

let loadedWsClient: typeof import('$lib/transport/ws-client') | null = null;
let originalWebSocket: typeof WebSocket;
let mounted: (() => Promise<void>)[] = [];

/** Bring one radio up through the production path: capabilities into the
 *  store, the socket open, the first state envelope delivered. */
async function connectRadio(caps: Capabilities, envelope: Envelope): Promise<Session> {
  fetchCapabilities.mockResolvedValue(caps);
  const wsClient = await import('$lib/transport/ws-client');
  loadedWsClient = wsClient;
  const store = await import('$lib/stores/radio.svelte');
  const capabilities = await import('$lib/stores/capabilities.svelte');
  const { resetCommandLifecycle } = await import('$lib/stores/commands.svelte');
  const { mount, unmount, flushSync } = await import('svelte');
  const { ManagedAppTxHarness } = await import(
    '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness');
  txHost.current = new ManagedAppTxHarness().controller;
  expect(capabilities.setCapabilities(caps)).toBe(true);

  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  const deliver = (message: Record<string, unknown>) => {
    socket.simulateMessage(JSON.stringify(message));
    flushSync();
  };
  deliver({ type: 'state_update', data: structuredClone(envelope) });
  // The store accepted the served document as it is.
  expect(store.getRadioState()).not.toBeNull();

  return {
    socket, flushSync, resetCommandLifecycle, deliver,
    agcFrames: () => socket.sent
      .map((raw) => JSON.parse(raw) as { id: string; name?: string; params: Record<string, unknown> })
      .filter((frame): frame is { id: string; name: string; params: Record<string, unknown> } =>
        frame.name === 'set_agc'),
    mount: async (load) => {
      const { default: component } = await load();
      const target = document.createElement('div');
      document.body.appendChild(target);
      const instance = mount(component as never, { target, props: {} });
      mounted.push(() => unmount(instance));
      flushSync();
      return target;
    },
  };
}

const loadPhone = () => import('../MobileRadioLayout.svelte');
afterAll(() => { vi.useRealTimers(); });
/** The desktop control: the sidebar's own `AgcPanel`, mounted bare. */
const loadDesktopAgc = async () => desktopAgcModule;

/** Mount the phone layout in portrait and open its DSP tab. */
async function openPhoneDspTab(session: Session): Promise<HTMLElement> {
  expect(await loadPhone()).toBe(phoneModule);
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 });
  const root = await session.mount(loadPhone);
  const chip = [...root.querySelectorAll<HTMLButtonElement>('.m-chip')]
    .find((el) => el.getAttribute('aria-controls') === 'm-chip-panel-dsp');
  expect(chip, 'the DSP chip').toBeDefined();
  chip!.click();
  session.flushSync();
  const panel = root.querySelector<HTMLElement>('#m-chip-panel-dsp');
  expect(panel, 'the DSP tab panel').not.toBeNull();
  return panel!;
}

const agcPanelIn = (tab: HTMLElement) =>
  tab.querySelector<HTMLElement>('[data-panel-id="m-agc-chip"]');
const keysIn = (scope: HTMLElement) => [...scope.querySelectorAll<HTMLButtonElement>('button')]
  .filter((b) => !b.classList.contains('panel-header'));
const labels = (keys: HTMLButtonElement[]) => keys.map((k) => k.textContent?.trim());
const lit = (scope: HTMLElement) => keysIn(scope)
  .filter((k) => k.getAttribute('aria-pressed') === 'true').map((k) => k.textContent?.trim());
const press = (scope: HTMLElement, label: string, session: Session) => {
  keysIn(scope).find((k) => k.textContent?.trim() === label)!.click();
  session.flushSync();
};

/** Radios with an AGC table and an observed state envelope, plus the choice
 *  each test makes: a key other than the one the golden state reports. */
const AGC_RADIOS = [
  { model: 'IC-705', choose: 'SLOW' },
  { model: 'X6200', choose: 'OFF' },
  { model: 'G7250', choose: 'SLOW' },
].filter(({ model }) => goldens[model]?.allObserved);
const NO_AGC_MODEL = 'TX-500';

beforeEach(() => {
  instances.length = 0;
  mounted = [];
  originalWebSocket = globalThis.WebSocket;
  // @ts-expect-error install the mock as the global WebSocket constructor
  globalThis.WebSocket = MockWebSocket;
});

afterEach(async () => {
  for (const unmountComponent of mounted) await unmountComponent();
  while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
  loadedWsClient?.disconnect();
  loadedWsClient = null;
  const { resetRadioState } = await import('$lib/stores/radio.svelte');
  const { clearCapabilities } = await import('$lib/stores/capabilities.svelte');
  const { resetCommandLifecycle } = await import('$lib/stores/commands.svelte');
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
  txHost.current = undefined;
  globalThis.WebSocket = originalWebSocket;
  vi.restoreAllMocks();
});

describe('phone DSP tab AGC choice (MOR-4528)', () => {
  it('covers radios whose served AGC tables differ, and one with none', () => {
    const tables = AGC_RADIOS.map(({ model }) => servedKeys(model).join('/'));
    expect(AGC_RADIOS.map(({ model }) => model)).toEqual(expect.arrayContaining(['IC-705', 'X6200']));
    expect(new Set(tables).size).toBeGreaterThanOrEqual(2);
    expect(servedKeys(NO_AGC_MODEL)).toEqual([]);
  });

  it.each(AGC_RADIOS)('$model: offers exactly the served AGC modes and lights the reported one', async ({ model }) => {
    const envelope = liveEnvelope(model);
    const session = await connectRadio(connectedCapabilities(model), envelope);

    const agc = agcPanelIn(await openPhoneDspTab(session));

    expect(agc, 'the AGC panel in the DSP tab').not.toBeNull();
    expect(labels(keysIn(agc!))).toEqual(servedKeys(model));
    const reported = (envelope.data.main as { agc: number }).agc;
    expect(lit(agc!)).toEqual([connectedCapabilities(model).agcLabels![String(reported)]]);
  });

  it.each(AGC_RADIOS)('$model: a pressed key becomes the lit one once the radio confirms it', async ({ model, choose }) => {
    const caps = connectedCapabilities(model);
    const session = await connectRadio(caps, liveEnvelope(model));
    const agc = agcPanelIn(await openPhoneDspTab(session))!;
    const before = lit(agc);
    expect(before).toHaveLength(1);
    expect(before).not.toEqual([choose]);
    const mode = Number(Object.keys(caps.agcLabels!).find((key) => caps.agcLabels![key] === choose));

    press(agc, choose, session);

    // On the wire, and not yet shown as selected: the lit key is the
    // confirmed reading, never the request.
    const frames = session.agcFrames();
    expect(frames).toHaveLength(1);
    expect(frames[0].params).toEqual({ mode, receiver: 0 });
    expect(lit(agc)).toEqual(before);

    session.deliver({ type: 'response', id: frames[0].id, ok: true, result: {} });
    expect(lit(agc)).toEqual(before);

    session.deliver({ type: 'state_update', data: confirmedEnvelope(model, mode) });
    expect(lit(agc)).toEqual([choose]);
  });

  it.each(AGC_RADIOS)('$model: the phone key sends the desktop control\'s command', async ({ model, choose }) => {
    const session = await connectRadio(connectedCapabilities(model), liveEnvelope(model));

    press(await session.mount(loadDesktopAgc), choose, session);
    const [desktop] = session.agcFrames();
    expect(desktop).toBeDefined();

    session.resetCommandLifecycle();
    press(agcPanelIn(await openPhoneDspTab(session))!, choose, session);
    const frames = session.agcFrames();
    expect(frames).toHaveLength(2);
    expect({ name: frames[1].name, params: frames[1].params })
      .toEqual({ name: desktop.name, params: desktop.params });
  });

  it('draws no AGC panel for a radio whose profile declares no AGC', async () => {
    const entry = goldens[NO_AGC_MODEL];
    const session = await connectRadio(
      validateCapabilities(structuredClone(entry.capabilities)), entry.allUnobserved!);

    const tab = await openPhoneDspTab(session);

    expect(agcPanelIn(tab)).toBeNull();
    expect(tab.querySelectorAll('.collapsible-panel')).toHaveLength(1);
  });

  it('draws no AGC panel while the server publishes no agc capability', async () => {
    // The radio-less document: the profile's AGC table is served, the tag
    // is not. The table alone must not draw the control.
    const entry = goldens['X6200'];
    expect(servedKeys('X6200').length).toBeGreaterThan(0);
    const session = await connectRadio(
      validateCapabilities(structuredClone(entry.capabilities)), entry.allObserved!);

    expect(agcPanelIn(await openPhoneDspTab(session))).toBeNull();
  });

  it('keeps one AGC home on the phone: the SETUP sheet no longer carries it', async () => {
    const session = await connectRadio(connectedCapabilities('IC-705'), liveEnvelope('IC-705'));
    const root = await session.mount(loadPhone);

    root.querySelector<HTMLButtonElement>('.m-settings-btn')!.click();
    session.flushSync();

    expect(document.body.querySelector('[data-panel-id="m-rit"]'), 'the SETUP sheet is open')
      .not.toBeNull();
    expect(document.body.querySelector('[data-panel-id="m-agc"]')).toBeNull();
  });
});
