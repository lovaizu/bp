Do the steps in order. Each step's OUT is the next step's IN.

## Step 1 — Find songs
IN: the user's theme.
Use the agent tool to invoke the `bp-song-finder` subagent with the following prompt
(fill in the user's actual theme for `<theme>`):

---
theme: <theme>
songs_json: .claude/skills/blackpink/resources/songs.json
filter_sh: .claude/skills/blackpink/resources/filter-songs.sh
max_songs: 12
---

Wait for the subagent to return a JSON object.
If the result contains `"status": "error"`, show the error message to the user and stop.
Do not modify the result. Pass the full JSON object unchanged to Step 2.
OUT: the JSON object from the bp-song-finder subagent (unchanged).

## Step 2 — Order the setlist
IN: Step 1 OUT; `.claude/skills/blackpink/resources/members.json`.
1. Keep 8–10 of the songs.
2. Put them in performance order: opener, rising peaks, one cooldown, finale.
OUT: a JSON array of the ordered songs, each with: position, id, title, energy, bpm, note (one line). Plus total_duration_sec.

## Step 3 — Write the show plan
IN: Step 2 OUT; `.claude/skills/blackpink/resources/members.json`.
1. For each song, add: stage_layout, lighting, one choreography_highlight.
OUT: the final setlist for the user — ordered songs with the Step 3 details, plus total duration.
