"""Optional, loopback-only Streamable HTTP adapter; no mathematics in handlers."""

import argparse
import asyncio
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import WithJsonSchema

from .compiler import compile_reconstruction as compile_problem
from .ir import MAX_BYTES, problem_schema
from .verifier import verify_certificate as replay_certificate

ProblemRequest = Annotated[dict, WithJsonSchema(problem_schema())]
INSTRUCTIONS = """AI interprets. Compiler calculates. Verifier certifies.
Draft a complete exact typed specification; ask for missing structure constants.
Call compile_reconstruction, then verify_certificate on its certificates and canonical problem.
Explain only the verified claim. A weak fiber is not a strict bimodule or a deformation result.
MATHEMATICALLY_REJECTED with certificate_verified=true is a certified mathematical rejection.
Never soften a rejected certificate into success. Passes 7–10 and fixed_cubic_v1 are unsupported.
Preserve input digest, scope, backend and exact witness. No LLM output is a proof.
This server is stateless: clients retain returned bundles for replay. No saved-run lookup exists.
"""


def create_server(port=8000):
    server = FastMCP("Algebraic Constraint Compiler", instructions=INSTRUCTIONS,
                     host="127.0.0.1", port=port, stateless_http=True, json_response=True,
                     max_request_body_size=MAX_BYTES, log_level="WARNING")

    @server.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def compile_reconstruction(problem_spec: ProblemRequest) -> dict[str, Any]:
        """Compile a fixed-A,V,B rational specification through Passes 1–6 and verify each certificate.

        Sparse omitted coefficients mean zero, not missing data. Request prefixes
        of [structure, observability, weak, strict, tangent, obstruction]. Strict validates one supplied
        exact action point; it does not search for one. Tangent analyzes that point;
        obstruction tests a supplied direction through order two only.
        """
        return await asyncio.to_thread(compile_problem, problem_spec)

    @server.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def verify_certificate(certificate: dict, problem_spec: ProblemRequest) -> dict[str, Any]:
        """Independently replay one exact certificate against its canonical typed input, without an LLM."""
        return await asyncio.to_thread(replay_certificate, certificate, problem_spec)

    @server.prompt()
    def reconstruction_workflow() -> str:
        """Agent instructions for drafting, compiling, replaying and accurately explaining a weak reconstruction."""
        return INSTRUCTIONS

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    create_server(args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
