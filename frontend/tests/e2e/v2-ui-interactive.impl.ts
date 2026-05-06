import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PAGE_URL = process.env.RIGPLANE_V2_URL ?? process.env.ICOM_LAN_V2_URL;
const PAGE_ORIGIN = PAGE_URL ? new URL(PAGE_URL).origin : 'http://127.0.0.1:8080';
const STATE_URL = `${PAGE_ORIGIN}/api/v1/state`;
const CAPABILITIES_URL = `${PAGE_ORIGIN}/api/v1/capabilities`;
const CONTROL_DELAY_MS = 700;
const CLEANUP_DELAY_MS = 1200;
const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const SCREENSHOT_DIR = path.join(ROOT_DIR, 'docs', 'reviews', 'screenshots');
const REPORT_PATH = path.join(ROOT_DIR, 'docs', 'reviews', '2026-03-18-v2-interactive-test-results.md');

type ResultStatus = 'PASS' | 'FAIL' | 'KNOWN FAIL';

interface ReceiverState {
  afLevel: number;
  agc: number;
  agcTimeConstant: number;
  att: number;
  autoNotch: boolean;
  filter: number;
  filterWidth?: number;
  freqHz: number;
  manualNotch: boolean;
  mode: string;
  nb: boolean;
  nbLevel: number;
  nr: boolean;
  nrLevel: number;
  pbtInner: number;
  pbtOuter: number;
  preamp: number;
  rfGain: number;
}

interface ServerState {
  active: 'MAIN' | 'SUB';
  compressorLevel: number;
  compressorOn: boolean;
  cwPitch: number;
  main: ReceiverState;
  mainSubTracking?: boolean;
  meterSource?: string;
  micGain: number;
  monitorGain: number;
  monitorOn: boolean;
  notchFilter: number;
  ptt: boolean;
  revision: number;
  ritFreq: number;
  ritOn: boolean;
  ritTx: boolean;
  split: boolean;
  sub: ReceiverState;
  tunerStatus: number;
  voxOn: boolean;
}

interface BandDefinition {
  bsrCode?: number;
  default?: number;
  name: string;
}

interface FreqRange {
  bands?: BandDefinition[];
}

interface Capabilities {
  freqRanges?: FreqRange[];
}

interface WsCommandRecord {
  id: string | null;
  name: string;
  params: Record<string, unknown>;
  ts: number;
  type: string;
}

interface ConsoleErrorRecord {
  source: 'console' | 'pageerror';
  text: string;
}

interface ControlResult {
  panel: string;
  control: string;
  action: string;
  expected: string;
  actual: string;
  status: ResultStatus;
  details?: string;
  screenshot?: string;
}

interface CommandCheck {
  name: string;
  params?: Record<string, unknown>;
  approx?: Record<string, { expected: number; tolerance?: number }>;
}

interface CaseOutcome {
  actual: string;
  details?: string;
  status: ResultStatus;
}

interface CaseContext {
  capabilities: Capabilities;
  originalState: ServerState;
  page: Page;
  request: APIRequestContext;
}

interface AuditCase {
  panel: string;
  control: string;
  action: string;
  expected: string;
  locate: (page: Page) => Locator;
  act?: (page: Page, locator: Locator) => Promise<void>;
  cleanup?: (ctx: CaseContext) => Promise<void>;
  ensureMain?: boolean;
  onMissing?: (ctx: CaseContext) => Promise<CaseOutcome> | CaseOutcome;
  prepare?: (ctx: CaseContext) => Promise<void>;
  verify: (ctx: CaseContext, commands: WsCommandRecord[], caseErrors: ConsoleErrorRecord[]) => Promise<CaseOutcome> | CaseOutcome;
}

function isKnownConsoleError(error: ConsoleErrorRecord): boolean {
  return /unknown command: 'set_filter_width'/.test(error.text);
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sanitizeSlug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

function formatCommand(command: WsCommandRecord): string {
  return `${command.name} ${JSON.stringify(command.params)}`;
}

function formatCommandList(commands: WsCommandRecord[]): string {
  if (!commands.length) {
    return '(none)';
  }
  return commands.map(formatCommand).join('; ');
}

function commandsMatching(commands: WsCommandRecord[], check: CommandCheck): WsCommandRecord[] {
  return commands.filter((command) => {
    if (command.name !== check.name) {
      return false;
    }

    const params = command.params ?? {};
    for (const [key, expected] of Object.entries(check.params ?? {})) {
      if (params[key] !== expected) {
        return false;
      }
    }

    for (const [key, spec] of Object.entries(check.approx ?? {})) {
      const actual = params[key];
      if (typeof actual !== 'number') {
        return false;
      }
      if (Math.abs(actual - spec.expected) > (spec.tolerance ?? 0)) {
        return false;
      }
    }

    return true;
  });
}

function verifySingleCommand(
  commands: WsCommandRecord[],
  check: CommandCheck,
  failureStatus: ResultStatus = 'FAIL',
  details?: string,
): CaseOutcome {
  const matches = commandsMatching(commands, check);
  if (matches.length > 0) {
    return {
      status: 'PASS',
      actual: formatCommand(matches[0]),
    };
  }

  return {
    status: failureStatus,
    actual: formatCommandList(commands),
    details,
  };
}

function verifyAllCommands(
  commands: WsCommandRecord[],
  checks: CommandCheck[],
  failureStatus: ResultStatus = 'FAIL',
  details?: string,
): CaseOutcome {
  const missing = checks.filter((check) => commandsMatching(commands, check).length === 0);
  if (missing.length === 0) {
    return {
      status: 'PASS',
      actual: formatCommandList(commands),
    };
  }

  return {
    status: failureStatus,
    actual: formatCommandList(commands),
    details: details ?? `Missing expected commands: ${missing.map((item) => item.name).join(', ')}`,
  };
}

function clampToBipolarRange(value: number): number {
  return Math.max(-1200, Math.min(1200, Math.round(value)));
}

function deriveIfShift(pbtInner: number, pbtOuter: number): number {
  return clampToBipolarRange((pbtInner + pbtOuter) / 2);
}

function mapIfShiftToPbt(targetIfShift: number, currentPbtInner: number, currentPbtOuter: number) {
  const currentIfShift = deriveIfShift(currentPbtInner, currentPbtOuter);
  const delta = clampToBipolarRange(targetIfShift) - currentIfShift;
  return {
    pbtInner: clampToBipolarRange(currentPbtInner + delta),
    pbtOuter: clampToBipolarRange(currentPbtOuter + delta),
  };
}

async function installWebSocketInterceptor(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const win = window as Window & {
      __WS_COMMANDS__?: Array<Record<string, unknown>>;
      __WS_INSTANCES__?: WebSocket[];
    };

    win.__WS_COMMANDS__ = [];
    win.__WS_INSTANCES__ = [];

    const OriginalWebSocket = window.WebSocket;

    class InterceptedWebSocket extends OriginalWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        win.__WS_INSTANCES__?.push(this);

        const originalSend = this.send.bind(this);
        this.send = (data: string | ArrayBufferLike | Blob | ArrayBufferView) => {
          if (typeof data === 'string') {
            try {
              const parsed = JSON.parse(data) as Record<string, unknown>;
              if (parsed.type === 'cmd' || parsed.type === 'command') {
                win.__WS_COMMANDS__?.push({
                  type: parsed.type,
                  id: parsed.id ?? null,
                  name: parsed.name,
                  params: parsed.params ?? {},
                  ts: Date.now(),
                });
              }
            } catch {
              // Ignore non-JSON frames.
            }
          }
          return originalSend(data);
        };
      }
    }

    Object.setPrototypeOf(InterceptedWebSocket, OriginalWebSocket);
    Object.defineProperty(InterceptedWebSocket, 'CONNECTING', { value: OriginalWebSocket.CONNECTING });
    Object.defineProperty(InterceptedWebSocket, 'OPEN', { value: OriginalWebSocket.OPEN });
    Object.defineProperty(InterceptedWebSocket, 'CLOSING', { value: OriginalWebSocket.CLOSING });
    Object.defineProperty(InterceptedWebSocket, 'CLOSED', { value: OriginalWebSocket.CLOSED });
    window.WebSocket = InterceptedWebSocket as typeof WebSocket;
  });
}

async function drainCommands(page: Page): Promise<WsCommandRecord[]> {
  return page.evaluate(() => {
    const win = window as Window & { __WS_COMMANDS__?: WsCommandRecord[] };
    const commands = [...(win.__WS_COMMANDS__ ?? [])];
    win.__WS_COMMANDS__ = [];
    return commands;
  });
}

async function clearCommands(page: Page): Promise<void> {
  await drainCommands(page);
}

async function requestJsonWithRetry<T>(
  request: APIRequestContext,
  url: string,
  attempts = 5,
): Promise<T> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await request.get(url, { timeout: 5_000 });
      if (!response.ok()) {
        throw new Error(`HTTP ${response.status()} for ${url}`);
      }
      return (await response.json()) as T;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await wait(300 * attempt);
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function getState(request: APIRequestContext): Promise<ServerState> {
  return requestJsonWithRetry<ServerState>(request, STATE_URL);
}

async function getCapabilities(request: APIRequestContext): Promise<Capabilities> {
  return requestJsonWithRetry<Capabilities>(request, CAPABILITIES_URL);
}

async function waitForState(
  request: APIRequestContext,
  predicate: (state: ServerState) => boolean,
  timeoutMs = 5_000,
): Promise<ServerState> {
  const deadline = Date.now() + timeoutMs;
  let lastState = await getState(request);

  while (Date.now() < deadline) {
    if (predicate(lastState)) {
      return lastState;
    }
    await wait(250);
    lastState = await getState(request);
  }

  return lastState;
}

async function sendWsCommand(page: Page, name: string, params: Record<string, unknown> = {}): Promise<void> {
  await page.evaluate(
    async ({ commandName, commandParams }) => {
      const win = window as Window & { __WS_INSTANCES__?: WebSocket[] };
      const payload = JSON.stringify({
        type: 'cmd',
        id: `codex-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        name: commandName,
        params: commandParams,
      });

      const openSockets = (win.__WS_INSTANCES__ ?? []).filter((socket) => socket.readyState === WebSocket.OPEN);
      if (openSockets.length > 0) {
        openSockets[0].send(payload);
        return;
      }

      const wsUrl = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/ws`;
      await new Promise<void>((resolve, reject) => {
        const socket = new WebSocket(wsUrl);
        const timeout = setTimeout(() => {
          try {
            socket.close();
          } catch {
            // Ignore close failures during timeout cleanup.
          }
          reject(new Error('Timed out opening restore websocket'));
        }, 3_000);

        socket.addEventListener(
          'open',
          () => {
            clearTimeout(timeout);
            socket.send(payload);
            setTimeout(() => {
              try {
                socket.close();
              } catch {
                // Ignore close failures during normal cleanup.
              }
              resolve();
            }, 200);
          },
          { once: true },
        );

        socket.addEventListener(
          'error',
          () => {
            clearTimeout(timeout);
            reject(new Error('Restore websocket failed'));
          },
          { once: true },
        );
      });
    },
    { commandName: name, commandParams: params },
  );
}

async function sendRestoreCommands(
  page: Page,
  request: APIRequestContext,
  commands: Array<{ name: string; params?: Record<string, unknown> }>,
): Promise<void> {
  for (const command of commands) {
    await sendWsCommand(page, command.name, command.params ?? {});
    await wait(120);
  }
  await clearCommands(page);
  await wait(CLEANUP_DELAY_MS);
  await getState(request);
}

async function ensureMainSelected(page: Page, request: APIRequestContext): Promise<void> {
  const current = await getState(request);
  if (current.active === 'MAIN') {
    return;
  }

  await sendWsCommand(page, 'set_vfo', { vfo: 'MAIN' });
  await waitForState(request, (state) => state.active === 'MAIN');
  await clearCommands(page);
}

async function restoreOriginalActiveReceiver(ctx: CaseContext): Promise<void> {
  if (ctx.originalState.active === 'MAIN') {
    return;
  }

  await sendWsCommand(ctx.page, 'set_vfo', { vfo: ctx.originalState.active });
  await waitForState(ctx.request, (state) => state.active === ctx.originalState.active);
  await clearCommands(ctx.page);
}

async function ensureCompSliderVisible(ctx: CaseContext): Promise<void> {
  const slider = ctx.page.getByRole('slider', { name: 'Comp Level' });
  if (await slider.isVisible().catch(() => false)) {
    return;
  }

  await panelByHeader(ctx.page, 'TX').getByRole('button', { name: 'COMP', exact: true }).click();
  await slider.waitFor({ state: 'visible', timeout: 5_000 });
  await clearCommands(ctx.page);
}

async function ensureMonSliderVisible(ctx: CaseContext): Promise<void> {
  const slider = ctx.page.getByRole('slider', { name: 'Mon Level' });
  if (await slider.isVisible().catch(() => false)) {
    return;
  }

  await panelByHeader(ctx.page, 'TX').getByRole('button', { name: 'MON', exact: true }).click();
  await slider.waitFor({ state: 'visible', timeout: 5_000 });
  await clearCommands(ctx.page);
}

async function ensureCwPitchVisible(ctx: CaseContext): Promise<void> {
  const slider = ctx.page.getByRole('slider', { name: 'CW Pitch' });
  if (await slider.isVisible().catch(() => false)) {
    return;
  }

  await sendWsCommand(ctx.page, 'set_mode', { mode: 'CW', receiver: 0 });
  await wait(2_000);
  await slider.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => undefined);
  await clearCommands(ctx.page);
}

async function restoreSubReceiverFromOriginal(ctx: CaseContext): Promise<void> {
  const { sub } = ctx.originalState;
  const commands = [
    { name: 'set_freq', params: { freq: sub.freqHz, receiver: 1 } },
    { name: 'set_mode', params: { mode: sub.mode, receiver: 1 } },
    { name: 'set_filter', params: { filter: sub.filter, receiver: 1 } },
    { name: 'set_attenuator', params: { db: sub.att, receiver: 1 } },
    { name: 'set_preamp', params: { level: sub.preamp, receiver: 1 } },
    { name: 'set_rf_gain', params: { level: sub.rfGain, receiver: 1 } },
    { name: 'set_agc', params: { mode: sub.agc, receiver: 1 } },
    { name: 'set_agc_time_constant', params: { value: sub.agcTimeConstant, receiver: 1 } },
    { name: 'set_af_level', params: { level: sub.afLevel, receiver: 1 } },
    { name: 'set_nr', params: { on: sub.nr, receiver: 1 } },
    { name: 'set_nr_level', params: { level: sub.nrLevel, receiver: 1 } },
    { name: 'set_nb', params: { on: sub.nb, receiver: 1 } },
    { name: 'set_nb_level', params: { level: sub.nbLevel, receiver: 1 } },
    { name: 'set_pbt_inner', params: { value: sub.pbtInner, receiver: 1 } },
    { name: 'set_pbt_outer', params: { value: sub.pbtOuter, receiver: 1 } },
  ];

  if (sub.autoNotch) {
    commands.push({ name: 'set_auto_notch', params: { on: true, receiver: 1 } });
  } else {
    commands.push({ name: 'set_auto_notch', params: { on: false, receiver: 1 } });
  }

  if (sub.manualNotch) {
    commands.push({ name: 'set_manual_notch', params: { on: true, receiver: 1 } });
  } else {
    commands.push({ name: 'set_manual_notch', params: { on: false, receiver: 1 } });
  }

  await sendRestoreCommands(ctx.page, ctx.request, commands);
}

async function setRangeValue(locator: Locator, value: number): Promise<void> {
  await locator.scrollIntoViewIfNeeded();
  await locator.evaluate((element, targetValue) => {
    const input = element as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    setter?.call(input, String(targetValue));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
}

async function takeFailureScreenshot(page: Page, panel: string, control: string): Promise<string> {
  const filename = `2026-03-18-v2-${sanitizeSlug(`${panel}-${control}`)}.png`;
  const absolutePath = path.join(SCREENSHOT_DIR, filename);
  await page.screenshot({ fullPage: true, path: absolutePath });
  return path.posix.join('screenshots', filename);
}

function panelByHeader(page: Page, header: string): Locator {
  return page
    .locator('.panel, .filter-panel')
    .filter({ has: page.locator('.panel-header', { hasText: header }) })
    .first();
}

function rfControlRow(page: Page, label: string): Locator {
  const panel = panelByHeader(page, 'RF FRONT END');
  return panel.locator('.control-row').filter({ has: panel.locator('.control-label', { hasText: label }) }).first();
}

function dspSection(page: Page, label: string): Locator {
  const panel = panelByHeader(page, 'DSP');
  return panel.locator('.section').filter({ has: panel.locator('.section-label', { hasText: label }) }).first();
}

function bandCode(capabilities: Capabilities, bandName: string): number | undefined {
  for (const range of capabilities.freqRanges ?? []) {
    for (const band of range.bands ?? []) {
      if (band.name === bandName) {
        return band.bsrCode;
      }
    }
  }
  return undefined;
}

async function buildAuditCases(capabilities: Capabilities): Promise<AuditCase[]> {
  const expected20mCode = bandCode(capabilities, '20m');
  return [
    {
      panel: 'RF FRONT END',
      control: 'RF Gain',
      action: 'set 200',
      expected: 'set_rf_gain { level: 200, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'RF FRONT END').getByRole('slider', { name: 'RF Gain' }),
      act: async (_page, locator) => setRangeValue(locator, 200),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_rf_gain',
          approx: { level: { expected: 200 } },
          params: { receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_rf_gain', params: { level: ctx.originalState.main.rfGain, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'RF FRONT END',
      control: 'ATT',
      action: 'click 6dB',
      expected: 'set_attenuator { db: 6, receiver: 0 }',
      locate: (page) => page.getByRole('radio', { name: '6dB', exact: true }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_attenuator',
          params: { db: 6, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_attenuator', params: { db: ctx.originalState.main.att, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'RF FRONT END',
      control: 'PRE',
      action: 'click P1',
      expected: 'set_preamp { level: 1, receiver: 0 }',
      locate: (page) => page.getByRole('radio', { name: 'P1', exact: true }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_preamp',
          params: { level: 1, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_preamp', params: { level: ctx.originalState.main.preamp, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'FILTER',
      control: 'Width',
      action: 'set 1500',
      expected: 'set_filter_width { width: 1500, receiver: 0 }',
      locate: (page) => page.getByRole('slider', { name: 'Width' }),
      act: async (_page, locator) => setRangeValue(locator, 1500),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_filter_width',
          approx: { width: { expected: 1500 } },
          params: { receiver: 0 },
        }),
      cleanup: async (ctx) => {
        if (typeof ctx.originalState.main.filterWidth === 'number') {
          await sendRestoreCommands(ctx.page, ctx.request, [
            { name: 'set_filter_width', params: { width: ctx.originalState.main.filterWidth, receiver: 0 } },
          ]);
        }
      },
    },
    {
      panel: 'FILTER',
      control: 'IF Shift',
      action: 'set 300',
      expected: 'set_pbt_inner + set_pbt_outer',
      locate: (page) => page.getByRole('slider', { name: 'IF Shift' }),
      act: async (_page, locator) => setRangeValue(locator, 300),
      verify: (ctx, commands) => {
        const expected = mapIfShiftToPbt(300, ctx.originalState.main.pbtInner, ctx.originalState.main.pbtOuter);
        return verifyAllCommands(commands, [
          {
            name: 'set_pbt_inner',
            params: { receiver: 0 },
            approx: { value: { expected: expected.pbtInner } },
          },
          {
            name: 'set_pbt_outer',
            params: { receiver: 0 },
            approx: { value: { expected: expected.pbtOuter } },
          },
        ]);
      },
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_pbt_inner', params: { value: ctx.originalState.main.pbtInner, receiver: 0 } },
          { name: 'set_pbt_outer', params: { value: ctx.originalState.main.pbtOuter, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'AGC',
      control: 'Mode',
      action: 'click FAST',
      expected: 'set_agc { mode: 1, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'AGC').getByRole('radio', { name: 'FAST' }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_agc',
          params: { mode: 1, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_agc', params: { mode: ctx.originalState.main.agc, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'AGC',
      control: 'Decay',
      action: 'set 100',
      expected: 'set_agc_time_constant { value: 100, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'AGC').getByRole('slider', { name: 'Decay' }),
      act: async (_page, locator) => setRangeValue(locator, 100),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_agc_time_constant',
          params: { receiver: 0 },
          approx: { value: { expected: 100 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_agc_time_constant', params: { value: ctx.originalState.main.agcTimeConstant, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'RIT / XIT',
      control: 'RIT',
      action: 'toggle',
      expected: `set_rit_status { on: ${String(true)} }`,
      locate: (page) => panelByHeader(page, 'RIT / XIT').getByRole('button', { name: 'RIT' }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_rit_status',
          params: { on: !ctx.originalState.ritOn },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_rit_status', params: { on: ctx.originalState.ritOn } },
        ]);
      },
    },
    {
      panel: 'RIT / XIT',
      control: 'XIT',
      action: 'toggle',
      expected: `set_rit_tx_status { on: ${String(true)} }`,
      locate: (page) => panelByHeader(page, 'RIT / XIT').getByRole('button', { name: 'XIT' }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_rit_tx_status',
          params: { on: !ctx.originalState.ritTx },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_rit_tx_status', params: { on: ctx.originalState.ritTx } },
        ]);
      },
    },
    {
      panel: 'RIT / XIT',
      control: 'Offset',
      action: 'nudge +1 step',
      expected: 'set_rit_frequency { freq: <number> }',
      locate: (page) => page.getByRole('slider', { name: 'Offset', exact: true }),
      onMissing: async () => ({
        status: 'FAIL',
        actual: 'control missing from DOM',
        details: 'RIT / XIT panel did not render the shared Offset slider.',
      }),
      act: async (_page, locator) => {
        await locator.focus();
        await locator.press('ArrowRight');
      },
      verify: (_ctx, commands) =>
        (() => {
          const match = commands.find(
            (command) => command.name === 'set_rit_frequency' && typeof command.params.freq === 'number',
          );
          if (match) {
            return {
              status: 'PASS' as const,
              actual: formatCommand(match),
            };
          }
          return {
            status: 'FAIL' as const,
            actual: formatCommandList(commands),
          };
        })(),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_rit_frequency', params: { freq: ctx.originalState.ritFreq } },
        ]);
      },
    },
    {
      panel: 'RIT / XIT',
      control: 'Clear',
      action: 'click CLEAR',
      expected: 'set_rit_frequency { freq: 0 }',
      locate: (page) => panelByHeader(page, 'RIT / XIT').getByRole('button', { name: 'CLEAR' }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_rit_frequency',
          params: { freq: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_rit_frequency', params: { freq: ctx.originalState.ritFreq } },
        ]);
      },
    },
    {
      panel: 'BAND',
      control: '20m',
      action: 'click 20m',
      expected: expected20mCode !== undefined
        ? `set_band { band: ${expected20mCode} }`
        : 'set_band { band: <number> }',
      locate: (page) => page.locator('[data-band="20m"]').first(),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(
          commands,
          {
            name: 'set_band',
            params: expected20mCode !== undefined ? { band: expected20mCode } : {},
          },
          'FAIL',
          'Band selector did not emit set_band even though capabilities expose bsrCode for 20m.',
        ),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_freq', params: { freq: ctx.originalState.main.freqHz, receiver: 0 } },
          { name: 'set_mode', params: { mode: ctx.originalState.main.mode, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'RX AUDIO',
      control: 'AF Level',
      action: 'set 150',
      expected: 'set_af_level { level: 150, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'RX AUDIO').getByRole('slider', { name: 'AF Level' }),
      act: async (_page, locator) => setRangeValue(locator, 150),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_af_level',
          params: { receiver: 0 },
          approx: { level: { expected: 150 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_af_level', params: { level: ctx.originalState.main.afLevel, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'DSP',
      control: 'NR',
      action: 'click NR1',
      expected: 'set_nr { on: true, receiver: 0 }',
      locate: (page) => page.getByRole('radio', { name: 'NR1', exact: true }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_nr',
          params: { on: true, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_nr', params: { on: ctx.originalState.main.nr, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'DSP',
      control: 'NR Level',
      action: 'set 5',
      expected: 'set_nr_level { level: 5, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'DSP').getByRole('slider', { name: 'NR Level' }),
      act: async (_page, locator) => setRangeValue(locator, 5),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_nr_level',
          params: { receiver: 0 },
          approx: { level: { expected: 5 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_nr_level', params: { level: ctx.originalState.main.nrLevel, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'DSP',
      control: 'NB',
      action: 'toggle',
      expected: `set_nb { on: ${String(true)}, receiver: 0 }`,
      locate: (page) => page.getByRole('button', { name: /^(ON|OFF)$/ }).first(),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_nb',
          params: { on: !ctx.originalState.main.nb, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_nb', params: { on: ctx.originalState.main.nb, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'DSP',
      control: 'NB Level',
      action: 'set 5',
      expected: 'set_nb_level { level: 5, receiver: 0 }',
      locate: (page) => panelByHeader(page, 'DSP').getByRole('slider', { name: 'NB Level' }),
      act: async (_page, locator) => setRangeValue(locator, 5),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_nb_level',
          params: { receiver: 0 },
          approx: { level: { expected: 5 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_nb_level', params: { level: ctx.originalState.main.nbLevel, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'DSP',
      control: 'Notch',
      action: 'click AUTO',
      expected: 'set_auto_notch { on: true, receiver: 0 }',
      locate: (page) => page.getByRole('radio', { name: 'AUTO', exact: true }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_auto_notch',
          params: { on: true, receiver: 0 },
        }),
      cleanup: async (ctx) => {
        const commands = [];
        if (ctx.originalState.main.autoNotch) {
          commands.push({ name: 'set_auto_notch', params: { on: true, receiver: 0 } });
        } else {
          commands.push({ name: 'set_auto_notch', params: { on: false, receiver: 0 } });
        }
        if (ctx.originalState.main.manualNotch) {
          commands.push({ name: 'set_manual_notch', params: { on: true, receiver: 0 } });
        } else {
          commands.push({ name: 'set_manual_notch', params: { on: false, receiver: 0 } });
        }
        await sendRestoreCommands(ctx.page, ctx.request, commands);
      },
    },
    {
      panel: 'DSP',
      control: 'CW Pitch',
      action: 'set 700',
      expected: 'set_cw_pitch { value: 700 }',
      locate: (page) => page.locator('input[aria-label="CW Pitch"]:visible'),
      prepare: ensureCwPitchVisible,
      onMissing: async () => ({
        status: 'KNOWN FAIL',
        actual: 'control missing from DOM',
        details: 'CW Pitch did not render after switching MAIN to CW mode with a direct set_mode command.',
      }),
      act: async (_page, locator) => setRangeValue(locator, 700),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_cw_pitch',
          approx: { value: { expected: 700 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_cw_pitch', params: { value: ctx.originalState.cwPitch } },
          { name: 'set_mode', params: { mode: ctx.originalState.main.mode, receiver: 0 } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'Mic Gain',
      action: 'set 100',
      expected: 'set_mic_gain { level: 100 }',
      locate: (page) => panelByHeader(page, 'TX').getByRole('slider', { name: 'Mic Gain' }),
      act: async (_page, locator) => setRangeValue(locator, 100),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_mic_gain',
          approx: { level: { expected: 100 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_mic_gain', params: { level: ctx.originalState.micGain } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'VOX',
      action: 'toggle',
      expected: 'set_vox { on: true/false }',
      locate: (page) => panelByHeader(page, 'TX').getByRole('button', { name: 'VOX' }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_vox',
          params: { on: !ctx.originalState.voxOn },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_vox', params: { on: ctx.originalState.voxOn } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'COMP',
      action: 'toggle',
      expected: 'set_compressor { on: true/false }',
      locate: (page) => panelByHeader(page, 'TX').getByRole('button', { name: 'COMP' }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_compressor',
          params: { on: !ctx.originalState.compressorOn },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_compressor', params: { on: ctx.originalState.compressorOn } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'Comp Level',
      action: 'set 5',
      expected: 'set_compressor_level { level: 5 }',
      locate: (page) => page.getByRole('slider', { name: 'Comp Level' }),
      prepare: ensureCompSliderVisible,
      onMissing: async () => ({
        status: 'FAIL',
        actual: 'control missing from DOM',
        details: 'Comp Level slider did not render after enabling COMP.',
      }),
      act: async (_page, locator) => setRangeValue(locator, 5),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_compressor_level',
          approx: { level: { expected: 5 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_compressor_level', params: { level: ctx.originalState.compressorLevel } },
          { name: 'set_compressor', params: { on: ctx.originalState.compressorOn } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'MON',
      action: 'toggle',
      expected: 'set_monitor { on: true/false }',
      locate: (page) => panelByHeader(page, 'TX').getByRole('button', { name: 'MON' }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_monitor',
          params: { on: !ctx.originalState.monitorOn },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_monitor', params: { on: ctx.originalState.monitorOn } },
        ]);
      },
    },
    {
      panel: 'TX',
      control: 'Mon Level',
      action: 'set 100',
      expected: 'set_monitor_gain { level: 100 }',
      locate: (page) => page.getByRole('slider', { name: 'Mon Level' }),
      prepare: ensureMonSliderVisible,
      onMissing: async () => ({
        status: 'FAIL',
        actual: 'control missing from DOM',
        details: 'Mon Level slider did not render after enabling MON.',
      }),
      act: async (_page, locator) => setRangeValue(locator, 100),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_monitor_gain',
          approx: { level: { expected: 100 } },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_monitor_gain', params: { level: ctx.originalState.monitorGain } },
          { name: 'set_monitor', params: { on: ctx.originalState.monitorOn } },
        ]);
      },
    },
    {
      panel: 'VFO OPS',
      control: 'Copy',
      action: 'click M→S',
      expected: 'vfo_equalize {}',
      locate: (page) => page.getByRole('button', { name: 'M→S' }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, { name: 'vfo_equalize' }),
      cleanup: restoreSubReceiverFromOriginal,
    },
    {
      panel: 'VFO OPS',
      control: 'Split',
      action: 'toggle',
      expected: 'set_split { on: true/false }',
      locate: (page) => page.locator('.vfo-ops').getByRole('button', { name: 'SPLIT', exact: true }),
      act: async (_page, locator) => locator.click(),
      verify: (ctx, commands) =>
        verifySingleCommand(commands, {
          name: 'set_split',
          params: { on: !ctx.originalState.split },
        }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [
          { name: 'set_split', params: { on: ctx.originalState.split } },
        ]);
      },
    },
    {
      panel: 'VFO OPS',
      control: 'Swap',
      action: 'click M↔S',
      expected: 'vfo_swap {}',
      locate: (page) => page.getByRole('button', { name: 'M↔S' }),
      act: async (_page, locator) => locator.click(),
      verify: (_ctx, commands) =>
        verifySingleCommand(commands, { name: 'vfo_swap' }),
      cleanup: async (ctx) => {
        await sendRestoreCommands(ctx.page, ctx.request, [{ name: 'vfo_swap' }]);
      },
    },
  ];
}

async function writeReport(
  results: ControlResult[],
  consoleErrors: ConsoleErrorRecord[],
  pageUrl: string,
): Promise<void> {
  await mkdir(SCREENSHOT_DIR, { recursive: true });

  const passed = results.filter((result) => result.status === 'PASS').length;
  const knownFailures = results.filter((result) => result.status === 'KNOWN FAIL').length;
  const newFailures = results.filter((result) => result.status === 'FAIL').length;
  const panels = [...new Set(results.map((result) => result.panel))];

  const lines: string[] = [
    '# v2 UI Interactive Test Results - 2026-03-18',
    '',
    '## Summary',
    `- Total tests: ${results.length}`,
    `- Passed: ${passed}`,
    `- Known failures: ${knownFailures}`,
    `- New failures: ${newFailures}`,
    `- Console errors: ${consoleErrors.length}`,
    '',
  ];

  for (const panel of panels) {
    lines.push(`## ${panel}`);
    lines.push('| Control | Action | Expected | Actual | Status |');
    lines.push('| --- | --- | --- | --- | --- |');

    for (const result of results.filter((item) => item.panel === panel)) {
      const actual = result.screenshot
        ? `${result.actual} ([screenshot](${result.screenshot}))`
        : result.actual;
      lines.push(`| ${result.control} | ${result.action} | ${result.expected} | ${actual} | ${result.status} |`);
      if (result.details) {
        lines.push(`> ${result.details}`);
      }
    }

    lines.push('');
  }

  lines.push('## Console Errors');
  if (consoleErrors.length === 0) {
    lines.push('- None observed during load or control interactions.');
  } else {
    for (const error of consoleErrors) {
      lines.push(`- [${error.source}] ${error.text}`);
    }
  }
  lines.push('');
  lines.push('## Notes');
  lines.push(`- Live target: \`${pageUrl}\``);
  lines.push('- Safety guard: PTT and ATU TUNE were intentionally excluded.');
  lines.push('- The current frontend source sends WebSocket frames with `type: "cmd"`; the issue description still references `type: "command"`.');
  lines.push('- Cleanup restores radio state after each interaction using direct WS commands where a UI action is not safely invertible.');
  lines.push('');

  await writeFile(REPORT_PATH, `${lines.join('\n')}\n`, 'utf8');
}

test.describe.configure({ mode: 'serial' });

test('v2 interactive audit against the live backend', async ({ page, request }) => {
  test.skip(
    !PAGE_URL,
    'Set RIGPLANE_V2_URL=http://host:port?ui=v2 to run the live backend audit.',
  );

  const pageUrl = PAGE_URL ?? '';
  await mkdir(SCREENSHOT_DIR, { recursive: true });

  const allConsoleErrors: ConsoleErrorRecord[] = [];
  const actionConsoleErrors: ConsoleErrorRecord[] = [];
  const results: ControlResult[] = [];

  page.on('console', (message) => {
    if (message.type() === 'error') {
      allConsoleErrors.push({ source: 'console', text: message.text() });
    }
  });
  page.on('pageerror', (error) => {
    allConsoleErrors.push({ source: 'pageerror', text: error.message });
  });

  await installWebSocketInterceptor(page);
  await page.goto(pageUrl, { waitUntil: 'domcontentloaded' });
  await page.locator('.left-sidebar').waitFor({ state: 'visible' });
  await page.locator('.right-sidebar').waitFor({ state: 'visible' });
  await wait(1500);

  const capabilities = await getCapabilities(request);
  const auditCases = await buildAuditCases(capabilities);
  await clearCommands(page);

  try {
    for (const auditCase of auditCases) {
      const originalState = await getState(request);
      const ctx: CaseContext = {
        capabilities,
        originalState,
        page,
        request,
      };
      let outcome: ControlResult | null = null;

      try {
        if (auditCase.ensureMain !== false) {
          await ensureMainSelected(page, request);
        }

        if (auditCase.prepare) {
          await auditCase.prepare(ctx);
        }

        await clearCommands(page);
        const consoleStart = allConsoleErrors.length;

        const locator = auditCase.locate(page);
        const count = await locator.count();
        if (count === 0) {
          if (auditCase.onMissing) {
            const missingOutcome = await auditCase.onMissing(ctx);
            let screenshot: string | undefined;
            if (missingOutcome.status !== 'PASS') {
              screenshot = await takeFailureScreenshot(page, auditCase.panel, auditCase.control);
            }
            outcome = {
              panel: auditCase.panel,
              control: auditCase.control,
              action: auditCase.action,
              expected: auditCase.expected,
              actual: missingOutcome.actual,
              details: missingOutcome.details,
              screenshot,
              status: missingOutcome.status,
            };
          } else {
            throw new Error('Control not found in DOM');
          }
        } else {
          const target = locator.first();
          await target.waitFor({ state: 'visible', timeout: 5_000 });
          if (auditCase.act) {
            await auditCase.act(page, target);
          } else {
            await target.click();
          }

          await wait(CONTROL_DELAY_MS);

          const commands = await drainCommands(page);
          const caseErrors = allConsoleErrors.slice(consoleStart);
          actionConsoleErrors.push(...caseErrors);

          const caseOutcome = await auditCase.verify(ctx, commands, caseErrors);
          let screenshot: string | undefined;
          if (caseOutcome.status !== 'PASS') {
            screenshot = await takeFailureScreenshot(page, auditCase.panel, auditCase.control);
          }

          outcome = {
            panel: auditCase.panel,
            control: auditCase.control,
            action: auditCase.action,
            expected: auditCase.expected,
            actual: caseOutcome.actual,
            details: caseOutcome.details,
            screenshot,
            status: caseOutcome.status,
          };
        }
      } catch (error) {
        const screenshot = await takeFailureScreenshot(page, auditCase.panel, auditCase.control);
        outcome = {
          panel: auditCase.panel,
          control: auditCase.control,
          action: auditCase.action,
          expected: auditCase.expected,
          actual: 'exception during audit',
          details: error instanceof Error ? error.message : String(error),
          screenshot,
          status: 'FAIL',
        };
      } finally {
        try {
          if (auditCase.cleanup) {
            await auditCase.cleanup(ctx);
          }
        } finally {
          if (auditCase.ensureMain !== false) {
            await restoreOriginalActiveReceiver(ctx);
          }
          await clearCommands(page);
        }
      }

      results.push(outcome);
    }
  } finally {
    await writeReport(results, actionConsoleErrors, pageUrl);
  }

  const unexpectedFailures = results.filter((result) => result.status === 'FAIL');
  const unexpectedConsoleErrors = actionConsoleErrors.filter((item) => !isKnownConsoleError(item));
  expect(unexpectedFailures, `Unexpected failures:\n${unexpectedFailures.map((item) => `${item.panel}/${item.control}: ${item.details ?? item.actual}`).join('\n')}`).toHaveLength(0);
  expect(unexpectedConsoleErrors, `Unexpected console errors:\n${unexpectedConsoleErrors.map((item) => item.text).join('\n')}`).toHaveLength(0);
});
