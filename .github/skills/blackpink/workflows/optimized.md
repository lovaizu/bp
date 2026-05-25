# Workflow: Optimized Setlist

Full pipeline with flow evaluation. Search songs, evaluate the order,
then generate a polished show plan.

Flow: bp-song-finder → bp-setlist-evaluator → bp-show-planner

## Step 1: Song Search

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (the user's theme)
- songs.json path: .github/skills/blackpink/resources/songs.json
- filter-songs.sh path: .github/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 12

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Setlist Evaluation

Delegate to the bp-setlist-evaluator subagent with the following instructions.

Request:
- Song search result: (the full JSON result from Step 1)
- members.json path: .github/skills/blackpink/resources/members.json
- Target setlist length: 8-10 songs
- Evaluation criteria: energy flow, mood transitions, solo balance, dance break spacing

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: (the user's theme)
- Evaluated setlist: (the full JSON result from Step 2)
- members.json path: .github/skills/blackpink/resources/members.json
- Optimization level: full

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 4: Present to User

Format the show plan from Step 3 into a detailed setlist for the user.
Include: song order with position rationale, energy curve description,
estimated total duration, stage/lighting notes, and member highlights.
If the evaluator noted any warnings, mention them as suggestions.
