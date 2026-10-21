'use strict';
// Browser MCP client for Streamable HTTP, protocol 2025-11-25: the JavaScript twin of
// algebraic_compiler/mcp_client.py. JSON-RPC over fetch POST; JSON or SSE replies. No dependencies.
// Every request returns {result, ms}; `client.trace` keeps {method, ms, ok} for each call, for display.
(function (root) {
  const PROTOCOL = '2025-11-25';
  const DEFAULT_URL = 'https://show-your-work-mcp.onrender.com/mcp';
  const UI_EXTENSION = 'io.modelcontextprotocol/ui', UI_MIME = 'text/html;profile=mcp-app';
  const LOOPBACK = /^(localhost|127\.0\.0\.1|\[::1\])$/;

  // kind: 'timeout' | 'network' (unreachable, or blocked by the browser) | 'http' | 'rpc' | 'protocol'
  class McpError extends Error {
    constructor(message, kind, extra) { super(message); this.name = 'McpError'; this.kind = kind; Object.assign(this, extra); }
  }

  // The public server, or a local one for testing (?mcp=http://127.0.0.1:8000/mcp). The override is limited to
  // loopback so that a shared link cannot point this page at someone else's server.
  function endpointFor(search) {
    const wanted = new URLSearchParams(search || '').get('mcp');
    if (!wanted) return {url: DEFAULT_URL, custom: false};
    let url;
    try { url = new URL(wanted); } catch (e) { throw new McpError('?mcp= is not a URL: ' + wanted, 'protocol'); }
    if (!/^https?:$/.test(url.protocol) || !LOOPBACK.test(url.hostname)) throw new McpError('?mcp= may only point at localhost, 127.0.0.1 or [::1]', 'protocol');
    return {url: url.href, custom: true};
  }

  // MCP Apps linkage for a tool, as in mcp_client.ui_meta.
  function uiMeta(tool) {
    const meta = (tool && tool._meta) || {}, ui = meta.ui || {};
    return {resourceUri: ui.resourceUri || meta['ui/resourceUri'] || null, visibility: ui.visibility || ['model', 'app']};
  }

  function sseReply(text, id) {
    for (const block of text.split(/\r?\n\r?\n/)) {
      const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n');
      if (!data) continue;
      const message = JSON.parse(data);
      if (message.id === id) return message;
    }
    throw new McpError('No response event in the SSE stream', 'protocol');
  }

  class McpClient {
    constructor(url, options) {
      const o = options || {};
      this.url = url; this.timeout = o.timeout || 30000;
      this.fetch = o.fetch || ((resource, init) => root.fetch(resource, init));
      this.clientInfo = o.clientInfo || {name: 'show-your-work-web-demo', version: '0.1.0'};
      this.sessionId = null; this.protocol = null; this.serverInfo = null; this.instructions = '';
      this.trace = []; this._id = 0;
    }

    async _post(message, timeout) {
      const headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'};
      if (this.protocol) headers['MCP-Protocol-Version'] = this.protocol;  // required after initialize
      if (this.sessionId) headers['Mcp-Session-Id'] = this.sessionId;
      const limit = timeout || this.timeout, controller = new AbortController(), timer = setTimeout(() => controller.abort(), limit);
      const failure = e => e.name === 'AbortError' ? new McpError(`No answer from ${this.url} within ${Math.round(limit / 1000)} s`, 'timeout')
        : new McpError(`Cannot reach ${this.url} (${e.message}): the server may be asleep or offline, or the browser blocked the request`, 'network');
      try {
        let response, text;
        try {
          response = await this.fetch(this.url, {method: 'POST', headers, body: JSON.stringify(message), signal: controller.signal, credentials: 'omit'});
          text = await response.text();
        } catch (e) { throw failure(e); }
        const session = response.headers.get('Mcp-Session-Id');
        if (session) this.sessionId = session;
        if (!response.ok) throw new McpError(`HTTP ${response.status} from ${this.url}: ${text.slice(0, 200)}`, 'http', {status: response.status});
        return {text, type: response.headers.get('Content-Type') || ''};
      } finally { clearTimeout(timer); }
    }

    async request(method, params, timeout) {
      const id = ++this._id, started = performance.now(), entry = {method, ms: null, ok: false};
      this.trace.push(entry);
      try {
        const {text, type} = await this._post({jsonrpc: '2.0', id, method, params: params || {}}, timeout);
        let reply;
        try { reply = type.indexOf('text/event-stream') >= 0 ? sseReply(text, id) : JSON.parse(text); }
        catch (e) { throw e instanceof McpError ? e : new McpError('The reply was not JSON-RPC: ' + text.slice(0, 120), 'protocol'); }
        if (reply.error) throw new McpError(reply.error.message || 'MCP error', 'rpc', {code: reply.error.code});
        entry.ok = true;
        return {result: reply.result, ms: Math.round(performance.now() - started)};
      } finally { entry.ms = Math.round(performance.now() - started); }
    }

    async notify(method, params) {
      const started = performance.now(), entry = {method, ms: null, ok: false};
      this.trace.push(entry);
      try { await this._post({jsonrpc: '2.0', method, params: params || {}}); entry.ok = true; }
      finally { entry.ms = Math.round(performance.now() - started); }
    }

    // initialize, then notifications/initialized. A free-tier server can take ~50 s to wake, so connection
    // problems (not protocol errors) are retried until `budgetMs` runs out.
    async connect(budgetMs) {
      const deadline = Date.now() + (budgetMs || this.timeout);
      for (;;) {
        try {
          const params = {protocolVersion: PROTOCOL, clientInfo: this.clientInfo, capabilities: {extensions: {[UI_EXTENSION]: {mimeTypes: [UI_MIME]}}}};
          const {result} = await this.request('initialize', params, Math.max(5000, Math.min(deadline - Date.now(), 90000)));
          if (!result || typeof result.protocolVersion !== 'string') throw new McpError('initialize returned no protocolVersion', 'protocol');
          this.protocol = result.protocolVersion; this.serverInfo = result.serverInfo || null; this.instructions = result.instructions || '';
          await this.notify('notifications/initialized');
          return result;
        } catch (e) {
          const transient = e.kind === 'network' || e.kind === 'timeout' || e.kind === 'protocol' || (e.kind === 'http' && e.status >= 500);
          if (!transient || deadline - Date.now() < 3000) throw e;
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      }
    }

    listTools() { return this.request('tools/list'); }
    readResource(uri) { return this.request('resources/read', {uri}); }
    callTool(name, args, timeout) { return this.request('tools/call', {name, arguments: args || {}}, timeout); }
  }

  const api = {PROTOCOL, DEFAULT_URL, UI_MIME, McpClient, McpError, endpointFor, uiMeta, sseReply};
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.SYW_MCP = api;
})(typeof window !== 'undefined' ? window : global);
