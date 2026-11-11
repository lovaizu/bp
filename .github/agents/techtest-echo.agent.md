---
name: techtest-echo
description: Echoes the given input with a shell command, to confirm that delegation works. It's for technical validation only, and isn't part of BLACKPINK.
tools: ['runInTerminal']
target: vscode
---

## Input

- `input`: the text to echo, a string.

## Procedure

1. Run `echo "step2: <input>"`, with the actual input in place of `<input>`.
2. Return the result as JSON, as the output section says. Add no commentary, prose, or Markdown around the JSON object.

## Output

On success, your entire output MUST be exactly these two lines, in this order, and nothing else. Output both as plain text, without backticks, and don't put the JSON object in a code block or any other Markdown.

1. The BPTRACE line, which you must output first, before the JSON: `BPTRACE step=2 out actor=techtest-echo`
2. The JSON object, as one line of plain text: `{"status": "ok", "echoed": "<the command's stdout>"}`

"Nothing else" means no prose, commentary, or Markdown before, between, or after these two lines. It doesn't mean that you may leave out the BPTRACE line: without it, this step fails.

On an error, for example when the command fails, output exactly:

```json
{"status": "error", "agent": "techtest-echo", "message": "<description>"}
```
