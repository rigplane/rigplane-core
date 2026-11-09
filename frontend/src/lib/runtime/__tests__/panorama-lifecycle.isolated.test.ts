import { expect, it, vi } from 'vitest';

const harness = vi.hoisted(() => ({ kill: vi.fn(), run: async (_context: unknown, _info: unknown) => {} }));
vi.mock('@playwright/test', () => ({ expect,
  test: (_name: string, run: typeof harness.run) => { harness.run = run; },
}));
vi.mock('../../../../tests/e2e/journey/server-process', () => ({
  startJourneyServer: async () => ({ kill: harness.kill }),
}));

it('kills the acquired panorama server when browser instrumentation setup fails', async () => {
  await import('../../../../tests/e2e/journey/panorama-settle.spec');
  const failure = new Error('instrumentation failed');
  await expect(harness.run({ page: { on() {}, addInitScript: async () => { throw failure; } } },
    { outputPath: () => 'radio.jsonl' })).rejects.toBe(failure);
  expect(harness.kill).toHaveBeenCalledOnce();
});
