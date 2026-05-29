# Workflow: Quick Setlist

Fast setlist generation. Search songs, then directly generate a show plan.
Skips the evaluation/optimization step.

Flow: bp-song-finder → bp-show-planner

## Critical: subagent context isolation

Each subagent runs in an ISOLATED context. It can see ONLY the text you place in its
request — not the user's chat, not prior steps, not any earlier subagent's output.
So whenever a step says to pass a prior result, you MUST paste that result's COMPLETE,
VERBATIM JSON into the request. Do not summarize, paraphrase, reference it indirectly,
or omit it: the subagent cannot recover it on its own and will re-derive (wrong) data.

## Step 1: Song Search

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (the user's theme)
- songs.json path: .github/skills/blackpink/resources/songs.json
- filter-songs.sh path: .github/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 8

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: (the user's theme)
- Song data: PASTE the complete verbatim JSON returned by Step 1 here (the full object, not a summary or a reference)
- members.json path: .github/skills/blackpink/resources/members.json
- Optimization level: basic

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Present to User

Format the show plan from Step 2 into a readable setlist for the user.
Include: song order, estimated duration, and stage notes.
