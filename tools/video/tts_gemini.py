"""Synthesize demo narration and assistant replies with Gemini TTS (key from the git-ignored .env).

    python3 tools/video/tts_gemini.py <frames_dir> [--narrator Zephyr] [--assistant Kore] [--model NAME]
                                      [--assistant-model NAME] [--only a,b] [--what narration|assistant|both] [--force]

Writes <frames_dir>/narration/<scene>.wav (narrator; "pre" segments as <scene>.pre.wav) and
<frames_dir>/speech/<scene>-<n>.wav (the assistant's recorded replies). Clips that already exist are
kept (quota is small) unless --force. --assistant-model uses a different model for the assistant's replies
(for example gemini-2.5-flash-preview-tts while the narration uses gemini-3.1-flash-tts-preview).
Without <frames_dir>/scenes.json (before recording) the scene list comes from the narration file, so the
narration can be made first and the recorder can time its card reveals to the real voice.
The key is sent in a header, never in a URL, and is never printed.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from algebraic_compiler.llm import load_env  # noqa: E402

API = "https://generativelanguage.googleapis.com/v1beta"
STYLE_NARRATOR = ("Read this as the calm, confident, warm narrator of a short product demo video. "
                  "Natural pace, clear diction, no exaggeration:\n\n")
STYLE_ASSISTANT = ("Read this as a friendly, concise smart-home voice assistant talking to a parent in the kitchen. "
                   "Warm and clear:\n\n")


def request(path, body=None):
    key = os.environ["GEMINI_API_KEY"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}/{path}", data=data, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read())


def pick_model(preferred):
    if preferred:
        return preferred
    names = [m["name"].split("/", 1)[1] for m in request("models?pageSize=200").get("models", [])
             if "tts" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])]
    for wanted in ("3.1-flash", "3-flash", "2.5-flash", "2.5-pro"):
        for name in names:
            if wanted in name:
                return name
    raise SystemExit("No Gemini TTS model is available to this key: " + ", ".join(names))


def synthesize(model, voice, text, path, style):
    body = {"contents": [{"parts": [{"text": style + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    for attempt in range(6):
        try:
            data = request(f"models/{model}:generateContent", body)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 503) and attempt < 5:
                time.sleep(20 * (attempt + 1))
                continue
            raise SystemExit(f"TTS failed ({exc.code}): {exc.read()[:300].decode(errors='replace')}")
    part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    rate = 24000
    for piece in part.get("mimeType", "").split(";"):
        if piece.strip().startswith("rate="):
            rate = int(piece.split("=")[1])
    pcm = base64.b64decode(part["data"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return len(pcm) / 2 / rate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames")
    parser.add_argument("--narrator", default="Zephyr")
    parser.add_argument("--assistant", default="Kore")
    parser.add_argument("--model")
    parser.add_argument("--only", help="Comma-separated scene names to (re)generate")
    parser.add_argument("--assistant-model", help="Model for the assistant's replies (default: --model)")
    parser.add_argument("--what", choices=("narration", "assistant", "both"), default="both")
    parser.add_argument("--force", action="store_true", help="Regenerate clips that already exist")
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many API calls")
    args = parser.parse_args()
    load_env(str(ROOT / ".env"))
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not set (add it to .env)")
    frames = Path(args.frames)
    model = pick_model(args.model)
    assistant_model = args.assistant_model or model
    print("TTS model:", model, "| assistant model:", assistant_model)
    narration = json.loads((ROOT / "tools/video" / os.environ.get("NARRATION", "narration.json")).read_text())["scenes"]
    meta_path = frames / "scenes.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"scenes": [{"name": name} for name in narration]}
    only = set(args.only.split(",")) if args.only else None
    calls = 0

    def make(use_model, voice, text, path, style, label):
        nonlocal calls
        if path.exists() and not args.force:
            print(f"{label}: kept ({path.name})")
            return
        if args.limit is not None and calls >= args.limit:
            raise SystemExit(f"--limit {args.limit} reached before {label}")
        calls += 1
        seconds = synthesize(use_model, voice, text, path, style)
        print(f"{label} [{use_model}]: {seconds:.1f}s")

    for scene in meta["scenes"]:
        name = scene["name"]
        if only and name not in only:
            continue
        text = narration.get(name) or ""
        parts = text if isinstance(text, dict) else {"post": text}
        if args.what in ("narration", "both"):
            for key, suffix in (("pre", ".pre"), ("post", "")):
                if parts.get(key):
                    make(model, args.narrator, parts[key], frames / "narration" / f"{name}{suffix}.wav", STYLE_NARRATOR, f"narration {name}{suffix}")
        if args.what in ("assistant", "both"):
            for i, item in enumerate(scene.get("speech", [])):
                if item.get("silent"):  # shown on screen only, never voiced
                    continue
                make(assistant_model, args.assistant, item["text"], frames / "speech" / f"{name}-{i}.wav", STYLE_ASSISTANT, f"assistant {name}-{i}")
    print("API calls made:", calls)


if __name__ == "__main__":
    main()
