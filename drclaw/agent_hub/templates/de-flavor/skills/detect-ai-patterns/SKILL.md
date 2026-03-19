---
name: detect-ai-patterns
description: Diagnose formulaic writing patterns and authorial gaps before rewriting. Use when text feels generic, over-smoothed, abstract, or structurally mechanical and you need a targeted edit plan.
---

# Diagnose Writing Patterns

Use this skill at the start of a de-flavoring task.

Its job is not to rewrite. Its job is to identify what makes the text feel generic, unowned, or mechanically produced so later edits can stay targeted.

## When to Use

Use this skill when:

- the user asks to "remove AI flavor" from a passage
- the text feels formulaic but you need a crisp diagnosis first
- the user explicitly asks why a passage sounds stiff, generic, or AI-like

## What to Look For

Scan for patterns such as:

- template openings and empty framing
- rigid symmetry like "first, second, finally"
- stacked connectors that make logic feel mechanical
- repeated sentence shapes or paragraph cadence
- abstract, inflated wording with low information density
- generic authority markers and fake profundity
- canned warmth, canned empathy, or canned uplift
- excessive list structure where prose would be more natural
- translated-from-English Chinese phrasing
- weak authorial judgment
- places where the real point is buried under framing

## Output

Keep the diagnosis short and actionable.

Recommended structure:

1. `main_patterns`: the top 2-5 patterns
2. `severity`: low / medium / high
3. `rewrite_focus`: what the next stage should prioritize
4. `specificity_gaps`: where the text needs stronger grounding or sharper wording

## Rules

- Quote only short trigger fragments when needed
- Do not start rewriting in this stage unless the user asked for a combined result
- Do not claim a text is "definitely AI-generated"
- Frame this as pattern diagnosis, not authorship detection
- Focus on revision priorities, not detector language

## Prompt Pattern

Use a compact internal instruction shaped like this:

```text
Read the passage and identify the strongest formulaic writing patterns.
Focus on repeated structures, stacked transitions, empty significance claims,
over-smoothing, low-information abstraction, and weak authorial judgment.
Return only the main patterns, a severity level, specificity gaps,
and rewrite priorities.
Do not rewrite yet.
```
