import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Mock dependencies before importing the module under test
vi.mock('../../stores/tuning.svelte', () => ({
  tuneBy: vi.fn(() => 14_074_000),
}));

vi.mock('../../stores/radio.svelte', () => ({
  patchActiveReceiver: vi.fn(),
  getRadioState: vi.fn(() => ({ ptt: false })),
}));

vi.mock('../../transport/ws-client', () => ({
  sendCommand: vi.fn(() => true),
}));

// Minimal AudioContext mock
function createMockAudioContext() {
  const oscillator = {
    connect: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    disconnect: vi.fn(),
  };
  const gainNode = {
    gain: { value: 1 },
    connect: vi.fn(),
    disconnect: vi.fn(),
  };
  const ctx = {
    createOscillator: vi.fn(() => oscillator),
    createGain: vi.fn(() => gainNode),
    destination: {},
    close: vi.fn(() => Promise.resolve()),
  };
  return { ctx, oscillator, gainNode };
}

describe('media-session', () => {
  let mod: typeof import('../media-session');
  let tuningMod: typeof import('../../stores/tuning.svelte');
  let radioMod: typeof import('../../stores/radio.svelte');
  let wsMod: typeof import('../../transport/ws-client');

  const handlers = new Map<string, MediaSessionActionHandler | null>();
  let mockAudio: ReturnType<typeof createMockAudioContext>;

  beforeEach(async () => {
    vi.resetModules();
    handlers.clear();

    mockAudio = createMockAudioContext();
    // AudioContext and MediaMetadata are used with `new`, so mock as classes
    vi.stubGlobal(
      'AudioContext',
      class {
        createOscillator = mockAudio.ctx.createOscillator;
        createGain = mockAudio.ctx.createGain;
        destination = mockAudio.ctx.destination;
        close = mockAudio.ctx.close;
      },
    );
    vi.stubGlobal(
      'MediaMetadata',
      class {
        title: string;
        artist: string;
        constructor(opts: { title: string; artist: string }) {
          this.title = opts.title;
          this.artist = opts.artist;
        }
      },
    );

    // Install navigator.mediaSession mock
    const mediaSession = {
      metadata: null as any,
      setActionHandler: vi.fn((action: string, handler: MediaSessionActionHandler | null) => {
        handlers.set(action, handler);
      }),
    };
    Object.defineProperty(navigator, 'mediaSession', {
      value: mediaSession,
      writable: true,
      configurable: true,
    });

    mod = await import('../media-session');
    tuningMod = await import('../../stores/tuning.svelte');
    radioMod = await import('../../stores/radio.svelte');
    wsMod = await import('../../transport/ws-client');
  });

  afterEach(() => {
    mod.destroyMediaSession();
  });

  it('registers all four action handlers on init', () => {
    mod.initMediaSession();

    expect(handlers.has('previoustrack')).toBe(true);
    expect(handlers.has('nexttrack')).toBe(true);
    expect(handlers.has('play')).toBe(true);
    expect(handlers.has('pause')).toBe(true);
  });

  it('sets MediaSession metadata', () => {
    mod.initMediaSession();

    expect(navigator.mediaSession.metadata).toMatchObject({
      title: 'RigPlane',
      artist: 'Radio Control',
    });
  });

  it('starts silent audio context on init', () => {
    mod.initMediaSession();

    expect(mockAudio.ctx.createOscillator).toHaveBeenCalled();
    expect(mockAudio.ctx.createGain).toHaveBeenCalled();
    expect(mockAudio.gainNode.gain.value).toBe(0);
    expect(mockAudio.oscillator.connect).toHaveBeenCalledWith(mockAudio.gainNode);
    expect(mockAudio.gainNode.connect).toHaveBeenCalledWith(mockAudio.ctx.destination);
    expect(mockAudio.oscillator.start).toHaveBeenCalled();
  });

  it('previoustrack handler tunes down', () => {
    mod.initMediaSession();
    const handler = handlers.get('previoustrack')!;
    handler({ action: 'previoustrack' } as MediaSessionActionDetails);

    expect(tuningMod.tuneBy).toHaveBeenCalledWith(-1);
    expect(radioMod.patchActiveReceiver).toHaveBeenCalledWith({ freqHz: 14_074_000 }, true);
    expect(wsMod.sendCommand).toHaveBeenCalledWith('set_freq', { freq: 14_074_000, receiver: 0 });
  });

  it('nexttrack handler tunes up', () => {
    mod.initMediaSession();
    const handler = handlers.get('nexttrack')!;
    handler({ action: 'nexttrack' } as MediaSessionActionDetails);

    expect(tuningMod.tuneBy).toHaveBeenCalledWith(1);
    expect(radioMod.patchActiveReceiver).toHaveBeenCalledWith({ freqHz: 14_074_000 }, true);
    expect(wsMod.sendCommand).toHaveBeenCalledWith('set_freq', { freq: 14_074_000, receiver: 0 });
  });

  it('does not send command when tuneBy returns 0', () => {
    // Make tuneBy return 0 (no active receiver / freq unknown)
    vi.mocked(tuningMod.tuneBy).mockReturnValueOnce(0);

    mod.initMediaSession();
    const handler = handlers.get('previoustrack')!;

    // Clear any calls from initMediaSession setup
    vi.mocked(wsMod.sendCommand).mockClear();
    vi.mocked(radioMod.patchActiveReceiver).mockClear();

    handler({ action: 'previoustrack' } as MediaSessionActionDetails);

    expect(wsMod.sendCommand).not.toHaveBeenCalled();
    expect(radioMod.patchActiveReceiver).not.toHaveBeenCalled();
  });

  it('play handler sends PTT on', () => {
    mod.initMediaSession();
    const handler = handlers.get('play')!;
    handler({ action: 'play' } as MediaSessionActionDetails);

    expect(wsMod.sendCommand).toHaveBeenCalledWith('ptt', { state: true });
  });

  it('pause handler sends PTT off', () => {
    mod.initMediaSession();
    const handler = handlers.get('pause')!;
    handler({ action: 'pause' } as MediaSessionActionDetails);

    expect(wsMod.sendCommand).toHaveBeenCalledWith('ptt', { state: false });
  });

  it('destroyMediaSession clears handlers and stops audio', () => {
    mod.initMediaSession();
    mod.destroyMediaSession();

    // All handlers should be cleared (set to null)
    const setHandler = navigator.mediaSession.setActionHandler as ReturnType<typeof vi.fn>;
    const calls = setHandler.mock.calls as Array<[string, MediaSessionActionHandler | null]>;
    const nullCalls = calls.filter((call) => call[1] === null);
    expect(nullCalls.length).toBe(4);
    expect(mockAudio.oscillator.stop).toHaveBeenCalled();
    expect(mockAudio.ctx.close).toHaveBeenCalled();
  });

  describe('without MediaSession API', () => {
    beforeEach(() => {
      // Delete the property so 'mediaSession' in navigator === false
      delete (navigator as any).mediaSession;
    });

    it('initMediaSession is a no-op', () => {
      // Should not throw
      expect(() => mod.initMediaSession()).not.toThrow();
    });

    it('destroyMediaSession is a no-op', () => {
      expect(() => mod.destroyMediaSession()).not.toThrow();
    });
  });
});
