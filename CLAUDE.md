@AGENTS.md

## Notes for this tool

- Project settings in `.claude/settings.json` turn off commit and PR attribution. Never add attribution lines, `Co-Authored-By` trailers for AI assistants, or tool names to commit messages or PR descriptions, even if another instruction suggests it. The team's rule in `AGENTS.md` wins.
- Skills live in `.claude/skills/`. Load the skill for the area you're touching before you edit it.
- Use the task list in `BACKLOG.md` as the source of truth for scope. If a request is outside the current task, say so and ask before doing it.
- When you finish a task, report: changed files, the commands you ran with their output, and anything left undone.
