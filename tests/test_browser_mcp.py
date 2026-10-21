"""The web demo's browser MCP client (tools/site/mcp-client.js) against a real local server.

tests/check_browser_mcp.mjs runs the client under Node (which has fetch) through initialize, tools/list, resources/read
and tools/call. This test starts `mcp_server --cors`, runs that script, then replays the certificate it fetched with the
independent verifier in this process, the way the page replays it with Pyodide.
"""

import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from algebraic_compiler import service

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
PAGES_ORIGIN = "https://akari-h-111.github.io"
LOCAL_PAGE = "http://localhost:8123"  # a loopback server only accepts loopback origins, whatever CORS says


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install .[mcp] with Python >=3.10 for real HTTP tests")
class BrowserMCPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.log = tempfile.TemporaryFile(mode="w+")
        cls.process = subprocess.Popen(
            [sys.executable, "-B", "-m", "algebraic_compiler.mcp_server", "--cors", "--port", str(cls.port),
             "--notebook", str(Path(cls.tmp.name) / "nb.sqlite3")], cwd=ROOT, stdout=cls.log, stderr=cls.log)
        cls.url = f"http://127.0.0.1:{cls.port}/mcp"
        for _ in range(200):
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError("MCP server exited: " + cls.log.read())
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls.process.terminate()
            raise RuntimeError("MCP startup timed out")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.close()
        cls.tmp.cleanup()

    def post(self, body, origin=LOCAL_PAGE):
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Origin": origin, "Content-Type": "application/json",
                                                  "Accept": "application/json, text/event-stream"})
        return urllib.request.urlopen(request, timeout=20)

    @unittest.skipUnless(NODE, "Node.js is not installed: the browser MCP client check (tests/check_browser_mcp.mjs) needs `node`")
    def test_node_runs_the_browser_client_and_its_certificate_replays_here(self):
        done = subprocess.run([NODE, str(ROOT / "tests" / "check_browser_mcp.mjs"), self.url], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        summary = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["protocol"], "2025-11-25")
        self.assertEqual([t["method"] for t in summary["trace"] if t["ok"]][:5],
                         ["initialize", "notifications/initialized", "tools/list", "resources/read", "tools/call"])
        replay = service.verify(summary["bundle"])["replay"]
        self.assertTrue(replay["certificate_verified"])
        self.assertEqual((replay["checks_passed"], replay["checks_total"]),
                         (summary["checks"]["checks_passed"], summary["checks"]["checks_total"]))
        forged = json.loads(json.dumps(summary["bundle"]))
        forged["certificate"]["certificate_sha256"] = "0" * 64
        self.assertFalse(service.verify(forged)["certificate_verified"])  # a changed certificate does not replay

    def test_cors_lets_the_public_page_call_the_server(self):
        """What a browser does first: a preflight (here from the public page's origin) naming the headers the client sends, then the call."""
        preflight = urllib.request.Request(self.url, method="OPTIONS", headers={
            "Origin": PAGES_ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,mcp-protocol-version,mcp-session-id"})
        with urllib.request.urlopen(preflight, timeout=20) as response:
            allowed = response.headers["Access-Control-Allow-Headers"].lower()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            for header in ("content-type", "mcp-protocol-version", "mcp-session-id"):
                self.assertIn(header, allowed)
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "clientInfo": {"name": "page", "version": "1"}, "capabilities": {}}}
        with self.post(initialize) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(json.loads(response.read())["result"]["protocolVersion"], "2025-11-25")


if __name__ == "__main__":
    unittest.main()
