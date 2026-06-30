# ShadowRealm — Human Tasks

Things that cannot be automated by the AI build agent (file too large to patch safely,
requires local testing, or needs a one-time manual decision). Clear each item after it
is done and commit the update.

---

## Pending

### ▢ C06 — Wire `/status` route into `app.py`

Two lines to add manually:

**1. Near the other route imports** (alongside the `setup_auth_routes` import, ~line 90):

```python
from src.status_route import register_status_route
```

**2. Near the bottom where routes are registered** (right after `setup_auth_routes(app, ...)`):

```python
register_status_route(app)
```

Once added, `GET /status` is live and auth-gated identically to every other UI page.
Verify by visiting `http://localhost:7860/status` after restart.

---

### ▢ Manuel — Register Manuel as a companion in `companion/` config

Manuel is the conversational companion persona. These steps wire him into the runtime.

**1. Create `companion/manuel.json`** with at minimum:

```json
{
  "id": "manuel",
  "name": "Manuel",
  "persona": "A calm, precise assistant who communicates in clear steps.",
  "greeting": "Hello. I am Manuel. How may I assist you today?",
  "enabled": true
}
```

**2. Register Manuel in `companion/__init__.py`** (or whichever file enumerates companions):

```python
from companion.manuel import ManuelCompanion

COMPANIONS = [
    # ... existing companions ...
    ManuelCompanion,
]
```

**3. Add Manuel's system prompt** to `.env` (or `.env.example` as a template key):

```
MANUEL_SYSTEM_PROMPT="You are Manuel, a calm and methodical AI assistant. Always respond in numbered steps when explaining a process."
```

**4. Smoke-test** by selecting Manuel from the companion selector in the UI and sending a test message. Confirm his persona and greeting appear correctly.

---

### ▢ Manuel — Add Manuel's avatar asset

**1. Source or create** a portrait image for Manuel (recommended: 256×256 px PNG, transparent background).

**2. Save as** `src/static/avatars/manuel.png`.

**3. Reference it** in `companion/manuel.json` (if the schema supports an `avatar` field):

```json
"avatar": "static/avatars/manuel.png"
```

**4. Verify** the avatar loads in the companion selector UI without a broken-image fallback.

---

### ▢ Manuel — Verify Manuel's step-by-step response style in integration tests

**1. Run** `pytest tests/companions/test_manuel.py` (create the file if it doesn't exist yet).

**2. Assert** that for a prompt like `"How do I reset my password?"`, Manuel's response:
   - Begins with `"1."`
   - Contains at least three numbered steps
   - Does not exceed the configured `MAX_RESPONSE_TOKENS` limit

**3. Fix** any failures before merging Manuel into the main companion list.

---

## Completed

*(Move items here when done, with the date.)*
