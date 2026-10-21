# Product feedback

Show Your Work (v0.5.0) is an MCP server with an MCP Apps certificate card and a simulated Alexa+ smart display. Track: Alexa+. This file covers every tool, API and SDK we used. Sections for Amazon products come first. The matching problems are in [friction-log.md](../friction-log.md).

**AWS usage, stated plainly.** We ran no AWS service. Amazon Bedrock Converse (Amazon Nova) is implemented as an optional model provider in `algebraic_compiler/llm.py` and unit-tested with scripted responses. No AWS account, credits or live Bedrock call were used. No Alexa account is connected either: the Alexa+ experience is a simulation. Where a section covers something we did not run, it says so, and its feedback is limited to what we could read and build against.

## 1. Alexa+ developer path and docs (Agent Skills, MCP integration, preview gating, simulated experience)

**Used for.** Choosing the integration contract for the Alexa+ track: self-hosted MCP over Streamable HTTP, protocol 2025-11-25. We built the simulated route the rules allow.

**Worked well.** The rules name a protocol version and transport, a concrete target. The FAQ confirmed a web page acting as an MCP client satisfies it.

**Needs work.** The Add-on tools (Category SDK, MCP Toolkit, CLI, Web Simulator) are preview or select-partners only. Participants cannot get access. The FAQ says some setup pages omit that notice. No test path exists for self-hosted servers. We have no page-level notes on the Agent Skills docs.

**Onboarding (zero to hello world).** Not reached; we had no access. Our first working call was `initialize` against our own server.

**Build with it again?** Yes, if a public test path appears. Without one we cannot test against it.

## 2. Amazon Bedrock Converse API / Amazon Nova

**Used for.** An optional agent provider (`Bedrock` in `llm.py`): POST to `/model/{id}/converse` with `toolConfig.tools[].toolSpec`, default `us.amazon.nova-lite-v1:0`, auth by Bedrock API key or our own SigV4 signing. Never called live.

**Worked well.** Nothing observed; no call was made. The `text`, `toolUse` and `toolResult` blocks mapped easily onto our tool loop in scripted tests.

**Needs work.** We cannot report errors, latency, quotas or model access. Our SigV4 test checks credential scope and signed headers, not a signature AWS accepted. We cut tool descriptions to 1,000 characters without confirming Bedrock's limit.

**Onboarding (zero to hello world).** Not completed. We never created a Bedrock key or IAM credentials, so no first call and no time.

**Build with it again?** Can't say; we never ran it. We would try it first if a no-card trial existed.

## 3. Model Context Protocol 2025-11-25, Streamable HTTP

**Used for.** The link between the simulated host and our server, plus `mcp_client.py`, a 109-line standard-library client doing `initialize`, `notifications/initialized`, `tools/list`, `tools/call` and `resources/read`.

**Worked well.** A stateless server returning plain JSON is valid, so our server needs no SSE. Our client still accepts JSON or SSE. The spec sufficed for a library-free client.

**Needs work.** CORS for browser clients is outside what we found in the spec. We added a `--cors` flag exposing `Mcp-Session-Id` and `Mcp-Protocol-Version`; our public web page now calls the hosted server cross-origin with it (`initialize`, `tools/list`, `resources/read`, `tools/call`). A normative note on which headers to allow and expose would save every browser-client author the guesswork.

**Onboarding (zero to hello world).** On 2026-09-14 the SDK client's `initialize` and tool discovery worked on the first real HTTP attempt. The first failure was structured output (section 4). No time recorded.

**Build with it again?** Yes. Plain JSON-RPC over POST is easy to implement and test.

## 4. MCP Python SDK 1.30.0 (FastMCP)

**Used for.** The whole server: `FastMCP` with `stateless_http=True` and `json_response=True`, ten tools (one app-only), four prompts, one `ui://` resource and `/healthz`, served by `streamable_http_app()` and uvicorn, or over stdio.

**Worked well.** `meta=` on `@server.tool` and `@server.resource` was enough for MCP Apps linkage. Returning `CallToolResult(content, structuredContent, isError)` separates what the model reads from what the card renders.

**Needs work.** (1) A bare `dict` return gave `structuredContent=None` on the first real HTTP call; `dict[str, Any]` fixed it. (2) `streamablehttp_client` warned as deprecated. (3) The package needs Python 3.10+, so our core stays dependency-free for 3.9. (4) For public hosts we wrote our own allowed-hosts wiring.

**Onboarding (zero to hello world).** Installing 1.30.0 in a Python 3.12 venv, `initialize` and tool discovery worked on 2026-09-14. The structured-output fix followed. No time recorded.

**Build with it again?** Yes.

## 5. MCP Apps extension (SEP-1865, ext-apps)

**Used for.** The certificate card. Server side: `_meta.ui.resourceUri`, `visibility` (`["model","app"]` or `["app"]`), a `text/html;profile=mcp-app` resource. We built the host ourselves: sandbox proxy, CSP injection, `ui/initialize`, `ui/notifications/size-changed`.

**Worked well.** The spec was precise enough to implement a host from the document alone. Visibility gave us a clean app-only tool.

**Needs work.** The reference Python examples load the App SDK from unpkg inside the view. `examples/basic-host` takes its CSP from headers set by a TypeScript server. We wrote a 60-line `postMessage` client and a static sandbox page. In a hidden pane, nested frames reported height 0.

**Onboarding (zero to hello world).** One afternoon to a rendered card in our host, after reading the spec. Server side was about 20 lines; the host was the work.

**Build with it again?** Yes. It fits tools whose results need a UI.

## 6. Gemini API (OpenAI-compatible chat, Gemini TTS)

**Used for.** Live agent turns via `/v1beta/openai/chat/completions` (`gemini-2.5-flash`, fallbacks `gemini-2.5-flash-lite`, `gemini-3.5-flash`) speech via `generateContent` with `responseModalities: ["AUDIO"]`, voices Kore and Zephyr, and reading photos of homework (an `image_url` data URL in the same chat call; the model returns each line plus its box). Used live in the demo video.

**Worked well.** Function calling through the compatible endpoint needed no Gemini-specific code. One TTS request returns base64 PCM, with the sample rate in `inlineData.mimeType`.

**Needs work.** Intermittent HTTP 503 ("model is currently experiencing high demand"). Free-tier daily caps: 20 requests for `gemini-2.5-flash`, 10 per TTS model. One fallback reply had a tool call but empty text. `gemini-3.8-flash-tts` spoke our style prefix aloud; `gemini-3.1-flash-tts-preview` did not. Reading photos, `gemini-2.5-flash` read an ambiguous "1S" as "15" without flagging it in three of three tries, and once returned boxes y-first despite an x-first instruction; per-span confidence would let hosts route doubtful lines to review.

**Onboarding (zero to hello world).** A key sent as a Bearer token, then one chat call. The key format starts with `AQ.` and worked. No time recorded.

**Build with it again?** Yes for prototypes. For live demos we would need paid capacity.

## 7. Anthropic Claude API and official Python SDK

**Used for.** An optional agent provider (`Anthropic` in `llm.py`): `client.beta.messages.create` with tools, `output_config={"effort": "low"}` and a server-side fallback beta. Default model `claude-opus-5-5`. Not run live.

**Worked well.** The API answered with a clear message naming the cause: "credit balance too low". Tool-use blocks map directly onto our loop, but only scripted tests exercised that.

**Needs work.** The SDK reads `ANTHROPIC_BASE_URL` from the environment. Ours belonged to another tool's proxy, so we pin `base_url` and the key. No successful live call was ever made, so we have no verdict on tool use.

**Onboarding (zero to hello world).** `pip install -e '.[claude]'`, set `ANTHROPIC_API_KEY`, call. The first call failed with "credit balance too low". Hello world was not reached.

**Build with it again?** Can't say from experience. We would retry once credit is added.

## 8. Web Speech API (SpeechRecognition) in Chrome

**Used for.** Listening in the simulated display: `window.SpeechRecognition || window.webkitSpeechRecognition`, `lang = 'en-US'`, `interimResults = true`, `continuous = false`. Space is push-to-talk, and the final transcript is submitted as the request.

**Worked well.** Interim results show the words as they are heard, in a few lines of code. Feature detection is simple: without the constructor we disable the mic and ask the user to type.

**Needs work.** We have no accuracy data for spoken algebra, and our tests and headless QA do not exercise the microphone. Browser support varies, so a typed fallback is required.

**Onboarding (zero to hello world).** Construct the recognizer, set the properties, `start()`, handle `onresult` and `onend`. No time recorded and no onboarding problem recorded.

**Build with it again?** Yes, with a typed fallback.

## 9. Pyodide 0.28.3

**Used for.** Running our real Python compiler and verifier in the browser for the GitHub Pages demo. `static-backend.js` loads `pyodide.js` from jsDelivr, calls `loadPyodide({packages: ['sqlite3']})`, writes our engine files into the virtual filesystem, and calls a Python function.

**Worked well.** Our unmodified standard-library code (`fractions`, `hashlib`, `sqlite3`, `json`) ran as is. Warm start is about 1.2 s.

**Needs work.** The first visit downloads about 6 MB, so we show progress and preconnect to the CDN. `sqlite3` must be named in `packages`. We did not add persistence: the notebook lives in `/tmp` of the virtual filesystem.

**Onboarding (zero to hello world).** Load the script, `loadPyodide`, write the files, `runPython` an import of our package. No time recorded.

**Build with it again?** Yes. It gave a no-install demo running the real engine, with no server.

## 10. Render free web service (Docker)

**Used for.** The public MCP endpoint `https://show-your-work-mcp.onrender.com/mcp`, built from our `Dockerfile` (`python:3.12-slim`) on the free plan. `deploy/render.yaml` sets the `/healthz` health check.

**Worked well.** A Docker image from a public repo became an HTTPS endpoint. After a 29 s deploy, `/healthz` reported version 0.4.0 and a remote `check_work` replayed locally.

**Needs work.** The free instance sleeps; the first request can take about 50 s. With a public repo and no GitHub app grant there is no auto-deploy; we press Manual Deploy. The `/tmp` notebook is per instance. Docker was unavailable locally, so Render ran the first build.

**Onboarding (zero to hello world).** The user made the account and a Docker service from the public repo. `initialize` on the hosted URL returned 2025-11-25.

**Build with it again?** Yes for demos. Not for stored data or flows that cannot wait.

## 11. GitHub Pages and GitHub Actions

**Used for.** Pages serves the static demo from the `gh-pages` branch, built by `tools/site/build_site.py` (with `.nojekyll`). Actions runs `ci.yml`: a core job on Python 3.9 and 3.12, and an MCP job on 3.12 that fails if any test is skipped.

**Worked well.** Static hosting plus Pyodide gave a free no-install demo URL. The matrix covered our 3.9 floor and the SDK's 3.10+ need. The no-skips check stopped HTTP tests from skipping silently.

**Needs work.** No GitHub defect to report. The costs were ours: the demo is a separate branch we rebuild and push by hand, and replacing its tree would drop the hand-placed `thumbnail.png`.

**Onboarding (zero to hello world).** Push a built site to `gh-pages` and Pages serves it. No time recorded and no onboarding problem recorded.

**Build with it again?** Yes.

## 12. Chrome DevTools Protocol via plain Node

**Used for.** Visual QA and demo capture. `tools/cdp.mjs` (about 100 lines, Node 22+) launches headless Chrome with a throwaway profile, then uses `Page.navigate`, `Page.captureScreenshot` (with `clip`) and `Runtime.evaluate` over Node's built-in WebSocket.

**Worked well.** It needs no packages. Headless Chrome painted the nested cross-origin frames that a hidden in-app browser pane did not (2026-09-29).

**Needs work.** Our script waits with fixed sleeps rather than a paint signal, and we checked screenshots by eye. It finds the page by polling `/json/list` until Chrome is up.

**Onboarding (zero to hello world).** Spawn Chrome, poll `/json/list` for the page's WebSocket URL, send `Page.navigate`, then `Page.captureScreenshot`. No time recorded.

**Build with it again?** Yes. It is small and needs nothing installed.

## Feature requests

Ordered Alexa+ first. Each comes from a problem recorded in the friction log.

### 1. A public test path for self-hosted MCP servers on Alexa+

- **What:** A sandbox or test-account route, open to all developers, to register a self-hosted Streamable HTTP MCP server and see Alexa+ call it.
- **Why it matters:** The Add-on tools are preview or select-partners only, and a self-hosted server cannot be connected to a real device or developer console. We could build only a simulated host, so we could not check how Alexa+ treats our tools or results.
- **Urgency:** Critical.

### 2. A preview-only notice on every add-on setup page

- **What:** Put "preview / select partners only" at the top of each add-on setup-guide page, with a link to what developers can do without access.
- **Why it matters:** The FAQ says the notice is on the docs home page but sometimes missing from setup pages, which has caused confusion. A reader on a setup page should not have to guess.
- **Urgency:** Important.

### 3. A statement on MCP Apps support for Alexa+ displays

- **What:** Say whether Alexa+ renders `ui://` MCP Apps views, on which devices (such as Echo Show), at what container sizes, and with what input (touch, voice).
- **Why it matters:** We built the card to SEP-1865 with a fixed container for a smart display, but could not test it. Size notifications failed in a hidden pane, so we moved to a fixed container.
- **Urgency:** Important.

### 4. Tool-design guidance for speakable findings

- **What:** Guidance that a certified "no" (no solution, wrong answer) should be a normal tool result with a structured status, not `isError`, and how Alexa+ speaks a tool result.
- **Why it matters:** Our first design folded "the only answer is −12 tickets" into a generic rejection, so the assistant could not say why. We added a separate status to fix it.
- **Urgency:** Important.

### 5. A dependency-free view client and static sandbox page in ext-apps

- **What:** A no-CDN client snippet for `ui://` views and a static sandbox-proxy page that injects the CSP in the page.
- **Why it matters:** The Python examples load the App SDK from unpkg, and the reference host takes its CSP from a TypeScript server's headers. We had to write both ourselves for a strict-CSP, non-TypeScript host.
- **Urgency:** Important.

### 6. A zero-card trial path for Bedrock Converse with Nova

- **What:** A way for solo developers to try Converse with a tool-use call without a payment card or paid credits.
- **Why it matters:** With a zero budget we implemented Bedrock but never ran it, so we cannot tell other developers whether it works.
- **Urgency:** Nice-to-have.
