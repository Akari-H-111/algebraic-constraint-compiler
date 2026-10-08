"""Simulated Alexa+ host: guided mode over real HTTP, a scripted agent loop, and provider helpers."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
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

import hashlib
import io
import wave

from algebraic_compiler import llm, notebook, simulator, tts
from algebraic_compiler.llm import Call, simplify_schema, sigv4

ROOT = Path(__file__).resolve().parents[1]


class StaticDemoTests(unittest.TestCase):
    def test_static_backend_forwards_every_service_parameter(self):
        """The in-browser demo keeps its own argument whitelist; a parameter missing there is silently dropped."""
        js = (ROOT / "tools" / "site" / "static-backend.js").read_text()
        functions = dict(re.findall(r"'(\w+)': service\.(\w+)(?=\s*[,}])", js))
        listed = {tool: re.findall(r"'(\w+)'", args) for tool, args in re.findall(r"'(\w+)': \(([^)]*)\)", js)}
        tree = ast.parse((ROOT / "algebraic_compiler" / "service.py").read_text())
        params = {n.name: [a.arg for a in n.args.args] for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertEqual(set(functions), set(listed))
        for tool, function in functions.items():
            self.assertEqual(sorted(listed[tool]), sorted(params[function]), tool)


class RecordedVoiceTests(unittest.TestCase):
    """The page plays a recorded Gemini TTS clip only for the exact text it was recorded from."""

    def test_every_clip_exists_and_matches_the_engine_wording(self):
        voice = ROOT / "algebraic_compiler" / "web" / "alexa" / "voice"
        manifest = json.loads((voice / "manifest.json").read_text())
        self.assertEqual(manifest["provider"], "Gemini TTS")
        spec = importlib.util.spec_from_file_location("make_voice", ROOT / "tools" / "site" / "make_voice.py")
        make_voice = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ):
            try:
                spec.loader.exec_module(make_voice)
                texts = make_voice.replies()
            finally:
                notebook.reset_shared(None)
        for ident, text in texts.items():
            digest = hashlib.sha256(text.encode()).hexdigest()
            clip = manifest["clips"].get(digest)
            self.assertIsNotNone(clip, f"{ident}: its wording changed, so re-record it with tools/site/make_voice.py")
            self.assertEqual((clip["id"], clip["text_sha256"]), (ident, digest))
            self.assertGreater((voice / clip["file"]).stat().st_size, 5000)
        self.assertEqual(len(manifest["clips"]), len(texts))
        # ffmpeg is looked up explicitly, then on PATH, then in the local tools folder; a clear error when absent.
        self.assertEqual(make_voice.find_ffmpeg(__file__), Path(__file__))
        with mock.patch.object(make_voice, "FFMPEG", Path("/nonexistent/ffmpeg")), mock.patch.object(make_voice.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit):
                make_voice.find_ffmpeg()


class GeminiVoiceTests(unittest.TestCase):
    class Reply:
        def __init__(self, body): self.body = body
        def read(self): return json.dumps(self.body).encode()
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    def test_synthesize_sends_the_key_in_a_header_and_returns_wav(self):
        import base64
        pcm = b"\x01\x00" * 12000
        seen = {}

        def opener(request, timeout):
            seen.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data))
            return self.Reply({"candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]})
        wav, seconds = tts.synthesize("x = 6 and y = 4.", "secret-key", "Kore", "some-tts-model", opener=opener)
        self.assertNotIn("secret-key", seen["url"])
        self.assertEqual(seen["headers"]["X-goog-api-key"], "secret-key")
        self.assertTrue(seen["url"].endswith("/models/some-tts-model:generateContent"))
        config = seen["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
        self.assertEqual(config["voiceName"], "Kore")
        self.assertTrue(seen["body"]["contents"][0]["parts"][0]["text"].endswith("x = 6 and y = 4."))
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 24000, 12000))
        self.assertAlmostEqual(seconds, 0.5)

    def test_failures_become_tts_errors_without_leaking_the_key(self):
        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        with self.assertRaises(tts.TTSError) as caught:
            tts.synthesize("hello", "secret-key", opener=refused)
        self.assertIn("429", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))
        with self.assertRaises(tts.TTSError):
            tts.synthesize("hello", "k", opener=lambda request, timeout: self.Reply({"candidates": []}))
        with self.assertRaises(tts.TTSError):
            tts.synthesize("x" * (tts.MAX_CHARS + 1), "k")
        for mime in ("audio/L16;codec=pcm;rate=0", "audio/L16;codec=pcm;rate=abc"):  # an unreadable sample rate is an error, not a crash
            bad = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": "AAA="}}]}}]}
            with self.assertRaises(tts.TTSError):
                tts.synthesize("hello", "k", opener=lambda request, timeout, bad=bad: self.Reply(bad))


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
        self.assertEqual(verdicts, {"work": "error_found", "review": "needs_review", "tickets": "no_valid_answer",
                                    "practice": "practice",
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

    def test_recorded_clips_are_served_and_live_voice_needs_a_key(self):
        with urllib.request.urlopen(self.base + "/voice/manifest.json") as response:
            self.assertEqual(json.loads(response.read())["provider"], "Gemini TTS")
        with urllib.request.urlopen(self.base + "/voice/work.mp3") as response:
            self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
        for bad in ("/voice/nope.mp3", "/voice/..%2Fsimulator.py", "/voice/manifest.json/../../alexa.js", "/voice/.hidden"):
            status, _ = self.request(bad)
            self.assertEqual(status, 404, bad)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertFalse(self.request("/api/config")[1]["voice"]["live"])
            status, body = self.request("/api/tts", {"text": "Hello"})
            self.assertEqual(status, 501)
            self.assertIn("GEMINI_API_KEY", body["error"])

    def test_live_voice_is_cached_validated_and_reports_failures(self):
        calls = []

        def fake(text, key, voice, model, **kwargs):
            calls.append((text, key, voice, model))
            return tts.wav_from_pcm(b"\x00\x00" * 2400), 0.1
        env = {"GEMINI_API_KEY": "test-key", "SYW_TTS": "", "SYW_TTS_MODEL": "model-x", "SYW_TTS_VOICE": "Puck"}
        with mock.patch.dict(os.environ, env), mock.patch.object(simulator.tts, "synthesize", fake):
            self.host.voice_cache.clear()
            config = self.request("/api/config")[1]["voice"]
            self.assertEqual((config["live"], config["voice"], config["model"]), (True, "Puck", "model-x"))
            # Only a reply this display has shown can be spoken, and it is spoken exactly as shown.
            shown = self.request("/api/guided", {"scenario": "answer"})[1]["reply"]
            for _ in range(2):
                req = urllib.request.Request(self.base + "/api/tts", data=json.dumps({"text": shown}).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.headers["Content-Type"], "audio/wav")
                    self.assertEqual((response.headers["X-Voice"], response.headers["X-Voice-Model"]), ("Puck", "model-x"))
                    self.assertEqual(response.read()[:4], b"RIFF")
            self.assertEqual(calls, [(shown, "test-key", "Puck", "model-x")])  # second request came from the cache
            self.assertEqual(self.request("/api/tts", {"text": "  "})[0], 400)
            self.assertEqual(self.request("/api/tts", {"text": "x" * (tts.MAX_CHARS + 1)})[0], 400)
            self.assertEqual(self.request("/api/tts", {"text": "Say anything you like."})[0], 403)
            self.assertEqual(len(calls), 1)  # the refused text never reached the voice service

            # Only pages this server serves may use its API (a page on another site must not spend the keys).
            def post(headers, text=shown):
                req = urllib.request.Request(self.base + "/api/tts", data=json.dumps({"text": text}).encode(), headers=headers)
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status
                except urllib.error.HTTPError as exc:
                    return exc.code
            json_type = {"Content-Type": "application/json"}
            self.assertEqual(post({**json_type, "Origin": "http://evil.example"}), 403)
            self.assertEqual(post({**json_type, "Origin": "null"}), 403)
            self.assertEqual(post({**json_type, "Origin": self.base}), 200)
            self.assertEqual(post({**json_type, "Host": "rebound.example"}), 403)
            self.assertEqual(post({"Content-Type": "text/plain"}), 415)
            req = urllib.request.Request(self.base + "/api/config", headers={"Host": "rebound.example"})
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(req)
            self.assertEqual(refused.exception.code, 403)
            self.assertEqual(len(calls), 1)

            other = self.request("/api/guided", {"scenario": "system"})[1]["reply"]

            def broken(text, key, voice, model, **kwargs):
                raise tts.TTSError("Gemini TTS returned HTTP 503")
            with mock.patch.object(simulator.tts, "synthesize", broken):
                status, body = self.request("/api/tts", {"text": other})
            self.assertEqual((status, body["error"]), (502, "Gemini TTS returned HTTP 503"))

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
