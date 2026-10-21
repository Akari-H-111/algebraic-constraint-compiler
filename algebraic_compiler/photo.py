"""A photo of the page, as the simulated display sends it with a turn (standard library only).

The host checks that the bytes really are the image they claim to be, measures it and fingerprints it. It never
reads the picture: the language model does that, and whatever it reads is only an annotation for the exact
compiler (see `provenance` in linear_ir.py). Boxes on a photo use one convention everywhere:
[left, top, right, bottom] as whole numbers on a 0-1000 scale of the photo's own width and height
(0,0 is the top-left corner, 1000,1000 the bottom-right; the two axes are scaled separately).
"""

import base64
import binascii
import hashlib
import struct

TYPES = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 5_000_000  # decoded; the JSON body (base64 is 4/3 larger) is limited to MAX_BODY in simulator.py
MAX_SIDE = 12_000


class PhotoError(ValueError):
    """The photo cannot be used; `status` is the HTTP status that says why."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _jpeg_size(data):
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
        elif marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # a frame header
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        else:
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _png_size(data):
    return struct.unpack(">II", data[16:24]) if data[12:16] == b"IHDR" else None


def _webp_size(data):
    kind = data[12:16]
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None


def _size(data, mime):
    if mime == "image/jpeg" and data[:3] == b"\xff\xd8\xff":
        return _jpeg_size(data)
    if mime == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(data)
    if mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    return None


def inspect(image):
    """Validate the `{mime, data}` an API client sent: the checked photo, or PhotoError with a clear message.

    The result has mime, data (base64 text, as sent), bytes, sha256 (of the decoded bytes), width and height.
    """
    if not isinstance(image, dict) or not isinstance(image.get("mime"), str) or not isinstance(image.get("data"), str):
        raise PhotoError("image must be {mime, data}: a MIME type and base64 text")
    mime = image["mime"].strip().lower()
    if mime not in TYPES:
        raise PhotoError("Photos must be image/jpeg, image/png or image/webp", 415)
    text = image["data"].strip()
    if text.startswith("data:"):  # tolerate a pasted data URL
        text = text.partition(",")[2]
    if len(text) > MAX_BYTES * 4 // 3 + 4:
        raise PhotoError(f"The photo is larger than {MAX_BYTES // 1_000_000} MB", 413)
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise PhotoError("The photo is not valid base64") from None
    if not raw:
        raise PhotoError("The photo is empty")
    if len(raw) > MAX_BYTES:
        raise PhotoError(f"The photo is larger than {MAX_BYTES // 1_000_000} MB", 413)
    try:
        size = _size(raw, mime)
    except (struct.error, IndexError):
        size = None
    if size is None:
        raise PhotoError(f"The bytes are not a readable {mime} image")
    width, height = size
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise PhotoError(f"The photo must be 1 to {MAX_SIDE} pixels on each side")
    return {"mime": mime, "data": text, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "width": width, "height": height}


def data_url(photo):
    return f"data:{photo['mime']};base64,{photo['data']}"
