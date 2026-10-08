"""Record the spoken replies of the Guided requests with Gemini TTS (key from the git-ignored .env).

    python3 tools/site/make_voice.py [--model NAME] [--voice Kore] [--ffmpeg PATH] [--only id,id]

For each Guided request whose spoken reply does not depend on notebook state, this runs the same
engine call the web demo makes, takes the reply text, speaks it with Gemini TTS, and writes
algebraic_compiler/web/alexa/voice/<id>.mp3 plus manifest.json. The manifest is keyed by the SHA-256 of
the exact reply text, so the page plays a clip only when its recorded text is what is on the screen.
Clips are recordings of a Gemini TTS call made when this script ran; the page labels them that way.
The key is sent in a header, never in a URL, and is never printed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from algebraic_compiler import tts  # noqa: E402
from algebraic_compiler.llm import load_env  # noqa: E402
from algebraic_compiler.simulator import SCENARIOS  # noqa: E402

OUT = ROOT / "algebraic_compiler/web/alexa/voice"
FFMPEG = ROOT / ".local-qa/media-tools/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1"
MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
# progress_report speaks a summary of whatever the notebook holds, so its wording is not fixed. The likely
# states of a first visit are recorded (nothing yet; after "Check Maya's homework"; after that and the ticket
# problem, the order of the demo video). Any other state is spoken by the browser's voice, and the page says so.
STATEFUL = {"progress"}
PROGRESS_STATES = [("progress-empty", ()), ("progress-after-work", ("work",)), ("progress-after-work-and-tickets", ("work", "tickets"))]


def engine():
    """The web demo's own Python glue (the block inside static-backend.js), so replies match exactly.

    Each call starts from an empty notebook, like a fresh page load of the web demo.
    """
    from algebraic_compiler import notebook
    js = (ROOT / "tools/site/static-backend.js").read_text()
    code = re.search(r"py\.runPython\(`\n(.*?)`\);", js, re.S).group(1)
    path = str(Path(tempfile.mkdtemp(prefix="syw-voice-")) / "notebook.sqlite3")
    os.environ["SYW_NOTEBOOK_PATH"] = path
    notebook.reset_shared(path)
    namespace = {}
    exec(compile(code, "static-backend.js", "exec"), namespace)  # noqa: S102 - the repository's own source
    return namespace["call"]


def spoken(call, scenario):
    arguments = dict(scenario["arguments"], notebook="rivera family", learner="Maya")
    payload = json.loads(call(scenario["tool"], json.dumps(arguments)))["structuredContent"]
    return payload["view"]["spoken"]


def replies():
    """{clip id: exact reply text} for every wording that gets a recording."""
    by_id = {s["id"]: s for s in SCENARIOS}
    found = {}
    for scenario in SCENARIOS:
        if scenario["id"] not in STATEFUL:
            call = engine()
            text = spoken(call, scenario)
            assert text == spoken(engine(), scenario), f"{scenario['id']}: the spoken reply is not stable"
            found[scenario["id"]] = text
    for ident, before in PROGRESS_STATES:
        call = engine()
        for step in before:
            spoken(call, by_id[step])
        found[ident] = spoken(call, by_id["progress"])
    assert len(set(found.values())) == len(found), "two clips would have the same wording"
    return found


def speak(text, key, voice, models):
    last = None
    for model in models:
        for attempt in range(3):
            try:
                wav, seconds = tts.synthesize(text, key, voice, model, timeout=120)
                return wav, seconds, model
            except tts.TTSError as exc:
                last = f"{model}: {exc}"
                if "429" in str(exc) or "503" in str(exc):
                    time.sleep(15 * (attempt + 1))
                    continue
                break
    raise SystemExit("Gemini TTS failed for every model tried (" + str(last) + ")")


def encode(wav, ffmpeg, path):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "clip.wav"
        source.write_bytes(wav)
        subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-i", str(source), "-ac", "1", "-ar", "24000",
                        "-codec:a", "libmp3lame", "-b:a", "48k", str(path)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Use only this Gemini TTS model (default: 3.1 flash, then 2.5 flash)")
    parser.add_argument("--voice", default=tts.DEFAULT_VOICE)
    parser.add_argument("--ffmpeg", default=str(FFMPEG))
    parser.add_argument("--only", help="Comma-separated scenario ids to (re)record; others keep their clips")
    args = parser.parse_args()
    load_env(str(ROOT / ".env"))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set (add it to .env)")
    only = set(args.only.split(",")) if args.only else None
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"clips": {}}
    clips = {h: c for h, c in old["clips"].items() if (OUT / c["file"]).exists()}
    wanted = replies()
    keep = {hashlib.sha256(t.encode()).hexdigest() for t in wanted.values()}
    clips = {h: c for h, c in clips.items() if h in keep}  # drop clips whose wording has changed
    for ident, text in wanted.items():
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in clips and (not only or ident not in only):
            print(f"keep   {ident}")
            continue
        wav, seconds, model = speak(text, key, args.voice, [args.model] if args.model else MODELS)
        encode(wav, Path(args.ffmpeg), OUT / f"{ident}.mp3")
        clips[digest] = {"id": ident, "file": f"{ident}.mp3", "model": model, "seconds": round(seconds, 1),
                         "text_sha256": digest, "recorded": time.strftime("%Y-%m-%d")}
        print(f"record {ident}: {seconds:.1f}s with {model}")
    manifest = {"provider": "Gemini TTS", "voice": args.voice, "note": "Each clip is a recording of a Gemini TTS call "
                "made when tools/site/make_voice.py ran; it plays only for the exact text it was recorded from.",
                "clips": dict(sorted(clips.items(), key=lambda item: item[1]["id"]))}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"wrote {manifest_path.relative_to(ROOT)} with {len(clips)} clips")


if __name__ == "__main__":
    main()
