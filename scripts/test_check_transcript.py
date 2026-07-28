"""Tests for check_transcript.py (GWT style: Given / When / Then)."""

import json
import os
import subprocess
import sys
import time
import typing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_transcript as ct  # noqa: E402

SCRIPT = Path(__file__).with_name("check_transcript.py")
TESTDATA = Path(__file__).with_name("testdata")

# Hand-built minimal fixtures, written to the shape a real Claude Code
# transcript has: the same outer envelope (uuid / parentUuid / timestamp /
# sessionId / isSidechain / cwd / version / gitBranch), the same message and
# content-block structure, the same subagents/<name>.jsonl + .meta.json layout.
# They are *not* recordings -- the events in them were composed to pin one
# behaviour each -- so they are evidence that the parser handles a given shape,
# never evidence about what the model really did. Checks against genuine
# transcripts are a separate, manual step (and the opt-in tests below).
DATA_NO_DELEGATION = TESTDATA / "cc-no-delegation.jsonl"
DATA_DELEGATION = TESTDATA / "cc-delegation.jsonl"
DATA_UNTYPED_DELEGATION = TESTDATA / "cc-delegation-untyped.jsonl"
DATA_DEV_SESSION = TESTDATA / "cc-dev-session.jsonl"
DATA_FILES = [DATA_NO_DELEGATION, DATA_DELEGATION, DATA_UNTYPED_DELEGATION,
              DATA_DEV_SESSION]

# GHC fixtures are hand-built the same way: composed to pin one GHC-schema
# behaviour each, per docs/cross-platform-agent-design.md §3.1. There is no
# real GHC transcript anywhere -- nobody has run GHC for this project yet --
# so these are not recordings and never stand in for one.
DATA_GHC_NO_DELEGATION = TESTDATA / "ghc-no-delegation.jsonl"
DATA_GHC_DELEGATION = TESTDATA / "ghc-delegation.jsonl"

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


def happy_meta(tmp_path, session="s1"):
    """The .meta.json that links happy_run()'s delegation to its subagent transcript."""
    return tmp_path / session / "subagents" / "agent-aaa.meta.json"


def happy_sub(tmp_path, session="s1"):
    """The subagent transcript happy_run() delegates to."""
    return tmp_path / session / "subagents" / "agent-aaa.jsonl"


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
    "BPTRACE step=１ out actor=main",
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


def test_a_delegation_with_no_recorded_target_takes_it_from_the_meta(tmp_path):
    # Given an Agent call CC wrote without a subagent_type, whose spawned
    # transcript's .meta.json does name the agent type
    main = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Agent", "t1", prompt="input: neon night")),
    ])
    sub = tmp_path / "s" / "subagents" / "agent-a.jsonl"
    write_jsonl(sub, [assistant(text(STEP2))])
    write_meta(sub, agentType="techtest-echo", toolUseId="t1", spawnDepth=1)
    # When parsed
    run = ct.parse_cc_run(main)
    # Then `to` and `origin` agree, instead of one of them being unmatchable
    assert run.delegations[0].detail["to"] == "techtest-echo"
    assert run.delegations[0].describe().endswith("delegate -> techtest-echo")
    assert ct.evaluate(run, [ct.parse_expectation("delegate:to=techtest-echo"),
                             ct.parse_expectation(
                                 "step=2:origin=subagent:techtest-echo")], []).passed


def test_a_recorded_subagent_type_is_never_overwritten_by_the_meta(tmp_path):
    # Given a transcript whose Agent call and .meta.json disagree
    main = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Agent", "t1", subagent_type="as-called")),
    ])
    sub = tmp_path / "s" / "subagents" / "agent-a.jsonl"
    write_jsonl(sub, [assistant(text(STEP2))])
    write_meta(sub, agentType="as-spawned", toolUseId="t1", spawnDepth=1)
    # When parsed
    run = ct.parse_cc_run(main)
    # Then the fallback stays a fallback: what the call itself said wins
    assert run.delegations[0].detail["to"] == "as-called"


def test_a_delegation_with_no_target_and_no_agent_type_stays_unnamed(tmp_path):
    # Given neither a subagent_type nor an agentType to fall back on
    main = write_jsonl(tmp_path / "s.jsonl", [assistant(tool_use("Agent", "t1"))])
    sub = tmp_path / "s" / "subagents" / "agent-a.jsonl"
    write_jsonl(sub, [assistant(text(STEP2))])
    write_meta(sub, toolUseId="t1", spawnDepth=1)
    # When parsed
    run = ct.parse_cc_run(main)
    # Then the placeholder origin is not smuggled into `to` as if it were real
    assert run.delegations[0].detail["to"] == ""
    assert run.delegations[0].describe().endswith("delegate -> ?")


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
    # Given a synthetic event stream whose platform tool names are pure nonsense
    run = ct.Run(path=Path("synthetic.jsonl"), platform="made-up", events=[
        ct.Event(1, ct.DELEGATE, ct.MAIN, "synthetic.jsonl", 1,
                 name="Kwyjibo", detail={"to": "echo-bot"}),
        ct.Event(2, ct.BASH, ct.MAIN, "synthetic.jsonl", 2,
                 name="Zzyzx", detail={"command": "make build"}),
    ])
    # When expectations phrased purely in normalised kinds are evaluated
    result = ct.evaluate(run, [ct.parse_expectation("delegate:to=echo-bot"),
                               ct.parse_expectation("bash:contains=make")],
                         [ct.parse_count_expectation("delegate=1")],
                         allow_anomalies=True)
    # Then they match, so the judging layer never consulted the tool names
    assert result.passed, result.failures


def test_platform_registry_pairs_a_parser_with_a_project_dir_resolver():
    # Given the platform registry
    platform = ct.PLATFORMS["cc"]
    # When it is used
    # Then both halves of the platform binding are reachable from one entry
    assert platform.parse is ct.parse_cc_run
    assert platform.project_dir("/home/u/work/bp").name == "-home-u-work-bp"
    assert callable(platform.transcripts)


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
    happy_meta(tmp_path).write_text("{oops", encoding="utf-8")
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the parent stream survives and the reason is stated, not swallowed
    assert len(run.delegations) == 1
    assert not any(e.origin.startswith("subagent:") for e in run.events)
    assert any("not valid JSON" in w for w in run.warnings)


def test_meta_json_holding_a_list_is_a_warning_not_a_crash(tmp_path):
    # Given a .meta.json that is valid JSON but not an object
    p = happy_run(tmp_path, session="s1")
    happy_meta(tmp_path).write_text('["x"]', encoding="utf-8")
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
    meta = happy_meta(tmp_path)
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
    happy_sub(tmp_path).unlink()
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the gap is named rather than passed off as "never delegated"
    assert any("no transcript" in w for w in run.warnings)
    assert not any(e.origin.startswith("subagent:") for e in run.events)


def test_a_transcript_that_vanishes_mid_search_is_named_not_swallowed(tmp_path):
    # Given a dangling entry in the project dir (a file removed while listing)
    marked(tmp_path, "good.jsonl", START)
    (tmp_path / "gone.jsonl").symlink_to(tmp_path / "never-existed.jsonl")
    # When the search runs
    found = ct.find_runs(tmp_path, 1, ct.parse_cc_run, ct.cc_transcripts)
    # Then it does not raise, the readable transcript is still found, and the
    # candidate that could not even be stat()ed is reported as an evidence gap
    assert [r.path.name for r in found.runs] == ["good.jsonl"]
    assert [e.path.name for e in found.excluded] == ["gone.jsonl"]
    assert any("gone.jsonl" in g for g in found.gaps)


def test_subagent_meta_without_a_tool_use_id_is_ignored(tmp_path):
    # Given a subagents dir whose meta.json has no toolUseId
    p = happy_run(tmp_path, session="s1")
    happy_meta(tmp_path).write_text(
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


def test_duplicate_start_marker_is_caught_by_the_count_the_caller_asked_for(tmp_path):
    # Given two start markers in one transcript
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START)), assistant(text(START))])
    run = ct.parse_cc_run(p)
    # When the measurement says how many it wanted
    result = ct.evaluate(run, [], [ct.parse_count_expectation("start=1")])
    # Then it fails on that expectation -- not on a rule baked into evaluate()
    assert not result.passed
    assert "expected 1 x start, got 2" in " ".join(result.failures)


def test_zero_start_markers_is_caught_by_the_count_the_caller_asked_for(tmp_path):
    # Given a transcript without any start marker
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(STEP1))])
    run = ct.parse_cc_run(p)
    # When the measurement demands exactly one
    result = ct.evaluate(run, [], [ct.parse_count_expectation("start=1")])
    # Then it fails, and reports nothing of that kind was seen
    assert not result.passed
    assert "got 0" in " ".join(result.failures)


def test_a_check_unrelated_to_bptrace_is_judged_on_its_own_terms(tmp_path):
    # Given a transcript with no BPTRACE marker anywhere, checked for a shell call
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(tool_use("Bash", "t1", command="make"))])
    run = ct.parse_cc_run(p)
    # When only that shell call is expected
    result = ct.evaluate(run, [], [ct.parse_count_expectation("bash:contains=make=1")])
    # Then it passes: the checker has no opinion about markers nobody asked for
    assert result.passed, result.failures + result.anomalies
    assert result.anomalies == []


def test_the_docstring_recipe_spells_out_the_start_count_itself():
    # Given the measurement recipe kept in the module docstring
    # When it is read
    # Then it asks for the start-marker count explicitly, because evaluate() will not
    assert "--expect-count 'start=1'" in ct.__doc__


# --- V5: broken evidence must not pass ----------------------------------------
def damaged_run(tmp_path):
    """A run that meets its expectations but left a near-miss marker behind."""
    return write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("**" + START + "**")),
        assistant(text(STEP1)),
    ])


def test_an_anomaly_fails_a_checked_run(tmp_path):
    # Given a run that meets every expectation but also emitted a malformed marker
    run = ct.parse_cc_run(damaged_run(tmp_path))
    # When it is checked
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then the damaged evidence is a failure, not a footnote
    assert not result.passed
    assert any("anomal" in f for f in result.failures)


def test_allow_anomalies_restores_the_old_tolerance(tmp_path):
    # Given the same run
    run = ct.parse_cc_run(damaged_run(tmp_path))
    # When anomalies are explicitly allowed
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [],
                         allow_anomalies=True)
    # Then it passes, with the anomaly still reported
    assert result.passed and result.anomalies


def test_anomalies_alone_do_not_fail_a_report_only_run(tmp_path):
    # Given a run with anomalies but no expectations
    run = ct.parse_cc_run(damaged_run(tmp_path))
    # When evaluated
    result = ct.evaluate(run, [], [])
    # Then the damage is on the record, but there is no verdict to fail
    assert result.anomalies
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
    # and the failure text shows where the marker really came from
    assert "[main " in " ".join(result.failures)


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
    found = ct.find_runs(tmp_path, 2, ct.parse_cc_run, ct.cc_transcripts)
    # Then the marker-less run is still in the sample -- it is the one to catch
    assert [r.path.name for r in found.runs] == ["new.jsonl", "mid.jsonl"]
    assert found.shortfall == 0 and found.gaps == []


def test_theme_keeps_matching_runs_and_reports_the_ones_it_dropped(tmp_path):
    # Given runs from two different measurements
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=1000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=2000)
    marked(tmp_path, "c.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=3000)
    # When the theme scopes the search
    found = ct.find_runs(tmp_path, 5, ct.parse_cc_run, ct.cc_transcripts, theme="run-42")
    # Then that measurement's runs come back, newest first
    assert [r.path.name for r in found.runs] == ["c.jsonl", "a.jsonl"]
    # and the run the theme dropped is named, not silently absent
    assert [e.path.name for e in found.excluded] == ["b.jsonl"]
    assert "run-42" in found.excluded[0].reason
    assert found.shortfall == 3


def test_theme_never_backfills_the_sample_with_an_older_run(tmp_path):
    # Given a newest run that emitted no start marker at all, and two older good ones
    marked(tmp_path, "old.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=1000)
    marked(tmp_path, "mid.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=2000)
    marked(tmp_path, "new.jsonl", "I forgot to print the marker.", mtime=3000)
    # When the latest 2 of that theme are searched
    found = ct.find_runs(tmp_path, 2, ct.parse_cc_run, ct.cc_transcripts, theme="run-42")
    # Then the two older runs did fill the quota -- but the run that should have
    # been caught is reported as an exclusion, so the sample is not silently good
    assert [r.path.name for r in found.runs] == ["mid.jsonl", "old.jsonl"]
    assert found.shortfall == 0
    assert [e.path.name for e in found.excluded] == ["new.jsonl"]
    assert found.gaps


def test_since_is_a_window_not_an_exclusion(tmp_path):
    # Given plenty of transcripts from before the measurement window
    marked(tmp_path, "ancient.jsonl", START, mtime=1000)
    marked(tmp_path, "old.jsonl", START, mtime=2000)
    marked(tmp_path, "new.jsonl", START, mtime=time.time())
    # When the window starts after them
    found = ct.find_runs(tmp_path, 1, ct.parse_cc_run, ct.cc_transcripts,
                         since=time.time() - 3600)
    # Then the out-of-window files are simply not candidates: no gap is reported
    assert [r.path.name for r in found.runs] == ["new.jsonl"]
    assert found.excluded == [] and found.gaps == []


def test_theme_selection_is_not_a_substring_match(tmp_path):
    # Given a run whose theme merely contains the requested token
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="run-420" wf=techtest.md')
    # When the shorter theme is searched
    found = ct.find_runs(tmp_path, 1, ct.parse_cc_run, ct.cc_transcripts, theme="run-42")
    # Then it does not match, and the near miss is reported rather than dropped
    assert found.runs == []
    assert [e.path.name for e in found.excluded] == ["a.jsonl"]


def test_since_excludes_transcripts_written_before_the_session(tmp_path):
    # Given an old run and a new one
    marked(tmp_path, "old.jsonl", START, mtime=1000)
    marked(tmp_path, "new.jsonl", START, mtime=time.time())
    # When the search is limited to the last hour
    found = ct.find_runs(tmp_path, 5, ct.parse_cc_run, ct.cc_transcripts,
                         since=time.time() - 3600)
    # Then only the recent transcript is considered
    assert [r.path.name for r in found.runs] == ["new.jsonl"]


def test_since_reads_a_trailing_z_as_utc_and_a_naive_stamp_as_local():
    # Given the same wall-clock time written with and without a UTC marker
    # When they are converted
    utc = ct.parse_since("2026-07-27T00:00:00Z")
    naive = ct.parse_since("2026-07-27T00:00:00")
    # Then 'Z' is exactly that instant in UTC
    assert utc == 1785110400.0
    # an explicit offset is honoured too: +09:00 is nine hours earlier
    assert ct.parse_since("2026-07-27T00:00:00+09:00") == 1785078000.0
    assert utc - 1785078000.0 == 9 * 3600
    # and the naive form is that wall clock in the machine's own zone, so it is
    # the same instant as the 'Z' form only on a machine running UTC
    assert naive == time.mktime((2026, 7, 27, 0, 0, 0, 0, 0, -1))
    assert (naive == utc) == (time.timezone == 0 and not time.daylight)


def test_since_rejects_a_stamp_it_cannot_read():
    # Given something that is not a timestamp
    # When it is converted
    # Then a usage error is raised
    with pytest.raises(ct.UsageError):
        ct.parse_since("yesterday")


def test_latest_honours_the_requested_count(tmp_path):
    # Given two transcripts
    marked(tmp_path, "a.jsonl", START, mtime=1000)
    marked(tmp_path, "b.jsonl", START, mtime=2000)
    # When only the latest 1 is requested
    found = ct.find_runs(tmp_path, 1, ct.parse_cc_run, ct.cc_transcripts)
    # Then exactly one run comes back and nothing is flagged short
    assert [r.path.name for r in found.runs] == ["b.jsonl"] and found.gaps == []


def test_an_unreadable_transcript_does_not_abort_the_search(tmp_path):
    # Given one transcript the process cannot open and one it can
    bad = marked(tmp_path, "bad.jsonl", START, mtime=3000)
    marked(tmp_path, "good.jsonl", START, mtime=2000)
    bad.chmod(0o000)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("cannot make a file unreadable here (running as root?)")
        # When the search runs
        found = ct.find_runs(tmp_path, 1, ct.parse_cc_run, ct.cc_transcripts)
        # Then the readable one is still found and the skip is an evidence gap
        assert [r.path.name for r in found.runs] == ["good.jsonl"]
        assert [e.path.name for e in found.excluded] == ["bad.jsonl"]
        assert any("bad.jsonl" in g for g in found.gaps)
    finally:
        bad.chmod(0o644)


def test_latest_on_a_missing_project_dir_raises(tmp_path):
    # Given a project dir that does not exist
    # When runs are searched
    # Then a TranscriptError is raised
    with pytest.raises(ct.TranscriptError):
        ct.find_runs(tmp_path / "nope", 1, ct.parse_cc_run, ct.cc_transcripts)


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


def test_cli_treats_a_non_object_meta_json_as_a_failing_anomaly_not_a_crash(tmp_path):
    # Given a .meta.json that used to raise AttributeError deep in the parser
    p = happy_run(tmp_path, session="s1")
    happy_meta(tmp_path).write_text('["x"]', encoding="utf-8")
    # When the CLI runs with --json
    r = run_cli(str(p), "--json", "--expect", "start")
    # Then it is a FAIL (damaged evidence), not exit 2 and never a PASS
    assert r.returncode == 1, r.stdout + r.stderr
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


def test_cli_theme_cannot_silently_drop_a_newer_run_from_the_sample(tmp_path):
    # Given a newer run the theme does not match and an older one it does
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=3000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=2000)
    # When the theme is given together with expectations
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42",
                "--expect", "start:theme=run-42")
    # Then the matching run passes on its own merits, but the skipped candidate
    # is named and the verdict is FAIL: the sample is not the one asked for
    assert r.returncode == 1, r.stdout + r.stderr
    assert "b.jsonl" in r.stdout
    assert "a.jsonl" in r.stdout and "excluded" in r.stdout


def test_cli_dry_run_shows_the_selection_without_judging_it(tmp_path):
    # Given the same two runs
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=3000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=2000)
    # When --dry-run is used to inspect what would be measured
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42",
                "--dry-run")
    # Then both the selection and the exclusions are shown, and nothing is judged
    assert r.returncode == 0, r.stdout + r.stderr
    assert "b.jsonl" in r.stdout and "a.jsonl" in r.stdout
    assert "PASS" not in r.stdout and "FAIL" not in r.stdout


@pytest.mark.parametrize("check", [
    ["--expect", "start"],
    ["--expect-count", "delegate=1"],
    ["--expect-delegations", "1"],
])
def test_cli_refuses_to_pair_dry_run_with_an_expectation(tmp_path, check):
    # Given a perfectly good run and a command line that both selects and expects
    p = happy_run(tmp_path)
    # When --dry-run is combined with an expectation
    r = run_cli(str(p), "--dry-run", *check)
    # Then it is a usage error: a forgotten --dry-run must never exit 0 having
    # judged nothing at all
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--dry-run" in r.stderr


def test_cli_dry_run_still_emits_json_when_asked_to(tmp_path):
    # Given a project dir and a caller that wants the selection machine-readable
    marked(tmp_path, "a.jsonl", START, mtime=3000)
    # When --json --dry-run is used
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--dry-run", "--json")
    # Then --json is honoured rather than quietly replaced by the text report
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["dry_run"] is True and data["passed"] is None and data["runs"] == []
    assert [Path(x).name for x in data["selected"]] == ["a.jsonl"]


def test_cli_judges_every_transcript_named_on_the_command_line(tmp_path):
    # Given three runs handed over explicitly, one of which forgot its markers
    a = happy_run(tmp_path / "one", session="s1")
    b = happy_run(tmp_path / "two", session="s1")
    c = marked(tmp_path, "c.jsonl", "I skipped the markers this time.")
    # When all three paths are given as positional arguments
    r = run_cli(str(a), str(b), str(c), "--expect", "start")
    # Then all three are judged and the bad one cannot hide behind the good ones
    assert r.returncode == 1, r.stdout
    assert "2/3 run(s) PASS -> FAIL" in r.stdout


def test_cli_fails_when_an_explicitly_named_run_cannot_be_read(tmp_path):
    # Given one good run and one path that does not exist
    a = happy_run(tmp_path / "one", session="s1")
    # When both are named and expectations are given
    r = run_cli(str(a), str(tmp_path / "nope.jsonl"), "--expect", "start")
    # Then the unreadable one is a FAIL, never a sample of size one
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nope.jsonl" in r.stdout


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


def test_cli_json_without_expectations_reports_no_verdict_at_all(tmp_path):
    # Given a run whose --expect was lost to a shell quoting accident
    p = happy_run(tmp_path)
    # When the JSON report is read
    r = run_cli(str(p), "--json")
    data = json.loads(r.stdout)
    # Then nothing in it can be mistaken for "checked, and it was fine"
    assert data["checked"] is False
    assert data["passed"] is None
    assert data["runs"][0]["passed"] is None and data["runs"][0]["checked"] is False
    # and reporting is still a legitimate use, so the exit code stays 0
    assert r.returncode == 0, r.stdout + r.stderr


# --- V17: N runs means N different runs ---------------------------------------
def test_the_same_transcript_named_twice_is_a_usage_error(tmp_path):
    # Given one transcript handed over twice
    p = happy_run(tmp_path)
    # When the runs are loaded
    # Then it is refused rather than counted as a sample of two
    with pytest.raises(ct.UsageError):
        ct.load_runs([str(p), str(p)], ct.parse_cc_run)


def test_aliases_of_one_path_are_recognised_as_the_same_transcript(tmp_path):
    # Given three different spellings of one file
    p = happy_run(tmp_path)
    aliases = [str(p),
               str(p.parent / "." / p.name),
               str(p.parent / ".." / p.parent.name / p.name)]
    # When they are loaded together
    # Then the resolved path, not the spelling, decides
    with pytest.raises(ct.UsageError):
        ct.load_runs(aliases, ct.parse_cc_run)


def test_cli_three_copies_of_one_path_cannot_report_three_of_three(tmp_path):
    # Given the completion criterion "3 runs out of 3", and one run named 3 times
    p = happy_run(tmp_path)
    # When the CLI is asked to judge it
    r = run_cli(str(p), str(p), str(p), "--expect", "start")
    # Then it refuses (exit 2) instead of printing "3/3 run(s) PASS -> PASS"
    assert r.returncode == 2, r.stdout + r.stderr
    assert "same transcript" in r.stderr
    assert "3/3" not in r.stdout


def test_cli_two_copies_of_one_session_are_one_run_not_two(tmp_path):
    # Given a transcript copied to a second name -- two files, one session
    original = TESTDATA / "cc-no-delegation.jsonl"
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_bytes(original.read_bytes())
    b.write_bytes(original.read_bytes())
    # When both are judged as if they were two runs
    r = run_cli(str(a), str(b), "--expect", "start")
    # Then the duplicated session is named and the verdict is FAIL
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a.jsonl, b.jsonl" in r.stdout and "one run, not 2" in r.stdout


def test_cli_report_only_still_surfaces_a_duplicate_session_in_text(tmp_path):
    # Given the same two-copies-of-one-session setup, but with no expectations
    # at all -- report-only / plain listing mode
    original = TESTDATA / "cc-no-delegation.jsonl"
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_bytes(original.read_bytes())
    b.write_bytes(original.read_bytes())
    # When the CLI is run bare, with no --expect/--expect-count/--expect-delegations
    r = run_cli(str(a), str(b))
    # Then it still exits 0 (reporting is legitimate, no verdict was asked for)
    assert r.returncode == 0, r.stdout + r.stderr
    # but the duplication is not silently dropped: it is real evidence about
    # the sample, independent of whether anyone asked for a verdict
    assert "a.jsonl, b.jsonl" in r.stdout and "one run, not 2" in r.stdout


def test_cli_report_only_still_surfaces_a_duplicate_session_in_json(tmp_path):
    # Given the same setup, read through --json instead
    original = TESTDATA / "cc-no-delegation.jsonl"
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_bytes(original.read_bytes())
    b.write_bytes(original.read_bytes())
    # When the CLI is run bare with --json and no expectations
    r = run_cli(str(a), str(b), "--json")
    data = json.loads(r.stdout)
    # Then the exit-code/verdict semantics for report-only mode are unchanged
    assert r.returncode == 0, r.stdout + r.stderr
    assert data["checked"] is False and data["passed"] is None
    # but the duplicate-session evidence is still in the JSON gaps
    assert any("one run, not 2" in gap for gap in data["gaps"]), data["gaps"]


def test_two_genuinely_different_sessions_are_two_runs(tmp_path):
    # Given two transcripts recording two different sessions
    a = TESTDATA / "cc-no-delegation.jsonl"
    b = TESTDATA / "cc-dev-session.jsonl"
    # When they are loaded together
    found = ct.load_runs([str(a), str(b)], ct.parse_cc_run)
    # Then nothing is flagged: the session ids differ
    assert found.duplicates == []
    assert len(found.runs) == 2


def test_transcripts_that_record_no_session_id_are_never_called_duplicates(tmp_path):
    # Given two hand-built transcripts with no sessionId field at all
    a = marked(tmp_path, "a.jsonl", START)
    b = marked(tmp_path, "b.jsonl", START)
    # When they are loaded together
    found = ct.load_runs([str(a), str(b)], ct.parse_cc_run)
    # Then the check simply does not apply, rather than firing on "" == ""
    assert found.duplicates == [] and found.gaps == []
    assert all(run.session_ids == set() for run in found.runs)


def test_the_session_id_comes_from_the_transcript_itself():
    # Given a fixture written with the envelope a real CC transcript has
    run = ct.parse_cc_run(DATA_NO_DELEGATION)
    # When the run is parsed
    # Then the session it records is available to compare against other runs
    assert run.session_ids == {"3f1c8a52-0d47-4c1b-9a6e-70b2d5e41c88"}


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
    # Given a transcript with a corrupt line and a near-miss marker
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(assistant(text(START))) + "\n{bad\n"
                 + json.dumps(assistant(text("**" + START + "**"))) + "\n", encoding="utf-8")
    # When the CLI runs
    r = run_cli(str(p))
    # Then both anomalies appear in the report
    assert "malformed BPTRACE line" in r.stdout and "not valid JSON" in r.stdout


def test_cli_latest_says_so_when_no_run_matches_the_theme(tmp_path):
    # Given a project dir with no transcript for this measurement
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md')
    # When --latest runs without expectations
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42")
    # Then it selected nothing, said which candidate it passed over and why,
    # and still exits 0 (report-only mode has no verdict)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selected 0/1" in r.stdout
    assert "a.jsonl" in r.stdout and "run-42" in r.stdout


def test_cli_latest_reports_the_runs_it_selected(tmp_path):
    # Given two runs in the project dir
    happy_run(tmp_path, session="s1")
    happy_run(tmp_path, session="s2")
    for name, when in (("s1.jsonl", 1000), ("s2.jsonl", 2000)):
        os.utime(tmp_path / name, (when, when))
    # When the latest 2 are checked
    r = run_cli("--latest", "2", "--project-dir", str(tmp_path), "--expect", "start")
    # Then the sample it used is spelled out before the verdict
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selected 2/2" in r.stdout
    assert "s1.jsonl" in r.stdout and "s2.jsonl" in r.stdout


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


# --- C: what "quoted" means ---------------------------------------------------
QUOTED_SHAPES = {
    "four space indented code block":
        "The workflow says:\n\n    " + STEP1 + "\n\nThat is all.",
    # The marker sits flush left on purpose: if it were indented under the list
    # item, the four-space rule would hide it and this case would prove nothing
    # about "- ```" opening a fence.
    "fence inside a list item":
        "- the workflow must print\n- ```\n" + STEP1 + "\n```\n- and nothing else\n",
    # Quoting a file that itself contains fences needs a *longer* outer fence,
    # which is what CommonMark requires and what the parser follows.
    "fence nested inside a wider quotation":
        "Here is the agent file:\n\n````\n## OUT\n\n```json\n{\"status\": \"ok\"}\n```\n"
        + STEP2 + "\n````\n\nThat is the whole file.",
    "html comment":
        "<!-- hidden note\n" + STEP1 + "\nstill hidden -->\n",
}


@pytest.mark.parametrize("shape", sorted(QUOTED_SHAPES))
def test_a_marker_in_quoted_text_is_not_evidence(tmp_path, shape):
    # Given assistant text that quotes a marker without running anything
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(QUOTED_SHAPES[shape]))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the quotation is not mistaken for the model's own output
    assert run.start_markers == [] and run.step_markers == []
    assert run.malformed_markers == []


@pytest.mark.parametrize("shape", sorted(QUOTED_SHAPES))
def test_a_marker_in_quoted_text_is_recorded_rather_than_dropped(tmp_path, shape):
    # Given the same quotations
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(QUOTED_SHAPES[shape]))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then "not evidence" is not the same as "never happened"
    assert len(run.quoted_markers) == 1
    assert "BPTRACE" in run.quoted_markers[0].detail["text"]


def test_an_over_indented_closing_fence_does_not_close_the_outer_fence(tmp_path):
    # Given a quoted file body that itself contains a deeply indented ```
    body = "Here is the file:\n\n```\n        ```\n" + STEP1 + "\n```\nDone."
    # When the lines are classified
    quoted = dict(ct.evidence_lines(body))
    # Then the indented fence is content (CommonMark allows at most 3 spaces),
    # so it cannot promote the rest of the quotation back to evidence
    assert quoted[STEP1] is True
    assert quoted["Done."] is False
    # and the parser agrees
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(body))])
    run = ct.parse_cc_run(p)
    assert run.step_markers == [] and len(run.quoted_markers) == 1


def test_two_code_blocks_with_info_strings_do_not_swallow_what_follows(tmp_path):
    # Given the before/after shape an assistant writes all the time: two fenced
    # blocks in a row, both carrying an info string, then the real marker
    body = ("before\n```python\nprint(1)\n```python\nprint(2)\n```\n" + STEP1)
    # When parsed
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(body))])
    run = ct.parse_cc_run(p)
    # Then the info string decided nothing and the marker after the last fence
    # is still the model's own output
    assert [m.detail["step"] for m in run.step_markers] == ["1"]
    assert run.quoted_markers == []


def test_only_a_longer_fence_nests_inside_an_open_one(tmp_path):
    # Given an outer fence of four backticks holding an ordinary ```json block
    outer = "````\n```json\n{}\n```\n" + STEP1 + "\n````\n" + STEP2
    # When the lines are classified
    quoted = dict(ct.evidence_lines(outer))
    # Then the inner three-backtick fence neither closed nor nested inside the
    # wider one, and only the line past the four-backtick close is evidence
    assert quoted[STEP1] is True and quoted[STEP2] is False
    # and a longer fence that cannot be read as a close does nest, so it takes
    # its own close before the outer one can end
    nested = dict(ct.evidence_lines(
        "```\n````json\nx\n````\n" + STEP1 + "\n```\n" + STEP2))
    assert nested[STEP1] is True and nested[STEP2] is False


def test_a_list_marker_before_a_fence_still_opens_it(tmp_path):
    # Given a fence introduced by a list marker whose body is NOT indented, so
    # the four-space rule cannot be what is hiding the marker
    body = "- the workflow must print\n- ```\n" + STEP1 + "\n```\n" + STEP2
    # When the lines are classified
    quoted = dict(ct.evidence_lines(body))
    # Then "- ```" opened the fence and the bare ``` closed it
    assert not STEP1.startswith(" ")
    assert quoted[STEP1] is True and quoted[STEP2] is False
    # and the parser records the quoted one and counts only the other
    run = ct.parse_cc_run(write_jsonl(tmp_path / "s.jsonl", [assistant(text(body))]))
    assert [m.detail["step"] for m in run.step_markers] == ["2"]
    assert [m.detail["text"] for m in run.quoted_markers] == [STEP1]


def test_a_marker_inside_a_thinking_block_is_not_evidence(tmp_path):
    # Given an assistant turn whose thinking block "emits" both markers and
    # whose visible text says nothing
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "signature": "sig",
             "thinking": START + "\n" + STEP1 + "\nthat is what I will print"},
            text("Working on it.")]}},
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then private reasoning is not output: saying the marker is not emitting it
    assert run.start_markers == [] and run.step_markers == []
    assert run.malformed_markers == [] and run.quoted_markers == []
    assert ct.evaluate(run, [ct.parse_expectation("start")], []).passed is False


def test_an_inline_code_span_does_not_open_a_fence(tmp_path):
    # Given a line that is one long inline code span made of triple backticks
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("```" + START + "```\n" + STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the span is not a fence, so it swallows nothing after it
    assert [m.detail["step"] for m in run.step_markers] == ["1"]
    assert run.start_markers == []


def test_silencing_stdout_can_never_itself_crash():
    # Given a machine on which even /dev/null cannot be attached to stdout
    code = ("import os, sys\n"
            "import check_transcript as ct\n"
            "os.dup2 = lambda *a: (_ for _ in ()).throw(OSError('no fds'))\n"
            "ct._silence_stdout()\n"
            "sys.stderr.write('survived\\n')\n")
    # When the broken-pipe safety net runs
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(SCRIPT.parent))
    # Then it stays silent rather than turning a closed pipe into a traceback
    assert r.returncode == 0, r.stderr
    assert "survived" in r.stderr


def test_a_marker_inside_a_one_line_html_comment_is_neither_evidence_nor_a_miss(tmp_path):
    # Given a marker buried in a single-line HTML comment
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("<!-- " + STEP1 + " -->")),
        assistant(text("After the comment.")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it counts for nothing and the comment does not swallow what follows
    assert run.step_markers == [] and run.malformed_markers == []
    assert ct.evaluate(run, [ct.parse_expectation("start")], []).passed


def test_an_html_comment_closes_and_lets_evidence_resume(tmp_path):
    # Given a comment that ends before the real marker
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("<!-- draft\nBPTRACE step=9 out actor=nobody\n-->\n" + STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then only the line after the comment is evidence
    assert [m.detail["step"] for m in run.step_markers] == ["1"]


def test_a_fence_that_closes_an_inner_fence_does_not_reopen_evidence(tmp_path):
    # Given a quoted file whose own body contains a ```json fence
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(QUOTED_SHAPES["fence nested inside a wider quotation"])),
        assistant(text(STEP1)),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then only the marker the model really emitted counts
    assert [m.detail["step"] for m in run.step_markers] == ["1"]


def test_prose_after_a_nested_quotation_is_evidence_again(tmp_path):
    # Given the nested quotation followed, in the same block, by a real marker
    body = QUOTED_SHAPES["fence nested inside a wider quotation"] + "\n" + STEP1
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(body))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then the outer fence really did close and the marker after it counts
    assert [m.detail["step"] for m in run.step_markers] == ["1"]


def test_evidence_lines_marks_each_line_as_quoted_or_not():
    # Given a text block mixing prose, a fence and an indented block
    lines = list(ct.evidence_lines("alpha\n```\nbeta\n```\n    gamma\ndelta"))
    # When the classification is read
    # Then only the two prose lines are offered as evidence
    assert [line for line, quoted in lines if not quoted] == ["alpha", "delta"]


# --- C': a quoted marker is an anomaly, not a silence -------------------------
def test_a_quoted_marker_is_surfaced_as_an_anomaly(tmp_path):
    # Given a run that put its real-looking marker inside the JSON fence
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text('```json\n{"status": "ok"}\n' + STEP1 + "\n```")),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked for step 1
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then it fails, and says the marker was found but only inside quoted text
    assert not result.passed
    assert any("quoted" in a for a in result.anomalies)
    assert STEP1 in " ".join(result.anomalies)


def test_announcing_a_marker_before_emitting_it_is_not_an_anomaly(tmp_path):
    # Given the thing a well-behaved run most naturally does: show the line it
    # is about to print, inside a fence, and then actually print it
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text("I will now emit:\n\n```\n" + STEP1 + "\n```\n")),
        assistant(text(STEP1)),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then the quotation is not held against a run that really did emit one
    assert result.passed, result.failures + result.anomalies
    assert result.anomalies == []
    # and the quotation is still on the record, just not as damage
    assert len(run.quoted_markers) == 1


def test_a_quoted_marker_is_still_an_anomaly_when_only_the_quotation_exists(tmp_path):
    # Given a run that quoted a step marker of a kind it never actually emitted
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("```\n" + STEP1 + "\n```")),
    ])
    run = ct.parse_cc_run(p)
    # When it is evaluated
    result = ct.evaluate(run, [ct.parse_expectation("start")], [])
    # Then the "it is only quoted" finding is exactly the suspicion that remains
    assert not result.passed
    assert any("quoted" in a for a in result.anomalies)


def test_a_fabricated_marker_is_not_vindicated_by_a_different_marker_of_the_same_kind(tmp_path):
    # Given a run that really emits step=1 (unquoted, actor=main) but only
    # ever quotes a step=2 for a different actor -- that step=2 never really
    # happened, it was only shown inside a fence
    fabricated_step2 = "BPTRACE step=2 out actor=ghost"
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text(STEP1)),
        assistant(text("```\n" + fabricated_step2 + "\n```")),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked against the real step=1 only
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then the fabricated step=2 is still flagged as an anomaly: a genuinely
    # emitted step=1 does not vindicate a step=2 that was only ever quoted --
    # "any marker of the same kind was emitted somewhere" is too broad a guard
    assert any("quoted" in a and "step=2" in a and "ghost" in a
               for a in result.anomalies), result.anomalies


def test_a_fabricated_start_marker_is_not_vindicated_by_a_different_wf_same_theme(tmp_path):
    # Given a run that really emits a start marker (theme="neon night",
    # wf=techtest.md) but only ever quotes a start marker for the *same*
    # theme with a *different* wf -- that wf never really happened, it was
    # only shown inside a fence
    fabricated_start = 'BPTRACE start theme="neon night" wf=totally-different-workflow.md'
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text("```\n" + fabricated_start + "\n```")),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked against the real start marker only
    result = ct.evaluate(run, [ct.parse_expectation("start:theme=neon night,wf=techtest.md")], [])
    # Then the fabricated wf is still flagged as an anomaly: a genuinely
    # emitted start of the same theme does not vindicate a start that was
    # only ever quoted for a *different* wf -- theme alone is too broad a
    # guard, wf is a first-class part of a start marker's identity too
    assert any("quoted" in a and "totally-different-workflow.md" in a
               for a in result.anomalies), result.anomalies


def test_announcing_a_start_marker_before_emitting_it_is_not_an_anomaly(tmp_path):
    # Given the thing a well-behaved run most naturally does for a start
    # marker: show the line it is about to print, inside a fence, with the
    # exact same theme AND wf, and then actually print it
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text("I will now emit:\n\n```\n" + START + "\n```\n")),
        assistant(text(START)),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked
    result = ct.evaluate(run, [ct.parse_expectation("start:theme=neon night,wf=techtest.md")], [])
    # Then the quotation is not held against a run that really did emit the
    # exact same start marker (theme and wf both matching)
    assert result.passed, result.failures + result.anomalies
    assert result.anomalies == []
    # and the quotation is still on the record, just not as damage
    assert len(run.quoted_markers) == 1


def test_a_quoted_malformed_line_is_just_noise(tmp_path):
    # Given a fenced block holding a near miss rather than a real marker
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("```\nBPTRACE step=1 actor=main\n```")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then quoting a broken line is not worth reporting at all
    assert run.malformed_markers == [] and run.quoted_markers == []


# --- D: talking about BPTRACE is not a failed marker --------------------------
@pytest.mark.parametrize("line", [
    "BPTRACEマーカーは、この誤判定を防ぐための**固定トークン**です。",
    "- `BPTRACE` マーカーが正しく出力されるか",
    "BPTRACE something else entirely",
    "The BPTRACE convention is described in the design doc.",
    "`BPTRACE`",
])
def test_merely_mentioning_bptrace_is_not_a_malformed_marker(tmp_path, line):
    # Given Japanese or English prose that discusses the marker token
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START)), assistant(text(line))])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is not a near miss: the run is still clean
    assert run.malformed_markers == []
    assert ct.evaluate(run, [ct.parse_expectation("start")], []).passed


def test_a_run_is_not_failed_by_prose_that_discusses_the_marker(tmp_path):
    # Given a run that is perfect apart from a paragraph about BPTRACE
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)),
        assistant(text("BPTRACEマーカーは、この誤判定を防ぐための固定トークンです。")),
        assistant(text(STEP1)),
    ])
    run = ct.parse_cc_run(p)
    # When it is checked
    result = ct.evaluate(run, [ct.parse_expectation("step=1:actor=main")], [])
    # Then it passes
    assert result.passed, result.failures + result.anomalies


# --- E: only ASCII digits are a step number -----------------------------------
def test_a_full_width_step_number_is_not_a_valid_marker(tmp_path):
    # Given a marker whose step number is a full-width digit
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(text(START)), assistant(text("BPTRACE step=\uff11 out actor=main")),
    ])
    # When parsed
    run = ct.parse_cc_run(p)
    # Then it is a near miss, not a step marker (the expectation side is ASCII only)
    assert run.step_markers == []
    assert len(run.malformed_markers) == 1


# --- F: a second platform is a registry entry, not a fork ---------------------
def fake_platform(tmp_path):
    """A make-believe platform whose transcripts are .log files of marker lines."""

    def parse(path):
        path = Path(path)
        if not path.is_file():
            raise ct.TranscriptError(f"no such transcript: {path}")
        events, warnings = [], []
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for kind, detail in ct.markers_in_text(raw):
                events.append(ct.Event(0, kind, ct.MAIN, str(path), line_no, detail=detail))
        return ct.finalize_run(path, "ghc", events, warnings)

    return ct.Platform("ghc", parse, lambda repo: Path(tmp_path),
                       lambda d: sorted(Path(d).glob("*.log")))


def test_a_second_platform_only_has_to_supply_its_own_file_listing(tmp_path):
    # Given a platform whose transcripts are not *.jsonl at all
    platform = fake_platform(tmp_path)
    (tmp_path / "r1.log").write_text(START + "\n" + STEP1 + "\n", encoding="utf-8")
    # When the shared discovery layer is asked for the latest run
    found = ct.find_runs(tmp_path, 1, platform.parse, platform.transcripts)
    # Then it finds it, with no CC assumption anywhere in the way
    assert [r.path.name for r in found.runs] == ["r1.log"]
    assert found.gaps == []
    assert ct.evaluate(found.runs[0], [ct.parse_expectation("step=1:actor=main")], []).passed


def test_discovery_will_not_guess_a_parser(tmp_path):
    # Given a caller that forgot to say which platform it is reading
    # When discovery is invoked without a parser
    # Then it is a programming error, not a silent fallback to Claude Code
    with pytest.raises(TypeError):
        ct.find_runs(tmp_path, 1)


def test_the_marker_helpers_are_part_of_the_shared_layer():
    # Given the module
    # When a platform parser looks for the BPTRACE primitives
    # Then they are public, so a new parser need not reach into CC internals
    for name in ("evidence_lines", "marker_from_line", "markers_in_text", "finalize_run"):
        assert callable(getattr(ct, name)), name


def test_finalize_run_numbers_the_stream_it_is_given():
    # Given loose events from some parser
    events = [ct.Event(0, ct.BASH, ct.MAIN, "x", i, detail={"command": "ls"})
              for i in range(3)]
    # When the run is finalised
    run = ct.finalize_run(Path("x"), "ghc", events, ["note"])
    # Then the sequence numbers are assigned once, in order
    assert [e.seq for e in run.events] == [1, 2, 3]
    assert run.platform == "ghc" and run.warnings == ["note"]


# --- G: the exit-code contract ------------------------------------------------
def test_a_crash_while_building_the_parser_is_exit_2(tmp_path):
    # Given argument parsing itself blowing up in an unforeseen way
    boom = ("import check_transcript as ct, sys\n"
            "ct.build_parser = lambda: (_ for _ in ()).throw(RuntimeError('kaboom'))\n"
            "sys.exit(ct.main(['x.jsonl']))\n")
    # When the CLI runs
    r = subprocess.run([sys.executable, "-c", boom], capture_output=True, text=True,
                       cwd=str(SCRIPT.parent))
    # Then it is an internal error (2), not Python's default 1 which means FAIL
    assert r.returncode == 2, r.stdout + r.stderr
    assert "internal error" in r.stderr


def test_a_closed_pipe_is_not_an_internal_error(tmp_path):
    # Given a report far larger than a pipe buffer
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))] + [
        assistant(tool_use("Bash", f"t{i}", command=f"echo {'x' * 200} {i}"))
        for i in range(2000)])
    # When its output is piped into a reader that stops after one line
    r = subprocess.run(
        ["bash", "-c", f"{sys.executable} {SCRIPT} {p} --verbose | head -1; "
                       "exit ${PIPESTATUS[0]}"], capture_output=True, text=True)
    # Then the writer exits cleanly instead of reporting BrokenPipeError
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BrokenPipeError" not in r.stderr and "internal error" not in r.stderr


# --- H: the default report has to stay readable -------------------------------
def test_a_long_category_list_is_truncated_without_verbose(tmp_path):
    # Given a run with far more bash calls than anybody wants to read
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))] + [
        assistant(tool_use("Bash", f"t{i}", command=f"echo {i}")) for i in range(50)])
    # When the default report is printed
    r = run_cli(str(p))
    # Then the count is exact but the listing is capped, and says how to see it all
    assert "bash calls    : 50" in r.stdout
    assert r.stdout.count("bash: echo ") <= ct.LIST_LIMIT
    assert "more" in r.stdout and "--verbose" in r.stdout
    # and --verbose really does print them all
    assert run_cli(str(p), "--verbose").stdout.count("bash: echo ") >= 50


def test_a_multiline_command_is_described_on_one_line(tmp_path):
    # Given a heredoc-style bash call
    p = write_jsonl(tmp_path / "s.jsonl", [
        assistant(tool_use("Bash", "t1", command="python - <<'EOF'\n" + "print(1)\n" * 40
                           + "EOF")),
    ])
    run = ct.parse_cc_run(p)
    # When the event is described
    described = run.bash_calls[0].describe()
    # Then it is a single line, and bounded
    assert "\n" not in described
    assert len(described) < 250


def test_the_report_of_a_real_sized_run_stays_small(tmp_path):
    # Given a run with hundreds of events, as a real session has
    p = write_jsonl(tmp_path / "s.jsonl", [assistant(text(START))] + [
        assistant(tool_use("Bash", f"t{i}", command=f"line one {i}\nline two {i}"))
        for i in range(300)])
    # When the default report is produced
    r = run_cli(str(p))
    # Then it is a summary, not a transcript dump
    assert len(r.stdout.splitlines()) < 60, r.stdout[:2000]


# --- I: the expectation parser has no quiet reinterpretations -----------------
def test_a_comma_in_a_value_can_be_escaped():
    # Given a theme that really does contain a comma
    e = ct.parse_expectation(r"start:theme=a\,b,wf=quick.md")
    # When parsed
    # Then the escaped comma stays inside the value and the real one still splits
    assert e.keys == {"theme": "a,b", "wf": "quick.md"}


def test_a_backslash_in_a_value_can_be_escaped():
    # Given a value holding a literal backslash
    e = ct.parse_expectation(r"bash:contains=C:\\tmp")
    # When parsed
    # Then one backslash survives
    assert e.keys["contains"] == "C:\\tmp"


def test_a_dangling_backslash_is_a_usage_error():
    # Given a spec that ends mid escape
    # When parsed
    # Then it is rejected instead of being silently dropped
    with pytest.raises(ct.UsageError):
        ct.parse_expectation("start:theme=a\\")


def test_an_empty_contains_is_rejected():
    # Given a bash expectation with nothing to look for
    # When parsed
    # Then it is refused, because "" matches every command there is
    with pytest.raises(ct.UsageError):
        ct.parse_count_expectation("bash:contains==0")
    with pytest.raises(ct.UsageError):
        ct.parse_expectation("bash:contains=")


def test_a_subagent_without_an_agent_type_gets_an_unmistakable_origin(tmp_path):
    # Given a .meta.json that links a transcript but names no agent type
    p = happy_run(tmp_path, session="s1")
    write_meta(happy_sub(tmp_path), toolUseId="t2", spawnDepth=1)
    run = ct.parse_cc_run(p)
    # When the spliced events are inspected
    origins = {e.origin for e in run.events}
    # Then the placeholder cannot be mistaken for a real agent called "subagent"
    assert "subagent:subagent" not in origins
    assert "subagent:<unknown>" in origins
    # and an expectation naming a plausible agent type does not match it by luck
    assert not ct.evaluate(run, [ct.parse_expectation(
        "step=2:origin=subagent:subagent")], [], allow_anomalies=True).passed


# --- J: the declared types are the real ones ----------------------------------
def test_the_platform_registry_declares_callables_not_object():
    # Given the Platform record
    hints = typing.get_type_hints(ct.Platform)
    # When its annotations are read
    # Then the three hooks are declared as callables, not as `object`
    for field_name in ("parse", "project_dir", "transcripts"):
        assert hints[field_name] is not object, field_name
        assert "Callable" in str(hints[field_name]), (field_name, hints[field_name])


def test_an_optional_count_is_declared_optional():
    # Given the Expectation record, whose count really may be None
    hints = typing.get_type_hints(ct.Expectation)
    # When the annotation is read
    # Then it admits None
    assert type(None) in typing.get_args(hints["count"])


# --- --allow-anomalies through the CLI ----------------------------------------
def test_cli_allow_anomalies_turns_a_damaged_run_into_a_pass(tmp_path):
    # Given a run that meets its expectations but also emitted a malformed marker
    p = damaged_run(tmp_path)
    # When it is checked without the flag
    strict = run_cli(str(p), "--expect", "step=1:actor=main")
    # Then the damaged evidence fails it
    assert strict.returncode == 1 and "anomal" in strict.stdout
    # and with the flag it passes, with the anomaly still on the record
    lax = run_cli(str(p), "--expect", "step=1:actor=main", "--allow-anomalies")
    assert lax.returncode == 0, lax.stdout + lax.stderr
    assert "malformed BPTRACE line" in lax.stdout


def test_cli_allow_anomalies_does_not_excuse_a_missing_run(tmp_path):
    # Given fewer runs in the project dir than the check asked for
    happy_run(tmp_path, session="s1")
    # When --allow-anomalies is given as well
    r = run_cli("--latest", "3", "--project-dir", str(tmp_path), "--expect", "start",
                "--allow-anomalies")
    # Then a shortfall is still a FAIL: missing evidence is not a tolerable anomaly
    assert r.returncode == 1, r.stdout + r.stderr
    assert "2 missing" in r.stdout


def test_cli_allow_anomalies_does_not_excuse_an_unreadable_newest_run(tmp_path):
    # Given the newest transcript being unreadable and two older good ones
    happy_run(tmp_path, session="s1")
    happy_run(tmp_path, session="s2")
    newest = marked(tmp_path, "s3.jsonl", START)
    for name, when in (("s1.jsonl", 1000), ("s2.jsonl", 2000), ("s3.jsonl", 3000)):
        os.utime(tmp_path / name, (when, when))
    newest.chmod(0o000)
    try:
        if os.access(newest, os.R_OK):
            pytest.skip("cannot make a file unreadable here (running as root?)")
        # When the latest 2 are checked
        r = run_cli("--latest", "2", "--project-dir", str(tmp_path), "--expect", "start",
                    "--allow-anomalies")
        # Then the two older runs do not quietly backfill the sample
        assert r.returncode == 1, r.stdout + r.stderr
        assert "s3.jsonl" in r.stdout and "excluded" in r.stdout
    finally:
        newest.chmod(0o644)


def test_cli_json_carries_the_discovery_record(tmp_path):
    # Given a search that had to pass over a candidate
    marked(tmp_path, "a.jsonl", 'BPTRACE start theme="other" wf=techtest.md', mtime=3000)
    marked(tmp_path, "b.jsonl", 'BPTRACE start theme="run-42" wf=techtest.md', mtime=2000)
    # When --json is used
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--theme", "run-42",
                "--json", "--expect", "start")
    data = json.loads(r.stdout)
    # Then the machine-readable form names the sample and what was left out of it
    assert data["passed"] is False
    assert [Path(x).name for x in data["selected"]] == ["b.jsonl"]
    assert data["excluded"][0]["path"].endswith("a.jsonl")
    assert "run-42" in data["excluded"][0]["reason"]
    assert data["gaps"]


# --- committed fixtures (real transcript shape, composed contents) ------------
@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_fixtures_carry_the_outer_fields_a_real_transcript_has(path):
    # Given a committed fixture
    entries = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    turns = [e for e in entries if e.get("type") in ("user", "assistant")]
    # When its envelope is inspected
    # Then it is the CC envelope, not a bare {"type", "message"} pair -- the
    # parser is exercised against the shape it will actually meet
    assert turns
    for entry in turns:
        for key in ("uuid", "parentUuid", "timestamp", "sessionId", "isSidechain",
                    "userType", "cwd", "version", "gitBranch"):
            assert key in entry, (path.name, key, entry.get("type"))


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


def test_recorded_untyped_delegation_still_names_the_agent_it_called():
    # Given the fixture in the shape 6 of this machine's 44 real delegations
    # have: an Agent tool_use with no subagent_type, plus a .meta.json
    run = ct.parse_cc_run(DATA_UNTYPED_DELEGATION)
    # When parsed
    assert run.warnings == []
    # Then the delegation is attributable from both sides at once
    assert [d.detail["to"] for d in run.delegations] == ["techtest-echo"]
    assert [(m.detail["step"], m.origin) for m in run.step_markers] == [
        ("1", "main"), ("2", "subagent:techtest-echo")]


def test_recorded_untyped_delegation_passes_the_stage_b_expectations():
    # Given the same fixture and the full stage-B recipe from the docstring
    run = ct.parse_cc_run(DATA_UNTYPED_DELEGATION)
    ordered = [ct.parse_expectation(s) for s in [
        "start:theme=neon night,wf=techtest.md",
        "step=1:actor=main,origin=main",
        "delegate:to=techtest-echo",
        "step=2:actor=techtest-echo,origin=subagent:techtest-echo"]]
    counted = [ct.parse_count_expectation(s) for s in ["start=1", "delegate=1"]]
    # When it is judged
    result = ct.evaluate(run, ordered, counted)
    # Then `delegate:to=` and `origin=subagent:` no longer contradict each other
    assert result.passed, result.failures + result.anomalies


def test_recorded_dev_session_is_not_mistaken_for_a_run():
    # Given a recorded development session: marker text in tool results, tool inputs,
    # prose, fenced quotations, indented blocks and Japanese discussion of the
    # marker token, but never as the model's own output
    run = ct.parse_cc_run(DATA_DEV_SESSION)
    # When parsed
    # Then nothing at all is treated as evidence
    assert run.start_markers == [] and run.step_markers == []
    # and the Japanese prose about BPTRACE is not mistaken for a failed marker
    assert run.malformed_markers == []


def test_recorded_dev_session_does_not_count_its_thinking_block():
    # Given the same session, whose thinking block spells both markers out
    blob = DATA_DEV_SESSION.read_text(encoding="utf-8")
    assert '"thinking"' in blob and "BPTRACE step=1 out actor=main" in blob
    run = ct.parse_cc_run(DATA_DEV_SESSION)
    # When parsed
    # Then the reasoning is not output, and is not even recorded as a quotation
    thought = [m for m in run.quoted_markers if m.line == len(blob.splitlines())]
    assert thought == []
    assert run.start_markers == [] and run.step_markers == []


def test_recorded_dev_session_still_records_what_it_quoted():
    # Given the same development session
    run = ct.parse_cc_run(DATA_DEV_SESSION)
    # When the quoted markers are inspected
    quoted = [m.detail["text"] for m in run.quoted_markers]
    # Then every quotation shape is on the record rather than silently discarded
    assert len(quoted) >= 6
    assert 'BPTRACE start theme="neon night" wf=techtest.md' in quoted
    assert "BPTRACE step=2 out actor=techtest-echo" in quoted


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
    found = ct.find_runs(Path(LIVE_DIR), 3, ct.parse_cc_run, ct.cc_transcripts)
    # When the newest runs are parsed
    # Then every run comes back with an ordered, numbered event stream
    for run in found.runs:
        assert [e.seq for e in run.events] == list(range(1, len(run.events) + 1))


# =============================================================================
# GitHub Copilot Chat (GHC) parser
#
# GHC has no separate subagent file: a delegated subagent's own turns and
# tool calls sit inline in the same jsonl file, between the delegating
# `runSubagent` call's `tool.execution_start` and the `tool.execution_complete`
# carrying the same `toolCallId` (docs/cross-platform-agent-design.md §3.1).
# There is no live GHC transcript anywhere to record from -- nobody has run
# GHC for this project -- so every fixture below is hand-built to the
# documented schema, one behaviour at a time, exactly like the CC fixtures
# above. Nothing here is "verified against a real GHC session"; that is a
# separate, not-yet-taken step, called out explicitly in the coordinator's
# self-check (checks/task-1.md).
# =============================================================================
def ghc_assistant(content, tool_requests=None):
    return {"type": "assistant.message",
            "data": {"content": content, "toolRequests": tool_requests or []}}


def ghc_tool_request(tool_name, tool_call_id, **arguments):
    return {"name": tool_name, "toolCallId": tool_call_id, "arguments": arguments}


def ghc_exec_start(tool_name, tool_call_id, **arguments):
    return {"type": "tool.execution_start",
            "data": {"toolName": tool_name, "toolCallId": tool_call_id, "arguments": arguments}}


def ghc_exec_complete(tool_call_id, success=True):
    return {"type": "tool.execution_complete",
            "data": {"toolCallId": tool_call_id, "success": success}}


def ghc_session_start(session_id):
    return {"type": "session.start", "data": {"sessionId": session_id}}


def ghc_happy_run(tmp_path, name="s.jsonl"):
    """Step 1 done directly, step 2 delegated to techtest-echo inline: the GHC
    analogue of happy_run() above, same theme/steps/actors."""
    return write_jsonl(tmp_path / name, [
        ghc_session_start("ghc-sess-1"),
        ghc_assistant(START),
        ghc_assistant("Running step 1.",
                      [ghc_tool_request("runInTerminal", "tc1",
                                        command='echo "step1: neon night"')]),
        ghc_exec_start("runInTerminal", "tc1", command='echo "step1: neon night"'),
        ghc_exec_complete("tc1"),
        ghc_assistant(STEP1),
        ghc_assistant("Delegating step 2.",
                      [ghc_tool_request("runSubagent", "tc2", name="techtest-echo",
                                        prompt="input: neon night")]),
        ghc_exec_start("runSubagent", "tc2", name="techtest-echo", prompt="input: neon night"),
        ghc_assistant("", [ghc_tool_request("runInTerminal", "tc3",
                                             command='echo "step2: neon night"')]),
        ghc_exec_start("runInTerminal", "tc3", command='echo "step2: neon night"'),
        ghc_exec_complete("tc3"),
        ghc_assistant('{"status": "ok", "echoed": "step2: neon night"}\n' + STEP2),
        ghc_exec_complete("tc2"),
        ghc_assistant("Done."),
    ])


# --- GHC: what counts as a marker (reuses the shared evidence layer) ---------
def test_ghc_start_marker_is_extracted_from_assistant_message_content(tmp_path):
    # Given a transcript whose assistant.message content holds the start marker
    p = write_jsonl(tmp_path / "s.jsonl", [ghc_assistant(START)])
    # When the run is parsed
    run = ct.parse_ghc_run(p)
    # Then exactly one start marker with theme and wf is reported
    assert len(run.start_markers) == 1
    assert run.start_markers[0].detail == {"theme": "neon night", "wf": "techtest.md"}


def test_ghc_user_message_is_ignored(tmp_path):
    # Given a user.message that pastes a marker line
    p = write_jsonl(tmp_path / "s.jsonl", [{"type": "user.message", "data": {"content": START}}])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then only assistant output is evidence
    assert run.start_markers == []


def test_ghc_marker_inside_a_fenced_code_block_is_not_evidence(tmp_path):
    # Given assistant content that quotes the marker inside a fence -- proof
    # that GHC reuses the shared evidence_lines/markers_in_text layer instead
    # of reimplementing fence handling
    p = write_jsonl(tmp_path / "s.jsonl", [ghc_assistant("Quoting:\n\n```\n" + START + "\n```\n")])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is recorded as quoted, not evidence
    assert run.start_markers == []
    assert len(run.quoted_markers) == 1


# --- GHC: tool-call dedup (toolRequests[] + tool.execution_start -> one event) -
def test_ghc_tool_call_announced_twice_collapses_to_one_event(tmp_path):
    # Given the same call recorded once in toolRequests[] and again as its own
    # tool.execution_start, exactly as a real GHC log always records it
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("go", [ghc_tool_request("runInTerminal", "tc1", command="ls")]),
        ghc_exec_start("runInTerminal", "tc1", command="ls"),
        ghc_exec_complete("tc1"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is one event, not two
    assert len(run.tool_uses) == 1
    assert run.bash_calls[0].detail["command"] == "ls"


def test_ghc_a_tool_request_repeated_in_toolRequests_is_deduped_too(tmp_path):
    # Given the same toolCallId announced twice in toolRequests[] itself
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("go", [ghc_tool_request("runInTerminal", "tc1", command="ls")]),
        ghc_assistant("go again", [ghc_tool_request("runInTerminal", "tc1", command="ls")]),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the repeat is still recognised by toolCallId
    assert len(run.tool_uses) == 1


def test_ghc_execution_start_without_a_matching_toolRequests_entry_still_creates_an_event(
        tmp_path):
    # Given a tool.execution_start with no earlier toolRequests[] announcement
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_exec_start("runInTerminal", "tc1", command="ls"),
        ghc_exec_complete("tc1"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the call is still recorded, from the execution_start alone
    assert len(run.bash_calls) == 1 and run.bash_calls[0].detail["command"] == "ls"


def test_ghc_step_markers_reported_in_order_for_a_non_delegated_run(tmp_path):
    # Given a run that does both steps itself
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant(START),
        ghc_assistant("Running step 1.",
                      [ghc_tool_request("runInTerminal", "tc1",
                                        command='echo "step1: neon night"')]),
        ghc_exec_start("runInTerminal", "tc1", command='echo "step1: neon night"'),
        ghc_exec_complete("tc1"),
        ghc_assistant(STEP1),
        ghc_assistant("BPTRACE step=2 out actor=main"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then both step markers appear in order, both attributed to main
    assert [(m.detail["step"], m.detail["actor"]) for m in run.step_markers] == [
        ("1", "main"), ("2", "main")]
    assert run.delegations == []


# --- GHC: subagent delegation is scoped inline, by toolCallId span -----------
def test_ghc_inline_subagent_events_are_tagged_with_subagent_origin(tmp_path):
    # Given a run whose step 2 is delegated to techtest-echo inline
    run = ct.parse_ghc_run(ghc_happy_run(tmp_path))
    # When the merged event stream is read
    kinds = [(e.kind, e.origin) for e in run.events]
    # Then the subagent's own events sit right after the delegate event,
    # tagged with its origin
    delegate_at = kinds.index((ct.DELEGATE, "main"))
    assert kinds[delegate_at + 1] == (ct.BASH, "subagent:techtest-echo")
    assert kinds[delegate_at + 2] == (ct.MARKER_STEP, "subagent:techtest-echo")
    # and nothing before the delegation was tagged as the subagent's
    assert not any(o.startswith("subagent:") for _, o in kinds[:delegate_at])


def test_ghc_origin_reverts_to_main_after_the_delegation_span_closes(tmp_path):
    # Given a delegation whose span closes, followed by another tool call
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating.",
                      [ghc_tool_request("runSubagent", "tc1", name="echo-agent")]),
        ghc_exec_start("runSubagent", "tc1", name="echo-agent"),
        ghc_assistant("inside", [ghc_tool_request("runInTerminal", "tc2", command="inner")]),
        ghc_exec_start("runInTerminal", "tc2", command="inner"),
        ghc_exec_complete("tc2"),
        ghc_exec_complete("tc1"),
        ghc_assistant("outside", [ghc_tool_request("runInTerminal", "tc3", command="outer")]),
        ghc_exec_start("runInTerminal", "tc3", command="outer"),
        ghc_exec_complete("tc3"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the call inside the span is the subagent's, and the one after the
    # matching tool.execution_complete is main's again
    assert [e.origin for e in run.bash_calls] == ["subagent:echo-agent", "main"]


def test_ghc_delegation_is_counted_with_target_agent(tmp_path):
    # Given the happy-path GHC run
    run = ct.parse_ghc_run(ghc_happy_run(tmp_path))
    # When delegations are inspected
    # Then one runSubagent call to techtest-echo is reported
    assert len(run.delegations) == 1
    assert run.delegations[0].detail["to"] == "techtest-echo"


def test_ghc_nested_subagent_spans_are_attributed_layer_by_layer(tmp_path):
    # Given an outer runSubagent opened, then a second runSubagent opened
    # before the first closes, both closing in correct LIFO order -- the GHC
    # analogue of test_nested_subagent_events_are_spliced_recursively for CC
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating outer.",
                      [ghc_tool_request("runSubagent", "tc-outer", name="outer-agent")]),
        ghc_exec_start("runSubagent", "tc-outer", name="outer-agent"),
        ghc_assistant("outer working",
                      [ghc_tool_request("runInTerminal", "tc-a", command="in outer")]),
        ghc_exec_start("runInTerminal", "tc-a", command="in outer"),
        ghc_exec_complete("tc-a"),
        ghc_assistant("Delegating inner.",
                      [ghc_tool_request("runSubagent", "tc-inner", name="inner-agent")]),
        ghc_exec_start("runSubagent", "tc-inner", name="inner-agent"),
        ghc_assistant("inner working",
                      [ghc_tool_request("runInTerminal", "tc-b", command="in inner")]),
        ghc_exec_start("runInTerminal", "tc-b", command="in inner"),
        ghc_exec_complete("tc-b"),
        ghc_exec_complete("tc-inner"),  # inner closes first, correct LIFO order
        ghc_assistant("outer working again",
                      [ghc_tool_request("runInTerminal", "tc-c", command="still outer")]),
        ghc_exec_start("runInTerminal", "tc-c", command="still outer"),
        ghc_exec_complete("tc-c"),
        ghc_exec_complete("tc-outer"),  # outer closes last
        ghc_assistant("back home",
                      [ghc_tool_request("runInTerminal", "tc-d", command="main again")]),
        ghc_exec_start("runInTerminal", "tc-d", command="main again"),
        ghc_exec_complete("tc-d"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then each bash call is attributed to the layer that was open when it ran,
    # reverting correctly as each span closes, and nothing is flagged as an anomaly
    assert [e.origin for e in run.bash_calls] == [
        "subagent:outer-agent", "subagent:inner-agent",
        "subagent:outer-agent", "main",
    ]
    assert run.warnings == []


def test_ghc_happy_run_passes_the_stage_b_expectations(tmp_path):
    # Given the same run and the stage-B recipe from the module docstring
    run = ct.parse_ghc_run(ghc_happy_run(tmp_path))
    ordered = [ct.parse_expectation(s) for s in [
        "start:theme=neon night,wf=techtest.md",
        "step=1:actor=main,origin=main",
        "delegate:to=techtest-echo",
        "step=2:actor=techtest-echo,origin=subagent:techtest-echo"]]
    counted = [ct.parse_count_expectation(s) for s in ["start=1", "delegate=1"]]
    # When it is judged
    result = ct.evaluate(run, ordered, counted)
    # Then it passes -- exactly the invariant CC and GHC are meant to share
    assert result.passed, result.failures + result.anomalies


def test_ghc_session_id_is_recorded_from_session_start(tmp_path):
    # Given the happy-path run, whose session.start records a sessionId
    run = ct.parse_ghc_run(ghc_happy_run(tmp_path))
    # When the session ids are read
    # Then it is available to compare against other runs, as CC's is
    assert run.session_ids == {"ghc-sess-1"}


def test_ghc_scan_tolerates_no_session_ids_set_being_handed_in(tmp_path):
    # Given a caller of the lower-level scan that does not care about session
    # ids at all (parse_ghc_run always passes one; this covers the default)
    p = write_jsonl(tmp_path / "s.jsonl", [ghc_session_start("sid"), ghc_assistant(START)])
    # When scanned directly without a session_ids set
    events = ct._ghc_scan(p, [])
    # Then it does not crash, and the marker is still read
    assert len(events) == 1


def test_ghc_a_delegation_with_no_target_name_gets_an_unmistakable_origin(tmp_path):
    # Given a runSubagent call whose arguments name no target at all
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("go", [ghc_tool_request("runSubagent", "tc1")]),
        ghc_exec_start("runSubagent", "tc1"),
        ghc_assistant(STEP2),
        ghc_exec_complete("tc1"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then `to` stays empty rather than being smuggled in from the placeholder
    assert run.delegations[0].detail["to"] == ""
    assert any(e.origin == f"subagent:{ct.UNKNOWN_AGENT}" for e in run.events)
    assert any("no target" in w for w in run.warnings)


def test_ghc_a_runSubagent_execution_start_without_a_tool_call_id_is_warned_about(tmp_path):
    # Given a runSubagent execution_start that never recorded a toolCallId
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "tool.execution_start",
         "data": {"toolName": "runSubagent", "arguments": {"name": "echo-agent"}}},
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the delegation still counts, but the span cannot be scoped, and
    # that gap is stated rather than silently guessed at
    assert run.delegations and run.delegations[0].detail["to"] == "echo-agent"
    assert any("toolCallId" in w for w in run.warnings)


def test_ghc_an_unclosed_delegation_span_is_warned_about(tmp_path):
    # Given a runSubagent span with no matching tool.execution_complete
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("go", [ghc_tool_request("runSubagent", "tc1", name="techtest-echo")]),
        ghc_exec_start("runSubagent", "tc1", name="techtest-echo"),
        ghc_assistant(STEP2),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is named as a gap, not silently closed at end of file
    assert any("never closed" in w for w in run.warnings)
    assert run.events[-1].origin == "subagent:techtest-echo"


# --- GHC: out-of-order tool.execution_complete (review finding #1) ----------
def test_ghc_out_of_order_completion_closes_the_matched_frame_and_everything_above_it(tmp_path):
    # Given an outer runSubagent opened, then an inner one nested inside it, but
    # the OUTER's tool.execution_complete arrives before the inner's own ever
    # does -- a genuine out-of-order completion, not a well-formed LIFO close
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating outer.",
                      [ghc_tool_request("runSubagent", "tc-outer", name="outer-agent")]),
        ghc_exec_start("runSubagent", "tc-outer", name="outer-agent"),
        ghc_assistant("Delegating inner.",
                      [ghc_tool_request("runSubagent", "tc-inner", name="inner-agent")]),
        ghc_exec_start("runSubagent", "tc-inner", name="inner-agent"),
        ghc_assistant("still inside inner",
                      [ghc_tool_request("runInTerminal", "tc-mid", command="inside inner")]),
        ghc_exec_start("runInTerminal", "tc-mid", command="inside inner"),
        ghc_exec_complete("tc-mid"),
        ghc_exec_complete("tc-outer"),  # out of order: tc-inner never closes first
        ghc_assistant("after the out-of-order close",
                      [ghc_tool_request("runInTerminal", "tc-after", command="after")]),
        ghc_exec_start("runInTerminal", "tc-after", command="after"),
        ghc_exec_complete("tc-after"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the call made while inner was genuinely open is attributed to inner,
    # and the outer's out-of-order close pops BOTH frames -- not just its own --
    # so the call after it reverts all the way to main, not to the dangling outer
    assert [e.origin for e in run.bash_calls] == ["subagent:inner-agent", "main"]
    # And the anomaly is stated, naming the id and how many frames it took with it,
    # instead of being silently discarded or silently corrected
    warning = next(w for w in run.warnings if "closed out of order" in w)
    assert "toolCallId=tc-outer" in warning
    assert "popped 2 frame(s)" in warning
    assert "subagent:outer-agent" in warning and "subagent:inner-agent" in warning
    # And there is no separate, misleading "never closed" warning for tc-inner --
    # the out-of-order close already accounted for it closing too
    assert not any("never closed" in w for w in run.warnings)


# --- GHC: toolCallId reuse across two unrelated calls (review finding #2) ---
def test_ghc_a_reused_toolCallId_across_two_unrelated_calls_does_not_erase_the_second(tmp_path):
    # Given a runInTerminal call that completes normally under id "reused-id",
    # and later, an unrelated runSubagent delegation to a different agent that
    # happens to reuse that same, already-closed id
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("first call",
                      [ghc_tool_request("runInTerminal", "reused-id", command="first")]),
        ghc_exec_start("runInTerminal", "reused-id", command="first"),
        ghc_exec_complete("reused-id"),
        ghc_assistant("Delegating, reusing the id.",
                      [ghc_tool_request("runSubagent", "reused-id", name="second-agent")]),
        ghc_exec_start("runSubagent", "reused-id", name="second-agent"),
        ghc_assistant("BPTRACE step=2 out actor=second-agent"),
        ghc_exec_complete("reused-id"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the delegation is NOT silently dropped: it is recorded as its own
    # delegate event, its marker is scoped to the subagent it actually belongs
    # to, and the collision is visible as a warning rather than a clean,
    # warning-free run that hides a vanished delegation
    assert len(run.delegations) == 1
    assert run.delegations[0].detail["to"] == "second-agent"
    assert run.step_markers[0].origin == "subagent:second-agent"
    assert any("reused-id" in w and "reused" in w for w in run.warnings)


def test_ghc_a_reused_toolCallId_is_recognised_even_via_execution_start_alone(tmp_path):
    # Given a runInTerminal call that completes normally under id "reused-id",
    # and later a runSubagent tool.execution_start reuses that same id directly
    # -- with no toolRequests[] announcement in between -- exercising the reuse
    # check inside the tool.execution_start branch itself, not just the one in
    # the toolRequests[] loop
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("first call",
                      [ghc_tool_request("runInTerminal", "reused-id", command="first")]),
        ghc_exec_start("runInTerminal", "reused-id", command="first"),
        ghc_exec_complete("reused-id"),
        ghc_exec_start("runSubagent", "reused-id", name="second-agent"),
        ghc_assistant("BPTRACE step=2 out actor=second-agent"),
        ghc_exec_complete("reused-id"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the delegation is recognised as its own distinct event, not silently
    # merged into the earlier, already-closed runInTerminal call
    assert len(run.delegations) == 1
    assert run.delegations[0].detail["to"] == "second-agent"
    assert run.step_markers[0].origin == "subagent:second-agent"
    assert any("reused-id" in w and "reused" in w for w in run.warnings)


# --- GHC: toolCallId reused WHILE still open, not after close (fix round 2) --
# The first fix round only recognised reuse once a `tool.execution_complete`
# had been seen for the id. These reproduce the gap all three reviewers found
# independently: a genuinely different call reusing an id that some earlier
# call still legitimately holds open.
def test_ghc_reused_toolCallId_while_still_open_with_a_plain_call_is_not_merged_into_the_stale_delegation(
        tmp_path):
    # Given a runSubagent delegation that opens under id "tc1" and never
    # closes (stays open throughout), followed -- before its own completion
    # ever arrives -- by an unrelated plain runInTerminal call that reuses
    # that SAME id "tc1"
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating.",
                      [ghc_tool_request("runSubagent", "tc1", name="outer-agent")]),
        ghc_exec_start("runSubagent", "tc1", name="outer-agent"),
        ghc_assistant("Reusing the id for an unrelated call.",
                      [ghc_tool_request("runInTerminal", "tc1", command="collide")]),
        ghc_exec_start("runInTerminal", "tc1", command="collide"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the second call's own event survives -- it is not silently dropped
    # as "just the duplicate announcement of the still-open runSubagent call"
    assert len(run.bash_calls) == 1
    assert run.bash_calls[0].detail["command"] == "collide"
    # And the collision is named as a warning, not silently absorbed
    assert any("tc1" in w and "collides" in w for w in run.warnings)
    # And the stale delegation's own frame was NOT pushed a second time: only
    # one runSubagent span is reported unclosed at end of file, not two
    never_closed = [w for w in run.warnings if "never closed" in w]
    assert len(never_closed) == 1 and "1 runSubagent" in never_closed[0]


def test_ghc_reused_toolCallId_while_still_open_is_recognised_even_via_execution_start_alone(
        tmp_path):
    # Given a runSubagent delegation that opens under id "tc1" and stays
    # open, followed by a tool.execution_start alone -- no toolRequests[]
    # announcement in between -- that reuses that same id for an unrelated
    # plain call: exercises the collision check inside the
    # tool.execution_start branch itself, not just the one in the
    # toolRequests[] loop
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating.",
                      [ghc_tool_request("runSubagent", "tc1", name="outer-agent")]),
        ghc_exec_start("runSubagent", "tc1", name="outer-agent"),
        ghc_exec_start("runInTerminal", "tc1", command="collide"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the colliding call is recognised as its own distinct event, not
    # silently merged into the still-open runSubagent call
    assert len(run.bash_calls) == 1
    assert run.bash_calls[0].detail["command"] == "collide"
    assert any("tc1" in w and "collides" in w for w in run.warnings)
    # And the stale delegation's own frame was not pushed a second time
    never_closed = [w for w in run.warnings if "never closed" in w]
    assert len(never_closed) == 1 and "1 runSubagent" in never_closed[0]


def test_ghc_reused_toolCallId_while_still_open_with_a_second_runSubagent_survives_with_correct_origin(
        tmp_path):
    # Given a runSubagent delegation to agent-a that opens under id "tc1" and
    # stays open, followed -- before its own completion ever arrives -- by an
    # UNRELATED second runSubagent delegation to agent-b that reuses the same
    # id "tc1": reuse-while-open, both calls being delegations this time
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating to a.",
                      [ghc_tool_request("runSubagent", "tc1", name="agent-a")]),
        ghc_exec_start("runSubagent", "tc1", name="agent-a"),
        ghc_assistant("Reusing the id, delegating to b.",
                      [ghc_tool_request("runSubagent", "tc1", name="agent-b")]),
        ghc_exec_start("runSubagent", "tc1", name="agent-b"),
        ghc_assistant("BPTRACE step=2 out actor=agent-b"),
        ghc_exec_complete("tc1"),  # closes agent-b's frame, the innermost/most recent
        ghc_assistant("BPTRACE step=3 out actor=agent-a"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then BOTH delegations survive as their own distinct events -- the second
    # is not silently dropped, and the first is not silently merged with it
    assert len(run.delegations) == 2
    assert [d.detail["to"] for d in run.delegations] == ["agent-a", "agent-b"]
    # And origin tracks correctly: the marker made while both spans were open
    # is scoped to the innermost span (agent-b, the one that opened last)
    assert run.step_markers[0].origin == "subagent:agent-b"
    # and after agent-b's completion the ORIGINAL agent-a span is still open,
    # intact and un-duplicated -- not corrupted by the collision
    assert run.step_markers[1].origin == "subagent:agent-a"
    # And the collision is named, and only ONE span (agent-a's) is left open
    # at end of file, not two -- proof the stale call's frame was never
    # pushed a second time
    assert any("tc1" in w and "collides" in w for w in run.warnings)
    never_closed = [w for w in run.warnings if "never closed" in w]
    assert len(never_closed) == 1 and "1 runSubagent" in never_closed[0]


def test_ghc_reused_toolCallId_while_still_open_at_a_non_outermost_nesting_level(tmp_path):
    # Given three levels of delegation (L1 -> L2 -> L3), and -- while all
    # three are open, three levels deep -- an unrelated plain call that
    # reuses the MIDDLE level's id ("tc-mid"), not the innermost or outermost
    # one, exercising the collision at a non-outermost frame
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating L1.",
                      [ghc_tool_request("runSubagent", "tc-l1", name="l1-agent")]),
        ghc_exec_start("runSubagent", "tc-l1", name="l1-agent"),
        ghc_assistant("Delegating L2.",
                      [ghc_tool_request("runSubagent", "tc-mid", name="l2-agent")]),
        ghc_exec_start("runSubagent", "tc-mid", name="l2-agent"),
        ghc_assistant("Delegating L3.",
                      [ghc_tool_request("runSubagent", "tc-l3", name="l3-agent")]),
        ghc_exec_start("runSubagent", "tc-l3", name="l3-agent"),
        ghc_assistant("Three levels deep, reusing the middle id.",
                      [ghc_tool_request("runInTerminal", "tc-mid", command="collide")]),
        ghc_exec_start("runInTerminal", "tc-mid", command="collide"),
        ghc_exec_complete("tc-l3"),
        ghc_assistant("back in L2",
                      [ghc_tool_request("runInTerminal", "tc-after", command="in l2 again")]),
        ghc_exec_start("runInTerminal", "tc-after", command="in l2 again"),
        ghc_exec_complete("tc-after"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the colliding call, announced while nested 3 deep, survives as its
    # own event, scoped to the innermost frame at the time (l3-agent) -- not
    # dropped as "just the duplicate announcement" of the still-open middle
    # delegation
    assert [e.origin for e in run.bash_calls] == ["subagent:l3-agent", "subagent:l2-agent"]
    assert run.bash_calls[0].detail["command"] == "collide"
    # And the collision names the middle id specifically
    assert any("tc-mid" in w and "collides" in w for w in run.warnings)
    # And the middle and outer spans are still intact, un-duplicated: after
    # L3 closes, exactly two runSubagent spans (l1, l2) remain open -- not
    # three, which is what a second push of the middle frame would produce
    never_closed = [w for w in run.warnings if "never closed" in w]
    assert len(never_closed) == 1 and "2 runSubagent" in never_closed[0]


# --- GHC: stray/duplicate tool.execution_complete (fix round 2, Verification) -
def test_ghc_a_stray_duplicate_completion_after_a_legitimate_reuse_is_reported_not_silently_accepted(
        tmp_path):
    # Given a runSubagent delegation to agent-a that opens and closes normally
    # under id "reused-id", then a second, legitimate reuse of that same id
    # by agent-b that ALSO opens and closes normally -- followed by a THIRD,
    # stray/duplicate tool.execution_complete for the same id that does not
    # correspond to anything currently open (agent-b's own completion already
    # closed it, and nothing has reopened it since)
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("Delegating to a.",
                      [ghc_tool_request("runSubagent", "reused-id", name="agent-a")]),
        ghc_exec_start("runSubagent", "reused-id", name="agent-a"),
        ghc_exec_complete("reused-id"),
        ghc_assistant("Reusing the id, delegating to b.",
                      [ghc_tool_request("runSubagent", "reused-id", name="agent-b")]),
        ghc_exec_start("runSubagent", "reused-id", name="agent-b"),
        ghc_exec_complete("reused-id"),
        ghc_assistant("outside", [ghc_tool_request("runInTerminal", "tc-outer", command="outer")]),
        ghc_exec_start("runInTerminal", "tc-outer", command="outer"),
        ghc_exec_complete("tc-outer"),
        ghc_exec_complete("reused-id"),  # stray/duplicate: nothing open under this id anymore
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then both delegations are recorded normally and origin correctly
    # reverted to main after agent-b's own legitimate completion -- the call
    # made afterwards is main's, not left dangling in agent-b's span
    assert len(run.delegations) == 2
    assert run.bash_calls[0].origin == "main"
    # And the stray extra completion is reported as such, not silently
    # accepted as a no-op success -- exactly one such anomaly, naming the id
    stray = [w for w in run.warnings if "stray" in w]
    assert len(stray) == 1
    assert "reused-id" in stray[0]
    assert "already closed" in stray[0]
    # And there is no leftover unclosed span at end of file caused by the
    # stray completion mistakenly popping something it should not have
    assert not any("never closed" in w for w in run.warnings)


# --- GHC: event kinds are normalised the same way as CC ----------------------
def test_ghc_event_kinds_are_normalised_by_the_parser(tmp_path):
    # Given a run using a delegation tool, a shell tool and another tool
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("a", [ghc_tool_request("runSubagent", "t1", name="x")]),
        ghc_exec_start("runSubagent", "t1", name="x"),
        ghc_exec_complete("t1"),
        ghc_assistant("b", [ghc_tool_request("runInTerminal", "t2", command="ls")]),
        ghc_exec_start("runInTerminal", "t2", command="ls"),
        ghc_exec_complete("t2"),
        ghc_assistant("c", [ghc_tool_request("readFile", "t3", path="a.md")]),
        ghc_exec_start("readFile", "t3", path="a.md"),
        ghc_exec_complete("t3"),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the kinds carry the meaning and the GHC tool names are display only
    assert [e.kind for e in run.events] == [ct.DELEGATE, ct.BASH, ct.TOOL]
    assert [e.name for e in run.events] == ["runSubagent", "runInTerminal", "readFile"]


def test_ghc_a_tool_request_without_a_name_falls_back_to_the_tool_kind(tmp_path):
    # Given a toolRequests entry missing both name and arguments
    p = write_jsonl(tmp_path / "s.jsonl", [ghc_assistant("go", [{"toolCallId": "tc1"}])])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it still becomes an event, just an unclassified one
    assert run.tool_uses[0].kind == ct.TOOL and run.tool_uses[0].name == ""


# --- GHC: parsing robustness --------------------------------------------------
def test_ghc_broken_json_line_is_skipped_with_a_warning(tmp_path):
    # Given a transcript with one corrupt line
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(ghc_assistant(START)) + "\n{not json\n"
                 + json.dumps(ghc_assistant(STEP1)) + "\n", encoding="utf-8")
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the good lines survive and the bad one is reported
    assert len(run.start_markers) == 1 and len(run.step_markers) == 1
    assert any("line 2" in w for w in run.warnings)


def test_ghc_empty_file_yields_an_empty_run(tmp_path):
    # Given an empty transcript
    p = tmp_path / "s.jsonl"
    p.write_text("", encoding="utf-8")
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then there is nothing to report and no crash
    assert run.events == [] and run.start_markers == []


def test_ghc_missing_file_raises_transcript_error(tmp_path):
    # Given a path that does not exist
    # When parsed
    # Then a TranscriptError is raised, exactly like the CC parser
    with pytest.raises(ct.TranscriptError):
        ct.parse_ghc_run(tmp_path / "nope.jsonl")


def test_ghc_non_dict_top_level_entries_are_tolerated(tmp_path):
    # Given a stray non-object JSON line ahead of a good one
    p = tmp_path / "s.jsonl"
    p.write_text('"stray string"\n' + json.dumps(ghc_assistant(START)) + "\n", encoding="utf-8")
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is skipped rather than crashing the parser
    assert len(run.start_markers) == 1


def test_ghc_session_start_without_a_session_id_is_tolerated(tmp_path):
    # Given a session.start with no sessionId field
    p = write_jsonl(tmp_path / "s.jsonl", [{"type": "session.start", "data": {}},
                                            ghc_assistant(START)])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then nothing is recorded for it, and parsing continues normally
    assert run.session_ids == set() and len(run.start_markers) == 1


def test_ghc_assistant_message_without_a_data_object_is_tolerated(tmp_path):
    # Given an assistant.message entry with no data field at all
    p = write_jsonl(tmp_path / "s.jsonl", [{"type": "assistant.message"}, ghc_assistant(START)])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is skipped rather than crashing, and the rest is still read
    assert len(run.start_markers) == 1


def test_ghc_missing_tool_requests_field_is_tolerated(tmp_path):
    # Given an assistant.message with content but no toolRequests key
    p = write_jsonl(tmp_path / "s.jsonl", [{"type": "assistant.message", "data": {"content": START}}])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the marker is still read and no tool event is invented
    assert len(run.start_markers) == 1 and run.tool_uses == []


def test_ghc_a_non_dict_tool_request_entry_is_tolerated(tmp_path):
    # Given a toolRequests list holding a non-object entry
    p = write_jsonl(tmp_path / "s.jsonl",
                    [{"type": "assistant.message", "data": {"content": "", "toolRequests": ["oops"]}}])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it is skipped rather than crashing
    assert run.tool_uses == []


def test_ghc_a_tool_request_without_a_tool_call_id_is_skipped(tmp_path):
    # Given a toolRequests entry with no toolCallId to dedup or scope by
    p = write_jsonl(tmp_path / "s.jsonl", [
        ghc_assistant("go", [{"name": "runInTerminal", "arguments": {"command": "ls"}}]),
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then it produces no event at all, rather than one nothing can dedup against,
    # but the drop is stated rather than silently swallowed (consistent with every
    # other missing-field case in this parser: runSubagent-with-no-id,
    # no-delegation-target, unclosed-span-at-eof all warn too)
    assert run.tool_uses == []
    assert any("toolCallId" in w for w in run.warnings)


def test_ghc_execution_start_without_a_tool_call_id_does_not_crash(tmp_path):
    # Given a tool.execution_start and .execution_complete that never carried
    # a toolCallId
    p = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "tool.execution_start", "data": {"toolName": "runInTerminal",
                                                    "arguments": {"command": "ls"}}},
        {"type": "tool.execution_complete", "data": {}},
    ])
    # When parsed
    run = ct.parse_ghc_run(p)
    # Then the call is still recorded once, and nothing crashes trying to pop
    # a span that was never pushed
    assert len(run.bash_calls) == 1


# --- GHC: platform registration ------------------------------------------------
def test_ghc_platform_is_registered():
    # Given the platform registry
    platform = ct.PLATFORMS["ghc"]
    # When it is used
    # Then all three hooks resolve to the GHC implementations
    assert platform.parse is ct.parse_ghc_run
    assert platform.transcripts is ct.ghc_transcripts
    assert callable(platform.project_dir)


def test_platforms_registry_holds_exactly_cc_and_ghc():
    # Given the platform registry
    # When its keys are inspected
    # Then it holds exactly the two supported platforms -- no more, no fewer
    assert set(ct.PLATFORMS) == {"cc", "ghc"}


def test_cli_rejects_an_unknown_platform_value(tmp_path):
    # Given a transcript that would otherwise parse fine
    p = happy_run(tmp_path)
    # When the CLI is invoked with a --platform value that is not registered
    r = run_cli(str(p), "--platform", "not-a-real-platform")
    # Then argparse rejects it before any parsing is attempted
    assert r.returncode == 2
    assert "invalid choice" in r.stderr


# --- GHC: project-dir discovery (path-matching logic only; see note below) --
# There is no live VS Code install anywhere this suite runs, so none of this
# is "verified against a real workspaceStorage". What is tested is the
# matching logic itself, against directories built by hand with tmp_path:
# given a `workspace.json`, does the right wsHash come back.
def write_workspace_json(storage_dir, ws_hash, folder):
    d = storage_dir / ws_hash
    d.mkdir(parents=True, exist_ok=True)
    (d / "workspace.json").write_text(json.dumps({"folder": folder}), encoding="utf-8")
    return d


def test_ghc_workspace_dir_matches_by_folder_path_suffix(tmp_path):
    # Given a synthetic VS Code User dir whose workspace.json names this repo
    # through a WSL-style remote URI
    user_dir = tmp_path / "User"
    ws = write_workspace_json(user_dir / "workspaceStorage", "abc123",
                              "vscode-remote://wsl%2BUbuntu/home/u/work/bp")
    # When the repo path is looked up
    found = ct._ghc_workspace_dir(user_dir, "/home/u/work/bp")
    # Then the matching hash directory is returned
    assert found == ws


def test_ghc_workspace_dir_returns_none_without_a_match(tmp_path):
    # Given a workspace.json naming a different repository entirely
    user_dir = tmp_path / "User"
    write_workspace_json(user_dir / "workspaceStorage", "abc123",
                         "vscode-remote://wsl%2BUbuntu/home/u/other-repo")
    # When the repo path is looked up
    # Then nothing matches
    assert ct._ghc_workspace_dir(user_dir, "/home/u/work/bp") is None


def test_ghc_workspace_dir_ignores_a_corrupt_workspace_json(tmp_path):
    # Given one hash dir whose workspace.json is not valid JSON, and one that
    # is a genuine match
    user_dir = tmp_path / "User"
    bad = user_dir / "workspaceStorage" / "bad1"
    bad.mkdir(parents=True)
    (bad / "workspace.json").write_text("{not json", encoding="utf-8")
    write_workspace_json(user_dir / "workspaceStorage", "good1", "/home/u/work/bp")
    # When the repo path is looked up
    found = ct._ghc_workspace_dir(user_dir, "/home/u/work/bp")
    # Then the corrupt one is skipped rather than raising, and the real match
    # is still found
    assert found == user_dir / "workspaceStorage" / "good1"


def test_ghc_workspace_dir_ignores_a_workspace_json_that_is_not_an_object(tmp_path):
    # Given a workspace.json that parses but is not a JSON object
    user_dir = tmp_path / "User"
    listy = user_dir / "workspaceStorage" / "listy1"
    listy.mkdir(parents=True)
    (listy / "workspace.json").write_text("[1, 2, 3]", encoding="utf-8")
    write_workspace_json(user_dir / "workspaceStorage", "good1", "/home/u/work/bp")
    # When the repo path is looked up
    found = ct._ghc_workspace_dir(user_dir, "/home/u/work/bp")
    # Then it is skipped, not mistaken for a match
    assert found == user_dir / "workspaceStorage" / "good1"


def test_ghc_workspace_dir_ignores_a_non_string_folder_field(tmp_path):
    # Given a workspace.json whose folder field is not a string
    user_dir = tmp_path / "User"
    nully = user_dir / "workspaceStorage" / "null1"
    nully.mkdir(parents=True)
    (nully / "workspace.json").write_text(json.dumps({"folder": None}), encoding="utf-8")
    write_workspace_json(user_dir / "workspaceStorage", "good1", "/home/u/work/bp")
    # When the repo path is looked up
    found = ct._ghc_workspace_dir(user_dir, "/home/u/work/bp")
    # Then it is skipped, not mistaken for a match
    assert found == user_dir / "workspaceStorage" / "good1"


def test_ghc_workspace_dir_skips_a_hash_dir_without_workspace_json(tmp_path):
    # Given a hash dir with no workspace.json at all, alongside a real match
    user_dir = tmp_path / "User"
    (user_dir / "workspaceStorage" / "empty1").mkdir(parents=True)
    write_workspace_json(user_dir / "workspaceStorage", "good1", "/home/u/work/bp")
    # When the repo path is looked up
    found = ct._ghc_workspace_dir(user_dir, "/home/u/work/bp")
    # Then it is skipped, not mistaken for a match
    assert found == user_dir / "workspaceStorage" / "good1"


def test_ghc_workspace_dir_missing_storage_dir_returns_none(tmp_path):
    # Given a User dir that has no workspaceStorage at all
    # When the repo path is looked up
    # Then it is simply no match, not an error
    assert ct._ghc_workspace_dir(tmp_path / "User", "/home/u/work/bp") is None


def test_ghc_project_dir_finds_the_matching_workspace_among_several_user_dirs(tmp_path):
    # Given several candidate User dirs, only one of which names this repo
    other = tmp_path / "other-user"
    write_workspace_json(other / "workspaceStorage", "nope", "/home/u/elsewhere")
    mine = tmp_path / "my-user"
    write_workspace_json(mine / "workspaceStorage", "mine1", "/home/u/work/bp")
    # When the project dir is resolved
    found = ct.ghc_project_dir("/home/u/work/bp", user_dirs=[other, mine])
    # Then the transcripts dir under the matching one comes back
    assert found == mine / "workspaceStorage" / "mine1" / "GitHub.copilot-chat" / "transcripts"


def test_ghc_project_dir_with_no_match_returns_a_path_find_runs_will_reject(tmp_path):
    # Given a candidate that does not name this repo at all
    user_dir = tmp_path / "User"
    write_workspace_json(user_dir / "workspaceStorage", "x", "/home/u/somewhere-else")
    # When the project dir is resolved
    found = ct.ghc_project_dir("/home/u/work/bp", user_dirs=[user_dir])
    # Then it is a path that cannot exist -- find_runs reports it the same
    # way it reports any other missing project directory
    assert not found.is_dir()
    with pytest.raises(ct.TranscriptError):
        ct.find_runs(found, 1, ct.parse_ghc_run, ct.ghc_transcripts)


def test_ghc_project_dir_with_empty_user_dirs_falls_back_to_a_default_base(tmp_path):
    # Given no candidate User dirs at all
    # When the project dir is resolved
    found = ct.ghc_project_dir("/home/u/work/bp", user_dirs=[])
    # Then it still returns a Path rather than raising or returning None
    assert found.parts[-4:] == ("workspaceStorage", "<no-matching-workspace>",
                                "GitHub.copilot-chat", "transcripts")


def test_ghc_project_dir_honours_the_env_override_for_the_user_dir(tmp_path, monkeypatch):
    # Given BP_GHC_VSCODE_USER_DIR pointing at a synthetic User dir
    user_dir = tmp_path / "custom-user"
    write_workspace_json(user_dir / "workspaceStorage", "envhash", "/home/u/work/bp")
    monkeypatch.setenv("BP_GHC_VSCODE_USER_DIR", str(user_dir))
    # When the project dir is resolved with no explicit user_dirs
    found = ct.ghc_project_dir("/home/u/work/bp")
    # Then the override is searched (and found) without touching a real install
    assert found == user_dir / "workspaceStorage" / "envhash" / "GitHub.copilot-chat" / "transcripts"


def test_ghc_user_dirs_includes_the_vscode_server_default(monkeypatch):
    # Given no override set
    monkeypatch.delenv("BP_GHC_VSCODE_USER_DIR", raising=False)
    # When the candidate list is built
    dirs = ct._ghc_user_dirs()
    # Then the Linux/WSL-side default is always one of them
    assert Path.home() / ".vscode-server" / "data" / "User" in dirs


def test_listdir_safe_returns_entries_sorted(tmp_path):
    # Given a directory with two entries
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    # When it is listed
    # Then the entries come back sorted
    assert ct._listdir_safe(tmp_path) == [tmp_path / "a", tmp_path / "b"]


def test_listdir_safe_returns_empty_for_a_missing_directory(tmp_path):
    # Given a path that does not exist
    # When it is listed
    # Then it is simply empty, not an error -- one unusable candidate location
    # must not abort the search for the others
    assert ct._listdir_safe(tmp_path / "nope") == []


def test_listdir_safe_returns_empty_for_an_unreadable_directory(tmp_path):
    # Given a directory this process may not list
    d = tmp_path / "locked"
    d.mkdir()
    d.chmod(0o000)
    try:
        if os.access(d, os.R_OK):
            pytest.skip("cannot make a directory unreadable here (running as root?)")
        # When it is listed
        # Then it is empty rather than raising
        assert ct._listdir_safe(d) == []
    finally:
        d.chmod(0o755)


def test_ghc_transcripts_lists_jsonl_files_only(tmp_path):
    # Given a directory holding a transcript and an unrelated file
    (tmp_path / "s1.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    # When transcripts are listed
    found = sorted(p.name for p in ct.ghc_transcripts(tmp_path))
    # Then only the jsonl file is offered
    assert found == ["s1.jsonl"]


# --- GHC: CLI end to end -------------------------------------------------------
def test_cli_platform_ghc_end_to_end(tmp_path):
    # Given a GHC-shaped happy-path transcript, named explicitly
    p = ghc_happy_run(tmp_path)
    # When the CLI is invoked with --platform ghc and the techtest expectations
    r = run_cli(str(p), "--platform", "ghc",
                "--expect", "start:wf=techtest.md",
                "--expect", "step=1:actor=main",
                "--expect", "step=2:actor=techtest-echo",
                "--expect-delegations", "1")
    # Then it exits 0 and says PASS, exactly like the CC equivalent
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_cli_latest_scans_a_ghc_project_dir(tmp_path):
    # Given a GHC project dir holding one marked run
    ghc_happy_run(tmp_path, name="sess.jsonl")
    # When --latest --platform ghc is used against it
    r = run_cli("--latest", "1", "--project-dir", str(tmp_path), "--platform", "ghc",
                "--expect", "step=2:actor=techtest-echo")
    # Then the run is found and passes
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sess.jsonl" in r.stdout


# --- GHC: committed fixtures (documented schema, hand-composed contents) -----
def test_ghc_fixture_no_delegation_matches_the_transcript():
    # Given the committed GHC fixture that does every step itself
    run = ct.parse_ghc_run(DATA_GHC_NO_DELEGATION)
    # When parsed
    # Then the extraction matches what the file actually contains
    assert run.warnings == []
    assert [m.detail for m in run.start_markers] == [
        {"theme": "neon night", "wf": "techtest.md"}]
    assert [(m.detail["step"], m.detail["actor"], m.origin) for m in run.step_markers] == [
        ("1", "main", "main"), ("2", "main", "main")]
    assert run.delegations == []
    assert len(run.bash_calls) == 2


def test_ghc_fixture_delegation_merges_the_inline_subagent_events():
    # Given the committed GHC fixture that delegates step 2
    run = ct.parse_ghc_run(DATA_GHC_DELEGATION)
    # When parsed
    # Then the delegation and the subagent's own inline tool call are visible
    assert run.warnings == []
    assert [d.detail["to"] for d in run.delegations] == ["techtest-echo"]
    sub_bash = [e for e in run.bash_calls if e.origin == "subagent:techtest-echo"]
    assert len(sub_bash) == 1 and len(run.bash_calls) == 2


def test_ghc_fixture_delegation_passes_the_stage_b_expectations():
    # Given the same committed fixture and the stage-B recipe from the docstring
    run = ct.parse_ghc_run(DATA_GHC_DELEGATION)
    ordered = [ct.parse_expectation(s) for s in [
        "start:theme=neon night,wf=techtest.md",
        "step=1:actor=main,origin=main",
        "delegate:to=techtest-echo",
        "step=2:actor=techtest-echo,origin=subagent:techtest-echo"]]
    counted = [ct.parse_count_expectation(s) for s in ["start=1", "delegate=1"]]
    # When it is judged
    result = ct.evaluate(run, ordered, counted)
    # Then it passes -- the same invariant, extracted from the GHC schema
    assert result.passed, result.failures + result.anomalies
