---
name: skill_creator
description: Writes a new SKILL.md file from a successful workflow trace captured in the current session.
category: meta
tags: [skill-factory, meta, trace-to-skill, automation]
platforms: [all]
requires_toolsets: []
fallback_for_toolsets: []
when_to_use: |
  Invoke when the user says "write the skill", "save this as a skill", or "crystallize this"
  after a workflow has been completed successfully in the current conversation. Do NOT invoke
  this if the workflow failed, is still in progress, or if the user is asking about an
  existing skill. Use skill_refiner instead when patching a failing skill.
status: published
version: 1.0.0
confidence: 0.95
source: system
---

## Instructions

You are writing a new reusable SKILL.md from a workflow the user just completed
successfully in this session. Your goal is a skill that a future agent can follow
blindfoldly to reproduce the same outcome.

### Step 1 — Identify the workflow

Review the conversation trace above. Locate the segment where:
- A clear goal was stated or implied.
- You took a sequence of actions (tool calls, reasoning steps, edits).
- The user confirmed success or the outcome was unambiguous.

If the trace contains multiple workflows, ask the user which one to crystallize
before proceeding. If the trace is ambiguous, ask one clarifying question.

### Step 2 — Extract the procedure

From the successful trace, extract:
1. **Trigger** — the exact conditions or user intent that should invoke this skill.
2. **Procedure** — the ordered, concrete steps taken. Each step must be actionable:
   - Name the specific tool, command, or action used.
   - Include exact parameters where they are always the same; use `<placeholder>` for values that vary.
   - Do NOT paraphrase — if a specific API endpoint, file path, or flag was used, include it.
3. **Pitfalls** — any errors, dead ends, or wrong turns encountered during the trace.
4. **Verification** — how success was confirmed (output pattern, status code, user confirmation, etc.).

### Step 3 — Write the frontmatter

Choose metadata that will make retrieval NARROW and PRECISE:
- `name`: lowercase, hyphen-separated, ≤6 words. Derived from the action + subject (e.g. `git-squash-commits`, `add-chromadb-service`).
- `description`: one sentence, ≤200 chars. Describes WHAT it does, not when.
- `category`: one of: `git`, `docker`, `python`, `api`, `research`, `memory`, `agent`, `ui`, `data`, `system`, `meta`, `general`.
- `tags`: 2–5 narrow tags. Avoid broad tags like `python`, `system`, `linux` unless the skill is ONLY for that platform.
- `when_to_use`: 2–4 sentences. Describe the exact trigger condition AND explicitly state what adjacent tasks this skill does NOT cover.
- `platforms`: list only if the skill is platform-specific; otherwise `[all]`.

### Step 4 — Output the SKILL.md

Write the complete file following the canonical template (`skill_template.md`).
Output ONLY the raw SKILL.md content — no fences, no commentary, no preamble.

After writing, call `POST /api/skills/add` with the parsed fields, or instruct
the user to save the output as `skills/<name>.md` if direct API access is unavailable.

## Failure Modes

- **Trace too vague**: If no clear sequence of actions exists, tell the user
  "I can't identify a complete workflow in this session. Walk me through the
  steps and I'll write the skill from that."
- **Multiple workflows in trace**: Ask the user which to crystallize before writing.
- **Skill already exists**: Before writing, check `GET /api/skills` for a skill
  with the same name or >60% semantic overlap. If found, suggest using
  `skill_refiner` to improve the existing skill instead.
- **Procedure non-reproducible**: If a step relied on live data (e.g. a one-time
  token, a specific file that won't exist again), flag it and replace with a
  generic `<placeholder>` + a note in Pitfalls.
