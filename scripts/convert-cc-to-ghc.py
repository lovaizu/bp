#!/usr/bin/env python3
"""
CC → GHC conversion script.
Reads .claude/ files, applies mapping rules, writes .github/ files.

Usage: python3 scripts/convert-cc-to-ghc.py [--dry-run]
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = Path(__file__).resolve().parent / "mapping-rules.json"


def load_rules():
    with open(RULES_PATH) as f:
        return json.load(f)


def parse_frontmatter(text):
    """Parse YAML frontmatter from markdown. Returns (fields_dict, body_text)."""
    if not text.startswith("---"):
        return {}, text

    end = text.index("---", 3)
    fm_text = text[3:end].strip()
    body = text[end + 3:].lstrip("\n")

    fields = {}
    current_key = None
    current_value_lines = []

    for line in fm_text.split("\n"):
        # Multi-line value continuation (indented under previous key)
        if current_key and (line.startswith("  ") or line.startswith("\t")):
            current_value_lines.append(line.strip())
            continue

        # Flush previous key
        if current_key:
            fields[current_key] = " ".join(current_value_lines)
            current_key = None
            current_value_lines = []

        # New key-value pair
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == ">" or val == "|":
                current_key = key
                current_value_lines = []
            elif val:
                fields[key] = val
            else:
                fields[key] = ""

    if current_key:
        fields[current_key] = " ".join(current_value_lines)

    return fields, body


def render_frontmatter(fields):
    """Render fields dict back to YAML frontmatter string."""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif "\n" in str(value) or len(str(value)) > 72:
            lines.append(f"{key}: >")
            lines.append(f"  {value}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def convert_tools(tools_str, rules):
    """Convert CC tool names to GHC tool names. Returns YAML list string."""
    tool_map = rules["tool_names"]
    cc_tools = [t.strip() for t in tools_str.split(",")]
    ghc_tools = []
    seen = set()
    for t in cc_tools:
        mapped = tool_map.get(t, t)
        if mapped not in seen:
            ghc_tools.append(mapped)
            seen.add(mapped)
    return ghc_tools


def convert_model(model_str, rules):
    """Convert CC model name to GHC model name."""
    return rules["model_names"].get(model_str.strip(), model_str.strip())


def convert_body_paths(body, rules):
    """Replace CC paths with GHC paths in body text."""
    for cc_path, ghc_path in rules["path_replacements"].items():
        body = body.replace(cc_path, ghc_path)
    return body


def convert_command(text, rules):
    """Convert a CC command file to a GHC prompt file."""
    fields, body = parse_frontmatter(text)

    fm_rules = rules["frontmatter"]["command"]
    for f in fm_rules["remove_fields"]:
        fields.pop(f, None)
    for k, v in fm_rules["add_fields"].items():
        fields[k] = v

    if "allowed-tools" in fields:
        raw = fields.pop("allowed-tools")
        fields["tools"] = convert_tools(raw, rules)
    elif "tools" in fields and isinstance(fields["tools"], str):
        fields["tools"] = convert_tools(fields.pop("tools"), rules)

    if "model" in fields:
        fields["model"] = convert_model(fields["model"], rules)

    # $ARGUMENTS handling: remove the line, GHC appends user input automatically
    body_lines = []
    for line in body.split("\n"):
        if "$ARGUMENTS" in line:
            continue
        body_lines.append(line)
    body = "\n".join(body_lines)

    body = convert_body_paths(body, rules)

    return render_frontmatter(fields) + "\n" + body


def convert_agent(text, rules):
    """Convert a CC agent file to a GHC agent file."""
    fields, body = parse_frontmatter(text)

    fm_rules = rules["frontmatter"]["agent"]
    for f in fm_rules["remove_fields"]:
        fields.pop(f, None)
    for k, v in fm_rules["add_fields"].items():
        fields[k] = v

    if "tools" in fields:
        fields["tools"] = convert_tools(fields["tools"], rules)

    if "model" in fields:
        fields["model"] = convert_model(fields["model"], rules)

    body = convert_body_paths(body, rules)

    return render_frontmatter(fields) + "\n" + body


def convert_skill(text, rules):
    """Convert a CC skill file to a GHC skill file."""
    fields, body = parse_frontmatter(text)

    if "tools" in fields or "allowed-tools" in fields:
        raw = fields.pop("allowed-tools", fields.pop("tools", ""))
        if raw:
            fields["tools"] = convert_tools(raw, rules)

    if "model" in fields:
        fields["model"] = convert_model(fields["model"], rules)

    body = convert_body_paths(body, rules)

    return render_frontmatter(fields) + "\n" + body


def convert_workflow(text, rules):
    """Convert a CC workflow file to a GHC workflow file. Path replacement only."""
    fields, body = parse_frontmatter(text)
    body = convert_body_paths(body, rules)
    if fields:
        return render_frontmatter(fields) + "\n" + body
    return body


def process_file(cc_path, ghc_path, file_type, rules, dry_run):
    """Read CC file, convert, write to GHC path."""
    with open(cc_path) as f:
        text = f.read()

    converters = {
        "command": convert_command,
        "agent": convert_agent,
        "skill": convert_skill,
        "workflow": convert_workflow,
        "resource": None,
    }

    converter = converters.get(file_type)

    if converter is None:
        # Resource files: copy as-is
        if dry_run:
            print(f"  [copy] {cc_path} -> {ghc_path}")
            return
        ghc_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cc_path, ghc_path)
        print(f"  [copy] {cc_path.relative_to(REPO_ROOT)} -> {ghc_path.relative_to(REPO_ROOT)}")
        return

    converted = converter(text, rules)

    if dry_run:
        print(f"  [{file_type}] {cc_path.relative_to(REPO_ROOT)} -> {ghc_path.relative_to(REPO_ROOT)}")
        print(f"    --- preview (first 10 lines) ---")
        for i, line in enumerate(converted.split("\n")[:10]):
            print(f"    {line}")
        print()
        return

    ghc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ghc_path, "w") as f:
        f.write(converted)
    print(f"  [{file_type}] {cc_path.relative_to(REPO_ROOT)} -> {ghc_path.relative_to(REPO_ROOT)}")


def main():
    dry_run = "--dry-run" in sys.argv
    rules = load_rules()
    structs = rules["file_structure"]
    exts = rules["file_extensions"]

    if dry_run:
        print("=== DRY RUN (no files will be written) ===\n")
    else:
        print("=== Converting CC -> GHC ===\n")

    # 1. Commands -> Prompts
    cc_cmd_dir = REPO_ROOT / structs["commands_dir"]["cc"]
    ghc_cmd_dir = REPO_ROOT / structs["commands_dir"]["ghc"]
    if cc_cmd_dir.exists():
        print("Commands -> Prompts:")
        for f in sorted(cc_cmd_dir.glob("*.md")):
            stem = f.stem
            ghc_name = stem + exts["command"]["ghc"]
            process_file(f, ghc_cmd_dir / ghc_name, "command", rules, dry_run)

    # 2. Agents
    cc_agent_dir = REPO_ROOT / structs["agents_dir"]["cc"]
    ghc_agent_dir = REPO_ROOT / structs["agents_dir"]["ghc"]
    if cc_agent_dir.exists():
        print("Agents:")
        for f in sorted(cc_agent_dir.glob("*.md")):
            stem = f.stem
            ghc_name = stem + exts["agent"]["ghc"]
            process_file(f, ghc_agent_dir / ghc_name, "agent", rules, dry_run)

    # 3. Skills (walk recursively)
    cc_skills_dir = REPO_ROOT / structs["skills_dir"]["cc"]
    ghc_skills_dir = REPO_ROOT / structs["skills_dir"]["ghc"]
    if cc_skills_dir.exists():
        print("Skills:")
        for cc_file in sorted(cc_skills_dir.rglob("*")):
            if not cc_file.is_file():
                continue
            rel = cc_file.relative_to(cc_skills_dir)
            ghc_file = ghc_skills_dir / rel

            # Determine file type
            if cc_file.name == "SKILL.md":
                file_type = "skill"
            elif "workflows" in str(rel):
                file_type = "workflow"
            else:
                file_type = "resource"

            process_file(cc_file, ghc_file, file_type, rules, dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
