import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  resolveBrowserAudioRoute,
  type BrowserAudioRouteDescriptor,
  type BrowserMediaDeviceInfo,
} from '../browser-audio-route';

const descriptor: BrowserAudioRouteDescriptor = {
  routeId: 'example-loopback',
  input: { labelIncludes: ['Example Bridge Capture'] },
  output: { labelIncludes: ['Example Bridge Playback'] },
  preferExplicitOutput: true,
};

const neutralFixtures = [
  'Example Bridge Capture',
  'Example Bridge Playback',
  'Laptop Microphone',
  'USB Headphones',
];

function device(
  kind: BrowserMediaDeviceInfo['kind'],
  label: string,
  scopedDeviceId: string,
): BrowserMediaDeviceInfo {
  return {
    kind,
    label,
    deviceId: scopedDeviceId,
    groupId: `group-${scopedDeviceId}`,
  };
}

function mediaDevices(options: {
  beforePermission?: BrowserMediaDeviceInfo[];
  afterPermission?: BrowserMediaDeviceInfo[];
  rejectPermission?: boolean;
}) {
  let permissionGranted = false;
  const stream = {
    getTracks: () => [{ stop: vi.fn() }],
  } as unknown as MediaStream;

  return {
    getUserMedia: vi.fn(async () => {
      if (options.rejectPermission) {
        throw new DOMException('denied', 'NotAllowedError');
      }
      permissionGranted = true;
      return stream;
    }),
    enumerateDevices: vi.fn(async () => (
      permissionGranted
        ? (options.afterPermission ?? [])
        : (options.beforePermission ?? [])
    )),
  };
}

describe('browser audio route model', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requests microphone permission before relying on labels, then selects matched endpoints', async () => {
    const media = mediaDevices({
      beforePermission: [
        device('audioinput', '', 'scoped-input-before'),
        device('audiooutput', '', 'scoped-output-before'),
      ],
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input-after'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output-after'),
      ],
    });
    const sinkTarget = { setSinkId: vi.fn(async () => undefined) };

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
      sinkTarget,
    });

    expect(media.getUserMedia).toHaveBeenCalledBefore(media.enumerateDevices);
    expect(route.status).toBe('selected');
    if (route.status !== 'selected') throw new Error('expected selected route');
    expect(route.input.label).toBe('Example Bridge Capture');
    expect(route.output.label).toBe('Example Bridge Playback');
    expect(route.input.scopedDeviceId).toBe('scoped-input-after');
    expect(route.output.scopedDeviceId).toBe('scoped-output-after');
  });

  it('prefers explicit output devices over default and communications aliases', async () => {
    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input'),
        device('audiooutput', 'Example Bridge Playback', 'default'),
        device('audiooutput', 'Example Bridge Playback', 'communications'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output-explicit'),
      ],
    });

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
      sinkTarget: { setSinkId: vi.fn(async () => undefined) },
    });

    expect(route.status).toBe('selected');
    if (route.status !== 'selected') throw new Error('expected selected route');
    expect(route.output.scopedDeviceId).toBe('scoped-output-explicit');
  });

  it('returns unsupported-output-selection when a requested output route cannot use setSinkId', async () => {
    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output'),
      ],
    });

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
      sinkTarget: {},
    });

    expect(route).toMatchObject({
      status: 'unsupported-output-selection',
      outputSelectionSupported: false,
    });
  });

  it('feature-detects output selection support from the media element prototype', async () => {
    vi.stubGlobal('HTMLMediaElement', class {
      setSinkId() {
        return Promise.resolve();
      }
    });
    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output'),
      ],
    });

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
    });

    expect(route.status).toBe('selected');
  });

  it('returns permission-denied when microphone permission is rejected', async () => {
    const media = mediaDevices({ rejectPermission: true });

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
      sinkTarget: { setSinkId: vi.fn(async () => undefined) },
    });

    expect(route).toMatchObject({
      status: 'permission-denied',
      permissionState: 'denied',
    });
    expect(media.enumerateDevices).not.toHaveBeenCalled();
  });

  it('returns missing-endpoints with explicit missing directions', async () => {
    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Laptop Microphone', 'scoped-input'),
        device('audiooutput', 'USB Headphones', 'scoped-output'),
      ],
    });

    const route = await resolveBrowserAudioRoute({
      descriptor,
      mediaDevices: media,
      sinkTarget: { setSinkId: vi.fn(async () => undefined) },
    });

    expect(route).toMatchObject({
      status: 'missing-endpoints',
      missing: ['input', 'output'],
      permissionState: 'granted',
    });
  });

  it('keeps anonymized fixtures and ignores productMode descriptor data', async () => {
    for (const fixture of neutralFixtures) {
      expect(fixture).toMatch(/^(Example|Laptop|USB) /);
    }

    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output'),
      ],
    });
    const route = await resolveBrowserAudioRoute({
      descriptor: { ...descriptor, productMode: 'ignored-host-mode' } as BrowserAudioRouteDescriptor,
      mediaDevices: media,
      sinkTarget: { setSinkId: vi.fn(async () => undefined) },
    });

    expect(route.status).toBe('selected');
  });

  it('treats malformed host descriptors as untrusted data without throwing', async () => {
    const media = mediaDevices({
      afterPermission: [
        device('audioinput', 'Example Bridge Capture', 'scoped-input'),
        device('audiooutput', 'Example Bridge Playback', 'scoped-output'),
      ],
    });

    const route = await resolveBrowserAudioRoute({
      descriptor: {
        routeId: 123,
        input: { labelIncludes: [null, {}, ''] },
        output: { labelIncludes: 'Example Bridge Playback' },
      } as unknown as BrowserAudioRouteDescriptor,
      mediaDevices: media,
      sinkTarget: { setSinkId: vi.fn(async () => undefined) },
    });

    expect(route).toMatchObject({
      status: 'missing-endpoints',
      missing: ['input', 'output'],
    });
  });
});
