/** Verification-only managed-TX fixture: snapshot truth plus fake WS/HTTP delivery. */
import { mount, unmount } from 'svelte';
import { createManagedMobilePttSurface, type ManagedMobilePttBinding } from '../src/components-v2/wiring/managed-tx-gesture';
import PttFab from '../src/components-v2/controls/PttFab.svelte';
import { getPttMode, setPttMode } from './ptt-state.svelte';

type Snapshot = { phase: 'idle' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed'; intent: 'momentary' | 'latched' | null; radioTx: 'off' | 'on' | 'unknown'; txRisk: 'none' | 'uncertain' | 'confirmed-on'; fault: string | null; faultDetail: null; fresh: boolean; releaseRequired: boolean; remainingMs: number | null; lastOperation: 'ptt_on' | 'transmit_on' | 'force_receive' | null };
const rx = (): Snapshot => ({ phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null, lastOperation: 'force_receive' });
let state = rx();
const listeners = new Set<(s: Snapshot) => void>(); const calls: string[] = [];
const publish = () => { setPttMode(state.intent === 'latched' ? 'latched' : state.intent === 'momentary' ? 'held' : 'idle'); for (const f of listeners) f(state); };
const set = (next: Snapshot) => { state = next; publish(); };
const commands = {
  pttOn: () => { calls.push('ws.ptt_on'); set({ ...state, phase: 'key-confirm-pending', intent: 'momentary', txRisk: 'uncertain', lastOperation: 'ptt_on' }); },
  pttOff: () => { calls.push('ws.ptt_off'); set(rx()); },
  transmitOn: () => { if (!state.fresh) return; if (state.intent === 'momentary') calls.push('ws.ptt_off'); calls.push('http.transmit_on'); set({ ...state, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', lastOperation: 'transmit_on' }); },
  forceOff: () => { calls.push('http.force_off'); set(rx()); },
};
const host = { snapshot: () => state, subscribe: (f: (s: Snapshot) => void) => { listeners.add(f); return () => listeners.delete(f); }, ...commands };
let surface: 'portrait' | 'landscape' = 'portrait'; let generation = 0; let ptt: ManagedMobilePttBinding | null = null; let fab: object | null = null;
const portrait = document.getElementById('portrait-slot')!; const landscape = document.getElementById('landscape-slot')!;
function applySurface() { ptt?.destroy(); generation += 1; ptt = createManagedMobilePttSurface(surface, { latched: () => state.intent === 'latched', transmitAvailable: () => state.fresh }, host, { schedule: (f, ms) => setTimeout(f, ms), cancel: h => clearTimeout(h as ReturnType<typeof setTimeout>) }); const down = ptt.fabDown, up = ptt.fabUp; if (fab) unmount(fab); portrait.replaceChildren(); landscape.replaceChildren(); if (surface === 'portrait') fab = mount(PttFab, { target: portrait, props: { get mode() { return getPttMode(); }, txPermit: 'allowed', onDown: down, onUp: up } }); else { const b = document.createElement('button'); b.dataset.testid = 'ls-ptt'; b.style.cssText = 'min-width:52px;min-height:44px;'; b.textContent = 'PTT'; b.onpointerdown = () => ptt?.down(); b.onpointerup = b.onpointercancel = () => ptt?.up(); landscape.appendChild(b); } }
applySurface();
declare global { interface Window { __ptt: { setSurface(n: 'portrait' | 'landscape'): void; generation(): number; guardId(): string | null; intent(): Snapshot['intent']; callCount(): number; callsSince(n: number): string[]; epochBump(): void; } } }
window.__ptt = { setSurface: n => { surface = n; applySurface(); }, generation: () => generation, guardId: () => state.intent === null ? null : 'canonical', intent: () => state.intent, callCount: () => calls.length, callsSince: n => calls.slice(n), epochBump: () => set({ ...state, phase: 'idle', intent: null, radioTx: 'unknown', txRisk: 'none', fresh: false, releaseRequired: false, lastOperation: null }) };
document.body.dataset.harnessReady = 'true';
