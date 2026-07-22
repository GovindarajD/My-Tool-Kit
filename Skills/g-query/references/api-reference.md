# g_query API reference

## GQueryClient

```python
GQueryClient(
    *,
    headless: bool = False,          # keep False -- see troubleshooting.md
    profile_dir: str | None = None,  # default ~/.g-query/profile
    timeout: float = 20.0,           # seconds per selector attempt
    locale: str = "en-US",
    udm: str = "50",                 # Google's current AI-Mode query param
    selectors: Selectors | None = None,
    min_request_interval: float = 2.0,
    interactive_wait_seconds: float = 180.0,
    hide_window: bool = False,       # push window off-screen, still "headed"
)
```

Context manager: `with GQueryClient(...) as client:` calls `.start()` on
entry and `.close()` on exit. `.start()` is idempotent -- safe to call again.

### query()

```python
client.query(
    question: str,
    *,
    session_id: str | None = None,
    keep_session_open: bool = False,
) -> GQueryResult
```

- **One-shot** (default): opens a fresh page, collects the answer, closes it.
- **Multi-turn**: pass `keep_session_open=True` to keep the page open and get
  back a `session_id`. Pass that `session_id` (with `keep_session_open=True`
  again) into the next call to continue the same conversation -- typed into
  Google's own chat input, not a new search. Call `client.close_session(id)`
  when the conversation is done to free the page.
- If `session_id` is given but unknown/already closed, a new session starts
  silently and a new id comes back in the result -- compare
  `result.session_id` to what you passed if that distinction matters.

Reuse one `GQueryClient` (one `with` block) across multiple `.query()` calls
instead of creating a new client per call -- it keeps one browser context
alive and enforces `min_request_interval` between requests automatically.

### Other methods

- `close_session(session_id: str) -> None` -- ends a conversation, closes its
  page. Safe no-op on an unknown/already-closed id.
- `has_session(session_id: str) -> bool`

## GQueryResult

`result.to_dict()` / `result.to_json()` produce:

```jsonc
{
  "query": "string -- the original question",
  "answer_text": "string -- plain-text AI Mode answer",
  "answer_html": "string -- raw inner HTML of the answer container",
  "answer_markdown": "string -- the same content converted to Markdown",
  "sources": [
    { "title": "string", "url": "string", "snippet": "string, may be empty" }
  ],
  "follow_up_queries": ["string", "..."],
  "ai_mode_triggered": true,
  "extraction_method": "known_selector | heuristic",
  "copied_via_clipboard": true,
  "session_id": "string -- feed back into query() to continue this conversation",
  "fetched_at": "ISO-8601 UTC timestamp",
  "page_url": "the actual Google Search URL that was loaded"
}
```

Treat `sources` and `follow_up_queries` as possibly empty -- not every query
surfaces citations or follow-up chips.

## Exceptions

All subclass `GQueryError` (import from `g_query`).

| Exception                | Meaning                                       | What to do |
|---------------------------|------------------------------------------------|-------------|
| `CaptchaDetectedError`     | Google served a CAPTCHA/unusual-traffic wall   | Headed mode waits for a human to solve it (up to `interactive_wait_seconds`); headless raises immediately |
| `SignInRequiredError`      | Google is asking for sign-in                   | Headed mode waits for a human to log in; headless raises immediately |
| `AIModeUnavailableError`   | No AI Mode container found (or chat input missing on a follow-up) | See troubleshooting.md -- usually selectors.py needs updating |
| `GQueryTimeoutError`       | Answer/follow-up didn't render in time         | See troubleshooting.md before just raising `timeout=` |
| `GQueryError`              | Base class / generic browser automation failure | Catch this last as a fallback |

## Selectors (g_query.selectors.Selectors)

Every field is a list of CSS selectors tried in order; overridable per-client
via `GQueryClient(selectors=Selectors(...))`. See troubleshooting.md for which
of these are verified against real captured markup vs. best-effort guesses.
