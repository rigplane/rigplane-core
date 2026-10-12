import { spawn } from 'node:child_process';
import { connect } from 'node:net';
import path from 'node:path';

/**
 * A journey fake (tests/e2e/journey_web_server.py) the spec itself owns:
 * spawned detached so it lives in its own process group, which lets
 * kill() take the uv wrapper and the Python server down with one SIGKILL
 * to the group. The server-restart scenario uses this to start a fake on
 * its own port, kill it mid-session and start a replacement on the same
 * port.
 */

const HERE = path.dirname(new URL(import.meta.url).pathname);
const REPO_ROOT = path.resolve(HERE, '../../../..');
const START_TIMEOUT_MS = 30_000;
const KILL_TIMEOUT_MS = 10_000;
const POLL_INTERVAL_MS = 100;

export interface JourneyServerHandle {
  kill(): Promise<void>;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function portAcceptsConnections(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host: '127.0.0.1', port }, () => {
      socket.destroy();
      resolve(true);
    });
    socket.on('error', () => resolve(false));
    socket.setTimeout(500, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

async function until(deadlineMs: number, condition: () => Promise<boolean>): Promise<boolean> {
  while (Date.now() < deadlineMs) {
    if (await condition()) return true;
    await delay(POLL_INTERVAL_MS);
  }
  return await condition();
}

export async function startJourneyServer(
  port: number,
  freqHz: number,
  extraArgs: string[] = [],
): Promise<JourneyServerHandle> {
  // Without this check the readiness wait below could be answered by the
  // process already on the port instead of the one spawned here.
  if (await portAcceptsConnections(port)) {
    throw new Error(`something already listens on port ${port}; not starting a journey server on it`);
  }
  const child = spawn(
    'uv',
    [
      'run', 'python', 'tests/e2e/journey_web_server.py',
      '--port', String(port), '--freq', String(freqHz),
      ...extraArgs,
    ],
    { cwd: REPO_ROOT, detached: true, stdio: 'ignore' },
  );
  if (child.pid === undefined) {
    throw new Error(`failed to spawn uv for the journey server on port ${port}`);
  }
  child.unref();
  const apiUrl = `http://127.0.0.1:${port}/api/v1/state`;
  const exited = () => child.exitCode !== null;
  const answered = async () => {
    try {
      return (await fetch(apiUrl)).ok;
    } catch {
      return false;
    }
  };
  const ready = await until(Date.now() + START_TIMEOUT_MS, async () => exited() || (await answered()));
  if (exited()) {
    throw new Error(`journey server on port ${port} exited with code ${child.exitCode} before ${apiUrl} answered`);
  }
  if (!ready) {
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch (error) {
      if ((error as { code?: unknown }).code !== 'ESRCH') throw error;
    }
    throw new Error(`${apiUrl} did not answer within ${START_TIMEOUT_MS} ms`);
  }
  let killed = false;
  return {
    async kill(): Promise<void> {
      if (killed) return;
      killed = true;
      try {
        process.kill(-child.pid!, 'SIGKILL');
      } catch (error) {
        if ((error as { code?: unknown }).code !== 'ESRCH') throw error;
      }
      const closed = await until(Date.now() + KILL_TIMEOUT_MS, async () => !(await portAcceptsConnections(port)));
      if (!closed) {
        throw new Error(`port ${port} still accepts connections ${KILL_TIMEOUT_MS} ms after SIGKILL`);
      }
    },
  };
}
