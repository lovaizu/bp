Do the steps in order. Each step's OUT is the next step's IN.
The user gives two themes (e.g. "fierce vs emotional"). If only one theme is present, ask for the second before starting.

## Step 1A — Find songs for theme A
IN: the first theme.
1. Select mood tags that match theme A from the available tags:
   acoustic, anthemic, bright, carefree, chill, classical, confident, conflicted,
   cultural, dramatic, dreamy, elegant, emotional, empowering, energetic, fierce,
   fun, glamorous, graceful, heartfelt, hype, iconic, intense, party, playful,
   powerful, rebellious, reflective, sad, smooth, sweet, synthy, triumphant,
   uplifting, vulnerable, warm, yearning, youthful
   Use only filter-songs.sh to retrieve songs; do not use any other file access or search method.
   For each selected tag, run:
   `bash .claude/skills/blackpink/resources/filter-songs.sh .claude/skills/blackpink/resources/songs.json mood <tag>`
2. Keep up to 10 songs from the combined results.
OUT: result_a — a JSON array of the kept songs, each with: id, title, bpm, energy, mood, duration_sec, members_featured, has_dance_break, suitable_for.

## Step 1B — Find songs for theme B
IN: the second theme.
1. Select mood tags that match theme B from the available tags:
   acoustic, anthemic, bright, carefree, chill, classical, confident, conflicted,
   cultural, dramatic, dreamy, elegant, emotional, empowering, energetic, fierce,
   fun, glamorous, graceful, heartfelt, hype, iconic, intense, party, playful,
   powerful, rebellious, reflective, sad, smooth, sweet, synthy, triumphant,
   uplifting, vulnerable, warm, yearning, youthful
   Use only filter-songs.sh to retrieve songs; do not use any other file access or search method.
   For each selected tag, run:
   `bash .claude/skills/blackpink/resources/filter-songs.sh .claude/skills/blackpink/resources/songs.json mood <tag>`
2. Keep up to 10 songs from the combined results.
OUT: result_b — same JSON shape as result_a.

## Step 2 — Compare the two themes
IN: result_a; result_b; `.claude/skills/blackpink/resources/members.json`.
1. For each theme, keep 8–10 songs and put them in performance order: opener, rising peaks, one cooldown, finale.
2. Give each theme a flow score (energy flow, mood transitions, solo balance, dance break spacing).
3. The recommended theme is the one with the higher flow score.
OUT: a JSON object with theme_a and theme_b — each an ordered array (position, id, title, energy, bpm, note) plus flow_score and total_duration_sec — and recommended (the theme name with the higher score).

## Step 3 — Write the show plans
IN: Step 2 OUT; `.claude/skills/blackpink/resources/members.json`.
1. For each song in both themes, add: stage_layout, lighting, one choreography_highlight.
OUT: the final comparison for the user — both ordered setlists with the Step 3 details, each total duration, the recommended theme, and the key differences between them.
