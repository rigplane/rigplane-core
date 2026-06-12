import { describe, expect, it } from 'vitest';

import {
  evaluateBrowserAudioSelfTest,
  type BrowserAudioSelfTestAnalyzerInput,
} from '../browser-audio-self-test';
import type { BrowserAudioRouteState } from '../browser-audio-route';

const selectedRoute: BrowserAudioRouteState = {
  status: 'selected',
  outputSelectionSupported: true,
  permissionState: 'granted',
  routeId: 'example-loopback',
  input: {
    kind: 'audioinput',
    label: 'Example Bridge Capture',
    scopedDeviceId: 'scoped-input',
    isAlias: false,
  },
  output: {
    kind: 'audiooutput',
    label: 'Example Bridge Playback',
    scopedDeviceId: 'scoped-output',
    isAlias: false,
  },
};

const missingRoute: BrowserAudioRouteState = {
  status: 'missing-endpoints',
  outputSelectionSupported: true,
  permissionState: 'granted',
  missing: ['input'],
};

const passingAnalyzer: BrowserAudioSelfTestAnalyzerInput = {
  knownTone: {
    detected: true,
    frequencyHz: 1000,
    confidence: 0.96,
  },
  level: {
    measured: true,
    rmsDbfs: -18,
    peakDbfs: -6,
    silenceDetected: false,
    clippingDetected: false,
    usableRmsDbfs: {
      min: -42,
      max: -8,
    },
  },
  latency: {
    measured: false,
  },
};

describe('browser audio self-test model', () => {
  it('passes receive readiness from known-tone, non-silent, non-clipping input in the usable level range', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: passingAnalyzer,
    });

    expect(state.route.status).toBe('selected');
    expect(state.knownTone.status).toBe('detected');
    expect(state.level.status).toBe('usable');
    expect(state.receive.ready).toBe(true);
    expect(state.receive.verdict).toBe('pass');
    expect(state.receive.blockers).toEqual([]);
    expect(state.tx.ready).toBe(false);
    expect(state.tx.blockers).toContain('safe-tx-validation-required');
  });

  it('fails receive readiness when analyzer marks the input as silence', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: {
        ...passingAnalyzer,
        level: {
          ...passingAnalyzer.level,
          rmsDbfs: -80,
          peakDbfs: -72,
          silenceDetected: true,
        },
      },
    });

    expect(state.level.status).toBe('silence');
    expect(state.receive.ready).toBe(false);
    expect(state.receive.verdict).toBe('fail');
    expect(state.receive.blockers).toContain('silence');
  });

  it('fails receive readiness when analyzer marks clipping', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: {
        ...passingAnalyzer,
        level: {
          ...passingAnalyzer.level,
          peakDbfs: 0,
          clippingDetected: true,
        },
      },
    });

    expect(state.level.status).toBe('clipping');
    expect(state.receive.ready).toBe(false);
    expect(state.receive.blockers).toContain('clipping');
  });

  it('fails receive readiness when the route is wrong or missing', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: missingRoute,
      analyzer: passingAnalyzer,
    });

    expect(state.route.status).toBe('wrong-or-missing');
    expect(state.route.sourceStatus).toBe('missing-endpoints');
    expect(state.receive.ready).toBe(false);
    expect(state.receive.blockers).toContain('wrong-or-missing-route');
  });

  it('keeps unmeasured latency as a caveated non-blocking receive status', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: passingAnalyzer,
    });

    expect(state.latency).toMatchObject({
      status: 'not-measured',
      blockingReceive: false,
      unit: 'ms',
    });
    expect(state.receive.ready).toBe(true);
    expect(state.receive.caveats).toContain('latency-not-measured');
  });

  it('reports measured latency in milliseconds with analyzer caveats', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: {
        ...passingAnalyzer,
        latency: {
          measured: true,
          milliseconds: 37.4,
          caveats: ['single-run-estimate'],
        },
      },
    });

    expect(state.latency).toEqual({
      status: 'measured',
      milliseconds: 37.4,
      unit: 'ms',
      blockingReceive: false,
      caveats: ['single-run-estimate'],
    });
  });

  it('fails receive readiness when the known tone is missing', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: {
        ...passingAnalyzer,
        knownTone: {
          detected: false,
        },
      },
    });

    expect(state.knownTone.status).toBe('missing');
    expect(state.receive.ready).toBe(false);
    expect(state.receive.blockers).toContain('known-tone-missing');
  });

  it('fails receive readiness when measured level is outside the usable range', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: {
        ...passingAnalyzer,
        level: {
          ...passingAnalyzer.level,
          rmsDbfs: -4,
        },
      },
    });

    expect(state.level.status).toBe('out-of-range');
    expect(state.receive.ready).toBe(false);
    expect(state.receive.blockers).toContain('level-out-of-range');
  });

  it('keeps transmit readiness false by default even when receive passes', () => {
    const state = evaluateBrowserAudioSelfTest({
      route: selectedRoute,
      analyzer: passingAnalyzer,
    });

    expect(state.receive.ready).toBe(true);
    expect(state.tx).toEqual({
      ready: false,
      verdict: 'blocked',
      blockers: ['safe-tx-validation-required'],
      caveats: [],
    });
  });

  it('keeps new self-test files free of product endpoint labels and platform policy words', async () => {
    const fs = await import('node:fs/promises');
    const path = await import('node:path');
    const files = [
      path.resolve(__dirname, '../browser-audio-self-test.ts'),
      path.resolve(__dirname, './browser-audio-self-test.test.ts'),
    ];
    const blocked = [
      ['Rig', 'Plane'].join(''),
      ['Win', 'dows'].join(''),
      ['SYS', 'VAD'].join(''),
      ['reg', 'istry'].join(''),
      ['hard', 'ware ID'].join(''),
      ['IOC', 'TL'].join(''),
      ['ins', 'tall'].join(''),
      ['re', 'pair'].join(''),
      ['entitle', 'ment'].join(''),
    ];

    for (const file of files) {
      const content = await fs.readFile(file, 'utf8');
      const relative = path.relative(process.cwd(), file);
      for (const term of blocked) {
        expect(content, `${relative} contains ${term}`).not.toContain(term);
      }
    }
  });
});
