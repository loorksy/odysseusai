---
name: skill_refiner
description: Patches a failing or underperforming SKILL.md using its error log or audit failure report.
category: meta
tags: [skill-factory, meta, self-healing, patch]
platforms: [all]
requires_toolsets: []
fallback_for_toolsets: []
when_to_use: |
  Invoke when a skill has failed an audit (verdict: fail or needs_work), when the user
  reports that a skill is not working, or when skill_creator output needs correction after
  a test run. Do NOT invoke this to create a new skill from scratch — use skill_creator
  instead. Do NOT invoke this for skills with verdict: pass unless there are specific
  metadata issues to fix.
status: published
version: 1.0.0
confidence: 0.95
source: system
---

## Instructions

You are patching an existing SKILL.md that has failed or underperformed.
Your constraint: **fix only what the error log identifies — never rewrite
working parts of the skill alongside broken parts.**

### Step 1 — Load the failing skill

Fetch the current skill source:
```
GET /api/skills/<name>/markdown
```
Also fetch the audit verdict / error log. This will be one of:
- An audit job result from `GET /api/skills/audit-all/status` (fields: `verdict`, `summary`, `issues`).
- A user-reported error description.
- A test transcript from `GET /api/skills/<name>/test-status`.

If neither is available, ask the user to describe what went wrong before proceeding.

### Step 2 — Classify the failure

Categorize each issue in the error log:

| Type | Examples | Fix |
|---|---|---|
| **Missing tool** | "tool X does not exist", "404 on endpoint" | Remove or replace the step with the correct tool/endpoint |
| **Vague step** | "too generic", "unclear what to do with X" | Rewrite the step with a concrete action + example |
| **Wrong sequence** | "step 3 requires output from step 5" | Reorder the procedure |
| **Metadata mismatch** | "tags too broad", "when_to_use doesn't match body" | Narrow tags; rewrite when_to_use |
| **Missing pitfall** | "failed because of X, not documented" | Add X to the Pitfalls section |
| **Non-reproducible** | "relied on live data / one-time token" | Replace with `<placeholder>` + Pitfall note |

### Step 3 — Apply the minimal patch

Rules:
1. Change ONLY the fields/steps identified as broken. Leave passing steps verbatim.
2. Never change the `name` field — it is the skill's filesystem identity.
3. If fixing metadata (tags, category, when_to_use, description): narrow, don't broaden.
4. Bump the patch version: `1.0.0` → `1.0.1`, `1.1.0` → `1.1.1`, etc.
5. Add a one-line comment in `body_extra` describing what was patched and why
   (e.g. `Patched: removed broken /api/v1/foo step — endpoint removed in v2.`).

### Step 4 — Save the patched skill

Write the complete corrected SKILL.md and call:
```
POST /api/skills/<name>/markdown
{ "markdown": "<full corrected content>" }
```

Confirm with the user: "Patched `<name>` v<new_version>. Changed: <one-line summary>."

### Step 5 — Trigger re-test (optional)

If the failure was functional (not metadata-only), offer to re-test:
```
POST /api/skills/<name>/test
{}
```
Poll `GET /api/skills/<name>/test-status` until `status == "done"`,
then report the new verdict.

## Failure Modes

- **No error log available**: Ask the user to describe the failure or run a
  test first (`POST /api/skills/<name>/test`) to generate a transcript.
- **Skill not found**: Verify the name with `GET /api/skills` before patching.
- **Patch makes things worse**: If the re-test verdict is worse than before,
  revert by re-POSTing the original markdown fetched in Step 1.
- **Structural corruption**: If the frontmatter is unparseable after patching,
  validate with `POST /api/skills/<name>/markdown` — it will return a 400 with
  the parse error. Fix the YAML frontmatter and retry.
