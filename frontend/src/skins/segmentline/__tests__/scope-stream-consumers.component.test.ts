import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { audioScopeGoldenNames, loadAudioScopeGolden, scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { ScopeStream } from '../../../lib/runtime/scope-stream';
import LcdAfFft from '../LcdAfFft.svelte';
import LcdRfPanadapter from '../LcdRfPanadapter.svelte';
import { radio } from '$lib/stores/radio.svelte';
import type { ServerState } from '$lib/types/state';
import observedState from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';

vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });

function audioState(freqHz: number, mode = 'USB', cwPitch = 600): ServerState {
  const state = structuredClone(observedState) as unknown as ServerState;
  state.main.freqHz = freqHz; state.main.mode = mode; state.main.filterWidth = 2400; state.cwPitch = cwPitch;
  for (const path of ['main.freqHz', 'main.mode', 'main.filterWidth', 'cwPitch']) {
    state.fieldStatus![path] = { ...state.fieldStatus!['main.freqHz']!, observed: true,
      freshness: 'fresh', availability: 'available' };
  }
  return state;
}

function source(kind: 'radio' | 'audio_fft') {
  let receive = (_buffer: ArrayBuffer) => {};
  const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
    { source: kind, sourceId: kind, epoch: 1 }, 'borrowed');
  return { stream, frame(receiver = 0, pixels = [0, 128, 255], outOfRange = false) {
    const bytes = scopeWire({ source: kind, sourceId: kind, receiver: kind === 'audio_fft' ? null : receiver, viewStartHz: 14_200_000, viewEndHz: kind === 'audio_fft' ? 14_202_400 : 14_300_000, outOfRange, pixels: pixels.length > 0 ? Uint8Array.from(pixels) : null });
    receive(bytes); flushSync();
  } };
}

describe('LCD leaves consume ScopeStream directly', () => {
  it('retries a refused mounted AF subscription once when observed facts arrive and paints its frame', async () => {
    const previous = radio.current;
    radio.current = null;
    const publish = vi.fn();
    let receive = (_buffer: ArrayBuffer) => {};
    const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
      { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 }, 'borrowed', undefined, publish);
    const target = document.createElement('div'); document.body.append(target);
    const width = vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(500);
    const component = mount(LcdAfFft, { target, props: {
      scopeStream: stream, receiver: 'MAIN', inputState: 'live', receiverActivity: 'active',
    } });
    try {
      flushSync();
      expect(publish).toHaveBeenCalledTimes(1);
      stream.handleMessage({ type: 'error', message: 'audio mapping unavailable' }); flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('safe-empty');
      const usable = audioState(14_200_000);
      usable.main.filterWidth = 2400;
      usable.fieldStatus!['main.filterWidth'] = { ...usable.fieldStatus!['main.freqHz']!,
        storePath: 'receiver.main.active.freq_mode.filter_width' };
      radio.current = usable; flushSync();
      expect(publish).toHaveBeenCalledTimes(2);
      expect(publish.mock.lastCall?.[0]).toMatchObject({ receiver: null, view: { follow: 'vfo', spanHz: 48000 } });
      stream.handleMessage({ type: 'error', message: 'same facts rejected' }); flushSync();
      radio.current = { ...radio.current! }; flushSync();
      expect(publish).toHaveBeenCalledTimes(2);
      stream.handleMessage({ type: 'granted', sourceId: 'audio_fft', receiver: null,
        view: { startHz: 14_200_000, endHz: 14_202_400 }, coverage: null, width: 3, fps: 20 });
      receive(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null,
        viewStartHz: 14_200_000, viewEndHz: 14_202_400, pixels: new Uint8Array([0, 128, 255]) }));
      flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('live');
      expect(target.querySelector('.fft-trace')?.getAttribute('d')).toBe('M0.00,92.00 L250.00,50.84 L500.00,10.00');
      expect(publish).toHaveBeenCalledTimes(2);
    } finally {
      await unmount(component); stream.dispose(); target.remove(); width.mockRestore(); radio.current = previous;
    }
  });
  it('keeps retained TX-freeze samples live and clears only header-only out-of-range frames', async () => {
    const target = document.createElement('div'); document.body.append(target);
    const wire = source('radio');
    const component = mount(LcdRfPanadapter, { target, props: { scopeStream: wire.stream, receiver: 'MAIN' } });
    const state = () => target.querySelector('[data-testid="lcd-rf-panadapter"]');
    const samples = () => [...target.querySelectorAll('[data-rf-sample]')].map(node => Number(node.getAttribute('data-rf-sample')));
    try {
      flushSync(); wire.frame();
      expect(state()?.getAttribute('data-rf-mode')).toBe('live'); expect(samples()).toEqual([0, 128, 255]);
      wire.frame(0, [0, 128, 255], true);
      expect(state()?.getAttribute('data-rf-mode')).toBe('live'); expect(samples()).toEqual([0, 128, 255]);
      wire.frame(0, [], true);
      expect(state()?.getAttribute('data-rf-mode')).toBe('ghost');
      expect(state()?.getAttribute('data-frame-reason')).toBe('out-of-range'); expect(samples()).toEqual([]);
      wire.frame();
      expect(state()?.getAttribute('data-rf-mode')).toBe('live'); expect(samples()).toEqual([0, 128, 255]);
    } finally { await unmount(component); wire.stream.dispose(); target.remove(); }
  });
  it('paints audio pixels without a reactive frame carrier and clears stale geometry', async () => {
    const target = document.createElement('div'); document.body.append(target);
    const wire = source('audio_fft');
    const previous = radio.current; radio.current = audioState(14_200_000);
    const component = mount(LcdAfFft, { target, props: {
      scopeStream: wire.stream, receiver: 'MAIN', inputState: 'live', receiverActivity: 'active',
    } as never });
    try {
      flushSync(); wire.frame();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('live');
      expect(target.querySelector('.fft-trace')?.getAttribute('d')).toBe('M0.00,92.00 L250.00,50.84 L500.00,10.00');
      radio.current = audioState(14_200_000, 'LSB'); flushSync();
      expect(target.querySelector('.fft-trace')?.getAttribute('d')).not.toBe('M0.00,92.00 L250.00,50.84 L500.00,10.00');
      wire.stream.setFreshness('stale'); flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('safe-empty');
    } finally { await unmount(component); wire.stream.dispose(); target.remove(); radio.current = previous; }
  });
  it.each(audioScopeGoldenNames)('projects real %s producer pixels to the AF tone position', async name => {
    const { buffer, expected } = loadAudioScopeGolden(name);
    const previous = radio.current;
    radio.current = audioState(expected.audioPassband.tunedHz, expected.input.mode, expected.input.cwPitchHz ?? 600);
    if (expected.input.cwPitchHz !== null) radio.current.main.filterWidth = 500;
    let receive = (_buffer: ArrayBuffer) => {};
    const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
      { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 }, 'borrowed');
    const target = document.createElement('div'); document.body.append(target);
    const component = mount(LcdAfFft, { target, props: {
      scopeStream: stream, receiver: 'MAIN', inputState: 'live', receiverActivity: 'active',
    } });
    try {
      flushSync(); receive(buffer); flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('live');
      const path = target.querySelector('.fft-trace')!.getAttribute('d')!;
      const points = [...path.matchAll(/[ML]([\d.]+),([\d.]+)/g)].map(match => [Number(match[1]), Number(match[2])]);
      const peak = points.reduce((best, point) => point[1] < best[1] ? point : best);
      const carrier = expected.audioPassband.tunedHz + expected.audioPassband.carrierOffsetHz;
      const span = expected.audioPassband.sideband === 'lower'
        ? carrier - expected.frame.coverage_start_hz : expected.frame.coverage_end_hz - carrier;
      expect(peak[0] / 500 * span).toBeCloseTo(expected.input.toneHz, -2);
      expect(stream.latest(stream.selectedSlot!)?.pixels?.buffer).toBe(buffer);
    } finally { await unmount(component); stream.dispose(); target.remove(); radio.current = previous; }
  });
  it('projects a producer USB tone embedded in a negotiated 48 kHz RF view', async () => {
    const { expected } = loadAudioScopeGolden('audio-usb-tone');
    const previous = radio.current; radio.current = audioState(expected.audioPassband.tunedHz);
    const pixels = new Uint8Array(2041);
    pixels.set(expected.frame.pixels!, 1020);
    const buffer = scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null,
      viewStartHz: expected.audioPassband.tunedHz - 24_000,
      viewEndHz: expected.audioPassband.tunedHz + 24_000,
      coverageStartHz: expected.frame.coverage_start_hz, coverageEndHz: expected.frame.coverage_end_hz, pixels });
    let receive = (_buffer: ArrayBuffer) => {};
    const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
      { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 }, 'borrowed');
    const target = document.createElement('div'); document.body.append(target);
    const component = mount(LcdAfFft, { target, props: {
      scopeStream: stream, receiver: 'MAIN', inputState: 'live', receiverActivity: 'active',
    } });
    try {
      flushSync(); receive(buffer); flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('live');
      const path = target.querySelector('.fft-trace')!.getAttribute('d')!;
      const points = [...path.matchAll(/[ML]([\d.]+),([\d.]+)/g)].map(match => [Number(match[1]), Number(match[2])]);
      const peak = points.reduce((best, point) => point[1] < best[1] ? point : best);
      expect(peak[0] / 500 * (expected.frame.coverage_end_hz - expected.audioPassband.tunedHz))
        .toBeCloseTo(expected.input.toneHz, -2);
      expect(stream.latest(stream.selectedSlot!)?.pixels?.buffer).toBe(buffer);
    } finally { await unmount(component); stream.dispose(); target.remove(); radio.current = previous; }
  });
  it('paints exact RF samples from the selected receiver and rejects other receivers', async () => {
    const target = document.createElement('div'); document.body.append(target);
    const wire = source('radio');
    const component = mount(LcdRfPanadapter, { target, props: { scopeStream: wire.stream, receiver: 'MAIN' } as never });
    try {
      flushSync(); wire.frame();
      expect([...target.querySelectorAll('[data-rf-sample]')].map(node => Number(node.getAttribute('data-rf-sample')))).toEqual([0, 128, 255]);
      wire.frame(1, [255, 0]);
      expect(target.querySelectorAll('[data-rf-sample]')).toHaveLength(0);
    } finally { await unmount(component); wire.stream.dispose(); target.remove(); }
  });
});
