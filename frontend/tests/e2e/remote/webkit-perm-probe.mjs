import { webkit } from '@playwright/test';
const browser = await webkit.launch();
const context = await browser.newContext();
try { await context.grantPermissions(['local-network-access'], { origin: 'http://127.0.0.1:18790' }); console.log('granted local-network-access'); }
catch (error) { console.log(`grant refused: ${error.message.split('\n')[0]}`); }
const page = await context.newPage();
await page.route('*', (route) => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
await page.goto('http://127.0.0.1:18790/bare');
const report = await page.evaluate(() => new Promise((resolve) => {
  const events = [];
  const pc = new RTCPeerConnection();
  pc.createDataChannel('control', { ordered: true });
  pc.addEventListener('icecandidate', (e) => events.push(e.candidate ? `cand:${e.candidate.candidate.slice(0, 40)}` : 'end'));
  pc.addEventListener('icegatheringstatechange', () => events.push(`g=${pc.iceGatheringState}`));
  pc.createOffer().then((o) => pc.setLocalDescription(o)).catch((e) => resolve(`err ${e}`));
  setTimeout(() => resolve(events.join(' | ') || 'nothing'), 8000);
}));
console.log(report);
await browser.close();
