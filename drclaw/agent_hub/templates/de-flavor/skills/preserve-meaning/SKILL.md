---
name: preserve-meaning
description: Guard against meaning drift during de-flavor rewrites. Use when rewriting text to sound more natural without adding facts, changing claims, or losing important constraints.
---

# Preserve Meaning

Use this skill during and after rewriting.

Its purpose is simple: improve the writing without changing what the text actually says.

## When to Use

Use this skill whenever you rewrite user text, especially when the text contains:

- concrete claims
- numbers, dates, names, or technical terms
- legal, academic, or business wording
- constraints the user expects to keep
- rewrites that become more vivid or more stylistic

## Meaning Guardrails

- Do not add new facts, examples, evidence, or opinions
- Do not remove important qualifiers or limits
- Do not strengthen weak claims into strong claims
- Do not weaken strong claims unless the original text is already uncertain
- Preserve numbers, names, terminology, and core logic
- Keep the rhetorical intent intact even if sentence shape changes
- Keep the degree of certainty intact

## Editing Strategy

You may change:

- wording
- sentence rhythm
- paragraph flow
- explicit transition phrases
- list-to-prose conversion when appropriate

You may not change:

- factual payload
- causality
- stance
- scope
- commitments

## Self-Check

Before finalizing, ask:

1. What does the original text claim?
2. Does the rewrite claim the same thing?
3. Did I introduce any new implication that was not present before?
4. Did I accidentally delete a boundary, hedge, or constraint?

If any answer is risky, revise again.

## Prompt Pattern

```text
Rewrite for naturalness and stronger authorship, but preserve the original meaning exactly.
Do not add facts, examples, arguments, or stronger conclusions.
Keep names, numbers, constraints, and technical content intact.
After rewriting, compare the new version against the original for meaning drift.
```
