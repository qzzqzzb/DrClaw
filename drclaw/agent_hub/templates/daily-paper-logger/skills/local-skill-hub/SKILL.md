---
name: local-skill-hub
description: Manage dormant local skill templates and attach them to project agents
  or equipment agents.
i18n:
  zh:
    name: 本地技能库
    description: 管理本地休眠技能模板，绑定至项。
---

# Local Skill Hub

Use this when the user wants reusable local skills that are stored but not auto-activated.

## Storage model

- Local hub path: `~/.drclaw/local-skill-hub/<category>/<skill>/SKILL.md`
- Default category for uncategorized skills: `unarchived/`
- Hub skills are inert templates.
- Equipment agents use skills copied into:
  `~/.drclaw/equipments/<equipment>/skills/<skill>/SKILL.md`
- Project agents (students) use skills copied into:
  `~/.drclaw/projects/<project_id>/workspace/skills/<skill>/SKILL.md`
- Category metadata file (recommended): `~/.drclaw/local-skill-hub/<category>/CATEGORY.md`

## Main tools

1. `list_local_skill_hub_skills`
2. `list_local_skill_hub_categories`
3. `set_local_skill_hub_category_metadata`
4. `import_skill_to_local_hub`
5. `add_local_hub_skills_to_equipment`
6. `add_local_hub_skills_to_project`
7. `add_equipment` with `local_hub_skills`

## Recommended flow

1. Define category semantics with `set_local_skill_hub_category_metadata`.
2. Import templates into explicit categories (or leave them in `unarchived/`).
3. Choose the target agent type:
   - Equipment path: create a new equipment prototype and seed skills with `local_hub_skills`.
   - Project path: copy skills into a project workspace with `add_local_hub_skills_to_project`.
4. For existing equipment, copy additional hub skills with
   `add_local_hub_skills_to_equipment`.
5. Confirm:
   - Equipment targets: `list_equipments`
   - Project targets: inspect `~/.drclaw/projects/<project_id>/workspace/skills/`
