#!/usr/bin/env python3
"""verify-run.py — Layer-2 invariant checker for the bp setlist planner.

Reads a Claude Code (CC) transcript .jsonl and judges PASS/FAIL on the
layer-2 (plumbing) invariants defined in docs/steering.md. Layer-3 content
(which songs were picked, the prose quality) is intentionally NOT judged.

Stage covered: A (no subagents — the parent executes every WF step itself).

What it checks per run:
  1. Routing      — the theme routed to the expected workflow file
                    (SKILL.md rules: quick / versus / else→optimized).
  2. Step order   — optimized.md was Read, then filter-songs.sh ran (Step 1),
                    then Step 2 OUT, then Step 3 OUT — in that order.
  3. filter-songs — the script actually executed (transcript Bash + the
                    /tmp/bp-filter.log instrument, time-windowed to the run).
  4. OUT shapes   — Step 1 / Step 2 / Step 3 OUT field vocab is present
                    (searched across assistant text AND tool_result stdout,
                    because the parent emits Step1/2 JSON via python stdout).
  5. No delegation— Stage A expects zero Task (subagent) calls.

Usage:
  scripts/verify-run.py <transcript.jsonl> [<transcript.jsonl> ...]
  scripts/verify-run.py --theme white run1.jsonl run2.jsonl
  scripts/verify-run.py --latest 2          # auto-pick newest /blackpink runs
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

PROJECT_TRANSCRIPT_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-kiyo-work-lovaizu-bp"
)
FILTER_LOG = "/tmp/bp-filter.log"

# OUT-shape field vocab per workflow step. We require a distinctive subset of
# each step's fields (not every field) to call the shape "present". The marker
# set is keyed; a step in a profile names its marker key + how many must appear.
MARKERS = {
    "S1": ["has_dance_break", "duration_sec", "members_featured", "suitable_for"],
    "S2_optimized": ["position", "total_duration_sec"],   # ordered-list OUT
    "S2_versus": ["flow_score", "recommended"],            # comparative OUT
    "S3": ["stage", "lighting", "choreo"],                 # stage_layout/lighting/choreography_highlight
}

# Per-workflow invariant profile for Stage A (no subagents). Each entry is a
# step: (label, marker_key, min_present). Steps are listed in required order;
# the order check enforces their OUT positions are non-decreasing.
PROFILES = {
    "optimized.md": [("S1", "S1", 3), ("S2", "S2_optimized", 2), ("S3", "S3", 3)],
    "quick.md":     [("S1", "S1", 3), ("S3", "S3", 3)],
    "versus.md":    [("S1", "S1", 3), ("S2", "S2_versus", 2), ("S3", "S3", 3)],
}


def route_for_theme(theme):
    """Mirror SKILL.md routing rules. Returns the expected workflow filename."""
    t = (theme or "").lower()
    if any(k in t for k in ["quick", "simple", "just give me"]):
        return "quick.md"
    if any(k in t for k in [" vs ", "versus", "compare"]) or " vs" in t:
        return "versus.md"
    return "optimized.md"


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def load_events(path):
    """Yield (idx, ts, role, block) for every content block, in file order.

    role is 'assistant' or 'user' (tool_results live in user-type messages).
    block is the raw content-block dict.
    """
    idx = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        role = o.get("type")
        ts = parse_ts(o.get("timestamp"))
        m = o.get("message", {})
        cont = m.get("content")
        if isinstance(cont, str):
            yield idx, ts, role, {"type": "text", "text": cont}
            idx += 1
        elif isinstance(cont, list):
            for b in cont:
                if isinstance(b, dict):
                    yield idx, ts, role, b
                    idx += 1


def block_text(b):
    """Extract searchable text from a content block (text or tool_result)."""
    if b.get("type") == "text":
        return b.get("text", "") or ""
    if b.get("type") == "tool_result":
        c = b.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(
                x.get("text", "") for x in c
                if isinstance(x, dict) and x.get("type") == "text"
            )
    return ""


def analyze(path):
    cmd_args = None          # /blackpink <args>
    read_wf = None           # first workflow file Read
    read_wf_idx = None
    first_filter_idx = None  # first Bash containing filter-songs.sh
    filter_cmd_count = 0     # literal occurrences in Bash commands
    task_calls = 0
    chunks = []  # (idx, lowered_text) in true file order — used for ordering scan
    run_ts = []

    for idx, ts, role, b in load_events(path):
        if ts:
            run_ts.append(ts)
        bt = b.get("type")

        if bt == "text":
            txt = b.get("text", "") or ""
            # /blackpink command args — capture ONLY from the block that holds the
            # blackpink command-name (a preceding /clear also emits empty args).
            if cmd_args is None and "<command-name>/blackpink</command-name>" in txt:
                mcmd = re.search(r"<command-args>([^<]*)</command-args>", txt)
                if mcmd:
                    cmd_args = mcmd.group(1).strip()
            if role == "assistant":
                chunks.append((idx, txt.lower()))

        if bt == "tool_result":
            chunks.append((idx, block_text(b).lower()))

        if bt == "tool_use":
            name = b.get("name")
            inp = b.get("input", {}) or {}
            if name == "Read":
                fp = inp.get("file_path", "") or ""
                if "/workflows/" in fp and read_wf is None:
                    read_wf = os.path.basename(fp)
                    read_wf_idx = idx
            elif name == "Bash":
                cmd = inp.get("command", "") or ""
                n = cmd.count("filter-songs.sh")
                if n:
                    filter_cmd_count += n
                    if first_filter_idx is None:
                        first_filter_idx = idx
            elif name == "Task":
                task_calls += 1

    # Build the searchable blob in true chronological (file) order so that a
    # token's character position reflects WHEN it was produced.
    chunks.sort(key=lambda c: c[0])
    searchable = "\n".join(t for _, t in chunks)

    return {
        "path": path,
        "cmd_args": cmd_args,
        "read_wf": read_wf,
        "read_wf_idx": read_wf_idx,
        "first_filter_idx": first_filter_idx,
        "filter_cmd_count": filter_cmd_count,
        "task_calls": task_calls,
        "searchable": searchable,
        "ts_window": (min(run_ts), max(run_ts)) if run_ts else (None, None),
    }


def count_filter_log(window):
    """Count /tmp/bp-filter.log lines inside the run's time window."""
    if not os.path.exists(FILTER_LOG):
        return None, None
    lo, hi = window
    total = 0
    inwin = 0
    for line in open(FILTER_LOG, encoding="utf-8"):
        m = re.match(r"\[([^\]]+)\]", line)
        if not m:
            continue
        total += 1
        ts = parse_ts(m.group(1))
        if ts and lo and hi and lo <= ts <= hi:
            inwin += 1
    return inwin, total


def step_presence(blob, marker_key, min_present):
    """(present_bool, first_char_pos_or_None) for a step's markers in blob."""
    toks = MARKERS[marker_key]
    hits = [blob.find(t.lower()) for t in toks]
    present_count = sum(1 for p in hits if p >= 0)
    positions = [p for p in hits if p >= 0]
    pos = min(positions) if positions else None
    return present_count >= min_present, pos


def judge(a, theme_override=None):
    theme = theme_override if theme_override is not None else a["cmd_args"]
    expected_wf = route_for_theme(theme)
    profile = PROFILES.get(expected_wf, PROFILES["optimized.md"])
    blob = a["searchable"]
    checks = []

    # 1. Routing
    routing_ok = a["read_wf"] == expected_wf
    checks.append(("1. Routing",
                   routing_ok,
                   f"theme={theme!r} → expected {expected_wf}, read {a['read_wf']}"))

    # Resolve each profile step's presence + OUT position once (used by 2 & 4).
    step_results = []  # (label, present, pos, min_present, marker_key)
    for label, mkey, minp in profile:
        present, pos = step_presence(blob, mkey, minp)
        step_results.append((label, present, pos, minp, mkey))

    # 2. Step order: WF read < first filter, then step OUT positions non-decreasing.
    order_parts = []
    order_ok = True
    if a["read_wf_idx"] is None or a["first_filter_idx"] is None:
        order_ok = False
        order_parts.append("missing WF-read or filter-run")
    else:
        if not (a["read_wf_idx"] < a["first_filter_idx"]):
            order_ok = False
        order_parts.append(f"read@{a['read_wf_idx']} < filter@{a['first_filter_idx']}")
    positions = [pos for _, _, pos, _, _ in step_results]
    if any(p is None for p in positions):
        order_ok = False
        order_parts.append("a step OUT not found")
    else:
        if not all(positions[i] <= positions[i + 1] for i in range(len(positions) - 1)):
            order_ok = False
        order_parts.append(" ≤ ".join(f"{lab}@{pos}"
                                      for lab, _, pos, _, _ in step_results))
    labels = "→".join(lab for lab, *_ in step_results)
    checks.append((f"2. Step order {labels}", order_ok, "; ".join(order_parts)))

    # 3. filter-songs.sh executed
    inwin, total = count_filter_log(a["ts_window"])
    filter_ok = a["filter_cmd_count"] >= 1 and (inwin is None or inwin >= 1)
    log_note = (f"log {inwin} in-window / {total} total" if inwin is not None
                else "log absent")
    checks.append(("3. filter-songs.sh ran",
                   filter_ok,
                   f"bash refs={a['filter_cmd_count']}; {log_note}"))

    # 4. OUT shapes (per workflow profile)
    out_ok = all(present for _, present, _, _, _ in step_results)
    detail = " ".join(f"{lab}({mkey})={present}"
                      for lab, present, _, _, mkey in step_results)
    checks.append((f"4. OUT shapes ({labels})", out_ok, detail))

    # 5. No subagent delegation (Stage A)
    deleg_ok = a["task_calls"] == 0
    checks.append(("5. No delegation (Stage A)",
                   deleg_ok,
                   f"Task calls={a['task_calls']}"))

    return checks


def pick_latest(n):
    files = sorted(glob.glob(os.path.join(PROJECT_TRANSCRIPT_DIR, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    picked = []
    for f in files:
        try:
            head = open(f, encoding="utf-8").read(20000)
        except Exception:
            continue
        if "<command-name>/blackpink</command-name>" in head:
            picked.append(f)
        if len(picked) >= n:
            break
    return picked


def main():
    ap = argparse.ArgumentParser(description="Layer-2 invariant checker (Stage A, CC).")
    ap.add_argument("transcripts", nargs="*", help="transcript .jsonl paths")
    ap.add_argument("--theme", default=None,
                    help="override theme for routing check (default: from /blackpink args)")
    ap.add_argument("--latest", type=int, default=0,
                    help="auto-pick the N newest /blackpink transcripts for the project")
    args = ap.parse_args()

    paths = list(args.transcripts)
    if args.latest:
        paths = pick_latest(args.latest) + paths
    if not paths:
        ap.error("no transcripts given (pass paths or --latest N)")

    all_pass = True
    for p in paths:
        if not os.path.exists(p):
            print(f"!! not found: {p}")
            all_pass = False
            continue
        a = analyze(p)
        checks = judge(a, theme_override=args.theme)
        run_pass = all(ok for _, ok, _ in checks)
        all_pass = all_pass and run_pass
        print("=" * 72)
        print(f"RUN: {os.path.basename(p)}   theme={a['cmd_args']!r}   "
              f"=> {'PASS' if run_pass else 'FAIL'}")
        print("-" * 72)
        for name, ok, note in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:<26} {note}")
    print("=" * 72)
    print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}  ({len(paths)} run(s))")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
