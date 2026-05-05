export const LOCAL_EXTENSION_DOCK_LAYOUT_STORAGE_KEY = 'rigplane:local-extension-dock-layout:v1';
export const LOCAL_EXTENSION_DOCK_LAYOUT_VERSION = 1;

export type LocalExtensionDockMode =
  | 'floating'
  | 'dock-bottom'
  | 'dock-right'
  | 'dock-left'
  | 'collapsed';

export interface LocalExtensionDockPlacement {
  mode: LocalExtensionDockMode;
}

export interface LocalExtensionDockLayoutState {
  version: typeof LOCAL_EXTENSION_DOCK_LAYOUT_VERSION;
  extensions: Record<string, LocalExtensionDockPlacement>;
}

export type LocalExtensionDockStorage = Pick<Storage, 'getItem' | 'setItem'> | null | undefined;

export const DEFAULT_LOCAL_EXTENSION_DOCK_MODE: LocalExtensionDockMode = 'floating';

const SUPPORTED_DOCK_MODES = new Set<LocalExtensionDockMode>([
  'floating',
  'dock-bottom',
  'dock-right',
  'dock-left',
  'collapsed',
]);

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function createDefaultState(): LocalExtensionDockLayoutState {
  return {
    version: LOCAL_EXTENSION_DOCK_LAYOUT_VERSION,
    extensions: {},
  };
}

function defaultStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function isLocalExtensionDockMode(value: unknown): value is LocalExtensionDockMode {
  return typeof value === 'string' && SUPPORTED_DOCK_MODES.has(value as LocalExtensionDockMode);
}

export function normalizeLocalExtensionDockLayout(
  value: unknown,
): LocalExtensionDockLayoutState {
  const source = asObject(value);
  if (!source || source.version !== LOCAL_EXTENSION_DOCK_LAYOUT_VERSION) {
    return createDefaultState();
  }

  const rawExtensions = asObject(source.extensions);
  if (!rawExtensions) {
    return createDefaultState();
  }

  const extensions: Record<string, LocalExtensionDockPlacement> = {};
  for (const [id, rawPlacement] of Object.entries(rawExtensions)) {
    const placement = asObject(rawPlacement);
    if (id.trim() === '' || !placement || !isLocalExtensionDockMode(placement.mode)) {
      continue;
    }
    extensions[id] = { mode: placement.mode };
  }

  return {
    version: LOCAL_EXTENSION_DOCK_LAYOUT_VERSION,
    extensions,
  };
}

export function loadLocalExtensionDockLayout(
  storage: LocalExtensionDockStorage = defaultStorage(),
): LocalExtensionDockLayoutState {
  if (!storage) {
    return createDefaultState();
  }

  try {
    const item = storage.getItem(LOCAL_EXTENSION_DOCK_LAYOUT_STORAGE_KEY);
    return item ? normalizeLocalExtensionDockLayout(JSON.parse(item)) : createDefaultState();
  } catch {
    return createDefaultState();
  }
}

export function saveLocalExtensionDockLayout(
  state: LocalExtensionDockLayoutState,
  storage: LocalExtensionDockStorage = defaultStorage(),
): void {
  if (!storage) {
    return;
  }

  try {
    storage.setItem(
      LOCAL_EXTENSION_DOCK_LAYOUT_STORAGE_KEY,
      JSON.stringify(normalizeLocalExtensionDockLayout(state)),
    );
  } catch {
    // localStorage can be unavailable or quota-limited; layout persistence is best-effort.
  }
}

export function getLocalExtensionDockMode(
  state: LocalExtensionDockLayoutState,
  id: string,
): LocalExtensionDockMode {
  return state.extensions[id]?.mode ?? DEFAULT_LOCAL_EXTENSION_DOCK_MODE;
}

export function setLocalExtensionDockMode(
  state: LocalExtensionDockLayoutState,
  id: string,
  mode: LocalExtensionDockMode,
): LocalExtensionDockLayoutState {
  if (id.trim() === '' || !isLocalExtensionDockMode(mode)) {
    return normalizeLocalExtensionDockLayout(state);
  }

  return {
    version: LOCAL_EXTENSION_DOCK_LAYOUT_VERSION,
    extensions: {
      ...normalizeLocalExtensionDockLayout(state).extensions,
      [id]: { mode },
    },
  };
}
