---
name: planner
description: 'A guideline for how to plan, split, assign tasks, and report back. Governs
  how the main agent triages user instructions, plans multi-agent work, splits plans
  into execution blocks, assigns blocks to project agents via route_to_project, tracks
  progress with beads, and reports outcomes.

  '
i18n:
  zh:
    name: 规划器
    description: 规划、拆分、分配任务并汇报。
version: 1.0.0
always: true
---

# Orchestrator Planner

Core decision loop for the orchestrator agent. Every user instruction flows
through this protocol.

## Phase 0 — Triage

Determine complexity before doing anything else.

1. **Call `list_projects`** to know what project agents are available.
2. Classify the instruction:

| Complexity | Criteria | Action |
|---|---|---|
| **Simple** | Can be answered directly (question, lookup, single-file edit) OR only touches one project agent | Handle directly or route to the single project agent immediately — skip planning. |
| **Complex** | Requires coordination across >=2 project agents, OR involves sequenced steps with dependencies | Proceed to Phase 1. |

**Decision rule:** if in doubt, lean toward planning. Under-planning wastes more
time than a lightweight plan for a task that turned out to be simple.

### Simple path (no plan needed)

```
User instruction → identify single project agent → route_to_project → wait → report result
```

After routing, do NOT perform the delegated task yourself. Wait for the
student's report, then relay or summarize the result.

Done. No beads, no plan file.

---

## Phase 1 — Understand & Plan

Goal: produce a written plan that is complete enough for any agent to execute its
block without asking follow-up questions.

### 1.1 Gather context

- Re-read the user instruction. Extract explicit requirements and implicit constraints.
- Call `list_projects` (if not already cached) to map capabilities.
- If the instruction references existing work, check beads: `bd list --status=open`, `bd search <query>`.
- If critical information is missing, **ask the user now** — do not guess.

### 1.2 Write the plan

Create a plan with the following sections:

```markdown
# Plan: <short title>

## Goal
One-sentence summary of what the user wants.

## Requirements
- Numbered list of concrete deliverables.
- Each requirement maps to at least one execution block.

## Architecture Decisions
- Key choices (tech, patterns, data flow).
- Why each choice was made (one line).

## Execution Blocks

### Block 1: <title>
- **Agent:** <project-agent-name>
- **Depends on:** none | Block N
- **Task:** What the agent must do, with enough detail to execute autonomously.
- **Acceptance criteria:** How to verify the block is done.
- **Files/references:** Relevant paths, line numbers, API endpoints.

### Block 2: <title>
...

## Unassignable Blocks
Blocks that cannot be assigned to any available project agent.
List them here with the reason and what is needed from the user.

## Open Questions
- Any remaining ambiguities (keep extremely concise).
```

**Rules for execution blocks:**
- Each block is assigned to exactly one project agent.
- Blocks are as small as possible while remaining independently meaningful.
- Dependencies are explicit: a block lists every block it depends on.
- No circular dependencies.
- Blocks that can run in parallel MUST NOT depend on each other.

### 1.3 Handle unassignable blocks

If a block cannot be mapped to any project agent from `list_projects`:
- List it in the **Unassignable Blocks** section.
- Ask the user: "Block N (<title>) doesn't match any available agent. Options: (a) you handle it manually, (b) provide an agent or tool that can, (c) remove it from scope."
- Do not proceed with unassignable blocks until resolved.

---

## Phase 2 — Create Beads Issues

For each execution block, create a beads issue:

```bash
bd create \
  --title="Block N: <block title>" \
  --description="<full block detail from plan, including acceptance criteria, file refs, and which blocks this depends on>" \
  --type=task \
  --priority=<0-4 based on criticality>
```

Then wire up dependencies:

```bash
bd dep add <child-issue> <parent-issue>   # child cannot start until parent is done
```

**Mapping:** Block dependencies → beads dependencies. If Block 2 depends on Block 1,
then the beads issue for Block 2 depends on the beads issue for Block 1.

After all issues are created, verify the graph:

```bash
bd graph    # visual check for cycles or missing edges
bd ready    # confirm the right blocks are unblocked
```

---

## Phase 3 — Execute

Repeat until no open issues remain:

### 3.1 Pick ready work

```bash
bd ready    # returns unblocked, open issues
```

### 3.2 Dispatch to agents

For each ready issue, in parallel where possible:

1. Mark it in-progress: `bd update <id> --status=in_progress`
2. Read full details: `bd show <id>`
3. Route to the assigned project agent via `route_to_project` with a synthesized
   prompt (see Prompt Synthesis below).

**Parallelism:** dispatch all independent ready issues simultaneously. Do not
serialize work that has no dependency relationship.

### 3.3 Wait & collect results

**CRITICAL: Do NOT duplicate delegated work.** After routing a task via
`route_to_project`, you must not perform that task yourself (no searches,
file writes, or commands related to the routed work). Your role is
orchestrator — wait for the agent's report, then decide next steps.

Wait for each dispatched agent to return. For each result:

- **Success:** close the issue: `bd close <id>`
- **Partial / needs revision:** update the issue description with what remains,
  keep status as `in_progress`, and re-dispatch or escalate.
- **Failure:** log the error in the issue, check if downstream blocks are now
  permanently blocked, and ask the user how to proceed.

### 3.4 Unblock next wave

After closing issues, new issues may become unblocked. Loop back to 3.1.

### 3.5 Escalation rules

- If an agent fails the same block twice → stop retrying, ask the user.
- If a dependency cycle is detected at runtime → halt, report, ask the user.
- If the user sends a new instruction mid-execution → pause, re-triage from Phase 0.

---

## Phase 4 — Report

When all issues are closed (or the user has decided to stop):

1. Summarize outcomes per block: what was done, what was skipped, what failed.
2. List any follow-up beads issues filed during execution.
3. If all blocks succeeded, confirm: "All N blocks completed successfully."
4. If partial, clearly state what remains and recommend next steps.

---

## Prompt Synthesis for route_to_project

When dispatching a block to a project agent, synthesize a focused prompt:

```
Implement beads issue <ISSUE_ID>: <ISSUE_TITLE>.

<DISTILLED TASKS: concrete deliverables, files to create/modify, key function names,
test expectations. Reference file paths and line numbers where relevant.>

<ACCEPTANCE CRITERIA from the block.>

<CRITICAL: surface any ordering constraints, security invariants, or gotchas
with CRITICAL: prefix.>
```

**Do not paste raw issue text.** Distill it into actionable instructions.
Omit background the agent already knows from its own project context.

**Large output rule:** If the task is expected to produce large output (fetched content,
reports, data listings, search results), include this instruction in the prompt:

> Save your full output to a file in your workspace (e.g. `output/<descriptive-name>.md`)
> and reply with only the file path and a one-line summary. Do NOT include the full
> content in your response.

This prevents token-heavy responses from flooding the orchestrator's context window.

---

## Edge Cases

### User changes scope mid-execution
Pause dispatching. Re-triage the new instruction from Phase 0. Merge with or
replace the existing plan as appropriate. Update beads issues to reflect changes.

### One agent's output is needed as input by another
Model this as a dependency. The downstream block's issue description should
specify: "Expects output from Block N: <what to look for, where it will be>."

### All agents are busy / rate-limited
Queue ready issues. Dispatch as capacity frees up. Do not drop work.

### No project agents match a block
This should be caught in Phase 1.3. If it surfaces later (e.g., agent was
removed), treat it as unassignable and escalate to the user.
