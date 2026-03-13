---
name: local-skill-hub
description: Manage dormant local skill templates and attach them to equipment agents.
---

# Local Skill Hub

Use this when the user wants reusable local skills that are stored but not auto-activated.

## Storage model

- Local hub path: `~/.drclaw/local-skill-hub/<category>/<skill>/SKILL.md`
- Default category for uncategorized skills: `unarchived/`
- Hub skills are inert templates.
- Equipment agents only use skills copied into:
  `~/.drclaw/equipments/<equipment>/skills/<skill>/SKILL.md`
- Category metadata file (recommended): `~/.drclaw/local-skill-hub/<category>/CATEGORY.md`

## Main tools

1. `list_local_skill_hub_skills`
2. `list_local_skill_hub_categories`
3. `set_local_skill_hub_category_metadata`
4. `import_skill_to_local_hub`
5. `add_local_hub_skills_to_equipment`
6. `add_equipment` with `local_hub_skills`

## Recommended flow

1. Define category semantics with `set_local_skill_hub_category_metadata`.
2. Import templates into explicit categories (or leave them in `unarchived/`).
3. Create a new equipment prototype and seed skills with `local_hub_skills`.
4. For existing equipment, copy additional hub skills with
   `add_local_hub_skills_to_equipment`.
5. Confirm with `list_equipments` and optionally inspect files.
