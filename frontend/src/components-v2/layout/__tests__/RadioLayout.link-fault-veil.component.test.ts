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

/**
 * Splits a CSS selector list on the commas that are not inside parentheses,
 * so a `:not(a, b)` argument list survives intact.
 */
function splitSelectorList(text: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '(') depth++;
    else if (text[i] === ')') depth--;
    else if (text[i] === ',' && depth === 0) {
      out.push(text.slice(start, i));
      start = i + 1;
    }
  }
  out.push(text.slice(start));
  return out.map((s) => s.trim()).filter((s) => s !== '');
}

function stripGlobal(selector: string): string {
  const prefix = ':global(';
  if (!selector.startsWith(prefix) || !selector.endsWith(')')) return selector;
  return selector.slice(prefix.length, -1);
}

describe('RadioLayout link-fault veil (MOR-2425 C-R3)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let conn: typeof import('$lib/stores/connection.svelte');
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

  /**
   * "The link has been up at least once", which every case below except the
   * cold-start one needs: `hasEverConnected()` latches on the first
   * `setWsConnected(true)` and is never cleared, so a case that wants the
   * veil must establish it explicitly rather than inherit it from whichever
   * test happened to run first.
   */
  function linkHasBeenUp(): void {
    conn.setWsConnected(true);
  }

  function mountFace(skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2'): void {
    rt.state = structuredClone(stateFixture);
    rt.caps = structuredClone(capsFixture);
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

  function controlLinkLostBar(): Element | null {
    return faceRoot().querySelector('.control-link-lost');
  }

  function badLinkChip(): Element | null {
    return faceRoot().querySelector('[data-testid="bad-link-chip"]');
  }

  async function layoutSource(): Promise<string> {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    return readFileSync(
      resolve(process.cwd(), 'src/components-v2/layout/RadioLayout.svelte'), 'utf-8',
    );
  }

  /** The selectors of the one rule carrying the veil `filter`, unwrapped. */
  async function veilSelectors(): Promise<string[]> {
    const source = await layoutSource();
    const decl = source.indexOf('filter: saturate(');
    expect(decl, 'the veil declaration must be present').toBeGreaterThan(-1);
    const brace = source.lastIndexOf('{', decl);
    const prelude = source.slice(0, brace);
    // The rule is preceded by its explanatory comment; the selector list is
    // whatever follows that comment's terminator.
    const selectorText = prelude.slice(prelude.lastIndexOf('*/') + 2);
    return splitSelectorList(selectorText).map(stripGlobal);
  }

  /**
   * Every element under the face root except the StatusBar's own subtree.
   * The StatusBar is excluded because its bad-link chip (#3354) and its
   * `.control-link-lost` bar are deliberately `{#if}`-mounted — that is the
   * status bar's statement of the same condition, and this count is about
   * the face not moving.
   */
  function faceElementCount(): number {
    const chrome = [...faceRoot().querySelectorAll('.status-bar, .control-link-lost')];
    return [...faceRoot().querySelectorAll('*')]
      .filter((el) => !chrome.some((c) => c === el || c.contains(el)))
      .length;
  }

  /**
   * All the text the face itself renders, on the same exclusion. The veil
   * says nothing in words — the status bar carries both sentences — so this
   * string is invariant across the fault, and a sentence the veil put on the
   * face would change it.
   */
  function faceText(): string {
    return [...faceRoot().children]
      .filter((el) => !el.matches('.status-bar, .control-link-lost'))
      .map((el) => el.textContent)
      .join(' ');
  }

  // MUST be the first case in this file: `hasEverConnected()` is module-scoped
  // and never cleared, so any earlier case that connects the store would latch
  // it. The precondition below fails loudly if that ever stops holding.
  it('veils nothing before the first connect, though the WS is down', () => {
    expect(
      conn.hasEverConnected(),
      'precondition: this case must run before any case connects the store',
    ).toBe(false);
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().hasAttribute('data-link-fault')).toBe(false);
  });

  it('veils nothing while the WS is up and state updates are current', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().hasAttribute('data-link-fault')).toBe(false);
  });

  it('veils the face as radio-silent once state updates stall past the threshold', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();
    expect(faceRoot().hasAttribute('data-link-fault'), 'must not veil before the threshold').toBe(false);

    vi.advanceTimersByTime(6000);
    flushSync();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('radio-silent');
  });

  it('lifts the veil within one tick when a state_update lands', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();
    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('radio-silent');

    conn.markStateUpdated();
    flushSync();

    expect(faceRoot().hasAttribute('data-link-fault')).toBe(false);
  });

  it('veils the face as ws-down when a link that was up goes down', () => {
    linkHasBeenUp();
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('ws-down');
  });

  it('states ws-down, not radio-silent, when the transport is down and updates are also stale', () => {
    linkHasBeenUp();
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();

    expect(faceRoot().getAttribute('data-link-fault')).toBe('ws-down');
  });

  it('carries the fault on the face root and nowhere else', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();

    const faulted = target!.querySelectorAll('[data-link-fault]');
    expect(faulted).toHaveLength(1);
    expect(faulted[0]).toBe(faceRoot());
    // …and the rest of the face really is under that same element.
    expect(faceRoot().querySelector('.receiver-deck')).not.toBeNull();
  });

  // The veil says nothing itself; these two pin that whichever arm it reports,
  // the status bar is carrying the words for that same arm at that moment.
  it('shows the control-link-lost bar, and no chip, while the veil says ws-down', () => {
    linkHasBeenUp();
    conn.setWsConnected(false);
    conn.markStateUpdated();
    mountFace();

    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('ws-down');
    expect(controlLinkLostBar(), 'the bar states the ws-down arm').not.toBeNull();
    expect(badLinkChip()).toBeNull();
  });

  it('shows the bad-link chip, and no bar, while the veil says radio-silent', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();
    vi.advanceTimersByTime(6000);
    flushSync();

    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('radio-silent');
    expect(badLinkChip(), 'the chip states the radio-silent arm').not.toBeNull();
    expect(controlLinkLostBar()).toBeNull();
  });

  it('keeps the rendered values, the text and the element count unchanged across the fault', () => {
    linkHasBeenUp();
    conn.markStateUpdated();
    mountFace();
    const before = faceElementCount();
    const textBefore = faceText();
    const valuesBefore = faceRoot().querySelector('.receiver-deck')!.textContent;
    expect(valuesBefore, 'precondition: the deck must render some value text').not.toBe('');

    vi.advanceTimersByTime(6000);
    flushSync();
    expect(faceRoot().getAttribute('data-link-fault'), 'precondition').toBe('radio-silent');
    expect(faceElementCount()).toBe(before);
    expect(faceText()).toBe(textBefore);
    expect(faceRoot().querySelector('.receiver-deck')!.textContent).toBe(valuesBefore);

    conn.markStateUpdated();
    flushSync();
    expect(faceElementCount()).toBe(before);
    expect(faceText()).toBe(textBefore);
    expect(faceRoot().querySelector('.receiver-deck')!.textContent).toBe(valuesBefore);
  });

  it('declares one veil rule, keyed on the face root attribute', async () => {
    // jsdom computes no filter, so this pins the rule's presence by reading
    // the component source — a guard against the CSS being dropped, not a
    // check of how strong the veil looks. The AD judges the rendering.
    const selectors = await veilSelectors();
    expect(selectors.length).toBe(2);
    for (const selector of selectors) {
      expect(selector).toContain('.radio-layout[data-link-fault]');
    }
    expect(await layoutSource())
      .toMatch(/filter:\s*saturate\([^)]*\)\s*contrast\([^)]*\)\s*brightness\(/);
  });

  it('exempts the status-bar chrome from both veil selectors', async () => {
    const selectors = await veilSelectors();
    const root = document.createElement('div');
    root.className = 'radio-layout desktop-control-face semantic-deck standard-face';
    root.setAttribute('data-link-fault', 'ws-down');
    root.innerHTML = '<div class="control-link-lost"></div>'
      + '<div class="status-bar"><div class="np-backdrop"></div></div>'
      + '<section class="receiver-deck"><div class="desktop-controls-left"></div></section>';
    document.body.appendChild(root);
    try {
      const matchesVeil = (el: Element): boolean => selectors.some((s) => el.matches(s));

      // The exempt chrome: a filter here would re-anchor the `position: fixed`
      // popovers it hosts to the 28px strip.
      expect(matchesVeil(root.querySelector('.status-bar')!)).toBe(false);
      expect(matchesVeil(root.querySelector('.control-link-lost')!)).toBe(false);
      // The root itself is never filtered — only its children are.
      expect(matchesVeil(root)).toBe(false);
      // The deck is skipped so that its children, not it, carry the filter.
      expect(matchesVeil(root.querySelector('.receiver-deck')!)).toBe(false);
      expect(matchesVeil(root.querySelector('.desktop-controls-left')!)).toBe(true);
    } finally {
      root.remove();
    }
  });

  // Both desktop faces share the `.desktop-control-face` root class, and the
  // bar's placement rule is written against exactly that class — so the bar
  // is the first row on Standard and on SDR alike.
  it.each(['desktop-v2', 'sdr-test'] as const)(
    'roots %s on .desktop-control-face, which is what places the bar first',
    (skinId) => {
      linkHasBeenUp();
      conn.setWsConnected(false);
      conn.markStateUpdated();
      mountFace(skinId);

      const root = faceRoot();
      expect(root.classList.contains('desktop-control-face')).toBe(true);
      expect(root.classList.contains(skinId === 'sdr-test' ? 'sdr-test' : 'standard-face')).toBe(true);
      expect(controlLinkLostBar()?.parentElement).toBe(root);
    },
  );

  it('places the control-link-lost bar in the first grid row of that root', async () => {
    expect(await layoutSource()).toMatch(
      /\.desktop-control-face > :global\(\.control-link-lost\)\s*\{\s*grid-area:\s*1 \/ 1 \/ 2 \/ -1;/,
    );
  });
});
