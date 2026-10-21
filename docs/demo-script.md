# Demo video script, v3 (target 2:45–2:55, hard limit 3:00)

One household, one evening, one idea: Alexa+ only says "correct" when it can
prove it. All footage is the real running product: the simulated Alexa+ display
with a live Gemini agent, the public web page calling the hosted MCP server, and
a real Claude Code run against the same server. Narration and Alexa's replies
use Gemini TTS. The narration text is in
[`tools/video/narration.v3.json`](../tools/video/narration.v3.json).

| # | Scene | Picture | What it shows |
|---|---|---|---|
| 1 | Title | Title card | Show Your Work: an Alexa+ skill built as a self-hosted MCP server |
| 2 | Why | Pew 2026 and GSM-Symbolic statistics | The problem: fluent answers a parent can't check |
| 3 | Photo | Simulated Alexa+ display; the parent attaches a photo of Maya's page; the timeline shows the photo's SHA-256, Gemini choosing `check_work`, and the verifier 17/17; a red box lands on line 2 | The AI only reads; the MCP server finds the first line that changes the answer, with a counterexample |
| 4 | Unclear | Guided "A photo with a line I can't read" (the reading is labelled as written for the demo); an amber box on "3x = 1S" | A flagged line is held for review; everything else is still checked |
| 5 | Typo | Live agent: the ticket worksheet; "No valid answer: −12 child tickets" | A certified "no" with a two-line proof |
| 6 | Forgery | Replay verifier, then Try a forgery inside the MCP Apps card | A recomputed hash can't make a false proof true |
| 7 | Web | The public GitHub Pages demo connected to the hosted server; the timeline shows MCP `tools/call` and "your browser replayed the server's certificate" | The FAQ's "web page that acts as an actual MCP client", plus independent replay in the browser |
| 8 | Claude | Card with a real Claude Code run calling `check_work` on the public endpoint | The same standard MCP server in another assistant |
| 9 | Memory | Practice problem with a hidden verified key; weekly progress re-verified | It remembers, and re-checks what it remembers |
| 10 | Architecture | How-it-works card | MCP 2025-11-25 over Streamable HTTP, MCP Apps, exact arithmetic, an independent verifier |
| 11 | Close | Closing card with the try-it links | For parents, tutors and teachers |

On screen throughout the display scenes: *Simulated Alexa+ display · not an
Amazon product*. Recorded readings and Guided requests are labelled in the
"Behind the screen" timeline.

Recording: `tools/video/record.mjs` (headless Chrome through the DevTools
protocol) captures each scene as JPEG frames; `tools/video/build.py` assembles
clips, captions (SRT) and narration with FFmpeg; `tools/video/tts_gemini.py`
makes the narration (Zephyr) and Alexa's voice (Kore).
