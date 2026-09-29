"""Real loopback HTTP protocol/tool integration; requires the optional MCP extra."""

import copy
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from algebraic_compiler.ir import digest, load_json, rational, scalar
from algebraic_compiler.verifier import verify_run

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install .[mcp] with Python >=3.10 for real HTTP tests")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.log = tempfile.TemporaryFile(mode="w+")
        cls.process = subprocess.Popen([sys.executable, "-B", "-m", "algebraic_compiler.mcp_server",
                                        "--port", str(cls.port)], cwd=ROOT, stdout=cls.log, stderr=cls.log)
        for _ in range(200):
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError("MCP server exited: " + cls.log.read())
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.seek(0)
        raise RuntimeError("MCP startup timed out: " + cls.log.read())

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.close()

    async def test_http_handshake_discovery_compilation_replay_and_rejection(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from jsonschema import Draft202012Validator

        async with streamable_http_client(f"http://127.0.0.1:{self.port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.protocolVersion, "2025-11-25")
                tools = await client.list_tools()
                self.assertTrue({"compile_reconstruction", "verify_reconstruction"} <= {t.name for t in tools.tools})
                research = next(t for t in tools.tools if t.name == "compile_reconstruction")
                schema = research.inputSchema["properties"]["problem_spec"]
                Draft202012Validator.check_schema(schema)
                self.assertEqual(schema["properties"]["mode"]["const"], "fixed_A_V_B")
                prompt = await client.get_prompt("reconstruction_workflow")
                self.assertIn("No LLM output is a proof", prompt.messages[0].content.text)

                for name, expected in (("weak-consistent", "VERIFIED"), ("weak-inconsistent", "MATHEMATICALLY_REJECTED"),
                                       ("strict-point-valid", "VERIFIED"), ("second-order-obstructed", "MATHEMATICALLY_REJECTED"),
                                       ("second-order-extends", "VERIFIED")):
                    raw = load_json((ROOT / "examples" / "algebraic" / f"{name}.json").read_bytes())
                    Draft202012Validator(schema).validate(raw)
                    called = await client.call_tool("compile_reconstruction", {"problem_spec": raw})
                    self.assertFalse(called.isError)
                    run = called.structuredContent
                    self.assertEqual(run["status"], expected)
                    self.assertTrue(verify_run(run)["certificate_verified"])
                    for cert in run["certificates"]:
                        replay = await client.call_tool("verify_reconstruction", {"certificate": cert, "problem_spec": raw})
                        self.assertTrue(replay.structuredContent["certificate_verified"])
                    bad = copy.deepcopy(run["certificates"][-1])
                    result = bad["result"]
                    if name == "weak-consistent":
                        column = run["certificates"][1]["result"]["matrix"]["entries"][0]["col"]
                        result["particular_solution"][column] = scalar(rational(result["particular_solution"][column]) + 1)
                    elif name == "weak-inconsistent":
                        witness = result["inconsistency_witness"]
                        witness["rhs_pairing"] = scalar(rational(witness["rhs_pairing"]) + 1)
                    elif name == "strict-point-valid":
                        result["coordinates"][0] = scalar(rational(result["coordinates"][0]) + 1)
                    elif name == "second-order-obstructed":
                        witness = result["nonmembership_witness"]
                        witness["pairing"] = scalar(rational(witness["pairing"]) + 1)
                    else:
                        result["lift_witness"][0] = scalar(rational(result["lift_witness"][0]) + 1)
                    bad["certificate_sha256"] = digest({k: v for k, v in bad.items() if k != "certificate_sha256"})
                    rejected = await client.call_tool("verify_reconstruction", {"certificate": bad, "problem_spec": raw})
                    self.assertFalse(rejected.isError)
                    self.assertFalse(rejected.structuredContent["certificate_verified"])
                    self.assertEqual(rejected.structuredContent["code"], "CERTIFICATE_WITNESS_INVALID")

                raw["algebra"]["unit"]["coordinates"][0] = 1.0
                bad_float = await client.call_tool("compile_reconstruction", {"problem_spec": raw})
                self.assertEqual(bad_float.structuredContent["status"], "ASSUMPTION_REQUIRED")
                self.assertEqual(bad_float.structuredContent["code"], "NONCANONICAL_SCALAR")


if __name__ == "__main__":
    unittest.main()
