/**
 * Per-environment web-storage isolation for vitest.
 *
 * The repo convention runs vitest with `NODE_OPTIONS="--localstorage-file=…"`
 * (without it, Node's `localStorage` global throws and ~50+ unrelated tests
 * fail as an env artifact). That file-backed Storage is ONE store shared by
 * every worker thread in the process: `isolate: true` gives each test file a
 * fresh module registry, but all files still read and write the same backing
 * store, concurrently. Any file's `localStorage.setItem`/`clear` raced every
 * other file's module-load reads and `beforeEach` seeding — the source of
 * order-dependent flakes with no production change (canonical victim:
 * `mod-input-tx-guard.isolated.test.ts`, silenced by a sibling file's leaked
 * `rigplane:auto-lan-mod-input=true`; also the panel-order seeding in the
 * semantic-desktop/lcd migration component tests).
 *
 * This setup file shadows `localStorage`/`sessionStorage` with an in-memory
 * Storage per test environment: genuinely per-file under `isolate: true`,
 * per-worker in the `fast` pool (strictly less shared than before). It uses
 * `Object.defineProperty`, NOT `vi.stubGlobal`, on purpose: tests that stub
 * storage themselves (e.g. the workspace purity suites) must have
 * `vi.unstubAllGlobals()` restore to THIS stub, never to the shared Node
 * store.
 *
 * Contract pinned by `src/__tests__/storage-isolation.isolated.test.ts`.
 */

import { afterAll, afterEach, beforeEach, expect, onTestFinished, vi } from 'vitest';
import type { OnTestFinishedHandler } from 'vitest';
import { threadId } from 'node:worker_threads';
import { monitorEventLoopDelay } from 'node:perf_hooks';
import { resetSharedTuningAccumulatorForTests } from './src/lib/runtime/commands/tuning-accumulator';

/** Marker property test code can use to assert the stub is installed. */
export const TEST_STORAGE_MARKER = '__rigplaneTestStorage';

function makeMemoryStorage(): Storage {
  const data = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return data.size;
    },
    key(index: number): string | null {
      return [...data.keys()][index] ?? null;
    },
    getItem(key: string): string | null {
      return data.get(String(key)) ?? null;
    },
    setItem(key: string, value: string): void {
      data.set(String(key), String(value));
    },
    removeItem(key: string): void {
      data.delete(String(key));
    },
    clear(): void {
      data.clear();
    },
  };
  Object.defineProperty(storage, TEST_STORAGE_MARKER, {
    value: true,
    enumerable: false,
  });
  return storage;
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, {
    value: makeMemoryStorage(),
    writable: true,
    configurable: true,
  });
}

// MOR-1425 review B5: the tuning accumulator is now a module-level
// singleton shared by every `makeVfoHandlers()` caller (production
// correctness — see `tuning-accumulator.ts`). Left unreset, a "hot" burst
// left pending by one test's fake-timer clock is misread as still-hot by
// the next test in the same file (`isolate: true` is per-FILE, not
// per-test — same class of leak `localStorage` above was patched for).
afterEach(() => {
  resetSharedTuningAccumulatorForTests();
});

// ---------------------------------------------------------------------------
// MOR-3777 instrument-only failure snapshots (observability; parent-
// authorized). Active for EXACTLY four allowlisted files via one suffix
// check; unrelated files get zero output and zero further work. Numeric or
// enumerated metadata only: file F-id + case ordinal, real
// threadId/poolId/pid, fake-timer READS (timerCount only while fake timers
// are active), a native monotonic case-lifecycle span, the native
// event-loop-delay histogram (CUMULATIVE over the file's run in this
// worker; all-zero samples report UNKNOWN, not "no lag") and read-only
// PROCESS-scope resource counters. No raw test names, paths or
// parameter values; no timer wrapping/reset/advance, no vi mocks, no sleeps
// or yields; nothing throws and no output happens inside a measured test
// body — the per-failure line and per-file summary run in after-test hooks
// and are capped. Verdicts come from the pinned Vitest 4.1 `onTestFinished`
// hook reading `context.task.result.state` (typed TaskState); anything
// unreadable stays UNKNOWN. Observation adds real, unmeasured overhead per
// test; no effect outside these reads is claimed.
// ---------------------------------------------------------------------------

const MOR3777_FILES: ReadonlyArray<readonly [string, string]> = [
  ['src/components-v2/layout/__tests__/RadioLayout.isolated.test.ts', 'F1'],
  ['src/components-v2/layout/__tests__/RadioLayout.audio-fft.component.test.ts', 'F2'],
  ['src/components-v2/wiring/__tests__/freshness-invisibility.component.test.ts', 'F3'],
  ['src/components-v2/wiring/__tests__/semantic-rf-front-end-wiring.component.test.ts', 'F4'],
];
const MOR3777_STARTS_MAX = 64;
const MOR3777_EMIT_MAX = 8;
const MOR3777_VERDICTS = new Set(['pass', 'fail', 'skip', 'todo']);

// Native monotonic clock bound ONCE at module import, i.e. before any test
// can install vi fake timers (even `toFake: ['hrtime']`). Span semantics:
// beforeEach entry -> onTestFinished callback (case lifecycle); NOT the
// test body and NOT Vitest's own duration. Null => UNKNOWN, never a throw.
const mor3777Hrtime: (() => bigint) | null =
  typeof process.hrtime?.bigint === 'function'
    ? process.hrtime.bigint.bind(process.hrtime)
    : null;

type Mor3777Timers = { fake: boolean | 'UNKNOWN'; timerCount: number | null; t0: bigint | null };

const mor3777 = {
  active: false,
  fileId: '?',
  cases: 0,
  emitted: 0,
  verdicts: { pass: 0, fail: 0, other: 0 },
  starts: [] as Mor3777Timers[],
  histogram: null as ReturnType<typeof monitorEventLoopDelay> | null,
};

function mor3777Timers(): Mor3777Timers {
  try {
    const t0 = mor3777Hrtime ? mor3777Hrtime() : null;
    if (!vi.isFakeTimers()) return { fake: false, timerCount: null, t0 };
    return { fake: true, timerCount: vi.getTimerCount(), t0 };
  } catch {
    return { fake: 'UNKNOWN', timerCount: null, t0: null };
  }
}

function mor3777Lag(): { mean: string; max: string } {
  const histogram = mor3777.histogram;
  if (!histogram) return { mean: 'UNAVAILABLE', max: 'UNAVAILABLE' };
  try {
    if (histogram.count === 0) return { mean: 'UNKNOWN', max: 'UNKNOWN' };
    return { mean: (histogram.mean / 1e6).toFixed(3), max: (histogram.max / 1e6).toFixed(3) };
  } catch {
    return { mean: 'UNKNOWN', max: 'UNKNOWN' };
  }
}

function mor3777PoolId(): string {
  const raw = process.env.VITEST_POOL_ID;
  return typeof raw === 'string' && /^\d+$/.test(raw) ? raw : 'UNKNOWN';
}

function mor3777Resource(): string {
  try {
    const rssMb = (process.memoryUsage().rss / 1048576).toFixed(1);
    const usage = typeof process.resourceUsage === 'function' ? process.resourceUsage() : null;
    if (!usage) return `proc_rss_mb=${rssMb}`;
    return (
      `proc_rss_mb=${rssMb} proc_cpu_user_ms=${Math.round(usage.userCPUTime / 1000)}` +
      ` proc_max_rss_kb=${usage.maxRSS} proc_vctx=${usage.voluntaryContextSwitches}` +
      ` proc_ictx=${usage.involuntaryContextSwitches}`
    );
  } catch {
    return 'proc_resource=UNAVAILABLE';
  }
}

function mor3777Finish(
  context: Parameters<OnTestFinishedHandler>[0],
  start: Mor3777Timers,
): void {
  try {
    const state = context?.task?.result?.state;
    const verdict = typeof state === 'string' && MOR3777_VERDICTS.has(state) ? state : 'other';
    if (verdict === 'pass' || verdict === 'fail') mor3777.verdicts[verdict] += 1;
    else mor3777.verdicts.other += 1;
    if (verdict !== 'fail' || mor3777.emitted >= MOR3777_EMIT_MAX) return;
    mor3777.emitted += 1;
    const timers = mor3777Timers();
    const lag = mor3777Lag();
    const span =
      start.t0 !== null && timers.t0 !== null ? Number(timers.t0 - start.t0) / 1e6 : null;
    console.log(
      `[mor-3777-obs case ${mor3777.fileId}/${mor3777.cases} tid=${threadId}` +
        ` pool=${mor3777PoolId()} pid=${process.pid} fake_start=${String(start.fake)}` +
        ` fake_end=${String(timers.fake)} timers_end=${timers.timerCount ?? 'UNAVAILABLE'}` +
        ` case_lifecycle_ms=${span === null ? 'UNKNOWN' : span.toFixed(3)}` +
        ` evd_cum_mean_ms=${lag.mean} evd_cum_max_ms=${lag.max} ${mor3777Resource()}` +
        ` verdict=fail]`,
    );
  } catch {
    /* diagnostics never mask a test failure */
  }
}

beforeEach(() => {
  try {
    const testPath = expect.getState().testPath;
    const fileId = MOR3777_FILES.find(([suffix]) => testPath?.endsWith(suffix))?.[1];
    if (!fileId) return;
    if (!mor3777.active) {
      mor3777.active = true;
      mor3777.fileId = fileId;
      mor3777.histogram = monitorEventLoopDelay({ resolution: 20 });
      mor3777.histogram.enable();
    }
    mor3777.cases += 1;
    const start = mor3777Timers();
    mor3777.starts.push(start);
    if (mor3777.starts.length > MOR3777_STARTS_MAX) mor3777.starts.shift();
    onTestFinished((context) => mor3777Finish(context, start));
  } catch {
    /* never break a test */
  }
});

afterAll(() => {
  try {
    if (!mor3777.active) return;
    const lag = mor3777Lag();
    console.log(
      `[mor-3777-obs file ${mor3777.fileId} cases=${mor3777.cases}` +
        ` emitted=${mor3777.emitted} pass=${mor3777.verdicts.pass}` +
        ` fail=${mor3777.verdicts.fail} other=${mor3777.verdicts.other}` +
        ` evd_cum_mean_ms=${lag.mean} evd_cum_max_ms=${lag.max} ${mor3777Resource()}]`,
    );
    mor3777.histogram?.disable();
  } catch {
    /* never break a test */
  } finally {
    mor3777.histogram = null;
    mor3777.active = false;
    mor3777.starts = [];
  }
});
