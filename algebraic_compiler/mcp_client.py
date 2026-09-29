"""Minimal standard-library MCP client for Streamable HTTP (protocol 2025-11-25).

Used by the simulated Alexa+ host. It speaks plain JSON-RPC over HTTP POST,
accepts either JSON or SSE responses, and advertises MCP Apps support so the
server's ui:// resources can be rendered by the host.
"""

import itertools
import json
import threading
import urllib.error
import urllib.request

PROTOCOL = "2025-11-25"
UI_EXTENSION = "io.modelcontextprotocol/ui"
UI_MIME = "text/html;profile=mcp-app"


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, url, timeout=60, client_name="show-your-work-alexa-simulator", version="0.3.0"):
        self.url, self.timeout = url, timeout
        self.client_info = {"name": client_name, "version": version}
        self.session_id = None
        self.server_info = None
        self.instructions = ""
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._tools = None
        self._resources = {}

    def _post(self, message, expect_response=True):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                body = response.read().decode("utf-8")
                kind = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise MCPError(f"MCP HTTP {exc.code}: {exc.read()[:300].decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"MCP server unreachable at {self.url}: {exc.reason}") from exc
        if not expect_response:
            return None
        if "text/event-stream" in kind:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if data.get("id") == message.get("id"):
                        return self._unwrap(data)
            raise MCPError("No response event in SSE stream")
        return self._unwrap(json.loads(body))

    @staticmethod
    def _unwrap(data):
        if "error" in data:
            raise MCPError(data["error"].get("message", "MCP error"))
        return data.get("result")

    def request(self, method, params=None):
        with self._lock:
            return self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}})

    def connect(self):
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL, "clientInfo": self.client_info,
            "capabilities": {"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME]}}}})
        self.server_info = result.get("serverInfo")
        self.instructions = result.get("instructions") or ""
        self.protocol = result.get("protocolVersion")
        with self._lock:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        return result

    def tools(self, refresh=False):
        if self._tools is None or refresh:
            self._tools = self.request("tools/list").get("tools", [])
        return self._tools

    def tool(self, name):
        return next((t for t in self.tools() if t["name"] == name), None)

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def resource(self, uri, cached=True):
        if uri not in self._resources or not cached:
            contents = self.request("resources/read", {"uri": uri}).get("contents", [])
            if not contents:
                raise MCPError("Empty resource " + uri)
            self._resources[uri] = contents[0]
        return self._resources[uri]


def ui_meta(tool):
    """MCP Apps linkage for a tool: (resourceUri, visibility)."""
    meta = (tool or {}).get("_meta") or {}
    ui = meta.get("ui") or {}
    uri = ui.get("resourceUri") or meta.get("ui/resourceUri")
    return uri, ui.get("visibility") or ["model", "app"]
