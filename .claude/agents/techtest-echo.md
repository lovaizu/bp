---
name: techtest-echo
description: Echoes the given input with a shell command, to confirm that delegation works. It's for technical validation only, and isn't part of BLACKPINK.
tools: Bash
---

## Input

- `input`: the text to echo, a string.

## Procedure

1. Run `echo "step2: <input>"`, with the actual input in place of `<input>`.
2. Return the result as JSON, as the output section says. Add no commentary, prose, or Markdown around the JSON object.

## Output

On success, output exactly this JSON object, with no text around it, and then the BPTRACE line:

```json
{"status": "ok", "echoed": "<the command's stdout>"}
```
BPTRACE step=2 out actor=techtest-echo

On an error, for example when the command fails, output exactly:

```json
{"status": "error", "agent": "techtest-echo", "message": "<description>"}
```
