"""Photo homework: image validation on the host and the multimodal message each model provider receives."""

import base64
import hashlib
import json
from pathlib import Path
import struct
import unittest
from unittest import mock
import zlib

from algebraic_compiler import llm, photo, simulator
from algebraic_compiler.photo import PhotoError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "algebraic_compiler" / "web" / "alexa" / "samples" / "maya-homework.jpg"


def png(width=3, height=2):
    """A real, tiny PNG (a grey rectangle) without any imaging library."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def webp_header(width, height):
    """The header of an extended WebP file (VP8X): enough for the host to read its size."""
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def b64(raw):
    return base64.b64encode(raw).decode()


class PhotoValidationTests(unittest.TestCase):
    def test_accepts_jpeg_png_webp_and_reports_size_and_digest(self):
        jpeg = SAMPLE.read_bytes()
        checked = photo.inspect({"mime": "image/jpeg", "data": b64(jpeg)})
        self.assertEqual((checked["width"], checked["height"], checked["bytes"]), (1200, 1600, len(jpeg)))
        self.assertEqual(checked["sha256"], hashlib.sha256(jpeg).hexdigest())  # the digest is of the decoded bytes
        self.assertLess(len(jpeg), 400_000)
        raw = png(7, 5)
        checked = photo.inspect({"mime": "IMAGE/PNG", "data": b64(raw)})
        self.assertEqual((checked["mime"], checked["width"], checked["height"]), ("image/png", 7, 5))
        checked = photo.inspect({"mime": "image/webp", "data": b64(webp_header(640, 480))})
        self.assertEqual((checked["width"], checked["height"]), (640, 480))
        # A pasted data URL is tolerated, and the data URL for the model round-trips.
        wrapped = photo.inspect({"mime": "image/png", "data": "data:image/png;base64," + b64(raw)})
        self.assertEqual(wrapped["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(photo.data_url(wrapped), "data:image/png;base64," + b64(raw))

    def test_bad_photos_are_refused_with_a_clear_message_and_status(self):
        good = b64(png())
        cases = [({"mime": "image/gif", "data": good}, 415, "image/jpeg"),
                 ({"mime": "application/pdf", "data": good}, 415, "image/jpeg"),
                 ({"mime": "image/png", "data": "not base64 !!"}, 400, "base64"),
                 ({"mime": "image/png", "data": ""}, 400, "empty"),
                 ({"mime": "image/png", "data": b64(b"just some text")}, 400, "not a readable"),
                 ({"mime": "image/jpeg", "data": good}, 400, "not a readable"),  # a PNG that claims to be a JPEG
                 ({"mime": "image/png", "data": b64(png()[:20])}, 400, "not a readable"),  # truncated header
                 ({"mime": "image/png"}, 400, "{mime, data}"), ({"data": good}, 400, "{mime, data}"),
                 ({"mime": "image/png", "data": 5}, 400, "{mime, data}"), ("a string", 400, "{mime, data}"),
                 ({"mime": "image/webp", "data": b64(webp_header(20000, 10))}, 400, "pixels")]
        for image, status, words in cases:
            with self.assertRaises(PhotoError, msg=str(image)[:60]) as caught:
                photo.inspect(image)
            self.assertEqual(caught.exception.status, status, str(image)[:60])
            self.assertIn(words, str(caught.exception))
        big = b64(png() + b"\x00" * photo.MAX_BYTES)
        with self.assertRaises(PhotoError) as caught:
            photo.inspect({"mime": "image/png", "data": big})
        self.assertEqual(caught.exception.status, 413)
        # PhotoError is a ValueError, so a caller that only knows ValueError still treats it as a bad request.
        self.assertTrue(issubclass(PhotoError, ValueError))


class ProviderPhotoTests(unittest.TestCase):
    """Each provider turns the same checked photo into its own request format. No network: the HTTP and SDK seams are replaced."""

    def setUp(self):
        self.raw = SAMPLE.read_bytes()
        self.image = photo.inspect({"mime": "image/jpeg", "data": b64(self.raw)})
        self.tools = [{"name": "check_work", "description": "d", "inputSchema": {"type": "object", "properties": {}}}]

    def test_without_a_photo_the_messages_are_unchanged(self):
        for provider, expected in ((llm.OpenAICompatible("x", "http://h", "k", "m"), "hi"), (llm.Bedrock.__new__(llm.Bedrock), [{"text": "hi"}]),
                                   (llm.Anthropic.__new__(llm.Anthropic), "hi")):
            messages = []
            provider.user(messages, "hi")
            self.assertEqual(messages, [{"role": "user", "content": expected}])

    def test_openai_compatible_and_gemini_send_an_image_url_data_url(self):
        provider = llm.OpenAICompatible("gemini", "https://example.invalid/v1", "k", "gemini-2.5-flash")
        messages = []
        provider.user(messages, "check this page", self.image)
        sent = {}

        def fake(url, body, headers, **kwargs):
            sent.update(url=url, body=body, headers=headers)
            return {"choices": [{"message": {"content": "ok"}}]}
        with mock.patch.object(llm, "_http_json", fake):
            self.assertEqual(provider.complete("SYSTEM", self.tools, messages)[0], "ok")
        user = sent["body"]["messages"][1]
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["content"][0], {"type": "text", "text": "check this page"})
        self.assertEqual(user["content"][1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64(self.raw)}})
        self.assertEqual(sent["body"]["messages"][0], {"role": "system", "content": "SYSTEM"})

    def test_anthropic_sends_a_base64_image_block_before_the_text(self):
        provider = llm.Anthropic.__new__(llm.Anthropic)  # skip the SDK and the key: only the request is checked
        provider.model, provider.client = "claude-test", mock.Mock()
        provider.client.beta.messages.create.return_value = mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text="Looks fine.")])
        messages = []
        provider.user(messages, "check this page", self.image)
        self.assertEqual(provider.complete("SYSTEM", self.tools, messages)[0], "Looks fine.")
        sent = provider.client.beta.messages.create.call_args.kwargs["messages"][0]
        self.assertEqual(sent["content"], [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(self.raw)}},
            {"type": "text", "text": "check this page"}])

    def test_bedrock_converse_sends_an_image_block_with_format_and_bytes(self):
        provider = llm.Bedrock.__new__(llm.Bedrock)
        provider.model, provider.region, provider.bearer = "us.amazon.nova-lite-v1:0", "us-east-1", "token"
        provider.access = provider.secret = provider.token = None
        sent = {}

        def fake(url, body, headers, **kwargs):
            sent.update(url=url, body=body, headers=headers)
            return {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}}
        for mime, name in (("image/jpeg", "jpeg"), ("image/png", "png"), ("image/webp", "webp")):
            image = {"mime": mime, "data": b64(self.raw)}
            messages = []
            provider.user(messages, "check this page", image)
            with mock.patch.object(llm, "_http_json", fake):
                provider.complete("SYSTEM", self.tools, messages)
            self.assertEqual(sent["body"]["messages"][0]["content"],
                             [{"text": "check this page"}, {"image": {"format": name, "source": {"bytes": b64(self.raw)}}}])
        self.assertEqual(sent["headers"], {"Authorization": "Bearer token"})
        self.assertTrue(sent["url"].endswith("/converse"))

    def test_the_system_prompt_states_the_photo_rules_and_the_box_convention(self):
        system = simulator.SYSTEM.format(notebook="n", learner="Maya", instructions="")
        for words in ("exactly as the child wrote it", "never fix", "The first line is the original problem", "0 to 1000",
                      "[left, top, right, bottom]", "never y first", "[200, 300, 600, 360]", '"kind": "region"', '"page": 1', "review",
                      "AMBIGUOUS_SYMBOL", "best reading"):
            self.assertIn(words.lower(), system.lower(), words)
        json.dumps(system)


if __name__ == "__main__":
    unittest.main()
