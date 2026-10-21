'use strict';
// Static (GitHub Pages) backend for the simulated display. Two things start together on page load:
//  * the public Show Your Work MCP server, called from this page over Streamable HTTP (mcp-client.js); and
//  * the same Python compiler and independent verifier running in the browser with Pyodide, which replays
//    every certificate the server returns, and runs the request itself when the server cannot be reached.
// No language model: only Guided requests, labelled as such.
(function () {
  const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
  const CARD_URI = 'ui://show-your-work/certificate-card.html';
  const MCP = window.SYW_MCP;
  let engine = null;
  const loadScript = src => new Promise((resolve, reject) => {
    const s = document.createElement('script'); s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('Could not load ' + src));
    document.head.append(s);
  });
  const randomId = () => { try { return Array.from(crypto.getRandomValues(new Uint8Array(3)), b => b.toString(16).padStart(2, '0')).join(''); } catch (e) { return Math.random().toString(16).slice(2, 8); } };
  // The MCP connection. `notebook` is this page load's own notebook on the shared public server, so visitors
  // never see each other's saved results; the display still calls the household "Rivera family".
  const mcp = {state: 'connecting', url: '', custom: false, client: null, tools: [], card: null, error: '', degraded: '', steps: [],
               announced: false, started: performance.now(), notebook: 'rivera family ' + randomId(), pending: null};
  // This script runs before the page is parsed, so the latest messages are repainted once it is.
  // The note is the same message shown on the display itself, where a first-time visitor is looking.
  const shown = {status: '', note: ''};
  let configured = false;
  const $ = id => document.getElementById(id);
  const make = (tag, text) => { const n = document.createElement(tag); if (text != null) n.textContent = text; return n; };
  const short = text => text.length > 140 ? text.slice(0, 137) + '…' : text;
  function mcpStatus() {
    if (mcp.state === 'connected') return `MCP connected · Streamable HTTP ${mcp.client.protocol} · ${(mcp.client.serverInfo || {}).name || 'server'}${mcp.custom ? ' (local, ?mcp=)' : ''}${mcp.degraded ? ' · last call failed, used the in-browser engine' : ''}`;
    if (mcp.state === 'connecting') return `Waking the public MCP server (free tier, can take ~50 s)… ${Math.floor((performance.now() - mcp.started) / 1000)} s`;
    return 'Public MCP server unreachable · Guided requests run the same engine in your browser.';
  }
  // The top-bar pill, the server panel and the "Tap one" note are filled in by alexa.js from /api/config, once. This
  // backend repaints them (see /api/config) because the server's state changes after that: waking, connected, lost.
  function paintMcp() {
    const pill = $('mcp-pill');
    if (pill) {
      const text = mcp.state === 'connected' ? `MCP · ${mcp.client.protocol} · ${mcp.tools.length} tools${mcp.degraded ? ' · last call failed' : ''}`
        : mcp.state === 'connecting' ? 'MCP · waking server…' : 'MCP · unreachable · in-browser engine';
      pill.textContent = text; pill.className = 'pill' + (mcp.state === 'connected' && !mcp.degraded ? ' ok' : mcp.state === 'connecting' ? '' : ' warn');
    }
    if (!configured) return;
    const panel = $('server-info');
    if (panel) {
      panel.querySelectorAll('[data-mcp]').forEach(n => n.remove());
      const lead = make('p', mcp.state === 'connected' ? `${(mcp.client.serverInfo || {}).name} ${(mcp.client.serverInfo || {}).version || ''} · MCP ${mcp.client.protocol} over Streamable HTTP, called from this page` : mcp.state === 'failed' ? `${mcpStatus()} ${short(mcp.error)}` : mcpStatus());
      lead.dataset.mcp = '1';
      if (mcp.state === 'connected') {
        const where = make('p', 'Endpoint '); where.append(make('code', mcp.url)); where.dataset.mcp = '1';
        const list = make('ul'); list.dataset.mcp = '1';
        mcp.tools.forEach(t => { const m = MCP.uiMeta(t); list.append(make('li', `${t.name}${m.resourceUri ? ' · MCP Apps card' : ''}${m.visibility.join(',') === 'app' ? ' · app-only' : ''}`)); });
        panel.replaceChildren(lead, where, list);
      } else panel.prepend(lead);
    }
    const hint = $('mode-note');
    if (hint && mcp.state === 'connected') {
      hint.replaceChildren(make('b', 'Web demo: '), 'preset typed requests (no AI) call the public Show Your Work MCP server from this page, and your browser replays each certificate. Tap one:');
    }
  }
  function paint() {
    const bar = $('engine-status'); if (bar) bar.textContent = shown.status;
    const link = $('mcp-status'); if (link) link.textContent = mcpStatus();
    const screen = $('boot-note'); if (screen) { screen.textContent = shown.note; screen.hidden = !shown.note; }
    paintMcp();
  }
  function status(text) { shown.status = text; paint(); }
  function note(text) { shown.note = text; paint(); }
  document.addEventListener('DOMContentLoaded', paint);
  async function boot() {
    status('Loading the exact Python engine into your browser (first visit ≈ 6 MB)…');
    note('Starting the exact Python engine in your browser (first visit downloads about 6 MB)…');
    // Everything is requested at once: the engine's source files download while Pyodide itself does.
    const text = url => fetch(url).then(r => { if (!r.ok) throw new Error(url + ' → ' + r.status); return r.text(); });
    const manifestP = fetch('engine/manifest.json').then(r => r.json());
    const filesP = manifestP.then(m => Promise.all(m.files.map(file => text('engine/' + file).then(source => [file, source]))));
    const pyP = loadScript(PYODIDE + 'pyodide.js').then(() => loadPyodide({indexURL: PYODIDE, packages: ['sqlite3']}));
    const [scenarios, card, files, py] = await Promise.all([
      fetch('scenarios.json').then(r => r.json()), text('engine/algebraic_compiler/web/card.html'), filesP, pyP]);
    py.FS.mkdirTree('/engine/algebraic_compiler/web');
    for (const [file, source] of files) py.FS.writeFile('/engine/' + file, source);
    py.runPython(`
import json, os, sys
sys.path.insert(0, '/engine')
os.environ['SYW_NOTEBOOK_PATH'] = '/tmp/notebook.sqlite3'
from algebraic_compiler import __version__, service
DEFAULTS = {'notebook': 'rivera family', 'learner': 'Maya'}
TOOLS = {'solve_equations': service.solve, 'check_answer': service.check_answer, 'check_work': service.check_work,
         'practice_problem': service.practice, 'progress_report': service.progress, 'notebook_history': service.history,
         'verify_certificate': service.verify, 'forgery_check': service.forge}
NAMES = {'solve_equations': ('equations', 'domain', 'labels', 'question', 'notebook', 'learner'),
         'check_answer': ('equations', 'answer', 'domain', 'labels', 'question', 'notebook', 'learner'),
         'check_work': ('steps', 'domain', 'labels', 'question', 'notebook', 'learner', 'provenance'),
         'practice_problem': ('kind', 'seed', 'notebook', 'learner'), 'progress_report': ('notebook', 'learner', 'days'),
         'notebook_history': ('notebook', 'learner', 'limit'), 'verify_certificate': ('bundle',), 'forgery_check': ('bundle',)}
def call(name, arguments_json):
    arguments = json.loads(arguments_json)
    for key in ('notebook', 'learner'):
        if key in NAMES[name] and not arguments.get(key):
            arguments[key] = DEFAULTS[key]
    kwargs = {k: arguments[k] for k in NAMES[name] if k in arguments}
    if name == 'practice_problem':
        kwargs['kind'] = kwargs.get('kind', 'two_step')
    payload = TOOLS[name](**kwargs)
    return json.dumps({'content': [{'type': 'text', 'text': service.summary_text(payload)}], 'structuredContent': payload})
`);
    const version = py.globals.get('__version__');
    status(`Browser verifier ready · Show Your Work ${version} (Pyodide ${py.version})`);
    engine = {py, call: py.globals.get('call'), scenarios, card, version};
    note('');
    return engine;
  }
  const booting = boot().catch(error => { status('Engine failed to load: ' + error.message); note('The engine could not load: ' + error.message + '. Reload the page to try again.'); throw error; });
  async function callTool(name, args) {
    const e = await booting;
    return JSON.parse(e.call(name, JSON.stringify(args || {})));
  }

  // ---------- the public MCP server ----------
  // initialize -> notifications/initialized -> tools/list -> resources/read. A free-tier server can need ~50 s to wake,
  // so this runs beside the engine download and the page never waits for it.
  function connectMcp() {
    if (mcp.pending) return mcp.pending;
    mcp.state = 'connecting'; mcp.started = performance.now(); mcp.error = ''; paint();
    const ticker = setInterval(paint, 1000);
    mcp.pending = (async () => {
      if (!MCP) throw new Error('mcp-client.js did not load');
      const target = MCP.endpointFor(location.search);
      mcp.url = target.url; mcp.custom = target.custom;
      const client = new MCP.McpClient(target.url, {timeout: 30000});
      await client.connect(target.custom ? 8000 : 110000);  // a local server answers at once; the public one may be asleep
      const tools = (await client.listTools()).result.tools || [];
      const wanted = (tools.map(t => MCP.uiMeta(t).resourceUri).find(Boolean)) || CARD_URI;
      const card = (await client.readResource(wanted)).result.contents[0];
      mcp.client = client; mcp.tools = tools; mcp.steps = client.trace.filter(t => t.ok);
      mcp.card = card && typeof card.text === 'string' && /^text\/html/.test(card.mimeType || '') ? card : null;
      mcp.readyMs = Math.round(performance.now() - mcp.started); mcp.state = 'connected'; mcp.degraded = ''; mcp.announced = false;
    })().catch(error => { mcp.state = 'failed'; mcp.error = error.message; })
      .finally(() => { clearInterval(ticker); mcp.pending = null; paint(); });
    return mcp.pending;
  }
  // A call that fails because the server went away (a free instance sleeps after ~15 minutes idle) reconnects in the background.
  function lost(error) {
    mcp.degraded = error.message;
    if (error.kind === 'network' || error.kind === 'timeout') connectMcp();
    paint();
  }
  // Pings the public server while the page is open and visible, so it does not fall asleep under a reader.
  setInterval(() => {
    if (mcp.state === 'connected' && !mcp.custom && document.visibilityState === 'visible') fetch(mcp.url.replace(/\/mcp$/, '/healthz'), {mode: 'no-cors', cache: 'no-store'}).catch(() => {});
  }, 5 * 60 * 1000);

  const retryIfFailed = () => { if (mcp.state === 'failed') connectMcp(); };

  async function mcpCall(name, args, timeout) {
    const properties = ((mcp.tools.find(t => t.name === name) || {}).inputSchema || {}).properties || {};
    const sent = Object.assign({}, args);
    if (properties.notebook && !sent.notebook) sent.notebook = mcp.notebook;
    if (properties.learner && !sent.learner) sent.learner = 'Maya';
    const {result, ms} = await mcp.client.callTool(name, sent, timeout);
    mcp.degraded = '';
    return {result, ms, sent};
  }
  function sessionEvent() {
    const c = mcp.client, took = mcp.steps.map(s => `${s.method} ${s.ms.toLocaleString('en-US')} ms`).join(' · ');
    return {type: 'info', tag: 'MCP session', label: 'Connected to ' + mcp.url, tone: 'ok',
            text: `${took} · negotiated MCP ${c.protocol}, server “${(c.serverInfo || {}).name}”${mcp.readyMs > 5000 ? `; it took ${(mcp.readyMs / 1000).toFixed(1)} s to wake` : ''}. Saved results go to a demo notebook named “${mcp.notebook}” on that server.`};
  }
  // The server's certificate is replayed here by the Python verifier (Pyodide); the server's own say-so is not relied on.
  async function replayInBrowser(payload) {
    const bundle = payload.bundle;
    if (!bundle) return {ok: null, event: {type: 'info', tag: 'browser replay', label: 'No certificate in this reply to replay',
      text: 'This tool returns no proof bundle (a progress report re-verifies each saved certificate on the server).'}};
    const started = performance.now();
    try {
      const local = (await callTool('verify_certificate', {bundle})).structuredContent || {};
      const mine = local.replay || {}, theirs = payload.replay || {}, ms = Math.round(performance.now() - started);
      const agrees = mine.certificate_verified === true && local.claim === payload.claim && mine.checks_total === theirs.checks_total && mine.checks_passed === theirs.checks_passed;
      const cert = (bundle.certificate || {}).certificate_sha256;
      if (agrees) return {ok: true, event: {type: 'info', tag: 'browser replay', tone: 'ok', ms,
        label: `Your browser replayed the server’s certificate: ${mine.checks_passed}/${mine.checks_total} ✓`,
        text: `Independent Python verifier (Pyodide), not the server’s say-so. Same claim and check count as the server reported${cert ? ` · cert ${cert.slice(0, 10)}…` : ''}`}};
      return {ok: false, event: {type: 'info', tag: 'browser replay', tone: 'bad', ms,
        label: 'Your browser could NOT confirm the server’s certificate',
        text: mine.certificate_verified === true ? `The replay passed ${mine.checks_passed}/${mine.checks_total} checks but differs from the server’s report (${theirs.checks_passed}/${theirs.checks_total}, claim ${payload.claim}).`
          : `The independent verifier rejected it: ${mine.code || local.code || 'REJECTED'}${mine.message || local.message ? ' · ' + (mine.message || local.message) : ''}`}};
    } catch (error) {
      return {ok: false, event: {type: 'info', tag: 'browser replay', tone: 'bad', label: 'Your browser could not replay the certificate', text: error.message}};
    }
  }
  async function guidedOverMcp(scenario) {
    const {result, ms, sent} = await mcpCall(scenario.tool, scenario.arguments, 45000);
    const payload = result.structuredContent || {};
    const events = mcp.announced ? [] : [sessionEvent()];
    mcp.announced = true;
    const replay = await replayInBrowser(payload), rep = payload.replay || {};
    const refused = replay.ok === false;  // the browser's replay is the one that counts
    events.push({type: 'tool', transport: 'mcp', endpoint: mcp.url + ' · JSON-RPC tools/call', name: scenario.tool, arguments: sent, ms,
                 status: refused ? 'NOT VERIFIED IN THIS BROWSER' : payload.status, verdict: payload.verdict, certificate_verified: refused ? false : payload.certificate_verified,
                 checks: rep.checks_total ? `${rep.checks_passed}/${rep.checks_total}` : null, certificate: ((payload.bundle || {}).certificate || {}).certificate_sha256, saved: Boolean(payload.saved)}, replay.event);
    if (refused) return {events, reply: 'My browser could not confirm the server’s certificate, so nothing is verified and I will not say the answer.', card: null};
    const text = (result.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
    const uri = MCP.uiMeta(mcp.tools.find(t => t.name === scenario.tool)).resourceUri;
    return {events, reply: (payload.view || {}).spoken || text || 'No result.',
            card: uri ? {tool: scenario.tool, arguments: sent, result, resourceUri: uri} : null};
  }
  async function guidedInBrowser(scenario) {
    const started = performance.now();
    const result = await callTool(scenario.tool, scenario.arguments);
    const payload = result.structuredContent || {}, replay = payload.replay || {};
    const args = Object.assign({}, scenario.arguments, {notebook: 'rivera family', learner: 'Maya'});
    return {reply: (payload.view || {}).spoken || 'No result.', card: {tool: scenario.tool, arguments: args, result, resourceUri: CARD_URI},
            events: [{type: 'tool', name: scenario.tool, arguments: args, ms: Math.round(performance.now() - started), status: payload.status,
                      verdict: payload.verdict, certificate_verified: payload.certificate_verified,
                      checks: replay.checks_total ? `${replay.checks_passed}/${replay.checks_total}` : null,
                      certificate: ((payload.bundle || {}).certificate || {}).certificate_sha256, saved: Boolean(payload.saved)}]};
  }
  // Why the in-browser engine answered, said in the timeline instead of being implied.
  function fallbackEvent(reason, was, why) {
    const label = reason ? `MCP call failed — ran the same engine in your browser` : was === 'connecting'
      ? 'Public MCP server still waking up (free tier) — ran the same engine in your browser' : 'Public MCP server unreachable — ran the same engine in your browser';
    return {type: 'info', tag: 'MCP', label, tone: 'warn',
            text: reason || (was === 'connecting' ? 'It is a free instance and can take ~50 s to wake. The pill at the top turns green when the connection is open; ask again then.' : `${why}. Trying again in the background; the pill at the top turns green when it is open.`)};
  }
  // The card's own buttons (replay, forgery check) are tool calls too; they go over MCP when it is connected.
  // `hostEvents` rides on the result for the timeline and is not enumerable, so it is not sent on to the card.
  function withHostEvents(result, events) { return Object.defineProperty(result, 'hostEvents', {value: events, enumerable: false}); }
  async function cardTool(name, args) {
    let reason = null;
    const was = mcp.state, why = mcp.error;
    retryIfFailed();
    if (mcp.state === 'connected') {
      const tool = mcp.tools.find(t => t.name === name);
      if (!tool || MCP.uiMeta(tool).visibility.indexOf('app') < 0) throw new Error('Tool not callable from the app');
      try {
        const {result, ms} = await mcpCall(name, args, 45000);
        return withHostEvents(result, [{type: 'info', tag: 'MCP tools/call', label: name + ' (from the card)', tone: 'ok', ms,
                                        text: `Sent to ${mcp.url} over MCP; the card’s button is not a local function.`}]);
      } catch (error) { reason = error.message; lost(error); }
    }
    return withHostEvents(await callTool(name, args), [fallbackEvent(reason, was, why)]);
  }

  window.SYW_BACKEND = async function (path, body) {
    const e = await booting;
    if (path === '/api/config') {
      configured = true; setTimeout(paint, 0);  // after alexa.js has filled the pill, panel and note from this reply
      const connected = mcp.state === 'connected';
      return {version: e.version, static: true, connected: true, protocol: connected ? mcp.client.protocol : 'in-browser',
              mcp_url: connected ? mcp.url : undefined,
              server: connected ? mcp.client.serverInfo : {name: 'Show Your Work (in-browser engine)', version: e.version},
              tools: connected ? mcp.tools.map(t => { const m = MCP.uiMeta(t); return {name: t.name, ui: Boolean(m.resourceUri), visibility: m.visibility}; })
                : ['solve_equations', 'check_answer', 'check_work', 'practice_problem', 'verify_certificate', 'progress_report', 'notebook_history', 'forgery_check']
                  .map(name => ({name, ui: true, visibility: name === 'forgery_check' ? ['app'] : ['model', 'app']})),
              notebook: 'rivera family', learner: 'Maya', provider: null, providers: [], scenarios: e.scenarios.map(({id, label, utterance, photo, guided_only}) => ({id, label, utterance, photo, guided_only}))};
    }
    if (path.startsWith('/api/resource')) return mcp.state === 'connected' && mcp.card ? mcp.card : {uri: CARD_URI, mimeType: 'text/html;profile=mcp-app', text: e.card};
    if (path === '/api/tool') return cardTool(body.name, body.arguments);
    if (path === '/api/reset') return {ok: true};
    if (path === '/api/turn') return {reply: 'This web demo runs Guided requests only. To ask in your own words (typed or spoken) with an AI agent, run the simulator locally.', events: [{type: 'notice', text: 'Static demo: no language model'}], card: null};
    if (path === '/api/guided') {
      const scenario = e.scenarios.find(s => s.id === body.scenario);
      if (!scenario) throw new Error('Unknown scenario');
      let run = null, reason = null;
      const was = mcp.state, why = mcp.error;
      retryIfFailed();
      if (mcp.state === 'connected') {
        try { run = await guidedOverMcp(scenario); } catch (error) { reason = `${error.message}. ${error.kind === 'network' || error.kind === 'timeout' ? 'Reconnecting in the background.' : ''}`.trim(); lost(error); }
      }
      if (!run) run = Object.assign(await guidedInBrowser(scenario), {fallback: fallbackEvent(reason, was, why)});
      // alexa.js adds the photo and reading rows itself from photo.reading (recorded by a model, or written for the demo).
      const events = [{type: 'heard', text: scenario.utterance, guided: true}]
        .concat(run.fallback ? [run.fallback] : [], run.events, [{type: 'reply', text: run.reply, guided: true}]);
      const photo = scenario.photo ? {src: scenario.photo, sample: true, reading: scenario.reading || null} : null;
      return {guided: true, reply: run.reply, card: run.card, events, photo};
    }
    throw new Error('Unknown path ' + path);
  };
  connectMcp();
})();
