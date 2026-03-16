---
name: memory
description: Two-layer memory system with grep-based recall.
i18n:
  zh:
    name: 记忆
    description: 双层记忆，支持grep检索。
always: true
---

# Memory

## Structure

- `MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded into your context.
- `HISTORY.md` — Append-only event log. NOT loaded into context. Search it with grep. Each entry starts with [YYYY-MM-DD HH:MM].

## Search Past Events

Use the `exec` tool to run grep: `grep -i "keyword" HISTORY.md`
Combine patterns: `grep -iE "meeting|deadline" HISTORY.md`

## When to Update MEMORY.md

Write important facts immediately using `edit` or `write`:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Key relationships ("Alice is the project lead")

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md when the session grows large. Long-term facts are extracted to MEMORY.md. You don't need to manage this.
