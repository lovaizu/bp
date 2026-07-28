#!/usr/bin/env python3
r"""Extract BPTRACE markers and tool calls from an agent transcript and check them.

Platform-agnostic by design: the parser layer turns a transcript into an ordered
Event stream whose kinds (delegate / bash / tool / marker_*) carry the meaning,
and the expectation layer judges that stream without knowing which platform
produced it or what its tools are called. A second platform is one entry in
PLATFORMS: a parser, a project-directory resolver and a file listing.

Only text the model itself produced is treated as marker evidence: assistant
`type=text` content blocks, whole-line matches, outside fenced code blocks,
indented code blocks and HTML comments. A well-formed marker found in one of
those quoted regions becomes a `marker_quoted` event -- not evidence, but not
lost either. A line that tried to be a marker and missed the format becomes a
`marker_malformed` event.

Usage:
  check_transcript.py RUN.jsonl [RUN.jsonl ...] [checks...]
  check_transcript.py --latest N [discovery options] [checks...]

Judging named runs and finding candidate runs are two separate steps:

  1. See what is there, and why anything was passed over:
       check_transcript.py --latest 5 --since 2026-07-27T09:00:00 --dry-run
  2. Judge the runs you decided are the measurement, by path:
       check_transcript.py a.jsonl b.jsonl c.jsonl --expect 'start:theme=neon night'

  Every transcript named on the command line is judged. One that cannot be read
  is a failure, never a smaller sample. --dry-run is step 1 only: it refuses to
  run at all if expectations were given, so "3 of 3 passed" can never come from
  a command that judged nothing.

  A sample of N is N distinct runs. Naming the same file twice -- under any
  spelling of its path -- is a usage error, and two transcripts that record the
  same session are one run, not two.

Discovery options (--latest only):
  --since ISO8601    window: only transcripts modified at or after this moment
  --theme TEXT       keep only runs whose `BPTRACE start` theme equals TEXT
  --project-dir DIR  where to look (default: derived from --repo)
  --repo PATH        repository the transcripts belong to (default: .)
  --dry-run          print the selection and stop; refused with any expectation

  Every candidate inside the --since window that does not end up in the sample
  is listed with the reason it was left out. When expectations were given, an
  exclusion, an unreadable candidate or a shortfall is a FAIL on its own.
  --theme has to parse a run before it can see its theme, so a run that emitted
  no start marker is excluded *and reported* -- it never disappears in favour of
  an older run that happens to match.

Checks:
  --expect SPEC             ordered: SPEC must match at least once, in this order
  --expect-count SPEC=N     SPEC must match exactly N times
  --expect-delegations N    shorthand for --expect-count delegate=N
  --allow-anomalies         tolerate damaged evidence inside a run; it never
                            excuses a gap in the sample itself

SPEC syntax: <selector>[:<key>=<value>[,<key>=<value>]...]
  start                     the "BPTRACE start" line      keys: theme, wf
  step=<n>                  a "BPTRACE step=<n> out" line keys: actor
  delegate                  a subagent invocation         keys: to
  bash                      a shell invocation            keys: contains (substring)

  Every selector also accepts `origin`, matched exactly: "main" for the parent
  agent, "subagent:<agent type>" for a delegated one.

  Values are compared exactly, except `contains`, which is a substring test and
  may not be empty. A comma ends a value: write \, for a literal comma and \\
  for a literal backslash. --expect-count splits at the LAST '=' in the
  argument, so 'bash:contains=a=b=2' means "exactly 2 bash calls whose command
  contains 'a=b'". A count must be a plain non-negative decimal number.

Example -- the techtest stage-B invariant, kept here as an example because it is
BLACKPINK specific and the code is not:

  check_transcript.py run.jsonl \
      --expect 'start:theme=neon night,wf=techtest.md' \
      --expect 'step=1:actor=main,origin=main' \
      --expect 'delegate:to=techtest-echo' \
      --expect 'step=2:actor=techtest-echo,origin=subagent:techtest-echo' \
      --expect-count 'start=1' \
      --expect-count 'delegate=1'

Report-only (no expectation at all) prints the extracted facts and exits 0, and
its JSON says `"passed": null` -- never `true`, which would read as a verdict
nobody asked for.

Exit codes: 0 = PASS (or report-only), 1 = FAIL, 2 = usage/IO/internal error.

Coverage of this script, including the CLI tests that run it as a subprocess:

  COVERAGE_PROCESS_START=$PWD/.coveragerc python -m coverage run -m pytest \
      scripts/test_check_transcript.py
  python -m coverage combine . scripts && python -m coverage report -m
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

# Event kinds. The parser normalises to these; the judging layer sees only them.
MARKER_START = "marker_start"
MARKER_STEP = "marker_step"
MARKER_MALFORMED = "marker_malformed"
MARKER_QUOTED = "marker_quoted"
DELEGATE = "delegate"
BASH = "bash"
TOOL = "tool"
TOOL_KINDS = (DELEGATE, BASH, TOOL)

MAIN = "main"
UNKNOWN_AGENT = "<unknown>"  # cannot collide with a real agent type

# How many entries a category list prints before it asks for --verbose.
LIST_LIMIT = 10
# How much of one event's description is printed before it is cut short.
DESCRIBE_LIMIT = 160


class TranscriptError(Exception):
    """The transcript could not be read."""


class UsageError(Exception):
    """The command line asked for something that does not exist."""


# --------------------------------------------------------------------------- #
# BPTRACE marker grammar (shared by every platform parser)
# --------------------------------------------------------------------------- #
# Agent types and workflow file names are ASCII only, so stray punctuation (a
# trailing period, a bold marker's '**') fails the match instead of being
# swallowed into the captured value.
_ACTOR = r"[A-Za-z0-9_-]+"
_WF = r"[A-Za-z0-9_./-]+"
MARKER_START_RE = re.compile(
    rf'^BPTRACE\s+start\s+theme="(?P<theme>[^"]*)"\s+wf=(?P<wf>{_WF})$')
MARKER_STEP_RE = re.compile(
    rf"^BPTRACE\s+step=(?P<step>[0-9]+)\s+out\s+actor=(?P<actor>{_ACTOR})$")
# A near miss is a line that tried to write a marker, not one that mentions the
# token. "BPTRACEマーカーは..." and "`BPTRACE` について" are prose, not failures.
_NEAR_MISS_RE = re.compile(r"BPTRACE\s+(?:start|step)\b")
_DECORATION = " \t>*_`#-+"

# CommonMark-ish fences. The list-marker prefix matters: "- ```" opens a fence.
# Both ends allow at most three leading spaces, as CommonMark does: a deeper
# indent is content, so an indented ``` inside a quoted file body cannot end the
# quotation.
_FENCE_OPEN_RE = re.compile(
    r"^ {0,3}(?:(?:[-*+]|[0-9]{1,9}[.)])[ \t]+)?(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")


def _fence_open(line: str):
    """Return (char, length) when `line` opens a fenced block, else None.

    The info string is read only to rule out an inline code span; what it says
    decides nothing, because a fence's own contents are literal.
    """
    match = _FENCE_OPEN_RE.match(line)
    if not match:
        return None
    fence, info = match.group("fence"), match.group("info")
    if fence[0] == "`" and "`" in info:
        return None  # an inline code span, not a fence
    return fence[0], len(fence)


def _fence_close(line: str):
    """Return (char, length) when `line` could close a fenced block, else None."""
    match = _FENCE_CLOSE_RE.match(line)
    if not match:
        return None
    return match.group("fence")[0], len(match.group("fence"))


def evidence_lines(text: str) -> Iterator[tuple[str, bool]]:
    """Yield (line, quoted) for every line of one assistant text block.

    quoted is True for lines inside a fenced block, an indented code block or an
    HTML comment. Inside a fence, closing wins over opening: a bare fence of the
    same character and at least the same length ends the block, exactly as
    CommonMark says. Only a strictly longer fence nests, which is how one quotes
    a file whose own body contains ```json -- an info string decides nothing, so
    two ```python blocks in a row do not swallow the prose after them. Fence
    state never crosses a block boundary.
    """
    fences: list[tuple[str, int]] = []
    in_comment = False
    for line in text.splitlines():
        if fences:
            closer = _fence_close(line)
            if closer and closer[0] == fences[-1][0] and closer[1] >= fences[-1][1]:
                fences.pop()
            elif (opener := _fence_open(line)) and opener[1] > fences[-1][1]:
                fences.append(opener)
            yield line, True
        elif in_comment:
            in_comment = "-->" not in line
            yield line, True
        elif line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line[line.index("<!--") + 4:]
            yield line, True
        elif (opener := _fence_open(line)):
            fences.append(opener)
            yield line, True
        elif line.startswith("    ") or line.startswith("\t"):
            yield line, True
        else:
            yield line, False


def marker_from_line(line: str):
    """Return (kind, detail) for a line that is -- or tried to be -- a marker."""
    stripped = line.strip()
    match = MARKER_START_RE.match(stripped)
    if match:
        return MARKER_START, match.groupdict()
    match = MARKER_STEP_RE.match(stripped)
    if match:
        return MARKER_STEP, match.groupdict()
    if _NEAR_MISS_RE.match(stripped.lstrip(_DECORATION)):
        return MARKER_MALFORMED, {"text": stripped}
    return None


def markers_in_text(text: str) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield (kind, detail) for every marker-ish line of an assistant text block.

    A well-formed marker inside quoted text is downgraded to MARKER_QUOTED
    rather than dropped: "not evidence" and "never happened" are different
    findings. A near miss inside quoted text is only noise, so it is dropped.
    """
    for line, quoted in evidence_lines(text):
        found = marker_from_line(line)
        if not found:
            continue
        kind, detail = found
        if not quoted:
            yield kind, detail
        elif kind in (MARKER_START, MARKER_STEP):
            yield MARKER_QUOTED, {"text": line.strip(), "as": kind}


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def _oneline(value: str, limit: int = DESCRIBE_LIMIT) -> str:
    """Collapse a possibly multi-line value onto one bounded line."""
    flat = value.replace("\r\n", "\n").replace("\n", "⏎").replace("\t", " ")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@dataclass
class Event:
    """One observable thing that happened, in execution order."""

    seq: int
    kind: str  # one of the kind constants above
    origin: str  # "main" or "subagent:<agent type>"
    source: str  # file the event was read from
    line: int
    name: str = ""  # platform tool name -- display only, never judged on
    detail: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        where = f"{self.origin} {Path(self.source).name}:{self.line}"
        if self.kind == MARKER_START:
            body = 'BPTRACE start theme="{theme}" wf={wf}'.format(**self.detail)
        elif self.kind == MARKER_STEP:
            body = "BPTRACE step={step} out actor={actor}".format(**self.detail)
        elif self.kind == MARKER_MALFORMED:
            body = f"malformed BPTRACE line: {_oneline(self.detail.get('text', ''))!r}"
        elif self.kind == MARKER_QUOTED:
            body = f"quoted BPTRACE line: {_oneline(self.detail.get('text', ''))!r}"
        elif self.kind == DELEGATE:
            body = f"delegate -> {self.detail.get('to') or '?'}"
        elif self.kind == BASH:
            body = f"bash: {_oneline(self.detail.get('command', ''))}"
        else:
            body = f"tool: {self.name}"
        return f"[{where}] {body}"


@dataclass
class Run:
    """One transcript, parsed into an ordered event stream."""

    path: Path
    platform: str
    events: list[Event] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Session identifiers the transcript recorded, when the platform writes any.
    # Two files carrying the same one are two copies of one run.
    session_ids: set[str] = field(default_factory=set)

    def _of(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    @property
    def start_markers(self) -> list[Event]:
        return self._of(MARKER_START)

    @property
    def step_markers(self) -> list[Event]:
        return self._of(MARKER_STEP)

    @property
    def malformed_markers(self) -> list[Event]:
        return self._of(MARKER_MALFORMED)

    @property
    def quoted_markers(self) -> list[Event]:
        return self._of(MARKER_QUOTED)

    @property
    def tool_uses(self) -> list[Event]:
        return self._of(*TOOL_KINDS)

    @property
    def delegations(self) -> list[Event]:
        return self._of(DELEGATE)

    @property
    def bash_calls(self) -> list[Event]:
        return self._of(BASH)


def finalize_run(path, platform: str, events: list[Event], warnings: list[str],
                 session_ids: Optional[Iterable[str]] = None) -> Run:
    """Wrap a parser's raw event list into a Run, numbering it once, in order."""
    run = Run(path=Path(path), platform=platform, events=list(events),
              warnings=list(warnings), session_ids=set(session_ids or ()))
    for seq, event in enumerate(run.events, 1):
        event.seq = seq
    return run


# --------------------------------------------------------------------------- #
# Claude Code parser
# --------------------------------------------------------------------------- #
AGENT_TOOL_NAMES = {"Agent", "Task"}
BASH_TOOL_NAMES = {"Bash"}


def _read_jsonl(path, warnings):
    """Yield (line_no, obj) for every parsable line; note the rest as warnings."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield line_no, json.loads(raw)
                except json.JSONDecodeError as exc:
                    warnings.append(f"{path.name}: line {line_no} is not valid JSON ({exc.msg})")
    except OSError as exc:
        raise TranscriptError(f"cannot read {path}: {exc}") from exc


def _cc_tool_event(name, tool_input):
    """Normalise a CC tool call into (kind, detail). The only tool-name table."""
    if name in AGENT_TOOL_NAMES:
        return DELEGATE, {"to": tool_input.get("subagent_type")
                          or tool_input.get("agent_type") or ""}
    if name in BASH_TOOL_NAMES:
        return BASH, {"command": tool_input.get("command", "")}
    return TOOL, {}


def _cc_subagent_index(main_path, warnings):
    """Map an Agent tool_use id to (transcript path, agent type) for one session.

    CC writes every subagent of a session -- including nested ones -- flat into
    the same `subagents/` directory, so one index serves the whole tree.
    """
    index = {}
    sub_dir = Path(main_path).with_suffix("") / "subagents"
    if not sub_dir.is_dir():
        return index
    for meta_path in sorted(sub_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except OSError as exc:
            warnings.append(f"{meta_path.name}: cannot be read ({exc})")
            continue
        except json.JSONDecodeError as exc:
            warnings.append(f"{meta_path.name}: not valid JSON ({exc.msg})")
            continue
        if not isinstance(meta, dict):
            warnings.append(
                f"{meta_path.name}: expected a JSON object, got {type(meta).__name__}")
            continue
        transcript = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        tool_use_id = meta.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            warnings.append(
                f"{meta_path.name}: no toolUseId, so its transcript cannot be linked "
                f"to a delegation")
            continue
        if not transcript.exists():
            warnings.append(f"{meta_path.name}: no transcript {transcript.name} beside it")
            continue
        agent_type = meta.get("agentType")
        if not isinstance(agent_type, str) or not agent_type:
            agent_type = UNKNOWN_AGENT
            warnings.append(
                f"{meta_path.name}: no agentType, so its events are attributed to "
                f"origin subagent:{UNKNOWN_AGENT}")
        index[tool_use_id] = (transcript, agent_type)
    return index


def _cc_scan(path, origin, warnings, session_ids=None):
    """Turn one CC jsonl file into events. Model text is the only marker evidence.

    Events come back with seq=0; finalize_run numbers the merged stream. When a
    set is handed in, every `sessionId` the file records is added to it.

    Only `type=text` blocks are read for markers. A `thinking` block is the
    model's private reasoning, not its output -- it is the most common block
    kind in a real transcript, and saying a marker there is not emitting it.
    """
    events = []
    for line_no, entry in _read_jsonl(path, warnings):
        if entry.get("type") != "assistant":
            continue
        if session_ids is not None:
            session_id = entry.get("sessionId")
            if isinstance(session_id, str) and session_id:
                session_ids.add(session_id)
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                for kind, detail in markers_in_text(block.get("text") or ""):
                    events.append(Event(0, kind, origin, str(path), line_no,
                                        detail=detail))
            elif block.get("type") == "tool_use":
                name = block.get("name") or ""
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                kind, detail = _cc_tool_event(name, tool_input)
                detail["id"] = block.get("id") or ""
                events.append(Event(0, kind, origin, str(path), line_no,
                                    name=name, detail=detail))
    return events


def _cc_splice(events, subagents, warnings, visited):
    """Insert each delegated transcript at its delegation point, recursively.

    CC records delegation trees deeper than one level, so the expansion has to
    recurse or those events are dropped.

    The splice is also where a delegation with no recorded target gets one: CC
    sometimes writes an Agent call without `subagent_type`, and the .meta.json
    of the transcript it spawned knows the agent type anyway. Taking it from
    there keeps `delegate:to=X` and `origin=subagent:X` reading the same source,
    instead of one of them being unmatchable by construction.
    """
    merged = []
    for event in events:
        merged.append(event)
        if event.kind != DELEGATE:
            continue
        found = subagents.get(event.detail.get("id"))
        if not found:
            if subagents or event.detail.get("to"):
                warnings.append(
                    f"no subagent transcript for {event.name} id={event.detail.get('id')}")
            continue
        sub_path, agent_type = found
        if not event.detail.get("to") and agent_type != UNKNOWN_AGENT:
            event.detail["to"] = agent_type
        key = sub_path.resolve()
        if key in visited:
            warnings.append(
                f"subagent transcript {sub_path.name} is referenced more than once; "
                f"not expanded again")
            continue
        visited.add(key)
        sub_events = _cc_scan(sub_path, f"subagent:{agent_type}", warnings)
        merged.extend(_cc_splice(sub_events, subagents, warnings, visited))
    return merged


def parse_cc_run(path) -> Run:
    """Parse a Claude Code transcript, splicing subagent transcripts in place."""
    path = Path(path)
    if not path.is_file():
        raise TranscriptError(f"no such transcript: {path}")
    warnings: list[str] = []
    session_ids: set[str] = set()
    subagents = _cc_subagent_index(path, warnings)
    events = _cc_scan(path, MAIN, warnings, session_ids)
    events = _cc_splice(events, subagents, warnings, {path.resolve()})
    return finalize_run(path, "cc", events, warnings, session_ids)


def cc_project_dir(repo_path) -> Path:
    """~/.claude/projects/<abs path with every non-alphanumeric char as '-'>.

    CC collapses dots as well as separators, so `/x/.claude-worktrees/w` becomes
    `-x--claude-worktrees-w`.
    """
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(repo_path).resolve()))
    return Path.home() / ".claude" / "projects" / slug


def cc_transcripts(project_dir) -> Iterable[Path]:
    """Every file in a CC project directory that could be a session transcript."""
    return Path(project_dir).glob("*.jsonl")


# --------------------------------------------------------------------------- #
# Platform registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Platform:
    """Everything platform specific, bound together so main() picks one thing."""

    name: str
    parse: Callable[[Path], Run]
    project_dir: Callable[[object], Path]
    transcripts: Callable[[Path], Iterable[Path]]


PLATFORMS = {"cc": Platform("cc", parse_cc_run, cc_project_dir, cc_transcripts)}


# --------------------------------------------------------------------------- #
# Run discovery
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Exclusion:
    """A candidate that was not put in the sample, and why."""

    path: Path
    reason: str


@dataclass
class Discovery:
    """The sample to judge, plus every candidate that did not make it into it."""

    requested: int
    explicit: bool = False
    runs: list[Run] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)

    @property
    def shortfall(self) -> int:
        """How many requested runs were never found. The only definition of it."""
        return max(0, self.requested - len(self.runs))

    @property
    def duplicates(self) -> list[str]:
        """Runs in the sample that are really the same recorded session.

        Distinct files can still be one run: a copy, a re-export, the same
        session under two names. Counting them separately is how a sample of
        one is reported as "3 of 3".
        """
        by_session: dict[str, list[str]] = {}
        for run in self.runs:
            for session_id in sorted(run.session_ids):
                by_session.setdefault(session_id, []).append(run.path.name)
        return [f"{', '.join(names)} all record session {session_id}, so they "
                f"are one run, not {len(names)}"
                for session_id, names in sorted(by_session.items()) if len(names) > 1]

    @property
    def gaps(self) -> list[str]:
        """Every reason the sample may not be the evidence that was asked for."""
        notes = [f"excluded {e.path.name}: {e.reason}" for e in self.excluded]
        notes.extend(self.duplicates)
        if self.shortfall:
            notes.append(f"asked for {self.requested} run(s) but only "
                         f"{len(self.runs)} could be measured")
        return notes


def parse_since(value: str) -> float:
    """Turn an ISO 8601 timestamp into an epoch second count.

    A trailing 'Z' means UTC; a naive stamp is read in the machine's own zone.
    """
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageError(f"--since wants an ISO 8601 timestamp, got {value!r} ({exc})") from exc
    return moment.timestamp()


def _newest_first(paths, found: Discovery) -> list[tuple[float, Path]]:
    """Sort candidates by mtime, newest first; a file that cannot be stat()ed is
    reported rather than dropped."""
    dated = []
    for candidate in paths:
        try:
            dated.append((candidate.stat().st_mtime, candidate))
        except OSError as exc:
            found.excluded.append(Exclusion(candidate, f"cannot be stat()ed ({exc})"))
    dated.sort(key=lambda pair: (-pair[0], pair[1].name))
    return dated


def load_runs(paths, parse: Callable[[Path], Run]) -> Discovery:
    """Parse exactly the transcripts the caller named. Nothing is filtered out.

    The one thing that is checked first is that they are N different files:
    `a.jsonl ./a.jsonl /abs/a.jsonl` is one run spelled three ways, and letting
    it report "3/3 run(s) PASS" would satisfy a "3 runs out of 3" criterion with
    a single run. Sameness is decided on the resolved path, so symlinks and
    `..` cannot spell a repeat past it either.
    """
    seen: dict[Path, str] = {}
    for path in paths:
        resolved = Path(path).resolve()
        if resolved in seen:
            raise UsageError(
                f"{path} and {seen[resolved]} are the same transcript ({resolved}); "
                f"N runs means N different transcripts")
        seen[resolved] = str(path)
    found = Discovery(requested=len(paths), explicit=True)
    for path in paths:
        try:
            found.runs.append(parse(path))
        except TranscriptError as exc:
            found.excluded.append(Exclusion(Path(path), str(exc)))
    return found


def find_runs(project_dir, count: int, parse: Callable[[Path], Run],
              transcripts: Callable[[Path], Iterable[Path]],
              theme: Optional[str] = None,
              since: Optional[float] = None) -> Discovery:
    """Return a Discovery holding the newest `count` runs, newest first.

    `since` (epoch seconds) is a window: transcripts older than it were never
    candidates. Everything else that is passed over is an Exclusion carrying its
    reason, including a `theme` that does not match -- a run without a start
    marker is precisely the one a measurement must not lose.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise TranscriptError(f"no such project directory: {project_dir}")
    found = Discovery(requested=count)
    for mtime, candidate in _newest_first(transcripts(project_dir), found):
        if len(found.runs) >= count:
            break
        if since is not None and mtime < since:
            break
        try:
            run = parse(candidate)
        except TranscriptError as exc:
            found.excluded.append(Exclusion(candidate, f"cannot be read ({exc})"))
            continue
        if theme is not None and not any(
                m.detail.get("theme") == theme for m in run.start_markers):
            found.excluded.append(Exclusion(
                candidate, f"emitted no 'BPTRACE start' with theme {theme!r}"))
            continue
        found.runs.append(run)
    return found


# --------------------------------------------------------------------------- #
# Expectations (platform independent)
# --------------------------------------------------------------------------- #
_SELECTOR_KIND = {"start": MARKER_START, "step": MARKER_STEP,
                  "delegate": DELEGATE, "bash": BASH}
_SELECTOR_KEYS = {
    "start": {"theme", "wf"},
    "step": {"actor"},
    "delegate": {"to"},
    "bash": {"contains"},
}
_COMMON_KEYS = {"origin"}


@dataclass
class Expectation:
    spec: str
    selector: str
    step: str = ""
    keys: dict[str, str] = field(default_factory=dict)
    count: Optional[int] = None  # None = "at least one, in order"

    @property
    def kind(self) -> str:
        return _SELECTOR_KIND[self.selector]

    def matches(self, event: Event) -> bool:
        if event.kind != self.kind:
            return False
        if self.selector == "step" and event.detail.get("step") != self.step:
            return False
        for key, want in self.keys.items():
            if key == "origin":
                if event.origin != want:
                    return False
            elif key == "contains":
                if want not in event.detail.get("command", ""):
                    return False
            elif event.detail.get(key) != want:
                return False
        return True


def _split_pairs(tail: str, spec: str) -> list[str]:
    """Split `key=value,key=value` on unescaped commas, unescaping as it goes."""
    parts, current, escaped = [], [], False
    for char in tail:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        raise UsageError(
            f"{spec!r} ends in a lone backslash; write \\\\ for a literal backslash")
    parts.append("".join(current))
    return [p for p in parts if p]


def _parse_spec(spec: str, count: Optional[int] = None) -> Expectation:
    head, _, tail = spec.partition(":")
    selector, _, step = head.partition("=")
    if selector not in _SELECTOR_KIND:
        raise UsageError(
            f"unknown selector {selector!r} in {spec!r}; "
            f"use one of {', '.join(sorted(_SELECTOR_KIND))}")
    if selector == "step":
        if not (step.isascii() and step.isdigit()):
            raise UsageError(f"'step' needs a decimal number, as in step=1 (got {spec!r})")
        if len(step) > 1 and step.startswith("0"):
            raise UsageError(
                f"'step' must not be zero padded: markers are emitted as "
                f"step={int(step)}, so step={step} could never match ({spec!r})")
    elif step:
        raise UsageError(f"selector {selector!r} takes no '=<value>' (got {head!r} in {spec!r})")
    allowed = _SELECTOR_KEYS[selector] | _COMMON_KEYS
    keys: dict[str, str] = {}
    for part in _split_pairs(tail, spec):
        key, sep, value = part.partition("=")
        if not sep:
            raise UsageError(f"expected key=value, got {part!r} in {spec!r}")
        if key not in allowed:
            raise UsageError(
                f"{selector!r} has no key {key!r}; valid keys: {', '.join(sorted(allowed))}")
        if key in keys:
            raise UsageError(f"key {key!r} is given twice in {spec!r}")
        if key == "contains" and not value:
            raise UsageError(
                f"'contains' may not be empty in {spec!r}: it would match every "
                f"command there is")
        keys[key] = value
    return Expectation(spec=spec, selector=selector, step=step, keys=keys, count=count)


def parse_expectation(spec: str) -> Expectation:
    """Parse an ordered expectation ('at least one match, in this order')."""
    return _parse_spec(spec)


def parse_count_expectation(spec: str) -> Expectation:
    """Parse 'SPEC=N' into an exact-count expectation, splitting at the last '='."""
    body, sep, number = spec.rpartition("=")
    if not sep:
        raise UsageError(f"expected SPEC=<count>, got {spec!r}")
    if not (number.isascii() and number.isdigit()):
        raise UsageError(
            f"expected SPEC=<count> with a plain non-negative decimal count, "
            f"got {number!r} in {spec!r}")
    return _parse_spec(body, count=int(number))


def is_checked(ordered, counted) -> bool:
    """True when the caller actually asked for a verdict. The only definition."""
    return bool(ordered or counted)


@dataclass
class Result:
    run: Run
    passed: bool
    failures: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    checked: bool = False


def _nearby(run: Run, expectation: Expectation) -> str:
    """What was actually observed for the same selector, for the failure text."""
    loose = Expectation(expectation.spec, expectation.selector, expectation.step)
    seen = [e.describe() for e in run.events if loose.matches(e)]
    if seen:
        return "; observed: " + " / ".join(seen)
    # Widen: the same kind of thing, plus any line that tried to be a marker.
    wanted = {expectation.kind}
    if expectation.selector in ("start", "step"):
        wanted |= {MARKER_MALFORMED, MARKER_QUOTED}
    seen = [e.describe() for e in run.events if e.kind in wanted]
    return "; observed: " + (" / ".join(seen) if seen else "nothing of that kind")


def _marker_identity(kind: str, detail: dict[str, str]):
    """What makes two markers "the same one", for judging a quotation against
    what was genuinely emitted.

    A quoted step=2 is not vindicated by step=1 having really happened -- it
    must be that exact step, by that exact actor. A quoted start marker
    likewise needs a real start of that exact theme.
    """
    if kind == MARKER_STEP:
        return (kind, detail.get("step"), detail.get("actor"))
    return (kind, detail.get("theme"))


def evaluate(run: Run, ordered, counted, allow_anomalies: bool = False) -> Result:
    """Judge a parsed run against the expectations it was given, and nothing else.

    There is deliberately no built-in "a good run looks like this": how many
    start markers a measurement wants is the measurement's business, expressed
    as --expect-count 'start=1'. A check about something else entirely, say
    --expect-count 'bash:contains=make=1', must not be failed by a BPTRACE rule
    it never asked for.

    An anomaly is damage to the evidence itself, not a verdict.
    """
    failures: list[str] = []
    anomalies: list[str] = []

    for marker in run.malformed_markers:
        anomalies.append(f"malformed BPTRACE line at {marker.describe()}")
    # Quoting a marker is only suspicious when that exact marker was never
    # really emitted: "here is the line I am about to print", followed by
    # printing it, is what a well-behaved run looks like. Some *other* marker
    # of the same kind having been emitted for real does not vindicate it --
    # a fabricated step=2 is still fabricated even if step=1 really happened.
    emitted_identities = {_marker_identity(MARKER_START, m.detail) for m in run.start_markers}
    emitted_identities |= {_marker_identity(MARKER_STEP, m.detail) for m in run.step_markers}
    for marker in run.quoted_markers:
        parsed = marker_from_line(marker.detail.get("text", ""))
        identity = _marker_identity(*parsed) if parsed else None
        if identity in emitted_identities:
            continue
        anomalies.append(
            f"BPTRACE marker only appears inside quoted text, so it is not "
            f"evidence: {marker.describe()}")
    anomalies.extend(run.warnings)

    cursor = 0
    for expectation in ordered:
        hit = next((i for i in range(cursor, len(run.events))
                    if expectation.matches(run.events[i])), None)
        if hit is None:
            anywhere = any(expectation.matches(e) for e in run.events)
            reason = "out of order" if anywhere else "not found"
            failures.append(f"expected {expectation.spec} ({reason}){_nearby(run, expectation)}")
        else:
            cursor = hit + 1

    for expectation in counted:
        actual = sum(1 for e in run.events if expectation.matches(e))
        if actual != expectation.count:
            failures.append(
                f"expected {expectation.count} x {expectation.spec}, got {actual}"
                f"{_nearby(run, expectation)}")

    checked = is_checked(ordered, counted)
    if checked and anomalies and not allow_anomalies:
        failures.append(
            f"{len(anomalies)} anomaly/anomalies in the evidence, so this run cannot be "
            f"trusted (pass --allow-anomalies to judge it anyway)")
    return Result(run=run, passed=not failures, failures=failures,
                  anomalies=anomalies, checked=checked)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _run_dict(result: Result) -> dict:
    run = result.run
    return {
        "path": str(run.path),
        "platform": run.platform,
        "start_markers": [dict(m.detail, origin=m.origin) for m in run.start_markers],
        "step_markers": [{"step": m.detail["step"], "actor": m.detail["actor"],
                          "origin": m.origin} for m in run.step_markers],
        "malformed_markers": [{"text": m.detail["text"], "origin": m.origin,
                               "source": m.source, "line": m.line}
                              for m in run.malformed_markers],
        "quoted_markers": [{"text": m.detail["text"], "as": m.detail.get("as", ""),
                            "origin": m.origin, "source": m.source, "line": m.line}
                           for m in run.quoted_markers],
        "delegations": [{"to": d.detail.get("to", ""), "origin": d.origin}
                        for d in run.delegations],
        "bash_calls": [{"command": b.detail.get("command", ""), "origin": b.origin}
                       for b in run.bash_calls],
        "events": [{"seq": e.seq, "kind": e.kind, "origin": e.origin, "name": e.name,
                    "line": e.line, "source": e.source, "detail": e.detail}
                   for e in run.events],
        "checked": result.checked,
        "passed": result.passed if result.checked else None,
        "failures": result.failures,
        "anomalies": result.anomalies,
    }


def print_discovery(found: Discovery, out) -> None:
    """Show the sample and everything that was left out of it, with reasons."""
    print(f"selected {len(found.runs)}/{found.requested} run(s)", file=out)
    for run in found.runs:
        print(f"  + {run.path}", file=out)
    print(f"excluded {len(found.excluded)} candidate(s)", file=out)
    for exclusion in found.excluded:
        print(f"  - {exclusion.path}: {exclusion.reason}", file=out)


def _print_list(label: str, events: list[Event], out, verbose: bool) -> None:
    print(f"  {label} : {len(events)}", file=out)
    shown = events if verbose or len(events) <= LIST_LIMIT else events[:LIST_LIMIT]
    for event in shown:
        print(f"    - {event.describe()}", file=out)
    if len(shown) < len(events):
        print(f"    ... {len(events) - len(shown)} more (--verbose to list them)", file=out)


def print_report(result: Result, out, verbose: bool = False) -> None:
    run = result.run
    print(f"=== {run.path}", file=out)
    _print_list("start markers", run.start_markers, out, verbose)
    _print_list("step markers ", run.step_markers, out, verbose)
    _print_list("delegations  ", run.delegations, out, verbose)
    _print_list("bash calls   ", run.bash_calls, out, verbose)
    _print_list("malformed    ", run.malformed_markers, out, verbose)
    _print_list("quoted       ", run.quoted_markers, out, verbose)
    print(f"  event order   : {len(run.events)} event(s)", file=out)
    if verbose or (result.checked and not result.passed):
        for event in run.events:
            print(f"    {event.seq:>3}. {event.describe()}", file=out)
    for note in result.anomalies:
        print(f"  ! {note}", file=out)
    if not result.checked:
        print("  REPORT ONLY (no expectations given)", file=out)
        return
    for failure in result.failures:
        print(f"  x {failure}", file=out)
    print(f"  {'PASS' if result.passed else 'FAIL'}", file=out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__[__doc__.index("Usage:"):])
    parser.add_argument("transcript", nargs="*", metavar="RUN.jsonl",
                        help="the transcripts to judge; every one of them is judged")
    parser.add_argument("--latest", type=int, metavar="N",
                        help="instead, discover the newest N runs in the project directory")
    parser.add_argument("--theme", metavar="TEXT",
                        help="keep only runs whose 'BPTRACE start' theme is exactly TEXT; "
                             "candidates that do not match are reported, not dropped")
    parser.add_argument("--since", metavar="ISO8601",
                        help="window: only transcripts modified at or after this timestamp")
    parser.add_argument("--project-dir", help="where to look for transcripts (default: derived "
                                              "from --repo / the current directory)")
    parser.add_argument("--repo", help="repository the transcripts belong to (default: .)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the selection and its exclusions, then stop; "
                             "refused together with any expectation")
    parser.add_argument("--platform", default="cc", choices=sorted(PLATFORMS),
                        help="transcript format (default: cc)")
    parser.add_argument("--expect", "--expect-marker", action="append", default=[],
                        metavar="SPEC", dest="expect",
                        help="ordered expectation, e.g. 'step=1:actor=main,origin=main'")
    parser.add_argument("--expect-count", action="append", default=[], metavar="SPEC=N",
                        help="exact-count expectation, e.g. 'bash:contains=build.sh=2'")
    parser.add_argument("--expect-delegations", type=int, metavar="N",
                        help="shorthand for --expect-count delegate=N")
    parser.add_argument("--allow-anomalies", action="store_true",
                        help="report damaged evidence inside a run but do not fail for it; "
                             "a gap in the sample itself still fails")
    parser.add_argument("--verbose", action="store_true",
                        help="print every event and every list in full (implied by a failure)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _discover(args, platform: Platform) -> Discovery:
    """Resolve the command line into the runs to judge, plus what was left out."""
    if bool(args.transcript) == (args.latest is not None):
        raise UsageError("give either one or more transcript paths or --latest N")
    if args.transcript:
        for name in ("project_dir", "repo", "theme", "since"):
            if getattr(args, name) is not None:
                raise UsageError(
                    f"--{name.replace('_', '-')} only applies to --latest; explicit "
                    f"transcript paths already are the whole selection")
        return load_runs(args.transcript, platform.parse)
    if args.latest < 1:
        raise UsageError("--latest needs a positive count")
    project_dir = (Path(args.project_dir) if args.project_dir
                   else platform.project_dir(args.repo or "."))
    since = parse_since(args.since) if args.since else None
    return find_runs(project_dir, args.latest, platform.parse, platform.transcripts,
                     theme=args.theme, since=since)


def _run(args) -> int:
    platform = PLATFORMS[args.platform]
    ordered = [parse_expectation(s) for s in args.expect]
    counted = [parse_count_expectation(s) for s in args.expect_count]
    if args.expect_delegations is not None:
        counted.append(parse_count_expectation(f"delegate={args.expect_delegations}"))
    checked = is_checked(ordered, counted)
    if args.dry_run and checked:
        # Silently dropping the expectations would turn "I forgot to delete
        # --dry-run" into an unconditional exit 0 that judged nothing.
        raise UsageError(
            "--dry-run lists the selection and stops; it never judges it. Give "
            "--expect/--expect-count/--expect-delegations or --dry-run, not both")

    found = _discover(args, platform)
    if args.dry_run:
        if args.json:
            json.dump({"passed": None, "checked": False, "dry_run": True,
                       "requested": found.requested, "missing": found.shortfall,
                       "selected": [str(r.path) for r in found.runs],
                       "excluded": [{"path": str(e.path), "reason": e.reason}
                                    for e in found.excluded],
                       "gaps": [], "runs": []},
                      sys.stdout, indent=2, ensure_ascii=False)
            print()
        else:
            print_discovery(found, sys.stdout)
        return 0
    if not checked and found.explicit and found.excluded:
        # Nothing to judge and the named file could not be read: an IO error.
        raise TranscriptError(found.excluded[0].reason)

    results = [evaluate(run, ordered, counted, args.allow_anomalies) for run in found.runs]
    # A gap in the sample -- including a duplicate session -- is real evidence
    # about the sample and is reported whether or not a verdict was asked for.
    # It is not an anomaly to tolerate, either: it is missing evidence, and
    # --allow-anomalies does not reach it. Only the verdict itself stays gated
    # on `checked`, so report-only mode keeps its exit 0 / `passed: null`.
    gaps = found.gaps
    passed = all(r.passed for r in results) and not (checked and gaps)

    if args.json:
        # No expectation, no verdict: `true` here would read as "it was checked
        # and it was fine", which is exactly the mistake a forgotten --expect
        # makes. The exit code stays 0 -- reporting is a legitimate use.
        json.dump({"passed": passed if checked else None,
                   "checked": checked, "requested": found.requested,
                   "missing": found.shortfall,
                   "selected": [str(r.path) for r in found.runs],
                   "excluded": [{"path": str(e.path), "reason": e.reason}
                                for e in found.excluded],
                   "gaps": gaps,
                   "runs": [_run_dict(r) for r in results]},
                  sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print_discovery(found, sys.stdout)
        for result in results:
            print_report(result, sys.stdout, verbose=args.verbose)
        for gap in gaps:
            print(f"x {gap}")
        if checked:
            good = sum(1 for r in results if r.passed)
            missing = f" ({found.shortfall} missing)" if found.shortfall else ""
            print(f"{good}/{found.requested} run(s) PASS{missing} "
                  f"-> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def _silence_stdout() -> None:
    """Redirect stdout to /dev/null so the interpreter's exit flush cannot fail."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:
        pass


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return _run(args)
    except (UsageError, TranscriptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        _silence_stdout()  # `... | head` is not a failure of the check
        return 0
    except Exception as exc:  # a crash must never be mistaken for a FAIL verdict
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
