"""Show Your Work MCP server: Streamable HTTP (MCP 2025-11-25) with an MCP Apps card.

Handlers contain no mathematics. They call the exact producer, which is replayed
by the independent verifier before any claim is returned, explained or saved.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, WithJsonSchema

from . import __version__, service
from .compiler import compile_reconstruction as compile_problem
from .ir import MAX_BYTES, problem_schema
from .linear import PRACTICE_KINDS
from .verifier import verify_certificate as replay_research_certificate

LOG = logging.getLogger(__name__)
CARD_URI = "ui://show-your-work/certificate-card.html"
CARD_MIME = "text/html;profile=mcp-app"
CARD_PATH = Path(__file__).with_name("web") / "card.html"
LOOPBACK = ("127.0.0.1", "localhost", "::1")

INSTRUCTIONS = """Show Your Work: math answers an assistant can prove.
AI interprets. Compiler calculates. Verifier certifies.

You translate the person's words into exact linear equations; the tools calculate
with exact rationals and an independent verifier replays every certificate.
- Word problems: short unknown names plus labels, e.g. {"a": "adult tickets"}.
  Counts of people or things use domain "nonnegative_integer".
- Homework: prefer check_work (every line of the student's work, first line = the
  original problem) or check_answer before solve_equations. Give the hint first;
  reveal the verified answer only when asked.
- Speak the suggested_speech in one or two short sentences. Only state what a
  result with certificate_verified=true says. MATHEMATICALLY_REJECTED with
  certificate_verified=true is a certified finding (no solution, wrong answer,
  broken step), not a failure. If nothing was verified, say so; never supply
  your own number instead.
- Pass notebook (a family name) and learner (a first name) to remember results
  across sessions; progress_report re-verifies every saved certificate.
- Nonlinear equations (x^2, x*y) are unsupported: say so plainly.
- No LLM output is a proof. Only a replayed certificate is.
Research tier: compile_reconstruction / verify_reconstruction certify fixed
A,V,B algebra problems (Passes 1-6); no order above two and no fixed-cubic backend.
"""

ANSWER_TOOL = {"ui": {"resourceUri": CARD_URI, "visibility": ["model", "app"]}}
APP_ONLY = {"ui": {"resourceUri": CARD_URI, "visibility": ["app"]}}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SAVES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

Equations = Annotated[list[str], Field(min_length=1, max_length=12, description=(
    "Linear equations as plain text, e.g. ['a + c = 20', '12a + 7c = 300']. Use numbers, unknown names, "
    "+ - * / ( ) and exactly one '=' each. 2x means 2*x; decimals are exact (0.25 = 1/4)."))]
Domain = Annotated[Literal["rational", "integer", "nonnegative_integer"], Field(description=(
    "What the unknowns may be: any exact number, whole numbers, or whole numbers 0 or more (counts)."))]
Labels = Annotated[Optional[dict[str, str]], Field(description="Optional meaning of each unknown, e.g. {'a': 'adult tickets'}.")]
Question = Annotated[Optional[str], Field(max_length=600, description="The person's original wording, shown back for checking.")]
NotebookName = Annotated[Optional[str], Field(max_length=40, description="Optional family notebook to save this verified result in.")]
Learner = Annotated[Optional[str], Field(max_length=40, description="Optional first name of the learner, for progress reports.")]
ProblemRequest = Annotated[dict, WithJsonSchema(problem_schema())]


def _result(payload):
    error = payload.get("status") in ("ASSUMPTION_REQUIRED", "INFRASTRUCTURE_ERROR") or payload.get("kind") == "error"
    return CallToolResult(content=[TextContent(type="text", text=service.summary_text(payload))],
                          structuredContent=payload, isError=error)


def _security(host):
    allowed = [h.strip() for h in os.environ.get("ACC_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if host in LOOPBACK:
        return None  # FastMCP applies loopback-only Host/Origin checks.
    if allowed:
        origins = [f"https://{h.split(':')[0]}" for h in allowed] + [f"http://{h}" for h in allowed]
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed,
                                         allowed_origins=origins)
    LOG.warning("Public bind without ACC_ALLOWED_HOSTS: DNS-rebinding protection disabled")
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def create_server(port=8000, host="127.0.0.1"):
    server = FastMCP("Show Your Work", instructions=INSTRUCTIONS, host=host, port=port,
                     stateless_http=True, json_response=True, max_request_body_size=MAX_BYTES,
                     log_level="WARNING", transport_security=_security(host))

    @server.resource(CARD_URI, name="certificate_card", title="Show Your Work certificate card",
                     description="Interactive certificate: translation, proof steps, replay and forgery check.",
                     mime_type=CARD_MIME, meta={"ui": {"prefersBorder": True}})
    def certificate_card() -> str:
        return CARD_PATH.read_text(encoding="utf-8")

    @server.tool(title="Solve equations exactly", annotations=SAVES, meta=ANSWER_TOOL)
    async def solve_equations(equations: Equations, domain: Domain = "rational", labels: Labels = None,
                              question: Question = None, notebook: NotebookName = None,
                              learner: Learner = None) -> CallToolResult:
        """Solve linear equations exactly and return an independently verified certificate.

        Outcomes: one solution (with the combination of equations that isolates each
        unknown), infinitely many, no solution (a combination that leaves 0 = c), or no
        valid answer in the domain (e.g. the only solution has -12 tickets).
        """
        return _result(await asyncio.to_thread(service.solve, equations, domain, labels, question, notebook, learner))

    @server.tool(title="Check an answer", annotations=SAVES, meta=ANSWER_TOOL)
    async def check_answer(equations: Equations,
                           answer: Annotated[dict[str, str], Field(description="Value for every unknown as exact text, e.g. {'x': '4'} or {'x': '-3/2'}.")],
                           domain: Domain = "rational", labels: Labels = None, question: Question = None,
                           notebook: NotebookName = None, learner: Learner = None) -> CallToolResult:
        """Check a proposed answer by exact substitution; say which equation fails and by how much."""
        return _result(await asyncio.to_thread(service.check_answer, equations, answer, domain, labels, question,
                                               notebook, learner))

    @server.tool(title="Check step-by-step work", annotations=SAVES, meta=ANSWER_TOOL)
    async def check_work(steps: Annotated[list[list[str]], Field(min_length=2, max_length=16, description=(
                             "Each line of the student's work as a list of equations, first line = the original "
                             "problem, e.g. [['3x + 5 = 20'], ['3x = 25'], ['x = 25/3']]."))],
                         domain: Domain = "rational", labels: Labels = None, question: Question = None,
                         notebook: NotebookName = None, learner: Learner = None) -> CallToolResult:
        """Find the first line of work that changes the answer, with a certified counterexample.

        Each sound step is proved by expressing the new equations as exact combinations of
        the previous line. A broken step is shown by a value that satisfies the previous
        line but not the next one. Hints about the kind of slip are not certified.
        """
        return _result(await asyncio.to_thread(service.check_work, steps, domain, labels, question, notebook, learner))

    @server.tool(title="Create a practice problem", annotations=SAVES, meta=ANSWER_TOOL)
    async def practice_problem(kind: Annotated[Literal[PRACTICE_KINDS], Field(description=(
                                   "one_step, two_step, both_sides, distribute, system (2 unknowns) or tickets (word problem)."))] = "two_step",
                               seed: Annotated[Optional[int], Field(ge=0, lt=10 ** 12, description="Optional seed to reproduce a problem.")] = None,
                               notebook: NotebookName = None, learner: Learner = None) -> CallToolResult:
        """Generate a fresh practice problem whose answer key is independently verified. Do not read the key aloud unless asked."""
        return _result(await asyncio.to_thread(service.practice, kind, seed, notebook, learner))

    @server.tool(title="Replay a certificate", annotations=READ_ONLY, meta=ANSWER_TOOL)
    async def verify_certificate(bundle: Annotated[dict, Field(description="A portable Show Your Work bundle (or a research compilation run).")]) -> CallToolResult:
        """Independently replay a portable certificate without any language model. Rejects forged or altered witnesses."""
        return _result(await asyncio.to_thread(service.verify, bundle))

    @server.tool(title="Forgery check", annotations=READ_ONLY, meta=APP_ONLY)
    async def forgery_check(bundle: dict) -> CallToolResult:
        """App-only demo: change one witness number, recompute the certificate hash, and show the verifier rejects it."""
        return _result(await asyncio.to_thread(service.forge, bundle))

    @server.tool(title="Notebook history", annotations=READ_ONLY, meta=ANSWER_TOOL)
    async def notebook_history(notebook: Annotated[str, Field(max_length=40)], learner: Learner = None,
                               limit: Annotated[int, Field(ge=1, le=50)] = 10) -> CallToolResult:
        """List recently saved verified results for a family notebook (optionally one learner)."""
        return _result(await asyncio.to_thread(service.history, notebook, learner, limit))

    @server.tool(title="Progress report", annotations=READ_ONLY, meta=ANSWER_TOOL)
    async def progress_report(notebook: Annotated[str, Field(max_length=40)], learner: Learner = None,
                              days: Annotated[int, Field(ge=1, le=365)] = 7) -> CallToolResult:
        """Summarize recent work across sessions. Every saved certificate is re-verified before it is counted."""
        return _result(await asyncio.to_thread(service.progress, notebook, learner, days))

    @server.tool(annotations=READ_ONLY)
    async def compile_reconstruction(problem_spec: ProblemRequest) -> dict[str, Any]:
        """Research tier: compile a fixed-A,V,B rational specification through Passes 1-6 and verify each certificate.

        Sparse omitted coefficients mean zero. Request prefixes of [structure, observability,
        weak, strict, tangent, obstruction]. Strict validates one supplied point; obstruction
        tests a supplied direction through order two only.
        """
        return await asyncio.to_thread(compile_problem, problem_spec)

    @server.tool(annotations=READ_ONLY)
    async def verify_reconstruction(certificate: dict, problem_spec: ProblemRequest) -> dict[str, Any]:
        """Research tier: independently replay one exact reconstruction certificate against its canonical input."""
        return await asyncio.to_thread(replay_research_certificate, certificate, problem_spec)

    @server.prompt(title="Check homework by voice")
    def homework_helper(learner: str = "", notebook: str = "") -> str:
        """Workflow for checking a child's homework out loud, hint first."""
        return (INSTRUCTIONS + f"\nLearner: {learner or 'unknown'}; notebook: {notebook or 'none'}.\n"
                "Ask for the problem and each line of work. Call check_work (or check_answer for a final "
                "answer only). Read suggested_speech, offer the hint, and reveal the verified answer only on request. "
                "Offer practice_problem of the same kind afterwards.")

    @server.prompt(title="Solve a word problem with proof")
    def word_problem(problem: str) -> str:
        """Translate a word problem, confirm the translation, then solve with a certificate."""
        return (INSTRUCTIONS + "\nWord problem: " + problem + "\nWrite the equations with labelled unknowns and "
                "the right domain, call solve_equations, then say the answer and ask the person to check the translation.")

    @server.prompt(title="Weekly progress")
    def weekly_progress(notebook: str, learner: str = "") -> str:
        """Summarize a learner's week from re-verified notebook entries."""
        return INSTRUCTIONS + f"\nCall progress_report for notebook {notebook!r}, learner {learner!r}, days=7, and summarize kindly."

    @server.prompt()
    def reconstruction_workflow() -> str:
        """Agent instructions for the research reconstruction tier."""
        return INSTRUCTIONS

    @server.custom_route("/", methods=["GET"])
    async def landing(_request):
        from starlette.responses import HTMLResponse
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Show Your Work MCP</title>"
                            "<body style='font:16px system-ui;max-width:640px;margin:40px auto;padding:0 16px'>"
                            "<h1>Show Your Work · MCP server</h1><p>AI interprets. Compiler calculates. Verifier certifies.</p>"
                            "<p>Connect an MCP client (Streamable HTTP, protocol 2025-11-25) to <code>/mcp</code>. "
                            f"Version {__version__}. MCP Apps card: <code>{CARD_URI}</code>.</p>"
                            "<p><a href='https://github.com/Akari-H-111/algebraic-constraint-compiler'>Source (MIT)</a></p></body>")

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "version": __version__})

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--notebook", help="SQLite path for the family notebook (default ~/.show-your-work/)")
    parser.add_argument("--cors", action="store_true", help="Allow browser MCP clients from any origin")
    parser.add_argument("--stdio", action="store_true", help="Serve over stdio for desktop MCP hosts instead of HTTP")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.notebook:
        os.environ["SYW_NOTEBOOK_PATH"] = args.notebook
    server = create_server(args.port, args.host)
    if args.stdio:
        server.run(transport="stdio")
        return
    app = server.streamable_http_app()
    if args.cors:
        from starlette.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                           allow_headers=["*"], expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"])
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
