# Workflow: Quick Setlist

Fast setlist generation. Search songs, then directly generate a show plan.
Skips the evaluation/optimization step.

Flow: bp-song-finder → bp-show-planner

## Step 1: Song Search

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (the user's theme)
- songs.json path: .claude/skills/blackpink/resources/songs.json
- filter-songs.sh path: .claude/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 8

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: (the user's theme)
- Song data: (the full JSON result from Step 1)
- members.json path: .claude/skills/blackpink/resources/members.json
- Optimization level: basic

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Present to User

Format the show plan from Step 2 into a readable setlist for the user.
Include: song order, estimated duration, and stage notes.
