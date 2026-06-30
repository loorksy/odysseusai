# ShadowRealm v2.0 Manual To-Do & Verification List

This checklist tracks manual steps, verification tasks, and checks that require human overview or local interface interactions as we build the remaining v2.0 sprints.

## 🛠️ Environment & App Launch
- [ ] Run the local FastAPI backend on `127.0.0.1:7000`.
- [ ] Log in using the admin account (`admin` / `adminpassword`) or bypass via loopback.
- [ ] Verify that `/api/ready` returns a `200` status.

## 🎨 CSS & UI Verification (Sprint 4 & 5)
- [ ] Open the app in a desktop browser and verify window resizing works without overlaps.
- [ ] Open the app on a mobile view (or responsive devtools) to check if media overrides match.
- [ ] Run through the onboarding tour helper to check for visual jumps or position clipping.
- [ ] Verify keyboard navigation (Tab-key outlines) on major UI control elements.
- [ ] Verify the 3-panel IDE layout: File Tree (left), Agent Chat (center), Preview/Terminal (right).

## 🧠 Memory & Integrations (Sprint 6)
- [ ] Test the Notion MCP server by inputting your personal page credentials in settings.
- [ ] Index an Obsidian vault folder and verify search hits come up in the memory panel.
- [ ] Review the memory tier visualization panel to confirm data is correctly bucketed (Hot/Warm/Cool).

## 💻 Developer Power Layer (Sprint 7)
- [ ] Confirm `docker-compose.yml` has the Open Hands container running on port `3000` (or configured port).
- [ ] Verify that the Open Hands iframe loads and communicates securely with the workspace.
- [ ] Test the live preview refresh action on a dummy HTML/JS file modification.

## 🤖 Teach Mode & Skill Factory (Sprint 8 & 8B)
- [ ] Turn on "Teach Mode" toggle in the header.
- [ ] Run a 3-turn interactive sequence, observing if clarifying questions are asked.
- [ ] Click "Crystallize to Skill" and verify the generated SKILL.md draft has valid YAML frontmatter.
- [ ] Audit quarantined skills and check the version rollback history.
- [ ] Verify and approve the nightly proposed reflection patches.
