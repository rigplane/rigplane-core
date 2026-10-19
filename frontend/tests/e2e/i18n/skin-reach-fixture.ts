/**
 * Shared harness for the per-skin reachability matrix (MOR-3280).
 *
 * Boots the REAL built app (served by `playwright.i18n.config.ts`'s preview
 * server) against an IN-PAGE fake WebSocket — no Python, no radio, in the
 * style of `ritxit-offset-keys.spec.ts` / `desktop-geometry.spec.ts`: the
 * fake socket fires open, emits ONE full `state_update` on `/api/v1/ws`, and
 * records every `send()` frame to `window.skinReachFrames`. HTTP `/api/**`
 * is answered from `page.route`, never the network.
 *
 * The state is `fixtureById('topology-2-main-sub')` (`fixtures/catalog.ts`)
 * merged onto `mockState` (`./fixtures.ts`), with every leaf marked
 * observed+fresh by `observeAll` — the same synthetic-provenance pattern
 * `desktop-geometry.spec.ts`'s `fixture(true)` uses — so every semantic
 * control that is structurally present under these caps is also enabled
 * (`radio-view-model-adapter.ts: seen()` requires observed+fresh). State
 * and capabilities carry the catalog's `providerGeneration: 1` on BOTH
 * sides, which is what the interaction authority gates
 * (`SemanticRadioSurfaces.svelte: bandFiniteAuthority`,
 * `openFrequencyEntry`) require before a control may dispatch.
 */
import type { Page } from '@playwright/test';
import { fixtureById } from '../../../fixtures/catalog';
import { fulfillControllerRoute, mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ReceiverState, ServerState } from '../../../src/lib/types/state';

export interface SkinReachBoot {
  /** `rigplane:workspace` v2 `layout` value (a CanonicalLayoutMode). */
  layout?: string;
  /** QA-only skins selected by the exact `?layout=` query param
   * (`lib/stores/qa-cockpit-override.ts`); wins over the workspace. */
  qaLayout?: 'flagship-probe' | 'dual-receiver-cockpit';
  /** Viewport for this row. Default 1440x1000 (desktop); a viewport with
   * min(width, height) < 640 (e.g. 390x844) makes `App.svelte`'s `isMobile`
   * true, which `registry.ts: resolveSkinId` maps to the mobile skin. */
  viewport?: { width: number; height: number };
  /** Fields merged over BOTH receivers of the fixture state, before every
   * leaf is marked observed. */
  receiverOverrides?: Partial<ReceiverState>;
}

/** One outgoing frame exactly as the app serialises it (`ws-client.ts`). */
export interface SkinReachFrame {
  type: string;
  name: string;
  params: Record<string, unknown>;
}

export function skinReachFixture(
  receiverOverrides: Partial<ReceiverState> = {},
): { state: ServerState; caps: Capabilities } {
  const fixture = fixtureById('topology-2-main-sub');
  const topologyState = fixture?.state();
  const topologyCaps = fixture?.caps();
  if (!topologyState || !topologyCaps) throw new Error('missing topology-2-main-sub fixture');
  const state = {
    ...structuredClone(mockState),
    ...structuredClone(topologyState),
    main: { ...structuredClone(mockState.main), ...structuredClone(topologyState.main), ...receiverOverrides },
    sub: { ...structuredClone(mockState.sub), ...structuredClone(topologyState.sub), ...receiverOverrides },
  } as ServerState;
  state.updatedAt = new Date().toISOString();
  // Synthetic observation provenance for this harness, not a radio
  // measurement: every leaf of the merged state is observed+fresh, so the
  // reachability matrix tests wiring, not the observation gates.
  const fields: Record<string, unknown> = {};
  const observeAll = (obj: object, prefix = '') => {
    for (const [key, value] of Object.entries(obj)) {
      const path = prefix ? `${prefix}.${key}` : key;
      fields[path] = { storePath: path, observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 0 };
      if (value && typeof value === 'object') observeAll(value, path);
    }
  };
  delete (state as unknown as { fieldStatus?: unknown }).fieldStatus;
  observeAll(state);
  state.fieldStatus = fields as typeof state.fieldStatus;
  const caps: Capabilities = { ...structuredClone(mockCapabilities), ...structuredClone(topologyCaps) };
  return { state, caps };
}

export async function bootSkinReach(page: Page, boot: SkinReachBoot): Promise<void> {
  const { state, caps } = skinReachFixture(boot.receiverOverrides);
  await page.setViewportSize(boot.viewport ?? { width: 1440, height: 1000 });
  await page.addInitScript(({ state, layout }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 2, layout,
      designLanguageBySkin: {}, theme: 'nord' }));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
    const frames: unknown[] = [];
    Object.assign(window, { skinReachFrames: frames });
    class Socket extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
      readyState = 0; binaryType = 'arraybuffer'; bufferedAmount = 0;
      // Real local Core accepts the client's controller subprotocol
      // (docs/api/web.md); without the echo `ws-client.ts: _openSocket`
      // closes the channel right after open.
      protocol = '';
      onopen: ((e: Event) => void) | null = null;
      onmessage: ((e: MessageEvent) => void) | null = null;
      onclose: ((e: Event) => void) | null = null;
      constructor(public url: string, protocols: string[] = []) {
        super();
        this.protocol = protocols[0] ?? '';
        queueMicrotask(() => {
          this.readyState = 1;
          const open = new Event('open'); this.dispatchEvent(open); this.onopen?.(open);
          if (new URL(url, location.href).pathname === '/api/v1/ws') {
            const e = new MessageEvent('message', { data: JSON.stringify({ type: 'state_update',
              data: { type: 'full', data: state, revision: state.revision,
                stateRevision: state.stateRevision, freshnessRevision: state.freshnessRevision,
                observationSeq: state.observationSeq, stateContractVersion: state.stateContractVersion,
                providerGeneration: state.providerGeneration } }) });
            this.dispatchEvent(e); this.onmessage?.(e);
          }
        });
      }
      send(data: string) {
        const frame = JSON.parse(data);
        frames.push(frame);
        if (frame.type === 'controller_heartbeat') {
          // Real Core acks primary heartbeats; without acks the client
          // drops the session once the 6 s TTL passes. The generation is
          // the frozen `mockControllerGrant.generation`.
          const e = new MessageEvent('message', { data: JSON.stringify({ type: 'controller_heartbeat', generation: 0 }) });
          this.dispatchEvent(e); this.onmessage?.(e);
        }
      }
      close() { this.readyState = 3; const e = new Event('close'); this.dispatchEvent(e); this.onclose?.(e); }
    }
    Object.assign(window, { WebSocket: Socket });
  }, { state, layout: boot.layout });
  await page.route('**/api/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/v1/controller') return fulfillControllerRoute(route);
    const name = pathname.split('/').pop();
    const body = name === 'state' ? state : name === 'capabilities' ? caps : name === 'info' ? mockInfo
      // Same document shape `desktop-geometry.spec.ts` serves: the client
      // rejects anything missing schemaVersion/sampledAt
      // (`managed-transmit.ts: validateManagedTransmitDocument`).
      : name === 'managed-transmit' ? { schemaVersion: 1, sampledAt: new Date().toISOString(),
        managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
          lastError: null, lastActuation: null, abortErrors: [],
          tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
        txObservation: { observedPtt: 'off' } } : {};
    return route.fulfill({ json: body });
  });
  await page.goto(`/?locale=en-US${boot.qaLayout ? `&layout=${boot.qaLayout}` : ''}`, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
}

/** Every `cmd` frame recorded so far, `{name, params}` only. */
export async function skinReachCommands(
  page: Page,
): Promise<Pick<SkinReachFrame, 'name' | 'params'>[]> {
  return page.evaluate(() => (window as unknown as { skinReachFrames: SkinReachFrame[] })
    .skinReachFrames.filter((frame) => frame.type === 'cmd')
    .map(({ name, params }) => ({ name, params })));
}

/** Drop all recorded frames so the next probe's poll sees only new ones. */
export async function clearSkinReachFrames(page: Page): Promise<void> {
  await page.evaluate(() => { (window as unknown as { skinReachFrames: unknown[] }).skinReachFrames.length = 0; });
}
