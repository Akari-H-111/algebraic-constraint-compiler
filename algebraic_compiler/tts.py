"""Gemini text-to-speech for the simulated display's spoken replies (standard library only).

The key goes in a request header, never in a URL, and is never printed. Spoken words are only
ever text the engine already produced and the screen already shows; the voice model does not
see the certificate and cannot change a verdict.
"""

import base64
import io
import json
import urllib.error
import urllib.request
import wave

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"
STYLE = ("Read this as a friendly, concise smart-home voice assistant talking to a parent in the kitchen. "
         "Warm and clear:\n\n")
MAX_CHARS = 600


class TTSError(Exception):
    """The voice service could not produce audio; callers fall back to the browser's own voice."""


def wav_from_pcm(pcm, rate=24000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def synthesize(text, key, voice=DEFAULT_VOICE, model=DEFAULT_MODEL, timeout=60, opener=urllib.request.urlopen):
    """Return (wav_bytes, seconds) for `text` spoken by a Gemini prebuilt voice."""
    text = (text or "").strip()
    if not text:
        raise TTSError("There is nothing to say")
    if len(text) > MAX_CHARS:
        raise TTSError(f"Spoken replies are limited to {MAX_CHARS} characters")
    body = {"contents": [{"parts": [{"text": STYLE + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    request = urllib.request.Request(f"{API}/models/{model}:generateContent", data=json.dumps(body).encode(),
                                     headers={"x-goog-api-key": key, "Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read())
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
    except urllib.error.HTTPError as exc:
        raise TTSError(f"Gemini TTS returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TTSError("Could not reach Gemini TTS: " + type(exc).__name__) from None
    except (KeyError, IndexError, TypeError, ValueError):
        raise TTSError("Gemini TTS returned no audio") from None
    rate = 24000
    for piece in str(part.get("mimeType", "")).split(";"):
        if piece.strip().startswith("rate="):
            try:
                rate = int(piece.split("=", 1)[1])
            except ValueError:
                raise TTSError("Gemini TTS returned audio in a format this display cannot read") from None
    if not 8000 <= rate <= 192000:
        raise TTSError("Gemini TTS returned audio in a format this display cannot read")
    return wav_from_pcm(pcm, rate), len(pcm) / 2 / rate
