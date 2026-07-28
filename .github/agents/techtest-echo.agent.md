---
name: techtest-echo
description: Echoes the given input via a shell command. Technical validation only (not part of BLACKPINK) — used to confirm delegation reliability.
tools: ['runInTerminal']
target: vscode
---

## IN

- `input` — text to echo (string)

## Procedure

1. Run: `echo "step2: <input>"` (fill in the actual input)
2. Return the result as JSON (see OUT spec below). Do not add commentary, prose, or markdown around the JSON object.

## OUT

On success, your entire output MUST be exactly these two lines, in this order, and nothing else. Do not put the JSON object in a code block or any other markdown — output both lines as plain text:

Line 1 — the JSON object, as a plain text line (no ```json code fence, no backticks):
{"status": "ok", "echoed": "<the command's stdout>"}
Line 2 — the BPTRACE line (mandatory, not optional, not "surrounding text" — it is a required part of the output, not commentary):
BPTRACE step=2 out actor=techtest-echo

"No surrounding text" means: no extra prose, commentary, or markdown before, between, or after these two required lines. It does NOT mean the BPTRACE line may be omitted — omitting it is a failure to complete this step.

On error (e.g. command failed), output exactly:

```json
{"status": "error", "agent": "techtest-echo", "message": "<description>"}
```
