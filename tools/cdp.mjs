// Minimal Chrome DevTools Protocol driver (Node >= 22, no packages).
// Launches a throwaway headless Chrome profile, runs a scripted demo, and saves
// PNG screenshots or a frame sequence. Used for visual QA and demo recording.
//
//   node tools/cdp.mjs <plan.json>
//
// plan.json: {"url": "...", "width": 1920, "height": 1080, "scale": 1, "out": "dir",
//             "steps": [{"eval": "js"}, {"wait": 500}, {"shot": "name.png"},
//                       {"shot": "part.png", "clipEval": "js returning {x, y, width, height} in CSS pixels"},
//                       {"record": 12, "seconds": 5, "prefix": "clip"}]}
// "scale" is the device pixel ratio (default 1); a clipped shot is cropped to that region of the page.
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const plan = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), 'syw-chrome-'));
const width = plan.width || 1920, height = plan.height || 1080;
mkdirSync(plan.out, { recursive: true });

const chrome = spawn(CHROME, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--headless=new',
  `--window-size=${width},${height}`, '--hide-scrollbars', '--mute-audio', '--no-first-run', '--no-default-browser-check',
  '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 100; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(100);
  }
  throw new Error('Chrome did not start');
}

const ws = new WebSocket(await target());
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0; const waiting = new Map(); const listeners = [];
ws.onmessage = event => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) { const { resolve, reject } = waiting.get(msg.id); waiting.delete(msg.id); msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result); }
  else listeners.forEach(fn => fn(msg));
};
const send = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; waiting.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params })); });
const logs = [];
listeners.push(msg => { if (msg.method === 'Runtime.consoleAPICalled') logs.push(msg.params.args.map(a => a.value ?? a.description).join(' ')); if (msg.method === 'Runtime.exceptionThrown') logs.push('EXCEPTION ' + msg.params.exceptionDetails.text + ' ' + (msg.params.exceptionDetails.exception?.description || '')); });

await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: plan.scale || 1, mobile: false });
await send('Page.navigate', { url: plan.url });
await sleep(plan.settle ?? 1500);

let frameIndex = 0;
async function shot(path, clip) {
  const params = { format: 'png', captureBeyondViewport: false };
  if (clip) params.clip = { x: clip.x, y: clip.y, width: clip.width, height: clip.height, scale: 1 };
  const { data } = await send('Page.captureScreenshot', params);
  writeFileSync(path, Buffer.from(data, 'base64'));
}
for (const step of plan.steps) {
  if (step.eval) {
    const result = await send('Runtime.evaluate', { expression: step.eval, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) logs.push('EVAL ERROR ' + JSON.stringify(result.exceptionDetails.exception?.description || result.exceptionDetails.text));
    else if (result.result && result.result.value !== undefined && step.log) logs.push(String(result.result.value));
  }
  if (step.wait) await sleep(step.wait);
  if (step.shot) {
    let clip = null;
    if (step.clipEval) {
      const found = await send('Runtime.evaluate', { expression: step.clipEval, awaitPromise: true, returnByValue: true });
      clip = found.result && found.result.value;
      if (!clip) logs.push('CLIP ERROR ' + step.shot + ': ' + JSON.stringify(found.exceptionDetails || found.result));
    }
    await shot(join(plan.out, step.shot), clip);
  }
  if (step.record) {
    // Fixed-rate capture: frames are named sequentially for ffmpeg (-framerate step.record).
    const total = Math.round(step.record * step.seconds), interval = 1000 / step.record;
    const started = Date.now();
    for (let i = 0; i < total; i++) {
      await shot(join(plan.out, `${step.prefix || 'frame'}-${String(frameIndex++).padStart(5, '0')}.png`));
      const due = started + (i + 1) * interval; const now = Date.now(); if (due > now) await sleep(due - now);
    }
  }
}
writeFileSync(join(plan.out, 'console.log'), logs.join('\n'));
ws.close(); chrome.kill('SIGTERM');
await sleep(300);
try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
console.log(`done: ${plan.out} (${logs.length} console lines)`);
