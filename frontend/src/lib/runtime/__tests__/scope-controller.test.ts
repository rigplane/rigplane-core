/**
 * Unit tests for ScopeController.
 *
 * Uses constructor injection (channelFactory) so no vi.mock is needed —
 * safe to run in the fast (non-isolated) vitest pool.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ScopeController } from '../scope-controller.svelte';
import { PresentationResourceHost } from '../resource-host';
import type { ScopeFrame } from '../scope-controller.svelte';

// ── Helpers ──

function makeMockChannel() {
  const binaryHandlers = new Set<(buf: ArrayBuffer) => void>();
  return {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (buf: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    /** Fire a binary message to all registered handlers. */
    _fire(buf: ArrayBuffer) {
      for (const h of binaryHandlers) h(buf);
    },
  };
}

/** Build a minimal valid scope frame ArrayBuffer (16-byte header + 4 pixels). */
function makeScopeFrameBuffer(pixelCount = 4): ArrayBuffer {
  const buf = new ArrayBuffer(16 + pixelCount);
  const view = new DataView(buf);
  view.setUint8(0, 0x01);          // magic
  view.setUint8(1, 0);             // receiver 0
  view.setUint32(3, 14_100_000, true); // startFreq
  view.setUint32(7, 14_200_000, true); // endFreq
  view.setUint16(14, pixelCount, true);
  return buf;
}

// ── Tests ──

describe('ScopeController', () => {
  let channel: ReturnType<typeof makeMockChannel>;
  let ctrl: ScopeController;

  beforeEach(() => {
    channel = makeMockChannel();
    ctrl = new ScopeController(() => channel as any);
  });

  it('keeps frame subscriptions lifetime-neutral', () => {
    expect(channel.connect).not.toHaveBeenCalled();

    const unsubscribe = ctrl.subscribe(vi.fn());
    unsubscribe();
    unsubscribe();

    expect(channel.connect).not.toHaveBeenCalled();
    expect(channel.disconnect).not.toHaveBeenCalled();
  });

  it('registers one exact-handle driver for first/shared/last demand', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    ctrl.registerPresentationDriver(host);
    const first = host.acquire('audio-fft', 'first');
    const shared = host.acquire('audio-fft', 'shared');
    await vi.waitFor(() => expect(channel.connect).toHaveBeenCalledTimes(1));
    expect(channel.connect).toHaveBeenCalledWith('/api/v1/audio-scope');
    expect(host.snapshot('audio-fft').activeHandle).toBe(channel);

    expect(host.release(first)).toBe(true);
    expect(channel.disconnect).not.toHaveBeenCalled();
    expect(host.release(shared)).toBe(true);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
    expect(host.release(shared)).toBe(false);
  });

  it('both subscribers receive parsed frames', async () => {
    const h1 = vi.fn<(frame: ScopeFrame) => void>();
    const h2 = vi.fn<(frame: ScopeFrame) => void>();

    ctrl.subscribe(h1);
    ctrl.subscribe(h2);
    await ctrl.audioFftDriver.start();

    channel._fire(makeScopeFrameBuffer());

    expect(h1).toHaveBeenCalledTimes(1);
    expect(h2).toHaveBeenCalledTimes(1);

    const frame = h1.mock.calls[0][0];
    expect(frame.startFreq).toBe(14_100_000);
    expect(frame.endFreq).toBe(14_200_000);
  });

  it('unsubscribed handler no longer receives frames', async () => {
    const h1 = vi.fn();
    const h2 = vi.fn();

    const unsub1 = ctrl.subscribe(h1);
    ctrl.subscribe(h2);
    await ctrl.audioFftDriver.start();

    unsub1();
    channel._fire(makeScopeFrameBuffer());

    expect(h1).not.toHaveBeenCalled();
    expect(h2).toHaveBeenCalledTimes(1);
  });

  it('ignores malformed frames (wrong magic byte)', async () => {
    const handler = vi.fn();
    ctrl.subscribe(handler);
    await ctrl.audioFftDriver.start();

    // Bad magic — parseScopeFrame returns null
    const bad = new ArrayBuffer(20);
    channel._fire(bad);

    expect(handler).not.toHaveBeenCalled();
  });

  it('does not let a stale exact-handle stop close the current channel', async () => {
    const next = makeMockChannel();
    ctrl = new ScopeController(vi.fn()
      .mockReturnValueOnce(channel as any)
      .mockReturnValueOnce(next as any));
    const first = await ctrl.audioFftDriver.start();
    await ctrl.audioFftDriver.stop(first);
    const current = await ctrl.audioFftDriver.start();
    await ctrl.audioFftDriver.stop(first);

    expect(channel.disconnect).toHaveBeenCalledTimes(1);
    expect(next.disconnect).not.toHaveBeenCalled();
    await ctrl.audioFftDriver.stop(current);
    expect(next.disconnect).toHaveBeenCalledTimes(1);
  });

  it('keeps the panel on one demand lease without transport or hardware fallback', () => {
    const source = readFileSync(
      resolve('src/components-v2/panels/audio-scope/AudioSpectrumPanel.svelte'),
      'utf8',
    );
    expect(source).toContain("acquire('audio-fft'");
    expect(source).toContain('release(lease)');
    expect(source).toContain('registerPresentationDriver');
    expect(source).not.toMatch(/\$lib\/transport|hardware-scope/);
  });
});
