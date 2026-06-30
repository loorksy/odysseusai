# learning_mode

## Description
Toggle continuous reflection mode — when active, every skill execution trace feeds the Skill Factory improvement loop via ReflectionEngine.

## Trigger
Invoke when the user says: "enable learning mode", "start continuous reflection", "disable learning mode", "stop learning", "toggle learning mode", "is learning mode on?", or any request about the self-improvement loop being active or paused.

## Instructions

### Check current state
```
GET /api/learning-mode
```
Returns `enabled` (bool), activation timestamps, and cumulative counters for reflection cycles, proposals generated, and skills improved.

### Enable
```
POST /api/learning-mode/enable
Body: { "actor": "<username or agent name>" }
```
- Sets `enabled = true` and records activation timestamp.
- From this point, all skill execution traces are automatically queued for nightly ReflectionEngine analysis.
- ReflectionEngine generates improvement proposals → routed to human review queue (C87–C88).

### Disable
```
POST /api/learning-mode/disable
Body: { "actor": "<username or agent name>" }
```
- Sets `enabled = false`.
- ReflectionEngine completes its current nightly cycle then stops queuing new proposals.
- Historical data and counters are preserved.

### Toggle
```
POST /api/learning-mode/toggle
Body: { "actor": "<username or agent name>" }
```
Flips enabled ↔ disabled. Useful for quick on/off from UI or CLI.

### State is shared
All agents (ShadowCoder, ShadowResearcher, ShadowOps, etc.) read the same learning mode state. There is no per-agent learning mode.

### State persists across restarts
State is written to `data/learning_mode.json` on every change. The engine reloads it on startup.

## Examples

**User:** "Turn on learning mode."
→ `POST /api/learning-mode/enable` with `{ "actor": "user" }`
→ Respond: "Learning mode is now active. Every skill execution will be observed and queued for overnight reflection."

**User:** "Is continuous reflection running?"
→ `GET /api/learning-mode`
→ Respond with `enabled` status + cycle count + proposals generated.

**User:** "Pause the self-improvement loop."
→ `POST /api/learning-mode/disable`
→ Respond: "Learning mode paused. ReflectionEngine will finish its current cycle then stop."

## Failure Modes

| Error | Cause | Fix |
|---|---|---|
| `500` on enable/disable | State file write failure | Check `data/` directory permissions; ensure the process has write access |
| ReflectionEngine notification silently skipped | C85 not yet deployed | Expected — state is saved and engine will pick it up on next startup |
| State resets to default after restart | `data/learning_mode.json` deleted or corrupted | File is recreated with defaults on next toggle; previous counters lost |
| Toggle feels like no-op | Concurrent requests hit enable + disable simultaneously | State file is the source of truth; check `GET /api/learning-mode` for actual state |
