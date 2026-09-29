'use strict';
// Simulated Alexa+ smart display. Also a minimal MCP Apps host (SEP-1865):
// renders the server's ui:// card through a cross-origin sandbox proxy.
(function () {
  const $ = id => document.getElementById(id);
  const session = (() => { try { return sessionStorage.getItem('syw-session') || (sessionStorage.setItem('syw-session', crypto.randomUUID()), sessionStorage.getItem('syw-session')); } catch (e) { return 'default'; } })();
  const state = {config: null, busy: false, muted: false, voice: null, recognition: null, listening: false, host: null, provider: null};
  const MODEL_NAMES = {anthropic: 'Claude', gemini: 'Gemini', bedrock: 'Amazon Nova', openai: 'OpenAI-compatible', scripted: 'Scripted'};

  // ---------- helpers ----------
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  async function api(path, body) {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : undefined);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setState(name) { $('screen').className = 'screen' + (name ? ' state-' + name : ''); }
  function tick() {
    const now = new Date();
    $('clock').textContent = now.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
    const h = now.getHours();
    $('greeting').textContent = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  }

  // ---------- speech ----------
  function pickVoice() {
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    const preferred = ['Google US English', 'Microsoft Aria Online (Natural) - English (United States)', 'Microsoft Jenny Online (Natural) - English (United States)', 'Ava', 'Allison', 'Susan', 'Karen', 'Samantha'];
    state.voice = preferred.map(n => voices.find(v => v.name === n || v.name.startsWith(n + ' '))).find(Boolean)
      || voices.find(v => v.lang === 'en-US') || null;
  }
  function speak(text) {
    return new Promise(resolve => {
      if (state.muted || !window.speechSynthesis || !text) return resolve();
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      if (state.voice) u.voice = state.voice;
      u.rate = 1.03; u.pitch = 1.0;
      u.onend = u.onerror = () => { setState(''); resolve(); };
      setState('speaking');
      speechSynthesis.speak(u);
    });
  }
  function setupRecognition() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) { $('mic').disabled = true; $('mic').title = 'Speech recognition is not available in this browser; type instead.'; return; }
    const rec = new Rec();
    rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false;
    let finalText = '';
    rec.onresult = e => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
      }
      showHeard(finalText + interim);
    };
    rec.onend = () => { state.listening = false; $('mic').classList.remove('active'); if (finalText.trim()) submit(finalText.trim()); else setState(''); finalText = ''; };
    rec.onerror = () => { state.listening = false; $('mic').classList.remove('active'); setState(''); };
    state.recognition = rec;
  }
  function listen(start) {
    if (!state.recognition || state.busy) return;
    if (start && !state.listening) {
      if (window.speechSynthesis) speechSynthesis.cancel();
      state.listening = true; $('mic').classList.add('active'); setState('listening');
      try { state.recognition.start(); } catch (e) { state.listening = false; }
    } else if (!start && state.listening) {
      state.recognition.stop();
    }
  }

  // ---------- screen ----------
  function showHeard(text) {
    $('idle').hidden = true; $('conversation').hidden = false;
    const said = text.replace(/^\s*alexa[,!.]?\s*/i, '');
    $('heard').textContent = said ? said.charAt(0).toUpperCase() + said.slice(1) : '…';
  }
  function showReply(text) { $('reply').textContent = text || ''; }

  // ---------- timeline ----------
  let currentTurn = null;
  function logTurn(label) {
    const li = el('li', 'turn'); li.append(el('p', 'turn-label', label + ' · ' + new Date().toLocaleTimeString('en-US')));
    currentTurn = el('ol', 'turn-events'); li.append(currentTurn); $('timeline').prepend(li);
  }
  function logEvent(ev) {
    const li = el('li', 'ev-' + ev.type);
    const kind = el('div', 'kind');
    const detail = el('div', 'detail');
    const add = (tag, text) => { kind.append(el('span', 'tag', tag)); kind.append(document.createTextNode(text)); };
    if (ev.type === 'heard') { add(ev.guided ? 'guided' : 'heard', ev.guided ? 'Preset typed request (no AI)' : 'Speech → text'); detail.textContent = '“' + ev.text + '”'; }
    else if (ev.type === 'model') { add('AI interprets', `${MODEL_NAMES[ev.provider] || ev.provider} · ${ev.model}`); detail.textContent = ev.tool_calls.length ? 'Chose MCP tool: ' + ev.tool_calls.join(', ') : 'Composed the spoken reply'; }
    else if (ev.type === 'tool') {
      add('MCP tools/call', ev.name);
      if (ev.error) { detail.className += ' error'; detail.textContent = ev.error; }
      else {
        const args = Object.assign({}, ev.arguments); delete args.notebook; delete args.learner;
        const code = el('code', null, JSON.stringify(args));
        const verdict = el('div', ev.certificate_verified ? 'verified' : 'rejected',
          (ev.certificate_verified ? '✓ Compiler calculated · verifier certified' : '• ' + (ev.status || 'no claim')) +
          (ev.checks ? ` · ${ev.checks} independent checks` : '') + (ev.verdict ? ` · ${ev.verdict}` : '') + (ev.saved ? ' · saved to notebook' : '') +
          (ev.certificate ? ` · cert ${ev.certificate.slice(0, 10)}…` : ''));
        detail.append(code, verdict);
      }
    }
    else if (ev.type === 'reply') { add(ev.guided ? 'speech' : 'AI explains', ev.guided ? 'Verified suggested speech' : 'Spoken reply'); detail.textContent = ev.text; }
    else if (ev.type === 'app') { add('card → host', ev.name); detail.textContent = ev.text; }
    else { add('notice', ''); detail.className += ' error'; detail.textContent = ev.text; }
    if (ev.ms != null) kind.append(el('span', 'ms', ev.ms + ' ms'));
    li.append(kind, detail);
    if (!currentTurn) logTurn('Session');
    currentTurn.append(li);
  }

  // ---------- MCP Apps host ----------
  class AppHost {
    constructor(slot) { this.slot = slot; this.frame = null; this.card = null; this.resource = null; this.origin = sandboxOrigin(); window.addEventListener('message', e => this.onMessage(e)); }
    async show(card) {
      this.card = card;
      this.resource = await api('/api/resource?uri=' + encodeURIComponent(card.resourceUri));
      const frame = document.createElement('iframe');
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
      frame.setAttribute('title', 'Certificate card (MCP App)');
      frame.src = this.origin + '/sandbox.html';
      $('conversation').classList.add('compact');
      this.slot.replaceChildren(frame); this.slot.hidden = false;
      this.frame = frame;
      this.fit();
    }
    fit() {
      // Smart-display layout: the card gets a fixed box (the rest of the screen) and scrolls inside it.
      if (!this.frame) return;
      this.frame.style.height = Math.max(160, this.slot.clientHeight) + 'px';
    }
    post(message) { if (this.frame) this.frame.contentWindow.postMessage(message, this.origin); }
    reply(id, result) { this.post({jsonrpc: '2.0', id, result}); }
    fail(id, message, code) { this.post({jsonrpc: '2.0', id, error: {code: code || -32000, message}}); }
    context() {
      const width = Math.round(this.slot.clientWidth || 800);
      return {theme: 'dark', displayMode: 'inline', availableDisplayModes: ['inline'], platform: 'web', locale: 'en-US',
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone, userAgent: 'show-your-work-alexa-simulator',
        containerDimensions: {width, height: Math.max(160, Math.round(this.slot.clientHeight))}, deviceCapabilities: {touch: true, hover: false},
        toolInfo: {tool: {name: this.card.tool}},
        styles: {variables: {'--color-background-primary': '#111726', '--color-background-secondary': '#172036',
          '--color-text-primary': '#eef2fb', '--color-text-secondary': '#9aa6bf', '--color-border-primary': '#263049',
          '--font-sans': 'ui-sans-serif,-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif', '--border-radius-lg': '14px'}}};
    }
    async onMessage(event) {
      if (!this.frame || event.source !== this.frame.contentWindow || event.origin !== this.origin) return;
      const msg = event.data;
      if (!msg || msg.jsonrpc !== '2.0') return;
      const m = msg.method;
      if (m === 'ui/notifications/sandbox-proxy-ready') {
        const meta = (this.resource._meta && this.resource._meta.ui) || {};
        this.post({jsonrpc: '2.0', method: 'ui/notifications/sandbox-resource-ready', params: {html: this.resource.text, csp: meta.csp || {}, permissions: meta.permissions || {}}});
      } else if (m === 'ui/initialize') {
        this.reply(msg.id, {protocolVersion: '2026-01-26', hostInfo: {name: 'show-your-work-alexa-simulator', version: (state.config || {}).version || '0'},
          hostCapabilities: {serverTools: {}, logging: {}, openLinks: {}}, hostContext: this.context()});
      } else if (m === 'ui/notifications/initialized') {
        this.post({jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: {arguments: this.card.arguments}});
        this.post({jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: this.card.result});
      } else if (m === 'ui/notifications/size-changed') {
        // Fixed container: size hints are acknowledged by ignoring them.
      } else if (m === 'tools/call') {
        const name = msg.params && msg.params.name;
        const started = performance.now();
        try {
          const result = await api('/api/tool', {name, arguments: (msg.params && msg.params.arguments) || {}});
          const payload = result.structuredContent || {}; const rep = payload.replay || {};
          logEvent({type: 'app', name, ms: Math.round(performance.now() - started),
            text: (rep.certificate_verified ? '✓ verified ' : '✗ rejected ') + (rep.checks_total ? `(${rep.checks_passed}/${rep.checks_total} checks) ` : '') + (rep.message || '')});
          this.reply(msg.id, result);
        } catch (error) { logEvent({type: 'notice', text: 'Card tool call refused: ' + error.message}); this.fail(msg.id, error.message); }
      } else if (m === 'ping' || m === 'ui/update-model-context' || m === 'ui/resource-teardown') {
        if (msg.id !== undefined) this.reply(msg.id, {});
      } else if (m === 'ui/request-display-mode') {
        this.reply(msg.id, {mode: 'inline'});
      } else if (m === 'ui/open-link') {
        const url = msg.params && msg.params.url;
        if (/^https:\/\//.test(url || '')) { window.open(url, '_blank', 'noopener'); this.reply(msg.id, {}); } else this.fail(msg.id, 'Only https links');
      } else if (m === 'ui/message') {
        const text = msg.params && msg.params.content && msg.params.content.text;
        this.reply(msg.id, {}); if (text) submit(text);
      } else if (m === 'notifications/message') {
        logEvent({type: 'app', name: 'log', text: JSON.stringify(msg.params || {}).slice(0, 200)});
      } else if (msg.id !== undefined && m) {
        this.fail(msg.id, 'Method not supported: ' + m, -32601);
      }
    }
  }
  function sandboxOrigin() {
    const port = location.port ? ':' + location.port : '';
    const other = location.hostname === 'localhost' ? '127.0.0.1' : 'localhost';
    return location.protocol + '//' + other + port;
  }

  // ---------- turns ----------
  async function run(label, request) {
    if (state.busy) return;
    state.busy = true; setButtons();
    logTurn(label);
    setState('thinking'); showReply('');
    try {
      const data = await request();
      data.events.forEach(logEvent);
      showReply(data.reply);
      if (data.card) await state.host.show(data.card);
      await speak(data.reply);
    } catch (error) {
      showReply('Something went wrong: ' + error.message);
      logEvent({type: 'notice', text: error.message});
    } finally { state.busy = false; setState(''); setButtons(); }
  }
  function submit(text) {
    showHeard(text);
    const who = MODEL_NAMES[state.provider] || state.provider;
    run('Voice turn · ' + who, () => api('/api/turn', {session, text, provider: state.provider}));
  }
  function guided(scenario) {
    showHeard(scenario.utterance);
    run('Guided request', () => api('/api/guided', {scenario: scenario.id}));
  }
  function setButtons() {
    document.querySelectorAll('.scenarios button, .send').forEach(b => b.disabled = state.busy);
    $('mic').classList.toggle('busy', state.busy);
  }

  // ---------- config ----------
  async function loadConfig() {
    try {
      const cfg = await api('/api/config');
      state.config = cfg;
      $('household').textContent = cfg.notebook.replace(/\b\w/g, c => c.toUpperCase()) + ' · ' + cfg.learner;
      const mcp = $('mcp-pill');
      if (cfg.connected) { mcp.textContent = `MCP · ${cfg.protocol} · ${cfg.tools.length} tools`; mcp.className = 'pill ok'; }
      else { mcp.textContent = 'MCP · not connected'; mcp.className = 'pill warn'; }
      const model = $('model-pill'), sw = $('model-switch');
      state.provider = state.provider || cfg.provider;
      const paint = () => {
        const current = (cfg.providers || []).find(p => p.name === state.provider);
        if (current) { model.textContent = `AI · ${MODEL_NAMES[current.name] || current.name} · ${current.model}`; model.className = 'pill ok'; }
        else { model.textContent = 'AI · none (Guided mode)'; model.className = 'pill warn'; }
        sw.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.name === state.provider)));
      };
      sw.replaceChildren();
      (cfg.providers || []).forEach(p => { const b = el('button', null, MODEL_NAMES[p.name] || p.name); b.type = 'button'; b.dataset.name = p.name; b.setAttribute('role', 'radio'); b.onclick = () => { state.provider = p.name; paint(); }; sw.append(b); });
      sw.hidden = (cfg.providers || []).length < 2;
      paint();
      const note = $('mode-note');
      note.replaceChildren();
      if (cfg.provider) note.append('Talk freely, or try: ');
      else { const b = el('b', null, 'Guided mode: '); note.append(b, 'no AI model is configured, so these preset typed requests call the same MCP tools directly. Configure Bedrock, Claude or Gemini to talk freely.'); }
      const box = $('scenarios'); box.replaceChildren();
      cfg.scenarios.forEach(s => { const b = el('button', null, s.label); b.type = 'button'; b.title = s.utterance; b.onclick = () => cfg.provider ? submit(s.utterance) : guided(s); box.append(b); });
      const info = $('server-info'); info.replaceChildren();
      if (cfg.connected) {
        info.append(el('p', null, `${cfg.server.name} ${cfg.server.version} at `), el('code', null, cfg.mcp_url));
        const ul = el('ul');
        cfg.tools.forEach(t => ul.append(el('li', null, `${t.name}${t.ui ? ' · MCP Apps card' : ''}${t.visibility.join(',') === 'app' ? ' · app-only' : ''}`)));
        info.append(ul);
      } else info.append(el('p', null, cfg.error || 'Not connected'));
      if (cfg.provider_error) logEvent({type: 'notice', text: cfg.provider_error});
    } catch (error) { $('mcp-pill').textContent = 'Simulator error'; logEvent({type: 'notice', text: error.message}); }
  }

  // ---------- wiring ----------
  state.host = new AppHost($('card-slot'));
  $('talk').addEventListener('submit', e => { e.preventDefault(); const t = $('text').value.trim(); if (!t || state.busy) return; $('text').value = ''; if (state.config && state.config.provider) submit(t); else { showHeard(t); showReply('No AI model is configured. Pick a Guided request below.'); } });
  $('mic').addEventListener('pointerdown', () => listen(true));
  $('mic').addEventListener('pointerup', () => listen(false));
  $('mic').addEventListener('pointerleave', () => listen(false));
  document.addEventListener('keydown', e => { if (e.code === 'Space' && document.activeElement !== $('text') && !e.repeat) { e.preventDefault(); listen(true); } });
  document.addEventListener('keyup', e => { if (e.code === 'Space' && document.activeElement !== $('text')) { e.preventDefault(); listen(false); } });
  $('mute').addEventListener('click', () => { state.muted = !state.muted; $('mute').textContent = state.muted ? '🔇' : '🔊'; $('mute').setAttribute('aria-pressed', String(state.muted)); if (state.muted && window.speechSynthesis) speechSynthesis.cancel(); });
  $('clear').addEventListener('click', () => { $('timeline').replaceChildren(); currentTurn = null; api('/api/reset', {session}).catch(() => {}); });
  if (window.speechSynthesis) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
  setupRecognition();
  window.addEventListener('resize', () => { state.host.fit(); if (state.host.frame) state.host.post({jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: {containerDimensions: {width: Math.round($('card-slot').clientWidth), height: Math.round($('card-slot').clientHeight)}}}); });
  tick(); setInterval(tick, 15000);
  loadConfig();
  window.__syw = {submit, setProvider: name => { state.provider = name; const b = document.querySelector(`#model-switch button[data-name="${name}"]`); if (b) b.click(); }, guided: id => { const s = (state.config.scenarios || []).find(x => x.id === id); if (s) guided(s); }, state};
})();
