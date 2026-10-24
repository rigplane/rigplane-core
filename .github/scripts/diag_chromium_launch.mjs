import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const pick = names => Object.fromEntries(names.filter(k => process.env[k] !== undefined)
  .map(k => [k, process.env[k]]));
const baseNames = ['PATH', 'HOME', 'USER', 'LANG'];
const minimal = pick(baseNames);
const fallbackSeed = {
  DBUS_SESSION_BUS_ADDRESS: 'disabled:', FC_FONTATIONS: '1', VK_ICD_FILENAMES: '',
};
let seed3 = { ...fallbackSeed };
let seedSource = 'fixed fallback (shim not run)';
const padCounts = Array.from({ length: 8 }, (_, k) => k);
const defaultVariants = padCounts.flatMap(k => [`pad${k}`, `pad${k}+seed3`]);
const variantNames = [...defaultVariants, 'real', 'real+seed3', 'default',
  'default+seed3', 'pw', 'pw+seed3', 'min', 'min+seed3', 'min+tz'];
let variants = [];
let count = 200, pwCount = 150, realCount = 150, previewPort = 4173;
let keptRealCrashes = 0, printedHarnessErrors = 0;
let loadCount = 0, loadStopping = false, loadFailed = false, loadWatchdog;
const loadWorkers = [];
const load = { workers: 0, before: null, after: null };
const file = fileURLToPath(import.meta.url);
const frontend = path.resolve(path.dirname(file), '../../frontend');
const require = createRequire(path.join(frontend, 'package.json'));
const out = path.join(frontend, 'out');
const realReporter = path.resolve(path.dirname(file), 'diag/real-reporter.mjs');
const realTitle = 'MOR-2458 keeps RX/TX geometry fixed and attributes TX only ' +
  'to the known split target';
const realGrep = realTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$';
const shimLibrary = path.join(out, 'setenv_shim.so');
const shimLog = path.join(out, 'setenv.log');
const runner = process.env.RUNNER_NAME || os.hostname();
const slot = process.env.DIAG_SLOT ?? '0';
const results = [];
const errors = [];
const shimCounts = new Map();
const shimValues = new Map();
const seedValues = new Map();
const crashPattern = /browserType\.launch|signal=SIGSEGV|Received signal 11/;
let compiler;
let stopped = false;
let activeStop = () => {};
const read = file => { try { return fs.readFileSync(file, 'utf8'); } catch { return ''; } };
const append = (file, text) => fs.appendFileSync(path.join(out, file), text);

// Chromium may create its own session. Track descendants as well as the Node group.
function descendants(pid, known) {
  for (const child of read(`/proc/${pid}/task/${pid}/children`).trim().split(/\s+/)) {
    if (!/^\d+$/.test(child)) continue;
    const stat = read(`/proc/${child}/stat`).split(') ')[1]?.split(' ');
    if (stat) known.set(Number(child), stat[19]); // starttime guards against PID reuse.
    descendants(child, known);
  }
}
const kill = (pid, signal) => { try { process.kill(pid, signal); } catch { /* Gone. */ } };
function signalLoad(child, signal) {
  if (child.pid && child.exitCode === null && child.signalCode === null) {
    kill(-child.pid, signal);
  }
}
function stopLoad() {
  if (loadStopping) return;
  loadStopping = true;
  clearTimeout(loadWatchdog);
  for (const child of loadWorkers) signalLoad(child, 'SIGTERM');
  setTimeout(() => {
    for (const child of loadWorkers) signalLoad(child, 'SIGKILL');
  }, 500).unref();
}
function failLoad(reason) {
  if (loadStopping || loadFailed) return;
  loadFailed = true; stopped = true;
  errors.push(reason); activeStop(); stopLoad();
}
async function startLoad() {
  if (!loadCount || stopped) return;
  loadWatchdog = setTimeout(() => failLoad('Load watchdog expired'), 50 * 60 * 1000);
  loadWatchdog.unref();
  for (let index = 0; index < loadCount && !stopped; index++) {
    const ready = await new Promise(resolve => {
      let child;
      const timer = setTimeout(() => {
        failLoad('Load worker readiness timed out'); resolve(false);
      }, 8000);
      try {
        child = spawn(process.execPath, [file, '--load-worker',
          String(Date.now() + 50 * 60 * 1000)], {
          detached: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
        });
      } catch {
        clearTimeout(timer); failLoad('Load worker startup failed'); resolve(false); return;
      }
      loadWorkers.push(child);
      child.unref(); child.channel?.unref();
      child.once('message', message => {
        if (message !== 'ready') return;
        clearTimeout(timer); child.channel?.unref(); resolve(true);
      });
      child.once('error', () => {
        clearTimeout(timer); failLoad('Load worker startup failed'); resolve(false);
      });
      child.once('exit', () => {
        clearTimeout(timer); failLoad('Load worker exited before cleanup'); resolve(false);
      });
      child.channel?.unref();
    });
    if (!ready) break;
  }
}
async function cleanupLoad() {
  stopLoad();
  if (!loadWorkers.length) return;
  // Keep this bounded wait referenced until the final group kill has been sent.
  await new Promise(resolve => setTimeout(resolve, 600));
  for (const child of loadWorkers) {
    signalLoad(child, 'SIGKILL');
    if (child.connected) child.disconnect();
    child.unref(); child.channel?.unref();
  }
}
function loadSnapshot(phase) {
  load[phase] = read('/proc/loadavg').trim() || 'unavailable';
  append('env.txt', `\nmeasurement ${phase}: load workers=${loadCount} ` +
    `/proc/loadavg=${load[phase]}\n`);
}
function command(exe, args, { env = process.env, timeout = 8000, limit = 65536,
  settle = false } = {}) {
  return new Promise(resolve => {
    const start = performance.now();
    const child = spawn(exe, args, { env, cwd: frontend, detached: true,
      stdio: ['ignore', 'pipe', 'pipe'] });
    const known = new Map();
    let stdout = Buffer.alloc(0), stderr = Buffer.alloc(0), error = '', timedOut = false;
    let outputTail = '';
    let finished = false, hardTimer, fallback, crashOutput = false;
    const tails = { stdout: '', stderr: '' };
    const collect = (old, chunk) => Buffer.concat([old, chunk]).subarray(0, limit);
    const observe = (stream, chunk) => {
      const text = tails[stream] + chunk.toString();
      crashOutput ||= crashPattern.test(text);
      tails[stream] = text.slice(-64);
      outputTail = (outputTail + chunk.toString()).slice(-65536);
    };
    child.stdout.on('data', chunk => {
      observe('stdout', chunk); stdout = collect(stdout, chunk);
    });
    child.stderr.on('data', chunk => {
      observe('stderr', chunk); stderr = collect(stderr, chunk);
    });
    const clean = signal => {
      if (child.pid) descendants(child.pid, known);
      for (const [pid, starttime] of known) {
        const stat = read(`/proc/${pid}/stat`).split(') ')[1]?.split(' ');
        if (stat?.[19] === starttime) { kill(-pid, signal); kill(pid, signal); }
      }
      if (child.pid) kill(-child.pid, signal);
    };
    const stop = () => {
      clean('SIGTERM');
      hardTimer ??= setTimeout(() => clean('SIGKILL'), 500);
      fallback ??= setTimeout(() => finish(null, 'SIGKILL'), 1000);
    };
    const finish = async (exitCode, signal) => {
      if (finished) return;
      finished = true;
      clean('SIGKILL');
      clearInterval(watch); clearTimeout(timer);
      clearTimeout(hardTimer); clearTimeout(fallback);
      child.stdout.destroy(); child.stderr.destroy();
      if (activeStop === stop) activeStop = () => {};
      const live = () => [...known].some(([pid, starttime]) => {
        const stat = read(`/proc/${pid}/stat`).split(') ')[1]?.split(' ');
        return stat?.[19] === starttime && !['Z', 'X'].includes(stat[0]);
      });
      const until = performance.now() + 1000;
      while (settle && live() && performance.now() < until) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      resolve({ exitCode, signal, timedOut, error, crashOutput, outputTail,
        cleanupIncomplete: settle && live(),
        stdout: stdout.toString(),
        stderr: stderr.toString(), wallMs: Math.round(performance.now() - start) });
    };
    const watch = setInterval(() => { if (child.pid) descendants(child.pid, known); }, 100);
    const timer = setTimeout(() => { timedOut = true; stop(); }, timeout);
    activeStop = stop;
    child.on('error', err => { error = err.message; finish(null, null); });
    child.on('exit', (code, signal) => {
      // Drain pipes on close; inherited descriptors cannot keep us waiting forever.
      fallback ??= setTimeout(() => finish(code, signal), 500);
    });
    child.on('close', finish);
    if (stopped) stop();
  });
}
const shell = text => command('bash', ['-o', 'pipefail', '-c', text]);
const describe = r => `exit=${r.exitCode} signal=${r.signal} timeout=${r.timedOut}` +
  ` error=${r.error}\n${r.stdout}${r.stderr}\n`;
const failed = r => r.exitCode !== 0 || r.timedOut || Boolean(r.error);
const planned = v => v.shim ? 25 : v.real ? realCount : v.pw ? pwCount : count;
const launchBudget = v => v.real ? 140000 : 100000;
const missing = () => variants.reduce((n, v) => n + planned(v) -
  results.filter(r => r.variant === v.name).length, 0);
const checkpoint = () => fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({
  runner, slot, plannedPerVariant: count, plannedPerPwVariant: pwCount,
  plannedPerRealVariant: realCount, previewPort, realGrep, keptRealCrashes,
  variantEnvCounts: Object.fromEntries(variants.map(v => [v.name, Object.keys(v.env).length])),
  plannedShimLaunches: 25, seed3, seedSource, load, missing: missing(), results, errors,
}, null, 2) + '\n');

async function capture() {
  append('env.txt', `runner=${runner}\nhostname=${os.hostname()}\n`);
  append('env.txt', `slot=${slot}\n`);
  const probe = await shell('command -v cc gcc clang');
  append('env.txt', `$ command -v cc gcc clang\n${describe(probe)}`);
  compiler = probe.stdout.trim().split('\n').find(p => path.isAbsolute(p));
  const probes = [
    'uname -a', 'cat /etc/os-release', 'cat /proc/version', 'arch', 'nproc',
    'lscpu', "awk '/model name|Features/ {print; exit}' /proc/cpuinfo", 'free -m',
    'cat /proc/loadavg', 'cat /proc/self/cgroup', 'cat /proc/self/mountinfo',
    'ulimit -a', 'df -h /dev/shm /tmp',
    'sysctl vm.max_map_count vm.overcommit_memory kernel.randomize_va_space',
    'dpkg-query -W libc6 libnss3 libgbm1 libdrm2 libxkbcommon0 libasound2 fonts-liberation',
    'ldd --version', 'id', 'getent passwd "$(id -u)"', 'wc -l /etc/passwd',
    'npx --no-install playwright --version',
    'ps -eo pid,ppid,pcpu,pmem,comm --sort=-pcpu | head -15', 'dmesg | tail -50',
  ];
  for (const probe of probes) {
    if (stopped) break;
    append('env.txt', `\n$ ${probe}\n${describe(await shell(probe))}`);
  }
  const bases = new Set(['/sys/fs/cgroup', '/sys/fs/cgroup/memory',
    '/sys/fs/cgroup/pids', '/sys/fs/cgroup/cpu']);
  for (const line of read('/proc/self/mountinfo').trim().split('\n')) {
    const [left, right] = line.split(' - ');
    if (!right || !/^cgroup2? /.test(right)) continue;
    const fields = left.split(' '), mount = fields[4], root = fields[3];
    for (const entry of read('/proc/self/cgroup').trim().split('\n')) {
      const [, controllers, group] = entry.split(':');
      if (!group || (right.startsWith('cgroup ') &&
          !controllers.split(',').some(c => right.split(' ')[2]?.split(',').includes(c)))) {
        continue;
      }
      const relative = root === '/' ? group :
        group.startsWith(root) ? group.slice(root.length) : '';
      bases.add(path.join(mount, relative));
    }
  }
  for (const base of bases) for (const name of [
    'memory.max', 'memory.limit_in_bytes', 'pids.max', 'cpu.max',
  ]) {
    const target = path.join(base, name);
    try { append('env.txt', `${target}: ${fs.readFileSync(target, 'utf8')}`); }
    catch (err) { append('env.txt', `${target}: unavailable (${err.code})\n`); }
  }
  try {
    const { chromium } = require('playwright');
    const executable = chromium.executablePath();
    append('env.txt', `chromium executable: ${executable}\n`);
    let installation = path.dirname(executable);
    while (installation !== path.dirname(installation) &&
        !/^chromium[-_]/.test(path.basename(installation))) {
      installation = path.dirname(installation);
    }
    const cache = path.dirname(installation);
    append('env.txt', `headless shell directories: ${fs.readdirSync(cache)
      .filter(name => name.startsWith('chromium_headless_shell')).join(', ') || 'unavailable'}\n`);
  } catch (err) { append('env.txt', `chromium directory unavailable: ${err.message}\n`); }
}

async function address(stderr) {
  const unavailable = 'libc resolution unavailable: unsupported/missing x64 crash layout\n';
  if (process.arch !== 'x64' || !/Received signal 11\b/.test(stderr)) return unavailable;
  const frame = stderr.match(/^.*#2\s+(?:0x)?([a-fA-F0-9]+).*libc\.so\.6\+0x([\da-f]+)\)/m);
  const ip = stderr.match(/\bip:\s*(?:0x)?([\da-fA-F]+)/);
  if (!frame || !ip) return unavailable;
  // Chromium frame column is returnPC, while printed offset is returnPC-1-base.
  // Thus base = framePC - printedOffset - 1, and fault offset = register ip - base.
  const base = BigInt(`0x${frame[1]}`) - BigInt(`0x${frame[2]}`) - 1n;
  const offset = BigInt(`0x${ip[1]}`) - base;
  if (base < 0x100000000n || base > 0x7ffffffff000n || base % 4096n ||
      offset < 0n || offset > 0x10000000n) return unavailable;
  const symbols = await command('nm', ['-D', '--defined-only',
    '/lib/x86_64-linux-gnu/libc.so.6'], { limit: 1024 * 1024 });
  if (symbols.exitCode !== 0) return `libc resolution unavailable: ${describe(symbols)}`;
  let best;
  for (const line of symbols.stdout.split('\n')) {
    const match = line.match(/^([\da-fA-F]+)\s+\S\s+(\S+)/);
    if (!match) continue;
    const value = BigInt(`0x${match[1]}`);
    if (value <= offset && (!best || value > best.value)) best = { value, name: match[2] };
  }
  return best ? `libc base=0x${base.toString(16)} faultOffset=0x${offset.toString(16)} ` +
    `nearest exported symbol=${best.name}+0x${(offset - best.value).toString(16)}\n` :
    'libc resolution unavailable: no exported symbol below fault offset\n';
}

const shimSource = String.raw`#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
static _Atomic int log_fd = -1;
static void *lookup(_Atomic(void *) *slot, const char *name) {
  void *p = atomic_load(slot);
  if (!p) { p = dlsym(RTLD_NEXT, name); atomic_store(slot, p); }
  return p;
}
static char *text(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}
static char *number(char *p, unsigned long n) {
  char digits[32]; unsigned i = 0;
  do { digits[i++] = '0' + n % 10; n /= 10; } while (n);
  while (i) *p++ = digits[--i];
  return p;
}
static int allowed(const char *name) {
  return name && (!strcmp(name, "DBUS_SESSION_BUS_ADDRESS") ||
    !strcmp(name, "FC_FONTATIONS") || !strcmp(name, "VK_ICD_FILENAMES"));
}
static char *escaped(char *p, const char *value) {
  const char *hex = "0123456789abcdef";
  *p++ = '"';
  for (unsigned i = 0; value && value[i] && i < 120; i++) {
    unsigned char c = value[i];
    if (c == '"' || c == '\\') { *p++ = '\\'; *p++ = c; }
    else if (c >= 32 && c < 127) *p++ = c;
    else {
      p = text(p, "\\u00"); *p++ = hex[c >> 4]; *p++ = hex[c & 15];
    }
  }
  *p++ = '"';
  return p;
}
static void record(const char *fn, const char *name, const char *value, int overwrite) {
  int saved = errno, present = name && getenv(name) != NULL;
  int fd = atomic_load(&log_fd);
  if (fd == -1) {
    const char *target = getenv("DIAG_SHIM_LOG");
    int opened = target ? open(target, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600) : -2;
    if (opened < 0) opened = -2;
    int expected = -1;
    if (!atomic_compare_exchange_strong(&log_fd, &expected, opened)) {
      if (opened >= 0) close(opened);
      fd = expected;
    } else fd = opened;
  }
  if (fd >= 0) {
    char buffer[1024], *p = text(buffer, "pid=");
    p = number(p, (unsigned long)getpid()); p = text(p, " tid=");
    p = number(p, (unsigned long)syscall(SYS_gettid)); p = text(p, " fn=");
    p = text(p, fn); p = text(p, " name=");
    if (!name) *p++ = '-';
    for (unsigned i = 0; name && name[i] && name[i] != '=' && i < 64; i++) {
      unsigned char c = name[i]; *p++ = c > 32 && c < 127 ? c : '_';
    }
    p = text(p, " overwrite="); *p++ = overwrite < 0 ? '-' : overwrite ? '1' : '0';
    p = text(p, " present="); *p++ = present ? '1' : '0';
    if (allowed(name)) {
      p = text(p, " value="); p = escaped(p, value);
      p = text(p, " truncated="); *p++ = value && strlen(value) > 120 ? '1' : '0';
    }
    *p++ = '\n';
    ssize_t written;
    do { written = write(fd, buffer, p - buffer); } while (written < 0 && errno == EINTR);
  }
  errno = saved;
}
int setenv(const char *name, const char *value, int overwrite) {
  static _Atomic(void *) cached;
  int (*next)(const char *, const char *, int) = lookup(&cached, "setenv");
  record("setenv", name, value, overwrite != 0);
  return next ? next(name, value, overwrite) : (errno = ENOSYS, -1);
}
int putenv(char *value) {
  static _Atomic(void *) cached;
  int (*next)(char *) = lookup(&cached, "putenv");
  size_t n = strcspn(value, "="); char name[n + 1];
  memcpy(name, value, n); name[n] = 0;
  record("putenv", name, value[n] == '=' ? value + n + 1 : NULL, -1);
  return next ? next(value) : (errno = ENOSYS, -1);
}
int unsetenv(const char *name) {
  static _Atomic(void *) cached;
  int (*next)(const char *) = lookup(&cached, "unsetenv");
  record("unsetenv", name, allowed(name) ? getenv(name) : NULL, -1);
  return next ? next(name) : (errno = ENOSYS, -1);
}
int clearenv(void) {
  static _Atomic(void *) cached;
  int (*next)(void) = lookup(&cached, "clearenv");
  record("clearenv", NULL, NULL, -1);
  return next ? next() : (errno = ENOSYS, -1);
}`;
async function once() {
  const { chromium } = require('playwright');
  const shim = process.argv[3];
  // Preload only Chromium and its descendants, so counts exclude the Node launcher.
  const browser = await chromium.launch({ headless: true, ...(shim ? { env: {
    ...process.env, LD_PRELOAD: shim, DIAG_SHIM_LOG: shimLog,
  } } : {}) });
  try {
    const page = await browser.newPage();
    await page.goto('about:blank');
    if (await page.evaluate(() => 1 + 1) !== 2) throw new Error('Evaluation was not 2');
  } finally { await browser.close(); }
}
function shuffled(items, round) {
  let seed = 2166136261;
  for (const c of `${slot}:${round}`) seed = Math.imul(seed ^ c.charCodeAt(0), 16777619);
  const order = [...items];
  for (let i = order.length - 1; i > 0; i--) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const j = Math.floor(seed / 2 ** 32 * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}
function wilson(f, n) {
  if (!n) return 'n/a';
  const z = 1.959963984540054, p = f / n, d = 1 + z * z / n;
  const center = (p + z * z / (2 * n)) / d;
  const radius = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return `${(100 * Math.max(0, center - radius)).toFixed(2)}–` +
    `${(100 * Math.min(1, center + radius)).toFixed(2)}%`;
}
function choose(n, k) {
  if (k < 0 || k > n) return 0n;
  k = Math.min(k, n - k);
  let value = 1n;
  for (let i = 1; i <= k; i++) value = value * BigInt(n - k + i) / BigInt(i);
  return value;
}
function fisher(a, b) {
  if (!a.n || !b.n) return 'n/a';
  const failures = a.f + b.f, denominator = choose(a.n + b.n, failures);
  let numerator = 0n;
  // Conditional upper tail: the baseline has a greater crash rate than the seed arm.
  for (let x = a.f; x <= Math.min(a.n, failures); x++) {
    numerator += choose(a.n, x) * choose(b.n, failures - x);
  }
  const leading = value => {
    const s = value.toString(), length = Math.min(16, s.length);
    return [Number(s.slice(0, length)) / 10 ** (length - 1), s.length - 1];
  };
  const [top, topExp] = leading(numerator), [bottom, bottomExp] = leading(denominator);
  let mantissa = top / bottom, exponent = topExp - bottomExp;
  if (mantissa < 1) { mantissa *= 10; exponent--; }
  return `${mantissa.toFixed(6)}e${exponent >= 0 ? '+' : ''}${exponent}`;
}
function totals(name) {
  const rows = results.filter(r => r.variant === name);
  const h = rows.filter(r => r.outcome === 'harness-error').length;
  const f = rows.filter(r => ['crash', 'launch-failure'].includes(r.outcome)).length;
  return { rows, h, n: rows.length - h, f };
}
function summary() {
  const lines = [`Chromium launch diagnostic — ${runner}`, '',
    '| Variant | Launches | Failures | Harness errors | Failure rate | Wilson 95% |',
    '| --- | ---: | ---: | ---: | ---: | ---: |'];
  for (const v of variants) {
    const { rows, n, f, h } = totals(v.name);
    lines.push(`| ${v.name} | ${rows.length}/${planned(v)} | ${f} | ${h} | ` +
      `${n ? (100 * f / n).toFixed(2) + '%' : 'n/a'} | ${wilson(f, n)} |`);
  }
  const envCount = name => {
    const v = variants.find(v => v.name === name);
    return v ? Object.keys(v.env).length : 'n/a';
  };
  lines.push('', '| k | Unseeded env vars | Unseeded f/n | Wilson 95% | ' +
    'Seeded env vars | Seeded f/n | Wilson 95% | One-sided Fisher p |',
  '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |');
  for (const k of padCounts) {
    const name = `pad${k}`, seeded = `${name}+seed3`;
    const a = totals(name), b = totals(seeded);
    lines.push(`| ${k} | ${envCount(name)} | ${a.f}/${a.n} | ${wilson(a.f, a.n)} | ` +
      `${envCount(seeded)} | ${b.f}/${b.n} | ${wilson(b.f, b.n)} | ${fisher(a, b)} |`);
  }
  lines.push('', 'Rates, Wilson intervals and Fisher tests exclude harness errors.',
    'Padding pairs use Fisher alternative: unseeded failure rate > seeded.',
    'Direct variants count every unsuccessful launch as a failure.',
    'pw/real crashes require browserType.launch, signal=SIGSEGV or Received signal 11 in output.',
    'Every measurement uses a fresh process; pw/real use a fresh npm/runner/worker chain.',
    'real uses the i18n config/webServer/test, with list and match-count reporters.',
    `real grep: ${realGrep}; expected matched tests: 1.`,
    `Seed source: ${seedSource}.`,
    `Shim launches: ${results.filter(r => r.variant === 'min+shim').length}/25.`);
  for (const [baseline, seeded] of [
    ['real', 'real+seed3'], ['default', 'default+seed3'], ['pw', 'pw+seed3'],
    ['min', 'min+seed3'],
  ]) {
    const selected = [baseline, seeded].every(name => variants.some(v => v.name === name));
    const a = totals(baseline), b = totals(seeded);
    lines.push(`${baseline} vs ${seeded}: one-sided Fisher exact p=` +
      `${selected ? fisher(a, b) : 'n/a (not selected)'}` +
      ' (alternative: baseline failure rate > seeded).');
    const conclusion = !selected ? 'not selected' : !a.n || !b.n ? 'insufficient data' :
      b.f / b.n < a.f / a.n ? 'lower observed seeded failure rate' :
        b.f / b.n > a.f / a.n ? 'higher observed seeded failure rate' :
          'equal observed failure rates';
    lines.push(`Aggregate ${baseline}=${a.f}/${a.n}, ${seeded}=${b.f}/${b.n}: ` +
      `${conclusion}; empirical observations do not establish elimination of failures.`);
  }
  const final = padCounts.map(k => {
    const name = `pad${k}`;
    const { f, n } = totals(name);
    return `${name}=${f}/${n}`;
  });
  const seeded = padCounts.reduce((sum, k) => {
    const { f, n } = totals(`pad${k}+seed3`);
    return { f: sum.f + f, n: sum.n + n };
  }, { f: 0, n: 0 });
  const harness = variants.reduce((n, v) => n + totals(v.name).h, 0);
  lines.push(`Harness errors: ${harness}. Missing measurements: ${missing()}.`,
    ...errors.map(e => `Operational error: ${e}`),
    `DIAG-ROUND6 ${runner} n_pad0=${envCount('pad0')} ${final.join(' ')} | ` +
    `seeded total=${seeded.f}/${seeded.n}`);
  const text = lines.join('\n') + '\n';
  console.log(text);
  try { checkpoint(); } catch (err) { console.log(`Results file: ${err.message}`); }
  try { append('summary.md', text); } catch (err) { console.log(`Summary file: ${err.message}`); }
  try {
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, text);
  } catch (err) { console.log(`Step summary: ${err.message}`); }
}
function mode(values) {
  return values && [...values].sort(([a, na], [b, nb]) => nb - na || a.localeCompare(b))[0];
}
function addValue(map, key, value) {
  if (!map.has(key)) map.set(key, new Map());
  const values = map.get(key);
  values.set(value, (values.get(value) || 0) + 1);
}
function collectShim(log) {
  const entries = log.split('\n').flatMap(line => {
    const m = line.match(/^pid=(\d+) tid=\d+ fn=(\w+) name=(\S*) overwrite=[01-] (.*)$/);
    const fields = m?.[4].match(/^present=([01])(?: value=("(?:[^"\\]|\\.)*") truncated=([01]))?$/);
    if (!fields) return [];
    return [{ pid: m[1], fn: m[2], name: m[3], present: fields[1],
      value: fields[2], truncated: fields[3] === '1' }];
  });
  const browserPid = entries.find(e => e.fn === 'setenv' &&
    e.name === 'DBUS_SESSION_BUS_ADDRESS')?.pid;
  for (const e of entries) {
    const key = JSON.stringify([e.fn, e.name, e.present]);
    shimCounts.set(key, (shimCounts.get(key) || 0) + 1);
    if (e.value === undefined || !Object.hasOwn(fallbackSeed, e.name)) continue;
    const value = JSON.stringify([JSON.parse(e.value), e.truncated]);
    addValue(shimValues, JSON.stringify([e.fn, e.name]), value);
    if (e.pid === browserPid && e.fn === 'setenv') addValue(seedValues, e.name, value);
  }
}
function portFree() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', err => {
      if (err.code === 'EADDRINUSE') resolve(false);
      else reject(err);
    });
    server.listen({ host: '127.0.0.1', port: previewPort, exclusive: true }, () => {
      server.close(err => err ? reject(err) : resolve(true));
    });
  });
}
async function waitForPreviewPort() {
  const until = performance.now() + 10000;
  while (!stopped) {
    if (await portFree()) return;
    const remaining = until - performance.now();
    if (remaining <= 0) throw new Error('Preview port still occupied after 10 s');
    await new Promise(resolve => setTimeout(resolve, Math.min(100, remaining)));
  }
  throw new Error('Interrupted while waiting for preview port');
}
async function launch(v, index, deadline) {
  if (stopped) return;
  if (performance.now() + launchBudget(v) > deadline) {
    stopped = true; errors.push('Measurement budget exhausted'); return;
  }
  const logStart = v.shim ? fs.statSync(shimLog).size : 0;
  const outputDir = v.real ? path.join(out, 'real', `${v.name}-${index}`) : null;
  const exe = v.pw || v.real ? 'npx' : process.execPath;
  const args = v.real ? ['--no-install', 'playwright', 'test', '-c',
    './playwright.i18n.config.ts', '--grep', realGrep, '--output', outputDir,
    '--reporter', `list,${realReporter}`] : v.pw ? ['--no-install', 'playwright', 'test', '-c',
    '../.github/scripts/diag/playwright.diag.config.mjs'] :
    [file, '--once', ...(v.shim ? [shimLibrary] : [])];
  let r;
  try {
    if (v.real) await waitForPreviewPort();
    if (stopped) throw new Error('Interrupted before launch');
    r = await command(exe, args,
      { env: v.env, timeout: v.real ? 100000 : 70000, limit: 256 * 1024, settle: true });
  } catch (err) {
    r = { exitCode: null, signal: null, timedOut: false, error: err.message,
      crashOutput: false, stdout: '', stderr: '', outputTail: err.message, wallMs: 0 };
  }
  let outcome = 'ok';
  if (failed(r)) {
    outcome = v.pw || v.real ? r.crashOutput ? 'crash' : 'harness-error' : 'launch-failure';
  }
  let matchedTests;
  if (v.real) {
    const matches = [...r.stdout.matchAll(/^DIAG-REAL-MATCHED (\d+)\r?$/gm)];
    matchedTests = matches.length === 1 ? Number(matches[0][1]) : null;
    if (matchedTests !== 1) {
      outcome = 'harness-error';
      r.error ||= `Expected one matched test; observed ${matchedTests ?? 'unavailable'}`;
    } else if (!failed(r) &&
        [...r.stdout.matchAll(/^DIAG-REAL-TEST (\w+)\r?$/gm)]
          .map(m => m[1]).join(',') !== 'passed') {
      outcome = 'harness-error'; r.error = 'Expected one passed test';
    }
    if (index === 1) {
      console.log(`${v.name}: first launch matched=${matchedTests ?? 'unavailable'} (expected 1)`);
    }
  }
  if (loadFailed) outcome = 'harness-error';
  const row = { variant: v.name, index, round: index, slot, wallMs: r.wallMs,
    envCount: Object.keys(v.env).length,
    exitCode: r.exitCode, signal: r.signal, timedOut: r.timedOut, error: r.error,
    outcome, crashOutput: r.crashOutput, ...(v.real ? { matchedTests } : {}) };
  if (r.cleanupIncomplete) {
    stopped = true; errors.push('Launch cleanup unconfirmed; remaining measurements stopped.');
  }
  if (v.shim) {
    if (r.cleanupIncomplete) {
      row.shimAttribution = 'incomplete'; stopped = true;
      errors.push('Shim cleanup unconfirmed; attribution incomplete; pass stopped.');
    }
    row.shimLogStart = logStart; row.shimLogEnd = fs.statSync(shimLog).size;
    const log = fs.readFileSync(shimLog).subarray(logStart, row.shimLogEnd).toString();
    collectShim(log);
  }
  if (outputDir) {
    if (outcome === 'crash' && keptRealCrashes < 5) {
      keptRealCrashes++; row.outputDir = path.relative(out, outputDir);
    } else {
      fs.rmSync(outputDir, { recursive: true, force: true });
    }
  }
  if (outcome !== 'ok') {
    row.stderr = r.stderr; row.failureArtifact = `failures/${v.name}-${index}.txt`;
  }
  results.push(row); append('results.jsonl', JSON.stringify(row) + '\n'); checkpoint();
  if (outcome === 'harness-error' && printedHarnessErrors++ < 3) {
    const tail = r.outputTail.trimEnd().split('\n').slice(-30).join('\n');
    console.log(`Harness error ${v.name}-${index}: ${r.error || describe(r).split('\n')[0]}`);
    console.log(tail || '(no output)');
  }
  if (outcome !== 'ok') {
    const ps = await shell('ps -eo pid,ppid,pcpu,pmem,comm --sort=-pcpu | head -15');
    const load = await command('cat', ['/proc/loadavg']);
    append(row.failureArtifact, JSON.stringify(row) + '\n' + describe(r) +
      `\nps: ${describe(ps)}\nloadavg: ${describe(load)}` + await address(r.stdout + r.stderr));
  }
}
async function shimPass(deadline) {
  const report = text => fs.writeFileSync(path.join(out, 'setenv-summary.txt'), text + '\n');
  if (!compiler) {
    report('No C compiler found (cc, gcc, clang); shim pass skipped.');
    return 'no C compiler';
  }
  if (stopped) {
    report('Shim pass skipped: interrupted or budget exhausted.');
    return 'interrupted or budget exhausted';
  }
  const source = path.join(out, 'shim.c');
  fs.writeFileSync(source, shimSource);
  const args = ['-shared', '-fPIC', '-O1', '-o', shimLibrary, source, '-ldl'];
  append('env.txt', `\n$ ${compiler} ${args.join(' ')}\n`);
  const build = await command(compiler, args, { timeout: 30000 });
  append('env.txt', describe(build));
  if (failed(build)) {
    report('Shim compilation failed; see env.txt. Pass skipped.');
    return 'shim compilation failed';
  }
  fs.writeFileSync(shimLog, '');
  const shim = { name: 'min+shim', shim: true, env: minimal };
  try {
    for (let index = 1; index <= 25 && !stopped; index++) {
      await launch(shim, index, deadline);
    }
  } finally {
    const lines = ['Chromium/descendant calls; counts include failed launches.',
      'Values are logged only for the three seed names, at most 120 bytes.',
      'fn\tname\tpresent\tmin+shim'];
    for (const [key, counts] of [...shimCounts].sort(([a], [b]) => a.localeCompare(b))) {
      lines.push([...JSON.parse(key), counts].join('\t'));
    }
    lines.push('', 'Most frequent value per (fn,name):', 'fn\tname\tvalue\ttruncated\tcount');
    for (const [key, values] of [...shimValues].sort(([a], [b]) => a.localeCompare(b))) {
      const [encoded, n] = mode(values), [value, truncated] = JSON.parse(encoded);
      lines.push([...JSON.parse(key), JSON.stringify(value), truncated, n].join('\t'));
    }
    if (!shimCounts.size) lines.push('No interposed calls observed; instrumentation unconfirmed.');
    if (stopped) lines.push('Shim pass incomplete; see results.json and diagnostic errors.');
    report(lines.join('\n'));
  }
}
function deriveSeed(reason) {
  const fallback = [];
  for (const name of Object.keys(fallbackSeed)) {
    const best = mode(seedValues.get(name));
    const [value, truncated] = best ? JSON.parse(best[0]) : [undefined, false];
    if (value === undefined || truncated) fallback.push(name);
    else seed3[name] = value;
  }
  const observed = results.filter(r => r.variant === 'min+shim').length;
  seedSource = fallback.length === 3 ? 'fixed fallback' :
    `Chromium browser setenv modes (${observed}/25 min shim launches)`;
  if (fallback.length) {
    seedSource += `; fixed fallback for ${fallback.join(', ')} ` +
      `(${reason || 'missing or truncated browser setenv value'})`;
  }
  append('env.txt', `\nSeed source: ${seedSource}\n`);
  for (const [name, value] of Object.entries(seed3)) {
    append('env.txt', `seed3 ${name}=${JSON.stringify(value)}\n`);
  }
}
function mainVariants(names) {
  const realEnv = { ...process.env, RP_I18N_PREVIEW_PORT: String(previewPort) };
  const padded = padCounts.flatMap(k => {
    const padding = Object.fromEntries(Array.from({ length: k }, (_, i) =>
      [`DIAG_PAD_${i + 1}`, '1']));
    const env = { ...process.env, ...padding };
    if (names.includes(`pad${k}`) || names.includes(`pad${k}+seed3`)) {
      if (Object.keys(env).length !== Object.keys(process.env).length + k) {
        throw new Error('Padding names already exist in the job environment');
      }
    }
    return [{ name: `pad${k}`, env },
      { name: `pad${k}+seed3`, env: { ...env, ...seed3 } }];
  });
  const all = [
    ...padded,
    { name: 'real', real: true, env: realEnv },
    { name: 'real+seed3', real: true, env: { ...realEnv, ...seed3 } },
    { name: 'default', env: { ...process.env } },
    { name: 'min', env: minimal },
    { name: 'min+seed3', env: { ...minimal, ...seed3 } },
    { name: 'min+tz', env: { ...minimal, TZ: 'UTC' } },
    { name: 'default+seed3', env: { ...process.env, ...seed3 } },
    { name: 'pw', pw: true, env: { ...process.env } },
    { name: 'pw+seed3', pw: true, env: { ...process.env, ...seed3 } },
  ];
  return names.map(name => all.find(v => v.name === name));
}
async function main() {
  const deadline = performance.now() + 35 * 60 * 1000;
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
    stopped = true; errors.push(`Interrupted by ${signal}`); activeStop(); stopLoad();
  });
  try {
    fs.mkdirSync(path.join(out, 'failures'), { recursive: true });
    fs.writeFileSync(path.join(out, 'setenv-summary.txt'), 'Shim pass not run.\n');
    const positive = (name, fallback) => {
      const input = process.env[name] ?? String(fallback);
      if (!/^[1-9]\d*$/.test(input) || !Number.isSafeInteger(Number(input))) {
        throw new Error(`${name} must be a positive safe integer`);
      }
      return Number(input);
    };
    count = positive('DIAG_LAUNCHES', 200);
    pwCount = positive('DIAG_PW_LAUNCHES', 150);
    realCount = positive('DIAG_REAL_LAUNCHES', 150);
    if (!/^\d+$/.test(slot) || !Number.isSafeInteger(Number(slot)) || Number(slot) > 61362) {
      throw new Error('DIAG_SLOT must be an integer from 0 to 61362');
    }
    previewPort += Number(slot);
    const workers = process.env.DIAG_LOAD_WORKERS ?? '0';
    if (!/^\d+$/.test(workers) || !Number.isSafeInteger(Number(workers))) {
      throw new Error('DIAG_LOAD_WORKERS must be a nonnegative safe integer');
    }
    loadCount = Number(workers); load.workers = loadCount;
    const names = (process.env.DIAG_VARIANTS ?? defaultVariants.join(','))
      .split(',').map(s => s.trim());
    if (new Set(names).size !== names.length ||
        names.some(name => !variantNames.includes(name))) {
      throw new Error('DIAG_VARIANTS must be a comma list of unique known variant names');
    }
    variants = mainVariants(names);
    await capture();
    deriveSeed(await shimPass(deadline));
    variants = mainVariants(names);
    await startLoad();
    loadSnapshot('before');
    const rounds = Math.max(...variants.map(planned));
    for (let index = 1; index <= rounds && !stopped; index++) {
      const eligible = variants.filter(v => index <= planned(v));
      for (const v of shuffled(eligible, index)) await launch(v, index, deadline);
    }
  } catch (err) { errors.push(err.stack || String(err)); }
  finally {
    try { loadSnapshot('after'); }
    catch { errors.push('Post-measurement load snapshot failed'); }
    await cleanupLoad(); summary();
  }
}
if (process.argv[2] === '--load-worker') {
  const deadline = Math.min(Number(process.argv[3]), Date.now() + 50 * 60 * 1000);
  if (!Number.isSafeInteger(deadline) || deadline <= Date.now()) process.exit(1);
  setTimeout(() => process.exit(0), Math.max(1, deadline - Date.now()));
  process.once('disconnect', () => process.exit(0));
  const busy = () => {
    const until = Math.min(deadline, Date.now() + 20);
    while (Date.now() < until) { /* Busy for a bounded slice. */ }
    if (Date.now() >= deadline) process.exit(0);
    setImmediate(busy);
  };
  process.send?.('ready'); busy();
} else if (process.argv[2] === '--once') {
  once().then(() => { process.exitCode = 0; }, err => {
    console.error(err); process.exitCode = 1;
  });
} else { await main(); process.exitCode = 0; }
