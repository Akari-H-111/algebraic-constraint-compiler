// Record the demo scenes from the real running simulator (Node >= 22, no packages).
//
//   DEMO_MODE=guided|agent SIM=http://127.0.0.1:8787 WEB=http://127.0.0.1:8932 node tools/video/record.mjs <outdir>
//
// v3 scenes (see narration.v3.json). DEMO_MODE=guided is the dry run: every simulator turn uses a Guided
// request (no model, no TTS). DEMO_MODE=agent is the live take (Gemini agent turns; the photo scene attaches
// the real sample JPEG through the page's file input and checks the drawn focus box). ONLY=03-photo,05-typo
// re-records just those scenes and merges them into the existing scenes.json. NARR=<dir> (default
// <outdir>/narration) supplies real narration lengths so the card reveals follow the voice.
//
// Each scene becomes <outdir>/<scene>/NNNNN.jpg at FPS frames per second, plus
// scenes.json with measured durations. Clicks inside the MCP Apps card use a
// DevTools session attached to the card's cross-origin frame, so the card is
// driven exactly as a person would drive it.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const OUT = resolve(process.argv[2] || '.local-qa/video-frames');
const SIM = process.env.SIM || 'http://127.0.0.1:8787';
const MODE = process.env.DEMO_MODE || 'guided';
const FPS = Number(process.env.FPS || 10);
// PHOTO_LIVE_TRIES (default 3 = the first take plus two retries) lets a re-record skip live photo attempts already made and rejected.
const LIVE_TRIES = Number(process.env.PHOTO_LIVE_TRIES ?? 3);
const WEB = process.env.WEB || 'http://127.0.0.1:8932';
const NARRATION = JSON.parse(readFileSync(new URL('./' + (process.env.NARRATION || 'narration.v3.json'), import.meta.url), 'utf8')).scenes;
const NARR = resolve(process.env.NARR || join(OUT, 'narration'));
const TEMPO = Number(process.env.TEMPO || 1.15);
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(',')) : null;
const PHOTO = resolve('algebraic_compiler/web/alexa/samples/maya-homework.jpg');
const CARDS = pathToFileURL(resolve('tools/video/cards.html')).href;
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const W = Number(process.env.VW || 1536), H = Number(process.env.VH || 864), SCALE = 1920 / W; // CSS viewport; frames are 1920x1080
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
// Each scene records at least `minSeconds`, keeps recording until its actions finish,
// then `tail` more seconds, so slow model replies are never cut off.
let sceneStart = 0, speech = [], lastShot = null;
async function capture(dir, minSeconds, actions, tail = 2.5) {
  mkdirSync(dir, { recursive: true });
  let index = 0, done = !actions, doneAt = Date.now();
  const started = Date.now(); sceneStart = started;
  const loop = (async () => {
    for (;;) {
      if (done && index >= minSeconds * FPS && Date.now() - doneAt >= tail * 1000) break;
      const due = started + index * (1000 / FPS);
      const now = Date.now(); if (due > now) await sleep(due - now);
      // A navigation inside a scene (a retry reloading the page) can orphan a pending screenshot, so give each one a deadline
      // and repeat the last good frame rather than stalling the whole recording.
      const shot = await Promise.race([send('Page.captureScreenshot', { format: 'jpeg', quality: 90 }).catch(() => null), sleep(4000).then(() => null)]);
      if (shot) lastShot = shot.data;
      if (lastShot) writeFileSync(join(dir, String(index).padStart(5, '0') + '.jpg'), Buffer.from(lastShot, 'base64'));
      index++;
    }
  })();
  if (actions) { await actions(); done = true; doneAt = Date.now(); }
  await loop;
  return index / FPS;
}
async function typeInto(text, cps = 48) {
  await evaluate(`document.querySelector('#text').focus(); document.querySelector('#text').value = ''`);
  for (let i = 1; i <= text.length; i += 2) {
    await evaluate(`document.querySelector('#text').value = ${JSON.stringify(text.slice(0, i + 1))}`);
    await sleep(2000 / cps);
  }
}
let cuts = [];
// One simulator turn. guided: the Guided request `scenario` (no model). Otherwise the typed utterance goes to the
// live agent. voice: true = Alexa's reply is voiced (TTS); a number = the reply stays on screen unvoiced for that many
// seconds of the final cut; false = nothing reserved. Returns {text, submitted, replied, failed}. A reply that says the model was unreachable is cut from
// the edit (the caller decides whether to retry); Model latency beyond ~3.5 s becomes a jump cut.
async function turn(scenario, utterance, { guided = MODE !== 'agent', voice = true, provider = 'gemini' } = {}) {
  if (!guided) { await evaluate(`window.__syw.setProvider(${JSON.stringify(provider)})`); await sleep(600); }
  await typeInto(utterance);
  await sleep(250);
  const submitted = (Date.now() - sceneStart) / 1000;
  if (!guided) await evaluate(`document.querySelector('#talk').requestSubmit()`);
  else await evaluate(`document.querySelector('#text').value = ''; window.__syw.guided(${JSON.stringify(scenario)})`);
  await sleep(300);
  await waitIdle(120000);
  const replied = (Date.now() - sceneStart) / 1000;
  const text = await evaluate(`document.querySelector('#reply').textContent`);
  const failed = /isn't reachable|Something went wrong/.test(text);
  if (!failed) {
    if (replied - submitted > 2.5) cuts.push([submitted + 1.2, replied - 0.6]);
    if (voice === true && text) speech.push({ t: replied, text, provider: guided ? 'guided' : provider });
    else if (typeof voice === 'number' && text) speech.push({ t: replied, text, provider: guided ? 'guided' : provider, silent: true, seconds: voice });  // shown on screen, not voiced: only the time is reserved
  }
  return { text, submitted, replied, failed };
}
const sleepUntil = async t => { const d = sceneStart + t * 1000 - Date.now(); if (d > 0) await sleep(d); };
const mute = () => evaluate(`(() => { if (!window.__syw.state.muted) document.querySelector('#mute').click(); return true; })()`);
const wordCount = s => s.trim().split(/\s+/).length;
// Seconds of a narration clip in the final cut: the real wav when it exists, else the word estimate.
function narr(name, part = 'post') {
  const file = join(NARR, name + (part === 'pre' ? '.pre' : '') + '.wav');
  if (existsSync(file)) { const b = readFileSync(file); return (b.length - 44) / 2 / b.readUInt32LE(24) / TEMPO; }
  const entry = NARRATION[name]; const text = typeof entry === 'string' ? entry : (entry || {})[part];
  return text ? wordCount(text) / 2.55 : 0;
}
const replyEstimate = text => Math.max(1.5, wordCount(text) / 2.8);
async function smoothScroll(selector, top, card = false) {
  const js = `(() => { const e = ${selector === 'card' ? 'document.scrollingElement' : `document.querySelector(${JSON.stringify(selector)})`}; e.scrollTo({ top: ${top}, behavior: 'smooth' }); return e.scrollHeight; })()`;
  return card ? cardEval(js) : evaluate(js);
}
// Attach the real sample photo through the page's own file input (what a person's file picker does).
async function attachPhoto() {
  await send('DOM.enable');
  const { root } = await send('DOM.getDocument', { depth: 0 });
  const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: '#photo-input' });
  await send('DOM.setFileInputFiles', { files: [PHOTO], nodeId });
}
// Focus box drawn on the photo, as [l, t, r, b] on the 0-1000 scale (or null).
const drawnFocusBox = () => evaluate(`(() => { const b = document.querySelector('#photo-overlay .pbox.focus'); if (!b) return null;
  const p = s => parseFloat(s); return [p(b.style.left) * 10, p(b.style.top) * 10, (p(b.style.left) + p(b.style.width)) * 10, (p(b.style.top) + p(b.style.height)) * 10]; })()`);
function iou(a, b) {
  const w = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])), h = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const i = w * h, u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i;
  return u > 0 ? i / u : 0;
}
async function emphasize(selectorIndex) {
  await cardEval(`(() => { const s = document.createElement('style'); s.textContent = '@keyframes syw{50%{box-shadow:0 0 0 4px rgba(39,211,255,.55)}} .syw-em{animation:syw 1.2s ease-in-out 3;border-color:rgba(39,211,255,.8)!important}'; document.head.append(s);
    const boxes = document.querySelectorAll('.box'); if (boxes[${selectorIndex}]) boxes[${selectorIndex}].classList.add('syw-em'); return boxes.length; })()`);
}
const clickCard = label => cardEval(`(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(label)}); if (!b) return false; b.scrollIntoView({block:'center'}); b.click(); return true; })()`);

const utter = {
  photo: "Alexa, here's Maya's homework. Is it right?",
  tickets: 'Alexa, adult tickets cost $12 and child tickets cost $7. They sold 20 tickets for $300. How many child tickets?',
  practice: 'Alexa, give Maya a practice problem like that one.',
  progress: 'Alexa, how is Maya doing with math this week?',
};
const EXPECTED_BOX = [190, 330, 350, 360];  // line 2 ("3x = 25") on the 0-1000 scale of maya-homework.jpg
async function waitIdle(limit = 20000) { const t = Date.now(); while (Date.now() - t < limit) { if (!(await evaluate('window.__syw.state.busy'))) return; await sleep(150); } }

const ORDER = ['01-title', '02-why', '03-photo', '04-unclear', '05-typo', '06-forgery', '07-web', '08-claude', '09-memory', '10-architecture', '11-close'];
const scenes = [], takeLog = [];
const removedTotal = () => cuts.reduce((s, [a, b]) => s + (b - a > 0.2 ? b - a : 0), 0);
const adj = t => t - removedTotal();                         // scene time as it will be after the jump cuts
const atAdj = t => sleepUntil(t + removedTotal());            // wait until final-cut time t (cuts all precede the reply)
async function scene(name, { prep, min = 3, actions, tail = 2.5, mode = 'static', extra = {} }) {
  if (ONLY && !ONLY.has(name)) return;
  speech = []; cuts = [];
  if (prep) await prep();
  const entry = { name, mode };
  const measured = await capture(join(OUT, name), min, actions && (async () => { Object.assign(entry, (await actions()) || {}); }), tail);
  Object.assign(entry, { seconds: measured, speech, cuts }, extra);
  scenes.push(entry);
  console.log(`scene ${name} [${entry.mode}]: ${measured.toFixed(1)}s` + speech.map(x => ` | @${x.t.toFixed(1)}s ${x.text.slice(0, 60)}`).join(''));
}
// Cards: hide the stepwise parts, then reveal them on a schedule relative to the narration length.
const cardPrep = id => async () => { await goto(CARDS + '#' + id); await evaluate(`document.body.classList.add('reveal'); rv(0); true`); };
const reveals = steps => async () => { for (const [t, n] of steps) { await sleepUntil(t); await evaluate(`rv(${n})`); } };
const cardScene = (name, id, steps, extra) => scene(name, { prep: cardPrep(id), min: narr(name) + 0.75, actions: reveals(steps(narr(name))), tail: 0.3, ...extra });
async function freshSim() {
  await goto(SIM + '/');
  await evaluate(`document.querySelector('#clear').click(); true`); await mute();
}
async function ensureSim() { if (!(await evaluate('location.href')).startsWith(SIM)) await freshSim(); else await mute(); }
// After the last reply: find where the narration after it starts in the final cut and run `steps` over it.
async function postRoll(name, replies, steps = []) {
  let cursor = 0.3;
  if (narr(name, 'pre')) cursor += narr(name, 'pre') + 0.25;
  for (const r of replies) { cursor = Math.max(cursor, adj(r.replied) + 0.4) + (r.silent ? r.silent : replyEstimate(r.text)) + 0.3; }
  const post = narr(name, 'post');
  for (const [frac, fn] of steps) { await atAdj(cursor + frac * post); await fn(); }
  await atAdj(cursor + post + 0.45);
}
const scrollRow = (tag, block = 'start') => evaluate(`(() => { const b = document.querySelector('.backstage'); const li = [...document.querySelectorAll('#timeline li')].find(x => (x.querySelector('.tag')?.textContent || '').toLowerCase().includes(${JSON.stringify(tag.toLowerCase())}));
  if (!li) return false; const top = li.getBoundingClientRect().top - b.getBoundingClientRect().top + b.scrollTop - 70; b.scrollTo({ top: ${block === 'end' ? 'top + li.offsetHeight - b.clientHeight + 100' : 'top'}, behavior: 'smooth' }); return true; })()`);
const timelineTop = () => evaluate(`document.querySelector('.backstage').scrollTo({top: 0, behavior: 'smooth'})`);
const timelineEnd = () => evaluate(`(() => { const b = document.querySelector('.backstage'); b.scrollTo({top: b.scrollHeight, behavior: 'smooth'}); })()`);
const cardTo = y => () => smoothScroll('card', y, true).catch(() => {});

mkdirSync(OUT, { recursive: true });

await scene('01-title', { prep: async () => { await goto(CARDS + '#title'); await evaluate(`document.body.classList.remove('reveal'); true`); }, min: 2.6, tail: 0 });
await cardScene('02-why', 'why', D => [[0.4, 1], [0.3 + 0.32 * D, 2], [0.3 + 0.86 * D, 3]]);

// 03: the photo check, a live agent turn (guided in the dry run). The focus box is checked against line 2.
await scene('03-photo', {
  prep: freshSim, min: 6, mode: MODE === 'agent' ? 'live' : 'guided', tail: 0.4,
  actions: async () => {
    const pre = narr('03-photo', 'pre');
    await sleep(1000);
    let last = null, live = MODE === 'agent', attempts = [];
    const attempt = async guided => {
      const t0 = (Date.now() - sceneStart) / 1000, before = cuts.length, speechBefore = speech.length;
      if (!guided) { await attachPhoto(); await sleep(2000); await atAdj(Math.max(adj(t0) + 2.2, pre - 3.2)); }
      else await atAdj(Math.max(1.2, pre - 3.2));
      const r = await turn('photo', utter.photo, { guided, voice: false });
      await sleep(900);
      const box = await drawnFocusBox(), score = box ? iou(box, EXPECTED_BOX) : 0;
      const ok = guided ? Boolean(box) : (!r.failed && score >= 0.4);
      attempts.push({ guided, box, iou: Number(score.toFixed(3)), ok, failed: r.failed, reply: r.text.slice(0, 160) });
      console.log(`  photo attempt ${attempts.length}${guided ? ' (guided)' : ''}: box=${JSON.stringify(box)} iou=${score.toFixed(2)} ${ok ? 'ok' : 'REJECTED'}`);
      if (ok) { last = r; speech.push({ t: r.replied, text: r.text, provider: guided ? 'guided' : 'gemini' }); return true; }
      cuts.length = before; speech.length = speechBefore;
      await goto(SIM + '/'); await evaluate(`document.querySelector('#clear').click(); true`); await mute();
      cuts.push([t0, (Date.now() - sceneStart) / 1000 + 0.2]);
      return false;
    };
    if (MODE === 'agent') { for (let i = 0; i < LIVE_TRIES && !last; i++) await attempt(false); }
    if (!last) { live = false; await attempt(true); }
    takeLog.push({ scene: '03-photo', attempts, note: process.env.PHOTO_NOTE || null });
    await postRoll('03-photo', [last], [[0.0, timelineTop], [0.35, cardTo(300)], [0.62, timelineEnd], [0.82, cardTo(0)]]);
    return { mode: live ? 'live' : 'guided', note: live ? 'live Gemini agent turn with the attached photo' : 'guided photo (recorded reading), labelled in the timeline' };
  },
});

// 04: a line the reader cannot read: the guided photo-review request (its reading is labelled as written for the demo).
await scene('04-unclear', {
  prep: ensureSim, min: 6, mode: 'guided', tail: 0.4,
  actions: async () => {
    const pre = narr('04-unclear', 'pre');
    await sleep(600);
    await atAdj(Math.max(0.6, pre - 3.2));
    const r = await turn('photo-review', "Alexa, here is Maya's homework page. I can't tell if her second line ends in a 5 or an S.", { guided: true, voice: 1.8 });
    r.silent = 1.8;
    await postRoll('04-unclear', [r], [[0.0, timelineTop], [0.5, timelineEnd]]);
  },
});

// 05: a worksheet that cannot be right as written: live agent turn (guided in the dry run).
await scene('05-typo', {
  prep: freshSim, min: 6, mode: MODE === 'agent' ? 'live' : 'guided', tail: 0.4,  // fresh page: an empty timeline, so only this turn's rows show
  actions: async () => {
    const pre = narr('05-typo', 'pre');
    await sleep(600);
    await atAdj(Math.max(0.6, pre - 3.5));
    let r = null, tries = 0, before = cuts.length;
    for (; tries < 2; tries++) {
      r = await turn('tickets', utter.tickets, { voice: 1.8 });
      if (!r.failed) break;
      console.log('  model unavailable; retrying'); await sleep(4000);
    }
    r.silent = 1.8;
    takeLog.push({ scene: '05-typo', tries: tries + 1, reply: r.text.slice(0, 200) });
    await postRoll('05-typo', [r], [[0.0, cardTo(0)], [0.4, cardTo(260)], [0.8, cardTo(520)]]);
  },
});

// 06: replay the verifier, then try a forgery, inside the MCP Apps card of the ticket result.
await scene('06-forgery', {
  min: narr('06-forgery') + 0.75, tail: 0.3, mode: 'live-card',
  actions: async () => {
    const D = narr('06-forgery'), clicks = {};
    await sleepUntil(0.35 * D); clicks.replay = await clickCard('Replay verifier');
    await sleepUntil(0.6 * D); clicks.forgery = await clickCard('Try a forgery');
    console.log('  card clicks', JSON.stringify(clicks));
    takeLog.push({ scene: '06-forgery', clicks });
    await sleepUntil(D + 0.6);
  },
});

// 07: the public static web demo, a real MCP client of the hosted server.
await scene('07-web', {
  prep: async () => {
    await goto(WEB + '/?t=' + Date.now());
    for (let i = 0; i < 400; i++) {  // wake the hosted server and load the in-browser verifier
      const s = await evaluate(`JSON.stringify([document.querySelector('#mcp-status')?.textContent || '', document.querySelector('#engine-status')?.textContent || ''])`);
      const [mcp, engine] = JSON.parse(s);
      if (/MCP connected/.test(mcp) && /ready/i.test(engine)) break;
      if (i === 399) throw new Error('web demo not ready: ' + s);
      await sleep(500);
    }
    await mute(); await evaluate(`document.querySelector('#clear').click(); true`);
  },
  min: narr('07-web') + 0.75, tail: 0.3, mode: 'guided-web',
  actions: async () => {
    const D = narr('07-web');
    await sleepUntil(1.2);
    await evaluate(`window.__syw.guided('photo')`); await sleep(300); await waitIdle(60000);
    const rows = {};
    await sleepUntil(0.36 * D); rows.session = await scrollRow('MCP session');
    await sleepUntil(0.56 * D); rows.call = await scrollRow('MCP tools/call');  // session, call, POST line and the browser replay row fit together; held to the end
    console.log('  web rows', JSON.stringify(rows));
    takeLog.push({ scene: '07-web', rows, status: await evaluate(`document.querySelector('#mcp-status')?.textContent`) });
    await sleepUntil(D + 0.6);
  },
});
await cardScene('08-claude', 'claude', D => [[0.4, 1], [0.3 + 0.38 * D, 2], [0.3 + 0.74 * D, 3]]);

// 09: practice problem, then weekly progress (live agent turns; guided in the dry run).
await scene('09-memory', {
  prep: freshSim, min: 8, mode: MODE === 'agent' ? 'live' : 'guided', tail: 0.4,
  actions: async () => {
    const pre = narr('09-memory', 'pre');
    await sleep(500);
    await atAdj(Math.max(0.5, pre - 3.0));
    const first = await turn('practice', utter.practice, { voice: 1.8 });
    first.silent = 1.8;
    takeLog.push({ scene: '09-memory', practice: first.text.slice(0, 200) });
    const startFirst = Math.max(0.3 + pre + 0.25, adj(first.replied) + 0.4);
    await atAdj(startFirst + first.silent + 0.5);
    const second = await turn('progress', utter.progress, { voice: 1.8 });
    second.silent = 1.8;
    takeLog.push({ scene: '09-memory', progress: second.text.slice(0, 200) });
    await postRoll('09-memory', [first, second], [[0.0, timelineEnd]]);
  },
});
await cardScene('10-architecture', 'arch', D => [[0.3, 1], [0.3 + 0.12 * D, 2], [0.3 + 0.42 * D, 3], [0.3 + 0.64 * D, 4], [0.3 + 0.84 * D, 5]]);
await scene('11-close', { prep: async () => { await goto(CARDS + '#close'); await evaluate(`document.body.classList.remove('reveal'); true`); }, min: narr('11-close') + 0.75, tail: 0 });

let all = scenes;
const metaPath = join(OUT, 'scenes.json');
if (ONLY && existsSync(metaPath)) {  // merge the re-recorded scenes into the earlier take
  const old = JSON.parse(readFileSync(metaPath, 'utf8')).scenes.filter(s => !scenes.some(n => n.name === s.name));
  all = [...old, ...scenes];
}
all.sort((a, b) => ORDER.indexOf(a.name) - ORDER.indexOf(b.name));
writeFileSync(metaPath, JSON.stringify({ fps: FPS, mode: MODE, scenes: all }, null, 1));
writeFileSync(join(OUT, 'take-log.json'), JSON.stringify(takeLog, null, 1));
ws.close(); chrome.kill('SIGTERM'); await sleep(300);
try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
console.log('recorded', OUT);
