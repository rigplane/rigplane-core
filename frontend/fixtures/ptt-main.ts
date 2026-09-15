/** Verification-only managed-TX fixture: server snapshots plus delivery traces. */
import { mount, unmount } from 'svelte';
import { createManagedMobilePttSurface, type ManagedMobilePttBinding } from '../src/components-v2/wiring/managed-tx-gesture';
import PttFab from '../src/components-v2/controls/PttFab.svelte';
import { getPttMode, setPttMode } from './ptt-state.svelte';

type Snapshot = { phase: 'idle' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed'; intent: 'momentary' | 'latched' | null; radioTx: 'off' | 'on' | 'unknown'; txRisk: 'none' | 'uncertain' | 'confirmed-on'; fault: string | null; faultDetail: null; fresh: boolean; releaseRequired: boolean; remainingMs: number | null; lastOperation: 'ptt_on' | 'transmit_on' | 'force_receive' | null };
type Delivery = 'ws.ptt_on' | 'ws.ptt_off' | 'http.transmit_on' | 'http.force_off';
const rx = (): Snapshot => ({ phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null, lastOperation: 'force_receive' });
let state = rx();
const listeners = new Set<(s: Snapshot) => void>(); const deliveries: Delivery[] = [];
const emitServerSnapshot = (next: Snapshot) => { state = next; setPttMode(next.intent === 'latched' ? 'latched' : next.intent === 'momentary' ? 'held' : 'idle'); for (const f of listeners) f(next); };
const commands = {
  pttOn: () => { deliveries.push('ws.ptt_on'); },
  pttOff: () => { deliveries.push('ws.ptt_off'); },
  // The gesture reaches this transition only after a momentary PTT attempt.
  // Match ManagedTxController's cross-transport handoff exactly: release the
  // session-owned WS PTT before submitting the latched HTTP intent.
  transmitOn: () => { deliveries.push('ws.ptt_off', 'http.transmit_on'); },
  forceOff: () => { deliveries.push('http.force_off'); },
};
const host = { snapshot: () => state, subscribe: (f: (s: Snapshot) => void) => { listeners.add(f); return () => listeners.delete(f); }, ...commands };
let surface: 'portrait' | 'landscape' = 'portrait'; let generation = 0; let ptt: ManagedMobilePttBinding | null = null; let fab: object | null = null;
const portrait = document.getElementById('portrait-slot')!; const landscape = document.getElementById('landscape-slot')!;
function applySurface() { ptt?.destroy(); generation += 1; ptt = createManagedMobilePttSurface(surface, { latched: () => state.intent === 'latched', transmitAvailable: () => state.fresh }, host, { schedule: (f, ms) => setTimeout(f, ms), cancel: h => clearTimeout(h as ReturnType<typeof setTimeout>) }); const down = ptt.fabDown, up = ptt.fabUp; if (fab) unmount(fab); portrait.replaceChildren(); landscape.replaceChildren(); if (surface === 'portrait') fab = mount(PttFab, { target: portrait, props: { get mode() { return getPttMode(); }, txPermit: 'allowed', onDown: down, onUp: up } }); else { const b = document.createElement('button'); b.dataset.testid = 'ls-ptt'; b.style.cssText = 'min-width:52px;min-height:44px;'; b.textContent = 'PTT'; b.onpointerdown = () => ptt?.down(); b.onpointerup = b.onpointercancel = () => ptt?.up(); landscape.appendChild(b); } }
applySurface();
declare global { interface Window { __ptt: { setSurface(n: 'portrait' | 'landscape'): void; generation(): number; snapshot(): Snapshot; deliveryTrace(): Delivery[]; emitServerSnapshot(next: Snapshot): void; } } }
window.__ptt = { setSurface: n => { surface = n; applySurface(); }, generation: () => generation, snapshot: () => state, deliveryTrace: () => [...deliveries], emitServerSnapshot };
document.body.dataset.harnessReady = 'true';
