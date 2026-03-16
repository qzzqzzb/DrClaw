---
name: align-target-style
description: Convert a user style request into concrete rewrite constraints and apply that style during de-flavoring. Use when the user specifies a target tone, audience, or writing persona.
---

# Align Target Style

Use this skill after diagnosis and before final rewriting when the user provides an explicit style requirement.

This skill turns vague style requests into usable editing constraints.

## When to Use

Use this skill when the user says things like:

- rewrite this in a more natural blog style
- make it sound like a product manager
- keep it human but more casual
- remove AI flavor and make it sound like a sharp Chinese columnist

## Task

Convert the requested style into a small style card.

The style card should capture:

- tone
- formality level
- sentence rhythm
- degree of directness
- acceptable use of first person, humor, or rhetorical questions
- audience fit

## Rewrite Rules

- Treat the style request as a hard preference
- Keep the meaning stable
- Adapt diction, pacing, structure, and emphasis to the requested style
- Do not overperform the style into parody
- If the style request is underspecified, infer the simplest plausible version
- Prefer stable voice over obvious stylistic tricks

## Default If No Style Is Given

Do not use this skill when no style is given.

The agent default remains:

- natural
- concise
- grounded
- like a skilled Chinese writer

## Prompt Pattern

```text
Translate the user's style request into a compact style card for rewriting.
Specify tone, formality, rhythm, directness, and audience fit.
Then apply that style while making the prose sound authored and natural.
Preserve meaning and avoid exaggerated mimicry.
```
