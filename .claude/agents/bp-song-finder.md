---
name: bp-song-finder
description: Run filter-songs.sh for each matching mood tag, collect and deduplicate results, return a JSON object of candidate songs
tools: Bash, Read
---

## IN

- `theme` — the user's theme text (string)
- `songs_json` — absolute or repo-relative path to songs.json
- `filter_sh` — absolute or repo-relative path to filter-songs.sh
- `max_songs` — maximum number of songs to keep (integer)

## Procedure

1. Map the theme to mood tags. The full set of available tags is:
   `acoustic, anthemic, bright, carefree, chill, classical, confident, conflicted,
   cultural, dramatic, dreamy, elegant, emotional, empowering, energetic, fierce,
   fun, glamorous, graceful, heartfelt, hype, iconic, intense, party, playful,
   powerful, rebellious, reflective, sad, smooth, sweet, synthy, triumphant,
   uplifting, vulnerable, warm, yearning, youthful`
   Select every tag whose meaning overlaps the theme. Keep the list minimal but
   do not omit clearly relevant tags.

2. For each selected tag, run:
   ```
   bash <filter_sh> <songs_json> mood <tag>
   ```
   If the command exits with a non-zero status or its stdout is not a JSON array,
   stop immediately and return the error object (see ON ERROR below).
   Otherwise collect the stdout as a JSON array of song objects.

3. Merge all collected arrays into one list. Remove duplicate entries by `id`
   (keep the first occurrence). Keep up to `max_songs` entries from the merged
   list (truncate if needed).

4. Return the result as a JSON object (see OUT spec below). Do not add
   commentary, prose, or markdown around the JSON object.

## OUT

On success, output exactly this JSON object (no surrounding text):

```json
{
  "status": "ok",
  "theme_input": "<the theme text passed in>",
  "song_count": <integer>,
  "songs": [
    {
      "id": "<string>",
      "title": "<string>",
      "bpm": <number>,
      "energy": <number>,
      "mood": ["<tag>", ...],
      "duration_sec": <number>,
      "members_featured": ["<name>", ...],
      "has_dance_break": <bool>,
      "suitable_for": ["<string>", ...]
    }
  ]
}
```

On error (e.g. script not found, JSON parse failure), output exactly:

```json
{"status": "error", "agent": "bp-song-finder", "message": "<description>"}
```
