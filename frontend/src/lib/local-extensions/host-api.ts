import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState, subscribeRadioState } from '$lib/stores/radio.svelte';
import { dispatchRadioIntentWithResult, type RadioIntent } from '$lib/runtime/commands/radio-intents';
import { makeRxAudioHandlers } from '$lib/runtime/commands/panel-commands';
import { setLayoutMode } from '$lib/stores/layout.svelte';
import { getAvailableThemes, setThemeUserChoice } from '../../components-v2/theme/theme-switcher';
import {
  getHostUi, getHostSnapshot, subscribeHostSnapshot, getLayoutOptions,
  subscribeListening,
  type HostSnapshot, type ListeningSnapshot,
} from './host-ui';
import { readListeningSnapshot } from './host-listening';
export type { HostSnapshot, ListeningSnapshot, LocalExtensionHostDeclaration } from './host-ui';
import {
  resetLocalExtensionKeyboardScope,
  setLocalExtensionKeyboardScope,
} from './keyboard-scope';

export const LOCAL_EXTENSION_HOST_API_VERSION = 3;

export type RadioStateSubscriber = (state: ServerState | null) => void;

export interface LocalExtensionHostApiV2 {
  version: 2;
  getState(): ServerState | null;
  getCapabilities(): Capabilities | null;
  subscribeState(handler: RadioStateSubscriber): () => void;
  /** Returns the client transport result, not radio delivery, server admission or completion. */
  sendCommand(name: string, params?: Record<string, unknown>): boolean;
  /** Same acceptance result as sendCommand. */
  dispatchCommand(name: string, params?: Record<string, unknown>): boolean;
  setKeyboardScope(scope: string | null): void;
  register(extension: LocalExtensionRegistration): void;
}

/** v3 adds UI requests; v2 command booleans retain transport-only semantics. */
export interface LocalExtensionHostApiV3 extends Omit<LocalExtensionHostApiV2, 'version'> {
  version: 3;
  connect(): boolean;
  disconnect(): boolean;
  setRadioPower(on: boolean): boolean;
  openRadioControls(): boolean;
  getLayouts(): ReturnType<typeof getLayoutOptions>;
  setLayout(id: string): boolean;
  getThemes(): ReturnType<typeof getAvailableThemes>;
  setTheme(id: string): boolean;
  getHostSnapshot(): Readonly<HostSnapshot>;
  subscribeHostSnapshot(handler: (snapshot: Readonly<HostSnapshot>) => void): () => void;
  getListening(): Readonly<ListeningSnapshot>;
  setListeningMode(mode: ListeningSnapshot['mode']): boolean;
  setListeningLevel(level: number): boolean;
  subscribeListening(handler: (snapshot: Readonly<ListeningSnapshot>) => void): () => void;
}

export interface LocalExtensionHostDependencies {
  getListening?: () => Readonly<ListeningSnapshot>;
  getState: () => ServerState | null;
  getCapabilities: () => Capabilities | null;
  subscribeState: (handler: RadioStateSubscriber) => () => void;
  dispatchCommand: (name: string, params?: Record<string, unknown>) => boolean;
  setKeyboardScope: (scope: string | null) => void;
  register: (extension: LocalExtensionRegistration) => void;
}

export interface LocalExtensionRegistration {
  id: string;
  title?: string;
  mount?: string;
  render(container: HTMLElement, api: LocalExtensionHostApiV3): void | (() => void);
}

export interface LocalExtensionHostWindow extends Window {
  rigplaneExtensionHost?: LocalExtensionHostApiV2 | LocalExtensionHostApiV3;
  /** @deprecated Naming alias for the same API; use rigplaneExtensionHost. */
  icomLanExtensionHost?: LocalExtensionHostApiV2 | LocalExtensionHostApiV3;
}

function cloneParams(params: Record<string, unknown> | undefined): Record<string, unknown> {
  return params ? { ...params } : {};
}

function dispatchVia(
  deps: LocalExtensionHostDependencies,
  name: string,
  params: Record<string, unknown> | undefined,
): boolean {
  if (typeof name !== 'string' || name.trim() === '') {
    return false;
  }
  return deps.dispatchCommand(name, cloneParams(params));
}

export function createLocalExtensionHostApi(
  deps: LocalExtensionHostDependencies,
): LocalExtensionHostApiV3 {
  const getListening = deps.getListening ?? readListeningSnapshot;
  return {
    version: LOCAL_EXTENSION_HOST_API_VERSION,
    connect: () => getHostUi()?.connect() ?? false,
    disconnect: () => getHostUi()?.disconnect() ?? false,
    setRadioPower: (on) => typeof on === 'boolean' && (getHostUi()?.setRadioPower(on) ?? false),
    openRadioControls: () => getHostUi()?.openRadioControls() ?? false,
    getLayouts: () => getLayoutOptions(),
    setLayout(id) {
      const option = getLayoutOptions().find(option => option.value === id);
      if (!option) return false;
      setLayoutMode(option.value);
      return true;
    },
    getThemes: () => getAvailableThemes().map(theme => ({ ...theme, preview: [...theme.preview] })),
    setTheme(id) {
      if (!getAvailableThemes().some(theme => theme.id === id)) return false;
      setThemeUserChoice(id);
      return true;
    },
    getHostSnapshot,
    subscribeHostSnapshot,
    getListening,
    subscribeListening,
    setListeningMode(mode) {
      const listening = getListening();
      if (!listening.enabled || !['local', 'live', 'mute'].includes(mode)) return false;
      if (mode === 'live' && !deps.getCapabilities()?.capabilities.includes('audio')) return false;
      makeRxAudioHandlers().onMonitorModeChange(mode);
      return true;
    },
    setListeningLevel(level) {
      const listening = getListening();
      if (!listening.enabled || !Number.isFinite(level) || level < 0 || level > listening.maxLevel
        || listening.level === null) return false;
      makeRxAudioHandlers().onAfLevelChange(level);
      return true;
    },
    getState: deps.getState,
    getCapabilities: deps.getCapabilities,
    subscribeState: deps.subscribeState,
    sendCommand(name, params) {
      return dispatchVia(deps, name, params);
    },
    dispatchCommand(name, params) {
      return dispatchVia(deps, name, params);
    },
    setKeyboardScope(scope) {
      deps.setKeyboardScope(scope);
    },
    register(extension) {
      deps.register(extension);
    },
  };
}

/**
 * Default extension dispatch (MOR-1409 A08): catalog-validated delegation to
 * the typed intent facade. Unknown names, PTT, and malformed params raise a
 * validation error inside the facade before any transport is reached — the
 * host API fails closed with `false`.
 */
function dispatchThroughIntentFacade(
  name: string,
  params?: Record<string, unknown>,
): boolean {
  if (typeof name !== 'string' || name.trim() === '') {
    return false;
  }
  try {
    return dispatchRadioIntentWithResult({ name, params: params ?? {} } as RadioIntent).transportAccepted;
  } catch {
    return false;
  }
}

export function createDefaultLocalExtensionHostApi(
  register: (extension: LocalExtensionRegistration) => void = () => {},
): LocalExtensionHostApiV3 {
  return createLocalExtensionHostApi({
    getState: getRadioState,
    getCapabilities,
    subscribeState: (handler) => subscribeRadioState(handler),
    dispatchCommand: dispatchThroughIntentFacade,
    setKeyboardScope: setLocalExtensionKeyboardScope,
    register,
  });
}

export function installLocalExtensionHostApi(
  targetWindow: LocalExtensionHostWindow = window as LocalExtensionHostWindow,
  api: LocalExtensionHostApiV2 | LocalExtensionHostApiV3 = createDefaultLocalExtensionHostApi(),
): () => void {
  targetWindow.rigplaneExtensionHost = api;
  targetWindow.icomLanExtensionHost = api;
  return () => {
    if (targetWindow.rigplaneExtensionHost === api) {
      delete targetWindow.rigplaneExtensionHost;
    }
    if (targetWindow.icomLanExtensionHost === api) {
      delete targetWindow.icomLanExtensionHost;
    }
    resetLocalExtensionKeyboardScope();
  };
}
