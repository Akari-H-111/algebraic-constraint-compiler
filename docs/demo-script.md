# Demo video script (target 2:45, hard limit 3:00)

One household, one evening, one idea: Alexa+ only says "correct" when it can
prove it. All footage is the real running product. Voice turns are typed into
the simulator for the recording, and the spoken replies are the product's own
text. If no model key is available, the footage uses Guided mode and says so
on screen.

| # | Time | Picture | Narration (≈ 390 words total) |
|---|---|---|---|
| 1 | 0:00–0:17 | Title card, then a statistic card (Pew 2026; GSM-Symbolic) | "Half of U.S. teens now use AI chatbots for schoolwork, and about four in ten use them for math. But language models are fluent, not exact, and a parent can't tell a confident wrong answer from a right one. So we asked: what if Alexa had to prove it?" |
| 2 | 0:17–0:55 | Simulated Alexa+ display. The parent asks to check Maya's work; the timeline shows AI interprets → MCP `check_work` → verifier 14/14; the card appears | "This is Show Your Work, running on a simulated Alexa+ display. Maya solved 3x + 5 = 20 and got 25 over 3. Alexa doesn't guess. It sends her lines of work to our MCP server, which finds the exact step that changes the answer: x = 5 satisfies line one but not line two. An independent verifier replays that proof before Alexa says a word. Maya gets a hint, not the answer." |
| 3 | 0:55–1:18 | "What I understood" box highlighted, then the proof box | "The only AI step is turning words into equations, and the screen shows that translation back so a parent can check it. Everything after is exact rational arithmetic, with no floating point and no model in the loop." |
| 4 | 1:18–1:40 | The ticket worksheet: "No valid answer: −12 child tickets" | "Some homework can't be right as written. Twenty tickets for three hundred dollars forces minus twelve child tickets, so the worksheet has a typo. That's a certified 'no', with a two-line proof." |
| 5 | 1:40–2:02 | Tap Replay verifier, then Try a forgery; the red line reads "Forgery rejected" | "Every card is an MCP App. Replay re-checks the proof live. Try a forgery changes one number and recomputes the hash, and the verifier still rejects it. A valid fingerprint can't make a false proof true." |
| 6 | 2:02–2:25 | Practice problem with a hidden key, then "How is Maya doing this week?" showing 5/5 re-verified | "Alexa can make a practice problem whose answer key is verified before anyone sees it, and it remembers across sessions. The weekly report re-verifies every saved certificate before counting it, and names Maya's most common slip." |
| 7 | 2:25–2:45 | Architecture card (MCP 2025-11-25 Streamable HTTP, MCP Apps, verifier, notebook, research tier), then the closing title | "Under the hood: a self-hosted MCP server over Streamable HTTP, an interactive MCP Apps card that works in any compliant host, and a verifier that never imports the compiler. The same engine certifies research-level algebra too. AI interprets. Compiler calculates. Verifier certifies." |

On-screen lower third during scenes 2–6: *Simulated Alexa+ experience ·
real MCP calls · math never touches the language model*.

Recording: `node tools/video/record.mjs` (headless Chrome through the DevTools
protocol) captures each scene as JPEG frames. `tools/video/build.py` assembles
the clips, captions (SRT) and narration with FFmpeg. Narration audio is added
after the user chooses a voice.
