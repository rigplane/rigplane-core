import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';

const lcdSources = [
  ['AmberScope', 'src/components-v2/panels/lcd/AmberScope.svelte'],
  ['AmberCockpit', 'src/components-v2/panels/lcd/AmberCockpit.svelte'],
] as const;

function makeChannel() {
  const binaryHandlers = new Set<(data: ArrayBuffer) => void>();
  return {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    handlerCount: () => binaryHandlers.size,
  };
}

describe('LCD audio-FFT demand ownership', () => {
  it.each(lcdSources)('%s owns one demand lease and one frame subscription', (name, path) => {
    const source = readFileSync(resolve(path), 'utf8');
    expect(source).toContain(
      "import { presentationResources, runtime } from '$lib/runtime/frontend-runtime'",
    );
    expect(source).toContain('runtime.scope.registerPresentationDriver(presentationResources)');
    expect(source).toContain(`presentationResources.acquire('audio-fft', '${name}')`);
    expect(source.match(/runtime\.scope\.subscribe\(/g)).toHaveLength(1);
    expect(source).toContain('unsubscribe()');
    expect(source).toContain('presentationResources.release(lease)');
    expect(source).toContain('if (released) return');
    expect(source).not.toMatch(/\$lib\/transport|hardware-scope/);
    expect(source).not.toMatch(/acquire\('audio-fft',\s*(fftPush|frame|unsubscribe)/);
  });

  it('shares one channel until the final panel or LCD lease is released', async () => {
    const channel = makeChannel();
    const controller = new ScopeController(() => channel as never);
    const host = new PresentationResourceHost<unknown>('test');
    controller.registerPresentationDriver(host);

    const panel = host.acquire('audio-fft', 'AudioSpectrumPanel');
    controller.registerPresentationDriver(host);
    const scope = host.acquire('audio-fft', 'AmberScope');
    controller.registerPresentationDriver(host);
    const cockpit = host.acquire('audio-fft', 'AmberCockpit');
    const unsubscribeScope = controller.subscribe(vi.fn());
    const unsubscribeCockpit = controller.subscribe(vi.fn());

    expect(new Set([panel, scope, cockpit]).size).toBe(3);
    await vi.waitFor(() => expect(channel.connect).toHaveBeenCalledTimes(1));
    expect(channel.handlerCount()).toBe(1);
    expect(host.release(panel)).toBe(true);
    expect(channel.disconnect).not.toHaveBeenCalled();
    unsubscribeScope();
    expect(host.release(scope)).toBe(true);
    expect(channel.disconnect).not.toHaveBeenCalled();
    unsubscribeCockpit();
    expect(host.release(cockpit)).toBe(true);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
    expect(channel.handlerCount()).toBe(0);
    expect(host.release(cockpit)).toBe(false);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);
  });

  it('opens no channel when audio FFT is unavailable', async () => {
    const channel = makeChannel();
    const controller = new ScopeController(() => channel as never);
    const host = new PresentationResourceHost<unknown>('test');
    host.configure('audio-fft', {
      available: false,
      selected: true,
      driver: controller.audioFftDriver,
    });

    const lease = host.acquire('audio-fft', 'AmberScope');
    await Promise.resolve();
    expect(channel.connect).not.toHaveBeenCalled();
    expect(host.release(lease)).toBe(true);
    expect(channel.disconnect).not.toHaveBeenCalled();
  });
});
