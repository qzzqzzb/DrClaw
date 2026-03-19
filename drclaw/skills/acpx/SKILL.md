---
name: acpx
description: Use the ACPX CLI through DrClaw's existing exec/long_exec tools to run Codex in the current project workspace.
always: false
metadata: {"openclaw":{"requires":{"bins":["acpx"]},"install":["Install ACPX globally so agents can run `acpx ...` from exec/long_exec."]}}
---

# ACPX

Use this skill when you need Codex through the standard `acpx` CLI.

## Important

- In DrClaw, there is no dedicated ACPX tool.
- You must use the existing `exec` or `long_exec` tools to run `acpx` commands.
- Prefer `long_exec` unless the command is obviously short; ACPX turns can exceed the normal `exec` timeout.
- Run ACPX from the current project workspace or a subdirectory inside it.

## Recommended Workflow

### One-shot task

```bash
acpx codex exec '<instruction>'
```

Use this for a single request where you do not need follow-up turns.

### Persistent session

Create or recover a named session:

```bash
acpx codex sessions ensure --name drclaw-proj-<project-id>-<task-suffix>
```

Send a turn to that session:

```bash
acpx codex -s drclaw-proj-<project-id>-<task-suffix> '<instruction>'
```

Check status:

```bash
acpx codex -s drclaw-proj-<project-id>-<task-suffix> status
```

Cancel the current turn:

```bash
acpx codex -s drclaw-proj-<project-id>-<task-suffix> cancel
```

Close the session when it is no longer needed:

```bash
acpx codex sessions close drclaw-proj-<project-id>-<task-suffix>
```

## Session Discipline

- Reuse stable session names for ongoing tasks instead of creating duplicates.
- For sessions created by DrClaw project agents, always use the prefix `drclaw-proj-<project-id>-`.
- Before starting a new persistent session, check whether the same task already has one.
- If a command fails, first verify:
  - `acpx` is installed
  - you are in the intended workspace
  - the session name exists if using `-s`

## When To Use Which Tool

- Use `exec` only for very short ACPX commands.
- Use `long_exec` for `exec`, persistent sessions, or anything that may take more than a minute.
