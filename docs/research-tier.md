# Research tier: Algebraic Constraint Compiler (Passes 1–6)


Show Your Work runs on the same trust architecture as this research compiler, which came first.
It certifies finite-dimensional algebraic reconstruction and second-order deformation claims.
The MCP tools are `compile_reconstruction` and `verify_reconstruction` (renamed from
`verify_certificate` in 0.3.0, when that name moved to the Show Your Work bundles).

AI interprets and explains. The compiler calculates with rational arithmetic.
An independent verifier replays the certificate. The language model is never a
proof engine.

The current general MVP supports fixed `A,V,B` specifications over `Q`:

1. typed structure validation;
2. Leibniz observability compilation;
3. weak affine reconstruction with a full kernel or dual inconsistency witness;
4. strict validation of one supplied action point, including weak, unit, left,
   right, and mixed residuals;
5. exact tangent operator, complete kernel basis and local rigidity classification;
6. primary order-two obstruction for one supplied tangent direction, with an
   exact correction or dual non-membership witness.

Pass 4 validates a supplied point; it does not search the weak fiber. Passes 5–6
require that point. A non-tangent direction is rejected before evaluating an
obstruction. Order-two success does not certify higher orders; obstruction rules
out corrections for this direction at this point, not all solutions. Passes 7–10
and the missing fixed-cubic backend remain unsupported. Runtime limits: dim A ≤ 4,
dim V ≤ 3, bounded exact elimination and a 2 MB wire payload.

## Quick start

Python 3.9+ and the standard library are sufficient:

```bash
python3 -B -m algebraic_compiler compile examples/algebraic/second-order-obstructed.json
python3 -B -m algebraic_compiler compile examples/algebraic/weak-inconsistent.json | python3 -B -m algebraic_compiler verify -
python3 -B -m unittest discover -s tests -p 'test_algebraic.py' -v
```

Both `VERIFIED` and certified `MATHEMATICALLY_REJECTED` runs are successful
computations. Malformed, unsupported, incomplete, or unverifiable inputs fail
closed.

## Local verification workbench

From this source checkout (Python standard library only):

```bash
python3 -B -m algebraic_compiler.workbench --port 8765
```

Open http://127.0.0.1:8765. In either second-order teaching model, enter an exact
direction multiplier (for example `2` or `-3/2`) and select **Prepare this question**.
This stages a new typed candidate without certification; **Compile & check**
then calculates and independently verifies it. Scaling always starts from the
original fixture. Zero is explicitly a trivial direction; no higher-order claim
is implied. You can also compile an editable example, inspect exact witnesses,
export a bundle, import it in a fresh session and replay independently. The
rehashed-forgery button changes witness data and recomputes its hash; the verifier
still rejects it. Edited or imported data loses its verified badge until replay.

The local UI calls the exact Python facade. In a WebMCP-capable browser, four
page tools let your agent read the schema, stage a candidate, compile the exact
displayed model and replay its bundle. A changed specification is refused until
the agent reads or stages it again. Codex exercised this real path on September 27;
it is not an application-owned LLM loop or an actual Alexa+ account connection.
The visible session activity lists actual host-agent reads, candidate staging,
compiler results and independent replay, including failures and input digests.
It is a session observation, not certificate evidence. The current mathematical
claim remains in the result panel; history does not certify a changed model.
Ordinary browsers retain the manual workflow. No paid API key is needed for the
compiler or verifier. The MCP adapter below is separately tested over real HTTP.
Certificates persist only when
you export them. The UI requires the checkout's examples; the installed core CLI
has no UI requirement. Bind only to loopback; this development server is not a
public hosting configuration.

See the [mathematical contract](math-contract.md).

## MCP adapter

The Show Your Work MCP server also exposes the two research tools over
Streamable HTTP: `compile_reconstruction` and `verify_reconstruction`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python -m algebraic_compiler.mcp_server --port 8000
```

Connect an MCP client to `http://127.0.0.1:8000/mcp`. The server supports MCP
protocol version `2025-11-25`.

## Exact wire format

Trusted scalars use reduced objects such as `{"num": 1, "den": 3}`. Floats
and booleans are rejected. Sparse tensors are canonicalized, duplicate entries
are summed, and every certificate binds the canonical input SHA-256, compiler
version, scope, backend, provenance, result, and its own digest.

See the [friction log](../friction-log.md) for scope, provenance, and observed integration
constraints. The project is released under the MIT license.

## Verification

```bash
# Core and workbench; no dependencies
python3 -B -m unittest discover -s tests -p 'test_algebraic.py' -v
python3 -B -m unittest discover -s tests -p 'test_workbench.py' -v
python3 -B -m unittest discover -s tests -p 'test_deformation.py' -v
# All paths including real MCP HTTP; install .[mcp] first
.venv/bin/python -B -m unittest discover -s tests -p 'test*.py' -v
```

Use Python >= 3.10 for MCP. Do not count the optional MCP tests as passed when
they are skipped.

For the browser tool boundary, an optional Node >=18 check uses a minimal DOM
harness with the real local HTTP backend (start the workbench first):

```bash
node tests/check_agent_session.mjs http://127.0.0.1:8765
```

Research-tier evidence (2026-09-27, still passing in the 0.3.0 suite): real MCP
HTTP and both order-two cases. The independent verifier also accepts legacy 0.1.0
certificates for Passes 1–4; 0.2.0 added tangent/obstruction. The two rational
fixtures are constructed computational examples, not recovered historical cubic
source artifacts.