// Run against the local workbench: node tests/check_agent_session.mjs [URL]
// Minimal DOM harness checks tool behavior; real browser rehearsal checks rendering.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const base = process.argv[2] || 'http://127.0.0.1:8765';
const nodes = new Map(), tools = new Map();
function element() {
  return {value:'', textContent:'', dataset:{}, children:[],
    addEventListener(){}, replaceChildren(...children){this.children = children;},
    append(child){this.children.push(child); child.remove = () => this.children.splice(this.children.indexOf(child),1);},
    get firstElementChild(){return this.children[0];}};
}
const document = {
  getElementById(id){if (!nodes.has(id)) nodes.set(id,element()); return nodes.get(id);},
  createElement:element,
  modelContext:{registerTool(tool){tools.set(tool.name,tool);}}
};
const $ = id => document.getElementById(id);
$('example').value = 'second-order-extends';
let loaded;
const examplesReady = new Promise(resolve => {loaded = resolve;});
const context = vm.createContext({document, window:{addEventListener(){}}, AbortController, console,
  fetch:async (path, options) => {
    const response = await fetch(new URL(path,base),options);
    if (path === '/api/examples') {
      const data = await response.json();
      return {ok:response.ok, json:async () => {loaded(); return data;}};
    }
    return response;
  }});
vm.runInContext(await readFile(new URL('../algebraic_compiler/web/app.js',import.meta.url),'utf8'),context);
await examplesReady;
// Complete the registration and example-loading promise chains, without a timer.
for (let i=0;i<8;i++) await Promise.resolve();
assert.equal(tools.size,4);
const call = (name,input={}) => tools.get(name).execute(input);
const initial = $('spec').value;
const workspace = await call('get_algebraic_workspace');
assert.equal(workspace.candidate_specification_json,initial);
assert.equal($('spec').value,initial);
assert.equal($('status').textContent,'NOT VERIFIED');
const staged = await call('stage_algebraic_candidate',{specification_json:initial});
assert.equal(staged.status,'CANDIDATE_SPEC');
assert.equal(staged.certificate_verified,undefined);
const current = $('spec').value;
await assert.rejects(call('stage_algebraic_candidate',{specification_json:'{}'}));
assert.equal($('spec').value,current);
assert.match($('agent-events').children.at(-1).textContent,/FAILED/);
await assert.rejects(call('compile_staged_algebraic_model',{expected_specification_json:current+' '}),/SPEC_CHANGED/);
assert.equal($('bundle').value,'');
assert.match($('agent-events').children.at(-1).textContent,/FAILED/);
const compiled = await call('compile_staged_algebraic_model',{expected_specification_json:current});
assert.equal(compiled.replay.certificate_verified,true);
assert.match($('agent-events').children.at(-1).textContent,/VERIFIED.*obstruction.vanishes/);
assert.equal($('agent-events').children.at(-1).children[0].textContent,'Input SHA-256 · '+compiled.view.input_digest);
const replay = await call('replay_current_algebraic_bundle');
assert.equal(replay.replay.certificate_verified,true);
assert.equal($('spec').value,replay.view.specification);
// A rejected bundle must never appear as successful verification.
$('bundle').value = '{}';
const rejected = await call('replay_current_algebraic_bundle');
assert.equal(rejected.replay.certificate_verified,false);
assert.match($('agent-events').children.at(-1).textContent,/REJECTED/);
for (let i=0;i<10;i++) await call('get_algebraic_workspace');
assert.equal($('agent-events').children.length,8);
assert.equal($('status').textContent,'INDEPENDENT REPLAY · REJECTED');
assert.equal($('agent-session').open,true);
console.log('Agent session check passed: stage, changed-input refusal, compile, replay, rejection, bounded log.');
