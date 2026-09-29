# Integration friction log

Only observed problems belong here; this is not a claim of deployed AWS usage.

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

## 2026-09-14 — Standalone takeover observations

- Git has no commits or remote; project files are untracked. This is not a published repository.
- The first archive read assumed root-level members and raised KeyError. CRC passed; members actually live under `algebraic_constraint_compiler_handoff/`. Read using that observed prefix; no extraction or source modification occurred.
- PATH exposes Python 3.9 only; locate a Python >=3.10 runtime before running the optional MCP test. A skipped test is not transport evidence.

## 2026-09-14 — Oversized local HTTP test hit a connection reset

- Attempt: exercise the workbench's request-size boundary.
- Steps: send a body larger than 2 MB using `http.client`.
- Expected: read the server's structured size rejection.
- Actual: server rejected Content-Length before allocating/reading the body;
  the still-sending client received ConnectionResetError. The test failed.
- Severity: local test/transport edge case, not mathematical failure.
- Workaround: test oversized declared Content-Length without streaming a body,
  directly checking the pre-allocation rejection. Browser network errors remain
  visible and cannot retain a verified badge for the failed operation.
- Suggestion: distinguish early header rejection from full-body response tests.

## 2026-09-14 — Resumed process handles were no longer valid

- After the user resumed work, polling a pre-interruption test process returned
  an unknown process identifier. Its missing completion was not counted as a pass.
- Re-ran the full suite in the current session; completion evidence is recorded
  in the standalone handoff rather than inferred from the old process.

## 2026-09-14 — In-app browser download event was not observed

- Attempt: verify the Export bundle button through the browser download event.
- Actual: event waiter timed out, with no browser console error. The browser
  nevertheless wrote `Downloads/acc-certificate-bundle.json`.
- Workaround: locate the exact downloaded file and independently replay it with
  the CLI. The downloaded artifact verified successfully; the timeout was not
  treated as proof of either download failure or success.
- Severity: automation observability issue; no mathematical data loss observed.

## 2026-09-14 — Imported bundle left an unrelated model selected

- Browser QA replayed a strict bundle while the left panel still showed the
  default weak example. The result digest was correct, but the presentation
  could imply it certified the displayed candidate.
- Fixed by clearing the candidate on import and loading the bundle's exact bound
  specification after successful replay, with an Imported / edited model label.
- Added an HTTP assertion binding the displayed specification to the run input;
  replay rejection exposes no certified specification. Severity: important UX
  trust-boundary issue.

## 2026-09-27 — Browser agent tools and artifact readback

- Task: run an actual agent read/stage/compile/replay workflow at zero API cost.
- Steps: register four WebMCP tools; fetch available names; stage exact text;
  compile and replay both order-two fixtures; download the negative certificate.
- Expected: tool names and download events directly match the page registration.
- Actual: browser host decorates names with per-document identifiers; an initial
  unsuffixed name failed. Fresh discovery gave callable names. Changed-model
  compilation was deliberately rejected. Download-event waiter again timed out,
  but Downloads/acc-certificate-bundle (1).json existed and CLI replay verified
  obstruction.nonzero. No failed call or timeout counted as success by itself.
- Severity: automation discoverability/observability; no mathematical failure.
- Workaround: discover exact host names after reload; verify downloaded artifacts
  independently rather than inferring from an event timeout.
- Suggestion: host errors should preserve SPEC_CHANGED and other page-tool error
  text, and expose successful blob downloads consistently.

## 2026-09-27 — Official metadata and prose differ

- Task: refresh competition requirements and authenticated registration state.
- Expected: public Rules and structured key dates/submission fields agree.
- Actual: Rules judging starts November 9; API says October 26. Submission cutoff
  agrees at October 23 19:00 UTC. Repo field mentions @AmazonAppDev while current
  Rules name six reviewer accounts. Open Source now explicitly says additional
  project/contribution; do not assume the primary repo qualifies. Account is
  connected but initially not registered; registration required explicit
  agreement plus four answers. User then supplied them, registration succeeded
  (registration identifier omitted), and registered-only relationship was read back. No project submitted.
- Severity: important planning/eligibility ambiguity, not a product defect.
- Workaround: obey the earlier submission cutoff, keep evaluation access through
  November 20, use current Rules for reviewer access, record form IDs locally.
- Suggestion: synchronize structured dates and field help with current Rules.

## 2026-09-27 — Package build backend and clean installation

- Task: build an installable artifact rather than relying on editable source.
- Expected: no-build-isolation wheel command uses the current build backend.
- Actual: runtime venv lacked setuptools.build_meta, so the first build failed.
- Severity: local packaging environment, not core mathematical failure.
- Workaround: normal PEP 517 isolated build installs the declared setuptools
  backend. Wheel 0.2.0 built successfully, then clean Python 3.9 installation
  compiled/replayed both order-two signs from outside repo cwd. No new runtime
  dependency introduced; generated build/wheel artifacts are ignored.
- Suggestion: keep build tooling separate from the optional MCP runtime and use
  default isolated builds in release instructions.

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

## 2026-09-27 — MCP forgery test was partly a schema test

- Task: prepare a concrete Streamable HTTP demonstration with a rehashed witness.
- Actual: the existing integration test added `rank` to every final certificate.
  Some certificate types have no rank field, so their rejection exercised unknown
  schema fields rather than a false mathematical witness. Separate core tests
  already checked mathematical forgeries, but the transport assertion was weaker.
- Severity: test evidence quality; no verifier acceptance bug was observed.
- Workaround: mutate existing solution, pairing, strict coordinates or correction
  entries, recompute the digest and require CERTIFICATE_WITNESS_INVALID over real
  HTTP for all five fixtures. The focused transport test passed without skips.
- Suggestion: assert the specific failure code in adversarial integration checks,
  so a schema guard cannot accidentally stand in for mathematical replay.

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
