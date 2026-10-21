#!/bin/sh
# Finish the v3 demo video from a recorded take: TTS (narration Zephyr with gemini-3.1-flash-tts-preview, Alexa's
# replies Kore with gemini-2.5-flash-preview-tts), build with captions burned in, check length and loudness, make a
# contact sheet and an upload copy under 50 MB. Clips that already exist are kept, so it can be re-run after quota resets.
#   tools/video/finish_v3.sh [frames_dir]       (default .local-qa/video-v3/frames)
# NARRATION_MODEL / NARRATION_FALLBACK_MODEL override the TTS models; TTS_LIMIT (default 10) caps calls to the first model.
set -e
cd "$(dirname "$0")/../.."
FRAMES=${1:-.local-qa/video-v3/frames}
OUTDIR=.local-qa/video-v3
OUT=$OUTDIR/show-your-work-v3.mp4
FF=.local-qa/media-tools/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1
export NARRATION=narration.v3.json
# Narration: 3.1 Flash TTS first (10 calls/day on the free tier), then whatever is left on 2.5 Flash TTS (Zephyr).
env -u ANTHROPIC_BASE_URL python3 tools/video/tts_gemini.py "$FRAMES" --what narration --narrator Zephyr \
  --model "${NARRATION_MODEL:-gemini-3.1-flash-tts-preview}" --limit "${TTS_LIMIT:-10}" || echo "narration: first model stopped (limit or quota); continuing with the fallback model"
env -u ANTHROPIC_BASE_URL python3 tools/video/tts_gemini.py "$FRAMES" --what narration --narrator Zephyr \
  --model "${NARRATION_FALLBACK_MODEL:-gemini-2.5-flash-preview-tts}"
# Alexa's replies (Kore) always use 2.5 Flash TTS.
env -u ANTHROPIC_BASE_URL python3 tools/video/tts_gemini.py "$FRAMES" --what assistant --assistant Kore \
  --model gemini-2.5-flash-preview-tts
python3 tools/video/build.py "$FRAMES" "$OUT" --burn --tempo "${TEMPO:-1.15}" --reply-tempo "${REPLY_TEMPO:-1.1}" --first 2.5 --trim-tail
"$FF" -hide_banner -i "$OUT" 2>&1 | grep Duration
"$FF" -hide_banner -i "$OUT" -af ebur128 -f null - 2>&1 | grep -A1 "Integrated loudness" | tail -1
cat "${OUT%.mp4}.chapters.txt"
# Contact sheet: one frame per scene (the middle of each scene in the final cut).
python3 - "$FF" "$OUT" "$OUTDIR/contact.jpg" <<'PY'
import json, subprocess, sys
ff, out, sheet = sys.argv[1:4]
timing = json.load(open(out[:-4] + ".timing.json"))["scenes"]
t, marks = 0.0, []
for row in timing:
    marks.append((row["name"], t + row["final"] * 0.6)); t += row["final"]
cmd = [ff, "-y", "-loglevel", "error"]
for _, at in marks:
    cmd += ["-ss", f"{at:.2f}", "-i", out]
cols, rows = 3, (len(marks) + 2) // 3
filt = "".join(f"[{i}:v]scale=640:-1,drawtext=text='{n}':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6[f{i}];" for i, (n, _) in enumerate(marks))
filt += "".join(f"[f{i}]" for i in range(len(marks))) + f"xstack=inputs={len(marks)}:layout=" + "|".join(f"{(i % cols) * 640}_{(i // cols) * 360}" for i in range(len(marks)))
cmd += ["-filter_complex", filt, "-frames:v", "1", "-q:v", "3", sheet]
subprocess.run(cmd, check=True)
PY
# Upload copy under 50 MB: two-pass-free CRF encode, re-tried at a higher CRF if too big.
UP=$OUTDIR/show-your-work-v3-upload.mp4
for CRF in 24 27 30 33; do
  "$FF" -y -loglevel error -i "$OUT" -c:v libx264 -preset slow -crf $CRF -vf scale=1920:1080 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart "$UP"
  SIZE=$(wc -c < "$UP"); [ "$SIZE" -lt 49000000 ] && break
done
echo "upload copy: $UP $SIZE bytes (crf $CRF)"
