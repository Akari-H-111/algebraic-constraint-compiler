"""Simulated Alexa+ experience: a voice-first smart-display host for the Show Your Work MCP server.

This is an MCP host, not an Amazon product. It connects to the MCP server over
Streamable HTTP, lets a configured language model choose tools, renders the
server's MCP Apps card inside a sandboxed iframe, and shows every tool call.
Without a configured model it offers Guided mode: preset typed requests that
call the same MCP tools, clearly labelled as involving no AI interpretation.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import llm
from .llm import ProviderError
from .mcp_client import MCPClient, MCPError, ui_meta

WEB = Path(__file__).with_name("web") / "alexa"
MAX_BODY = 1_000_000
MAX_ROUNDS = 6

SYSTEM = """You are Alexa+, a voice assistant on a kitchen smart display, using the Show Your Work skill.
This is a simulated Alexa+ experience for a hackathon demo, not an Amazon product.
Household notebook: "{notebook}". The person talking may be a parent or the learner {learner}.

Voice rules:
- Answer in at most two short spoken sentences. No markdown, lists, emoji or LaTeX.
- For any math, call a Show Your Work tool. Never calculate or state a number yourself.
- Say "verified" only when the tool result has certificate_verified=true.
- Homework: check the child's work or answer; give the hint first and reveal the answer only when asked.
- Always pass notebook="{notebook}" and learner="{learner}" to tools that accept them.
- The certificate card appears on screen automatically; you can say "it's on the screen".
- If something is unsupported or not verified, say so plainly and do not guess.

{instructions}"""

SCENARIOS = [
    {"id": "work", "label": "Check Maya's homework",
     "utterance": "Alexa, check Maya's homework. The problem is 3x + 5 = 20. She wrote 3x = 25, then x = 25 over 3.",
     "tool": "check_work", "arguments": {"steps": [["3x + 5 = 20"], ["3x = 25"], ["x = 25/3"]]}},
    {"id": "tickets", "label": "A worksheet with a typo",
     "utterance": "Alexa, adult tickets cost $12 and child tickets cost $7. They sold 20 tickets for $300. How many child tickets?",
     "tool": "solve_equations", "arguments": {"equations": ["a + c = 20", "12a + 7c = 300"], "domain": "nonnegative_integer",
                                               "labels": {"a": "adult tickets", "c": "child tickets"},
                                               "question": "Adult tickets cost $12 and child tickets cost $7. 20 tickets sold for $300. How many child tickets?"}},
    {"id": "practice", "label": "Practice problem",
     "utterance": "Alexa, give Maya a practice problem like that one.",
     "tool": "practice_problem", "arguments": {"kind": "two_step", "seed": 2026}},
    {"id": "answer", "label": "Is x = 4 right?",
     "utterance": "Alexa, Maya says x equals 4 for 2x - 7 = 1. Is that right?",
     "tool": "check_answer", "arguments": {"equations": ["2x - 7 = 1"], "answer": {"x": "4"}}},
    {"id": "system", "label": "Two equations",
     "utterance": "Alexa, solve x + y = 10 and x - y = 2.",
     "tool": "solve_equations", "arguments": {"equations": ["x + y = 10", "x - y = 2"]}},
    {"id": "progress", "label": "How is Maya doing?",
     "utterance": "Alexa, how is Maya doing with math this week?",
     "tool": "progress_report", "arguments": {"days": 7}},
]


class Host:
    """One MCP connection and model provider shared by all browser sessions."""

    def __init__(self, mcp_url, notebook="rivera family", learner="Maya", providers=None):
        self.mcp = MCPClient(mcp_url)
        self.notebook, self.learner = notebook, learner
        found = llm.available() if providers is None else providers
        self.providers = {name: p for name, p in found.items() if not isinstance(p, Exception)}
        self.provider_errors = {name: str(p) for name, p in found.items() if isinstance(p, Exception)}
        preferred = os.environ.get("SYW_PROVIDER", "").lower()
        self.default = preferred if preferred in self.providers else next(iter(self.providers), None)
        self.sessions = {}
        self.lock = threading.Lock()
        self.connected = False

    def ensure(self):
        if not self.connected:
            self.mcp.connect()
            self.mcp.tools(refresh=True)
            self.connected = True

    def config(self):
        default = self.providers.get(self.default) if self.default else None
        info = {"version": __version__, "mcp_url": self.mcp.url, "notebook": self.notebook, "learner": self.learner,
                "provider": default.name if default else None, "model": default.model if default else None,
                "providers": [{"name": n, "model": p.model} for n, p in self.providers.items()],
                "provider_error": "; ".join(f"{n}: {e}" for n, e in self.provider_errors.items()) or None,
                "scenarios": [{k: s[k] for k in ("id", "label", "utterance")} for s in SCENARIOS]}
        try:
            self.ensure()
            info.update(connected=True, protocol=self.mcp.protocol, server=self.mcp.server_info,
                        tools=[{"name": t["name"], "ui": ui_meta(t)[0] is not None,
                                "visibility": ui_meta(t)[1]} for t in self.mcp.tools()])
        except MCPError as exc:
            info.update(connected=False, error=str(exc))
        return info

    def _model_tools(self):
        return [t for t in self.mcp.tools() if "model" in ui_meta(t)[1]
                and t["name"] not in ("compile_reconstruction", "verify_reconstruction")]

    def _call(self, name, arguments, events, card_holder):
        tool = self.mcp.tool(name)
        if tool is None or "model" not in ui_meta(tool)[1]:
            events.append({"type": "tool", "name": name, "arguments": arguments, "error": "Unknown tool"})
            return f"Unknown tool {name}"
        schema = (tool.get("inputSchema") or {}).get("properties", {})
        for key, value in (("notebook", self.notebook), ("learner", self.learner)):
            if key in schema and not arguments.get(key):
                arguments[key] = value
        started = time.monotonic()
        try:
            result = self.mcp.call(name, arguments)
        except MCPError as exc:
            events.append({"type": "tool", "name": name, "arguments": arguments, "error": str(exc),
                           "ms": round((time.monotonic() - started) * 1000)})
            return "Tool call failed: " + str(exc)
        payload = result.get("structuredContent") or {}
        replay = payload.get("replay") or {}
        events.append({"type": "tool", "name": name, "arguments": arguments,
                       "ms": round((time.monotonic() - started) * 1000), "status": payload.get("status"),
                       "verdict": payload.get("verdict"), "certificate_verified": payload.get("certificate_verified"),
                       "checks": f"{replay.get('checks_passed')}/{replay.get('checks_total')}" if replay else None,
                       "certificate": ((payload.get("bundle") or {}).get("certificate") or {}).get("certificate_sha256"),
                       "saved": bool(payload.get("saved"))})
        uri = ui_meta(tool)[0]
        if uri:
            card_holder["card"] = {"tool": name, "arguments": arguments, "result": result, "resourceUri": uri}
        return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")

    def turn(self, session_id, text, provider_name=None):
        self.ensure()
        events, holder = [{"type": "heard", "text": text}], {}
        name = provider_name if provider_name in self.providers else self.default
        provider = self.providers.get(name) if name else None
        if not provider:
            return {"reply": "No AI model is configured, so I can't interpret free speech. Try a Guided request.",
                    "events": events + [{"type": "notice", "text": "No model configured"}], "card": None}
        with self.lock:
            messages = self.sessions.setdefault((session_id, name), [])  # one append-only history per model
            if len(messages) > 60:  # start fresh rather than editing history
                messages.clear()
        system = SYSTEM.format(notebook=self.notebook, learner=self.learner, instructions=self.mcp.instructions)
        tools = self._model_tools()
        provider.user(messages, text)
        reply = ""
        for _ in range(MAX_ROUNDS):
            started = time.monotonic()
            try:
                reply, calls = provider.complete(system, tools, messages)
            except (ProviderError, Exception) as exc:  # any model failure is reported, never masked as math
                events.append({"type": "notice", "text": f"{provider.name}: {str(exc)[:300]}"})
                with self.lock:
                    messages.clear()
                return {"reply": "Sorry, my language model isn't reachable right now.", "events": events, "card": holder.get("card")}
            events.append({"type": "model", "provider": provider.name, "model": provider.model,
                           "ms": round((time.monotonic() - started) * 1000), "tool_calls": [c.name for c in calls]})
            if not calls:
                break
            results = [(c.id, c.name, self._call(c.name, dict(c.arguments or {}), events, holder)) for c in calls]
            provider.tool_results(messages, results)
        else:
            reply = reply or "I ran out of steps on that one."
        events.append({"type": "reply", "text": reply})
        return {"reply": reply, "events": events, "card": holder.get("card")}

    def guided(self, scenario_id):
        self.ensure()
        scenario = next((s for s in SCENARIOS if s["id"] == scenario_id), None)
        if scenario is None:
            raise ValueError("Unknown scenario")
        events, holder = [{"type": "heard", "text": scenario["utterance"], "guided": True}], {}
        self._call(scenario["tool"], json.loads(json.dumps(scenario["arguments"])), events, holder)
        card = holder.get("card")
        payload = (card or {}).get("result", {}).get("structuredContent") or {}
        reply = (payload.get("view") or {}).get("spoken") or "No result."
        events.append({"type": "reply", "text": reply, "guided": True})
        return {"reply": reply, "events": events, "card": card, "guided": True}

    def app_tool(self, name, arguments):
        """Tool calls initiated by the card: only tools whose visibility includes 'app'."""
        self.ensure()
        tool = self.mcp.tool(name)
        if tool is None or "app" not in ui_meta(tool)[1]:
            raise PermissionError("Tool not callable from the app")
        return self.mcp.call(name, arguments)

    def resource(self, uri):
        self.ensure()
        if not uri.startswith("ui://"):
            raise PermissionError("Only ui:// resources are rendered")
        return self.mcp.resource(uri, cached=False)  # hosts may cache; a demo host re-reads for freshness


def make_handler(host):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ShowYourWorkSimulator/" + __version__

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, kind="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            routes = {"/": ("index.html", "text/html; charset=utf-8"), "/alexa.js": ("alexa.js", "text/javascript; charset=utf-8"),
                      "/alexa.css": ("alexa.css", "text/css; charset=utf-8"),
                      "/sandbox.html": ("sandbox.html", "text/html; charset=utf-8")}
            if parsed.path in routes:
                name, kind = routes[parsed.path]
                extra = {"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; frame-src 'self' http://127.0.0.1:* http://localhost:*; img-src 'self' data:"} \
                    if name == "index.html" else None
                return self._send(200, (WEB / name).read_bytes(), kind, extra)
            if parsed.path == "/api/config":
                return self._send(200, host.config())
            if parsed.path == "/api/resource":
                try:
                    return self._send(200, host.resource(parse_qs(parsed.query).get("uri", [""])[0]))
                except (PermissionError, MCPError) as exc:
                    return self._send(400, {"error": str(exc)})
            self._send(404, {"error": "Not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "Invalid JSON"})
            try:
                if self.path == "/api/turn":
                    text = str(body.get("text", "")).strip()[:1000]
                    if not text:
                        return self._send(400, {"error": "Say or type something"})
                    return self._send(200, host.turn(str(body.get("session", "default"))[:64], text,
                                                     str(body.get("provider") or "") or None))
                if self.path == "/api/guided":
                    return self._send(200, host.guided(str(body.get("scenario"))))
                if self.path == "/api/tool":
                    return self._send(200, host.app_tool(str(body.get("name")), body.get("arguments") or {}))
                if self.path == "/api/reset":
                    session = str(body.get("session", "default"))[:64]
                    for key in [k for k in host.sessions if k[0] == session]:
                        host.sessions.pop(key, None)
                    return self._send(200, {"ok": True})
            except PermissionError as exc:
                return self._send(403, {"error": str(exc)})
            except (MCPError, ValueError) as exc:
                return self._send(502 if isinstance(exc, MCPError) else 400, {"error": str(exc)})
            self._send(404, {"error": "Not found"})

    return Handler


def _reachable(url):
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.3):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--mcp-url", default=os.environ.get("SYW_MCP_URL", "http://127.0.0.1:8000/mcp"))
    parser.add_argument("--notebook", default="rivera family", help="Household notebook name used by the agent")
    parser.add_argument("--learner", default="Maya")
    parser.add_argument("--no-spawn", action="store_true", help="Do not start a local MCP server automatically")
    parser.add_argument("--env-file", default=".env", help="Local git-ignored KEY=VALUE file with model keys")
    args = parser.parse_args()
    loaded = llm.load_env(args.env_file)
    child = None
    if not _reachable(args.mcp_url) and not args.no_spawn and urlparse(args.mcp_url).hostname in ("127.0.0.1", "localhost"):
        if importlib.util.find_spec("mcp") is None:
            parser.error("The MCP server is not running and the 'mcp' package is missing: pip install -e '.[mcp]'")
        port = str(urlparse(args.mcp_url).port or 8000)
        child = subprocess.Popen([sys.executable, "-m", "algebraic_compiler.mcp_server", "--port", port])
        for _ in range(100):
            if _reachable(args.mcp_url):
                break
            time.sleep(0.1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run the finally block so a spawned server stops too
    host = Host(args.mcp_url, args.notebook, args.learner)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(host))
    models = ", ".join(f"{p.name} / {p.model}" for p in host.providers.values()) or "none - Guided mode"
    print(f"Simulated Alexa+ experience: http://127.0.0.1:{args.port}  (MCP: {args.mcp_url}; models: {models}; "
          f"keys read from {args.env_file}: {', '.join(loaded) or 'none'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if child:
            child.terminate()


if __name__ == "__main__":
    main()
