import { describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeStream } from '../../../lib/runtime/scope-stream';
import LcdAfFft from '../LcdAfFft.svelte';
import LcdRfPanadapter from '../LcdRfPanadapter.svelte';

function source(kind: 'radio' | 'audio_fft') {
  let receive = (_buffer: ArrayBuffer) => {};
  const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
    { source: kind, sourceId: kind, epoch: 1 }, 'borrowed');
  return { stream, frame(receiver = 0, pixels = [0, 128, 255]) {
    const bytes = new ArrayBuffer(16 + pixels.length);
    const view = new DataView(bytes);
    view.setUint8(0, 1); view.setUint8(1, receiver);
    view.setUint32(3, 14_200_000, true); view.setUint32(7, 14_300_000, true);
    view.setUint16(14, pixels.length, true); new Uint8Array(bytes, 16).set(pixels);
    receive(bytes); flushSync();
  } };
}

describe('LCD leaves consume ScopeStream directly', () => {
  it('paints audio pixels without a reactive frame carrier and clears stale geometry', async () => {
    const target = document.createElement('div'); document.body.append(target);
    const wire = source('audio_fft');
    const component = mount(LcdAfFft, { target, props: {
      scopeStream: wire.stream, receiver: 'MAIN', inputState: 'live', receiverActivity: 'active',
    } as never });
    try {
      flushSync(); wire.frame();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('live');
      expect(target.querySelector('.fft-trace')?.getAttribute('d')).toBe('M0.00,92.00 L250.00,50.84 L500.00,10.00');
      wire.stream.setFreshness('stale'); flushSync();
      expect(target.querySelector('[data-fft-mode]')?.getAttribute('data-fft-mode')).toBe('safe-empty');
    } finally { await unmount(component); wire.stream.dispose(); target.remove(); }
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
