"""Tests for check-transcript.py (GWT style: Given / When / Then)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("check-transcript.py")

_spec = importlib.util.spec_from_file_location("check_transcript", SCRIPT)
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)

# --- real data used by the "must match the actual transcript" tests -----------
REAL_DIR = Path.home() / ".claude/projects/-home-tie303177-work-lovaizu-bp"
REAL_NO_DELEGATION = REAL_DIR / "dc1addaf-3a1a-413c-b811-38002e93efd9.jsonl"
REAL_WITH_DELEGATION = REAL_DIR / "f71b8f11-f2d4-4a0d-8fa4-26da7d7d35e7.jsonl"
REAL_DEV_SESSION = REAL_DIR / "2f444960-95ea-435c-b10a-86c620c91ecb.jsonl"

needs_real = pytest.mark.skipif(
    not REAL_DIR.is_dir(), reason="local CC transcripts not available"
)


# --- fixture helpers ----------------------------------------------------------
def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def text(s):
    return {"type": "text", "text": s}


def tool_use(name, tid, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def user_result(tid, content):
    return {
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content}]},
    }


def write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


START = 'BPTRACE start theme="neon night" wf=techtest.md'
STEP1 = "BPTRACE step=1 out actor=main"
STEP2 = "BPTRACE step=2 out actor=techtest-echo"


def happy_run(tmp_path, session="s1"):
    """A parent transcript + a subagent transcript that together form a full run."""
    main = tmp_path / f"{session}.jsonl"
    write_jsonl(main, [
        {"type": "system", "content": "boot"},
        assistant(text(START)),
        assistant(text("Running step 1."),
                  tool_use("Bash", "t1", command='echo "step1: neon night"')),
        user_result("t1", "step1: neon night"),
        assistant(text(STEP1)),
        assistant(tool_use("Agent", "t2", subagent_type="techtest-echo",
                           prompt="input: neon night")),
        user_result("t2", '{"status": "ok"}'),
        assistant(text("Done.")),
    ])
    sub = tmp_path / session / "subagents" / "agent-aaa.jsonl"
    write_jsonl(sub, [
        assistant(tool_use("Bash", "s-t1", command='echo "step2: neon night"')),
        user_result("s-t1", "step2: neon night"),
        assistant(text('{"status": "ok", "echoed": "step2: neon night"}\n' + STEP2)),
    ])
    meta = sub.with_suffix(".meta.json")
    meta.write_text(json.dumps(
        {"agentType": "techtest-echo", "description": "echo", "toolUseId": "t2",
         "spawnDepth": 1}), encoding="utf-8")
    return main


# --- parsing: what counts as a marker ----------------------------------------
def test_start_marker_is_extracted_from_assistant_text(tmp_path):
    # Given a transcript whose assistant text block holds the start marker
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))])
    # When the run is parsed
    run = ct.parse_cc_run(p)
    # Then exactly one start marker with theme and wf is reported
    assert len(run.start_markers) == 1
    assert run.start_markers[0].detail == {"theme": "neon night", "wf": "techtest.md"}


def test_marker_inside_tool_result_is_ignored(tmp_path):
    # Given markers that only appear in a tool_result (e.g. a Read of the WF file)
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Read", "t1", file_path="techtest.md")),
        user_result("t1", f"  9\t{START}\n 10\t{STEP1}\n"),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then no markers are detected
    assert run.start_markers == [] and run.step_markers == []


def test_marker_inside_tool_use_input_is_ignored(tmp_path):
    # Given a Write tool_use whose input contains marker text
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Write", "t1", file_path="wf.md", content=f"{START}\n{STEP1}\n")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then no markers are detected (only the tool call itself)
    assert run.start_markers == [] and run.step_markers == []
    assert [e.name for e in run.tool_uses] == ["Write"]


def test_marker_mentioned_inside_prose_is_ignored(tmp_path):
    # Given assistant prose that merely quotes the marker format mid-line
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text('- rewrote 7.3: `BPTRACE start theme="..." wf=...` and step lines')),
        assistant(text("prefix " + STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then nothing matches, because a marker must occupy a whole line
    assert run.start_markers == [] and run.step_markers == []


def test_user_message_text_is_ignored(tmp_path):
    # Given the user pasting a marker line
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": START}]}},
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is not counted (only assistant output is evidence)
    assert run.start_markers == []


# --- parsing: events ----------------------------------------------------------
def test_step_markers_are_reported_in_order(tmp_path):
    # Given a full run
    p = happy_run(tmp_path)
    # When parsed
    run = ct.parse_cc_run(p)
    # Then both step markers appear in execution order with their actors
    assert [(m.detail["step"], m.detail["actor"]) for m in run.step_markers] == [
        ("1", "main"), ("2", "techtest-echo")]


def test_delegation_is_counted_with_target_agent(tmp_path):
    # Given a run that delegates once
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When delegations are inspected
    # Then one Agent call to techtest-echo is reported
    assert len(run.delegations) == 1
    assert run.delegations[0].detail["to"] == "techtest-echo"


def test_bash_calls_are_collected_with_commands(tmp_path):
    # Given a run with one parent Bash and one subagent Bash
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When bash calls are inspected
    cmds = [e.detail["command"] for e in run.bash_calls]
    # Then both commands are present
    assert cmds == ['echo "step1: neon night"', 'echo "step2: neon night"']


def test_subagent_transcript_is_spliced_at_the_delegation_point(tmp_path):
    # Given a run whose step=2 marker lives in the subagent transcript
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When the merged event stream is read
    kinds = [(e.kind, e.origin) for e in run.events]
    # Then subagent events sit right after the Agent call and are labelled
    i = kinds.index(("tool_use", "main"))
    assert ("marker_step", "subagent:techtest-echo") in kinds
    assert kinds[-1][1] == "subagent:techtest-echo"
    assert i >= 0


def test_merged_stream_is_numbered_in_display_order(tmp_path):
    # Given a run whose subagent events are spliced into the middle of the parent's
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When the sequence numbers are read
    # Then they increase monotonically across the splice
    assert [e.seq for e in run.events] == list(range(1, len(run.events) + 1))


def test_legacy_task_tool_name_counts_as_delegation(tmp_path):
    # Given an older transcript that names the delegation tool "Task"
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Task", "t1", subagent_type="techtest-echo")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is still recognised as a delegation
    assert len(run.delegations) == 1


# --- parsing: robustness ------------------------------------------------------
def test_broken_json_line_is_skipped_with_a_warning(tmp_path):
    # Given a transcript with one corrupt line
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(assistant(text(START))) + "\n{not json\n"
                 + json.dumps(assistant(text(STEP1))) + "\n", encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the good lines survive and the bad one is reported
    assert len(run.start_markers) == 1 and len(run.step_markers) == 1
    assert any("line 2" in w for w in run.warnings)


def test_empty_file_yields_an_empty_run(tmp_path):
    # Given an empty transcript
    p = tmp_path / "s.jsonl"
    p.write_text("", encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then there is nothing to report and no crash
    assert run.events == [] and run.start_markers == []


def test_missing_file_raises_transcript_error(tmp_path):
    # Given a path that does not exist
    # When parsed
    # Then a TranscriptError is raised
    with pytest.raises(ct.TranscriptError):
        ct.parse_cc_run(tmp_path / "nope.jsonl")


def test_bptrace_line_that_is_not_a_known_marker_is_ignored(tmp_path):
    # Given a BPTRACE-prefixed line that does not match either marker grammar
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("BPTRACE start wf=techtest.md")),      # theme missing
        assistant(text("BPTRACE step=1 actor=main")),          # "out" missing
        assistant(text("BPTRACE something else entirely")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then nothing is treated as a marker
    assert run.start_markers == [] and run.step_markers == []


def test_blank_lines_and_non_dict_blocks_are_tolerated(tmp_path):
    # Given a transcript padded with blank lines and a stray non-dict content block
    p = tmp_path / "s.jsonl"
    p.write_text("\n\n" + json.dumps(
        {"type": "assistant", "message": {"content": ["stray", text(START)]}}) + "\n\n",
        encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the valid block is still read and nothing is warned about
    assert len(run.start_markers) == 1 and run.warnings == []


def test_delegation_without_a_subagent_transcript_is_warned_about(tmp_path):
    # Given an Agent call whose subagent transcript was never written
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Agent", "t1", subagent_type="techtest-echo")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the delegation still counts but the missing trace is flagged
    assert len(run.delegations) == 1
    assert any("no subagent transcript" in w for w in run.warnings)


def test_corrupt_subagent_meta_is_skipped(tmp_path):
    # Given a subagents dir whose meta.json is unreadable
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text("{oops",
                                                                      encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the parent stream survives and the unlinked delegation is flagged
    assert len(run.delegations) == 1
    assert not any(e.origin.startswith("subagent:") for e in run.events)


def test_string_content_does_not_crash_the_parser(tmp_path):
    # Given an assistant event whose content is a bare string
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "assistant", "message": {"role": "assistant", "content": START}},
        assistant(text(STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then only the structured text block is used
    assert run.start_markers == [] and len(run.step_markers) == 1


# --- expectations (platform independent) --------------------------------------
def test_expectations_pass_for_a_correct_run(tmp_path):
    # Given the expected marker/delegation pattern for techtest
    run = ct.parse_cc_run(happy_run(tmp_path))
    expects = [ct.parse_expectation(s) for s in
               ["start:wf=techtest.md", "step=1:actor=main", "step=2:actor=techtest-echo"]]
    counts = [ct.parse_count_expectation("delegate=1")]
    # When evaluated
    result = ct.evaluate(run, expects, counts)
    # Then the run passes
    assert result.passed, result.failures


def test_expectation_failure_states_expected_and_actual(tmp_path):
    # Given a run whose step 2 was NOT delegated (actor=main)
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text(STEP1)),
        assistant(text("BPTRACE step=2 out actor=main")),
    ])
    run = ct.parse_cc_run(p)
    expects = [ct.parse_expectation("step=2:actor=techtest-echo")]
    # When evaluated
    result = ct.evaluate(run, expects, [ct.parse_count_expectation("delegate=1")])
    # Then it fails and the message names both the expectation and what was seen
    assert not result.passed
    joined = " | ".join(result.failures)
    assert "step=2:actor=techtest-echo" in joined
    assert "actor=main" in joined
    assert "expected 1" in joined and "got 0" in joined


def test_out_of_order_markers_fail_the_ordered_expectation(tmp_path):
    # Given step 2 emitted before step 1
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text("BPTRACE step=2 out actor=techtest-echo")),
        assistant(text(STEP1)),
    ])
    run = ct.parse_cc_run(p)
    expects = [ct.parse_expectation(s) for s in
               ["start", "step=1:actor=main", "step=2:actor=techtest-echo"]]
    # When evaluated
    result = ct.evaluate(run, expects, [])
    # Then the ordered match fails
    assert not result.passed
    assert "order" in " ".join(result.failures).lower()


def test_duplicate_start_marker_is_reported_as_an_anomaly(tmp_path):
    # Given two start markers in one transcript
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START)), assistant(text(START))])
    run = ct.parse_cc_run(p)
    # When evaluated with no expectations
    result = ct.evaluate(run, [], [])
    # Then the anomaly is surfaced even though nothing was expected
    assert any("start marker" in a for a in result.anomalies)


def test_zero_start_markers_is_reported_as_an_anomaly(tmp_path):
    # Given a transcript without any start marker
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(STEP1))])
    run = ct.parse_cc_run(p)
    # When evaluated
    result = ct.evaluate(run, [], [])
    # Then the anomaly is surfaced
    assert any("start marker" in a for a in result.anomalies)


def test_bash_count_expectation_can_match_a_command_substring(tmp_path):
    # Given a run with two bash calls, one of them an echo of step2
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When a substring-scoped count expectation is evaluated
    result = ct.evaluate(run, [], [ct.parse_count_expectation("bash:contains=step2=1")])
    # Then it passes
    assert result.passed, result.failures
    # and an unscoped bash count sees both calls
    assert ct.evaluate(run, [], [ct.parse_count_expectation("bash=2")]).passed


def test_bash_expectation_failure_lists_the_commands_actually_run(tmp_path):
    # Given a run whose bash command does not contain the wanted substring
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When a bash expectation misses
    result = ct.evaluate(run, [ct.parse_expectation("bash:contains=filter-songs.sh")], [])
    # Then the failure text lists what did run
    assert not result.passed
    assert 'echo "step1: neon night"' in " ".join(result.failures)


def test_delegation_expectation_failure_reports_no_delegation(tmp_path):
    # Given a run that never delegates
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))])
    run = ct.parse_cc_run(p)
    # When a delegation is expected
    result = ct.evaluate(run, [ct.parse_expectation("delegate:to=techtest-echo")], [])
    # Then the failure says nothing of that kind was seen
    assert "nothing of that kind" in " ".join(result.failures)


def test_parse_warnings_are_surfaced_as_anomalies(tmp_path):
    # Given a transcript with a corrupt line
    p = tmp_path / "s.jsonl"
    p.write_text("{bad\n" + json.dumps(assistant(text(START))) + "\n", encoding="utf-8")
    run = ct.parse_cc_run(p)
    # When evaluated
    result = ct.evaluate(run, [], [])
    # Then the parse warning is reported alongside the findings
    assert any("not valid JSON" in a for a in result.anomalies)


def test_start_expectation_skips_over_earlier_events(tmp_path):
    # Given a run that read the WF file before emitting the start marker
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Read", "t1", file_path="techtest.md")),
        assistant(text(START)),
    ])
    run = ct.parse_cc_run(p)
    # When the start marker is expected
    result = ct.evaluate(run, [ct.parse_expectation("start:wf=techtest.md")], [])
    # Then the preceding tool call does not confuse the match
    assert result.passed, result.failures


def test_delegation_with_no_subagent_dir_and_no_target_is_not_warned(tmp_path):
    # Given an Agent tool_use that recorded neither a target nor a transcript
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(tool_use("Agent", "t1"))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it still counts as a delegation, with nothing to warn about
    assert len(run.delegations) == 1 and run.warnings == []


def test_subagent_meta_without_a_tool_use_id_is_ignored(tmp_path):
    # Given a subagents dir whose meta.json has no toolUseId
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text(
        json.dumps({"agentType": "techtest-echo"}), encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then no subagent events are spliced in
    assert not any(e.origin.startswith("subagent:") for e in run.events)


def test_missing_step_failure_lists_the_steps_that_were_emitted(tmp_path):
    # Given a run that emitted steps 1 and 2 only
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When step 3 is expected
    result = ct.evaluate(run, [ct.parse_expectation("step=3:actor=main")], [])
    # Then the failure lists the step markers that did appear
    joined = " ".join(result.failures)
    assert "step=1" in joined and "step=2" in joined


def test_bash_expectation_on_a_run_without_bash_calls(tmp_path):
    # Given a run with markers but no shell calls
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))])
    run = ct.parse_cc_run(p)
    # When a bash call is expected
    result = ct.evaluate(run, [ct.parse_expectation("bash:contains=filter")], [])
    # Then it fails and says nothing of that kind ran
    assert not result.passed and "nothing of that kind" in " ".join(result.failures)


def test_other_tools_are_described_by_name(tmp_path):
    # Given a run that used a tool which is neither Agent nor Bash
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(tool_use("Read", "t1", file_path="a.md"))])
    run = ct.parse_cc_run(p)
    # When the event is described
    # Then the tool name is shown
    assert run.tool_uses[0].describe().endswith("tool: Read")


def test_unreadable_transcript_raises_transcript_error(tmp_path):
    # Given a transcript the process may not open
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))])
    p.chmod(0o000)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("cannot make a file unreadable here (running as root?)")
        # When parsed
        # Then a TranscriptError is raised rather than an OSError escaping
        with pytest.raises(ct.TranscriptError):
            ct.parse_cc_run(p)
    finally:
        p.chmod(0o644)


@pytest.mark.parametrize("spec", ["bogus:x=1", "step=x", "start:nope=1", "start:novalue"])
def test_malformed_expectations_are_rejected(spec):
    # Given a malformed expectation
    # When parsed
    # Then a usage error is raised
    with pytest.raises(ct.UsageError):
        ct.parse_expectation(spec)


def test_count_expectation_needs_a_number():
    # Given a count expectation without a number
    # When parsed
    # Then a usage error is raised
    with pytest.raises(ct.UsageError):
        ct.parse_count_expectation("delegate")


# --- run discovery ------------------------------------------------------------
def test_latest_returns_runs_newest_first(tmp_path):
    # Given three transcripts, only two of which contain a start marker
    a = write_jsonl(tmp_path / "a.jsonl", [assistant(text(START))])
    b = write_jsonl(tmp_path / "b.jsonl", [assistant(text(START))])
    write_jsonl(tmp_path / "c.jsonl", [assistant(text("no markers here"))])
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    # When the latest 5 are searched
    runs, warns = ct.find_latest_runs(tmp_path, 5)
    # Then only marked runs are returned, newest first, with a shortfall warning
    assert [r.path.name for r in runs] == ["b.jsonl", "a.jsonl"]
    assert any("5" in w and "2" in w for w in warns)


def test_latest_honours_the_requested_count(tmp_path):
    # Given two marked transcripts
    a = write_jsonl(tmp_path / "a.jsonl", [assistant(text(START))])
    b = write_jsonl(tmp_path / "b.jsonl", [assistant(text(START))])
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    # When only the latest 1 is requested
    runs, warns = ct.find_latest_runs(tmp_path, 1)
    # Then exactly one run comes back and nothing is flagged short
    assert [r.path.name for r in runs] == ["b.jsonl"] and warns == []


def test_latest_on_a_missing_project_dir_raises(tmp_path):
    # Given a project dir that does not exist
    # When runs are searched
    # Then a TranscriptError is raised
    with pytest.raises(ct.TranscriptError):
        ct.find_latest_runs(tmp_path / "nope", 1)


def test_project_dir_is_derived_from_an_absolute_path():
    # Given a repository path
    # When the CC project dir is derived
    d = ct.cc_project_dir("/home/u/work/bp")
    # Then the slug replaces separators with dashes
    assert d.name == "-home-u-work-bp"


# --- CLI ----------------------------------------------------------------------
def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def test_cli_reports_pass_with_exit_code_0(tmp_path):
    # Given a correct run
    p = happy_run(tmp_path)
    # When the CLI is invoked with the techtest expectations
    r = run_cli(str(p), "--expect", "start:wf=techtest.md",
                "--expect-marker", "step=1:actor=main",
                "--expect", "step=2:actor=techtest-echo",
                "--expect-delegations", "1")
    # Then it exits 0 and says PASS
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_cli_reports_fail_with_exit_code_1(tmp_path):
    # Given a run that never delegates
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START)), assistant(text(STEP1))])
    # When the CLI expects a delegation
    r = run_cli(str(p), "--expect-delegations", "1")
    # Then it exits 1 and prints FAIL
    assert r.returncode == 1
    assert "FAIL" in r.stdout


def test_cli_exits_2_on_a_missing_file(tmp_path):
    # Given a path that does not exist
    # When the CLI is invoked
    r = run_cli(str(tmp_path / "nope.jsonl"))
    # Then it exits 2 with an error on stderr
    assert r.returncode == 2 and "nope.jsonl" in r.stderr


def test_cli_emits_machine_readable_json(tmp_path):
    # Given a correct run
    p = happy_run(tmp_path)
    # When --json is used
    r = run_cli(str(p), "--json", "--expect", "step=2:actor=techtest-echo")
    # Then stdout parses and carries the extracted facts
    data = json.loads(r.stdout)
    assert data["passed"] is True
    assert data["runs"][0]["delegations"] == [{"to": "techtest-echo", "origin": "main"}]
    assert data["runs"][0]["step_markers"] == [
        {"step": "1", "actor": "main", "origin": "main"},
        {"step": "2", "actor": "techtest-echo", "origin": "subagent:techtest-echo"}]


def test_cli_latest_scans_the_given_project_dir(tmp_path):
    # Given a project dir holding one marked run
    happy_run(tmp_path, session="s1")
    # When --latest is used against it
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path),
                "--expect", "step=2:actor=techtest-echo")
    # Then the run is found and passes
    assert r.returncode == 0, r.stdout + r.stderr
    assert "s1.jsonl" in r.stdout


def test_cli_latest_fails_when_fewer_runs_than_requested(tmp_path):
    # Given only one marked run but three demanded
    happy_run(tmp_path, session="s1")
    # When --latest 3 is used with expectations
    r = run_cli("--latest", "3", "--project-dir", str(tmp_path), "--expect", "start")
    # Then the shortfall is a FAIL, not a silent pass
    assert r.returncode == 1
    assert "3" in r.stdout and "1" in r.stdout


def test_cli_without_expectations_only_reports(tmp_path):
    # Given a run and no expectations
    p = happy_run(tmp_path)
    # When the CLI is invoked bare
    r = run_cli(str(p))
    # Then it exits 0 and prints the extracted facts without a verdict
    assert r.returncode == 0
    assert "REPORT ONLY" in r.stdout


@pytest.mark.parametrize("args", [
    [],                                  # neither a path nor --latest
    ["x.jsonl", "--latest", "1"],        # both
    ["--latest", "0", "--project-dir", "."],
    ["x.jsonl", "--expect", "bogus"],
])
def test_cli_exits_2_on_bad_usage(args):
    # Given a command line that cannot be honoured
    # When the CLI runs
    r = run_cli(*args)
    # Then it exits 2 with an explanation
    assert r.returncode == 2 and r.stderr.startswith("error:")


def test_cli_reports_parse_warnings_and_marker_anomalies(tmp_path):
    # Given a transcript with a corrupt line and two start markers
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(assistant(text(START))) + "\n{bad\n"
                 + json.dumps(assistant(text(START))) + "\n", encoding="utf-8")
    # When the CLI runs
    r = run_cli(str(p))
    # Then both anomalies appear in the report
    assert "2 start marker(s)" in r.stdout and "not valid JSON" in r.stdout


def test_cli_latest_says_so_when_nothing_matches(tmp_path):
    # Given a project dir with no marked transcript
    write_jsonl(tmp_path / "a.jsonl", [assistant(text("nothing here"))])
    # When --latest runs without expectations
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path))
    # Then it says so and still exits 0 (report-only mode has no verdict)
    assert r.returncode == 0 and "no runs found" in r.stdout


# --- real transcripts ---------------------------------------------------------
@needs_real
@pytest.mark.skipif(not REAL_DEV_SESSION.exists(), reason="dev-session transcript missing")
def test_real_dev_session_with_marker_text_in_tool_results_detects_no_run():
    # Given the live development session, which only ever *read* files containing markers
    run = ct.parse_cc_run(REAL_DEV_SESSION)
    # When parsed
    # Then no markers are detected at all (a naive grep would find several)
    assert run.start_markers == [], [m.detail for m in run.start_markers]
    assert run.step_markers == [], [m.detail for m in run.step_markers]


@needs_real
@pytest.mark.skipif(not REAL_NO_DELEGATION.exists(), reason="transcript missing")
def test_real_run_without_delegation_matches_the_transcript():
    # Given the recorded /bp run dc1addaf (quick.md, no delegation)
    run = ct.parse_cc_run(REAL_NO_DELEGATION)
    # When parsed
    # Then the extraction matches what the file actually contains
    assert len(run.start_markers) == 1
    assert run.start_markers[0].detail == {
        "theme": "give me a quick setlist", "wf": "quick.md"}
    assert [(m.detail["step"], m.detail["actor"], m.origin) for m in run.step_markers] == [
        ("1", "main", "main"), ("2", "main", "main")]
    assert run.delegations == []
    assert len(run.bash_calls) == 2
    assert all("filter-songs.sh" in e.detail["command"] for e in run.bash_calls)


@needs_real
@pytest.mark.skipif(not REAL_WITH_DELEGATION.exists(), reason="transcript missing")
def test_real_run_with_delegation_merges_the_subagent_transcript():
    # Given the recorded run f71b8f11, which delegated to bp-song-finder
    run = ct.parse_cc_run(REAL_WITH_DELEGATION)
    # When parsed
    # Then the delegation and the subagent's own Bash calls are both visible
    assert [d.detail["to"] for d in run.delegations] == ["bp-song-finder"]
    sub_bash = [e for e in run.bash_calls if e.origin == "subagent:bp-song-finder"]
    assert len(sub_bash) == 7
    assert len(run.bash_calls) == 8
    # and the subagent events sit after the Agent call in the merged stream
    idx_agent = next(i for i, e in enumerate(run.events) if e.kind == "tool_use"
                     and e.name in ct.AGENT_TOOL_NAMES)
    idx_sub = next(i for i, e in enumerate(run.events) if e.origin.startswith("subagent:"))
    assert idx_sub == idx_agent + 1
