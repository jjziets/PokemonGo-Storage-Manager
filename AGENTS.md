# AGENTS.md

## Codex Global Memory

This repo uses the global Codex memory system configured in `~/.codex/AGENTS.md`.

At session start:
- Read `~/.codex/memory/memory.md`.
- Read `~/.codex/memory/projects.md`.
- If a mapped project memory exists under `~/.codex/memory/projects/`, read its `MEMORY.md`.
- Load `~/.codex/memory/tools/beads.md` when using Beads.

Do not copy global memory rules into this repo; keep durable cross-project notes in `~/.codex/memory/`.

## Beads Issue Tracking

`bd` is installed globally and is available for dependency-aware task tracking.

Use Beads only if this repo has `.beads/`, this file is later updated to require Beads, or the user explicitly asks for `bd`/Beads work. When active:
- Run `bd prime` first.
- Use `bd ready --json`, `bd show <id> --json`, and other `--json` output for agent-readable state.
- Do not use `bd edit`; use `bd update` flags.
- Do not run `bd init` unless the user asks to initialize Beads for this repo.
