import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/lib/runtime/system-controller.ts', import.meta.url), 'utf8');
function fixture() {
  const calls = [];
  const window = new EventTarget();
  const context = vm.createContext({ window, CustomEvent, AbortController,
    controllerClient: { remote: false, prepare: async () => calls.push('prepare') },
    ControllerClientError: Error, emitLocalNotification: () => calls.push('error'),
  });
  const code = stripTypeScriptTypes(source.replace(/^import .*;\n/gm, '').replace(/^export /gm, ''), { mode: 'transform' });
  vm.runInContext(code.replace('const systemController = new SystemController();', ''), context);
  context.effects = Object.fromEntries(['destroyAudio','disconnectWebSockets','destroyMediaSession','setRadioDisconnected','resetRadioState','initMediaSession','reconnectWebSockets'].map(name => [name, () => calls.push(name)]));
  const controller = vm.runInContext('new SystemController(effects)', context);
  return { window, controller, calls };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('explicit connect awaits host once before browser adoption', async () => {
  const { window, controller, calls } = fixture();
  await controller.disconnect(); calls.length = 0;
  let finish;
  window.addEventListener('rigplane:before-connect', event => {
    calls.push('host'); event.detail.handle(() => new Promise(resolve => { finish = resolve; }));
  });
  controller.connect({ operatorInitiated: true }); controller.connect({ operatorInitiated: true }); await settle();
  assert.deepEqual(calls, ['host']);
  finish(true); await settle();
  assert.deepEqual(calls, ['host', 'prepare', 'initMediaSession', 'reconnectWebSockets']);
});

test('host navigation and cancellation never resume an obsolete browser attempt', async () => {
  for (const cancel of [false, true]) {
    const { window, controller, calls } = fixture();
    await controller.disconnect(); calls.length = 0;
    let finish, signal;
    window.addEventListener('rigplane:before-connect', event => {
      signal = event.detail.signal; event.detail.handle(() => new Promise(resolve => { finish = resolve; }));
    });
    controller.connect({ operatorInitiated: true }); await settle();
    if (cancel) await controller.disconnect();
    finish(cancel); await settle();
    assert.equal(signal.aborted, cancel);
    assert.ok(!calls.includes('prepare'));
    assert.ok(!calls.includes('reconnectWebSockets'));
  }
});

test('unhandled local connect retains ordinary browser lifecycle', async () => {
  const { controller, calls } = fixture();
  await controller.disconnect(); calls.length = 0; controller.connect({ operatorInitiated: true }); await settle();
  assert.deepEqual(calls, ['prepare', 'initMediaSession', 'reconnectWebSockets']);
});


test('bootstrap/remount connect does not ask host to reacquire', async () => {
  const { window, controller, calls } = fixture();
  await controller.disconnect(); calls.length = 0;
  window.addEventListener('rigplane:before-connect', () => calls.push('host'));
  controller.connect(); await settle();
  assert.deepEqual(calls, ['prepare', 'initMediaSession', 'reconnectWebSockets']);
});

test('only the first host handler runs and rejection never prepares browser', async () => {
  const { window, controller, calls } = fixture();
  await controller.disconnect(); calls.length = 0;
  window.addEventListener('rigplane:before-connect', event => {
    assert.equal(event.detail.handle(async () => { calls.push('first'); throw new Error('refused'); }), true);
    assert.equal(event.detail.handle(async () => { calls.push('second'); return true; }), false);
  });
  controller.connect({ operatorInitiated: true }); await settle();
  assert.deepEqual(calls, ['first', 'error']);
  assert.equal(controller.clientConnected, false);
});
