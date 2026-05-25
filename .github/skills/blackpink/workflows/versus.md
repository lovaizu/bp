# Workflow: Versus Setlist

Compare two themes by running song search twice, merging results,
evaluating them together, then producing a comparative show plan.

Flow: bp-song-finder(A) + bp-song-finder(B) → bp-setlist-evaluator → bp-show-planner

The user will provide two themes (e.g., "fierce vs emotional").
Parse the two themes from the user's input. If only one theme is found, ask the user
to provide a second theme before proceeding.

## Step 1A: Song Search for Theme A

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (first theme)
- songs.json path: .github/skills/blackpink/resources/songs.json
- filter-songs.sh path: .github/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 10

Keep the full JSON result as `result_a`.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 1B: Song Search for Theme B

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (second theme)
- songs.json path: .github/skills/blackpink/resources/songs.json
- filter-songs.sh path: .github/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 10

Keep the full JSON result as `result_b`.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Comparative Evaluation

Delegate to the bp-setlist-evaluator subagent with the following instructions.

Request:
- Song search results: provide BOTH result_a and result_b as follows:
  - theme_a: { name: "(first theme)", search_result: (result_a) }
  - theme_b: { name: "(second theme)", search_result: (result_b) }
- members.json path: .github/skills/blackpink/resources/members.json
- Target setlist length: 8-10 songs per theme
- Evaluation criteria: energy flow, mood transitions, solo balance, dance break spacing
- Mode: comparative (evaluate both and recommend which theme produces the better setlist)

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: provide both theme names and indicate which was recommended by the evaluator
- Evaluated setlist: (the full JSON result from Step 2, which contains both plans)
- members.json path: .github/skills/blackpink/resources/members.json
- Optimization level: full
- Mode: comparative (generate plans for both and highlight differences)

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 4: Present to User

Format both show plans side by side for comparison.
Include: which theme the evaluator recommended and why,
song order for each, energy curves, total durations, and key differences.
Let the user choose which plan to go with.
