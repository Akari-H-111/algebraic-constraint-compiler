// The web demo's browser MCP client against a real server:
//   python -m algebraic_compiler.mcp_server --cors --port 8000
//   node tests/check_browser_mcp.mjs http://127.0.0.1:8000/mcp
// Loads tools/site/mcp-client.js (the file the Pages site ships) and runs initialize -> notifications/initialized ->
// tools/list -> resources/read -> tools/call. The last line printed is JSON ({ok, trace, bundle, ...}); the Python
// test replays that bundle with the independent verifier, as the page does in Pyodide.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { McpClient, McpError, endpointFor, uiMeta, sseReply, PROTOCOL, DEFAULT_URL, UI_MIME } =
  createRequire(import.meta.url)('../tools/site/mcp-client.js');
const url = process.argv[2];
assert.ok(url, 'usage: node tests/check_browser_mcp.mjs <MCP endpoint, e.g. http://127.0.0.1:8000/mcp>');

// ---- endpoint choice: the public server unless a loopback override is given ----
assert.equal(PROTOCOL, '2025-11-25');
assert.deepEqual(endpointFor(''), { url: DEFAULT_URL, custom: false });
assert.deepEqual(endpointFor('?x=1'), { url: DEFAULT_URL, custom: false });
assert.deepEqual(endpointFor('?mcp=http://127.0.0.1:8123/mcp'), { url: 'http://127.0.0.1:8123/mcp', custom: true });
assert.equal(endpointFor('?mcp=http://localhost:9/mcp').custom, true);
assert.equal(endpointFor('?mcp=http://[::1]:9/mcp').custom, true);
for (const bad of ['?mcp=https://evil.example/mcp', '?mcp=http://127.0.0.1.evil.example/mcp', '?mcp=not a url', '?mcp=file:///etc/passwd'])
  assert.throws(() => endpointFor(bad), McpError, bad);

// ---- response parsing and headers, with a stand-in fetch ----
const sse = 'event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{"other":true}}\n\nevent: message\ndata: {"jsonrpc":"2.0","id":2,\ndata: "result":{"ok":1}}\n\n';
assert.deepEqual(sseReply(sse, 2).result, { ok: 1 });
assert.throws(() => sseReply(sse, 3), /No response event/);

const seen = [];
function fake(handler) {
  return async (resource, init) => { seen.push({ resource, headers: init.headers, body: JSON.parse(init.body) }); return handler(init); };
}
const json = (body, headers) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json', ...headers } });
const stream = (body, headers) => new Response(`event: message\ndata: ${JSON.stringify(body)}\n\n`, { status: 200, headers: { 'Content-Type': 'text/event-stream', ...headers } });
{
  const client = new McpClient('http://fake.invalid/mcp', { fetch: fake(init => {
    const message = JSON.parse(init.body);
    if (message.method === 'initialize') return stream({ jsonrpc: '2.0', id: message.id, result: { protocolVersion: '2025-06-18', serverInfo: { name: 'Fake', version: '1' } } }, { 'Mcp-Session-Id': 'abc' });
    if (message.method === 'notifications/initialized') return new Response(null, { status: 202 });
    return json({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'no such method' } });
  }) });
  await client.connect(1000);
  assert.equal(client.protocol, '2025-06-18');  // the negotiated version is the one sent from then on
  assert.equal(seen[0].headers['MCP-Protocol-Version'], undefined, 'initialize must not claim a version yet');
  assert.equal(seen[0].headers.Accept, 'application/json, text/event-stream');
  assert.equal(seen[0].body.params.protocolVersion, '2025-11-25');
  assert.ok(seen[0].body.params.capabilities.extensions['io.modelcontextprotocol/ui'].mimeTypes.includes(UI_MIME));
  assert.equal(seen[1].body.method, 'notifications/initialized');
  assert.equal(seen[1].headers['MCP-Protocol-Version'], '2025-06-18');
  assert.equal(seen[1].headers['Mcp-Session-Id'], 'abc');
  await assert.rejects(client.request('nope'), err => err.kind === 'rpc' && err.code === -32601 && /no such method/.test(err.message));
}

// ---- clear errors: unreachable, silent, refused, and a server that is still waking ----
const failing = handler => new McpClient('http://fake.invalid/mcp', { fetch: handler, timeout: 60 });
await assert.rejects(failing(async () => { throw new TypeError('Failed to fetch'); }).request('ping'), err => err.kind === 'network' && /Cannot reach http:\/\/fake\.invalid\/mcp/.test(err.message));
await assert.rejects(failing((resource, init) => new Promise((resolve, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))))).request('ping'), err => err.kind === 'timeout' && /within 0 s/.test(err.message));
await assert.rejects(failing(async () => new Response('Origin not allowed', { status: 403 })).connect(1000), err => err.kind === 'http' && err.status === 403);
await assert.rejects(failing(async () => new Response('<html>Service waking up</html>', { status: 200, headers: { 'Content-Type': 'text/html' } })).request('ping'), err => err.kind === 'protocol');
await assert.rejects(new McpClient('http://127.0.0.1:1/mcp', { timeout: 2000 }).connect(1000), err => err.kind === 'network');

// ---- a real server ----
const client = new McpClient(url, { timeout: 30000 });
const init = await client.connect(10000);
assert.equal(init.protocolVersion, PROTOCOL);
assert.equal(client.protocol, PROTOCOL);
assert.equal(client.serverInfo.name, 'Show Your Work');
assert.match(client.instructions, /certificate/i);

const { result: listed } = await client.listTools();
const tools = new Map(listed.tools.map(tool => [tool.name, tool]));
for (const name of ['solve_equations', 'check_answer', 'check_work', 'practice_problem', 'verify_certificate', 'progress_report', 'notebook_history', 'forgery_check'])
  assert.ok(tools.has(name), 'missing tool ' + name);
assert.deepEqual(uiMeta(tools.get('forgery_check')).visibility, ['app']);  // app-only: the card may call it, the model may not
assert.ok(uiMeta(tools.get('check_work')).visibility.includes('model'));
const cardUri = uiMeta(tools.get('check_work')).resourceUri;
assert.equal(cardUri, 'ui://show-your-work/certificate-card.html');

const { result: resource } = await client.readResource(cardUri);
assert.equal(resource.contents[0].uri, cardUri);
assert.equal(resource.contents[0].mimeType, UI_MIME);
assert.match(resource.contents[0].text, /<html|<!doctype/i);

const work = [['3x + 5 = 20'], ['3x = 25'], ['x = 25/3']];
const { result: called, ms } = await client.callTool('check_work', { steps: work });
assert.ok(ms >= 0);
assert.notEqual(called.isError, true);
const payload = called.structuredContent;
assert.equal(payload.verdict, 'error_found');
assert.equal(payload.certificate_verified, true);
assert.ok(payload.replay.checks_total > 0 && payload.replay.checks_passed === payload.replay.checks_total);
assert.equal(payload.bundle.format, 'show-your-work/bundle');
assert.ok(called.content[0].text.includes('error_found'));

// A tool the server does not have is reported, not mistaken for a result.
let unknown;
try { unknown = (await client.callTool('no_such_tool', {})).result; } catch (err) { assert.equal(err.kind, 'rpc'); }
if (unknown) assert.equal(unknown.isError, true);

assert.deepEqual(client.trace.filter(t => t.ok).map(t => t.method).slice(0, 5),
  ['initialize', 'notifications/initialized', 'tools/list', 'resources/read', 'tools/call']);
assert.ok(client.trace.every(t => Number.isInteger(t.ms) && t.ms >= 0));
console.log(JSON.stringify({ ok: true, server: client.serverInfo, protocol: client.protocol, tools: listed.tools.length, ms,
  trace: client.trace, checks: payload.replay, bundle: payload.bundle }));
