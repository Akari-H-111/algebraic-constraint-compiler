---
name: show-your-work
description: Check math homework and solve linear word problems with independently verified certificates through the Show Your Work MCP server. Use when someone asks to check a student's algebra steps, verify an answer, solve linear equations exactly, find whether a word problem is even possible, create a practice problem, or report a learner's progress.
---

# Show Your Work

AI interprets. Compiler calculates. Verifier certifies.

You translate the person's words into exact linear equations. The Show Your
Work MCP server calculates with exact rationals and an independent verifier
replays every certificate before anything is returned. You never calculate.

## Connect

Streamable HTTP (MCP 2025-11-25): `http://127.0.0.1:8000/mcp` after
`python -m algebraic_compiler.mcp_server`, or stdio:
`python -m algebraic_compiler.mcp_server --stdio`. Result tools link the MCP
Apps card `ui://show-your-work/certificate-card.html`.

## Workflow

1. **Translate.** Short unknown names plus labels: `{"a": "adult tickets"}`.
   Counts of people or things use `domain: "nonnegative_integer"`.
   Keep the person's wording in `question` so the card can show it back.
2. **Pick the tool.**
   - A student's lines of work → `check_work` (first line = the original problem).
   - A final answer only → `check_answer`.
   - "What is the answer?" → `solve_equations`.
   - "Give me one like that" → `practice_problem` (do not read the key aloud).
   - "How is she doing?" → `progress_report` (re-verifies every saved result).
3. **Remember.** Pass `notebook` (household) and `learner` (first name) so
   results persist across sessions.
4. **Speak.** Use `suggested_speech` in one or two short sentences. Give the
   hint first; reveal the verified answer only when asked.

## Rules

- Only state what a result with `certificate_verified=true` says.
- `MATHEMATICALLY_REJECTED` with a verified certificate is a finding (no
  solution, wrong answer, broken step), not a failure.
- If the status is `UNSUPPORTED` (for example `x^2`), `ASSUMPTION_REQUIRED` or
  an error, say nothing was verified. Never substitute your own number.
- Hints about the *kind* of slip are rule-based guesses and are labelled as
  not certified. The step that breaks is certified.
- Ask the person to check the typed equations: translation is the only AI step.
