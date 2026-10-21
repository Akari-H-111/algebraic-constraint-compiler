'use strict';
// Simulated Alexa+ smart display. Also a minimal MCP Apps host (SEP-1865):
// renders the server's ui:// card through a cross-origin sandbox proxy.
(function () {
  const $ = id => document.getElementById(id);
  const session = (() => { try { return sessionStorage.getItem('syw-session') || (sessionStorage.setItem('syw-session', crypto.randomUUID()), sessionStorage.getItem('syw-session')); } catch (e) { return 'default'; } })();
  const state = {config: null, busy: false, muted: false, voice: null, recognition: null, listening: false, host: null, provider: null, photo: null};
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
  const sizeText = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  // The state is shown as text as well as by the light bar, so it does not depend on motion.
  const STATE_TEXT = {listening: 'Listening…', thinking: 'Checking…', speaking: 'Speaking…'};
  let stateName = '';
  function setState(name) {
    stateName = name || '';
    const screen = $('screen');  // only the state-* classes change here; has-photo and dropping belong to other code
    Array.from(screen.classList).filter(c => c.startsWith('state-')).forEach(c => screen.classList.remove(c));
    if (name) screen.classList.add('state-' + name);
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
      add(STATIC && ev.transport !== 'mcp' ? 'engine call' : 'MCP tools/call', ev.name);
      if (ev.error) { detail.className += ' error'; detail.textContent = ev.error; }
      else {
        const args = Object.assign({}, ev.arguments); delete args.notebook; delete args.learner;
        const code = el('code', null, JSON.stringify(args));
        const verdict = el('div', ev.certificate_verified ? 'verified' : 'rejected',
          (ev.certificate_verified ? '✓ Compiler calculated · verifier certified' : '• ' + (ev.status || 'no claim')) +
          (ev.checks ? ` · ${ev.checks} independent checks` : '') + (ev.verdict ? ` · ${ev.verdict}` : '') + (ev.saved ? ' · saved to notebook' : '') +
          (ev.certificate ? ` · cert ${ev.certificate.slice(0, 10)}…` : ''));
        detail.append(code, verdict);
        if (ev.endpoint) detail.append(el('div', null, 'POST ' + ev.endpoint));
      }
    }
    else if (ev.type === 'reply') { add(ev.guided || ev.source ? 'speech' : 'AI explains', ev.guided ? 'Verified suggested speech' : ev.source ? 'Verified suggested speech (model said nothing)' : 'Spoken reply'); detail.textContent = ev.text; }
    else if (ev.type === 'photo') {
      add(ev.sample ? 'sample page' : 'photo', ev.sample ? 'Sample sheet shown' : 'Photo received');
      if (ev.sha256) {
        const hash = el('code', null, 'sha256 ' + ev.sha256.slice(0, 12) + '…'); hash.title = ev.sha256;
        detail.append(`${ev.width}×${ev.height} · ${sizeText(ev.bytes)} · `, hash);
        if (ev.sample) detail.append(' · a rendered picture of a fictional child’s work, not a camera photo');
      } else detail.textContent = ev.text || '';
    }
    else if (ev.type === 'reading') { add('reading', ev.recorded ? 'Recorded reading' : 'Example reading'); detail.textContent = ev.text; }
    else if (ev.type === 'voice') { add('voice', ev.label); detail.textContent = ev.note; }
    else if (ev.type === 'app') { add('card → host', ev.name); detail.textContent = ev.text; }
    else if (ev.type === 'info') { add(ev.tag || 'info', ev.label || ''); detail.textContent = ev.text || ''; detail.className += {ok: ' verified', warn: ' rejected', bad: ' error'}[ev.tone] || ''; }
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
      this.slot.replaceChildren(frame); this.slot.hidden = false; syncWork();
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
          (result.hostEvents || []).forEach(logEvent);  // web demo: says whether the call went over MCP or ran in the browser
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

  // ---------- photo of the page ----------
  // "Show Alexa the page": a photo is attached (camera, file picker or drag and drop), downscaled here (long side <= 1600 px,
  // JPEG quality 0.85) and sent with the turn. A model reads the lines; the display then draws boxes on the photo from the
  // verified result only. BOX CONVENTION, shared with the model's instructions (simulator.py SYSTEM) and with `provenance`:
  // [left, top, right, bottom] as whole numbers on a 0-1000 scale of the photo's own width and height, so (0, 0) is the
  // top-left corner and (1000, 1000) the bottom-right corner, whatever the photo's shape. Percentages of the picture = box / 10.
  const PHOTO_SIDE = 1600, PHOTO_QUALITY = 0.85, PHOTO_MAX_FILE = 40e6;
  const REVIEW_WORDS = {AMBIGUOUS_SYMBOL: 'a symbol is unclear', AMBIGUOUS_GROUPING: 'the grouping is unclear', ILLEGIBLE: 'I can’t read it',
                        UNPARSEABLE: 'it doesn’t read as an equation', OTHER: 'it needs a second look'};
  const readDataURL = blob => new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => reject(r.error); r.readAsDataURL(blob); });
  async function decodeImage(blob) {
    if (window.createImageBitmap) { try { return await createImageBitmap(blob, {imageOrientation: 'from-image'}); } catch (e) { /* fall back to an image element */ } }
    const url = await readDataURL(blob);  // a data: URL, because the page's CSP allows data: but not blob: images
    return new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error('decode')); img.src = url; });
  }
  async function prepareImage(file) {
    const image = await decodeImage(file);
    const w0 = image.width || image.naturalWidth, h0 = image.height || image.naturalHeight;
    if (!w0 || !h0) throw new Error('empty');
    const scale = Math.min(1, PHOTO_SIDE / Math.max(w0, h0));
    const width = Math.max(1, Math.round(w0 * scale)), height = Math.max(1, Math.round(h0 * scale));
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height); ctx.drawImage(image, 0, 0, width, height);
    if (image.close) image.close();
    const url = canvas.toDataURL('image/jpeg', PHOTO_QUALITY), data = url.slice(url.indexOf(',') + 1);
    return {mime: 'image/jpeg', data, url, width, height, bytes: Math.floor(data.length * 3 / 4) - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0)};
  }
  // A photo whose src is a data: URL came from the person; any other src is one of the rendered sample sheets.
  const isSample = photo => photo.sample !== undefined ? Boolean(photo.sample) : !/^data:/.test(photo.src || '');
  const readingText = r => (r.recorded ? `Recorded reading by ${r.model} on ${r.date}` : 'Example reading written for the demo, not a model output')
    + ' — no AI runs in Guided mode. The sheet is a rendered sample, not a camera photo.';
  const attachNote = text => { const n = $('attach-note'); n.textContent = text || ''; n.hidden = !text; };
  function setPhoto(photo) {
    state.photo = photo;
    $('attach').hidden = !photo;
    if (photo) { $('attach-thumb').src = photo.url; $('attach-info').textContent = `Photo of the page ready · ${photo.width}×${photo.height} · ${sizeText(photo.bytes)}`; }
    else $('attach-thumb').removeAttribute('src');
  }
  async function attachFile(file) {
    if (!file || state.busy) return;
    if (!/^image\//.test(file.type) && !/\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(file.name || '')) { attachNote('That file is not a picture. Choose a JPEG, PNG or WebP photo of the page.'); return; }
    if (file.size > PHOTO_MAX_FILE) { attachNote('That picture is too large. Choose one under 40 MB.'); return; }
    attachNote('Preparing the photo…');
    try { setPhoto(await prepareImage(file)); attachNote(''); }
    catch (e) { setPhoto(null); attachNote('This browser could not read that picture. Try a JPEG or PNG photo.'); }
  }

  // A box is drawn only if it is four whole numbers inside the photo's 0-1000 frame (left < right, top < bottom) on page 1.
  function regionBox(source) {
    if (!source || source.kind !== 'region' || source.page !== 1 || !Array.isArray(source.box) || source.box.length !== 4) return null;
    const [l, t, r, b] = source.box;
    return source.box.every(v => Number.isInteger(v) && v >= 0 && v <= 1000) && l < r && t < b ? source.box : null;
  }
  // What to draw, from the tool result only: red for the verified first line that changes the answer, amber for lines held for
  // review, a green tick for steps the certificate proves sound. If the certificate was not verified, boxes are neutral and no verdict is drawn.
  function overlayPlan(card) {
    if (!card || card.tool !== 'check_work') return null;
    const payload = (card.result && card.result.structuredContent) || {}, view = payload.view || {};
    const notes = ((payload.bundle || {}).input || {}).provenance || (card.arguments || {}).provenance;
    if (!Array.isArray(notes)) return null;
    const boxes = notes.map(n => regionBox(n && n.source));
    if (!boxes.some(Boolean)) return null;
    const line = i => `Line ${i + 1}`;
    const all = boxes.filter(Boolean);
    if (payload.certificate_verified !== true) return {verified: false, boxes: all, items: boxes.map((box, i) => box && {i, box, kind: 'plain', text: line(i)}).filter(Boolean)};
    const items = [], transitions = ((((payload.bundle || {}).certificate || {}).result || {}).transitions) || [];
    const focus = view.verdict === 'error_found' && view.focus ? view.focus.step_index : -1;
    const review = new Map((view.review || []).map(r => [r.step_index, r]));
    const sound = new Map(transitions.filter(t => t.status === 'sound').map(t => [t.to, t.from]));
    boxes.forEach((own, i) => {
      if (i === focus) { const box = regionBox((view.focus || {}).source) || own; if (box) items.push({i, box, kind: 'focus', text: `${line(i)}: first line that changes the answer`}); }
      else if (review.has(i)) {
        const r = review.get(i), box = regionBox(r.source) || own;
        if (box) items.push({i, box, kind: 'review', text: '?', note: r.note || REVIEW_WORDS[r.reason] || 'it needs a second look', legend: `${line(i)}: ${r.note || REVIEW_WORDS[r.reason] || 'it needs a second look'}`});
      }
      else if (sound.has(i) && own) items.push({i, box: own, kind: 'sound', text: `${line(i)} follows from ${line(sound.get(i)).toLowerCase()}`});
    });
    return {verified: true, boxes: all, items};
  }
  // "Zoom to work": the part of the page (a square on the 0-1000 scale, so the picture keeps its own aspect ratio) that holds every
  // box with 12% of the page on each side, at most about 3x. null when that would be less than ~1.15x, so there is nothing to zoom to.
  const ZOOM_MARGIN = 120, ZOOM_MIN_SIDE = 334, ZOOM_MAX_SIDE = 870;
  function workWindow(boxes) {
    if (!boxes || !boxes.length) return null;
    const left = Math.min(...boxes.map(b => b[0])), top = Math.min(...boxes.map(b => b[1]));
    const right = Math.max(...boxes.map(b => b[2])), bottom = Math.max(...boxes.map(b => b[3]));
    const side = Math.max(ZOOM_MIN_SIDE, Math.max(right - left, bottom - top) + 2 * ZOOM_MARGIN);
    if (side > ZOOM_MAX_SIDE) return null;
    const place = (low, high) => Math.min(1000 - side, Math.max(0, Math.round((low + high) / 2 - side / 2)));
    return {x0: place(left, right), y0: place(top, bottom), side};
  }
  function drawOverlay(plan, view) {
    const layer = $('photo-overlay'); layer.replaceChildren();
    const edge = view ? view.x0 + view.side : 1000;  // labels stay inside what can be seen
    const perUnit = ($('photo-frame').clientWidth || 260) / (view ? view.side : 1000);  // screen pixels per unit of the 0-1000 scale
    const need = $('photo-panel').classList.contains('wide') ? 150 : 66;  // room a label needs beside its line, in pixels
    // Ticks share one column just right of the widest line, like marks in a margin, so they never sit on the handwriting.
    const gutter = Math.min(Math.max(0, ...((plan && plan.items) || []).map(x => x.box[2])) + 45, edge - 30);
    ((plan && plan.items) || []).forEach(item => {
      const [l, t, r, b] = item.box, mid = (t + b) / 2;
      const box = el('div', 'pbox ' + item.kind);
      box.style.cssText = `left:${l / 10}%;top:${t / 10}%;width:${(r - l) / 10}%;height:${(b - t) / 10}%`;
      layer.append(box);
      if (item.kind === 'sound') {
        const tick = el('span', 'ptick', '✓'); tick.style.cssText = `left:${gutter / 10}%;top:${mid / 10}%`; layer.append(tick);
      } else if (item.kind === 'focus' || item.kind === 'review') {
        // The label sits beside the line when there is room to its right (handwriting leaves the right side empty), else under it.
        // A small photo gets a short label ("Line 2", "?"); the full words show on the photo when it is large, and always in the legend.
        const tag = el('span', 'ptag ' + item.kind);
        tag.append(el('span', 't-short', item.kind === 'review' ? '?' : `Line ${item.i + 1}`), el('span', 't-full', item.kind === 'review' ? '? ' + item.note : item.text));
        tag.style.cssText = (edge - r) * perUnit >= need ? `left:calc(${r / 10}% + 8px);top:${mid / 10}%;max-width:calc(${(edge - r) / 10}% - 10px);transform:translateY(-50%)`
                                            : `left:${l / 10}%;top:calc(${b / 10}% + 5px);max-width:${(edge - l) / 10}%`;
        layer.append(tag);
      }
    });
  }
  function drawLegend(photo, plan, pending) {
    const legend = $('photo-legend'); legend.replaceChildren();
    legend.append(el('p', 'photo-cap', pending ? 'Alexa is reading the page…' : isSample(photo) ? 'Sample page (a rendered picture)' : 'Your photo'));
    if (!pending && plan && !plan.verified) legend.append(el('p', 'photo-warn', 'Not verified: the boxes only show where each line was read. No verdict is drawn.'));
    else if (!pending && !plan) legend.append(el('p', 'photo-none', 'No line positions came with this reading, so nothing is marked.'));
    const list = el('ul');
    if (plan && plan.verified) {
      // "Line 2:" in bold, then the words: the first wrong line is a red chip, review lines an amber one.
      const row = (cls, mark, text) => { const li = el('li', cls), at = text.indexOf(': '); const words = el('span', 'l-text'); words.append(el('b', null, text.slice(0, at + 1)), ' ' + text.slice(at + 2)); li.append(el('span', 'l-mark', mark), words); return li; };
      plan.items.filter(x => x.kind === 'focus').forEach(x => list.append(row('l-focus', '✗', x.text)));
      plan.items.filter(x => x.kind === 'review').forEach(x => list.append(row('l-review', '?', x.legend)));
      const ticks = plan.items.filter(x => x.kind === 'sound');
      if (ticks.length) list.append(el('li', 'l-sound', '✓ ' + ticks.map(x => x.text).join(' · ')));
    }
    if (list.childNodes.length) legend.append(list);
  }
  // The frame is sized to the photo's own aspect ratio (as large as the space allows) and never changes with the view. Inside it a
  // stage holds the picture and the overlay together, so every box is a percentage of the page and stays exactly on its line when
  // the stage is moved and scaled to show only the work.
  let photoView = null, photoPlan = null, zoomed = true;
  function fitPhoto() {
    const panel = $('photo-panel'); if (panel.hidden) return;
    const screen = $('screen'), style = getComputedStyle(screen), img = $('photo-img'), frame = $('photo-frame');
    if (!img.naturalWidth || !img.naturalHeight) { frame.style.width = frame.style.height = '0px'; return; }
    const inner = {w: screen.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                   h: screen.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - screen.querySelector('.statusbar').offsetHeight - 8};
    // On a display the photo takes the left column down the whole screen; on a phone it sits above the card.
    const maxW = PHONE.matches ? inner.w : Math.round(inner.w * 0.42);
    const maxH = PHONE.matches ? 340 : Math.max(120, inner.h - $('photo-legend').offsetHeight - ($('photo-views').hidden ? 0 : $('photo-views').offsetHeight + 6) - 8);
    const width = Math.floor(Math.min(maxW, maxH * img.naturalWidth / img.naturalHeight));
    frame.style.width = width + 'px'; frame.style.height = Math.round(width * img.naturalHeight / img.naturalWidth) + 'px';
    panel.classList.toggle('wide', width >= 380);
  }
  function applyView(animate) {
    const stage = $('photo-stage'), view = zoomed ? photoView : null;
    const side = view ? view.side : 1000, x0 = view ? view.x0 : 0, y0 = view ? view.y0 : 0;
    if (!animate) stage.style.transition = 'none';
    Object.assign(stage.style, {left: `${-x0 * 100 / side}%`, top: `${-y0 * 100 / side}%`, width: `${100000 / side}%`, height: `${100000 / side}%`});
    if (!animate) { void stage.offsetWidth; stage.style.transition = ''; }
    drawOverlay(photoPlan, view);
    $('photo-views').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String((b.dataset.view === 'zoom') === zoomed)));
  }
  $('photo-views').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b || (b.dataset.view === 'zoom') === zoomed) return;
    zoomed = b.dataset.view === 'zoom'; applyView(true);
  });
  function syncWork() {
    $('work-area').hidden = $('card-slot').hidden && $('photo-panel').hidden;
    $('screen').classList.toggle('has-photo', !$('photo-panel').hidden);  // on a display the photo gets its own column (CSS)
  }
  function hidePhoto() {
    $('photo-panel').hidden = true; $('photo-img').removeAttribute('src'); $('photo-overlay').replaceChildren(); $('photo-views').hidden = true; syncWork();
  }
  // data.photo is {src, ...}; pending shows the picture at once while the page is being read.
  function showPhoto(data, pending) {
    const photo = data && data.photo;
    if (!photo || !photo.src) { hidePhoto(); return; }
    const plan = pending ? null : overlayPlan(data.card);
    const img = $('photo-img'), changed = img.getAttribute('src') !== photo.src;
    if (changed) img.src = photo.src;
    $('photo-panel').hidden = false; syncWork();
    $('conversation').classList.add('compact');
    // A reading with boxes opens zoomed to the work (the toggle goes back to the whole page); while reading, the whole page shows.
    photoPlan = plan; photoView = plan ? workWindow(plan.boxes) : null; zoomed = true;
    $('photo-views').hidden = !photoView;
    drawLegend(photo, plan, pending);
    applyView(!changed);
    fitPhoto(); requestAnimationFrame(() => { fitPhoto(); drawOverlay(photoPlan, zoomed ? photoView : null); state.host.fit(); });
  }
  $('photo-img').addEventListener('load', () => { fitPhoto(); drawOverlay(photoPlan, zoomed ? photoView : null); state.host.fit(); });

  // ---------- turns ----------
  // A request disables the control that started it (and hides the first-screen starters), which drops a keyboard
  // user's focus onto the page. Callers note the focused control before touching the screen; run() puts it back.
  function keyboardFocus() {
    const a = document.activeElement;
    return a && a !== document.body && a.matches && a.matches(':focus-visible') ? (a.textContent || '').trim() : null;
  }
  async function run(label, request, focusLabel = null, pending = null) {
    if (state.busy) return;
    state.busy = true; setButtons();
    stopSpeaking();
    logTurn(label);
    setState('thinking'); showReply('');
    if (pending) showPhoto({photo: {src: pending.url}}, true);  // the page appears while it is being read
    let reply = '';
    try {
      const data = await request();
      // A backend that sends only photo.src (the static web demo) still gets its timeline rows: the picture, and where the reading came from.
      const events = data.events.slice(), photo = data.photo;
      if (photo && !events.some(e => e.type === 'photo')) events.splice(1, 0, {type: 'photo', sample: isSample(photo), text: isSample(photo) ? 'A rendered picture of a fictional child’s work, not a camera photo' : 'Photo shown'});
      if (photo && photo.reading && !events.some(e => e.type === 'reading')) events.splice(2, 0, {type: 'reading', recorded: Boolean(photo.reading.recorded), text: readingText(photo.reading)});
      events.forEach(logEvent);
      showReply(data.reply);
      showPhoto(data);  // draws only what the verified result says, or hides the panel when this turn had no photo
      if (data.card) await state.host.show(data.card); else state.host.fit();
      reply = data.reply;
    } catch (error) {
      if (pending) hidePhoto();
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
  // Typed or spoken requests carry the attached photo, if any; a preset request passes {photo: null} (or its own picture).
  function submit(text, options = {}) {
    if (state.busy) return;
    const focus = keyboardFocus();
    const photo = 'photo' in options ? options.photo : state.photo;
    const attached = photo && photo === state.photo;
    showHeard(text);
    const who = MODEL_NAMES[state.provider] || state.provider;
    if (attached) setPhoto(null);
    const image = photo && {mime: photo.mime, data: photo.data};
    run((photo ? 'Photo turn · ' : 'Voice turn · ') + who, async () => {
      try { return await api('/api/turn', {session, text, provider: state.provider, image}); }
      catch (error) { if (attached && !state.photo) setPhoto(photo); throw error; }  // keep the page for another try
    }, focus, photo && {url: photo.url || `data:${photo.mime};base64,${photo.data}`});
  }
  // With a model, the preset photo request sends the rendered sample sheet to the model, like a real photo.
  async function submitSample(scenario) {
    if (state.busy) return;
    try {
      const blob = await (await fetch(scenario.photo)).blob();
      const url = await readDataURL(blob);
      submit(scenario.utterance, {photo: {mime: blob.type || 'image/jpeg', data: url.slice(url.indexOf(',') + 1), url}});
    } catch (error) { logEvent({type: 'notice', text: 'Could not load the sample sheet: ' + error.message}); }
  }
  const askDefault = () => `Alexa, check ${(state.config && state.config.learner) || 'Maya'}’s homework on this page.`;
  function guided(scenario) {
    const focus = keyboardFocus();
    showHeard(scenario.utterance);
    run('Guided request', () => api('/api/guided', {scenario: scenario.id}), focus);
  }
  function setButtons() {
    document.querySelectorAll('.scenarios button, .send, #photo-btn, #attach-remove').forEach(b => b.disabled = state.busy);
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
      const chip = s => { const b = el('button', null, s.label); b.type = 'button'; b.title = s.utterance; b.onclick = () => cfg.provider && !s.guided_only ? (s.photo ? submitSample(s) : submit(s.utterance, {photo: null})) : guided(s); return b; };
      cfg.scenarios.forEach(s => box.append(chip(s)));
      // The first screen offers the four requests that show the idea; the full list stays below the screen.
      ['work', 'photo', 'review', 'tickets'].forEach(id => { const s = cfg.scenarios.find(x => x.id === id); if (s) first.append(chip(s)); });
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
  $('talk').addEventListener('submit', e => {
    e.preventDefault();
    const typed = $('text').value.trim();
    if ((!typed && !state.photo) || state.busy) return;
    const t = typed || askDefault();
    $('text').value = '';
    if (state.config && state.config.provider) submit(t);
    else { showHeard(t); showReply(state.photo ? 'No AI model is configured, so I can’t read a photo. The “Check a photo of the page” request below shows a recorded reading of a sample page.' : 'No AI model is configured. Pick a Guided request below.'); }
  });
  // Photo: the button opens the camera on a phone (capture) or a file picker; a picture can also be dropped on the display.
  $('photo-btn').addEventListener('click', () => { if (!state.busy) $('photo-input').click(); });
  $('photo-input').addEventListener('change', e => { const file = e.target.files && e.target.files[0]; e.target.value = ''; attachFile(file); });
  $('attach-remove').addEventListener('click', () => { setPhoto(null); attachNote(''); $('photo-btn').focus({preventScroll: true}); });
  let dragDepth = 0;
  const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  document.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; $('screen').classList.add('dropping'); });
  document.addEventListener('dragover', e => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
  document.addEventListener('dragleave', e => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('screen').classList.remove('dropping'); });
  document.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; $('screen').classList.remove('dropping');
    const files = Array.from(e.dataTransfer.files || []);
    attachFile(files.find(f => /^image\//.test(f.type)) || files[0]);
  });
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
  window.addEventListener('resize', () => { fitPhoto(); state.host.fit(); if (state.host.frame) state.host.post({jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: {containerDimensions: {width: Math.round($('card-slot').clientWidth), height: Math.round($('card-slot').clientHeight)}}}); });
  tick(); setInterval(tick, 15000);
  loadConfig();
  window.__syw = {submit, attachFile, showPhoto, overlayPlan, setProvider: name => { state.provider = name; const b = document.querySelector(`#model-switch button[data-name="${name}"]`); if (b) b.click(); }, guided: id => { const s = (state.config.scenarios || []).find(x => x.id === id); if (s) guided(s); }, state};
})();
