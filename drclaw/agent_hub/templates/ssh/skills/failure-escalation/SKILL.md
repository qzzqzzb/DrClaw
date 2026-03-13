---
name: failure-escalation
description: Terminate on unrecoverable external blockers and report precise error
  details.
always: true
i18n:
  zh:
    description: 用于failure escalation相关任务。
---

# Unrecoverable Error Policy

## Trigger Conditions

Treat these as unrecoverable unless new credentials/access are provided:

- Missing API key / auth token.
- No network connectivity or DNS resolution failures.
- Access denied to required external database/API/resource.
- Repeated authentication or authorization failures.

## Required Behavior

1. Stop execution immediately when an unrecoverable blocker is confirmed.
2. Do not continue with fabricated or knowingly incomplete final results.
3. Report:
   - exact error message,
   - what was attempted,
   - what dependency is missing/inaccessible,
   - the minimum next action needed from caller/user.
4. Wait for the next instruction after reporting.

## Equipment Agent Specific

- Call `report_to_caller` with `status=\"failed\"` and a detailed `summary`.
- Include structured `artifacts` fields for error context when possible
  (for example `error_type`, `missing_dependency`, `endpoint`, `retry_hint`).
