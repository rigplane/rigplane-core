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

// These entry points must drain before actionability and after the UI changes.
describe('panorama action entry points', () => {
  it('drains around a click without increasing the existing action timeout', async () => {
    const events: string[] = [];
    const page = { evaluate: async () => { events.push('drain'); } };
    const control = { click: async (options: unknown) => { events.push(`click:${JSON.stringify(options)}`); } };
    const { clickPanorama } = await import('../../../../tests/e2e/journey/panorama-ready');
    await clickPanorama(page as never, control as never);
    expect(events).toEqual(['drain', 'click:{"timeout":10000}', 'drain']);
  });

  it('reloads to commit before draining the new document instead of waiting for load', async () => {
    const events: string[] = [];
    const page = {
      evaluate: async () => { events.push('drain'); },
      reload: async (options: unknown) => { events.push(`reload:${JSON.stringify(options)}`); },
    };
    const { reloadPanorama } = await import('../../../../tests/e2e/journey/panorama-ready');
    await reloadPanorama(page as never);
    expect(events).toEqual(['drain', 'reload:{"waitUntil":"commit","timeout":10000}', 'drain']);
  });

  it('does not dispatch a click when the pre-action pipeline drain fails', async () => {
    const page = { evaluate: async () => { throw new Error('renderer failed'); } };
    let clicked = false;
    const control = { click: async () => { clicked = true; } };
    const { clickPanorama } = await import('../../../../tests/e2e/journey/panorama-ready');
    await expect(clickPanorama(page as never, control as never)).rejects.toThrow('renderer failed');
    expect(clicked).toBe(false);
  });
});

describe('panorama frequency readiness', () => {
  it('drains before waiting for the rendered VFO and retains the original 10 s condition budget', async () => {
    const events: string[] = [];
    const page = {
      evaluate: async () => { events.push('drain'); },
      waitForFunction: async (_predicate: unknown, hz: number, options: unknown) => {
        events.push(`frequency:${hz}:${JSON.stringify(options)}`);
      },
    };
    const { expectPanoramaFrequency } = await import('../../../../tests/e2e/journey/panorama-ready');
    await expectPanoramaFrequency(page as never, 14_290_000);
    expect(events).toEqual(['drain', 'frequency:14290000:{"timeout":10000}', 'drain']);
  });
});
