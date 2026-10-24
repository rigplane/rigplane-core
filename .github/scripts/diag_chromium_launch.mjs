import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const pick = names => Object.fromEntries(names.filter(k => process.env[k] !== undefined)
  .map(k => [k, process.env[k]]));
const baseNames = ['PATH', 'HOME', 'USER', 'LANG'];
const minimal = pick(baseNames);
const otherNames = Object.keys(process.env).filter(k => !baseNames.includes(k)).sort();
const halfA = otherNames.slice(0, Math.floor(otherNames.length / 2));
const halfB = otherNames.slice(halfA.length);
const allVariants = [
  { name: 'default', env: { ...process.env } },
  { name: 'min', env: minimal },
  { name: 'min+gh', env: { ...minimal,
    ...pick(otherNames.filter(k => /^(GITHUB_|RUNNER_)|^CI$/.test(k))) } },
  { name: 'min+xdg', env: { ...minimal, ...pick(otherNames.filter(k => /^XDG_/.test(k))) } },
  { name: 'min+lc', env: { ...minimal, LC_ALL: 'C.UTF-8', LANGUAGE: 'en_US:en' } },
  { name: 'min+tz', env: { ...minimal, TZ: 'UTC' } },
  { name: 'halfA', env: { ...minimal, ...pick(halfA) } },
  { name: 'halfB', env: { ...minimal, ...pick(halfB) } },
];
let variants = allVariants;
const file = fileURLToPath(import.meta.url);
const frontend = path.resolve(path.dirname(file), '../../frontend');
const require = createRequire(path.join(frontend, 'package.json'));
const out = path.join(frontend, 'out');
const shimLibrary = path.join(out, 'setenv_shim.so');
const shimLog = path.join(out, 'setenv.log');
const runner = process.env.RUNNER_NAME || os.hostname();
const slot = process.env.DIAG_SLOT ?? '0';
const results = [];
const errors = [];
const shimCounts = new Map();
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
function command(exe, args, { env = process.env, timeout = 8000, limit = 65536,
  settle = false } = {}) {
  return new Promise(resolve => {
    const start = performance.now();
    const child = spawn(exe, args, { env, cwd: frontend, detached: true,
      stdio: ['ignore', 'pipe', 'pipe'] });
    const known = new Map();
    let stdout = Buffer.alloc(0), stderr = Buffer.alloc(0), error = '', timedOut = false;
    let finished = false, hardTimer, fallback;
    const collect = (old, chunk) => Buffer.concat([old, chunk]).subarray(0, limit);
    child.stdout.on('data', chunk => { stdout = collect(stdout, chunk); });
    child.stderr.on('data', chunk => { stderr = collect(stderr, chunk); });
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
      resolve({ exitCode, signal, timedOut, error, cleanupIncomplete: settle && live(),
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
const missing = count => variants.reduce((n, v) => n + (v.shim ? 25 : count), 0) -
  results.length;
const checkpoint = count => fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({
  runner, slot, plannedPerVariant: count, plannedPerShimVariant: 25,
  missing: missing(count), results, errors,
}, null, 2) + '\n');

async function capture() {
  append('env.txt', `runner=${runner}\nhostname=${os.hostname()}\n`);
  append('env.txt', `slot=${slot}\nhalfA names: ${halfA.join(',')}\n` +
    `halfB names: ${halfB.join(',')}\n`);
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
static void record(const char *fn, const char *name, int overwrite) {
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
    char buffer[256], *p = text(buffer, "pid=");
    p = number(p, (unsigned long)getpid()); p = text(p, " tid=");
    p = number(p, (unsigned long)syscall(SYS_gettid)); p = text(p, " fn=");
    p = text(p, fn); p = text(p, " name=");
    if (!name) *p++ = '-';
    for (unsigned i = 0; name && name[i] && name[i] != '=' && i < 64; i++) {
      unsigned char c = name[i]; *p++ = c > 32 && c < 127 ? c : '_';
    }
    p = text(p, " overwrite="); *p++ = overwrite < 0 ? '-' : overwrite ? '1' : '0';
    p = text(p, " present="); *p++ = present ? '1' : '0'; *p++ = '\n';
    ssize_t written;
    do { written = write(fd, buffer, p - buffer); } while (written < 0 && errno == EINTR);
  }
  errno = saved;
}
int setenv(const char *name, const char *value, int overwrite) {
  static _Atomic(void *) cached;
  int (*next)(const char *, const char *, int) = lookup(&cached, "setenv");
  record("setenv", name, overwrite != 0);
  return next ? next(name, value, overwrite) : (errno = ENOSYS, -1);
}
int putenv(char *value) {
  static _Atomic(void *) cached;
  int (*next)(char *) = lookup(&cached, "putenv");
  size_t n = strcspn(value, "="); char name[n + 1];
  memcpy(name, value, n); name[n] = 0;
  record("putenv", name, -1);
  return next ? next(value) : (errno = ENOSYS, -1);
}
int unsetenv(const char *name) {
  static _Atomic(void *) cached;
  int (*next)(const char *) = lookup(&cached, "unsetenv");
  record("unsetenv", name, -1);
  return next ? next(name) : (errno = ENOSYS, -1);
}
int clearenv(void) {
  static _Atomic(void *) cached;
  int (*next)(void) = lookup(&cached, "clearenv");
  record("clearenv", NULL, -1);
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
function summary(count) {
  const lines = [`Chromium launch diagnostic — ${runner}`, '',
    '| Variant | Launches | Failures | Failure rate | Wilson 95% | Median ms | p95 ms |',
    '| --- | ---: | ---: | ---: | ---: | ---: | ---: |'];
  const totals = [];
  for (const v of variants) {
    const rows = results.filter(r => r.variant === v.name), n = rows.length;
    const times = rows.map(r => r.wallMs).sort((a, b) => a - b), f = rows.filter(failed).length;
    const q = p => n ? times[Math.max(0, Math.ceil(n * p) - 1)] : 'n/a';
    const median = n ? (times[Math.floor((n - 1) / 2)] + times[Math.floor(n / 2)]) / 2 : 'n/a';
    lines.push(`| ${v.name} | ${n}/${v.shim ? 25 : count} | ${f} | ` +
      `${n ? (100 * f / n).toFixed(2) + '%' : 'n/a'} | ${wilson(f, n)} | ${median} | ` +
      `${q(0.95)} |`);
    totals.push(`${v.name}=${f}/${n}`);
  }
  lines.push('', 'All variants use fresh processes and no extra Chromium flags.',
    'Times include Node startup, launch, page/evaluate, browser close and bounded cleanup.',
    `Missing measurements: ${missing(count)}.`, ...errors.map(e => `Operational error: ${e}`),
    `DIAG-ROUND2 ${runner} ${totals.join(' ')}`);
  const text = lines.join('\n') + '\n';
  console.log(text);
  try { checkpoint(count); } catch (err) { console.log(`Results file: ${err.message}`); }
  try { append('summary.md', text); } catch (err) { console.log(`Summary file: ${err.message}`); }
  try {
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, text);
  } catch (err) { console.log(`Step summary: ${err.message}`); }
}
async function launch(v, index, count, deadline) {
  if (stopped) return;
  if (performance.now() + 75000 > deadline) {
    stopped = true; errors.push('Measurement budget exhausted'); return;
  }
  const logStart = v.shim ? fs.statSync(shimLog).size : 0;
  const r = await command(process.execPath, [file, '--once', ...(v.shim ? [shimLibrary] : [])],
    { env: v.env, timeout: 70000, limit: 20 * 1024, settle: Boolean(v.shim) });
  const row = { variant: v.name, index, round: index, slot, wallMs: r.wallMs,
    exitCode: r.exitCode, signal: r.signal, timedOut: r.timedOut, error: r.error };
  if (v.shim) {
    if (r.cleanupIncomplete) {
      row.shimAttribution = 'incomplete'; stopped = true;
      errors.push('Shim cleanup unconfirmed; attribution incomplete; pass stopped.');
    }
    row.shimLogStart = logStart; row.shimLogEnd = fs.statSync(shimLog).size;
    const log = fs.readFileSync(shimLog).subarray(logStart, row.shimLogEnd).toString();
    for (const line of log.split('\n')) {
      const m = line.match(/ fn=(\w+) name=(\S*) overwrite=[01-] present=([01])$/);
      if (!m) continue;
      const key = JSON.stringify([m[1], m[2], m[3]]);
      if (!shimCounts.has(key)) shimCounts.set(key, { 'min+shim': 0, 'default+shim': 0 });
      shimCounts.get(key)[v.name]++;
    }
  }
  if (failed(r)) {
    row.stderr = r.stderr; row.failureArtifact = `failures/${v.name}-${index}.txt`;
  }
  results.push(row); append('results.jsonl', JSON.stringify(row) + '\n'); checkpoint(count);
  if (failed(r)) {
    const ps = await shell('ps -eo pid,ppid,pcpu,pmem,comm --sort=-pcpu | head -15');
    const load = await command('cat', ['/proc/loadavg']);
    append(row.failureArtifact, JSON.stringify(row) + '\n' + describe(r) +
      `\nps: ${describe(ps)}\nloadavg: ${describe(load)}` + await address(r.stderr));
  }
}
async function shimPass(count, deadline) {
  const report = text => fs.writeFileSync(path.join(out, 'setenv-summary.txt'), text + '\n');
  if (!compiler) { report('No C compiler found (cc, gcc, clang); shim pass skipped.'); return; }
  if (stopped) { report('Shim pass skipped: interrupted or budget exhausted.'); return; }
  const source = path.join(out, 'shim.c');
  fs.writeFileSync(source, shimSource);
  const args = ['-shared', '-fPIC', '-O1', '-o', shimLibrary, source, '-ldl'];
  append('env.txt', `\n$ ${compiler} ${args.join(' ')}\n`);
  const build = await command(compiler, args, { timeout: 30000 });
  append('env.txt', describe(build));
  if (failed(build)) { report('Shim compilation failed; see env.txt. Pass skipped.'); return; }
  fs.writeFileSync(shimLog, '');
  const extra = ['min', 'default'].map(name => ({ name: `${name}+shim`, shim: true,
    env: allVariants.find(v => v.name === name).env }));
  variants = [...variants, ...extra];
  try {
    for (let index = 1; index <= 25 && !stopped; index++) {
      for (const v of shuffled(extra, index)) await launch(v, index, count, deadline);
    }
  } finally {
    const lines = ['Chromium/descendant calls; names only; counts include failed launches.',
      'fn\tname\tpresent\tmin+shim\tdefault+shim'];
    for (const [key, counts] of [...shimCounts].sort(([a], [b]) => a.localeCompare(b))) {
      lines.push([...JSON.parse(key), counts['min+shim'], counts['default+shim']].join('\t'));
    }
    if (!shimCounts.size) lines.push('No interposed calls observed; instrumentation unconfirmed.');
    if (stopped) lines.push('Shim pass incomplete; see results.json and diagnostic errors.');
    report(lines.join('\n'));
  }
}
async function main() {
  let count = 300;
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
    stopped = true; errors.push(`Interrupted by ${signal}`); activeStop();
  });
  try {
    fs.mkdirSync(path.join(out, 'failures'), { recursive: true });
    fs.writeFileSync(path.join(out, 'setenv-summary.txt'), 'Shim pass not run.\n');
    const input = process.env.DIAG_LAUNCHES ?? '300';
    if (!/^[1-9]\d*$/.test(input) || !Number.isSafeInteger(Number(input))) {
      throw new Error('DIAG_LAUNCHES must be a positive safe integer');
    }
    count = Number(input);
    const names = (process.env.DIAG_VARIANTS ?? allVariants.map(v => v.name).join(','))
      .split(',').map(s => s.trim());
    if (new Set(names).size !== names.length ||
        names.some(name => !allVariants.some(v => v.name === name))) {
      throw new Error('DIAG_VARIANTS must be a comma list of unique known variant names');
    }
    variants = names.map(name => allVariants.find(v => v.name === name));
    await capture();
    const deadline = performance.now() + 28 * 60 * 1000;
    for (let index = 1; index <= count && !stopped; index++) {
      for (const v of shuffled(variants, index)) await launch(v, index, count, deadline);
    }
    await shimPass(count, deadline);
  } catch (err) { errors.push(err.stack || String(err)); }
  finally { summary(count); }
}
if (process.argv[2] === '--once') {
  once().then(() => { process.exitCode = 0; }, err => {
    console.error(err); process.exitCode = 1;
  });
} else { await main(); process.exitCode = 0; }
