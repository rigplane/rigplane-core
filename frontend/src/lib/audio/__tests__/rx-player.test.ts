import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RxPlayer } from '../rx-player';
import { AUDIO_HEADER_SIZE, MSG_TYPE_RX, CODEC_OPUS, CODEC_PCM16, SAMPLE_RATE, FRAME_DURATION_MS } from '../constants';

let ctx: any;
beforeEach(() => {
  const gains: any[] = [];
  const panners: any[] = [];
  const splitters: any[] = [];
  ctx = {
    state: 'running', currentTime: 0, destination: {},
    resume: vi.fn().mockResolvedValue(undefined), close: vi.fn().mockResolvedValue(undefined),
    createGain: vi.fn(() => {
      const g = { gain: { value: 1 }, connect: vi.fn() };
      gains.push(g);
      return g;
    }),
    createStereoPanner: vi.fn(() => {
      const p = { pan: { value: 0 }, connect: vi.fn() };
      panners.push(p);
      return p;
    }),
    createChannelSplitter: vi.fn((_n: number = 2) => {
      const s = { connect: vi.fn() };
      splitters.push(s);
      return s;
    }),
    createBuffer: vi.fn((ch: number, n: number, sr: number) => ({
      duration: n / sr, getChannelData: () => new Float32Array(n),
    })),
    _lastSrc: null as any,
    createBufferSource: vi.fn(function (this: any) {
      const src = { buffer: null, connect: vi.fn(), start: vi.fn() };
      ctx._lastSrc = src;
      return src;
    }),
    // Named accessors preserved for backward-compat with earlier tests.
    get _gain() { return gains[0]; },  // preGain
    _gains: gains,
    _panners: panners,
    _splitters: splitters,
  };
  (globalThis as any).AudioContext = function () { return ctx; } as any;
});
afterEach(() => { delete (globalThis as any).AudioContext; });

function pcm16(n: number): ArrayBuffer {
  const buf = new ArrayBuffer(AUDIO_HEADER_SIZE + n * 2);
  const v = new DataView(buf);
  v.setUint8(0, MSG_TYPE_RX); v.setUint8(1, CODEC_PCM16);
  v.setUint16(4, SAMPLE_RATE / 100, true); v.setUint8(6, 1); v.setUint8(7, FRAME_DURATION_MS);
  return buf;
}

describe('RxPlayer', () => {
  it('creates AudioContext and routing graph on start', () => {
    const p = new RxPlayer(); p.start();
    expect(ctx.createGain).toHaveBeenCalledTimes(3);  // preGain + mainGain + subGain
    // Terminal nodes in the new graph are the two stereo panners.
    const [mainP, subP] = ctx._panners;
    expect(mainP.connect).toHaveBeenCalledWith(ctx.destination);
    expect(subP.connect).toHaveBeenCalledWith(ctx.destination);
    expect(p.active).toBe(true); p.stop();
  });
  it('is inactive before start and after stop', () => {
    const p = new RxPlayer();
    expect(p.active).toBe(false); p.start(); p.stop(); expect(p.active).toBe(false);
  });
  it('resumes suspended context', () => {
    ctx.state = 'suspended'; const p = new RxPlayer(); p.start(); p.start();
    expect(ctx.resume).toHaveBeenCalled(); p.stop();
  });
  it('handles missing AudioContext', () => {
    delete (globalThis as any).AudioContext;
    const p = new RxPlayer(); p.start(); expect(p.active).toBe(false);
  });
  it('clamps volume and applies to gain', () => {
    const p = new RxPlayer(); p.start();
    p.volume = -1; expect(p.volume).toBe(0);
    p.volume = 5; expect(p.volume).toBe(1);
    p.volume = 0.3; expect(ctx._gain.gain.value).toBeCloseTo(0.3); p.stop();
  });
  it('processes PCM16 frame and schedules playback', () => {
    const p = new RxPlayer(); p.start(); p.feed(pcm16(480));
    expect(ctx.createBuffer).toHaveBeenCalledWith(1, 480, SAMPLE_RATE);
    expect(ctx.createBufferSource).toHaveBeenCalled();
    expect(ctx._lastSrc.start).toHaveBeenCalled();
    p.stop();
  });
  it('ignores feed when stopped', () => {
    new RxPlayer().feed(pcm16(480));
    expect(ctx.createBuffer).not.toHaveBeenCalled();
  });
  it('cleans up on stop', () => {
    const p = new RxPlayer(); p.start(); p.stop();
    expect(ctx.close).toHaveBeenCalled();
  });
});

describe('RxPlayer audio routing (#753)', () => {
  it('builds splitter + 2 gains + 2 panners graph on start', () => {
    const p = new RxPlayer();
    p.start();
    // 3 gains: preGain + mainGain + subGain
    expect(ctx._gains.length).toBe(3);
    expect(ctx._panners.length).toBe(2);
    expect(ctx._splitters.length).toBe(1);
    p.stop();
  });

  it('default state: focus=both, split=off → both gains on unity, panners centred', () => {
    const p = new RxPlayer();
    p.start();
    const [_pre, mainG, subG] = ctx._gains;
    const [mainP, subP] = ctx._panners;
    expect(mainG.gain.value).toBe(1);
    expect(subG.gain.value).toBe(1);
    expect(mainP.pan.value).toBe(0);
    expect(subP.pan.value).toBe(0);
    p.stop();
  });

  it("setFocus('main') silences SUB gain", () => {
    const p = new RxPlayer();
    p.start();
    p.setFocus('main');
    const [_pre, mainG, subG] = ctx._gains;
    expect(mainG.gain.value).toBe(1);
    expect(subG.gain.value).toBe(0);
    p.stop();
  });

  it("setFocus('sub') silences MAIN gain", () => {
    const p = new RxPlayer();
    p.start();
    p.setFocus('sub');
    const [_pre, mainG, subG] = ctx._gains;
    expect(mainG.gain.value).toBe(0);
    expect(subG.gain.value).toBe(1);
    p.stop();
  });

  it("setFocus('both') restores both gains to their dB settings", () => {
    const p = new RxPlayer();
    p.start();
    p.setChannelGainDb('main', -6);
    p.setChannelGainDb('sub', -12);
    p.setFocus('main');
    p.setFocus('both');
    const [_pre, mainG, subG] = ctx._gains;
    expect(mainG.gain.value).toBeCloseTo(0.5012, 3);  // -6 dB
    expect(subG.gain.value).toBeCloseTo(0.2512, 3);   // -12 dB
    p.stop();
  });

  it('setSplitStereo(true) puts MAIN pan left, SUB pan right', () => {
    const p = new RxPlayer();
    p.start();
    p.setSplitStereo(true);
    const [mainP, subP] = ctx._panners;
    expect(mainP.pan.value).toBe(-1);
    expect(subP.pan.value).toBe(+1);
    p.stop();
  });

  it('setSplitStereo(false) centres both panners', () => {
    const p = new RxPlayer();
    p.start();
    p.setSplitStereo(true);
    p.setSplitStereo(false);
    const [mainP, subP] = ctx._panners;
    expect(mainP.pan.value).toBe(0);
    expect(subP.pan.value).toBe(0);
    p.stop();
  });

  it('setChannelGainDb clamps very negative dB to 0 linear', () => {
    const p = new RxPlayer();
    p.start();
    p.setChannelGainDb('main', -999);
    const [_pre, mainG] = ctx._gains;
    expect(mainG.gain.value).toBe(0);
    p.stop();
  });

  it('pre-start setFocus does not throw and persists to graph after start', () => {
    const p = new RxPlayer();
    p.setFocus('sub');
    p.setSplitStereo(true);
    p.start();
    const [_pre, mainG, subG] = ctx._gains;
    const [mainP, subP] = ctx._panners;
    expect(mainG.gain.value).toBe(0);
    expect(subG.gain.value).toBe(1);
    expect(mainP.pan.value).toBe(-1);
    expect(subP.pan.value).toBe(+1);
    p.stop();
  });

  it('volume still sets preGain (backwards-compat)', () => {
    const p = new RxPlayer();
    p.start();
    p.volume = 0.42;
    const [preGain] = ctx._gains;
    expect(preGain.gain.value).toBeCloseTo(0.42);
    p.stop();
  });
});

describe('RxPlayer jitter bounds (#1363)', () => {
  it('uses default 50/300 ms bounds without setJitterBounds', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    // Floor 50 ms → nextPlayTime resets to 0 + 0.05 = 0.05.
    expect(ctx._lastSrc.start).toHaveBeenCalledTimes(1);
    expect(ctx._lastSrc.start.mock.calls[0][0]).toBeCloseTo(0.05, 5);
    p.stop();
  });

  it('setJitterBounds(100, 500) changes floor used by reset', () => {
    const p = new RxPlayer();
    p.start();
    p.setJitterBounds(100, 500);
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    // Floor 100 ms → nextPlayTime resets to 0 + 0.10 = 0.10.
    expect(ctx._lastSrc.start).toHaveBeenCalledTimes(1);
    expect(ctx._lastSrc.start.mock.calls[0][0]).toBeCloseTo(0.10, 5);
    p.stop();
  });

  it('reset trigger uses floor/2 (derived, not hardcoded)', () => {
    const p = new RxPlayer();
    p.start();
    // First feed with defaults (50/300): nextPlayTime → ~0.06 after start+duration.
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    expect(ctx._lastSrc.start.mock.calls[0][0]).toBeCloseTo(0.05, 5);
    // Switch to bounds where floor=200: floor/2 = 0.10. Current nextPlayTime
    // (~0.06) is below floor/2, so a new feed must reset to 0 + 0.20 = 0.20.
    p.setJitterBounds(200, 1000);
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    expect(ctx._lastSrc.start).toHaveBeenCalledTimes(1);
    expect(ctx._lastSrc.start.mock.calls[0][0]).toBeCloseTo(0.20, 5);
    p.stop();
  });
});

describe('RxPlayer suspended-context recovery (MOR-239)', () => {
  it('start() resumes a context that is created suspended (WKWebView autoplay)', () => {
    ctx.state = 'suspended';
    const p = new RxPlayer();
    p.start();
    expect(ctx.resume).toHaveBeenCalled();
    p.stop();
  });

  it('drops the frame while suspended but re-attempts resume and warns (not silent)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctx.state = 'suspended';
    const p = new RxPlayer();
    p.start();
    ctx.resume.mockClear();

    p.feed(pcm16(480));

    // Frame can't play on a suspended ctx, but instead of a silent return
    // we re-attempt resume() and surface a warning so it's observable.
    expect(ctx.createBufferSource).not.toHaveBeenCalled();
    expect(ctx.resume).toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
    p.stop();
    warn.mockRestore();
  });

  it('plays frames once the context has resumed to running', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctx.state = 'suspended';
    const p = new RxPlayer();
    p.start();
    p.feed(pcm16(480));           // dropped — suspended
    expect(ctx._lastSrc).toBeNull();

    ctx.state = 'running';        // resume() honoured by the gesture
    p.feed(pcm16(480));           // now audible
    expect(ctx.createBufferSource).toHaveBeenCalled();
    expect(ctx._lastSrc.start).toHaveBeenCalled();
    p.stop();
    warn.mockRestore();
  });

  it('re-attempts resume on focus/visibility regain (WKWebView re-suspend)', () => {
    ctx.state = 'suspended';
    const p = new RxPlayer();
    p.start();
    ctx.resume.mockClear();
    window.dispatchEvent(new Event('focus'));
    expect(ctx.resume).toHaveBeenCalled();
    p.stop();
    // After stop the listener must be gone — no further resume calls.
    ctx.resume.mockClear();
    window.dispatchEvent(new Event('focus'));
    expect(ctx.resume).not.toHaveBeenCalled();
  });
});

describe('RxPlayer link-quality stats (MOR-585)', () => {
  it('starts with zeroed stats', () => {
    const p = new RxPlayer();
    p.start();
    expect(p.stats()).toEqual({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 });
    p.stop();
  });

  it('does NOT count the initial priming rebase as an underrun', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));           // first frame: nextPlayTime 0 → floor rebase
    expect(p.stats().underruns).toBe(0);
    p.stop();
  });

  it('counts a real underrun when playback catches up with the schedule', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));           // primes: nextPlayTime ≈ 0.06
    ctx.currentTime = 10;         // playback ran past the buffer — it drained
    p.feed(pcm16(480));           // late frame → rebase = underrun
    expect(p.stats().underruns).toBe(1);
    p.stop();
  });

  it('flush() rebase after reconnect is not an underrun', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    p.flush();                    // reconnect path resets the schedule
    ctx.currentTime = 10;
    p.feed(pcm16(480));
    expect(p.stats().underruns).toBe(0);
    p.stop();
  });

  it('reports current jitter-buffer depth in ms', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));           // floor 50 ms + 10 ms frame → 60 ms ahead
    expect(p.stats().bufferDepthMs).toBe(60);
    p.stop();
  });

  it('buffer depth is 0 when idle or stopped', () => {
    const p = new RxPlayer();
    expect(p.stats().bufferDepthMs).toBe(0);
    p.start();
    expect(p.stats().bufferDepthMs).toBe(0);
    p.stop();
    expect(p.stats().bufferDepthMs).toBe(0);
  });

  it('counts frames dropped at the jitter ceiling', () => {
    const p = new RxPlayer();
    p.start();
    p.setJitterBounds(50, 65);    // ceiling between frame boundaries (no FP ties)
    ctx.currentTime = 0;
    p.feed(pcm16(480));           // → 0.06
    p.feed(pcm16(480));           // 0.06 < 0.065 → scheduled → 0.07
    p.feed(pcm16(480));           // 0.07 > 0.065 → dropped
    expect(p.stats().droppedFrames).toBe(1);
    p.stop();
  });

  it('counts frames dropped while the context is suspended', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctx.state = 'suspended';
    const p = new RxPlayer();
    p.start();
    p.feed(pcm16(480));
    expect(p.stats().droppedFrames).toBe(1);
    p.stop();
    warn.mockRestore();
  });

  it('stop() resets all counters', () => {
    const p = new RxPlayer();
    p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));
    ctx.currentTime = 10;
    p.feed(pcm16(480));           // 1 underrun
    p.stop();
    expect(p.stats()).toEqual({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 });
  });
});

describe('RxPlayer mid-stream codec switch (MOR-588)', () => {
  // Adaptive egress (server-side controller) switches PCM16↔Opus
  // mid-stream; the codec byte travels per frame. The player must
  // re-init the WebCodecs Opus decoder on every switch (the server
  // starts a fresh encoder stream) without rebasing the schedule.
  class FakeAudioDecoder {
    static instances: FakeAudioDecoder[] = [];
    state = 'unconfigured';
    config: any = null;
    chunks: any[] = [];
    constructor(public init: any) { FakeAudioDecoder.instances.push(this); }
    configure(cfg: any) { this.config = cfg; this.state = 'configured'; }
    decode(chunk: any) {
      this.chunks.push(chunk);
      // Synchronously emit one decoded 480-frame mono buffer.
      this.init.output({
        numberOfFrames: 480, numberOfChannels: 1, sampleRate: SAMPLE_RATE,
        copyTo: vi.fn(), close: vi.fn(),
      });
    }
    close() { this.state = 'closed'; }
  }
  class FakeEncodedAudioChunk {
    constructor(public init: any) {}
  }

  function opus(): ArrayBuffer {
    const buf = new ArrayBuffer(AUDIO_HEADER_SIZE + 40);
    const v = new DataView(buf);
    v.setUint8(0, MSG_TYPE_RX); v.setUint8(1, CODEC_OPUS);
    v.setUint16(4, SAMPLE_RATE / 100, true); v.setUint8(6, 1); v.setUint8(7, FRAME_DURATION_MS);
    return buf;
  }

  beforeEach(() => {
    FakeAudioDecoder.instances = [];
    vi.stubGlobal('AudioDecoder', FakeAudioDecoder);
    vi.stubGlobal('EncodedAudioChunk', FakeEncodedAudioChunk);
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('PCM16→Opus switch initialises a fresh Opus decoder', () => {
    const p = new RxPlayer(); p.start();
    p.feed(pcm16(480));
    expect(FakeAudioDecoder.instances.length).toBe(0);
    p.feed(opus());
    expect(FakeAudioDecoder.instances.length).toBe(1);
    expect(FakeAudioDecoder.instances[0].config?.codec).toBe('opus');
    p.stop();
  });

  it('Opus→PCM16 switch closes the stale Opus decoder and keeps playing', () => {
    const p = new RxPlayer(); p.start();
    p.feed(opus());
    const dec = FakeAudioDecoder.instances[0];
    expect(dec.state).toBe('configured');
    p.feed(pcm16(480));
    expect(dec.state).toBe('closed');
    // PCM frame still scheduled — switch is seamless.
    expect(ctx._lastSrc.start).toHaveBeenCalled();
    p.stop();
  });

  it('Opus→PCM16→Opus re-init builds a NEW decoder for the fresh server stream', () => {
    const p = new RxPlayer(); p.start();
    p.feed(opus());
    p.feed(pcm16(480));
    p.feed(opus());
    expect(FakeAudioDecoder.instances.length).toBe(2);
    expect(FakeAudioDecoder.instances[0].state).toBe('closed');
    expect(FakeAudioDecoder.instances[1].state).toBe('configured');
    // The fresh decoder's timestamps restart at 0 (fresh encoder stream).
    expect(FakeAudioDecoder.instances[1].chunks[0].init.timestamp).toBe(0);
    p.stop();
  });

  it('does not rebase the playback schedule on a switch (no audible gap)', () => {
    const p = new RxPlayer(); p.start();
    ctx.currentTime = 0;
    p.feed(pcm16(480));   // primes: floor 0.05 + 10 ms → nextPlayTime 0.06
    p.feed(opus());        // decoded buffer must continue at 0.06, not re-floor
    const calls = ctx._lastSrc.start.mock.calls;
    expect(calls[0][0]).toBeCloseTo(0.06, 5);
    expect(p.stats().underruns).toBe(0);
    p.stop();
  });

  it('stop() resets codec tracking — restart does not misread a switch', () => {
    const p = new RxPlayer(); p.start();
    p.feed(opus());
    p.stop();
    p.start();
    p.feed(opus());
    expect(FakeAudioDecoder.instances.length).toBe(2);
    expect(FakeAudioDecoder.instances[1].state).toBe('configured');
    p.stop();
  });
});

describe('RxPlayer mono routing (MOR-239)', () => {
  it('routes a mono (1-ch) PCM16 buffer to the audible MAIN channel', () => {
    const p = new RxPlayer();
    p.start();                    // default focus='both' → MAIN gain audible
    p.feed(pcm16(480));           // header marks 1 channel

    // Mono buffer allocated as 1 channel...
    expect(ctx.createBuffer).toHaveBeenCalledWith(1, 480, SAMPLE_RATE);
    // ...fed into preGain (→ splitter[0] → mainGain → mainPanner → destination).
    const [preGain, mainGain] = ctx._gains;
    expect(ctx._lastSrc.connect).toHaveBeenCalledWith(preGain);
    // MAIN channel must carry audible gain so mono lands on an output, not
    // only on the silenced SUB (channel 1) of the splitter.
    expect(mainGain.gain.value).toBeGreaterThan(0);
    p.stop();
  });
});
