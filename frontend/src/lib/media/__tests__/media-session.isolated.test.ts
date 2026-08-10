import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const runtimeHarness = vi.hoisted(() => ({
  runtime: {
    state: Object.freeze({ identity: 'media-state' }),
    caps: Object.freeze({ identity: 'media-capabilities' }),
  },
}));

const viewHarness = vi.hoisted(() => {
  const harness = {
    current: null as any,
    toRadioViewModel: vi.fn(() => harness.current),
  };
  return harness;
});

const tuningHarness = vi.hoisted(() => ({
  step: 1_000,
  getTuningStep: vi.fn(() => 1_000),
  snapToStep: vi.fn((frequency: number) => Math.round(frequency / 1_000) * 1_000),
}));

const vfoHarness = vi.hoisted(() => {
  const handlers = Object.freeze({
    onMainVfoClick: vi.fn(),
    onSubVfoClick: vi.fn(),
    onFreqChange: vi.fn(),
  });
  return {
    handlers,
    getVfoHandlers: vi.fn(() => handlers),
  };
});

const legacyAlarm = vi.hoisted(() => ({
  tuneBy: vi.fn(),
  patchActiveReceiver: vi.fn(),
  sendCommand: vi.fn(),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: runtimeHarness.runtime }));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: viewHarness.toRadioViewModel,
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getVfoHandlers: vfoHarness.getVfoHandlers,
}));
vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: tuningHarness.getTuningStep,
  snapToStep: tuningHarness.snapToStep,
  tuneBy: legacyAlarm.tuneBy,
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  patchActiveReceiver: legacyAlarm.patchActiveReceiver,
  getRadioState: vi.fn(),
}));
vi.mock('$lib/transport/ws-client', () => ({ sendCommand: legacyAlarm.sendCommand }));

function activeView(
  receiver: 'MAIN' | 'SUB' = 'MAIN',
  frequencyHz: number | null = 14_074_000,
  slot: Record<string, unknown> = { kind: 'slotted', id: 'A' },
) {
  return Object.freeze({
    activeReceiver: Object.freeze({ status: 'known' as const, receiver }),
    vfos: Object.freeze([
      Object.freeze({ receiver, isActive: true, frequencyHz, slot: Object.freeze(slot) }),
    ]),
  });
}

function createMockAudioContext() {
  const oscillator = {
    connect: vi.fn(), start: vi.fn(), stop: vi.fn(), disconnect: vi.fn(),
  };
  const gainNode = {
    gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn(),
  };
  const ctx = {
    createOscillator: vi.fn(() => oscillator),
    createGain: vi.fn(() => gainNode),
    destination: {},
    close: vi.fn(() => Promise.resolve()),
  };
  return { ctx, oscillator, gainNode };
}

describe('media-session cycle-safe radio authority', () => {
  let mod: typeof import('../media-session');
  const handlers = new Map<string, MediaSessionActionHandler | null>();
  const existingPlayHandler = vi.fn<MediaSessionActionHandler>();
  const existingPauseHandler = vi.fn<MediaSessionActionHandler>();
  let mockAudio: ReturnType<typeof createMockAudioContext>;
  let audioContexts: ReturnType<typeof createMockAudioContext>[];

  function installMediaSession(): void {
    handlers.clear();
    handlers.set('play', existingPlayHandler);
    handlers.set('pause', existingPauseHandler);
    Object.defineProperty(navigator, 'mediaSession', {
      value: {
        metadata: null,
        setActionHandler: vi.fn((action: string, handler: MediaSessionActionHandler | null) => {
          handlers.set(action, handler);
        }),
      },
      writable: true,
      configurable: true,
    });
  }

  function fire(action: 'previoustrack' | 'nexttrack'): void {
    const handler = handlers.get(action);
    if (!handler) throw new Error(`${action} handler not installed`);
    handler({ action } as MediaSessionActionDetails);
  }

  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    viewHarness.current = activeView();
    tuningHarness.step = 1_000;
    tuningHarness.getTuningStep.mockImplementation(() => tuningHarness.step);
    tuningHarness.snapToStep.mockImplementation(
      (frequency: number) => Math.round(frequency / tuningHarness.step) * tuningHarness.step,
    );
    vfoHarness.getVfoHandlers.mockImplementation(() => vfoHarness.handlers);

    mockAudio = createMockAudioContext();
    audioContexts = [];
    vi.stubGlobal('AudioContext', class {
      createOscillator;
      createGain;
      destination;
      close;

      constructor() {
        const audio = audioContexts.length === 0 ? mockAudio : createMockAudioContext();
        audioContexts.push(audio);
        this.createOscillator = audio.ctx.createOscillator;
        this.createGain = audio.ctx.createGain;
        this.destination = audio.ctx.destination;
        this.close = audio.ctx.close;
      }
    });
    vi.stubGlobal('MediaMetadata', class {
      title: string;
      artist: string;
      constructor(options: { title: string; artist: string }) {
        this.title = options.title;
        this.artist = options.artist;
      }
    });
    installMediaSession();

    mod = await import('../media-session');
  });

  afterEach(() => {
    mod.destroyMediaSession();
    vi.unstubAllGlobals();
  });

  it('does not bind the canonical accessor during module import', () => {
    expect(vfoHarness.getVfoHandlers).not.toHaveBeenCalled();
  });

  it('binds exactly once on first successful init, before registration, and not on duplicate init/actions', () => {
    const order: string[] = [];
    vfoHarness.getVfoHandlers.mockImplementation(() => {
      order.push('bind');
      return vfoHarness.handlers;
    });
    vi.mocked(navigator.mediaSession.setActionHandler).mockImplementation((action, handler) => {
      order.push(`register:${action}`);
      handlers.set(action, handler);
    });
    mod.initMediaSession();
    mod.initMediaSession();
    fire('previoustrack');
    fire('nexttrack');
    expect(order[0]).toBe('bind');
    expect(vfoHarness.getVfoHandlers).toHaveBeenCalledTimes(1);
  });

  it('feature-missing init is a no-op and does not bind', () => {
    delete (navigator as { mediaSession?: MediaSession }).mediaSession;
    mod.initMediaSession();
    expect(vfoHarness.getVfoHandlers).not.toHaveBeenCalled();
    expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
  });

  it('preserves unrelated MediaSession handlers and registers only previous/next', () => {
    mod.initMediaSession();
    expect(handlers.get('play')).toBe(existingPlayHandler);
    expect(handlers.get('pause')).toBe(existingPauseHandler);
    expect(handlers.get('previoustrack')).toEqual(expect.any(Function));
    expect(handlers.get('nexttrack')).toEqual(expect.any(Function));
    expect(navigator.mediaSession.metadata).toMatchObject({
      title: 'RigPlane', artist: 'Radio Control',
    });
  });

  it.each([
    ['MAIN', 'previoustrack', 14_073_000, 0],
    ['MAIN', 'nexttrack', 14_075_000, 0],
    ['SUB', 'previoustrack', 14_073_000, 1],
    ['SUB', 'nexttrack', 14_075_000, 1],
  ] as const)('%s %s emits one exact canonical frequency intent', (receiver, action, target, physical) => {
    viewHarness.current = activeView(receiver);
    mod.initMediaSession();
    fire(action);
    expect(viewHarness.toRadioViewModel).toHaveBeenCalledWith(
      runtimeHarness.runtime.state,
      runtimeHarness.runtime.caps,
    );
    expect(vfoHarness.handlers.onFreqChange).toHaveBeenCalledTimes(1);
    expect(vfoHarness.handlers.onFreqChange).toHaveBeenCalledWith(target, physical);
    expect(legacyAlarm.tuneBy).not.toHaveBeenCalled();
    expect(legacyAlarm.patchActiveReceiver).not.toHaveBeenCalled();
    expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
  });

  it('one-receiver Selected/Unselected identity remains physical MAIN', () => {
    viewHarness.current = activeView('MAIN', 14_074_000, { kind: 'relative', id: 'selected' });
    mod.initMediaSession();
    fire('nexttrack');
    expect(vfoHarness.handlers.onFreqChange).toHaveBeenCalledWith(14_075_000, 0);
  });

  it.each([
    ['null view', null],
    ['unknown receiver', { activeReceiver: { status: 'unknown' }, vfos: [] }],
    ['no active VFO', { activeReceiver: { status: 'known', receiver: 'MAIN' }, vfos: [
      { receiver: 'MAIN', isActive: false, frequencyHz: 14_074_000 },
    ] }],
    ['multiple active VFOs', { activeReceiver: { status: 'known', receiver: 'MAIN' }, vfos: [
      { receiver: 'MAIN', isActive: true, frequencyHz: 14_074_000 },
      { receiver: 'MAIN', isActive: true, frequencyHz: 7_074_000 },
    ] }],
    ['mismatched active VFO', { activeReceiver: { status: 'known', receiver: 'SUB' }, vfos: [
      { receiver: 'MAIN', isActive: true, frequencyHz: 14_074_000 },
    ] }],
  ])('%s fails closed with zero intent', (_label, model) => {
    viewHarness.current = model;
    mod.initMediaSession();
    fire('nexttrack');
    expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
    expect(legacyAlarm.tuneBy).not.toHaveBeenCalled();
    expect(legacyAlarm.patchActiveReceiver).not.toHaveBeenCalled();
    expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
  });

  it.each([null, 0, -1, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    'unsafe confirmed frequency %s fails closed',
    (frequency) => {
      viewHarness.current = activeView('MAIN', frequency);
      mod.initMediaSession();
      fire('nexttrack');
      expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
      expect(legacyAlarm.tuneBy).not.toHaveBeenCalled();
      expect(legacyAlarm.patchActiveReceiver).not.toHaveBeenCalled();
      expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
    },
  );

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 1.5])(
    'unsafe local step %s fails closed',
    (step) => {
      tuningHarness.step = step;
      mod.initMediaSession();
      fire('nexttrack');
      expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
      expect(legacyAlarm.tuneBy).not.toHaveBeenCalled();
      expect(legacyAlarm.patchActiveReceiver).not.toHaveBeenCalled();
      expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
    },
  );

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1, 14_074_000])(
    'unsafe/no-op snapped target %s fails closed',
    (target) => {
      tuningHarness.snapToStep.mockReturnValueOnce(target);
      mod.initMediaSession();
      fire('nexttrack');
      expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
      expect(legacyAlarm.tuneBy).not.toHaveBeenCalled();
      expect(legacyAlarm.patchActiveReceiver).not.toHaveBeenCalled();
      expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
    },
  );

  it('unexpectedly absent cached handler object fails closed', () => {
    vfoHarness.getVfoHandlers.mockReturnValueOnce(null as never);
    mod.initMediaSession();
    fire('nexttrack');
    expect(vfoHarness.handlers.onFreqChange).not.toHaveBeenCalled();
    expect(legacyAlarm.sendCommand).not.toHaveBeenCalled();
  });

  it('destroy/reinitialize retains one canonical object and never rebinds', () => {
    const replacement = Object.freeze({ ...vfoHarness.handlers, onFreqChange: vi.fn() });
    vfoHarness.getVfoHandlers
      .mockReturnValueOnce(vfoHarness.handlers)
      .mockReturnValueOnce(replacement);
    mod.initMediaSession();
    fire('nexttrack');
    mod.destroyMediaSession();
    mod.initMediaSession();
    fire('nexttrack');
    expect(vfoHarness.getVfoHandlers).toHaveBeenCalledTimes(1);
    expect(vfoHarness.handlers.onFreqChange).toHaveBeenCalledTimes(2);
    expect(replacement.onFreqChange).not.toHaveBeenCalled();
  });

  it('keeps silent audio/idempotence/cleanup behavior and never adds PTT/TUNE handlers', () => {
    mod.initMediaSession();
    mod.initMediaSession();
    expect(mockAudio.ctx.createOscillator).toHaveBeenCalledTimes(1);
    expect(mockAudio.ctx.createGain).toHaveBeenCalledTimes(1);
    expect(mockAudio.gainNode.gain.value).toBe(0);
    expect(mockAudio.oscillator.start).toHaveBeenCalledTimes(1);
    expect(Array.from(handlers.keys())).not.toEqual(expect.arrayContaining(['ptt', 'ptt_on', 'ptt_off']));
    mod.destroyMediaSession();
    mod.destroyMediaSession();
    expect(mockAudio.oscillator.stop).toHaveBeenCalledTimes(1);
    expect(mockAudio.ctx.close).toHaveBeenCalledTimes(1);
    expect(handlers.get('play')).toBe(existingPlayHandler);
    expect(handlers.get('pause')).toBe(existingPauseHandler);
  });

  it('starts a fresh silent-audio session after destroy while retaining the radio binding', () => {
    mod.initMediaSession();
    const firstHandler = handlers.get('previoustrack');
    mod.destroyMediaSession();
    mod.initMediaSession();
    expect(audioContexts).toHaveLength(2);
    expect(audioContexts[0]).not.toBe(audioContexts[1]);
    expect(handlers.get('previoustrack')).not.toBe(firstHandler);
    expect(vfoHarness.getVfoHandlers).toHaveBeenCalledTimes(1);
  });

  it('source has static cycle-safe imports, nullable cache, and zero legacy authority', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/media/media-session.ts'), 'utf8');
    expect(source).toContain("import { runtime } from '../runtime/frontend-runtime'");
    expect(source).toContain("import { getVfoHandlers } from '../runtime/adapters/panel-adapters'");
    expect(source).toMatch(/let vfoHandlers: ReturnType<typeof getVfoHandlers> \| null = null/);
    expect(source).toContain('vfoHandlers ??= getVfoHandlers()');
    expect(source).not.toMatch(/tuneBy|patchActiveReceiver|sendCommand|set_tuner_status|cw_auto_tune|\bptt\b/i);
  });

  it('the real panel-adapter/frontend-runtime/system-controller/media graph imports without TDZ', async () => {
    mod.destroyMediaSession();
    vi.resetModules();
    vi.doUnmock('../../runtime/adapters/panel-adapters');
    vi.doUnmock('../../runtime/frontend-runtime');
    vi.doUnmock('../../runtime/system-controller');
    vi.doUnmock('../../runtime/adapters/radio-view-model-adapter');
    vi.doUnmock('../../stores/tuning.svelte');
    vi.doUnmock('$lib/runtime/adapters/panel-adapters');
    vi.doUnmock('$lib/runtime/frontend-runtime');
    vi.doUnmock('$lib/runtime/adapters/radio-view-model-adapter');
    vi.doUnmock('$lib/stores/tuning.svelte');
    vi.doUnmock('$lib/stores/radio.svelte');
    vi.doUnmock('$lib/transport/ws-client');
    vi.doUnmock('../media-session');
    const actual = await import('../../runtime/adapters/panel-adapters');
    expect(actual.getVfoHandlers()).toBe(actual.getVfoHandlers());
  });
});
