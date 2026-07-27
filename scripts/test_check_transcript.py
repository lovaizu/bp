"""Tests for check_transcript.py (GWT style: Given / When / Then)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_transcript as ct  # noqa: E402

SCRIPT = Path(__file__).with_name("check_transcript.py")
TESTDATA = Path(__file__).with_name("testdata")

# Recorded, anonymised excerpts of real Claude Code transcripts. These are
# committed, so the "does it match reality" tests run everywhere.
DATA_NO_DELEGATION = TESTDATA / "cc-no-delegation.jsonl"
DATA_DELEGATION = TESTDATA / "cc-delegation.jsonl"
DATA_DEV_SESSION = TESTDATA / "cc-dev-session.jsonl"

# Opt-in only: point this at a live CC project dir to re-check against whatever
# that machine happens to hold. Never a substitute for the fixtures above.
LIVE_DIR = os.environ.get("BP_LIVE_CC_PROJECT_DIR")
needs_live = pytest.mark.skipif(not LIVE_DIR, reason="BP_LIVE_CC_PROJECT_DIR not set")


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


def write_meta(sub_path, **fields):
    meta = sub_path.with_name(sub_path.name[: -len(".jsonl")] + ".meta.json")
    meta.write_text(json.dumps(fields), encoding="utf-8")
    return meta


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
    write_meta(sub, agentType="techtest-echo", description="echo", toolUseId="t2",
               spawnDepth=1)
    return main


def marked(tmp_path, name, *lines, mtime=None):
    """A one-line-per-assistant-text transcript, optionally back-dated."""
    p = write_jsonl(tmp_path / name, [assistant(text(line)) for line in lines])
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


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
    assert run.malformed_markers == []


def test_user_message_text_is_ignored(tmp_path):
    # Given the user pasting a marker line
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "user",
         "message": {"role": "user", "content": [{"type": "text", "text": START}]}},
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is not counted (only assistant output is evidence)
    assert run.start_markers == []


# --- V3: quoted markers are not evidence --------------------------------------
def test_marker_inside_a_fenced_code_block_is_not_evidence(tmp_path):
    # Given an assistant that quotes the marker lines inside a ``` fence
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("The workflow emits:\n\n```\n" + START + "\n" + STEP1 + "\n```\n\nOk.")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the quoted lines are not treated as the model having run anything
    assert run.start_markers == [] and run.step_markers == []
    assert run.malformed_markers == []


def test_tilde_fence_is_skipped_too(tmp_path):
    # Given the same quotation using a ~~~ fence with an info string
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("~~~text\n" + START + "\n~~~")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then nothing inside the fence counts
    assert run.start_markers == []


def test_marker_after_a_fenced_block_is_still_evidence(tmp_path):
    # Given a real step OUT: a fenced JSON dump followed by the marker line
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text('**Step 1 OUT**\n\n```json\n[{"id": "a"}]\n```\n\n' + STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the marker outside the fence still counts (block-level fence state)
    assert [m.detail["step"] for m in run.step_markers] == ["1"]


def test_an_unclosed_fence_swallows_the_rest_of_the_block_only(tmp_path):
    # Given a block whose fence is never closed, followed by a separate block
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("```\n" + START)),
        assistant(text(STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the fence state does not leak into the next block
    assert run.start_markers == [] and len(run.step_markers) == 1


# --- V4: markers that missed the format must be visible -----------------------
@pytest.mark.parametrize("line", [
    "**BPTRACE start theme=\"neon night\" wf=techtest.md**",
    "> BPTRACE step=1 out actor=main",
    "BPTRACE step=1 out actor=main.",
    'BPTRACE start theme="a "b" c" wf=techtest.md',
    "BPTRACE start wf=techtest.md",
    "BPTRACE step=1 actor=main",
    "BPTRACE something else entirely",
])
def test_a_bptrace_line_that_missed_the_format_is_recorded_as_malformed(tmp_path, line):
    # Given a line that tried to be a marker but is not one
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(line))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is neither silently dropped nor mistaken for a good marker
    assert run.start_markers == [] and run.step_markers == []
    assert [m.detail["text"] for m in run.malformed_markers] == [line.strip()]


def test_a_malformed_marker_is_reported_as_an_anomaly(tmp_path):
    # Given a run whose start marker was emitted in bold
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text("**" + START + "**"))])
    run = ct.parse_cc_run(p)
    # When evaluated
    result = ct.evaluate(run, [], [])
    # Then the near-miss is surfaced, not lost
    assert any("malformed" in a for a in result.anomalies)


def test_a_malformed_marker_shows_up_in_the_observed_list(tmp_path):
    # Given a run whose only step-ish line missed the format
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("BPTRACE step=1 out actor=main.")),
    ])
    run = ct.parse_cc_run(p)
    # When step 1 is expected
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then the failure text shows the near-miss instead of "nothing of that kind"
    joined = " ".join(result.failures)
    assert "actor=main." in joined and "nothing of that kind" not in joined


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
    # Then the subagent's events sit immediately after the delegate event
    delegate_at = kinds.index((ct.DELEGATE, "main"))
    assert kinds[delegate_at + 1] == (ct.BASH, "subagent:techtest-echo")
    assert kinds[delegate_at + 2] == (ct.MARKER_STEP, "subagent:techtest-echo")
    # and nothing from the subagent appears before the delegation
    assert not any(o.startswith("subagent:") for _, o in kinds[:delegate_at])


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


# --- V9: the event stream is platform neutral ---------------------------------
def test_event_kinds_are_normalised_by_the_parser(tmp_path):
    # Given a run using a delegation tool, a shell tool and another tool
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Agent", "t1", subagent_type="x")),
        assistant(tool_use("Bash", "t2", command="ls")),
        assistant(tool_use("Read", "t3", file_path="a.md")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the kinds carry the meaning and the CC tool names are display only
    assert [e.kind for e in run.events] == [ct.DELEGATE, ct.BASH, ct.TOOL]
    assert [e.name for e in run.events] == ["Agent", "Bash", "Read"]


def test_expectations_never_look_at_tool_names():
    # Given the judging layer
    source = Path(ct.__file__).read_text(encoding="utf-8")
    judging = source[source.index("class Expectation"):source.index("def _run_dict")]
    # When it is inspected
    # Then it mentions no platform tool-name table at all
    assert "AGENT_TOOL_NAMES" not in judging and "BASH_TOOL_NAMES" not in judging


def test_platform_registry_pairs_a_parser_with_a_project_dir_resolver():
    # Given the platform registry
    platform = ct.PLATFORMS["cc"]
    # When it is used
    # Then both halves of the platform binding are reachable from one entry
    assert platform.parse is ct.parse_cc_run
    assert platform.project_dir("/home/u/work/bp").name == "-home-u-work-bp"


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


def test_corrupt_subagent_meta_says_why_the_link_failed(tmp_path):
    # Given a subagents dir whose meta.json is unreadable
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text("{oops",
                                                                      encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the parent stream survives and the reason is stated, not swallowed
    assert len(run.delegations) == 1
    assert not any(e.origin.startswith("subagent:") for e in run.events)
    assert any("not valid JSON" in w for w in run.warnings)


def test_meta_json_holding_a_list_is_a_warning_not_a_crash(tmp_path):
    # Given a .meta.json that is valid JSON but not an object
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text('["x"]',
                                                                      encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the parser degrades to a warning instead of raising AttributeError
    assert any("JSON object" in w for w in run.warnings)
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


def test_unreadable_subagent_meta_says_so(tmp_path):
    # Given a .meta.json the process cannot open
    p = happy_run(tmp_path, session="s1")
    meta = tmp_path / "s1" / "subagents" / "agent-aaa.meta.json"
    meta.chmod(0o000)
    try:
        if os.access(meta, os.R_OK):
            pytest.skip("cannot make a file unreadable here (running as root?)")
        # When parsed
        run = ct.parse_cc_run(p)
        # Then the delegation is left unlinked with the reason recorded
        assert any("cannot be read" in w for w in run.warnings)
    finally:
        meta.chmod(0o644)


def test_subagent_meta_without_its_transcript_says_so(tmp_path):
    # Given a .meta.json whose sibling .jsonl was never written
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.jsonl").unlink()
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the gap is named rather than passed off as "never delegated"
    assert any("no transcript" in w for w in run.warnings)
    assert not any(e.origin.startswith("subagent:") for e in run.events)


def test_a_transcript_that_vanishes_mid_search_is_skipped(tmp_path):
    # Given a dangling entry in the project dir (a file removed while listing)
    marked(tmp_path, "good.jsonl", START)
    (tmp_path / "gone.jsonl").symlink_to(tmp_path / "never-existed.jsonl")
    # When the search runs
    found = ct.find_latest_runs(tmp_path, 1)
    # Then it does not raise, and the readable transcript is still found
    assert [r.path.name for r in found.runs] == ["good.jsonl"]


def test_subagent_meta_without_a_tool_use_id_is_ignored(tmp_path):
    # Given a subagents dir whose meta.json has no toolUseId
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text(
        json.dumps({"agentType": "techtest-echo"}), encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then no subagent events are spliced in, and the reason is recorded
    assert not any(e.origin.startswith("subagent:") for e in run.events)
    assert any("toolUseId" in w for w in run.warnings)


def test_delegation_with_no_subagent_dir_and_no_target_is_not_warned(tmp_path):
    # Given an Agent tool_use that recorded neither a target nor a transcript
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(tool_use("Agent", "t1"))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it still counts as a delegation, with nothing to warn about
    assert len(run.delegations) == 1 and run.warnings == []


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


# --- V8: nested delegation ----------------------------------------------------
def nested_run(tmp_path, session="n1"):
    """Parent -> child -> grandchild, all flat in one subagents/ dir (as CC writes it)."""
    main = write_jsonl(tmp_path / f"{session}.jsonl", [
        assistant(text(START)),
        assistant(tool_use("Agent", "t-child", subagent_type="child")),
    ])
    subs = tmp_path / session / "subagents"
    child = write_jsonl(subs / "agent-child.jsonl", [
        assistant(tool_use("Agent", "t-grand", subagent_type="grandchild")),
    ])
    write_meta(child, agentType="child", toolUseId="t-child", spawnDepth=1)
    grand = write_jsonl(subs / "agent-grand.jsonl", [
        assistant(text("BPTRACE step=9 out actor=grandchild")),
    ])
    write_meta(grand, agentType="grandchild", toolUseId="t-grand", spawnDepth=2,
               parentAgentId="agent-child")
    return main


def test_nested_subagent_events_are_spliced_recursively(tmp_path):
    # Given a run that delegated, and whose subagent delegated again
    run = ct.parse_cc_run(nested_run(tmp_path))
    # When the merged stream is read
    origins = [(e.kind, e.origin) for e in run.events]
    # Then the grandchild's marker is present rather than silently dropped
    assert (ct.MARKER_STEP, "subagent:grandchild") in origins
    assert [m.detail["actor"] for m in run.step_markers] == ["grandchild"]


def test_a_subagent_referenced_twice_is_not_expanded_twice(tmp_path):
    # Given two delegations that both resolve to the same subagent transcript
    main = write_jsonl(tmp_path / "c1.jsonl", [
        assistant(tool_use("Agent", "t-a", subagent_type="loop")),
        assistant(tool_use("Agent", "t-b", subagent_type="loop")),
    ])
    subs = tmp_path / "c1" / "subagents"
    sub = write_jsonl(subs / "agent-loop.jsonl", [assistant(text(STEP1))])
    write_meta(sub, agentType="loop", toolUseId="t-a", spawnDepth=1)
    dup = subs / "agent-loop2.meta.json"
    dup.write_text(json.dumps({"agentType": "loop", "toolUseId": "t-b"}), encoding="utf-8")
    (subs / "agent-loop2.jsonl").symlink_to(sub)
    # When parsed
    run = ct.parse_cc_run(main)
    # Then it terminates, expands once, and says why the second was not expanded
    assert len(run.step_markers) == 1
    assert any("more than once" in w for w in run.warnings)


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


# --- V5: broken evidence must not pass ----------------------------------------
def test_an_anomaly_fails_a_checked_run(tmp_path):
    # Given a run that meets every expectation but emitted two start markers
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text(START)), assistant(text(STEP1)),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then the damaged evidence is a failure, not a footnote
    assert not result.passed
    assert any("anomal" in f for f in result.failures)


def test_allow_anomalies_restores_the_old_tolerance(tmp_path):
    # Given the same run
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text(START)), assistant(text(STEP1)),
    ])
    run = ct.parse_cc_run(p)
    # When anomalies are explicitly allowed
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [],
                         allow_anomalies=True)
    # Then it passes, with the anomaly still reported
    assert result.passed and result.anomalies


def test_anomalies_alone_do_not_fail_a_report_only_run(tmp_path):
    # Given a run with anomalies but no expectations
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START)), assistant(text(START))])
    run = ct.parse_cc_run(p)
    # When evaluated
    result = ct.evaluate(run, [], [])
    # Then there is no verdict to fail
    assert not result.checked and result.failures == []


def test_a_corrupt_line_fails_a_checked_run(tmp_path):
    # Given a transcript with a corrupt line that otherwise meets expectations
    p = tmp_path / "s.jsonl"
    p.write_text("{bad\n" + json.dumps(assistant(text(START))) + "\n", encoding="utf-8")
    run = ct.parse_cc_run(p)
    # When evaluated with an expectation
    result = ct.evaluate(run, [ct.parse_expectation("start")], [])
    # Then the unreadable evidence blocks the PASS
    assert not result.passed
    assert any("not valid JSON" in a for a in result.anomalies)


# --- V2: origin is expressible ------------------------------------------------
def test_origin_can_be_expected_on_a_step_marker(tmp_path):
    # Given a run whose step 2 marker was emitted by the subagent itself
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When the origin is pinned to the subagent
    result = ct.evaluate(run, [ct.parse_expectation(
        "step=2:actor=techtest-echo,origin=subagent:techtest-echo")], [])
    # Then it passes
    assert result.passed, result.failures


def test_origin_catches_a_marker_the_parent_merely_transcribed(tmp_path):
    # Given a parent that printed the subagent's marker line itself
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text(STEP1)),
        assistant(tool_use("Agent", "t2", subagent_type="techtest-echo")),
        assistant(text(STEP2)),
    ])
    sub = tmp_path / "s" / "subagents" / "agent-a.jsonl"
    write_jsonl(sub, [assistant(text("done"))])
    write_meta(sub, agentType="techtest-echo", toolUseId="t2", spawnDepth=1)
    run = ct.parse_cc_run(p)
    # When the marker is required to come from the subagent
    result = ct.evaluate(run, [ct.parse_expectation(
        "step=2:actor=techtest-echo,origin=subagent:techtest-echo")], [])
    # Then the parent's copy does not satisfy it
    assert not result.passed
    assert "origin" in " ".join(result.failures) or "main" in " ".join(result.failures)


def test_origin_is_matched_exactly_not_by_prefix(tmp_path):
    # Given a marker emitted by subagent "techtest-echo"
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When a shorter origin prefix is expected
    result = ct.evaluate(run, [ct.parse_expectation("step=2:origin=subagent")], [])
    # Then it does not match
    assert not result.passed


@pytest.mark.parametrize("spec", [
    "start:origin=main", "step=1:origin=main", "delegate:origin=main", "bash:origin=main"])
def test_origin_is_a_key_on_every_selector(tmp_path, spec):
    # Given a run where everything happened in the parent
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text(STEP1)),
        assistant(tool_use("Agent", "t1", subagent_type="x")),
        assistant(tool_use("Bash", "t2", command="ls")),
    ])
    run = ct.parse_cc_run(p)
    # When origin=main is expected on each selector
    result = ct.evaluate(run, [ct.parse_expectation(spec)], [], allow_anomalies=True)
    # Then each one matches
    assert result.passed, result.failures


def test_origin_narrows_a_count_expectation(tmp_path):
    # Given a run whose bash calls are split between parent and subagent
    run = ct.parse_cc_run(happy_run(tmp_path))
    # When counts are scoped by origin
    # Then each side is counted separately
    assert ct.evaluate(run, [], [ct.parse_count_expectation("bash:origin=main=1")]).passed
    assert ct.evaluate(
        run, [], [ct.parse_count_expectation("bash:origin=subagent:techtest-echo=1")]).passed


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
    result = ct.evaluate(run, [ct.parse_expectation("bash:contains=deploy.sh")], [])
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


# --- V13: expectation syntax is validated -------------------------------------
@pytest.mark.parametrize("spec", ["bogus:x=1", "step=x", "start:nope=1", "start:novalue",
                                  "step=001", "start:theme=a,theme=b", "delegate=1"])
def test_malformed_expectations_are_rejected(spec):
    # Given a malformed expectation
    # When parsed
    # Then a usage error is raised
    with pytest.raises(ct.UsageError):
        ct.parse_expectation(spec)


@pytest.mark.parametrize("spec", ["delegate", "delegate=-1", "delegate=²", "delegate=x"])
def test_malformed_count_expectations_are_rejected(spec):
    # Given a count expectation that is not a plain non-negative decimal
    # When parsed
    # Then a usage error is raised rather than a silent no-op or a traceback
    with pytest.raises(ct.UsageError):
        ct.parse_count_expectation(spec)


def test_count_expectation_splits_at_the_last_equals():
    # Given a spec whose value itself contains '='
    e = ct.parse_count_expectation("bash:contains=a=b=2")
    # When parsed
    # Then everything left of the final '=' is the spec (documented rule)
    assert e.count == 2 and e.keys["contains"] == "a=b"


def test_zero_is_a_valid_count():
    # Given an expectation of zero delegations
    e = ct.parse_count_expectation("delegate=0")
    # When parsed
    # Then it is accepted
    assert e.count == 0


# --- run discovery ------------------------------------------------------------
def test_latest_takes_the_newest_runs_regardless_of_their_verdict(tmp_path):
    # Given three transcripts, the newest of which never emitted a start marker
    marked(tmp_path, "old.jsonl", START, mtime=1000)
    marked(tmp_path, "mid.jsonl", START, mtime=2000)
    marked(tmp_path, "new.jsonl", "I forgot to print the marker.", mtime=3000)
    # When the latest 2 are searched
    found = ct.find_latest_runs(tmp_path, 2)
    # Then the marker-less run is still in the sample -- it is the one to catch
    assert [r.path.name for r in found.runs] == ["new.jsonl", "mid.jsonl"]
    assert found.shortfall == 0


def test_latest_selects_by_exact_theme(tmp_path):
    # Given runs from two different measurements
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=1000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=2000)
    marked(tmp_path, "c.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=3000)
    # When the theme scopes the search
    found = ct.find_latest_runs(tmp_path, 5, theme="run-42")
    # Then only that measurement's runs come back, newest first
    assert [r.path.name for r in found.runs] == ["c.jsonl", "a.jsonl"]
    assert found.shortfall == 3


def test_theme_selection_is_not_a_substring_match(tmp_path):
    # Given a run whose theme merely contains the requested token
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="run-420" wf=techtest.md')
    # When the shorter theme is searched
    found = ct.find_latest_runs(tmp_path, 1, theme="run-42")
    # Then it does not match
    assert found.runs == []


def test_since_excludes_transcripts_written_before_the_session(tmp_path):
    # Given an old run and a new one
    marked(tmp_path, "old.jsonl", START, mtime=1000)
    marked(tmp_path, "new.jsonl", START, mtime=time.time())
    # When the search is limited to the last hour
    found = ct.find_latest_runs(tmp_path, 5, since=time.time() - 3600)
    # Then only the recent transcript is considered
    assert [r.path.name for r in found.runs] == ["new.jsonl"]


def test_since_accepts_an_iso8601_string():
    # Given an ISO 8601 timestamp
    # When it is converted
    ts = ct.parse_since("2026-07-27T00:00:00")
    # Then a comparable epoch value comes back
    assert isinstance(ts, float) and ts > 0
    # and nonsense is rejected
    with pytest.raises(ct.UsageError):
        ct.parse_since("yesterday")


def test_latest_honours_the_requested_count(tmp_path):
    # Given two transcripts
    marked(tmp_path, "a.jsonl", START, mtime=1000)
    marked(tmp_path, "b.jsonl", START, mtime=2000)
    # When only the latest 1 is requested
    found = ct.find_latest_runs(tmp_path, 1)
    # Then exactly one run comes back and nothing is flagged short
    assert [r.path.name for r in found.runs] == ["b.jsonl"] and found.warnings == []


def test_an_unreadable_transcript_does_not_abort_the_search(tmp_path):
    # Given one transcript the process cannot open and one it can
    bad = marked(tmp_path, "bad.jsonl", START, mtime=3000)
    marked(tmp_path, "good.jsonl", START, mtime=2000)
    bad.chmod(0o000)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("cannot make a file unreadable here (running as root?)")
        # When the search runs
        found = ct.find_latest_runs(tmp_path, 1)
        # Then the readable one is still found and the skip is reported
        assert [r.path.name for r in found.runs] == ["good.jsonl"]
        assert any("bad.jsonl" in w for w in found.warnings)
    finally:
        bad.chmod(0o644)


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


def test_project_dir_slug_collapses_dots_too():
    # Given a worktree path containing a dot directory (as CC itself writes it)
    d = ct.cc_project_dir("/home/u/work/bp/.claude-worktrees/wt")
    # Then every non-alphanumeric character becomes a dash
    assert d.name == "-home-u-work-bp--claude-worktrees-wt"


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


def test_cli_exits_2_when_an_unexpected_exception_escapes(tmp_path):
    # Given a .meta.json that used to raise AttributeError deep in the parser
    p = happy_run(tmp_path, session="s1")
    (tmp_path / "s1" / "subagents" / "agent-aaa.meta.json").write_text('["x"]',
                                                                      encoding="utf-8")
    # When the CLI runs with --json
    r = run_cli(str(p), "--json", "--expect", "start")
    # Then it must not exit 2; a crash is reserved for genuine internal errors
    assert r.returncode in (0, 1), r.stderr
    assert json.loads(r.stdout)["runs"][0]["anomalies"]


def test_cli_maps_an_internal_error_to_exit_2(tmp_path, monkeypatch):
    # Given a checker whose parser blows up in an unforeseen way
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))])
    boom = "import check_transcript as ct, sys\n" \
           "ct.PLATFORMS['cc'].parse.__globals__['_cc_scan'] = lambda *a, **k: 1 / 0\n" \
           f"sys.exit(ct.main([{str(p)!r}, '--expect', 'start']))\n"
    # When it runs
    r = subprocess.run([sys.executable, "-c", boom], capture_output=True, text=True,
                       cwd=str(SCRIPT.parent))
    # Then the CLI reports an internal error and exits 2, not 1 (which means FAIL)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "internal error" in r.stderr


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


def test_cli_latest_fails_when_a_recent_run_forgot_its_marker(tmp_path):
    # Given three runs where the newest never emitted a start marker (the V1 case)
    happy_run(tmp_path, session="s1")
    happy_run(tmp_path, session="s2")
    marked(tmp_path, "s3.jsonl", "I skipped the markers this time.")
    for name, when in (("s1.jsonl", 1000), ("s2.jsonl", 2000), ("s3.jsonl", 3000)):
        os.utime(tmp_path / name, (when, when))
    # When the latest 3 are checked
    r = run_cli("--latest", "3", "--project-dir", str(tmp_path), "--expect", "start")
    # Then the bad run cannot hide behind an older good one
    assert r.returncode == 1, r.stdout
    assert "s3.jsonl" in r.stdout and "2/3" in r.stdout


def test_cli_theme_scopes_the_measurement(tmp_path):
    # Given one run from this measurement and one from an earlier one
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=3000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=2000)
    # When the theme is given
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42",
                "--expect", "start:theme=run-42")
    # Then only the matching run is checked
    assert r.returncode == 0, r.stdout + r.stderr
    assert "b.jsonl" in r.stdout and "a.jsonl" not in r.stdout


def test_cli_latest_fails_when_fewer_runs_than_requested(tmp_path):
    # Given only one run but three demanded
    happy_run(tmp_path, session="s1")
    # When --latest 3 is used with expectations
    r = run_cli("--latest", "3", "--project-dir", str(tmp_path), "--expect", "start")
    # Then the shortfall is a FAIL and the summary is self-consistent
    assert r.returncode == 1
    assert "1/3 run(s) PASS (2 missing) -> FAIL" in r.stdout


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
    ["x.jsonl", "--project-dir", "."],   # discovery options with an explicit path
    ["x.jsonl", "--repo", "."],
    ["x.jsonl", "--theme", "t"],
    ["--latest", "1", "--project-dir", ".", "--since", "not-a-date"],
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


def test_cli_latest_says_so_when_no_run_matches_the_theme(tmp_path):
    # Given a project dir with no transcript for this measurement
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md')
    # When --latest runs without expectations
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42")
    # Then it says so and still exits 0 (report-only mode has no verdict)
    assert r.returncode == 0 and "no runs found" in r.stdout


# --- V15: output volume -------------------------------------------------------
def test_the_event_dump_is_off_by_default(tmp_path):
    # Given a passing run
    p = happy_run(tmp_path)
    # When the CLI reports it without --verbose
    r = run_cli(str(p), "--expect", "start")
    # Then only the counts are printed, not every event
    assert "event order   : " in r.stdout
    assert "  1. [" not in r.stdout


def test_verbose_prints_every_event(tmp_path):
    # Given the same run
    p = happy_run(tmp_path)
    # When --verbose is given
    r = run_cli(str(p), "--verbose", "--expect", "start")
    # Then the full ordered stream is printed
    assert "  1. [" in r.stdout


def test_a_failing_run_prints_its_events_without_verbose(tmp_path):
    # Given a run that fails its expectation
    p = happy_run(tmp_path)
    # When it is checked
    r = run_cli(str(p), "--expect", "step=9:actor=nobody")
    # Then the evidence is dumped so the failure can be read
    assert r.returncode == 1 and "  1. [" in r.stdout


def test_the_help_text_carries_no_project_specific_example():
    # Given the CLI help
    r = run_cli("--help")
    # When it is read
    # Then no BLACKPINK-specific resource leaks into the generic option help
    assert "filter-songs.sh" not in r.stdout


# --- recorded real transcripts (committed fixtures) ---------------------------
def test_recorded_run_without_delegation_matches_the_transcript():
    # Given the recorded run that did every step itself
    run = ct.parse_cc_run(DATA_NO_DELEGATION)
    # When parsed
    # Then the extraction matches what the file actually contains
    assert run.warnings == []
    assert [m.detail for m in run.start_markers] == [
        {"theme": "give me a quick setlist", "wf": "quick.md"}]
    assert [(m.detail["step"], m.detail["actor"], m.origin) for m in run.step_markers] == [
        ("1", "main", "main"), ("2", "main", "main")]
    assert run.delegations == []
    assert len(run.bash_calls) == 2
    assert all("filter-songs.sh" in e.detail["command"] for e in run.bash_calls)
    # and the marker printed after a fenced JSON dump was still picked up
    assert run.malformed_markers == []


def test_recorded_run_without_delegation_passes_the_stage_a_expectations():
    # Given the same recorded run
    run = ct.parse_cc_run(DATA_NO_DELEGATION)
    expects = [ct.parse_expectation(s) for s in [
        "start:theme=give me a quick setlist,wf=quick.md",
        "step=1:actor=main,origin=main", "step=2:actor=main,origin=main"]]
    # When the stage-A invariant is checked
    result = ct.evaluate(run, expects, [ct.parse_count_expectation("delegate=0")])
    # Then it passes
    assert result.passed, result.failures


def test_recorded_run_with_delegation_merges_the_subagent_transcript():
    # Given the recorded run that delegated once
    run = ct.parse_cc_run(DATA_DELEGATION)
    # When parsed
    # Then the delegation and the subagent's own Bash calls are both visible
    assert run.warnings == []
    assert [d.detail["to"] for d in run.delegations] == ["sample-song-finder"]
    sub_bash = [e for e in run.bash_calls if e.origin == "subagent:sample-song-finder"]
    assert len(sub_bash) == 3 and len(run.bash_calls) == 4
    # and the subagent events sit right after the delegate event
    idx_agent = next(i for i, e in enumerate(run.events) if e.kind == ct.DELEGATE)
    idx_sub = next(i for i, e in enumerate(run.events) if e.origin.startswith("subagent:"))
    assert idx_sub == idx_agent + 1


def test_recorded_delegating_run_emitted_both_step_markers_from_the_parent():
    # Given the recorded delegating run
    run = ct.parse_cc_run(DATA_DELEGATION)
    # When the origins are inspected
    # Then step 1 was delegated but the marker came from the parent, not the subagent
    assert [(m.detail["step"], m.origin) for m in run.step_markers] == [
        ("1", "main"), ("2", "main")]
    result = ct.evaluate(run, [ct.parse_expectation(
        "step=1:origin=subagent:sample-song-finder")], [])
    assert not result.passed


def test_recorded_dev_session_is_not_mistaken_for_a_run():
    # Given a recorded development session: marker text in tool results, tool inputs,
    # prose and fenced quotations, but never as the model's own output
    run = ct.parse_cc_run(DATA_DEV_SESSION)
    # When parsed
    # Then nothing at all is treated as evidence
    assert run.start_markers == [] and run.step_markers == []
    assert run.malformed_markers == []


def test_recorded_dev_session_fails_the_stage_b_expectations():
    # Given the same development session
    run = ct.parse_cc_run(DATA_DEV_SESSION)
    expects = [ct.parse_expectation(s) for s in
               ["start:wf=techtest.md", "step=1:actor=main,origin=main"]]
    # When the techtest invariant is checked against it
    result = ct.evaluate(run, expects, [])
    # Then it fails -- a discussion session can never stand in for a measurement
    assert not result.passed


def test_recorded_fixtures_carry_no_personal_data():
    # Given the committed fixtures
    blob = "\n".join(p.read_text(encoding="utf-8") for p in sorted(TESTDATA.rglob("*"))
                     if p.is_file())
    # When they are scanned
    # Then no home directory, user name or e-mail address survived the anonymisation
    for leak in ["/home/", "tie303177", "@", "toolu_bdrk"]:
        assert leak not in blob, leak


# --- opt-in: live machine transcripts -----------------------------------------
@needs_live
def test_live_project_dir_can_be_scanned_without_crashing():
    # Given a live CC project directory on this machine
    found = ct.find_latest_runs(Path(LIVE_DIR), 3)
    # When the newest runs are parsed
    # Then every run comes back with an ordered, numbered event stream
    for run in found.runs:
        assert [e.seq for e in run.events] == list(range(1, len(run.events) + 1))
