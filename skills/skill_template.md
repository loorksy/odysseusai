---
name: skill_name
description: One sentence — what this skill does. This is ALL that loads into context by default.
category: general
tags: [narrow-tag-1, narrow-tag-2]
platforms: [all]
requires_toolsets: []
fallback_for_toolsets: []
when_to_use: |
  Describe the EXACT trigger condition: when should this skill be invoked?
  Include what adjacent tasks it does NOT cover, to prevent over-selection.
status: draft
version: 1.0.0
confidence: 0.8
source: user
---

## Instructions

<!-- Full step-by-step procedure. Loaded only when this skill is active. -->
<!-- Each step must be concrete and actionable. -->
<!-- Use <placeholder> for values that vary per invocation. -->

1. First step — name the specific tool, command, or action.
2. Second step — include exact parameters where they are always the same.
3. Third step — reference outputs from prior steps explicitly.

## Examples

<!-- Optional. Loaded only on first invocation or low-confidence runs. -->
<!-- Provide 1–2 concrete input→output examples. -->

**Example:**
Input: `<what the user provides>`
Expected output: `<what the skill produces>`

## Failure Modes

<!-- Known errors and their fixes. Fed to skill_refiner on failure. -->

- **Error pattern**: Description of when this fails and how to recover.
- **Common mistake**: What to watch out for and the correct alternative.

---
<!-- PROGRESSIVE DISCLOSURE CONTRACT -->
<!-- SkillRegistry loads ONLY name + description at startup (~53 tokens). -->
<!-- Full Instructions, Examples, and Failure Modes are injected on-demand. -->
<!-- NEVER put critical routing logic inside Instructions — keep it in when_to_use. -->
