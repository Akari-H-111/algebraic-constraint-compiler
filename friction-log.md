# Integration friction log

Show Your Work is an MCP server with an MCP Apps certificate card and a simulated Alexa+ smart display that checks math homework with exact, independently verified certificates. Only problems we actually observed are logged here, and later fixes are noted beside them. This project claims no live AWS usage: Amazon Bedrock is implemented as an optional model provider and unit-tested with scripted responses, but no AWS account, credits or live Bedrock call were used, and no Alexa account is connected (the Alexa+ experience is a simulation). The highlights below are the entries most useful to the Amazon developer teams, rewritten in one template. The full chronological log under them is the original record, unchanged. Per-tool feedback is in [docs/product-feedback.md](docs/product-feedback.md).

## Highlights for the Amazon developer teams

### 1. Alexa+ add-on developer tools are preview-only, and there is no route to attach a self-hosted MCP server (recorded 2026-10-21 from the organizer FAQ)

- **Task**: Connect our self-hosted Streamable HTTP MCP server (protocol 2025-11-25) to Alexa+, on a real or test account, for the Alexa+ track.
- **Steps**: (1) Worked from the hackathon Overview and Resources pages, which link the Alexa+ Agent Skills and Streamable HTTP documentation, Amazon developer tools and samples. (2) Looked for a way to register or test the server against Alexa+. (3) Found no route open to participants, and we hold no Alexa+ Preview admission. (4) Built a simulated web host instead (2026-09-29). The organizer FAQ states the same limits.
- **Expected**: A documented way for a hackathon participant to attach a self-hosted MCP server to a test Alexa+ account or developer console, or a clear statement, on every page a developer might start from, that none exists.
- **Actual**: No such route. Per the official FAQ, the Alexa+ Add-on tools (Category SDK, MCP Toolkit, CLI, Web Simulator) are preview or select-partners only, participants cannot get access, and a self-hosted MCP server cannot be connected to a real Alexa+ device or developer console. The FAQ says this is called out on the add-on docs home page but "sometimes missing from individual setup-guide pages, which has caused some confusion". We did not record which individual pages we read, so we cannot say which ones lacked the notice.
- **Severity**: High. It closes the track's main integration path, a real Alexa+ call. It did not block the entry, because the rules allow an agentic simulated web experience.
- **Workaround**: A simulated Alexa+ smart display whose Python side is a hand-written MCP client (`mcp_client.py`: `initialize`, `tools/list`, `tools/call`, `resources/read` over Streamable HTTP). It is labelled a simulation everywhere. No Alexa account is connected and no Amazon service is called.
- **Suggestion**: To the Alexa+ Add-on developer documentation team: put the "preview / select partners only" notice at the top of every setup-guide page, and link a "what you can build without access" page. To the Alexa+ Add-on program: open a public sandbox or test-account path for registering a self-hosted Streamable HTTP MCP server.

### 2. A certified "no" needs its own status all the way to speech (2026-09-29)

- **Task**: Voice a worksheet problem whose only solution is −12 tickets, as an Alexa+-style spoken reply.
- **Steps**: Solve the rational system exactly, then check the domain (whole numbers, 0 or more).
- **Expected**: A clear spoken answer that is neither "error" nor a guess.
- **Actual**: The first design folded the domain failure into a generic rejection, so the assistant could not say why the problem was impossible.
- **Severity**: High. Product-critical for trust in our skill. This was our design gap, not a defect in a product.
- **Workaround**: A separate claim, `solve.no_solution_in_domain`. The certificate carries the unique-solution derivations plus the violating unknown, and the view says "no valid answer: the only exact solution gives −12 child tickets". Certified rejections return `isError: false` with a structured status; only assumption-required, infrastructure and error results set `isError: true`.
- **Suggestion**: To the Alexa+ MCP developer documentation team: add tool-design guidance that findings such as "no solution" are normal tool results with a structured status, not `isError`, and document how Alexa+ speaks a tool result.

### 3. MCP Apps card stayed at its initial height in a background pane (2026-09-29)

- **Task**: Render the `ui://` certificate card in our own MCP Apps web host (the simulated Alexa+ display), sized by the view's `ui/notifications/size-changed`.
- **Steps**: Host page, then a cross-origin sandbox proxy (`127.0.0.1` and `localhost`), then an inner `srcdoc` iframe with injected CSP; the view measured `scrollHeight` after rendering.
- **Expected**: The host receives the content height and grows the iframe.
- **Actual**: When the browser pane was hidden (`visibilityState: hidden`), the nested cross-origin frame reported height 0 or never reported. A second card created in the hidden pane rendered (a view log message confirmed it) but was not painted in captures.
- **Severity**: High for kiosk and smart-display hosts. No data or verification loss.
- **Workaround**: The host passes a fixed `containerDimensions.height` (allowed by the spec), the sandbox's inner frame fills it, and the view scrolls inside. Visual QA moved to headless Chrome driven through the DevTools protocol, which renders out-of-process frames.
- **Suggestion**: To the MCP Apps (SEP-1865 / ext-apps) authors: recommend fixed containers for smart-display and kiosk hosts, and warn that size notifications depend on layout, which hidden documents may skip. To the Alexa+ team: if MCP Apps views are supported on Echo Show, publish the container sizes.

### 4. Reference MCP Apps examples assume a CDN or a TypeScript dev server (2026-09-29)

- **Task**: Build a view and a host that work offline under a strict CSP.
- **Steps**: Read the SEP-1865 spec (2026-01-26) and the ext-apps repository examples.
- **Expected**: A dependency-free view client and a static sandbox proxy page.
- **Actual**: The Python examples import the App SDK from unpkg inside the view. The reference sandbox (`examples/basic-host`) takes its CSP from HTTP headers set by its TypeScript server.
- **Severity**: Medium. Nice-to-have for most hosts; important for strict-CSP or offline hosts.
- **Workaround**: A roughly 60-line `postMessage` JSON-RPC client in the card, and a static sandbox proxy that injects the CSP as a `<meta>` tag into the inner `srcdoc` frame.
- **Suggestion**: To the MCP Apps (ext-apps) maintainers: publish a no-CDN view client snippet and a static sandbox-proxy page that injects the CSP in the page.

### 5. MCP Python SDK returned no structured output on the first real HTTP call (2026-09-14)

- **Task**: Return the compiler run as machine-readable MCP structured content.
- **Steps**: Install official SDK 1.30.0 in an isolated Python 3.12 environment; initialize 2025-11-25; discover tools; compile the exact fixture over HTTP.
- **Expected**: `CallToolResult.structuredContent` contains the compilation run.
- **Actual**: Handshake and discovery succeeded, but unparameterized `dict` return annotations gave text content and `structuredContent=None`; the transport test failed. The older `streamablehttp_client` name also emitted a deprecation warning.
- **Severity**: High. It blocks reliable machine interpretation downstream.
- **Workaround**: Inspect the installed SDK's output-schema inference, declare `dict[str, Any]` with `structured_output=True`, and use `streamable_http_client`. Result: structured content returned, and the renamed client completed the real 2025-11-25 handshake, both compiler outcomes, independent replay, and forged-certificate rejection.
- **Suggestion**: To the MCP Python SDK maintainers: show a nested JSON dictionary return plus a real client assertion on structured content in the examples, and say plainly when a return type yields text-only output.

### 6. No provider key and a zero budget: Amazon Bedrock was implemented but never run (2026-09-29)

- **Task**: Run the simulated Alexa+ agent loop end to end with a real model.
- **Steps**: Implemented Bedrock Converse (API-key bearer or SigV4), Claude via the official SDK, and Gemini via its OpenAI-compatible endpoint; checked the environment for credentials.
- **Expected**: At least one provider available for local testing.
- **Actual**: No provider key was configured on the development machine, and the project budget is zero. The agent loop was covered by a scripted provider over real MCP HTTP. Later the same day a free-tier Gemini key worked and the Anthropic key returned "credit balance too low". Bedrock was never called: no AWS account or credits were used.
- **Severity**: Medium. Important for the demo, not for correctness (math never passes through the model).
- **Workaround**: Guided mode sends preset typed requests to the same MCP tools, labelled as involving no AI interpretation. The recorded live agent demo used Gemini.
- **Suggestion**: To the Amazon Bedrock team and the hackathon credits program: document a zero-card trial path for Converse with Nova, so solo entrants can demonstrate Nova-driven Alexa+ simulations.

### 7. Free-tier Gemini capacity shaped the live agent demo (2026-09-29)

- **Task**: Record the simulated Alexa+ agent live with Gemini (`gemini-2.5-flash`).
- **Steps**: Real agent loop over MCP; several turns per scene; narration with Gemini TTS.
- **Expected**: Steady responses within a few seconds.
- **Actual**: Intermittent HTTP 503 ("model is currently experiencing high demand"). Free-tier daily per-model caps were reached during recording: 20 per day for `gemini-2.5-flash`, 10 per day per TTS model. The fallback model answered once with a tool call but an empty spoken reply.
- **Severity**: Medium. Important for demos; no effect on correctness.
- **Workaround**: Retries with short backoff, a fallback model list, and speaking the tool's verified suggested speech when the model says nothing (tested). The recorder retries failed turns and jump-cuts model latency in the edit while the on-screen timeline keeps the real milliseconds.
- **Suggestion**: To the Gemini API team: publish a free demo tier with higher daily limits, or hackathon credits, so solo entrants can record live agent demos reliably.

### 8. A newer Gemini TTS model spoke its style instructions (2026-09-29)

- **Task**: Narrate the demo with a newer Gemini TTS model using a style prefix ("Read this as a calm narrator: ...").
- **Steps**: Generate narration clips with the style prefix placed before the text; measure each clip's length and loudness envelope.
- **Expected**: The prefix sets the tone and is not spoken.
- **Actual**: The envelope showed about 10 extra seconds of speech per clip. The earlier model (`gemini-3.1-flash-tts-preview`) had honoured the same prefix; our notes name `gemini-3.8-flash-tts` as the model that spoke it.
- **Severity**: Low. Caught before publishing by measuring clip lengths and envelopes.
- **Workaround**: Kept the earlier model for narration and checked transcripts with a separate model.
- **Suggestion**: To the Gemini API speech-generation documentation team: state per-model support for style prompts, or offer a separate style field.

## Full chronological log (platform entries)

Entries about our own local test tooling were removed from this public copy; every entry below is about an external tool, API or platform.

The entries below are the original record, unchanged.

## 2026-09-14 — Browser WebMCP is not an HTTP MCP endpoint

- Attempted task: reuse the current agent tool path for the compiler.
- Steps: inspect Taste Gate tool registration, request schemas, and deployment files.
- Expected result: identify an existing Streamable HTTP endpoint to extend.
- Actual result: only `document.modelContext` page tools exist; no HTTP server or AWS runtime configuration.
- Severity: important integration gap, not an Amazon service defect.
- Workaround: retain the page tools and add an optional thin HTTP MCP adapter around the tested compiler.
- Actionable suggestion: onboarding examples should distinguish browser WebMCP, HTTP MCP, and deployed Alexa+ integration, with separate smoke-test instructions.

## 2026-09-14 — MCP structured output was absent on first real HTTP call

- Attempted task: return the compiler run as machine-readable MCP structured content.
- Steps: install official SDK 1.30.0 in an isolated Python 3.12 environment; initialize 2025-11-25; discover tools; compile the exact fixture over HTTP.
- Expected result: `CallToolResult.structuredContent` contains the compilation run.
- Actual result: handshake/discovery succeeded, but unparameterized `dict` return annotations yielded text content and `structuredContent=None`; the transport test failed. The older `streamablehttp_client` name also emitted a deprecation warning.
- Severity: important; blocks reliable downstream machine interpretation.
- Workaround: inspect installed SDK output-schema inference, declare explicit structured output and typed dictionaries, and use `streamable_http_client`.
- Actionable suggestion: SDK examples should demonstrate a nested JSON dictionary return plus a real client assertion on structured content, and distinguish text-only output from structured output.
- Resolution: `dict[str, Any]` plus `structured_output=True` returned structured content; the renamed client completed the real 2025-11-25 handshake, both compiler outcomes, independent replay, and forged-certificate rejection.

## 2026-09-27 — Browser agent tools and artifact readback

- Task: run an actual agent read/stage/compile/replay workflow at zero API cost.
- Steps: register four WebMCP tools; fetch available names; stage exact text;
  compile and replay both order-two fixtures; download the negative certificate.
- Expected: tool names and download events directly match the page registration.
- Actual: browser host decorates names with per-document identifiers; an initial
  unsuffixed name failed. Fresh discovery gave callable names. Changed-model
  compilation was deliberately rejected. Download-event waiter again timed out,
  but a local file existed and CLI replay verified
  obstruction.nonzero. No failed call or timeout counted as success by itself.
- Severity: automation discoverability/observability; no mathematical failure.
- Workaround: discover exact host names after reload; verify downloaded artifacts
  independently rather than inferring from an event timeout.
- Suggestion: host errors should preserve SPEC_CHANGED and other page-tool error
  text, and expose successful blob downloads consistently.

## 2026-09-27 — Actual browser recording required painted-state verification

- Task: capture the functioning workbench and actual host-agent calls for an
  English demo, using page-only screencast footage.
- Actual: a recorder left running between automation calls lost its tab/session;
  that incomplete attempt was not counted as a deliverable. A fast stage call
  also updated the DOM before its captured final image showed the new state.
  Screenshot images had twice the screencast resolution; initial subtitles
  overflowed the reserved caption strip.
- Severity: recording/automation quality, not mathematical computation failure.
- Workaround: await each complete recording operation; inspect rendered frames;
  capture a stable staged-state image; normalize footage to 1280×720 before
  encoding; wrap captions in a separate 120-pixel strip. All displayed outcomes
  came from actual calls. The recorded positive and negative bundles separately
  passed CLI replay. Local media tools are isolated from product dependencies.

## 2026-09-27 — Narration quality and Gemini TTS replacement

- Task: make a judge-facing English demo worth watching within a zero budget.
- Actual: the user rejected the native Samantha-voice videos. They are historical
  QA artifacts, not approved upload candidates. Gemini 3.1 Flash TTS Preview,
  Zephyr, generated eight replacement narration clips in the signed-in AI Studio
  UI, with no paid API key selected and no upgrade or billing action.
- Observed automation friction: a download-event waiter timed out while a valid
  WAV was saved in a local folder. Existing-dialog Run state became unreliable after
  edits; a fresh dialog per clip worked. A UI wait timed out while generation
  remained active; that same operation was polled to completion, not restarted.
- Workaround: inspect terminal generation state, use the Download control, then
  verify each distinct local WAV header, duration and SHA-256 before editing.
  The replacement uses actual captured product results and recorded HTTP replies.
- Severity: presentation quality and automation observability; no mathematics
  changed. Voice/video acceptability still requires the user's review.
- Suggestion: expose stable generation/download states, and audition narration
  before finalizing a demo edit. Do not infer quality from a successful encoder.

## YouTube Studio upload controls — September 27

Task: publish the user-approved English demo and SRT on the owned channel.
Steps: Studio Create, Select files, file-chooser wait, subtitle import, publish.
Expected: button click opens its menu/chooser. Actual: several click actions
returned without a visible transition; the chooser wait timed out. Severity:
moderate (blocks upload until recovered). Workaround: the direct YouTube upload
entry opened the dialog; keyboard Space triggered Select files and keyboard
Enter activated subtitle import and publish. Upload was performed once; public
player, English subtitle selection and unauthenticated oEmbed then succeeded.
Raw CDP file assignment was unsupported and did not upload anything. Suggestion:
make click completion report a confirmed control transition or a clear failure;
catch event-wait errors to preserve the browser automation session.

## 2026-09-29: MCP Apps card stayed at its initial height in a background pane

- Attempted task: render the `ui://` certificate card in our own MCP Apps web host
  (the simulated Alexa+ display), sized by the view's `ui/notifications/size-changed`.
- Steps: host page → cross-origin sandbox proxy (`127.0.0.1` ↔ `localhost`) → inner
  `srcdoc` iframe with injected CSP; the view measured `scrollHeight` after rendering.
- Expected: the host receives the content height and grows the iframe.
- Actual: when the browser pane was hidden (`visibilityState: hidden`), the nested
  cross-origin frame reported height 0 or never reported. A second card created in the
  hidden pane rendered (confirmed by a view log message) but was not painted in captures.
- Severity: important for kiosk and smart-display hosts. No data or verification loss.
- Workaround: the host passes a fixed `containerDimensions.height` (allowed by the spec),
  the sandbox's inner frame fills it, and the view scrolls inside. Visual QA moved to a
  headless Chrome instance driven through the DevTools protocol, which renders out-of-process frames.
- Actionable suggestion: the MCP Apps spec or SDK could recommend fixed containers
  for smart-display hosts and warn that size notifications depend on layout, which
  hidden documents may skip.

## 2026-09-29: A certified "no" needs its own status all the way to speech

- Attempted task: voice a worksheet problem whose only solution is −12 tickets.
- Steps: solve the rational system, then check the domain (whole numbers ≥ 0).
- Expected: a clear spoken answer that is neither "error" nor a guess.
- Actual: the first design folded domain failure into a generic rejection, so the
  assistant could not say *why* the problem was impossible.
- Severity: product-critical for trust.
- Workaround: a separate claim `solve.no_solution_in_domain`. The certificate carries the
  unique-solution derivations plus the violating unknown, and the view says "no valid
  answer: the only exact solution gives −12 child tickets".
- Actionable suggestion: tool-design guidance for Alexa+ skills should encourage
  distinct, speakable negative outcomes instead of `isError` for findings.

## 2026-09-29: The model-provider key is the only gate for a live agent demo

- Attempted task: run the simulated Alexa+ agent loop end to end with a real model.
- Steps: implemented Bedrock Converse (API-key bearer or SigV4), Claude via the official SDK,
  and Gemini via its OpenAI-compatible endpoint; checked the environment for credentials.
- Expected: at least one provider available for local testing.
- Actual: no provider key is configured on the development machine, and the project budget is zero.
  The agent loop is covered by a scripted provider over real MCP HTTP. Live provider
  calls are not claimed.
- Severity: important for the demo, not for correctness (math never passes through the model).
- Workaround: Guided mode sends preset typed requests to the same MCP tools, labelled as involving
  no AI interpretation.
- Actionable suggestion: hackathon credits for Bedrock with a zero-card sandbox path
  would let solo entrants demonstrate Nova-driven Alexa+ simulations.

## 2026-09-29: Reference MCP Apps examples assume a CDN or a TypeScript dev server

- Attempted task: build a view and a host that work offline under a strict CSP.
- Steps: read the SEP-1865 spec (2026-01-26) and the ext-apps repository examples.
- Expected: a dependency-free view client and a static sandbox proxy page.
- Actual: the Python examples import the App SDK from unpkg inside the view. The reference
  sandbox (`examples/basic-host`) takes its CSP from HTTP headers set by its TypeScript server.
- Severity: nice-to-have for most hosts; important for strict-CSP or offline hosts.
- Workaround: a roughly 60-line `postMessage` JSON-RPC client in the card, and a static sandbox
  proxy that injects the CSP as a `<meta>` tag into the inner `srcdoc` frame.
- Actionable suggestion: publish a no-CDN view client snippet and a static sandbox page.

## 2026-09-29: Free-tier model capacity shaped the live agent demo

- Attempted task: record the simulated Alexa+ agent live with Gemini (gemini-2.5-flash).
- Steps: real agent loop over MCP; several turns per scene; narration with Gemini TTS.
- Expected result: steady responses within a few seconds.
- Actual result: intermittent HTTP 503 ("model is currently experiencing high demand"). Free-tier daily
  per-model caps were reached during recording: 20 per day for gemini-2.5-flash, 10 per day per TTS model.
  The fallback model answered once with a tool call but an empty spoken reply.
- Severity: important for demos; no effect on correctness (math never passes through the model).
- Workaround: retries with short backoff, a fallback model list, and speaking the tool's verified
  suggested speech when the model says nothing (tested). The recorder retries failed turns and
  jump-cuts model latency in the edit while the on-screen timeline keeps the real milliseconds.
- Actionable suggestion: hackathon model credits, or a documented free "demo" tier with higher daily
  limits, would let solo entrants record live agent demos reliably.

## 2026-09-29: A newer TTS model spoke its style instructions

- Attempted task: narrate the demo with a newer Gemini TTS model using a style prefix
  ("Read this as a calm narrator: ...").
- Expected result: the prefix sets the tone and is not spoken.
- Actual result: the loudness envelope showed about 10 extra seconds of speech per clip. The earlier
  TTS model (gemini-3.1-flash-tts-preview) had honoured the same prefix.
- Severity: nice-to-have; caught before publishing by measuring clip lengths and envelopes.
- Workaround: kept the earlier model for narration and verified transcripts with a separate model.
- Actionable suggestion: document per-model support for style prompts, or offer a separate style field.

## 2026-10-21: A vision model read an ambiguous digit confidently instead of flagging it

- Attempted task: read a photographed line of homework where the child's 5 looks like an S ("3x = 1S"),
  and flag it for review rather than choose a reading.
- Steps: sent a rendered sample sheet to gemini-2.5-flash through the OpenAI-compatible chat endpoint
  (`image_url` data URL), with photo rules asking for `review: {reason: "AMBIGUOUS_SYMBOL"}` on any symbol
  that could be read two ways; three attempts, the wording tightened between them.
- Expected result: line 2 comes back flagged, so the exact compiler holds it instead of judging it.
- Actual result: all three attempts read the line as `3x = 15` with no flag. One attempt also returned its
  boxes y-first ([top, left, ...]) despite an explicit [left, top, right, bottom] instruction, and box
  positions varied by tens of units on a 0-1000 scale between runs.
- Severity: medium. The math stays sound (an unflagged line is checked exactly as read, and the display
  shows the reading back with each line's box drawn on the photo), but the review state only helps when
  the reader admits doubt.
- Workaround: the display always shows "What I understood — please check" next to the boxed photo. The
  unclear-photo Guided example uses a reading written by hand and labelled as such, not a model output.
- Actionable suggestion: to the Gemini API team: expose per-span confidence (or a documented convention
  for "uncertain" spans) in vision transcription, and accept a requested box order in function-call
  arguments. To ourselves: ask the reader for alternative readings per line and treat disagreement as review.

## 2026-10-21: A browser page as a real MCP client of the hosted server

- Attempted task: make the public web demo an actual MCP client (`initialize`, `tools/list`,
  `resources/read`, `tools/call` over Streamable HTTP) of the hosted server, as the organizer FAQ describes.
- Steps: a ~120-line standard-JavaScript client on GitHub Pages calling the Render-hosted FastMCP server
  (stateless, JSON responses) cross-origin; preflight for `content-type` and `mcp-protocol-version`.
- Expected result: browser calls succeed once CORS allows the Pages origin.
- Actual result: worked first time with Starlette's `CORSMiddleware` (`allow_origins=["*"]`, exposing
  `Mcp-Session-Id` and `Mcp-Protocol-Version`). The friction was the free instance: after idling it can take
  about 50 seconds to answer, longer than a visitor waits.
- Severity: low for correctness, important for first impressions.
- Workaround: the page connects while Pyodide loads, falls back to the same engine in the browser (and says
  so) while the server wakes, and pings `/healthz` every five minutes while a visitor has the page open.
- Actionable suggestion: to the MCP specification authors: a short, normative note on CORS for Streamable
  HTTP servers meant for browser clients (which request and response headers to allow and expose).
