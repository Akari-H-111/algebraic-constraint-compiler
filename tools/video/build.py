"""Assemble recorded scene frames into the demo video with captions and optional narration.

    python3 tools/video/build.py <frames_dir> <out.mp4> [--burn] [--ffmpeg PATH]

Scenes come from <frames_dir>/scenes.json (written by record.mjs). If
<frames_dir>/narration/<scene>.wav exists, it is placed at the scene start and
the scene's last frame is held when the narration runs longer. Captions are
written next to the video as .srt; --burn also renders them into the picture.
"""

import argparse
import json
import os
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
    parser.add_argument("--tempo", type=float, default=1.0, help="Speed factor for narration clips (e.g. 1.05)")
    parser.add_argument("--first", type=float, default=None, help="Override the first scene's length (title card)")
    args = parser.parse_args()
    frames, out, ff = Path(args.frames), Path(args.out), args.ffmpeg
    tempo = args.tempo
    spoken = lambda path: wav_seconds(path) / (tempo if "narration" in path.parts else 1.0)
    meta = json.loads((frames / "scenes.json").read_text())
    text = json.loads((ROOT / "tools/video" / os.environ.get("NARRATION", "narration.json")).read_text())["scenes"]
    fps = meta["fps"]
    work = frames / "_build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    clips, srt, audio_inputs, t = [], [], [], 0.0
    chapters = []
    for number, scene in enumerate(meta["scenes"]):
        name, recorded = scene["name"], scene["seconds"]
        if number == 0 and args.first:
            recorded = min(recorded, args.first)
        cuts = sorted((max(0.0, a), min(recorded, b)) for a, b in scene.get("cuts", []) if b - a > 0.2)
        removed = lambda t: sum(max(0.0, min(b, t) - a) for a, b in cuts)
        seconds = recorded - removed(recorded)
        for item in scene.get("speech", []):
            item["t"] = item["t"] - removed(item["t"])
        entry = text.get(name, "")
        parts = entry if isinstance(entry, dict) else {"post": entry}
        cursor, placed = 0.3, []  # (start, path, caption) within the scene; voices never overlap
        pre = frames / "narration" / f"{name}.pre.wav"
        if pre.exists():
            placed.append((cursor, pre, parts.get("pre", "")))
            cursor += spoken(pre) + 0.25
        for i, item in enumerate(scene.get("speech", [])):
            clip = frames / "speech" / f"{name}-{i}.wav"
            if clip.exists():
                start = max(cursor, item["t"] + 0.4)
                placed.append((start, clip, "Alexa: " + item["text"]))
                cursor = start + spoken(clip) + 0.3
        post = frames / "narration" / f"{name}.wav"
        if post.exists():
            placed.append((cursor, post, parts.get("post", "")))
            cursor += spoken(post)
        elif parts.get("post") and not placed:
            placed.append((0.3, None, parts["post"]))  # silent draft: captions only
            cursor = seconds
        length = max(seconds, cursor + 0.45)
        clip_path = work / f"{name}.mp4"
        hold = max(0.0, length - seconds)
        keep = "".join(f"+between(t,{a:.2f},{b:.2f})" for a, b in cuts)
        vf = (f"select='not({keep[1:]})',setpts=N/({fps}*TB)," if cuts else "") + "fps=30,format=yuv420p" + (
            f",tpad=stop_mode=clone:stop_duration={hold:.2f}" if hold else "")
        run([ff, "-y", "-loglevel", "error", "-framerate", fps, "-i", frames / name / "%05d.jpg",
             "-t", f"{length:.3f}", "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "30", clip_path])
        clips.append(clip_path)
        chapters.append((t, name))
        for start, audio, caption in placed:
            span = spoken(audio) if audio else (seconds - 1.0)
            if audio:
                audio_inputs.append((audio, t + start))
            lines = chunks(caption)
            for i, line in enumerate(lines):
                a = t + start + i * span / len(lines)
                srt.append((a, a + span / len(lines) - 0.05, line))
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
        # A fixed lower-third band keeps captions readable over any UI.
        style = "FontName=Helvetica,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=0.5,Shadow=0,MarginV=9"
        escaped = str(captions.resolve()).replace(":", r"\:").replace("'", r"\'")
        filters.append(f"[0:v]drawbox=x=0:y=ih-132:w=iw:h=132:color=black@0.82:t=fill,subtitles='{escaped}':force_style='{style}'[v]")
        video_label = "[v]"
    if audio_inputs:
        parts = []
        for i, (_, start) in enumerate(audio_inputs, 1):  # _ is the clip path
            delay = int(start * 1000)
            speed = f"atempo={tempo}," if tempo != 1.0 and "narration" in Path(str(_)).parts else ""
            filters.append(f"[{i}:a]{speed}aresample=48000,adelay={delay}|{delay}[a{i}]")
            parts.append(f"[a{i}]")
        filters.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad=whole_dur={t:.3f}[a]")
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", video_label if video_label != "0:v" else "0:v"]
    if audio_inputs:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", f"{t:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    run(cmd)
    titles = json.loads((ROOT / "tools/video" / os.environ.get("NARRATION", "narration.json")).read_text()).get("chapters", {})
    lines = [f"{int(a // 60)}:{int(a % 60):02d} {titles[n]}" for a, n in chapters if n in titles]
    out.with_suffix(".chapters.txt").write_text("\n".join(lines) + "\n")
    print(f"{out} ({t:.1f}s), captions {captions}")


if __name__ == "__main__":
    main()
