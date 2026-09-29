"""Simulated Alexa+ host: guided mode over real HTTP, a scripted agent loop, and provider helpers."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from algebraic_compiler import llm, simulator
from algebraic_compiler.llm import Call, simplify_schema, sigv4

ROOT = Path(__file__).resolve().parents[1]


class ProviderHelperTests(unittest.TestCase):
    def test_schema_simplification_for_function_calling_apis(self):
        schema = {"title": "x", "type": "object", "properties": {
            "labels": {"anyOf": [{"type": "object", "additionalProperties": {"type": "string"}}, {"type": "null"}],
                       "default": None, "title": "Labels"}}}
        self.assertEqual(simplify_schema(schema), {"type": "object", "properties": {
            "labels": {"type": "object", "additionalProperties": {"type": "string"}, "default": None}}})

    def test_sigv4_double_encodes_the_model_path_and_scopes_the_credential(self):
        url = "https://bedrock-runtime.us-east-1.amazonaws.com/model/us.amazon.nova-lite-v1%3A0/converse"
        when = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        headers = sigv4(url, b"{}", "us-east-1", "bedrock", "AKIDEXAMPLE", "secret", "token", now=when)
        self.assertTrue(headers["Authorization"].startswith(
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260929/us-east-1/bedrock/aws4_request, "
            "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date;x-amz-security-token, Signature="))
        self.assertEqual(headers["x-amz-date"], "20260929T120000Z")
        self.assertEqual(headers["x-amz-security-token"], "token")
        other = sigv4(url.replace("%3A", ":"), b"{}", "us-east-1", "bedrock", "AKIDEXAMPLE", "secret", "token", now=when)
        self.assertNotEqual(headers["Authorization"], other["Authorization"])

    def test_no_model_configured_means_guided_mode(self):
        keys = ("SYW_PROVIDER", "AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertIsNone(llm.configured())
            self.assertEqual(llm.available(), {})
            os.environ["SYW_PROVIDER"] = "gemini"
            with self.assertRaises(llm.ProviderError):
                llm.configured()

    def test_env_file_loads_without_overriding_and_providers_pin_endpoints(self):
        keys = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "SYW_PROVIDER", "AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY")
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {**clean, "ANTHROPIC_BASE_URL": "http://proxy.invalid"}, clear=True):
            path = Path(tmp) / ".env"
            path.write_text("# keys\nexport GEMINI_API_KEY='g-test'\nANTHROPIC_API_KEY=a-test\nEMPTY=\n")
            os.environ["ANTHROPIC_API_KEY"] = "already-set"
            self.assertEqual(llm.load_env(str(path)), ["GEMINI_API_KEY"])
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "already-set")
            found = llm.available()
            self.assertEqual(found["gemini"].model, "gemini-2.5-flash")
            if importlib.util.find_spec("anthropic"):
                self.assertEqual(found["anthropic"].model, "claude-opus-5-5")
                self.assertEqual(str(found["anthropic"].client.base_url).rstrip("/"), "https://api.anthropic.com")


class ScriptedProvider(llm._Provider):
    """Deterministic stand-in for a language model: one tool call, then a reply."""

    name, model = "scripted", "test"

    def __init__(self):
        self.calls = 0

    def complete(self, system, tools, messages):
        self.calls += 1
        assert "never calculate" in system.lower() or "Never calculate" in system
        if self.calls == 1:
            messages.append({"role": "assistant", "content": "", "tool_calls": [1]})
            return "", [Call("c1", "check_work", {"steps": [["3x + 5 = 20"], ["3x = 25"]]})]
        text = messages[-1]["content"]
        assert "certificate_verified=true" in text and "suggested_speech=" in text
        messages.append({"role": "assistant", "content": "Almost! Check the step where 5 moves across."})
        return "Almost! Check the step where 5 moves across.", []

    def tool_results(self, messages, results):
        for call_id, _name, text in results:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": text})


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install .[mcp] with Python >=3.10 for real HTTP tests")
class SimulatorHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mcp_port = free_port()
        cls.log = tempfile.TemporaryFile(mode="w+")
        env = {**os.environ, "SYW_NOTEBOOK_PATH": str(Path(cls.tmp.name) / "nb.sqlite3")}
        cls.mcp = subprocess.Popen([sys.executable, "-B", "-m", "algebraic_compiler.mcp_server", "--port", str(cls.mcp_port)],
                                   cwd=ROOT, stdout=cls.log, stderr=cls.log, env=env)
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", cls.mcp_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        cls.host = simulator.Host(f"http://127.0.0.1:{cls.mcp_port}/mcp", "test family", "Maya", providers={})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), simulator.make_handler(cls.host))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.mcp.terminate()
        cls.mcp.wait(timeout=5)
        cls.log.close()
        cls.tmp.cleanup()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_config_static_files_and_guided_scenarios(self):
        status, config = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(config["connected"])
        self.assertEqual(config["protocol"], "2025-11-25")
        self.assertIsNone(config["provider"])
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn("frame-src", response.headers["Content-Security-Policy"])
            self.assertIn(b"Simulated Alexa+ experience", response.read())
        with urllib.request.urlopen(self.base + "/sandbox.html") as response:
            self.assertIn(b"sandbox-proxy-ready", response.read())
        verdicts = {}
        for scenario in simulator.SCENARIOS:
            status, data = self.request("/api/guided", {"scenario": scenario["id"]})
            self.assertEqual(status, 200)
            self.assertTrue(data["guided"])
            self.assertEqual(data["card"]["resourceUri"], "ui://show-your-work/certificate-card.html")
            tool_events = [e for e in data["events"] if e["type"] == "tool"]
            self.assertEqual(len(tool_events), 1)
            verdicts[scenario["id"]] = tool_events[0]["verdict"]
            self.assertTrue(data["reply"])
        self.assertEqual(verdicts, {"work": "error_found", "tickets": "no_valid_answer", "practice": "practice",
                                    "answer": "correct", "system": "unique_solution", "progress": "progress"})

    def test_app_only_tools_resources_and_free_speech_without_model(self):
        status, data = self.request("/api/guided", {"scenario": "system"})
        bundle = data["card"]["result"]["structuredContent"]["bundle"]
        status, forged = self.request("/api/tool", {"name": "forgery_check", "arguments": {"bundle": bundle}})
        self.assertEqual(status, 200)
        self.assertEqual(forged["structuredContent"]["verdict"], "forgery_rejected")
        status, _ = self.request("/api/tool", {"name": "no_such_tool", "arguments": {}})
        self.assertEqual(status, 403)
        status, resource = self.request("/api/resource?uri=ui%3A%2F%2Fshow-your-work%2Fcertificate-card.html")
        self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
        status, _ = self.request("/api/resource?uri=https%3A%2F%2Fexample.com")
        self.assertEqual(status, 400)
        status, turn = self.request("/api/turn", {"session": "t", "text": "hello"})
        self.assertIn("No AI model is configured", turn["reply"])

    def test_silent_model_falls_back_to_verified_suggested_speech(self):
        class Silent(ScriptedProvider):
            def complete(self, system, tools, messages):
                self.calls += 1
                if self.calls == 1:
                    return "", [Call("c1", "solve_equations", {"equations": ["x + y = 10", "x - y = 2"]})]
                return "", []
        self.host.providers, self.host.default = {"silent": Silent()}, "silent"
        try:
            result = self.host.turn("silent-test", "solve x + y = 10 and x - y = 2")
        finally:
            self.host.providers, self.host.default = {}, None
        self.assertEqual(result["reply"], "The answer is x = 6 and y = 4. I checked it independently, and it's the only solution.")
        self.assertIn("verified suggested speech", result["events"][-1]["source"])

    def test_scripted_agent_loop_calls_mcp_and_returns_card(self):
        self.host.providers, self.host.default = {"scripted": ScriptedProvider()}, "scripted"
        try:
            result = self.host.turn("agent-test", "Alexa, check Maya's work: 3x + 5 = 20, then 3x = 25")
        finally:
            self.host.providers, self.host.default = {}, None
        kinds = [e["type"] for e in result["events"]]
        self.assertEqual(kinds, ["heard", "model", "tool", "model", "reply"])
        tool = result["events"][2]
        self.assertEqual((tool["name"], tool["verdict"], tool["certificate_verified"]), ("check_work", "error_found", True))
        self.assertEqual(tool["arguments"]["notebook"], "test family")
        self.assertEqual(result["card"]["tool"], "check_work")
        self.assertTrue(result["reply"].startswith("Almost!"))


if __name__ == "__main__":
    unittest.main()
