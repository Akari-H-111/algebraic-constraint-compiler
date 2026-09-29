"""Model providers for the simulated Alexa+ host agent (interpretation only).

The language model translates speech into tool calls and reads verified
results back. It is never asked to calculate or to judge a certificate.

Providers (first configured wins unless SYW_PROVIDER is set):
  bedrock   Amazon Bedrock Converse API (default model: Amazon Nova Lite). Auth with a
            Bedrock API key (AWS_BEARER_TOKEN_BEDROCK) or AWS_ACCESS_KEY_ID/SECRET (SigV4).
  anthropic Claude via the official `anthropic` SDK (pip install anthropic).
  gemini    Google Gemini through its OpenAI-compatible endpoint (GEMINI_API_KEY).
  openai    Any OpenAI-compatible chat endpoint (OPENAI_API_KEY, SYW_OPENAI_BASE_URL).
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(RuntimeError):
    pass


def _http_json(url, body, headers, timeout=60):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", errors="replace")
        raise ProviderError(f"Model API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Model API unreachable: {exc.reason}") from exc


def simplify_schema(schema):
    """Drop JSON-Schema features some function-calling APIs reject (anyOf-null, titles)."""
    if isinstance(schema, list):
        return [simplify_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    options = schema.get("anyOf")
    if options and len(options) == 2 and {"type": "null"} in options:
        merged = {k: v for k, v in schema.items() if k != "anyOf"}
        merged.update(next(o for o in options if o != {"type": "null"}))
        schema = merged
    return {k: simplify_schema(v) for k, v in schema.items() if k not in ("title", "$schema")}


class Call:
    __slots__ = ("id", "name", "arguments")

    def __init__(self, call_id, name, arguments):
        self.id, self.name, self.arguments = call_id, name, arguments


class _Provider:
    name = "none"
    model = None

    def user(self, messages, text):
        messages.append({"role": "user", "content": text})

    def complete(self, system, tools, messages):
        raise NotImplementedError

    def tool_results(self, messages, results):
        raise NotImplementedError


class OpenAICompatible(_Provider):
    def __init__(self, name, base_url, api_key, model):
        self.name, self.base_url, self.api_key, self.model = name, base_url.rstrip("/"), api_key, model

    def complete(self, system, tools, messages):
        body = {"model": self.model, "messages": [{"role": "system", "content": system}] + messages,
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                            "parameters": simplify_schema(t["inputSchema"])}}
                          for t in tools],
                "tool_choice": "auto", "temperature": 0.2}
        data = _http_json(self.base_url + "/chat/completions", body, {"Authorization": "Bearer " + self.api_key})
        message = data["choices"][0]["message"]
        assistant = {"role": "assistant", "content": message.get("content") or ""}
        calls = []
        for item in message.get("tool_calls") or []:
            try:
                arguments = json.loads(item["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(Call(item["id"], item["function"]["name"], arguments))
        if message.get("tool_calls"):
            assistant["tool_calls"] = message["tool_calls"]
        messages.append(assistant)
        return assistant["content"], calls

    def tool_results(self, messages, results):
        for call_id, _name, text in results:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": text})


class Anthropic(_Provider):
    """Claude through the official SDK. History is append-only, including thinking blocks."""

    name = "anthropic"

    def __init__(self, model=None):
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("pip install anthropic to use the Claude provider") from exc
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("Set ANTHROPIC_API_KEY")
        # Pin the endpoint and key explicitly: an unrelated ANTHROPIC_BASE_URL or token in the
        # environment (for example another tool's proxy) must never receive this key.
        self.client = anthropic.Anthropic(api_key=key,
                                          base_url=os.environ.get("SYW_ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
        self.model = model or "claude-opus-5-5"

    def complete(self, system, tools, messages):
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=16000, system=system, messages=messages,
            tools=[{"name": t["name"], "description": t.get("description", ""), "input_schema": t["inputSchema"]}
                   for t in tools],
            output_config={"effort": "low"},  # voice turns: short, fast tool routing
            betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"})
        if response.stop_reason == "refusal":
            messages.append({"role": "assistant", "content": response.content})
            return "Sorry, I can't help with that one.", []
        messages.append({"role": "assistant", "content": response.content})
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        calls = [Call(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"]
        return text, calls

    def tool_results(self, messages, results):
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": text} for call_id, _name, text in results]})


class Bedrock(_Provider):
    """Amazon Bedrock Converse API with tool use; standard library only."""

    name = "bedrock"

    def __init__(self, model=None, region=None):
        self.model = model or "us.amazon.nova-lite-v1:0"
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        self.bearer = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        self.access = os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.token = os.environ.get("AWS_SESSION_TOKEN")
        if not self.bearer and not (self.access and self.secret):
            raise ProviderError("Set AWS_BEARER_TOKEN_BEDROCK or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY")

    def user(self, messages, text):
        messages.append({"role": "user", "content": [{"text": text}]})

    def complete(self, system, tools, messages):
        url = (f"https://bedrock-runtime.{self.region}.amazonaws.com/model/"
               f"{urllib.parse.quote(self.model, safe='')}/converse")
        body = {"messages": messages, "system": [{"text": system}],
                "toolConfig": {"tools": [{"toolSpec": {"name": t["name"], "description": t.get("description", "")[:1000],
                                                        "inputSchema": {"json": simplify_schema(t["inputSchema"])}}}
                                         for t in tools]},
                "inferenceConfig": {"maxTokens": 1200, "temperature": 0}}
        payload = json.dumps(body).encode("utf-8")
        headers = ({"Authorization": "Bearer " + self.bearer} if self.bearer
                   else sigv4(url, payload, self.region, "bedrock", self.access, self.secret, self.token))
        data = _http_json(url, body, headers)
        message = data["output"]["message"]
        messages.append(message)
        text = " ".join(part["text"] for part in message["content"] if "text" in part).strip()
        calls = [Call(part["toolUse"]["toolUseId"], part["toolUse"]["name"], part["toolUse"].get("input") or {})
                 for part in message["content"] if "toolUse" in part]
        return text, calls

    def tool_results(self, messages, results):
        messages.append({"role": "user", "content": [
            {"toolResult": {"toolUseId": call_id, "content": [{"text": text}]}} for call_id, _name, text in results]})


def sigv4(url, payload, region, service, access_key, secret_key, session_token=None, now=None):
    """AWS Signature Version 4 headers for a JSON POST (non-S3: path is encoded twice)."""
    parsed = urllib.parse.urlparse(url)
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"content-type": "application/json", "host": parsed.netloc, "x-amz-date": amz_date,
               "x-amz-content-sha256": payload_hash}
    if session_token:
        headers["x-amz-security-token"] = session_token
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["POST", urllib.parse.quote(parsed.path, safe="/-_.~"), parsed.query,
                           "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret_key).encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    result = {k: v for k, v in headers.items() if k != "host"}
    result["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return result


def load_env(path=".env"):
    """Read KEY=VALUE lines from a local, git-ignored file without overriding the environment."""
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except OSError:
        return []
    loaded = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def build(choice, model=None):
    """Instantiate one named provider."""
    if choice == "bedrock":
        return Bedrock(model)
    if choice == "anthropic":
        return Anthropic(model)
    if choice == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("Set GEMINI_API_KEY")
        return OpenAICompatible("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", key,
                                model or os.environ.get("SYW_GEMINI_MODEL", "gemini-2.5-flash"))
    if choice == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("Set OPENAI_API_KEY")
        return OpenAICompatible("openai", os.environ.get("SYW_OPENAI_BASE_URL", "https://api.openai.com/v1"), key,
                                model or "gpt-4.1-mini")
    raise ProviderError("Provider must be bedrock, anthropic, gemini or openai")


def available():
    """Every provider whose credentials are present, in preference order: {name: provider or error}."""
    signals = {"bedrock": os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or (
                   os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")),
               "anthropic": os.environ.get("ANTHROPIC_API_KEY"), "gemini": os.environ.get("GEMINI_API_KEY"),
               "openai": os.environ.get("OPENAI_API_KEY")}
    result = {}
    for name, present in signals.items():
        if present:
            try:
                result[name] = build(name, os.environ.get(f"SYW_{name.upper()}_MODEL"))
            except ProviderError as exc:
                result[name] = exc
    return result


def configured():
    """Return a provider instance, or None when no model is configured (guided mode)."""
    choice = os.environ.get("SYW_PROVIDER", "").lower()
    model = os.environ.get("SYW_MODEL") or None
    if not choice:
        if os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
            choice = "bedrock"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            choice = "anthropic"
        elif os.environ.get("GEMINI_API_KEY"):
            choice = "gemini"
        elif os.environ.get("OPENAI_API_KEY"):
            choice = "openai"
        else:
            return None
    if choice == "none":
        return None
    return build(choice, model)
