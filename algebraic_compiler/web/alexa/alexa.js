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
  // A static build (GitHub Pages) supplies window.SYW_BACKEND, which runs the same Python
  // compiler and verifier in the browser; otherwise the simulator server answers.
  const STATIC = typeof window.SYW_BACKEND === 'function';
  const PHONE = window.matchMedia('(max-width:700px)');
  async function api(path, body) {
    if (STATIC) return window.SYW_BACKEND(path, body);
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : undefined);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  // The state is shown as text as well as by the light bar, so it does not depend on motion.
  const STATE_TEXT = {listening: 'Listening…', thinking: 'Checking…', speaking: 'Speaking…'};
  let stateName = '';
  function setState(name) {
    stateName = name || '';
    $('screen').className = 'screen' + (name ? ' state-' + name : '');
    $('state-text').textContent = STATE_TEXT[name] || '';
  }
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
  // Spoken replies, in this order, and the timeline says which one was used:
  //  1. a pre-recorded Gemini TTS clip whose recorded text is exactly the text on the screen;
  //  2. live Gemini TTS, when the local simulator has a Gemini key;
  //  3. the browser's own voice.
  const VOICE_BASE = 'voice/';
  const AUDIO_START_MS = 8000;  // audio that has not begun to play by then is given up on, so a stalled file cannot hold the page
  let recorded = null, speaking = null, speechTurn = 0;
  // A failed fetch is not remembered, so the next reply tries the manifest again.
  const loadRecorded = () => recorded || (recorded = fetch(VOICE_BASE + 'manifest.json').then(r => r.ok ? r.json() : null).catch(() => null)
    .then(manifest => { if (!manifest) recorded = null; return manifest; }));
  async function sha256Hex(text) {
    if (!(window.crypto && crypto.subtle)) return null;
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  }
  async function chooseVoice(text) {
    const manifest = await loadRecorded();
    const hash = manifest && await sha256Hex(text);
    const clip = hash && manifest.clips[hash];
    if (clip) return {url: VOICE_BASE + clip.file, kind: 'The recorded Gemini clip', label: `Gemini TTS · pre-recorded clip (${manifest.voice}, ${clip.model})`,
                      note: 'Recorded once from exactly this text with Gemini TTS; not a live model call.'};
    const live = state.config && state.config.voice;
    if (!STATIC && live && live.live) {
      try {
        const response = await fetch('/api/tts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        if (!response.ok) throw new Error((await response.json()).error || ('HTTP ' + response.status));
        return {url: URL.createObjectURL(await response.blob()), revoke: true, kind: 'The live Gemini audio',
                label: `Gemini TTS · live (${response.headers.get('X-Voice')}, ${response.headers.get('X-Voice-Model')})`,
                note: 'Synthesised just now from the text on the screen.'};
      } catch (error) { logEvent({type: 'notice', text: 'Gemini voice unavailable, using the browser voice: ' + error.message}); }
    }
    // No audio to play: say why, so the timeline does not claim there was simply no matching clip.
    const why = !manifest ? 'The list of recorded clips could not be loaded, and live Gemini voice is not available here.'
      : !hash ? 'This page cannot fingerprint the text (that needs HTTPS or localhost), so recorded clips cannot be matched, and live Gemini voice is not available here.'
      : 'No recorded Gemini clip matches this text and live Gemini voice is not available here.';
    return {why};
  }
  // Resolves {ok: true} when the audio played to its end or was stopped, and {ok: false, why} when it could not play.
  function playAudio(choice, started) {
    return new Promise(resolve => {
      const audio = new Audio(choice.url);
      const handle = {stop: () => { audio.pause(); done({ok: true}); }};
      let timer = null, over = false, began = false;
      function done(result) {
        if (over) return;
        over = true; clearTimeout(timer);
        if (speaking === handle) speaking = null;
        if (choice.revoke) URL.revokeObjectURL(choice.url);
        resolve(result);
      }
      speaking = handle;
      audio.onended = () => done({ok: true});
      audio.onerror = () => done({ok: false, why: 'could not be loaded'});
      audio.onplaying = () => {
        if (!began) { began = true; started(); }
        clearTimeout(timer);
        timer = setTimeout(() => { audio.pause(); done({ok: true}); }, (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 60) * 1000 + 4000);
      };
      timer = setTimeout(() => { audio.pause(); done({ok: false, why: `did not start within ${AUDIO_START_MS / 1000} seconds`}); }, AUDIO_START_MS);
      audio.play().catch(error => done({ok: false, why: error && error.name === 'NotAllowedError' ? 'was blocked by the browser (autoplay)' : 'could not be played'}));
    });
  }
  function browserVoice(text) {
    return new Promise(resolve => {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      if (state.voice) u.voice = state.voice;
      u.rate = 1.03; u.pitch = 1.0;
      const handle = {stop: () => { speechSynthesis.cancel(); done(); }};
      const timer = setTimeout(done, Math.min(45000, 4000 + text.length * 90));
      function done() { clearTimeout(timer); if (speaking === handle) speaking = null; resolve(); }
      speaking = handle;
      u.onend = u.onerror = done;
      speechSynthesis.speak(u);
    });
  }
  // Stopping also retires whatever speak() call is still waiting (a fetch, a clip, the browser voice), so it
  // cannot start talking again after the person has muted, asked something new, or pressed to talk.
  function stopSpeaking() {
    speechTurn++;
    if (speaking) speaking.stop();
    if (window.speechSynthesis) speechSynthesis.cancel();
    if (stateName === 'speaking') setState('');
  }
  // Speaks in the background: the buttons are usable while a reply is being said, and a new reply replaces it.
  async function speak(text) {
    stopSpeaking();
    if (!text) return;
    if (state.muted) { logEvent({type: 'voice', label: 'Muted', note: 'Spoken replies are off (Voice button), so nothing was spoken.'}); return; }
    const turn = ++speechTurn, current = () => turn === speechTurn;
    setState('speaking');
    try {
      const choice = await chooseVoice(text);
      if (!current()) return;
      let why = choice.why;
      if (choice.url) {
        const played = await playAudio(choice, () => { if (current()) logEvent({type: 'voice', label: choice.label, note: choice.note}); });
        if (!current() || played.ok) return;
        why = `${choice.kind} ${played.why}.`;
      }
      if (!window.speechSynthesis) { logEvent({type: 'voice', label: 'No voice available', note: why + ' This browser has no speech synthesis, so nothing was spoken.'}); return; }
      logEvent({type: 'voice', label: 'Browser voice (speechSynthesis)', note: choice.url ? why + ' The browser voice was used instead.' : why});
      await browserVoice(text);
    } finally { if (current() && stateName === 'speaking') setState(''); }
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
      stopSpeaking();
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
      add(STATIC ? 'engine call' : 'MCP tools/call', ev.name);
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
    else if (ev.type === 'reply') { add(ev.guided || ev.source ? 'speech' : 'AI explains', ev.guided ? 'Verified suggested speech' : ev.source ? 'Verified suggested speech (model said nothing)' : 'Spoken reply'); detail.textContent = ev.text; }
    else if (ev.type === 'voice') { add('voice', ev.label); detail.textContent = ev.note; }
    else if (ev.type === 'app') { add('card → host', ev.name); detail.textContent = ev.text; }
    else { add('notice', ''); detail.className += ' error'; detail.textContent = ev.text; }
    if (ev.ms != null) kind.append(el('span', 'ms', ev.ms + ' ms'));
    li.append(kind, detail);
    if (!currentTurn) logTurn('Session');
    currentTurn.append(li);
  }

  // ---------- MCP Apps host ----------
  class AppHost {
    constructor(slot) { this.slot = slot; this.frame = null; this.card = null; this.resource = null; this.origin = STATIC ? 'null' : sandboxOrigin(); window.addEventListener('message', e => this.onMessage(e)); }
    async show(card) {
      this.card = card;
      this.resource = await api('/api/resource?uri=' + encodeURIComponent(card.resourceUri));
      const frame = document.createElement('iframe');
      frame.setAttribute('title', 'Certificate card (MCP App)');
      if (STATIC) {
        // Single-origin static hosting: the view runs in an opaque-origin srcdoc frame with the same CSP.
        const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'";
        frame.setAttribute('sandbox', 'allow-scripts');
        frame.srcdoc = this.resource.text.replace(/<head[^>]*>/i, m => m + `<meta http-equiv="Content-Security-Policy" content="${csp}">`);
      } else {
        frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
        frame.src = this.origin + '/sandbox.html';
      }
      $('conversation').classList.add('compact');
      this.slot.replaceChildren(frame); this.slot.hidden = false;
      this.frame = frame;
      this.fit();
    }
    fit() {
      // Smart-display layout: the card gets a fixed box (the rest of the screen) and scrolls inside it.
      // On a phone there is no such box, so the card is as tall as it reports itself (see size-changed).
      if (!this.frame) return;
      if (PHONE.matches) { if (!this.frame.style.height || this.fixedBox) this.frame.style.height = '360px'; this.fixedBox = false; return; }
      this.fixedBox = true;
      this.frame.style.height = Math.max(160, this.slot.clientHeight) + 'px';
    }
    post(message) { if (this.frame) this.frame.contentWindow.postMessage(message, STATIC ? '*' : this.origin); }
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
        // Fixed container on a display: size hints are acknowledged by ignoring them. On a phone the
        // card's own height is used so the whole card can be read by scrolling the page.
        const wanted = msg.params && Number(msg.params.height);
        if (PHONE.matches && wanted > 0) this.frame.style.height = Math.min(Math.max(160, Math.ceil(wanted)), 2400) + 'px';
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
  // A request disables the control that started it (and hides the first-screen starters), which drops a keyboard
  // user's focus onto the page. Callers note the focused control before touching the screen; run() puts it back.
  function keyboardFocus() {
    const a = document.activeElement;
    return a && a !== document.body && a.matches && a.matches(':focus-visible') ? (a.textContent || '').trim() : null;
  }
  async function run(label, request, focusLabel = null) {
    if (state.busy) return;
    state.busy = true; setButtons();
    stopSpeaking();
    logTurn(label);
    setState('thinking'); showReply('');
    let reply = '';
    try {
      const data = await request();
      data.events.forEach(logEvent);
      showReply(data.reply);
      if (data.card) await state.host.show(data.card);
      reply = data.reply;
    } catch (error) {
      showReply('Something went wrong: ' + error.message);
      logEvent({type: 'notice', text: error.message});
    } finally {
      state.busy = false; setState(''); setButtons();
      const now = document.activeElement;  // a hidden or disabled control can still be reported as focused until the next frame
      if (focusLabel !== null && (!now || now === document.body || now.disabled || !now.isConnected || now.closest('[hidden]'))) {
        const again = [...document.querySelectorAll('#scenarios button')].find(b => b.textContent === focusLabel);
        (again || $('text')).focus({preventScroll: true});  // keep the reply and card in view
      }
    }
    speak(reply).catch(error => logEvent({type: 'notice', text: 'Voice: ' + error.message}));
  }
  function submit(text) {
    const focus = keyboardFocus();
    showHeard(text);
    const who = MODEL_NAMES[state.provider] || state.provider;
    run('Voice turn · ' + who, () => api('/api/turn', {session, text, provider: state.provider}), focus);
  }
  function guided(scenario) {
    const focus = keyboardFocus();
    showHeard(scenario.utterance);
    run('Guided request', () => api('/api/guided', {scenario: scenario.id}), focus);
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
      if (cfg.static) { mcp.textContent = 'Engine · exact Python in your browser'; mcp.className = 'pill ok'; }
      else if (cfg.connected) { mcp.textContent = `MCP · ${cfg.protocol} · ${cfg.tools.length} tools`; mcp.className = 'pill ok'; }
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
      else if (cfg.static) { const b = el('b', null, 'Web demo: '); note.append(b, 'preset typed requests (no AI) run the real compiler and verifier in your browser. Tap one:'); }
      else { const b = el('b', null, 'Guided mode: '); note.append(b, 'no AI model is configured, so these preset typed requests call the same MCP tools directly. Configure Bedrock, Claude or Gemini to talk freely.'); }
      const box = $('scenarios'); box.replaceChildren();
      const first = $('idle-scenarios'); first.replaceChildren();
      const chip = s => { const b = el('button', null, s.label); b.type = 'button'; b.title = s.utterance; b.onclick = () => cfg.provider ? submit(s.utterance) : guided(s); return b; };
      cfg.scenarios.forEach(s => box.append(chip(s)));
      // The first screen offers the three requests that show the idea; the full list stays below the screen.
      ['work', 'review', 'tickets'].forEach(id => { const s = cfg.scenarios.find(x => x.id === id); if (s) first.append(chip(s)); });
      $('boot-note').hidden = true;
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
  // Space is push-to-talk only when nothing else has the keyboard (the page itself, or the mic button).
  // On any other control it keeps its normal job, so buttons, links and the details summary work from the keyboard.
  let spaceTalking = false;
  const spaceIsTalk = () => { const a = document.activeElement; return !a || a === document.body || a === $('mic'); };
  document.addEventListener('keydown', e => {
    if (e.code !== 'Space' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (spaceTalking) { e.preventDefault(); return; }
    if (e.repeat || !state.recognition || !spaceIsTalk()) return;
    e.preventDefault();
    listen(true);
    spaceTalking = state.listening;
  });
  document.addEventListener('keyup', e => { if (e.code === 'Space' && spaceTalking) { e.preventDefault(); spaceTalking = false; listen(false); } });
  // The button is named "Voice"; pressed means spoken replies are on.
  $('mute').addEventListener('click', () => { state.muted = !state.muted; $('mute').querySelector('.mute-icon').textContent = state.muted ? '🔇' : '🔊'; $('mute').setAttribute('aria-pressed', String(!state.muted)); if (state.muted) stopSpeaking(); });
  $('clear').addEventListener('click', () => { $('timeline').replaceChildren(); currentTurn = null; api('/api/reset', {session}).catch(() => {}); });
  if (window.speechSynthesis) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
  setupRecognition();
  window.addEventListener('resize', () => { state.host.fit(); if (state.host.frame) state.host.post({jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: {containerDimensions: {width: Math.round($('card-slot').clientWidth), height: Math.round($('card-slot').clientHeight)}}}); });
  tick(); setInterval(tick, 15000);
  loadConfig();
  window.__syw = {submit, setProvider: name => { state.provider = name; const b = document.querySelector(`#model-switch button[data-name="${name}"]`); if (b) b.click(); }, guided: id => { const s = (state.config.scenarios || []).find(x => x.id === id); if (s) guided(s); }, state};
})();
