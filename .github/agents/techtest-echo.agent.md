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

On success, output exactly this JSON object (no surrounding text), then the BPTRACE line:

```json
{"status": "ok", "echoed": "<the command's stdout>"}
```
BPTRACE step=2 out actor=techtest-echo

On error (e.g. command failed), output exactly:

```json
{"status": "error", "agent": "techtest-echo", "message": "<description>"}
```
