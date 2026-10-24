import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

// One source of truth for parent measurements and fresh child launches.
const variants = [
  { name: 'default', args: [] }, // Baseline Playwright launch.
  { name: 'cold', args: [], cold: true }, // Host cache pressure, when permitted.
  { name: 'no-gpu', args: ['--disable-gpu'] }, // Disable GPU acceleration.
  { name: 'no-zygote', args: ['--no-zygote'] }, // Bypass Chromium's zygote.
  { name: 'no-dbus', args: [], env: {
    DBUS_SESSION_BUS_ADDRESS: 'disabled:', DBUS_SYSTEM_BUS_ADDRESS: 'disabled:',
  } }, // Invalidate bus addresses; unverified --disable-features=DBus is omitted.
  { name: 'single-process-free', args: [], minimal: true }, // Environment isolation only.
];
const file = fileURLToPath(import.meta.url);
const frontend = path.resolve(path.dirname(file), '../../frontend');
const require = createRequire(path.join(frontend, 'package.json'));
const out = path.join(frontend, 'out');
const runner = process.env.RUNNER_NAME || os.hostname();
const results = [];
const errors = [];
let stopped = false;
let activeStop = () => {};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
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
function kill(pid, signal) {
  try { process.kill(pid, signal); } catch { /* Process already gone. */ }
}
function command(exe, args, { env = process.env, timeout = 8000, limit = 65536 } = {}) {
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
    const finish = (exitCode, signal) => {
      if (finished) return;
      finished = true;
      clean('SIGKILL');
      clearInterval(watch); clearTimeout(timer);
      clearTimeout(hardTimer); clearTimeout(fallback);
      child.stdout.destroy(); child.stderr.destroy();
      if (activeStop === stop) activeStop = () => {};
      resolve({ exitCode, signal, timedOut, error, stdout: stdout.toString(),
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
const checkpoint = count => fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({
  runner, plannedPerVariant: count, missing: count * variants.length - results.length,
  results, errors,
}, null, 2) + '\n');

async function capture() {
  append('env.txt', `runner=${runner}\nhostname=${os.hostname()}\n`);
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

async function once() {
  const variant = variants.find(v => v.name === process.argv[3]);
  if (!variant) throw new Error('Unknown variant');
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true, args: variant.args });
  try {
    const page = await browser.newPage();
    await page.goto('about:blank');
    if (await page.evaluate(() => 1 + 1) !== 2) throw new Error('Evaluation was not 2');
  } finally { await browser.close(); }
}
function summary(count) {
  const lines = [`Chromium launch diagnostic — ${runner}`, '',
    '| Variant | Launches | Failures | Failure rate | Median ms | p95 ms |',
    '| --- | ---: | ---: | ---: | ---: | ---: |'];
  for (const v of variants) {
    const rows = results.filter(r => r.variant === v.name);
    const times = rows.map(r => r.wallMs).sort((a, b) => a - b);
    const failures = rows.filter(failed).length;
    const q = p => times.length ? times[Math.max(0, Math.ceil(times.length * p) - 1)] : 'n/a';
    const median = times.length ? (times[Math.floor((times.length - 1) / 2)] +
      times[Math.floor(times.length / 2)]) / 2 : 'n/a';
    lines.push(`| ${v.name} | ${rows.length}/${count} | ${failures} | ` +
      `${rows.length ? (100 * failures / rows.length).toFixed(2) + '%' : 'n/a'} | ` +
      `${median} | ${q(0.95)} |`);
  }
  lines.push('', `Environment: ${os.platform()} ${os.arch()}; host ${os.hostname()}; ` +
    `${os.cpus().length} CPUs; ${Math.round(os.totalmem() / 1048576)} MiB RAM. See env.txt.`,
  'Times include fresh Node startup, launch, page/evaluate, browser close and bounded cleanup.',
  'no-dbus omits unverified --disable-features=DBus; single-process-free uses no special flag.',
  `Missing measurements: ${count * variants.length - results.length}.`,
  ...errors.map(e => `Operational error: ${e}`));
  const text = lines.join('\n') + '\n';
  console.log(text);
  try { checkpoint(count); } catch (err) { console.log(`Results file: ${err.message}`); }
  try { append('summary.md', text); } catch (err) { console.log(`Summary file: ${err.message}`); }
  try {
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, text);
  } catch (err) { console.log(`Step summary: ${err.message}`); }
  console.log(`DIAG-RESULT ${runner} failures=${results.filter(failed).length}` +
    `/${results.length}`);
}
async function main() {
  let count = 60;
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
    stopped = true; errors.push(`Interrupted by ${signal}`); activeStop();
  });
  try {
    fs.mkdirSync(path.join(out, 'failures'), { recursive: true });
    const input = process.env.DIAG_LAUNCHES ?? '60';
    if (!/^[1-9]\d*$/.test(input) || !Number.isSafeInteger(Number(input))) {
      throw new Error('DIAG_LAUNCHES must be a positive safe integer');
    }
    count = Number(input);
    await capture();
    const deadline = performance.now() + 28 * 60 * 1000;
    for (const v of variants) for (let index = 1; index <= count && !stopped; index++) {
      if (performance.now() + 75000 > deadline) {
        stopped = true; errors.push('Measurement budget exhausted'); break;
      }
      let coldCache;
      if (v.cold) {
        const sync = await command('sync', []);
        const drop = await shell('echo 3 | sudo -n tee /proc/sys/vm/drop_caches');
        coldCache = `sync: ${describe(sync)}drop_caches: ${describe(drop)}`;
        if (sync.exitCode !== 0 || drop.exitCode !== 0) coldCache += 'drop_caches unavailable\n';
        await sleep(2000);
      }
      if (stopped) break;
      const env = v.minimal ? Object.fromEntries(['PATH', 'HOME', 'USER', 'LANG']
        .filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]])) :
        { ...process.env, ...v.env };
      const r = await command(process.execPath, [file, '--once', v.name],
        { env, timeout: 70000, limit: 20 * 1024 });
      const row = { variant: v.name, index, wallMs: r.wallMs, exitCode: r.exitCode,
        signal: r.signal, timedOut: r.timedOut, error: r.error, coldCache };
      if (failed(r)) {
        row.stderr = r.stderr;
        row.failureArtifact = `failures/${v.name}-${index}.txt`;
      }
      results.push(row);
      append('results.jsonl', JSON.stringify(row) + '\n');
      checkpoint(count);
      if (failed(r)) {
        const ps = await shell('ps -eo pid,ppid,pcpu,pmem,comm --sort=-pcpu | head -15');
        const load = await command('cat', ['/proc/loadavg']);
        append(row.failureArtifact, JSON.stringify(row) + '\n' +
          describe(r) + `\nps: ${describe(ps)}\nloadavg: ${describe(load)}` +
          await address(r.stderr));
      }
    }
  } catch (err) { errors.push(err.stack || String(err)); }
  finally { summary(count); }
}
if (process.argv[2] === '--once') {
  once().then(() => { process.exitCode = 0; }, err => {
    console.error(err); process.exitCode = 1;
  });
} else { await main(); process.exitCode = 0; }
