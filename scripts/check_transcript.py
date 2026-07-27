#!/usr/bin/env python3
"""Extract BPTRACE markers and tool calls from an agent transcript and check them.

Platform-agnostic by design: the parser layer turns a transcript into an ordered
Event stream whose kinds (delegate / bash / tool / marker_*) carry the meaning,
and the expectation layer judges that stream without knowing which platform
produced it or what its tools are called. A second platform plugs in by adding
one entry to PLATFORMS. No project-specific expectation is baked in -- callers
pass what they expect on the command line.

Only text the model itself produced is treated as marker evidence: assistant
`type=text` content blocks, whole-line matches, outside fenced code blocks.
Markers appearing in tool results, tool inputs, file contents, prose or
quotations are deliberately ignored. A line that tried to be a marker but
missed the format is recorded as `marker_malformed` and reported -- never
silently dropped, because a near-miss is the most informative failure there is.

Usage:
  check_transcript.py <transcript.jsonl> [checks...]
  check_transcript.py --latest N [--theme T] [--since ISO] [--project-dir DIR] [checks...]

Selecting runs:
  --latest N                the newest N transcripts in the project directory
  --theme TEXT              only runs whose `BPTRACE start` theme equals TEXT
  --since ISO8601           only transcripts modified at or after that moment
  --project-dir DIR         where to look (default: derived from --repo)
  --repo PATH               repository the transcripts belong to (default: .)

  Selection never consults the verdict. Filtering on "has a start marker"
  would drop exactly the runs worth catching -- the ones where the model never
  emitted one -- and quietly backfill the sample with older runs. Scope a
  measurement with --theme (design doc 7.3) and/or --since instead.

Checks:
  --expect SPEC             ordered: SPEC must match at least once, in this order
  --expect-count SPEC=N     SPEC must match exactly N times
  --expect-delegations N    shorthand for --expect-count delegate=N
  --allow-anomalies         tolerate damaged evidence instead of failing on it

SPEC syntax: <selector>[:<key>=<value>[,<key>=<value>]...]
  start                     the "BPTRACE start" line      keys: theme, wf
  step=<n>                  a "BPTRACE step=<n> out" line keys: actor
  delegate                  a subagent invocation         keys: to
  bash                      a shell invocation            keys: contains (substring)

  Every selector also accepts `origin`, matched exactly: "main" for the parent
  agent, "subagent:<agent type>" for a delegated one. That is what separates
  "the subagent emitted this marker itself" from "the parent copied the
  subagent's output into its own message".

  Values are compared exactly (except `contains`) and may not contain a comma.
  --expect-count splits at the LAST '=' in the argument: everything to its left
  is the SPEC, everything to its right is the count. So
  'bash:contains=a=b=2' means "exactly 2 bash calls whose command contains
  'a=b'". A count must be a plain non-negative decimal number.

Example -- the techtest stage-B invariant. This is BLACKPINK/techtest specific,
so it lives here as a usage example and not in the code:

  check_transcript.py --latest 3 --theme 'neon night' \
      --expect 'start:theme=neon night,wf=techtest.md' \
      --expect 'step=1:actor=main,origin=main' \
      --expect 'delegate:to=techtest-echo' \
      --expect 'step=2:actor=techtest-echo,origin=subagent:techtest-echo' \
      --expect-count 'start=1' \
      --expect-count 'delegate=1'

  The ordered expectations pin the delegation *between* step 1 and step 2, and
  'delegate=1' pins it to exactly one -- so a delegation at step 1 is ruled out
  by order and count together, and step 2's marker only counts when the
  subagent itself emitted it.

Exit codes: 0 = PASS (or report-only), 1 = FAIL, 2 = usage/IO/internal error.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Event kinds. The parser normalises to these; the judging layer sees only them.
MARKER_START = "marker_start"
MARKER_STEP = "marker_step"
MARKER_MALFORMED = "marker_malformed"
DELEGATE = "delegate"
BASH = "bash"
TOOL = "tool"
TOOL_KINDS = (DELEGATE, BASH, TOOL)

# Agent types and workflow file names, kept tight so that stray punctuation
# (a trailing period, a bold marker's '**') fails the match instead of being
# swallowed into the captured value.
_ACTOR = r"[A-Za-z0-9_-]+"
_WF = r"[A-Za-z0-9_./-]+"
MARKER_START_RE = re.compile(
    rf'^BPTRACE\s+start\s+theme="(?P<theme>[^"]*)"\s+wf=(?P<wf>{_WF})$')
MARKER_STEP_RE = re.compile(
    rf"^BPTRACE\s+step=(?P<step>\d+)\s+out\s+actor=(?P<actor>{_ACTOR})$")
# "This line meant to be a marker": the token on its own, after any decoration.
_BPTRACE_RE = re.compile(r"BPTRACE(?![A-Za-z0-9_])")
_DECORATION = " \t>*_`#-+"
_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})")

MAIN = "main"


class TranscriptError(Exception):
    """The transcript could not be read."""


class UsageError(Exception):
    """The command line asked for something that does not exist."""


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    """One observable thing that happened, in execution order."""

    seq: int
    kind: str  # one of the kind constants above
    origin: str  # "main" or "subagent:<name>"
    source: str  # file the event was read from
    line: int
    name: str = ""  # platform tool name -- display only, never judged on
    detail: dict = field(default_factory=dict)

    def describe(self):
        where = f"{self.origin} {Path(self.source).name}:{self.line}"
        if self.kind == MARKER_START:
            body = 'BPTRACE start theme="{theme}" wf={wf}'.format(**self.detail)
        elif self.kind == MARKER_STEP:
            body = "BPTRACE step={step} out actor={actor}".format(**self.detail)
        elif self.kind == MARKER_MALFORMED:
            body = f"malformed BPTRACE line: {self.detail.get('text', '')!r}"
        elif self.kind == DELEGATE:
            body = f"delegate -> {self.detail.get('to') or '?'}"
        elif self.kind == BASH:
            body = f"bash: {self.detail.get('command', '')}"
        else:
            body = f"tool: {self.name}"
        return f"[{where}] {body}"


@dataclass
class Run:
    """One transcript, parsed into an ordered event stream."""

    path: Path
    platform: str
    events: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def _of(self, *kinds):
        return [e for e in self.events if e.kind in kinds]

    @property
    def start_markers(self):
        return self._of(MARKER_START)

    @property
    def step_markers(self):
        return self._of(MARKER_STEP)

    @property
    def malformed_markers(self):
        return self._of(MARKER_MALFORMED)

    @property
    def tool_uses(self):
        return self._of(*TOOL_KINDS)

    @property
    def delegations(self):
        return self._of(DELEGATE)

    @property
    def bash_calls(self):
        return self._of(BASH)


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


def _evidence_lines(text):
    """Yield the lines of an assistant text block that count as model output.

    Lines inside a fenced code block are quotation -- the model showing what a
    workflow says, not the model running it -- so they are skipped. The fence
    state is carried across the whole block and reset between blocks, which is
    what lets a marker printed *after* a fenced JSON dump still count.
    """
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        opener = _FENCE_RE.match(stripped)
        if fence is None:
            if opener:
                fence = opener.group("fence")
            else:
                yield line
        elif (opener and opener.group("fence")[0] == fence[0]
              and len(opener.group("fence")) >= len(fence)
              and not stripped[len(opener.group("fence")):].strip()):
            fence = None


def _marker_from_line(line):
    """Return (kind, detail) for a line that is -- or tried to be -- a marker."""
    stripped = line.strip()
    probe = stripped.lstrip(_DECORATION)
    if not _BPTRACE_RE.match(probe):
        return None
    match = MARKER_START_RE.match(stripped)
    if match:
        return MARKER_START, match.groupdict()
    match = MARKER_STEP_RE.match(stripped)
    if match:
        return MARKER_STEP, match.groupdict()
    return MARKER_MALFORMED, {"text": stripped}


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
        index[tool_use_id] = (transcript, meta.get("agentType") or "subagent")
    return index


def _cc_scan(path, origin, warnings):
    """Turn one CC jsonl file into events. Model text is the only marker evidence.

    Events come back with seq=0; the caller numbers them once the merged stream
    (parent + spliced subagents) is assembled.
    """
    events = []
    for line_no, entry in _read_jsonl(path, warnings):
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                for text_line in _evidence_lines(block.get("text") or ""):
                    found = _marker_from_line(text_line)
                    if found:
                        kind, detail = found
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

    The design doc assumes one flat level of delegation, but CC does record
    deeper trees. Expanding them recursively is the difference between seeing
    those events and dropping them without a word.
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


def parse_cc_run(path):
    """Parse a Claude Code transcript, splicing subagent transcripts in place."""
    path = Path(path)
    if not path.is_file():
        raise TranscriptError(f"no such transcript: {path}")
    run = Run(path=path, platform="cc")
    subagents = _cc_subagent_index(path, run.warnings)
    events = _cc_scan(path, MAIN, run.warnings)
    run.events = _cc_splice(events, subagents, run.warnings, {path.resolve()})
    for seq, event in enumerate(run.events, 1):
        event.seq = seq
    return run


def cc_project_dir(repo_path):
    """~/.claude/projects/<abs path with every non-alphanumeric char as '-'>.

    CC collapses dots as well as separators, so `/x/.claude-worktrees/w` becomes
    `-x--claude-worktrees-w`.
    """
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(repo_path).resolve()))
    return Path.home() / ".claude" / "projects" / slug


# --------------------------------------------------------------------------- #
# Platform registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Platform:
    """Everything platform specific, bound together so main() picks one thing."""

    name: str
    parse: object        # (transcript path) -> Run
    project_dir: object  # (repo path) -> Path


PLATFORMS = {"cc": Platform("cc", parse_cc_run, cc_project_dir)}


# --------------------------------------------------------------------------- #
# Run discovery
# --------------------------------------------------------------------------- #
@dataclass
class Discovery:
    """The outcome of looking for runs to check."""

    requested: int
    runs: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def shortfall(self):
        """How many requested runs were never found. The only definition of it."""
        return max(0, self.requested - len(self.runs))


def parse_since(value):
    """Turn an ISO 8601 timestamp into an epoch second count."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageError(f"--since wants an ISO 8601 timestamp, got {value!r} ({exc})") from exc
    return moment.timestamp()


def _by_mtime_desc(project_dir):
    """The project dir's transcripts, newest first, tolerating files that vanish."""
    dated = []
    for candidate in project_dir.glob("*.jsonl"):
        try:
            dated.append((candidate.stat().st_mtime, candidate))
        except OSError:
            continue
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return dated


def find_latest_runs(project_dir, count, parser=parse_cc_run, theme=None, since=None):
    """Return a Discovery holding the newest `count` runs, newest first.

    Selection is deliberately blind to whether a run passes: a run that failed
    to emit its markers has to stay in the sample, or the check would be
    choosing its own evidence. Narrow the sample with `theme` (exact match on a
    `BPTRACE start` theme, design doc 7.3) or `since` (epoch seconds).
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise TranscriptError(f"no such project directory: {project_dir}")
    found = Discovery(requested=count)
    for mtime, candidate in _by_mtime_desc(project_dir):
        if len(found.runs) >= count:
            break
        if since is not None and mtime < since:
            continue
        try:
            run = parser(candidate)
        except TranscriptError as exc:
            found.warnings.append(f"skipped {candidate.name}: {exc}")
            continue
        if theme is not None and not any(
                m.detail.get("theme") == theme for m in run.start_markers):
            continue
        found.runs.append(run)
    if found.shortfall:
        scope = f" with theme {theme!r}" if theme is not None else ""
        found.warnings.append(
            f"asked for the latest {count} run(s){scope} but found only "
            f"{len(found.runs)} in {project_dir}")
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
    keys: dict = field(default_factory=dict)
    count: int = None  # None = "at least one, in order"

    @property
    def kind(self):
        return _SELECTOR_KIND[self.selector]

    def matches(self, event):
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


def _parse_spec(spec, count=None):
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
    keys = {}
    for part in filter(None, tail.split(",")):
        key, sep, value = part.partition("=")
        if not sep:
            raise UsageError(f"expected key=value, got {part!r} in {spec!r}")
        if key not in allowed:
            raise UsageError(
                f"{selector!r} has no key {key!r}; valid keys: {', '.join(sorted(allowed))}")
        if key in keys:
            raise UsageError(f"key {key!r} is given twice in {spec!r}")
        keys[key] = value
    return Expectation(spec=spec, selector=selector, step=step, keys=keys, count=count)


def parse_expectation(spec):
    """Parse an ordered expectation ('at least one match, in this order')."""
    return _parse_spec(spec)


def parse_count_expectation(spec):
    """Parse 'SPEC=N' into an exact-count expectation, splitting at the last '='."""
    body, sep, number = spec.rpartition("=")
    if not sep:
        raise UsageError(f"expected SPEC=<count>, got {spec!r}")
    if not (number.isascii() and number.isdigit()):
        raise UsageError(
            f"expected SPEC=<count> with a plain non-negative decimal count, "
            f"got {number!r} in {spec!r}")
    return _parse_spec(body, count=int(number))


def is_checked(ordered, counted):
    """True when the caller actually asked for a verdict. The only definition."""
    return bool(ordered or counted)


@dataclass
class Result:
    run: Run
    passed: bool
    failures: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)
    checked: bool = False


def _nearby(run, expectation):
    """Describe what was actually observed for the same selector, for the failure text."""
    loose = Expectation(expectation.spec, expectation.selector, expectation.step)
    seen = [e.describe() for e in run.events if loose.matches(e)]
    if seen:
        return "; observed: " + " / ".join(seen)
    # Widen: the same kind of thing, plus any line that tried to be a marker.
    wanted = {expectation.kind}
    if expectation.selector in ("start", "step"):
        wanted.add(MARKER_MALFORMED)
    seen = [e.describe() for e in run.events if e.kind in wanted]
    return "; observed: " + (" / ".join(seen) if seen else "nothing of that kind")


def evaluate(run, ordered, counted, allow_anomalies=False):
    """Judge a parsed run. Ordered expectations must match in sequence."""
    failures, anomalies = [], []

    starts = len(run.start_markers)
    if starts != 1:
        anomalies.append(
            f"{starts} start marker(s) found (exactly 1 expected for a well-formed run)")
    for marker in run.malformed_markers:
        anomalies.append(f"malformed BPTRACE line at {marker.describe()}")
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
def _run_dict(result):
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
        "delegations": [{"to": d.detail.get("to", ""), "origin": d.origin}
                        for d in run.delegations],
        "bash_calls": [{"command": b.detail.get("command", ""), "origin": b.origin}
                       for b in run.bash_calls],
        "events": [{"seq": e.seq, "kind": e.kind, "origin": e.origin, "name": e.name,
                    "line": e.line, "source": e.source, "detail": e.detail}
                   for e in run.events],
        "checked": result.checked,
        "passed": result.passed,
        "failures": result.failures,
        "anomalies": result.anomalies,
    }


def print_report(result, out, verbose=False):
    run = result.run
    print(f"=== {run.path}", file=out)
    for label, events in (("start markers", run.start_markers),
                          ("step markers ", run.step_markers),
                          ("delegations  ", run.delegations),
                          ("bash calls   ", run.bash_calls),
                          ("malformed    ", run.malformed_markers)):
        print(f"  {label} : {len(events)}", file=out)
        for event in events:
            print(f"    - {event.describe()}", file=out)
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
def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__[__doc__.index("Usage:"):])
    parser.add_argument("transcript", nargs="?", help="path to a transcript .jsonl")
    parser.add_argument("--latest", type=int, metavar="N",
                        help="check the newest N runs in the project directory")
    parser.add_argument("--theme", metavar="TEXT",
                        help="only runs whose 'BPTRACE start' theme is exactly TEXT")
    parser.add_argument("--since", metavar="ISO8601",
                        help="only transcripts modified at or after this timestamp")
    parser.add_argument("--project-dir", help="where to look for transcripts (default: derived "
                                              "from --repo / the current directory)")
    parser.add_argument("--repo", help="repository the transcripts belong to (default: .)")
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
                        help="report damaged evidence but do not fail the run for it")
    parser.add_argument("--verbose", action="store_true",
                        help="print the whole ordered event stream (implied by a failure)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _discover(args, platform):
    """Resolve the command line into the runs to judge, plus discovery warnings."""
    if (args.transcript is None) == (args.latest is None):
        raise UsageError("give either a transcript path or --latest N")
    if args.transcript is not None:
        for name in ("project_dir", "repo", "theme", "since"):
            if getattr(args, name) is not None:
                raise UsageError(
                    f"--{name.replace('_', '-')} only applies to --latest; an explicit "
                    f"transcript path already is the whole selection")
        return Discovery(requested=1, runs=[platform.parse(args.transcript)])
    if args.latest < 1:
        raise UsageError("--latest needs a positive count")
    project_dir = (Path(args.project_dir) if args.project_dir
                   else platform.project_dir(args.repo or "."))
    since = parse_since(args.since) if args.since else None
    return find_latest_runs(project_dir, args.latest, platform.parse,
                            theme=args.theme, since=since)


def _run(args):
    platform = PLATFORMS[args.platform]
    ordered = [parse_expectation(s) for s in args.expect]
    counted = [parse_count_expectation(s) for s in args.expect_count]
    if args.expect_delegations is not None:
        counted.append(parse_count_expectation(f"delegate={args.expect_delegations}"))
    checked = is_checked(ordered, counted)

    found = _discover(args, platform)
    results = [evaluate(run, ordered, counted, args.allow_anomalies) for run in found.runs]
    # A shortfall is only a verdict when a verdict was asked for; report-only
    # mode just says how little it found.
    passed = all(r.passed for r in results) and not (checked and found.shortfall)

    if args.json:
        json.dump({"passed": passed, "checked": checked, "requested": found.requested,
                   "missing": found.shortfall, "warnings": found.warnings,
                   "runs": [_run_dict(r) for r in results]},
                  sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        for result in results:
            print_report(result, sys.stdout, verbose=args.verbose)
        for warning in found.warnings:
            print(f"! {warning}")
        if checked:
            good = sum(1 for r in results if r.passed)
            missing = f" ({found.shortfall} missing)" if found.shortfall else ""
            print(f"{good}/{found.requested} run(s) PASS{missing} "
                  f"-> {'PASS' if passed else 'FAIL'}")
        elif not results:
            print("no runs found")
    return 0 if passed else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (UsageError, TranscriptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # a crash must never be mistaken for a FAIL verdict
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
