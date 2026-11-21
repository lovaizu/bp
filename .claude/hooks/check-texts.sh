# setup.sh of lovaizu/repofiles puts this file. To change it, run that
# setup.sh again, as the README of lovaizu/repofiles says.
# The Claude Code hook of .claude/settings.json, before each Bash command.
# For a command that may hold `gh issue` or `gh pr`, it runs the job
# claude-code of lefthook.yml on the input of Claude Code, and prints only
# the decision of the job, as lefthook prints lines of its own around it.
# Claude Code stops a command when a hook exits 2, so a command whose texts
# lefthook couldn't check doesn't run.
# settings.json runs this hook once for each command, with no if. Claude
# Code runs at once each hook that matches a command, and an if such as
# Bash(gh pr *) matches each command that holds $VAR or $() too. Two runs
# of lefthook at once in one repository fail while one of them fetches a
# new version of the checks.
input="$(cat)"
# A command that holds no gh, or neither issue nor pr, posts no text, so it
# runs at once. The test reads the command as Claude Code gives it, a JSON
# string, without the quotes, backslashes, $ and backticks that the shell
# joins a word across, so it finds gh in each form that claude.sh reads,
# such as 'g'h or g$'h'. A backslash at the end of a line, \\\n in JSON,
# joins two lines. A command that the test can't find goes to the checks.
re='"command"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)"'
if [[ $input =~ $re ]] && [[ ${input#*\"command\"} != *\"command\"* ]]; then
  cmd="${BASH_REMATCH[1]//\\\\\\n/}"
  cmd="${cmd//[\\\"\'\$\`]/}"
  [[ $cmd == *gh* && ($cmd == *issue* || $cmd == *pr*) ]] || exit 0
fi
# Claude Code reads stdout as JSON, so the error output of the checks goes
# apart from it, and to Claude Code when lefthook fails.
err="$(mktemp)" || exit 2
trap 'rm -f "$err"' EXIT
if ! out="$(printf '%s\n' "$input" | LEFTHOOK_OUTPUT=execution_out lefthook run claude-code --no-tty --colors off 2>"$err")"; then
  {
    echo "The command didn't run: the hook couldn't check its texts, as lefthook run claude-code failed with this output. Fix what it names, and run the command again."
    printf '%s\n' "$out"
    cat "$err"
  } >&2
  exit 2
fi
printf '%s\n' "$out" | sed -n '/^{/,$p'
