export const LOCAL_EXTENSION_MANIFEST_URL = '/api/local/v1/ui/manifest';
export const LOCAL_EXTENSION_MANIFEST_VERSION = 1;
export const LOCAL_EXTENSION_HOST_API_VERSION = '2.0';

/**
 * MOR-2242 — `/api/local/v1/*` is the Pro station-supervisor surface and does
 * not exist on a core-only server. The browser logs a console error for every
 * failed fetch regardless of how page JS handles the response, so the UI must
 * not fire any `/api/local` request unless the supervisor is known to serve
 * it. The supervisor advertises itself by injecting this meta tag into the
 * served page; the tag is plain DOM, so no CSP allowance is needed for it.
 */
export const LOCAL_SUPERVISOR_META_NAME = 'rigplane-local-supervisor';

/** True when the local (Pro) supervisor advertises itself on the served page. */
export function localSupervisorAdvertised(doc?: Document): boolean {
  const document_ = doc ?? globalThis.document;
  return document_?.querySelector(
    `meta[name="${LOCAL_SUPERVISOR_META_NAME}"]`,
  ) != null;
}

export type LocalExtensionMount = 'floating-overlay';

export interface LocalExtensionDescriptor {
  id: string;
  entry: string;
  style?: string;
  mount: LocalExtensionMount;
  title?: string;
  requires?: string[];
}

export interface LocalExtensionManifest {
  version: typeof LOCAL_EXTENSION_MANIFEST_VERSION;
  host_api: typeof LOCAL_EXTENSION_HOST_API_VERSION;
  extensions: LocalExtensionDescriptor[];
}

export interface LoadManifestOptions {
  fetch?: typeof globalThis.fetch;
  url?: string;
  baseUrl?: string;
  /** Override the supervisor-advertisement check (tests pin both outcomes). */
  supervisorAdvertised?: boolean;
}

const SUPPORTED_MOUNTS = new Set<LocalExtensionMount>(['floating-overlay']);

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function currentBaseUrl(): string {
  return globalThis.location?.href ?? 'http://localhost/';
}

function normalizeSameOriginPath(value: unknown, baseUrl: string): string | null {
  if (typeof value !== 'string' || value.trim() === '') {
    return null;
  }

  try {
    const base = new URL(baseUrl);
    const url = new URL(value, base);
    if (url.origin !== base.origin) {
      return null;
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export function parseLocalExtensionManifest(
  value: unknown,
  baseUrl: string = currentBaseUrl(),
): LocalExtensionManifest | null {
  const manifest = asObject(value);
  if (!manifest || manifest.version !== LOCAL_EXTENSION_MANIFEST_VERSION) {
    return null;
  }

  if (manifest.host_api !== LOCAL_EXTENSION_HOST_API_VERSION) {
    return null;
  }

  if (!Array.isArray(manifest.extensions)) {
    return null;
  }

  const extensions: LocalExtensionDescriptor[] = [];

  for (const candidate of manifest.extensions) {
    const item = asObject(candidate);
    if (!item || typeof item.id !== 'string' || item.id.trim() === '') {
      continue;
    }

    const mount = item.mount;
    if (typeof mount !== 'string' || !SUPPORTED_MOUNTS.has(mount as LocalExtensionMount)) {
      continue;
    }

    const entry = normalizeSameOriginPath(item.entry, baseUrl);
    if (!entry) {
      continue;
    }

    const style = item.style === undefined
      ? undefined
      : normalizeSameOriginPath(item.style, baseUrl) ?? undefined;
    const requires = Array.isArray(item.requires)
      ? item.requires.filter((requirement): requirement is string => typeof requirement === 'string')
      : undefined;

    extensions.push({
      id: item.id,
      entry,
      style,
      mount: mount as LocalExtensionMount,
      title: typeof item.title === 'string' && item.title.trim() !== '' ? item.title : undefined,
      requires,
    });
  }

  if (extensions.length === 0) {
    return null;
  }

  return {
    version: LOCAL_EXTENSION_MANIFEST_VERSION,
    host_api: LOCAL_EXTENSION_HOST_API_VERSION,
    extensions,
  };
}

export async function loadLocalExtensionManifest(
  options: LoadManifestOptions = {},
): Promise<LocalExtensionManifest | null> {
  const fetcher = options.fetch ?? globalThis.fetch;
  if (typeof fetcher !== 'function') {
    return null;
  }

  // MOR-2242: without a supervisor advertisement there is nothing to load,
  // and firing the request on a core-only server would log a console 404.
  if (!(options.supervisorAdvertised ?? localSupervisorAdvertised())) {
    return null;
  }

  let response: Response;
  try {
    response = await fetcher(options.url ?? LOCAL_EXTENSION_MANIFEST_URL, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch {
    return null;
  }

  if (!response.ok) {
    return null;
  }

  try {
    const value: unknown = await response.json();
    const manifest = asObject(value);
    if (manifest?.version === LOCAL_EXTENSION_MANIFEST_VERSION
      && manifest.host_api !== LOCAL_EXTENSION_HOST_API_VERSION) {
      console.warn(
        '[local-extensions] Extension manifest rejected: explicit host_api "2.0" is required. '
        + 'Migrate to host version 2 canonical command names and parameters, including required receivers. '
        + 'Command booleans report client transport acceptance only; PTT commands remain unsupported.',
      );
    }
    return parseLocalExtensionManifest(value, options.baseUrl);
  } catch {
    return null;
  }
}
