import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState, subscribeRadioState } from '$lib/stores/radio.svelte';
import { sendCommand } from '$lib/transport/ws-client';
import {
  resetLocalExtensionKeyboardScope,
  setLocalExtensionKeyboardScope,
} from './keyboard-scope';

export const LOCAL_EXTENSION_HOST_API_VERSION = 1;

export type RadioStateSubscriber = (state: ServerState | null) => void;

export interface LocalExtensionHostApiV1 {
  version: typeof LOCAL_EXTENSION_HOST_API_VERSION;
  getState(): ServerState | null;
  getCapabilities(): Capabilities | null;
  subscribeState(handler: RadioStateSubscriber): () => void;
  sendCommand(name: string, params?: Record<string, unknown>): boolean;
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
  render(container: HTMLElement, api: LocalExtensionHostApiV1): void | (() => void);
}

export interface LocalExtensionHostWindow extends Window {
  /** Primary v2.x global. Pro local-extensions written for rigplane should read this. */
  rigplaneExtensionHost?: LocalExtensionHostApiV1;
  /**
   * @deprecated Alias kept for v1.x extensions written against icom-lan.
   * Will be removed in a future major. New code should use
   * `rigplaneExtensionHost` instead.
   */
  icomLanExtensionHost?: LocalExtensionHostApiV1;
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
): LocalExtensionHostApiV1 {
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

export function createDefaultLocalExtensionHostApi(
  register: (extension: LocalExtensionRegistration) => void = () => {},
): LocalExtensionHostApiV1 {
  return createLocalExtensionHostApi({
    getState: getRadioState,
    getCapabilities,
    subscribeState: (handler) => subscribeRadioState(handler),
    dispatchCommand: sendCommand,
    setKeyboardScope: setLocalExtensionKeyboardScope,
    register,
  });
}

export function installLocalExtensionHostApi(
  targetWindow: LocalExtensionHostWindow = window as LocalExtensionHostWindow,
  api: LocalExtensionHostApiV1 = createDefaultLocalExtensionHostApi(),
): () => void {
  // Primary v2.x global.
  targetWindow.rigplaneExtensionHost = api;
  // Backwards-compat: Pro local-extensions written for v1.x icom-lan read
  // `window.icomLanExtensionHost`. Expose the same instance under both names
  // so existing extensions keep working without modification.
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
