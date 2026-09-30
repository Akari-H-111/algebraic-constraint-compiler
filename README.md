# Show Your Work: math answers Alexa+ can prove

**AI interprets. Compiler calculates. Verifier certifies.**

Show Your Work is an MCP server and a simulated Alexa+ smart-display experience.
It checks a child's math homework and never says "correct" unless an
independent verifier has replayed an exact proof. It finds the exact line of
work where the mistake happened, catches word problems that can't be true as
written, and remembers each learner's progress across sessions. Every result
comes with a portable certificate that anyone can re-check without trusting
the assistant.

**Demo video (2:54):** https://youtu.be/MAS9ZeFuNRg

**Public MCP endpoint (Streamable HTTP, 2025-11-25):** `https://show-your-work-mcp.onrender.com/mcp`
(free instance: the first request may take about 50 seconds while it wakes up).

**Try it now, no install:** [akari-h-111.github.io/algebraic-constraint-compiler](https://akari-h-111.github.io/algebraic-constraint-compiler/).
The web demo runs the real Python compiler and verifier in your browser (Guided requests, no AI). For voice, the
Claude or Gemini agent and the MCP server, run it locally (below).

![Simulated Alexa+ display checking a child's homework: the mistake is on line 1 → 2, with the typed equations, a hint, the proof, and a verified certificate](docs/images/homework-check.png)

## Why

Just over half of U.S. teens have used AI chatbots for schoolwork, and about
four in ten have used them to solve math problems. Parents underestimate how
often this happens ([Pew Research Center, 2026](https://www.pewresearch.org/internet/2026/02/24/how-teens-use-and-view-ai/);
survey of 1,458 teens and parents, Sept–Oct 2025). Language models sound sure
of themselves, but they are fragile at math: in Apple's GSM-Symbolic study,
accuracy dropped when only the numbers in a problem changed, and by up to 65%
when one irrelevant clause was added
([Mirzadeh et al., 2024](https://machinelearning.apple.com/research/gsm-symbolic)).

A parent at the kitchen counter can't tell a correct explanation from a
confident wrong one. A voice assistant in the home shouldn't make them guess.

## What it does

| Ask Alexa… | Show Your Work returns (all certified) |
|---|---|
| "Check Maya's homework: 3x + 5 = 20, then 3x = 25, then x = 25/3." | **The first step that changes the answer**, with a counterexample: x = 5 works before the step but gives 15 ≠ 25 after it. A labelled, uncertified hint: "when +5 moves across, it becomes −5". |
| "Adult tickets $12, child $7, 20 tickets for $300. How many child tickets?" | **No valid answer:** the only exact solution is −12 child tickets. The worksheet has a typo, proved by combining the two equations. |
| "Maya says x = 4 for 2x − 7 = 1. Right?" | **Correct**, by substitution, and it is the only solution. |
| "Give her a practice problem like that." | A fresh problem whose answer key the verifier has already checked, hidden until someone asks. |
| "How is Maya doing this week?" | A summary rebuilt from the notebook. Every stored certificate is replayed first, and the most common slip is named. |

The card on screen is an **MCP App** (SEP-1865, the open standard for
interactive UI in MCP). It is written only against that standard, so the same
certificate is meant to render inline in any MCP Apps host (Claude, ChatGPT,
VS Code, Goose, …). It is tested here in the simulator, which implements the
host side of the spec, including the cross-origin sandbox proxy. Its buttons
call back into the server: **Replay verifier** re-checks the proof live, and **Try a
forgery** changes one number, recomputes the SHA-256, and shows that the
verifier still rejects it.

<p align="center"><img src="docs/images/no-valid-answer.png" width="49%" alt="A worksheet problem with no valid answer, certified"> <img src="docs/images/progress.png" width="49%" alt="Weekly progress rebuilt from re-verified notebook entries"></p>

## How it works

```mermaid
flowchart LR
  V["Voice or text<br/>(Alexa+ style display)"] --> A["Agent<br/>AI interprets"]
  A -- "MCP tools/call<br/>Streamable HTTP 2025-11-25" --> S["Show Your Work<br/>MCP server"]
  S --> C["Exact compiler<br/>rationals, no floats"]
  C --> K["Certificate<br/>witness + SHA-256"]
  K --> R["Independent verifier<br/>re-reads the text, multiplies and adds"]
  R -- "only if replay passes" --> S
  S -- "ui:// MCP App card" --> V
  S <--> N[("Family notebook<br/>re-verified on every read")]
```

- **The only AI step is translating words into equations**, and the card
  shows that translation back ("What I understood — please check").
- **The compiler** reads a deliberately small, safe language: numbers, exact
  decimals, unknowns, `+ − × ÷ ( ) =`. There is no `eval` and no floating
  point. Products of unknowns and powers are rejected as `UNSUPPORTED`,
  never approximated.
- **Every claim carries a witness the verifier can check with multiplication
  and addition alone.** A unique answer comes with the combination of
  equations that isolates each unknown. "No solution" comes with a combination
  that cancels every unknown and leaves 0 = c. A broken step comes with a value
  that satisfies one line but not the next. A sound step comes with the new
  equations written as exact combinations of the previous ones.
- **The verifier is independent.** It never imports the producer, never uses
  the producer's matrix, and never runs elimination. It re-reads each equation
  with its own evaluator by plugging in numbers.
- **Statuses stay distinct.** `VERIFIED`, certified `MATHEMATICALLY_REJECTED`
  (a finding, not a failure), `UNSUPPORTED`, `ASSUMPTION_REQUIRED`,
  `INCOMPLETE_RESOURCE_LIMIT` and `INFRASTRUCTURE_ERROR` are never blurred.

## Try it in two minutes

Python 3.10+ for the MCP server (the exact core runs on 3.9+ with no dependencies):

```bash
git clone https://github.com/Akari-H-111/algebraic-constraint-compiler
cd algebraic-constraint-compiler
python3 -m venv .venv && .venv/bin/pip install -e '.[mcp]'
.venv/bin/python -m algebraic_compiler.simulator
```

Open http://127.0.0.1:8787. The simulator starts the MCP server for you.
Without a model key it runs in **Guided mode**: preset typed requests call the
same MCP tools directly, clearly labelled as involving no AI. To talk freely by
voice or text, put one or more keys in a git-ignored `.env` file in the project
root (the simulator reads it and never sends keys to the browser):

```bash
ANTHROPIC_API_KEY=...          # Claude (claude-opus-5-5) via the official SDK: pip install -e '.[claude]'
GEMINI_API_KEY=...             # Gemini (gemini-2.5-flash) via its OpenAI-compatible endpoint
AWS_BEARER_TOKEN_BEDROCK=...   # Amazon Bedrock (Amazon Nova Lite)
```

With more than one key set, a switch in the top bar changes the agent's model
live. The certificate for the same typed model doesn't change, because neither
model calculates.

### Use it from any MCP host

```bash
.venv/bin/python -m algebraic_compiler.mcp_server              # http://127.0.0.1:8000/mcp
.venv/bin/python -m algebraic_compiler.mcp_server --stdio      # for desktop hosts
```

Claude Desktop (`claude_desktop_config.json`), VS Code and other stdio hosts:

```json
{"mcpServers": {"show-your-work": {"command": "/path/to/.venv/bin/python",
  "args": ["-m", "algebraic_compiler.mcp_server", "--stdio"]}}}
```

An [Agent Skill](skills/show-your-work/SKILL.md) teaches any skills-aware agent
the workflow. A [Dockerfile](Dockerfile) and [Render blueprint](deploy/render.yaml)
publish the Streamable HTTP endpoint (`--host 0.0.0.0`; set
`ACC_ALLOWED_HOSTS` for Host/Origin checks).

## Tools

| Tool | What it certifies | Card |
|---|---|---|
| `check_work` | First line of work that changes the answer (counterexample), or that every step follows | ✓ |
| `check_answer` | A proposed answer by exact substitution, plus uniqueness when it holds | ✓ |
| `solve_equations` | One solution, infinitely many (complete family), no solution, or no valid answer in the domain | ✓ |
| `practice_problem` | A fresh exercise with a pre-verified, hidden answer key | ✓ |
| `verify_certificate` | Independent replay of any portable bundle | ✓ |
| `progress_report` / `notebook_history` | Learner progress across sessions, re-verified from storage | ✓ |
| `forgery_check` | App-only (`visibility: ["app"]`): tamper, rehash, show rejection | ✓ |
| `compile_reconstruction` / `verify_reconstruction` | Research tier: algebraic reconstruction and second-order obstruction ([details](docs/research-tier.md)) | |

Prompts: `homework_helper`, `word_problem`, `weekly_progress`, `reconstruction_workflow`.

## Evidence

```bash
.venv/bin/python -B -m unittest discover -s tests -p 'test*.py' -v   # 76 tests
python3 -B -m unittest discover -s tests -p 'test*.py'               # core on Python 3.9 (HTTP tests skip)
```

The suite on Python 3.14 with MCP SDK 1.30.0 (zero skips) covers:

- real Streamable HTTP handshake at protocol 2025-11-25, tool `_meta.ui`
  linkage and visibility, and the `text/html;profile=mcp-app` resource;
- every claim type replayed, and rehashed forgeries rejected for each one;
- 250 random systems cross-checked against brute force;
- verifier independence (the producer is patched to raise and replay still
  passes, and the verifier's imports are checked);
- notebook rows corrupted on disk are excluded from progress;
- the simulator's guided scenarios, the app-only tool policy, and a scripted
  agent loop over real HTTP.

A headless-Chrome script ([tools/cdp.mjs](tools/cdp.mjs)) drives the
simulator for visual QA and demo capture. The MCP Apps host side (sandbox
proxy, CSP injection, handshake, message log) is also released as a separate
open-source tool, [mcp-apps-preview](https://github.com/Akari-H-111/mcp-apps-preview).
Show Your Work passes its SEP-1865 lint with 0 errors and 0 warnings.

## Limits (on purpose)

- Linear equations over ℚ: up to 12 equations and 12 unknowns. No quadratics
  yet. For infinitely many solutions under a whole-number domain, the rational
  family is certified but which members are whole numbers is reported as not
  decided.
- Hints about the *kind* of slip are rule-based and labelled "not certified".
  The broken step itself is certified.
- The Alexa+ experience is a simulation built for this hackathon, not an
  Amazon product, and no Alexa account is connected. It uses the browser's
  speech recognition and synthesis. Math never passes through the language
  model.
- The agent loop is tested with scripted providers, and live with Gemini
  (the demo video). Live Claude and Bedrock calls need your own key and credit.
  If a model fails, the device says so. If it calls a tool but stays silent,
  the device speaks the tool's verified suggested speech.
- The notebook is a local SQLite file. Hosted containers keep it only
  per instance.
- Certificates are replayable computational records, not proof-assistant
  formalizations.

## Research tier and history

The project began as the Algebraic Constraint Compiler: exact finite-dimensional
algebraic reconstruction and second-order deformation certificates (Passes 1–6)
with the same producer/verifier split. That tier still ships, with its
workbench (`python -m algebraic_compiler.workbench`). See
[docs/research-tier.md](docs/research-tier.md) and the [friction log](friction-log.md).
MIT licensed.
