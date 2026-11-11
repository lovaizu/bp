---
description: A throwaway harness for technical validation, not part of BLACKPINK. It confirms that the model reliably outputs the BPTRACE markers, delegates a step when told to, and does a step itself when told to.
command: /techtest
allowed-tools: Bash, Agent
---

Do the steps in order for input: $ARGUMENTS

Before you start Step 1, output the line `BPTRACE start theme="<input>" wf=techtest.md`, with the input text in place of `<input>`. Output it as plain text on a line of its own, without the backticks.

## Step 1: echo directly, with no delegation
IN: the input text.
Don't use the Agent tool or any subagent, techtest-echo included, for this step.
Run this command yourself, directly:
`echo "step1: <input>"`
OUT: the command's stdout.
Then output the line `BPTRACE step=1 out actor=main`, as plain text on a line of its own, without the backticks.

## Step 2: echo through a subagent, with delegation required
IN: the input text.
Use the Agent tool to invoke the `techtest-echo` subagent with the following prompt, with the actual input in place of `<input>`:

```text
input: <input>
```

Wait for the subagent to return a JSON object. Don't change it.
OUT: the JSON object from the techtest-echo subagent, unchanged.
The techtest-echo subagent outputs its own `BPTRACE step=2 out` line right after its JSON object, so don't output that line yourself.
