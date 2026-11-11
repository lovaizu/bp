# setup.sh of lovaizu/repofiles puts this file. To change it, run that
# setup.sh again, as the README of lovaizu/repofiles says.
# The Claude Code hook of .claude/settings.json, before each `gh issue` and
# `gh pr` command. It runs the job claude-code of lefthook.yml on the input
# of Claude Code, and prints only the decision of the job, as lefthook
# prints lines of its own around it. Claude Code stops a command when a hook
# exits 2, so a command whose texts lefthook couldn't check doesn't run.
if ! out="$(LEFTHOOK_OUTPUT=execution_out lefthook run claude-code --no-tty --colors off 2>/dev/null)"; then
  echo "The text of this command wasn't checked, as lefthook couldn't run the checks. Fix: install lefthook, and run lefthook install in this repository." >&2
  exit 2
fi
printf '%s\n' "$out" | sed -n '/^{/,$p'
