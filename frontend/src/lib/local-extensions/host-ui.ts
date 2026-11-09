import type { CanonicalLayoutMode } from '$lib/stores/layout.svelte';
import { LOCAL_SUPERVISOR_META_NAME } from './manifest';
import { runtime } from '$lib/runtime';
import { getRadioPowerOn } from '$lib/stores/connection.svelte';
import { hasCapability } from '$lib/stores/capabilities.svelte';

export function powerRequestAllowed(on: boolean, origin?: 'powered-off-overlay'): boolean {
  if (runtime.remoteController) return false;
  const power = getRadioPowerOn();
  if (origin === 'powered-off-overlay') {
    return on && runtime.caps?.powerOnCommand === true
      && runtime.radioHealth?.likelyCause !== 'radio_not_connected'
      && (power === false || (power !== true
        && runtime.radioHealth?.likelyCause === 'radio_powered_off_likely'));
  }
  return hasCapability('power_control') && power !== null && power !== on;
}

/** Injected by the embedding host, never by an extension manifest. */
export interface LocalExtensionHostDeclaration {
  version: 1;
  report: boolean;
  language: boolean;
  /** The host supplies connection health and connect/disconnect controls. */
  health?: boolean;
}

export function parseHostDeclaration(value: unknown): LocalExtensionHostDeclaration {
  const declaration = value as Partial<LocalExtensionHostDeclaration> | null;
  const valid = declaration?.version === 1 && typeof declaration.report === 'boolean' && typeof declaration.language === 'boolean';
  return { version: 1, report: valid && declaration.report === true,
    language: valid && declaration.language === true,
    health: valid && declaration.health === true };
}

export function readHostDeclaration(doc: Document | undefined = globalThis.document): LocalExtensionHostDeclaration {
  try {
    return parseHostDeclaration(JSON.parse(doc?.querySelector<HTMLMetaElement>(
      `meta[name="${LOCAL_SUPERVISOR_META_NAME}"]`,
    )?.content ?? 'null'));
  } catch {
    return parseHostDeclaration(null);
  }
}

export interface HostSnapshot {
  connected: boolean;
  power: boolean | null;
  powerSupported: boolean;
  layout: string;
  theme: string;
}

export interface ListeningSnapshot {
  mode: 'local' | 'live' | 'mute';
  /** Normalized radio AF, or browser gain (which may exceed unity). */
  level: number | null;
  target: 'radio' | 'playback';
  enabled: boolean;
  maxLevel: number;
}

export interface HostUi {
  /** true means the UI accepted the request, not that an action completed. */
  connect(): boolean;
  disconnect(): boolean;
  setRadioPower(on: boolean, origin?: 'powered-off-overlay'): boolean;
  openRadioControls(): boolean;
}

let ui: HostUi | null = null;
let radioControls: (() => void) | undefined;
export function bindHostRadioControls(open: (() => void) | undefined): () => void {
  radioControls = open;
  return () => { if (radioControls === open) radioControls = undefined; };
}
export function openHostRadioControls(): boolean {
  if (!radioControls) return false;
  radioControls();
  return true;
}
export function bindHostUi(binding: HostUi): () => void {
  ui = binding;
  return () => { if (ui === binding) ui = null; };
}
export function getHostUi(): HostUi | null { return ui; }

function channel<T>(initial: T) {
  let current = Object.freeze(initial);
  const subscribers = new Set<(snapshot: Readonly<T>) => void>();
  return {
    get: () => current,
    publish(next: T) {
      if (JSON.stringify(next) === JSON.stringify(current)) return;
      current = Object.freeze({ ...next });
      for (const subscriber of subscribers) {
        try { subscriber(current); } catch (error) { console.warn('Extension subscriber failed', error); }
      }
    },
    subscribe(handler: (snapshot: Readonly<T>) => void) {
      subscribers.add(handler);
      try { handler(current); } catch (error) { subscribers.delete(handler); throw error; }
      return () => { subscribers.delete(handler); };
    },
  };
}

const host = channel<HostSnapshot>({ connected: false, power: null, powerSupported: false, layout: 'auto', theme: 'default' });
const listening = channel<ListeningSnapshot>({ mode: 'local', level: null, target: 'radio', enabled: false, maxLevel: 1 });
export const getHostSnapshot = host.get;
export const publishHostSnapshot = host.publish;
export const subscribeHostSnapshot = host.subscribe;
export const getListening = listening.get;
export const publishListening = listening.publish;
export const subscribeListening = listening.subscribe;

const skinOptions: Array<{ value: CanonicalLayoutMode; label: string }> = [
  { value: 'auto', label: 'AUTO' },
  { value: 'standard', label: 'Standard' },
  { value: 'lcd-cockpit', label: 'LCD Cockpit' },
  { value: 'lcd-scope', label: 'LCD Scope' },
  { value: 'peer-split', label: 'LCD Peer Split' },
  { value: 'unified-instrument', label: 'LCD Unified Instrument' },
  { value: 'panadapter-first', label: 'LCD Panadapter' },
  { value: 'sdr-test', label: 'SDR Screen (test)' },
  { value: 'dual-sdr-face', label: 'Dual SDR Face' },
];
export function getLayoutOptions(development = import.meta.env.DEV) {
  return skinOptions.filter(option => development || option.value !== 'sdr-test').map(option => ({ ...option }));
}
