Do the steps in order. Each step's OUT is the next step's IN.

## Step 1 — Find songs
IN: the user's theme.
1. Select mood tags that match the theme from the available tags:
   acoustic, anthemic, bright, carefree, chill, classical, confident, conflicted,
   cultural, dramatic, dreamy, elegant, emotional, empowering, energetic, fierce,
   fun, glamorous, graceful, heartfelt, hype, iconic, intense, party, playful,
   powerful, rebellious, reflective, sad, smooth, sweet, synthy, triumphant,
   uplifting, vulnerable, warm, yearning, youthful
   Use only filter-songs.sh to retrieve songs; do not use any other file access or search method.
   For each selected tag, run:
   `bash .claude/skills/blackpink/resources/filter-songs.sh .claude/skills/blackpink/resources/songs.json mood <tag>`
2. Keep up to 12 songs from the combined results.
OUT: a JSON array of the kept songs, each with: id, title, bpm, energy, mood, duration_sec, members_featured, has_dance_break, suitable_for.

## Step 2 — Order the setlist
IN: Step 1 OUT; `.claude/skills/blackpink/resources/members.json`.
1. Keep 8–10 of the songs.
2. Put them in performance order: opener, rising peaks, one cooldown, finale.
OUT: a JSON array of the ordered songs, each with: position, id, title, energy, bpm, note (one line). Plus total_duration_sec.

## Step 3 — Write the show plan
IN: Step 2 OUT; `.claude/skills/blackpink/resources/members.json`.
1. For each song, add: stage_layout, lighting, one choreography_highlight.
OUT: the final setlist for the user — ordered songs with the Step 3 details, plus total duration.
