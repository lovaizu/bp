Do the steps in order. Each step's OUT is the next step's IN.

## Step 1 — Find songs
IN: the user's theme.
1. For each mood tag that fits the theme, run:
   `bash .github/skills/blackpink/resources/filter-songs.sh .github/skills/blackpink/resources/songs.json mood <tag>`
2. Keep up to 8 songs from the combined results.
OUT: a JSON array of the kept songs, each with: id, title, bpm, energy, mood, duration_sec, members_featured, has_dance_break, suitable_for.

## Step 2 — Write the show plan
IN: Step 1 OUT; `.github/skills/blackpink/resources/members.json`.
1. Put the songs in performance order: opener, rising peaks, finale.
2. For each song, add: stage_layout, lighting, one choreography_highlight.
OUT: the final setlist for the user — ordered songs with the Step 2 details, plus total duration.
