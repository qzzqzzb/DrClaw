---
name: cron
description: Schedule and manage recurring or one-time jobs for main/project agents.
---

# Cron

Use the `cron` tool to create, list, remove, enable/disable, and run scheduled jobs.

## Actions

- `add`: create a job
- `list`: show jobs
- `remove`: delete a job by id
- `enable`: enable or disable by id
- `run`: run a job now

## Add Examples

Daily at 8:00:
```
cron(action="add", message="Report yesterday's progress", cron_expr="0 8 * * *")
```

Every 30 minutes:
```
cron(action="add", message="Check project status", every_seconds=1800)
```

Target a student/project agent:
```
cron(action="add", target="proj:project-id", message="Summarize blockers", cron_expr="0 9 * * 1-5")
```

One-time reminder:
```
cron(action="add", message="Prepare meeting notes", at="2026-03-10T14:00:00")
```

## Notes

- If `target` is omitted, default is `main`.
- For `add`, provide exactly one of: `every_seconds`, `cron_expr`, `at`.
- `tz` can be used only with `cron_expr`.
