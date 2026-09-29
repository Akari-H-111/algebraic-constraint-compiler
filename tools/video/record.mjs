// Record the demo scenes from the real running simulator (Node >= 22, no packages).
//
//   DEMO_MODE=guided|agent SIM=http://127.0.0.1:8787 node tools/video/record.mjs <outdir>
//
// Each scene becomes <outdir>/<scene>/NNNNN.jpg at FPS frames per second, plus
// scenes.json with measured durations. Clicks inside the MCP Apps card use a
// DevTools session attached to the card's cross-origin frame, so the card is
// driven exactly as a person would drive it.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const OUT = resolve(process.argv[2] || '.local-qa/video-frames');
const SIM = process.env.SIM || 'http://127.0.0.1:8787';
const MODE = process.env.DEMO_MODE || 'guided';
const FPS = Number(process.env.FPS || 10);
const CARDS = pathToFileURL(resolve('tools/video/cards.html')).href;
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const W = Number(process.env.VW || 1440), H = Number(process.env.VH || 810), SCALE = 1920 / W; // CSS viewport; frames are 1920x1080
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- Chrome + CDP ----------
const port = 9400 + Math.floor(Math.random() * 400);
const profile = mkdtempSync(join(tmpdir(), 'syw-rec-'));
const chrome = spawn(CHROME, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--headless=new',
  `--window-size=${W},${H}`, '--hide-scrollbars', '--mute-audio', '--no-first-run', '--force-device-scale-factor=1',
  '--site-per-process', 'about:blank'], { stdio: 'ignore' });
let wsUrl;
for (let i = 0; i < 100 && !wsUrl; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page')?.webSocketDebuggerUrl; } catch { /* starting */ }
  if (!wsUrl) await sleep(100);
}
const ws = new WebSocket(wsUrl);
await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = fail; });
let nextId = 0; const waiting = new Map();
const frames = new Map(); // sessionId -> {url, contexts: Map(id -> ctx)}
function send(method, params = {}, sessionId) {
  const id = ++nextId;
  ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
  return new Promise((ok, fail) => waiting.set(id, { ok, fail, method }));
}
ws.onmessage = event => {
  const msg = JSON.parse(event.data);
  if (msg.id) { const w = waiting.get(msg.id); if (w) { waiting.delete(msg.id); msg.error ? w.fail(new Error(w.method + ': ' + msg.error.message)) : w.ok(msg.result); } return; }
  const p = msg.params || {};
  if (msg.method === 'Target.attachedToTarget') {
    frames.set(p.sessionId, { url: p.targetInfo.url, contexts: new Map(), at: Date.now() });
    send('Runtime.enable', {}, p.sessionId).catch(() => {});
    send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true }, p.sessionId).catch(() => {});
  } else if (msg.method === 'Target.detachedFromTarget') {
    frames.delete(p.sessionId);
  } else if (msg.method === 'Runtime.executionContextCreated' && msg.sessionId && frames.has(msg.sessionId)) {
    const c = p.context; frames.get(msg.sessionId).contexts.set(c.id, { id: c.id, origin: c.origin, frameId: c.auxData?.frameId, isDefault: c.auxData?.isDefault });
  } else if (msg.method === 'Runtime.executionContextDestroyed' && msg.sessionId && frames.has(msg.sessionId)) {
    frames.get(msg.sessionId).contexts.delete(p.executionContextId);
  }
};
await send('Page.enable'); await send('Runtime.enable');
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: SCALE, mobile: false });

async function evaluate(expression) {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
async function cardEval(expression) {
  for (let attempt = 0; attempt < 40; attempt++) {
    // The card is an opaque-origin srcdoc frame; with site isolation it is its own target.
    const candidates = [...frames.entries()].sort((a, b) => b[1].at - a[1].at);
    for (const [sessionId, frame] of candidates) {
      const card = [...frame.contexts.values()].find(c => c.isDefault && (c.origin === 'null' || c.origin === '' || c.origin === '://'));
      if (!card) continue;
      const r = await send('Runtime.evaluate', { expression, contextId: card.id, awaitPromise: true, returnByValue: true }, sessionId);
      if (!r.exceptionDetails) return r.result.value;
      break;
    }
    await sleep(100);
  }
  const seen = [...frames.values()].map(f => ({ url: f.url, contexts: [...f.contexts.values()] }));
  throw new Error('card frame not found; frames=' + JSON.stringify(seen));
}
async function goto(url) { await send('Page.navigate', { url }); await sleep(900); }

// ---------- recording ----------
let recording = null;
async function capture(dir, seconds, actions) {
  mkdirSync(dir, { recursive: true });
  let index = 0, stop = false;
  const started = Date.now(), total = Math.round(seconds * FPS);
  const loop = (async () => {
    while (!stop && index < total) {
      const due = started + index * (1000 / FPS);
      const now = Date.now(); if (due > now) await sleep(due - now);
      const { data } = await send('Page.captureScreenshot', { format: 'jpeg', quality: 90 });
      writeFileSync(join(dir, String(index).padStart(5, '0') + '.jpg'), Buffer.from(data, 'base64'));
      index++;
    }
  })();
  if (actions) await actions();
  await loop; stop = true;
  return index / FPS;
}
async function typeInto(text, cps = 32) {
  await evaluate(`document.querySelector('#text').focus(); document.querySelector('#text').value = ''`);
  for (let i = 1; i <= text.length; i += 2) {
    await evaluate(`document.querySelector('#text').value = ${JSON.stringify(text.slice(0, i + 1))}`);
    await sleep(2000 / cps);
  }
}
async function ask(scenario, utterance) {
  await typeInto(utterance);
  await sleep(250);
  if (MODE === 'agent') await evaluate(`document.querySelector('#talk').requestSubmit()`);
  else await evaluate(`document.querySelector('#text').value = ''; window.__syw.guided(${JSON.stringify(scenario)})`);
}
async function emphasize(selectorIndex) {
  await cardEval(`(() => { const s = document.createElement('style'); s.textContent = '@keyframes syw{50%{box-shadow:0 0 0 4px rgba(39,211,255,.55)}} .syw-em{animation:syw 1.2s ease-in-out 3;border-color:rgba(39,211,255,.8)!important}'; document.head.append(s);
    const boxes = document.querySelectorAll('.box'); if (boxes[${selectorIndex}]) boxes[${selectorIndex}].classList.add('syw-em'); return boxes.length; })()`);
}
const clickCard = label => cardEval(`(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(label)}); if (!b) return false; b.scrollIntoView({block:'center'}); b.click(); return true; })()`);

const utter = {
  work: "Alexa, check Maya's homework. The problem is 3x + 5 = 20. She wrote 3x = 25, then x = 25 over 3.",
  tickets: 'Alexa, adult tickets cost $12 and child tickets cost $7. They sold 20 tickets for $300. How many child tickets?',
  answer: 'Alexa, Maya says x equals 4 for 2x - 7 = 1. Is that right?',
  practice: 'Alexa, give Maya a practice problem like that one.',
  progress: 'Alexa, how is Maya doing with math this week?',
};
const waitIdle = async (limit = 20000) => { const t = Date.now(); while (Date.now() - t < limit) { if (!(await evaluate('window.__syw.state.busy'))) return; await sleep(150); } };

const scenes = [];
async function scene(name, seconds, actions) {
  const measured = await capture(join(OUT, name), seconds, actions);
  scenes.push({ name, seconds: measured });
  console.log(`scene ${name}: ${measured.toFixed(1)}s`);
}

mkdirSync(OUT, { recursive: true });
await goto(CARDS + '#title');
await scene('01-title', 4.5);
await evaluate(`location.hash = 'why'`);
await scene('02-why', 12.5);

await goto(SIM + '/');
await evaluate(`document.querySelector('#mute').click(); document.querySelector('#clear').click(); true`);
await scene('03-homework', 38, async () => {
  await sleep(1200); await ask('work', utter.work); await waitIdle(); await sleep(800);
});
await scene('04-translation', 23, async () => {
  await sleep(1500); await emphasize(0); await sleep(9000); await emphasize(1);
});
await scene('05-typo', 22, async () => {
  await sleep(600); await ask('tickets', utter.tickets); await waitIdle();
});
await scene('06-forgery', 22, async () => {
  await sleep(2500); await clickCard('Replay verifier'); await sleep(6500); await clickCard('Try a forgery');
});
await scene('07-memory', 23, async () => {
  await sleep(300); await ask('answer', utter.answer); await waitIdle(); await sleep(2600);
  await ask('practice', utter.practice); await waitIdle(); await sleep(2600);
  await ask('progress', utter.progress); await waitIdle();
});
await goto(CARDS + '#arch');
await scene('08-architecture', 14.5);
await evaluate(`location.hash = 'close'`);
await scene('09-close', 6);

writeFileSync(join(OUT, 'scenes.json'), JSON.stringify({ fps: FPS, mode: MODE, scenes }, null, 1));
ws.close(); chrome.kill('SIGTERM'); await sleep(300);
try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
console.log('recorded', OUT);
