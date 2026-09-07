/**
 * MOR-2425 C-R3 — the link-fault veil on the face.
 *
 * Modelled on the sibling `StatusBar.bad-link-chip.component.test.ts`: the
 * REAL staleness mechanism in `$lib/stores/connection.svelte` (its real
 * `setInterval`, its real `markStateUpdated()`) is driven under fake timers
 * rather than stubbed, so a regression in the wiring between the store and
 * `RadioLayout.svelte` cannot hide behind a mock that returns whatever the
 * test wants. That store is therefore NOT mocked here (unlike
 * `RadioLayout.isolated.test.ts`), and the `$lib/runtime` mock's
 * `connectionStale` getter forwards to it, so both paths the layout reads
 * observe one shared staleness state.
 *
 * App modules are imported dynamically, once, in `beforeAll`, AFTER
 * `vi.useFakeTimers()` — `connection.svelte.ts` registers its staleness
 * interval at module load, and only a timer scheduled after fake timers are
 * installed can be driven by `vi.advanceTimersByTime`.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import stateFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import capsFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';

vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

vi.mock('$lib/transport/ws-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/ws-client')>(),
  getControlSession: () => ({ state: 'connected' as const, epoch: 7 }),
  sendCommand: () => true,
}));

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
      scope: { hardwareScopeConnected: false },
      // Forwards to the real, unmocked store — see the file header.
      get connectionStale() { return conn.isStale(); },
    },
  };
});

vi.mock('$lib/runtime', runtimeMock);
vi.mock('$lib/runtime/frontend-runtime', runtimeMock);

describe('RadioLayout link-fault veil (MOR-2425 C-R3)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let conn: typeof import('$lib/stores/connection.svelte');
  let t: typeof import('$lib/i18n')['t'];
  let mount: typeof import('svelte')['mount'];
  let unmount: typeof import('svelte')['unmount'];
  let flushSync: typeof import('svelte')['flushSync'];
  let Fixture: (typeof import('./fixtures/HostedRadioLayoutFixture.svelte'))['default'];

  // Explicit budget rather than vitest's 10s default: these dynamic imports
  // pull the whole semantic surface graph, which is far heavier than the
  // four the sibling chip test loads.
  beforeAll(async () => {
    vi.useFakeTimers();
    conn = await import('$lib/stores/connection.svelte');
    ({ t } = await import('$lib/i18n'));
    ({ mount, unmount, flushSync } = await import('svelte'));
    ({ default: Fixture } = await import('./fixtures/HostedRadioLayoutFixture.svelte'));
  }, 30_000);

  afterAll(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    conn.setWsConnected(false);
    conn.markStateUpdated();
    if (txHarness.listenerCount() === 0) txHarness.reset({ stale: true });
  });

  function mountFace(): void {
    rt.state = structuredClone(stateFixture);
    rt.caps = structuredClone(capsFixture);
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(Fixture, { target, props: { skinId: 'desktop-v2' } }) as object;
    flushSync();
  }

  function faceRoot(): Element {
    const root = target!.querySelector('.radio-layout');
    expect(root, 'precondition: the face root must be mounted').not.toBeNull();
    return root!;
  }

  function statement(): Element {
    const el = target!.querySelector('[data-link-fault-statement]');
    expect(el, 'the statement slot is always present').not.toBeNull();
    return el!;
  }

  /**
   * Every element under the face root except the StatusBar's own subtree.
   * The StatusBar is excluded because its bad-link chip (#3354) is
   * deliberately `{#if}`-mounted — that is the status bar's statement of the
   * same condition, and this count is about the face not moving.
   */
  function faceElementCount(): number {
    const chrome = [...faceRoot().querySelectorAll('.status-bar, .control-link-lost')];
    return [...faceRoot().querySelectorAll('*')]
      .filter((el) => !chrome.some((c) => c === el || c.contains(el)))
      .length;
  }

  it('veils nothing while the WS is up and state updates are current', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().hasAttribute('data-link-fault')).toBe(false);
    expect(statement().textContent).toBe('');
  });

  it('veils the face as radio-silent once state updates stall past the threshold', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountFace();
    expect(faceRoot().hasAttribute('data-link-fault'), 'must not veil before the threshold').toBe(false);

    vi.advanceTimersByTime(6000);
    flushSync();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('radio-silent');
    expect(statement().textContent).toBe(t('core.linkFault.radioSilent'));
  });

  it('lifts the veil within one tick when a state_update lands', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();
    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('radio-silent');

    conn.markStateUpdated();
    flushSync();

    expect(faceRoot().hasAttribute('data-link-fault')).toBe(false);
    expect(statement().textContent).toBe('');
  });

  it('veils the face as ws-down when the transport itself is down', () => {
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('ws-down');
    expect(statement().textContent).toBe(t('core.linkFault.wsDown'));
  });

  it('states ws-down, not radio-silent, when the transport is down and updates are also stale', () => {
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('ws-down');
    expect(statement().textContent).toBe(t('core.linkFault.wsDown'));
  });

  it('carries the fault on the face root, with the statement as its own child', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();

    const faulted = target!.querySelectorAll('[data-link-fault]');
    expect(faulted).toHaveLength(1);
    expect(faulted[0]).toBe(faceRoot());
    // The statement is a CHILD of the element the attribute sits on: a CSS
    // filter applies to the whole subtree of the box carrying it, so the
    // statement can only stay unfiltered by being excluded at this level.
    expect(statement().parentElement).toBe(faceRoot());
    // …and the rest of the face really is under that same element.
    expect(faceRoot().querySelector('.receiver-deck')).not.toBeNull();
  });

  it('keeps the rendered values and the element count unchanged across the fault', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountFace();
    const before = faceElementCount();
    const valuesBefore = faceRoot().querySelector('.receiver-deck')!.textContent;
    expect(valuesBefore, 'precondition: the deck must render some value text').not.toBe('');

    vi.advanceTimersByTime(6000);
    flushSync();
    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('radio-silent');
    expect(faceElementCount()).toBe(before);
    expect(faceRoot().querySelector('.receiver-deck')!.textContent).toBe(valuesBefore);

    conn.markStateUpdated();
    flushSync();
    expect(faceElementCount()).toBe(before);
    expect(faceRoot().querySelector('.receiver-deck')!.textContent).toBe(valuesBefore);
  });

  it('declares one veil rule that filters the face contents and exempts the statement', async () => {
    // jsdom computes no filter, so this pins the rule's presence by reading
    // the component source — a guard against the CSS being dropped, not a
    // check of how strong the veil looks. The AD judges the rendering.
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const source = readFileSync(
      resolve(process.cwd(), 'src/components-v2/layout/RadioLayout.svelte'), 'utf-8',
    );
    const rule = source.match(
      /:global\(\.radio-layout\[data-link-fault\][^}]*\{[^}]*\}/,
    );
    expect(rule, 'the veil rule must be keyed on the face root attribute').not.toBeNull();
    expect(rule![0]).toContain(':not([data-link-fault-statement]');
    expect(rule![0]).toMatch(/filter:\s*saturate\([^)]*\)\s*contrast\(/);
  });
});
