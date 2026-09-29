"""Assemble recorded scene frames into the demo video with captions and optional narration.

    python3 tools/video/build.py <frames_dir> <out.mp4> [--burn] [--ffmpeg PATH]

Scenes come from <frames_dir>/scenes.json (written by record.mjs). If
<frames_dir>/narration/<scene>.wav exists, it is placed at the scene start and
the scene's last frame is held when the narration runs longer. Captions are
written next to the video as .srt; --burn also renders them into the picture.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import wave

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FFMPEG = ROOT / ".local-qa/media-tools/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1"


def run(cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def wav_seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def stamp(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunks(text, limit=84):
    """Split narration into caption lines at sentence or clause boundaries."""
    pieces, current = [], ""
    for part in re.split(r"(?<=[.?!:,])\s+", text.strip()):
        if current and len(current) + 1 + len(part) > limit:
            pieces.append(current)
            current = part
        else:
            current = (current + " " + part).strip()
    if current:
        pieces.append(current)
    return pieces


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames")
    parser.add_argument("out")
    parser.add_argument("--burn", action="store_true")
    parser.add_argument("--ffmpeg", default=str(DEFAULT_FFMPEG))
    args = parser.parse_args()
    frames, out, ff = Path(args.frames), Path(args.out), args.ffmpeg
    meta = json.loads((frames / "scenes.json").read_text())
    text = json.loads((ROOT / "tools/video/narration.json").read_text())["scenes"]
    fps = meta["fps"]
    work = frames / "_build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    clips, srt, audio_inputs, t = [], [], [], 0.0
    for scene in meta["scenes"]:
        name, seconds = scene["name"], scene["seconds"]
        voice = frames / "narration" / f"{name}.wav"
        spoken = wav_seconds(voice) if voice.exists() else 0.0
        length = max(seconds, spoken + 0.6)
        clip = work / f"{name}.mp4"
        hold = max(0.0, length - seconds)
        vf = "fps=30,format=yuv420p" + (f",tpad=stop_mode=clone:stop_duration={hold:.2f}" if hold else "")
        run([ff, "-y", "-loglevel", "error", "-framerate", fps, "-i", frames / name / "%05d.jpg",
             "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "30", clip])
        clips.append(clip)
        if voice.exists():
            audio_inputs.append((voice, t + 0.3))
        lines = chunks(text.get(name, ""))
        if lines:
            span = (spoken or seconds - 1.0) / len(lines)
            for i, line in enumerate(lines):
                start = t + 0.3 + i * span
                srt.append((start, start + span - 0.05, line))
        t += length
    listing = work / "clips.txt"
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    silent = work / "silent.mp4"
    run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", silent])
    captions = out.with_suffix(".srt")
    captions.write_text("".join(f"{i + 1}\n{stamp(a)} --> {stamp(b)}\n{line}\n\n" for i, (a, b, line) in enumerate(srt)))
    cmd = [ff, "-y", "-loglevel", "error", "-i", silent]
    for voice, _ in audio_inputs:
        cmd += ["-i", voice]
    filters, video_label = [], "0:v"
    if args.burn:
        style = "FontName=Helvetica,FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=6,Shadow=0,MarginV=26"
        escaped = str(captions.resolve()).replace(":", r"\:").replace("'", r"\'")
        filters.append(f"[0:v]subtitles='{escaped}':force_style='{style}'[v]")
        video_label = "[v]"
    if audio_inputs:
        parts = []
        for i, (_, start) in enumerate(audio_inputs, 1):
            delay = int(start * 1000)
            filters.append(f"[{i}:a]aresample=48000,adelay={delay}|{delay}[a{i}]")
            parts.append(f"[a{i}]")
        filters.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0,apad[a]")
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", video_label if video_label != "0:v" else "0:v"]
    if audio_inputs:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    run(cmd)
    print(f"{out} ({t:.1f}s), captions {captions}")


if __name__ == "__main__":
    main()
