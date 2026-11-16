import { afterEach, describe, expect, it, vi } from 'vitest';
import hostWire from '../../../../tests/fixtures/host-api-v3-wire.json' with { type: 'json' };
import type { TestCase, TestResult } from '@playwright/test/reporter';
import NoSkipsReporter, { REQUIRED_MATRIX, rowOf } from './no-skips-reporter';
import {
  collectFirstTexts,
  foreignDestinations,
  acquireControllerKey,
  buildFreqRequest,
  parseRelayedAnswer,
  parseSimState,
  unkeyLatencyMs,
  type SimState,
} from './station';

/**
 * Unit tests of the remote E2E harness's own rules (playwright.remote
 * .config.ts), run with `npx vitest run -c tests/e2e/remote/vitest.config.ts`:
 * the no-skips reporter, the unkey-latency rule, the origin predicate and
 * the attach-ack collection of the P0 probe.
 */

type Status = TestResult['status'];

/** A TestCase shaped like Playwright's for the fields the reporter reads. */
function fakeTest(project: string, row: string, repeat: number): TestCase {
  return {
    id: `${project}-${row}-${repeat}`,
    titlePath: () => ['', project, 'remote.spec.ts', `${row}: behaviour`],
    repeatEachIndex: repeat,
    parent: { project: () => ({ name: project }) },
  } as unknown as TestCase;
}

interface Executed {
  project: string;
  row: string;
  repeat: number;
  status?: Status;
}

/** What a (possibly filtered) run does: one onTestEnd per executed case,
 * then the verdict from onEnd. */
async function verdict(executed: Executed[]): Promise<{ status: 'failed' } | undefined> {
  const reporter = new NoSkipsReporter();
  const tests = executed.map((e) => fakeTest(e.project, e.row, e.repeat));
  executed.forEach((e, index) =>
    reporter.onTestEnd(tests[index], { status: e.status ?? 'passed' } as TestResult),
  );
  return reporter.onEnd();
}

const everyCase = (filter: (c: Executed) => boolean = () => true): Executed[] =>
  REQUIRED_MATRIX.projects
    .flatMap((project) =>
      REQUIRED_MATRIX.rows.flatMap((row) =>
        Array.from({ length: REQUIRED_MATRIX.repeatEach }, (_, repeat) => ({
          project,
          row,
          repeat,
        })),
      ),
    )
    .filter(filter);

describe('NoSkipsReporter: the required matrix, not the filtered suite', () => {
  it('passes the full matrix (20 cases, R-A1 included)', async () => {
    expect(everyCase()).toHaveLength(20);
    expect(REQUIRED_MATRIX.rows).toContain('R-A1');
    expect(await verdict(everyCase())).toBeUndefined();
  });

  it('fails when --project filtered one browser away', async () => {
    const result = await verdict(everyCase((c) => c.project === 'chromium'));
    expect(result).toEqual({ status: 'failed' });
  });

  it('fails when --grep filtered rows away (the R-A1 repetitions too)', async () => {
    const result = await verdict(everyCase((c) => c.row === 'L1'));
    expect(result).toEqual({ status: 'failed' });
    const withoutRemoteRow = await verdict(everyCase((c) => c.row !== 'R-A1'));
    expect(withoutRemoteRow).toEqual({ status: 'failed' });
  });

  it('reads the hyphenated row id of a test title', () => {
    expect(rowOf('R-A1: remote boot, real seam')).toBe('R-A1');
    expect(rowOf('L2: tuning follows both ways')).toBe('L2');
    expect(rowOf('no row prefix here')).toBeNull();
  });

  it('fails when --repeat-each=1 reduced the repetitions', async () => {
    const result = await verdict(everyCase((c) => c.repeat === 0));
    expect(result).toEqual({ status: 'failed' });
  });

  it('fails when a case was skipped', async () => {
    const executed = everyCase().map((c, i) => ({
      ...c,
      status: (i === 3 ? 'skipped' : 'passed') as Status,
    }));
    expect(await verdict(executed)).toEqual({ status: 'failed' });
  });

  it('fails when a case did not complete', async () => {
    const executed = everyCase().map((c, i) => ({
      ...c,
      status: (i === 5 ? 'timedOut' : 'passed') as Status,
    }));
    expect(await verdict(executed)).toEqual({ status: 'failed' });
  });
});

const state = (keyed: boolean, timeline: [number, boolean][]): SimState => ({
  ptt_on: keyed,
  freq_hz: 14_074_000,
  ptt_transitions: timeline,
});

describe('unkeyLatencyMs: the unkey that answers this press/release', () => {
  const pressed = 100_000;
  const released = 101_000;

  it('measures the OFF transition after the release', () => {
    const latency = unkeyLatencyMs(
      state(false, [[100.05, true], [101.2, false]]),
      pressed,
      released,
    );
    expect(latency).toBeCloseTo(200, 3);
  });

  it('is not settled while the simulator is still keyed (OFF then ON)', () => {
    const latency = unkeyLatencyMs(
      state(true, [[100.05, true], [101.05, false], [101.1, true]]),
      pressed,
      released,
    );
    expect(latency).toBeNull();
  });

  it('ignores a historical OFF from before the release', () => {
    const latency = unkeyLatencyMs(
      state(false, [[99.9, false], [100.05, true]]),
      pressed,
      released,
    );
    expect(latency).toBeNull();
  });

  it('never reports a negative latency for a slightly earlier OFF', () => {
    const latency = unkeyLatencyMs(
      state(false, [[100.05, true], [100.9, false]]),
      pressed,
      released,
    );
    expect(latency).toBeNull();
  });

  it('is not settled without a keying after the press', () => {
    expect(unkeyLatencyMs(state(false, [[101.2, false]]), pressed, released)).toBeNull();
  });

  it('uses the final OFF when the radio re-keyed in between', () => {
    const latency = unkeyLatencyMs(
      state(false, [[100.05, true], [101.05, false], [101.1, true], [101.6, false]]),
      pressed,
      released,
    );
    expect(latency).toBeCloseTo(600, 3);
  });
});

describe('foreignDestinations: HTTP and WebSocket destinations', () => {
  it('accepts the station over http and ws', () => {
    expect(
      foreignDestinations(['http://127.0.0.1:18780/', 'ws://127.0.0.1:18780/api/v1/ws']),
    ).toEqual([]);
  });

  it('flags a foreign WebSocket destination', () => {
    expect(foreignDestinations(['ws://198.51.100.7:18780/api/v1/ws'])).toEqual([
      'http://198.51.100.7:18780',
    ]);
  });

  it('flags a wss destination on the station host (not the station origin)', () => {
    expect(foreignDestinations(['wss://127.0.0.1:18780/api/v1/ws'])).toEqual([
      'https://127.0.0.1:18780',
    ]);
  });

  it('flags a foreign http origin', () => {
    expect(foreignDestinations(['https://example.com/a'])).toEqual(['https://example.com']);
  });
});

describe('collectFirstTexts: every listener exists before any attach is sent', () => {
  const message = (data: unknown) => Object.assign(new Event('message'), { data });
  const channels = () => ({
    control: new EventTarget(),
    scope: new EventTarget(),
    audio: new EventTarget(),
  });
  const deadlines = { control: 200, scope: 200, audio: 200 };

  it('collects the acks when scope and audio answer before control', async () => {
    const c = channels();
    const frames = await collectFirstTexts(
      c,
      () => {
        c.scope.dispatchEvent(message('scope-ack'));
        c.audio.dispatchEvent(message('audio-ack'));
        c.control.dispatchEvent(message('control-hello'));
      },
      deadlines,
    );
    expect(frames).toEqual({ control: 'control-hello', scope: 'scope-ack', audio: 'audio-ack' });
  });

  it('collects the acks in the usual order too', async () => {
    const c = channels();
    const frames = await collectFirstTexts(
      c,
      () => {
        c.control.dispatchEvent(message('control-hello'));
        c.scope.dispatchEvent(message('scope-ack'));
        c.audio.dispatchEvent(message('audio-ack'));
      },
      deadlines,
    );
    expect(frames.control).toBe('control-hello');
  });

  it('ignores binary frames and times out on a silent channel', async () => {
    const c = channels();
    await expect(
      collectFirstTexts(
        c,
        () => {
          c.control.dispatchEvent(message('control-hello'));
          c.scope.dispatchEvent(message(new Uint8Array(1)));
          c.audio.dispatchEvent(message('audio-ack'));
        },
        deadlines,
      ),
    ).rejects.toThrow('scope timed out');
  });
});

describe('producer contracts consumed by the harness', () => {
  const snapshot = {
    ptt_on: 'bool',
    freq_hz: 'int',
    ptt_transitions: 'tuple[tuple[float, bool], ...]',
  };
  // The whole document `GET /sim/contract` serves: one entry per sidecar
  // dataclass (SimSnapshot response, SimFreqRequest request).
  const contract = { SimSnapshot: snapshot, SimFreqRequest: { hz: 'int' } };
  const good = { ptt_on: false, freq_hz: 14_074_000, ptt_transitions: [[1.5, true]] };

  it('accepts a sidecar response that matches the served contract', () => {
    expect(parseSimState(contract, good)).toEqual(good);
  });

  it('throws when the producer changed a field type', () => {
    expect(() => parseSimState({ ...contract, SimSnapshot: { ...snapshot, freq_hz: 'float' } }, good)).toThrow('contract drift');
  });

  it('throws when the producer dropped or renamed a field', () => {
    const { ptt_on: _dropped, ...rest } = snapshot;
    expect(() => parseSimState({ ...contract, SimSnapshot: rest }, good)).toThrow('contract drift');
  });

  it('builds the frequency request from the served contract', () => {
    expect(buildFreqRequest(contract, 14_074_000)).toEqual({ hz: 14_074_000 });
    expect(() => buildFreqRequest({ ...contract, SimFreqRequest: { hz: 'float' } }, 1)).toThrow(
      'contract drift',
    );
    expect(() => buildFreqRequest({ ...contract, SimFreqRequest: { freq: 'int' } }, 1)).toThrow(
      'contract drift',
    );
    expect(() => buildFreqRequest(contract, 14_074_000.5)).toThrow('hz');
  });

  it('throws when a response violates the contract', () => {
    expect(() => parseSimState(contract, { ...good, ptt_on: 'yes' })).toThrow('ptt_on');
    expect(() => parseSimState(contract, { ...good, freq_hz: 1.5 })).toThrow('freq_hz');
    expect(() => parseSimState(contract, { ...good, ptt_transitions: [[1, 2]] })).toThrow(
      'ptt_transitions',
    );
    expect(() => parseSimState(contract, { ...good, extra: 1 })).toThrow('keys');
    expect(() => parseSimState(contract, { ptt_on: true })).toThrow('keys');
  });

  it('types the relayed answer by its status alone; the browser validates the SDP', () => {
    // sdp/type are the W3C RTCSessionDescriptionInit pair: setRemoteDescription
    // is their validator, so the harness does not second-guess them.
    const real = { status: 'ok', sessionId: 's1', sdp: 'v=0', type: 'answer' };
    expect(parseRelayedAnswer(real)).toEqual(real);
    const odd = { status: 'ok', sdp: 'not even sdp', type: 'pranswer' };
    expect(parseRelayedAnswer(odd)).toEqual(odd);
    expect(() => parseRelayedAnswer({ status: 'error', code: 'sdp_error' })).toThrow('refused');
    expect(() => parseRelayedAnswer(null)).toThrow('refused');
    expect(() => parseRelayedAnswer('ok')).toThrow('refused');
  });

  describe('controller grant, consumed by the production ControllerClient', () => {
    // The producer-built golden document (tests/fixtures/host-api-v3-wire.json,
    // asserted equal to ControllerAuthority's reply by
    // tests/test_host_api_v3_wire_contract.py), never a re-typed grant.
    const grant = hostWire.controllerGrant;

    /** A station answering GET (status) and POST (grant) of /api/v1/controller. */
    function station(mode: 'local' | 'remote', body: unknown, code = 201) {
      const fetcher = vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? new Response(JSON.stringify(body), { status: code })
          : new Response(JSON.stringify({ ...hostWire.controllerStatus, mode })),
      );
      vi.stubGlobal('fetch', fetcher);
      return fetcher;
    }

    afterEach(() => vi.unstubAllGlobals());

    it('yields the key of the producer grant, against the station origin', async () => {
      for (const mode of ['local', 'remote'] as const) {
        const fetcher = station(mode, grant);
        expect(await acquireControllerKey()).toBe(grant.controller_key);
        expect(fetcher.mock.calls.every(([url]) => url.startsWith('http://127.0.0.1:'))).toBe(true);
      }
    });

    it('rejects a grant missing any producer field, in both modes', async () => {
      for (const mode of ['local', 'remote'] as const) {
        for (const field of Object.keys(grant)) {
          const { [field as keyof typeof grant]: _dropped, ...partial } = grant;
          station(mode, partial);
          await expect(acquireControllerKey()).rejects.toThrow();
        }
      }
    });

    it('rejects a refusal', async () => {
      station('remote', { error: 'controller_busy' }, 409);
      await expect(acquireControllerKey()).rejects.toThrow();
    });
  });
});
