export type BrowserAudioDeviceKind = 'audioinput' | 'audiooutput';

export interface BrowserMediaDeviceInfo {
  kind: BrowserAudioDeviceKind;
  deviceId: string;
  label: string;
  groupId?: string;
}

export interface BrowserAudioDeviceMatcher {
  labelIncludes?: readonly string[];
  groupId?: string;
}

export interface BrowserAudioRouteDescriptor {
  routeId: string;
  input?: BrowserAudioDeviceMatcher;
  output?: BrowserAudioDeviceMatcher;
  preferExplicitOutput?: boolean;
  [key: string]: unknown;
}

export interface BrowserAudioRouteEndpoint {
  kind: BrowserAudioDeviceKind;
  label: string;
  groupId?: string;
  /**
   * Browser-scoped id for immediate getUserMedia/setSinkId use.
   * Do not persist this as a stable OS or product identity.
   */
  scopedDeviceId: string;
  isAlias: boolean;
}

export type BrowserAudioRouteState =
  | {
      status: 'unsupported-output-selection';
      outputSelectionSupported: false;
      permissionState: 'prompt';
    }
  | {
      status: 'permission-denied';
      outputSelectionSupported: boolean;
      permissionState: 'denied';
    }
  | {
      status: 'missing-endpoints';
      outputSelectionSupported: boolean;
      permissionState: 'granted';
      missing: Array<'input' | 'output'>;
      input?: BrowserAudioRouteEndpoint;
      output?: BrowserAudioRouteEndpoint;
    }
  | {
      status: 'selected';
      outputSelectionSupported: boolean;
      permissionState: 'granted';
      routeId: string;
      input: BrowserAudioRouteEndpoint;
      output: BrowserAudioRouteEndpoint;
    };

export interface BrowserAudioRouteResolveOptions {
  descriptor: BrowserAudioRouteDescriptor;
  mediaDevices: {
    getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
    enumerateDevices(): Promise<readonly BrowserMediaDeviceInfo[]>;
  };
  sinkTarget?: unknown;
}

const DEVICE_ID_ALIASES = new Set(['default', 'communications']);
const MAX_MATCH_TERMS = 8;
const MAX_TERM_LENGTH = 160;
const MAX_ROUTE_ID_LENGTH = 120;

function hasOutputSelectionSupport(target: unknown): boolean {
  if (
    typeof target === 'object'
    && target !== null
    && typeof (target as { setSinkId?: unknown }).setSinkId === 'function'
  ) {
    return true;
  }

  return (
    typeof HTMLMediaElement !== 'undefined'
    && typeof HTMLMediaElement.prototype.setSinkId === 'function'
  );
}

function safeText(value: unknown, maxLength = MAX_TERM_LENGTH): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.slice(0, maxLength);
}

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function safeMatcher(value: unknown): Required<Pick<BrowserAudioDeviceMatcher, 'labelIncludes'>> & {
  groupId?: string;
} {
  if (typeof value !== 'object' || value === null) return { labelIncludes: [] };
  const raw = value as BrowserAudioDeviceMatcher;
  const labelIncludes = Array.isArray(raw.labelIncludes)
    ? raw.labelIncludes
      .slice(0, MAX_MATCH_TERMS)
      .map((term) => safeText(term))
      .filter((term): term is string => term !== null)
      .map(normalize)
    : [];
  const groupId = safeText(raw.groupId);
  return groupId ? { labelIncludes, groupId } : { labelIncludes };
}

function safeRouteId(descriptor: BrowserAudioRouteDescriptor): string {
  return safeText(descriptor.routeId, MAX_ROUTE_ID_LENGTH) ?? 'browser-audio-route';
}

function deviceMatches(
  device: BrowserMediaDeviceInfo,
  matcher: ReturnType<typeof safeMatcher>,
): boolean {
  if (matcher.groupId && device.groupId !== matcher.groupId) return false;
  if (matcher.labelIncludes.length === 0) return false;
  const label = normalize(device.label);
  if (!label) return false;
  return matcher.labelIncludes.every((term) => label.includes(term));
}

function toEndpoint(device: BrowserMediaDeviceInfo): BrowserAudioRouteEndpoint {
  return {
    kind: device.kind,
    label: device.label,
    groupId: device.groupId,
    scopedDeviceId: device.deviceId,
    isAlias: DEVICE_ID_ALIASES.has(device.deviceId),
  };
}

function chooseDevice(
  devices: readonly BrowserMediaDeviceInfo[],
  kind: BrowserAudioDeviceKind,
  matcher: ReturnType<typeof safeMatcher>,
  preferExplicit: boolean,
): BrowserMediaDeviceInfo | undefined {
  const candidates = devices.filter((device) => (
    device.kind === kind && deviceMatches(device, matcher)
  ));
  if (!preferExplicit) return candidates[0];
  return candidates.find((device) => !DEVICE_ID_ALIASES.has(device.deviceId)) ?? candidates[0];
}

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) {
    track.stop();
  }
}

export async function resolveBrowserAudioRoute(
  options: BrowserAudioRouteResolveOptions,
): Promise<BrowserAudioRouteState> {
  const outputSelectionSupported = hasOutputSelectionSupport(options.sinkTarget);
  if (options.descriptor.output && !outputSelectionSupported) {
    return {
      status: 'unsupported-output-selection',
      outputSelectionSupported: false,
      permissionState: 'prompt',
    };
  }

  let stream: MediaStream;
  try {
    stream = await options.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return {
      status: 'permission-denied',
      outputSelectionSupported,
      permissionState: 'denied',
    };
  }

  stopTracks(stream);

  const devices = await options.mediaDevices.enumerateDevices();
  const inputMatcher = safeMatcher(options.descriptor.input);
  const outputMatcher = safeMatcher(options.descriptor.output);
  const inputDevice = chooseDevice(devices, 'audioinput', inputMatcher, false);
  const outputDevice = chooseDevice(
    devices,
    'audiooutput',
    outputMatcher,
    options.descriptor.preferExplicitOutput === true,
  );
  const input = inputDevice ? toEndpoint(inputDevice) : undefined;
  const output = outputDevice ? toEndpoint(outputDevice) : undefined;
  const missing: Array<'input' | 'output'> = [];

  if (!input) missing.push('input');
  if (!output) missing.push('output');

  if (missing.length > 0 || !input || !output) {
    return {
      status: 'missing-endpoints',
      outputSelectionSupported,
      permissionState: 'granted',
      missing,
      input,
      output,
    };
  }

  return {
    status: 'selected',
    outputSelectionSupported,
    permissionState: 'granted',
    routeId: safeRouteId(options.descriptor),
    input,
    output,
  };
}
