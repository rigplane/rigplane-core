import { expect, it, vi } from 'vitest';

// Registrations are captured by NAME: the assertion below targets the settle
// loop's own test (the one with browser instrumentation), independent of how
// many other tests the spec registers afterwards (MOR-4387 added more).
const harness = vi.hoisted(() => ({
  kill: vi.fn(),
  runs: new Map<string, (context: unknown, info: unknown) => Promise<void>>(),
}));
vi.mock('@playwright/test', () => ({ expect,
  test: (name: string, run: (context: unknown, info: unknown) => Promise<void>) => { harness.runs.set(name, run); },
}));
vi.mock('../../../../tests/e2e/journey/server-process', () => ({
  startJourneyServer: async () => ({ kill: harness.kill }),
}));

it('kills the acquired panorama server when browser instrumentation setup fails', async () => {
  await import('../../../../tests/e2e/journey/panorama-settle.spec');
  const run = harness.runs.get('IC-705: one drag settles subscriptions and WebKit renderer work')!;
  const failure = new Error('instrumentation failed');
  await expect(run({ page: { on() {}, addInitScript: async () => { throw failure; } } },
    { outputPath: () => 'radio.jsonl' })).rejects.toBe(failure);
  expect(harness.kill).toHaveBeenCalledOnce();
});
