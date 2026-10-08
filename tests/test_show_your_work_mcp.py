"""Real Streamable HTTP tests for the Show Your Work tools and MCP Apps card (requires .[mcp])."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from algebraic_compiler.linear_verifier import verify_bundle
from algebraic_compiler.mcp_client import MCPClient, ui_meta

ROOT = Path(__file__).resolve().parents[1]
CARD = "ui://show-your-work/certificate-card.html"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def start_server(port, notebook_path):
    log = tempfile.TemporaryFile(mode="w+")
    env = {**os.environ, "SYW_NOTEBOOK_PATH": notebook_path}
    process = subprocess.Popen([sys.executable, "-B", "-m", "algebraic_compiler.mcp_server", "--port", str(port)],
                               cwd=ROOT, stdout=log, stderr=log, env=env)
    for _ in range(200):
        if process.poll() is not None:
            log.seek(0)
            raise RuntimeError("MCP server exited: " + log.read())
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process, log
        except OSError:
            time.sleep(0.05)
    process.terminate()
    raise RuntimeError("MCP startup timed out")


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install .[mcp] with Python >=3.10 for real HTTP tests")
class ShowYourWorkMCPTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.process, cls.log = start_server(cls.port, str(Path(cls.tmp.name) / "nb.sqlite3"))
        cls.url = f"http://127.0.0.1:{cls.port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.close()
        cls.tmp.cleanup()

    async def test_tools_card_resource_and_certified_results_over_sdk_client(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.protocolVersion, "2025-11-25")
                self.assertIn("No LLM output is a proof", initialized.instructions)
                tools = {t.name: t for t in (await client.list_tools()).tools}
                for name in ("solve_equations", "check_answer", "check_work", "practice_problem", "verify_certificate",
                             "notebook_history", "progress_report"):
                    self.assertEqual(tools[name].meta["ui"], {"resourceUri": CARD, "visibility": ["model", "app"]})
                self.assertEqual(tools["forgery_check"].meta["ui"]["visibility"], ["app"])
                self.assertIsNone(tools["compile_reconstruction"].meta)
                resource = (await client.read_resource(CARD)).contents[0]
                self.assertEqual(resource.mimeType, "text/html;profile=mcp-app")
                self.assertEqual(resource.meta, {"ui": {"prefersBorder": True}})
                self.assertIn("ui/initialize", resource.text)
                self.assertIn("ui/notifications/tool-result", resource.text)

                work = await client.call_tool("check_work", {"steps": [["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]],
                                                             "notebook": "test family", "learner": "Maya"})
                self.assertFalse(work.isError)
                payload = work.structuredContent
                self.assertEqual((payload["status"], payload["verdict"]), ("MATHEMATICALLY_REJECTED", "error_found"))
                self.assertTrue(payload["certificate_verified"])
                self.assertTrue(verify_bundle(payload["bundle"])["certificate_verified"])
                self.assertIn("suggested_speech=Almost!", work.content[0].text)
                self.assertIn("reveal_only_if_asked=Verified answer: x = 5", work.content[0].text)
                self.assertEqual(payload["saved"]["learner"], "maya")

                self.assertIn("provenance", tools["check_work"].inputSchema["properties"])
                region = {"kind": "region", "page": 1, "box": [40, 210, 520, 262]}
                pending = await client.call_tool("check_work", {
                    "steps": [["3x + 5 = 20"], ["3x = 15"], ["x = 5"]], "notebook": "test family", "learner": "Maya",
                    "provenance": [None, {"source": region, "raw": "3x = 1S",
                                          "review": {"reason": "AMBIGUOUS_SYMBOL", "note": "5 or S"}}, None]})
                self.assertFalse(pending.isError)
                held = pending.structuredContent
                self.assertEqual((held["status"], held["verdict"], held["claim"]),
                                 ("NEEDS_REVIEW", "needs_review", "work.needs_review"))
                self.assertTrue(held["certificate_verified"])
                self.assertIsNone(held["saved"])
                self.assertEqual(held["view"]["focus"]["source"], region)
                self.assertIn("next=ask the person", pending.content[0].text)
                self.assertTrue(verify_bundle(held["bundle"])["certificate_verified"])

                forged = await client.call_tool("forgery_check", {"bundle": payload["bundle"]})
                self.assertEqual(forged.structuredContent["verdict"], "forgery_rejected")
                replay = await client.call_tool("verify_certificate", {"bundle": payload["bundle"]})
                self.assertTrue(replay.structuredContent["certificate_verified"])

                tickets = await client.call_tool("solve_equations", {
                    "equations": ["a + c = 20", "12a + 7c = 300"], "domain": "nonnegative_integer",
                    "labels": {"a": "adult tickets", "c": "child tickets"}})
                self.assertEqual(tickets.structuredContent["verdict"], "no_valid_answer")
                practice = await client.call_tool("practice_problem", {"kind": "tickets", "seed": 4})
                self.assertEqual(practice.structuredContent["verdict"], "practice")
                progress = await client.call_tool("progress_report", {"notebook": "test family", "learner": "Maya"})
                self.assertIn("mistake pinpointed", progress.structuredContent["view"]["spoken"])
                nonlinear = await client.call_tool("solve_equations", {"equations": ["x*y = 2"]})
                self.assertFalse(nonlinear.isError)
                self.assertEqual(nonlinear.structuredContent["status"], "UNSUPPORTED")
                malformed = await client.call_tool("solve_equations", {"equations": ["x + 1"]})
                self.assertTrue(malformed.isError)
                self.assertEqual(malformed.structuredContent["code"], "INVALID_EQUATION")
                prompts = {p.name for p in (await client.list_prompts()).prompts}
                self.assertTrue({"homework_helper", "word_problem", "weekly_progress"} <= prompts)

    def test_standard_library_client_and_http_routes(self):
        client = MCPClient(self.url)
        result = client.connect()
        self.assertEqual(result["protocolVersion"], "2025-11-25")
        uri, visibility = ui_meta(client.tool("check_answer"))
        self.assertEqual((uri, visibility), (CARD, ["model", "app"]))
        called = client.call("check_answer", {"equations": ["2x - 7 = 1"], "answer": {"x": "4"}})
        self.assertEqual(called["structuredContent"]["verdict"], "correct")
        self.assertEqual(client.resource(CARD)["mimeType"], "text/html;profile=mcp-app")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz") as response:
            self.assertTrue(json.loads(response.read())["ok"])
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as response:
            self.assertIn(b"Show Your Work", response.read())


if __name__ == "__main__":
    unittest.main()
