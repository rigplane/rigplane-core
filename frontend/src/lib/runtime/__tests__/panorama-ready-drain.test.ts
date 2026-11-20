import { describe, expect, it } from 'vitest';
import { drainAfterFirstDraw, type GlDrainWindow } from '../../../../tests/e2e/journey/panorama-ready';

describe('panorama pipeline drain', () => {
  it('drains only after a draw was submitted, even when the first draw lands after several frames', async () => {
    const events: string[] = [];
    const win: GlDrainWindow = {
      __journeyGlContexts: [{ finish: () => events.push('finish') }],
      __journeyGlDraws: 0,
    };
    let frames = 0;
    // Visibility arrives late: the renderer's first draw is submitted only
    // after the helper's second callback.
    const nextFrame = async () => {
      frames += 1;
      events.push(`frame${frames}`);
      if (frames === 3) { win.__journeyGlDraws = 1; events.push('draw'); }
    };
    await drainAfterFirstDraw(win, nextFrame);
    expect(events.indexOf('finish')).toBeGreaterThan(events.indexOf('draw'));
    expect(events.filter((event) => event === 'finish')).toHaveLength(2);
  });
  it('does not drain a page that never drew', async () => {
    const win: GlDrainWindow = { __journeyGlContexts: [{ finish: () => { throw new Error('drained too early'); } }], __journeyGlDraws: 0 };
    let frames = 0;
    await expect(drainAfterFirstDraw(win, async () => { if (++frames > 50) throw new Error('gave up'); })).rejects.toThrow('gave up');
  });
});
