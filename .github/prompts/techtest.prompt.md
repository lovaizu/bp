---
description: Technical validation harness (throwaway, NOT part of BLACKPINK) — confirms BPTRACE marker output, explicit delegation, and explicit non-delegation are all reliably followed by the model.
tools: ['runInTerminal', 'agent/runSubagent']
agent: agent
---

Do the steps below in order, using the user's input as the input text throughout.

Before starting Step 1, output exactly this line (fill in the input text for <input>):
BPTRACE start theme="<input>" wf=techtest.md

## Step 1 — Echo directly (no delegation)
IN: the input text.
Do not use the runSubagent tool or any subagent (including techtest-echo) for this step.
Run this command yourself, directly:
`echo "step1: <input>"`
OUT: the command's stdout.
Then output exactly this line:
BPTRACE step=1 out actor=main

## Step 2 — Echo via subagent (delegation required)
IN: the input text.
Run the `techtest-echo` subagent (runSubagent) with the following prompt
(fill in the actual input for `<input>`):

---
input: <input>
---

Wait for the subagent to return a JSON object. Do not modify it.
OUT: the JSON object from the techtest-echo subagent (unchanged).
(The techtest-echo subagent emits its own BPTRACE step=2 out line right after its OUT JSON — do not emit it yourself.)
