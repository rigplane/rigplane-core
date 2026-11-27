/**
 * MOR-4828 fix round (review RV-4825-r1 P2): the desktop refusal row must not
 * break the Standard desktop grid.
 *
 * `StatusBar.svelte` emits `.refusal-line` as a sibling of `.status-bar`, so
 * it is a direct grid item of `RadioLayout`'s `.desktop-control-face` root.
 * That root places only `.control-link-lost` (row 1) and `.status-bar`
 * (row 2) explicitly, so the new row auto-places into row 1, column 1 — the
 * 228px sidebar column — where the full sentence wraps and stretches the top
 * row across the whole desktop. The refusal row needs its own explicit
 * full-width health row in each consuming grid, coexisting with the
 * disconnected banner.
 *
 * Modelled on `RadioLayout.link-fault-veil.component.test.ts`: the REAL
 * connection store (its real staleness interval) runs under fake timers —
 * the shared virtual clock — and the REAL `refusedFieldEntries` + locale
 * chain renders the committed FTX-1 refusal golden grafted onto the IC-7300
 * face state. jsdom computes no layout, so placement itself is pinned by
 * reading the stylesheet source, the same way the veil test pins its rule.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import stateFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import capsFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';
import stateGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import { stubResizeObserver } from './support/stub-resize-observer';

stubResizeObserver();

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

const txHarness = new ManagedAppTxHarness({ stale: true });

vi.mock('$lib/runtime/tx-controller/managed-app-host', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/tx-controller/managed-app-host')>(),
  getManagedAppTxController: () => txHarness.controller,
}));

const rt = vi.hoisted(() => ({ state: null as unknown, caps: null as unknown }));

const runtimeMock = vi.hoisted(() => async () => {
  const conn = await import('$lib/stores/connection.svelte');
  return {
    presentationResources: null,
    runtime: {
      get state() { return rt.state; },
      get caps() { return rt.caps; },
      subscribeControlAuthority(handler: (publication: unknown) => void) {
        handler({
          state: rt.state, caps: rt.caps, session: { state: 'connected', epoch: 7 },
          rxAudioTarget: Object.freeze({ muted: false, rxEnabled: false }),
        });
        return () => {};
      },
      onTxAudioDied: () => () => {},
      controlSession: Object.freeze({ state: 'connected', epoch: 7 }) satisfies ControlSessionSnapshot,
      subscribeControlSession: () => () => {},
      connectionStatus: 'disconnected',
      radioPowerOn: null,
      connection: { status: 'disconnected', radioPowerOn: null },
      audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
      connectionAudio: false,
      system: {
        identifyFrequency: vi.fn(async () => ({ stations: [] })),
        connect: vi.fn(), disconnect: vi.fn(),
        powerOn: vi.fn(async () => {}), powerOff: vi.fn(async () => {}),
      },
      defaultScopeStatus: {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      },
      scope: { setFrameAuthority: vi.fn(), hardwareScopeConnected: false },
      get connectionStale() { return conn.isStale(); },
    },
  };
});

vi.mock('$lib/runtime', runtimeMock);
vi.mock('$lib/runtime/frontend-runtime', runtimeMock);

const EN_REFUSED =
  'The radio refused to report mic gain. The control is unavailable ' +
  'until the radio answers. The app is still connected.';

// Same budget rationale as the veil test: the dynamic imports pull the whole
// semantic surface graph through the on-demand Vite/Svelte transform.
const CI_HOOK_BUDGET_MS = 90_000;

describe('RadioLayout refusal-line grid placement (MOR-4828 fix round)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let conn: typeof import('$lib/stores/connection.svelte');
  let mount: typeof import('svelte')['mount'];
  let unmount: typeof import('svelte')['unmount'];
  let flushSync: typeof import('svelte')['flushSync'];
  let Fixture: (typeof import('./fixtures/HostedRadioLayoutFixture.svelte'))['default'];

  beforeAll(async () => {
    vi.useFakeTimers();
    conn = await import('$lib/stores/connection.svelte');
    ({ mount, unmount, flushSync } = await import('svelte'));
    ({ default: Fixture } = await import('./fixtures/HostedRadioLayoutFixture.svelte'));
  }, CI_HOOK_BUDGET_MS);

  afterAll(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    conn.setWsConnected(false);
    conn.setRigConnected(false);
    conn.setRadioHealth(null);
    conn.markStateUpdated();
    if (txHarness.listenerCount() === 0) txHarness.reset({ stale: true });
  });

  /** The real producer shape: IC-7300 face state with the committed FTX-1
   * refusal row grafted in, exactly as the scheduler + wire goldens emit it
   * (keyed row with messageKey/messageParams). */
  function refusedFaceState(): unknown {
    const state = structuredClone(stateFixture) as {
      fieldStatus: Record<string, unknown>;
    };
    state.fieldStatus['micGain'] = structuredClone(
      stateGoldens['FTX-1'].refusedMicGainFieldStatus,
    );
    return state;
  }

  function mountFace(skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2'): void {
    rt.state = refusedFaceState();
    rt.caps = structuredClone(capsFixture);
    conn.setRigConnected(true);
    conn.setRadioHealth(null);
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(Fixture, { target, props: { skinId } }) as object;
    flushSync();
  }

  function faceRoot(): Element {
    const root = target!.querySelector('.radio-layout');
    expect(root, 'precondition: the face root must be mounted').not.toBeNull();
    return root!;
  }

  function refusalLine(): Element | null {
    return faceRoot().querySelector('[data-testid="refusal-line"]');
  }

  async function layoutSource(): Promise<string> {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    return readFileSync(
      resolve(process.cwd(), 'src/components-v2/layout/RadioLayout.svelte'), 'utf-8',
    );
  }

  it.each(['desktop-v2', 'sdr-test'] as const)(
    'shows the refused golden sentence on %s without wrapping it in a sidebar column',
    (skinId) => {
      conn.setWsConnected(true);
      conn.markStateUpdated();
      mountFace(skinId);

      const root = faceRoot();
      expect(root.classList.contains('desktop-control-face')).toBe(true);
      const line = refusalLine();
      expect(line, 'the refusal reason must be visible on the desktop face').not.toBeNull();
      expect(line!.textContent).toBe(EN_REFUSED);
      // A wrapped row would make the line a descendant of a sidebar column;
      // as a grid item it must sit directly on the grid root.
      expect(line!.parentElement).toBe(root);
    },
  );

  it('gives the refusal row its own full-width health row below the disconnected banner', async () => {
    const source = await layoutSource();
    expect(source).toMatch(
      /\.desktop-control-face > :global\(\.refusal-line\)\s*\{\s*grid-area:\s*2 \/ 1 \/ 3 \/ -1;/,
    );
  });

  it('keeps the disconnected banner, the refusal row and the bar on distinct full-width rows', async () => {
    // Coexistence: disconnected AND refused at once. The banner keeps row 1,
    // the refusal row takes row 2, the bar moves to row 3.
    conn.setWsConnected(true);
    conn.markStateUpdated();
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();

    const root = faceRoot();
    const banner = root.querySelector('.control-link-lost');
    expect(banner, 'precondition: the disconnected banner must show').not.toBeNull();
    expect(banner!.parentElement).toBe(root);
    expect(refusalLine(), 'precondition: the refusal line must show').not.toBeNull();

    const source = await layoutSource();
    expect(source).toMatch(
      /\.desktop-control-face > :global\(\.control-link-lost\)\s*\{\s*grid-area:\s*1 \/ 1 \/ 2 \/ -1;/,
    );
    expect(source).toMatch(
      /\.desktop-control-face > :global\(\.status-bar\)\s*\{\s*grid-area:\s*3 \/ 1 \/ 4 \/ -1;/,
    );
  });

  it('gives the wide semantic-deck grid a full-width health area', async () => {
    const source = await layoutSource();
    expect(source).toMatch(/"health health health"/);
    expect(source).toMatch(
      /\.radio-layout\.semantic-deck:not\(\.desktop-control-face\) > :global\(\.refusal-line\)\s*\{\s*grid-area:\s*health;/,
    );
  });

  it('gives the stacked Standard band its own refusal row', async () => {
    const source = await layoutSource();
    const bandStart = source.indexOf('@media (max-width: 1024px)');
    expect(bandStart).toBeGreaterThan(-1);
    const stacked = source.slice(bandStart);
    expect(stacked).toMatch(
      /\.desktop-control-face\.standard-face > :global\(\.refusal-line\)\s*\{\s*grid-area:\s*2 \/ 1 \/ 3 \/ 2;/,
    );
  });
});
