#!/usr/bin/env python3
"""Extract BPTRACE markers and tool calls from an agent transcript and check them.

Platform-agnostic by design: the parser layer turns a transcript into an ordered
Event stream (currently only the Claude Code parser exists; a GHC parser plugs in
at PARSERS), and the expectation layer judges that stream without knowing which
platform produced it. No project-specific expectation is baked in -- callers pass
what they expect on the command line.

Only text the model itself produced is treated as marker evidence: assistant
`type=text` content blocks, whole-line matches. Markers appearing in tool results,
tool inputs, file contents or prose are deliberately ignored.

Usage:
  check-transcript.py <transcript.jsonl> [checks...]
  check-transcript.py --latest N [--project-dir DIR] [checks...]

Checks:
  --expect SPEC              ordered: SPEC must match at least once, in this order
  --expect-count SPEC=N      SPEC must match exactly N times
  --expect-delegations N     shorthand for --expect-count delegate=N

SPEC syntax: <selector>[:<key>=<value>[,<key>=<value>]...]
  start                      the "BPTRACE start" line     keys: theme, wf
  step=<n>                   a "BPTRACE step=<n> out" line keys: actor
  delegate                   a subagent invocation         keys: to
  bash                       a shell invocation            keys: contains

Exit codes: 0 = PASS (or report-only), 1 = FAIL, 2 = usage/IO error.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARKER_START_RE = re.compile(r'^BPTRACE\s+start\s+theme="(?P<theme>[^"]*)"\s+wf=(?P<wf>\S+)$')
MARKER_STEP_RE = re.compile(r"^BPTRACE\s+step=(?P<step>\d+)\s+out\s+actor=(?P<actor>\S+)$")

AGENT_TOOL_NAMES = {"Agent", "Task"}
BASH_TOOL_NAMES = {"Bash"}

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
    kind: str  # marker_start | marker_step | tool_use
    origin: str  # "main" or "subagent:<name>"
    source: str  # file the event was read from
    line: int
    name: str = ""  # tool name, for kind=tool_use
    detail: dict = field(default_factory=dict)

    def describe(self):
        where = f"{self.origin} {Path(self.source).name}:{self.line}"
        if self.kind == "marker_start":
            body = 'BPTRACE start theme="{theme}" wf={wf}'.format(**self.detail)
        elif self.kind == "marker_step":
            body = "BPTRACE step={step} out actor={actor}".format(**self.detail)
        elif self.name in AGENT_TOOL_NAMES:
            body = f"delegate -> {self.detail.get('to') or '?'}"
        elif self.name in BASH_TOOL_NAMES:
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

    def _of(self, kind):
        return [e for e in self.events if e.kind == kind]

    @property
    def start_markers(self):
        return self._of("marker_start")

    @property
    def step_markers(self):
        return self._of("marker_step")

    @property
    def tool_uses(self):
        return self._of("tool_use")

    @property
    def delegations(self):
        return [e for e in self.events if e.kind == "tool_use" and e.name in AGENT_TOOL_NAMES]

    @property
    def bash_calls(self):
        return [e for e in self.events if e.kind == "tool_use" and e.name in BASH_TOOL_NAMES]


# --------------------------------------------------------------------------- #
# Claude Code parser
# --------------------------------------------------------------------------- #
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


def _marker_from_line(line):
    """Return (kind, detail) if the whole line is a BPTRACE marker, else None."""
    stripped = line.strip()
    if not stripped.startswith("BPTRACE"):
        return None
    match = MARKER_START_RE.match(stripped)
    if match:
        return "marker_start", match.groupdict()
    match = MARKER_STEP_RE.match(stripped)
    if match:
        return "marker_step", match.groupdict()
    return None


def _tool_detail(name, tool_input):
    if name in AGENT_TOOL_NAMES:
        return {"to": tool_input.get("subagent_type") or tool_input.get("agent_type") or ""}
    if name in BASH_TOOL_NAMES:
        return {"command": tool_input.get("command", "")}
    return {}


def _cc_subagent_index(main_path):
    """Map an Agent tool_use id to (transcript path, agent type) for one session."""
    index = {}
    sub_dir = main_path.with_suffix("") / "subagents"
    if not sub_dir.is_dir():
        return index
    for meta_path in sorted(sub_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        transcript = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        if meta.get("toolUseId") and transcript.exists():
            index[meta["toolUseId"]] = (transcript, meta.get("agentType") or "subagent")
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
                for text_line in (block.get("text") or "").splitlines():
                    found = _marker_from_line(text_line)
                    if found:
                        kind, detail = found
                        events.append(Event(0, kind, origin, str(path), line_no,
                                            detail=detail))
            elif block.get("type") == "tool_use":
                name = block.get("name") or ""
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                detail = _tool_detail(name, tool_input)
                detail["id"] = block.get("id") or ""
                events.append(Event(0, "tool_use", origin, str(path), line_no,
                                    name=name, detail=detail))
    return events


def parse_cc_run(path):
    """Parse a Claude Code transcript, splicing subagent transcripts in place."""
    path = Path(path)
    if not path.is_file():
        raise TranscriptError(f"no such transcript: {path}")
    run = Run(path=path, platform="cc")
    subagents = _cc_subagent_index(path)
    for event in _cc_scan(path, MAIN, run.warnings):
        run.events.append(event)
        if event.kind != "tool_use" or event.name not in AGENT_TOOL_NAMES:
            continue
        found = subagents.get(event.detail.get("id"))
        if not found:
            if subagents or event.detail.get("to"):
                run.warnings.append(
                    f"no subagent transcript for {event.name} id={event.detail.get('id')}")
            continue
        sub_path, agent_type = found
        run.events.extend(_cc_scan(sub_path, f"subagent:{agent_type}", run.warnings))
    for seq, event in enumerate(run.events, 1):
        event.seq = seq
    return run


PARSERS = {"cc": parse_cc_run}


# --------------------------------------------------------------------------- #
# Run discovery
# --------------------------------------------------------------------------- #
def cc_project_dir(repo_path):
    """~/.claude/projects/<abs path with '/' replaced by '-'>."""
    slug = str(Path(repo_path).resolve()).replace(os.sep, "-")
    return Path.home() / ".claude" / "projects" / slug


def find_latest_runs(project_dir, count, parser=parse_cc_run):
    """Return (runs, warnings): the newest `count` transcripts holding a start marker."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise TranscriptError(f"no such project directory: {project_dir}")
    candidates = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    runs, warnings = [], []
    for candidate in candidates:
        if len(runs) >= count:
            break
        run = parser(candidate)
        if run.start_markers:
            runs.append(run)
    if len(runs) < count:
        warnings.append(
            f"asked for the latest {count} run(s) but only {len(runs)} transcript(s) "
            f"in {project_dir} contain a BPTRACE start marker")
    return runs, warnings


# --------------------------------------------------------------------------- #
# Expectations (platform independent)
# --------------------------------------------------------------------------- #
@dataclass
class Expectation:
    spec: str
    selector: str
    step: str = ""
    keys: dict = field(default_factory=dict)
    count: int = None  # None = "at least one, in order"

    def matches(self, event):
        if self.selector == "start":
            if event.kind != "marker_start":
                return False
        elif self.selector == "step":
            if event.kind != "marker_step" or event.detail.get("step") != self.step:
                return False
        elif self.selector == "delegate":
            if event.kind != "tool_use" or event.name not in AGENT_TOOL_NAMES:
                return False
        elif self.selector == "bash":
            if event.kind != "tool_use" or event.name not in BASH_TOOL_NAMES:
                return False
        for key, want in self.keys.items():
            if key == "contains":
                if want not in event.detail.get("command", ""):
                    return False
            elif event.detail.get(key) != want:
                return False
        return True


_SELECTOR_KEYS = {
    "start": {"theme", "wf"},
    "step": {"actor"},
    "delegate": {"to"},
    "bash": {"contains"},
}


def _parse_spec(spec, count=None):
    head, _, tail = spec.partition(":")
    selector, _, step = head.partition("=")
    if selector not in _SELECTOR_KEYS:
        raise UsageError(
            f"unknown selector {selector!r} in {spec!r}; "
            f"use one of {', '.join(sorted(_SELECTOR_KEYS))}")
    if selector == "step" and not step.isdigit():
        raise UsageError(f"'step' needs a number, as in step=1 (got {spec!r})")
    keys = {}
    for part in filter(None, tail.split(",")):
        key, sep, value = part.partition("=")
        if not sep:
            raise UsageError(f"expected key=value, got {part!r} in {spec!r}")
        if key not in _SELECTOR_KEYS[selector]:
            raise UsageError(
                f"{selector!r} has no key {key!r}; "
                f"valid keys: {', '.join(sorted(_SELECTOR_KEYS[selector])) or '(none)'}")
        keys[key] = value
    return Expectation(spec=spec, selector=selector, step=step, keys=keys, count=count)


def parse_expectation(spec):
    """Parse an ordered expectation ('at least one match, in this order')."""
    return _parse_spec(spec)


def parse_count_expectation(spec):
    """Parse 'SPEC=N' into an exact-count expectation."""
    body, sep, number = spec.rpartition("=")
    if not sep or not number.lstrip("-").isdigit():
        raise UsageError(f"expected SPEC=<count>, got {spec!r}")
    return _parse_spec(body, count=int(number))


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
    same_kind = {"start": "marker_start", "step": "marker_step"}.get(expectation.selector)
    if same_kind:
        seen = [e.describe() for e in run.events if e.kind == same_kind]
    elif expectation.selector == "delegate":
        seen = [e.describe() for e in run.delegations]
    else:
        seen = [e.describe() for e in run.bash_calls]
    return "; observed: " + (" / ".join(seen) if seen else "nothing of that kind")


def evaluate(run, ordered, counted):
    """Judge a parsed run. Ordered expectations must match in sequence."""
    failures, anomalies = [], []

    starts = len(run.start_markers)
    if starts != 1:
        anomalies.append(
            f"{starts} start marker(s) found (exactly 1 expected for a well-formed run)")
    if run.warnings:
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

    checked = bool(ordered or counted)
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
        "delegations": [{"to": d.detail.get("to", ""), "origin": d.origin}
                        for d in run.delegations],
        "bash_calls": [{"command": b.detail.get("command", ""), "origin": b.origin}
                       for b in run.bash_calls],
        "events": [{"seq": e.seq, "kind": e.kind, "origin": e.origin, "name": e.name,
                    "line": e.line, "source": e.source, "detail": e.detail}
                   for e in run.events],
        "passed": result.passed,
        "failures": result.failures,
        "anomalies": result.anomalies,
    }


def print_report(result, out):
    run = result.run
    print(f"=== {run.path}", file=out)
    print(f"  start markers : {len(run.start_markers)}", file=out)
    for marker in run.start_markers:
        print(f"    - {marker.describe()}", file=out)
    print(f"  step markers  : {len(run.step_markers)}", file=out)
    for marker in run.step_markers:
        print(f"    - {marker.describe()}", file=out)
    print(f"  delegations   : {len(run.delegations)}", file=out)
    for call in run.delegations:
        print(f"    - {call.describe()}", file=out)
    print(f"  bash calls    : {len(run.bash_calls)}", file=out)
    for call in run.bash_calls:
        print(f"    - {call.describe()}", file=out)
    print(f"  event order   : {len(run.events)} event(s)", file=out)
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
                        help="check the newest N runs that contain a start marker")
    parser.add_argument("--project-dir", help="where to look for transcripts (default: derived "
                                              "from --repo / the current directory)")
    parser.add_argument("--repo", default=".", help="repository the transcripts belong to")
    parser.add_argument("--platform", default="cc", choices=sorted(PARSERS),
                        help="transcript format (default: cc)")
    parser.add_argument("--expect", "--expect-marker", action="append", default=[],
                        metavar="SPEC", dest="expect",
                        help="ordered expectation, e.g. 'step=1:actor=main'")
    parser.add_argument("--expect-count", action="append", default=[], metavar="SPEC=N",
                        help="exact-count expectation, e.g. 'bash:contains=filter-songs.sh=2'")
    parser.add_argument("--expect-delegations", type=int, metavar="N",
                        help="shorthand for --expect-count delegate=N")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    parse_run = PARSERS[args.platform]
    try:
        if (args.transcript is None) == (args.latest is None):
            raise UsageError("give either a transcript path or --latest N")
        ordered = [parse_expectation(s) for s in args.expect]
        counted = [parse_count_expectation(s) for s in args.expect_count]
        if args.expect_delegations is not None:
            counted.append(parse_count_expectation(f"delegate={args.expect_delegations}"))

        discovery_warnings = []
        if args.latest is not None:
            if args.latest < 1:
                raise UsageError("--latest needs a positive count")
            project_dir = Path(args.project_dir) if args.project_dir else cc_project_dir(args.repo)
            runs, discovery_warnings = find_latest_runs(project_dir, args.latest, parse_run)
        else:
            runs = [parse_run(args.transcript)]
    except (UsageError, TranscriptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results = [evaluate(run, ordered, counted) for run in runs]
    checked = bool(ordered or counted)
    short = checked and args.latest is not None and len(runs) < args.latest
    passed = all(r.passed for r in results) and not short

    if args.json:
        json.dump({"passed": passed, "checked": checked, "warnings": discovery_warnings,
                   "runs": [_run_dict(r) for r in results]}, sys.stdout, indent=2,
                  ensure_ascii=False)
        print()
    else:
        for result in results:
            print_report(result, sys.stdout)
        for warning in discovery_warnings:
            print(f"! {warning}")
        if checked:
            good = sum(1 for r in results if r.passed)
            print(f"{good}/{len(runs)} run(s) PASS -> {'PASS' if passed else 'FAIL'}")
        elif not runs:
            print("no runs found")
    if not checked:
        return 0
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
