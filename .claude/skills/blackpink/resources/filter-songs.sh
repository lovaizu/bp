#!/bin/bash
# filter-songs.sh - Filter songs from songs.json by criteria
# Usage: filter-songs.sh <songs.json path> <filter_type> <filter_value>
#
# Filter types:
#   mood <tag>         - Songs matching a mood tag (e.g., "fierce")
#   energy <min> <max> - Songs within energy range (e.g., 70 100)
#   member <id>        - Solo songs for a specific member (e.g., "lisa")
#   suitable <role>    - Songs suitable for a setlist role (e.g., "opener")
#   bpm <min> <max>    - Songs within BPM range
#
# Output: JSON array of matching song objects

SONGS_FILE="$1"
FILTER_TYPE="$2"

if [ ! -f "$SONGS_FILE" ]; then
  echo '{"error": "songs.json not found at: '"$SONGS_FILE"'"}'
  exit 1
fi

case "$FILTER_TYPE" in
  mood)
    TAG="$3"
    jq --arg tag "$TAG" '[.songs[] | select(.mood | index($tag))]' "$SONGS_FILE"
    ;;
  energy)
    MIN="$3"
    MAX="$4"
    jq --argjson min "$MIN" --argjson max "$MAX" \
      '[.songs[] | select(.energy >= $min and .energy <= $max)]' "$SONGS_FILE"
    ;;
  member)
    MEMBER="$3"
    jq --arg m "$MEMBER" \
      '[.songs[] | select((.members_featured | length) == 1 and (.members_featured | index($m)))]' \
      "$SONGS_FILE"
    ;;
  suitable)
    ROLE="$3"
    jq --arg role "$ROLE" '[.songs[] | select(.suitable_for | index($role))]' "$SONGS_FILE"
    ;;
  bpm)
    MIN="$3"
    MAX="$4"
    jq --argjson min "$MIN" --argjson max "$MAX" \
      '[.songs[] | select(.bpm >= $min and .bpm <= $max)]' "$SONGS_FILE"
    ;;
  *)
    echo '{"error": "Unknown filter type: '"$FILTER_TYPE"'. Use: mood, energy, member, suitable, bpm"}'
    exit 1
    ;;
esac
