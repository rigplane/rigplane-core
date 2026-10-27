/** Verification-only managed-TX fixture: server snapshots plus delivery traces. */
import { mount, unmount } from 'svelte';
import { createManagedTxGesture, createPttHold } from '../src/components-v2/wiring/managed-tx-gesture';
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
// One gesture and one hold for the page, as in MobileRadioLayout.svelte: a
// surface swap remounts only the button, so a hold outlives it (MOR-3817).
const gesture = createManagedTxGesture({ latched: () => state.intent === 'latched', transmitAvailable: () => state.fresh }, host, { schedule: (f, ms) => setTimeout(f, ms), cancel: h => clearTimeout(h as ReturnType<typeof setTimeout>) });
const pttHold = createPttHold({ press: () => gesture.down(), release: () => gesture.up(), abort: () => gesture.abort() });
let surface: 'portrait' | 'landscape' = 'portrait'; let generation = 0; let fab: object | null = null; let lsAction: { destroy(): void } | null = null;
const portrait = document.getElementById('portrait-slot')!; const landscape = document.getElementById('landscape-slot')!;
function applySurface() { generation += 1; if (fab) unmount(fab); fab = null; lsAction?.destroy(); lsAction = null; portrait.replaceChildren(); landscape.replaceChildren(); if (surface === 'portrait') fab = mount(PttFab, { target: portrait, props: { get mode() { return getPttMode(); }, txPermit: 'allowed', hold: pttHold } }); else { const b = document.createElement('button'); b.dataset.testid = 'ls-ptt'; b.style.cssText = 'min-width:52px;min-height:44px;'; b.textContent = 'PTT'; lsAction = pttHold.attach(b); landscape.appendChild(b); } }
applySurface();
declare global { interface Window { __ptt: { setSurface(n: 'portrait' | 'landscape'): void; generation(): number; snapshot(): Snapshot; deliveryTrace(): Delivery[]; emitServerSnapshot(next: Snapshot): void; } } }
window.__ptt = { setSurface: n => { surface = n; applySurface(); }, generation: () => generation, snapshot: () => state, deliveryTrace: () => [...deliveries], emitServerSnapshot };
document.body.dataset.harnessReady = 'true';
