'use strict';
// Static (GitHub Pages) backend for the simulated display: the same Python compiler,
// verifier, explanations and notebook run in the browser with Pyodide. No MCP server,
// no language model: only Guided requests, labelled as such.
(function () {
  const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
  let engine = null;
  const loadScript = src => new Promise((resolve, reject) => {
    const s = document.createElement('script'); s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('Could not load ' + src));
    document.head.append(s);
  });
  // This script runs before the page is parsed, so the latest messages are repainted once it is.
  // The note is the same message shown on the display itself, where a first-time visitor is looking.
  const shown = {status: '', note: ''};
  function paint() {
    const bar = document.getElementById('engine-status'); if (bar) bar.textContent = shown.status;
    const screen = document.getElementById('boot-note'); if (screen) { screen.textContent = shown.note; screen.hidden = !shown.note; }
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
    status(`Engine ready · Show Your Work ${version} running in your browser (Pyodide ${py.version})`);
    engine = {py, call: py.globals.get('call'), scenarios, card, version};
    note('');
    return engine;
  }
  const booting = boot().catch(error => { status('Engine failed to load: ' + error.message); note('The engine could not load: ' + error.message + '. Reload the page to try again.'); throw error; });
  async function callTool(name, args) {
    const e = await booting;
    return JSON.parse(e.call(name, JSON.stringify(args || {})));
  }
  window.SYW_BACKEND = async function (path, body) {
    const e = await booting;
    if (path === '/api/config') {
      return {version: e.version, static: true, connected: true, protocol: 'in-browser', server: {name: 'Show Your Work (in-browser engine)', version: e.version},
              tools: ['solve_equations', 'check_answer', 'check_work', 'practice_problem', 'verify_certificate', 'progress_report', 'notebook_history', 'forgery_check']
                .map(name => ({name, ui: true, visibility: name === 'forgery_check' ? ['app'] : ['model', 'app']})),
              notebook: 'rivera family', learner: 'Maya', provider: null, providers: [], scenarios: e.scenarios.map(({id, label, utterance}) => ({id, label, utterance}))};
    }
    if (path.startsWith('/api/resource')) return {uri: 'ui://show-your-work/certificate-card.html', mimeType: 'text/html;profile=mcp-app', text: e.card};
    if (path === '/api/tool') return callTool(body.name, body.arguments);
    if (path === '/api/reset') return {ok: true};
    if (path === '/api/turn') return {reply: 'This web demo runs Guided requests only. To ask in your own words (typed or spoken) with an AI agent, or to use the MCP server, run the simulator locally.', events: [{type: 'notice', text: 'Static demo: no language model'}], card: null};
    if (path === '/api/guided') {
      const scenario = e.scenarios.find(s => s.id === body.scenario);
      const started = performance.now();
      const result = await callTool(scenario.tool, scenario.arguments);
      const payload = result.structuredContent || {};
      const replay = payload.replay || {};
      const args = Object.assign({}, scenario.arguments, {notebook: 'rivera family', learner: 'Maya'});
      const reply = (payload.view || {}).spoken || 'No result.';
      return {guided: true, reply, card: {tool: scenario.tool, arguments: args, result, resourceUri: 'ui://show-your-work/certificate-card.html'},
              events: [{type: 'heard', text: scenario.utterance, guided: true},
                       {type: 'tool', name: scenario.tool, arguments: args, ms: Math.round(performance.now() - started), status: payload.status,
                        verdict: payload.verdict, certificate_verified: payload.certificate_verified,
                        checks: replay.checks_total ? `${replay.checks_passed}/${replay.checks_total}` : null,
                        certificate: ((payload.bundle || {}).certificate || {}).certificate_sha256, saved: Boolean(payload.saved)},
                       {type: 'reply', text: reply, guided: true}]};
    }
    throw new Error('Unknown path ' + path);
  };
})();
