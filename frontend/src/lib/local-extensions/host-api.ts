import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState, subscribeRadioState } from '$lib/stores/radio.svelte';
import { dispatchRadioIntentWithResult, type RadioIntent } from '$lib/runtime/commands/radio-intents';
import {
  resetLocalExtensionKeyboardScope,
  setLocalExtensionKeyboardScope,
} from './keyboard-scope';

export const LOCAL_EXTENSION_HOST_API_VERSION = 2;

export type RadioStateSubscriber = (state: ServerState | null) => void;

export interface LocalExtensionHostApiV2 {
  version: typeof LOCAL_EXTENSION_HOST_API_VERSION;
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

export interface LocalExtensionHostDependencies {
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
  render(container: HTMLElement, api: LocalExtensionHostApiV2): void | (() => void);
}

export interface LocalExtensionHostWindow extends Window {
  rigplaneExtensionHost?: LocalExtensionHostApiV2;
  /** @deprecated Naming alias for the same v2 API; use rigplaneExtensionHost. */
  icomLanExtensionHost?: LocalExtensionHostApiV2;
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
): LocalExtensionHostApiV2 {
  return {
    version: LOCAL_EXTENSION_HOST_API_VERSION,
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
): LocalExtensionHostApiV2 {
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
  api: LocalExtensionHostApiV2 = createDefaultLocalExtensionHostApi(),
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
