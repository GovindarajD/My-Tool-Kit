---
name: g-query
description: Query Google Search AI Mode for grounded, current-web answers as structured JSON (plain text, Markdown, sources, and follow-up suggestions) via the g_query Python library, which drives a real signed-in Chromium browser -- not a public API. Use this whenever the user needs an up-to-date factual answer, current events, or anything your training data might be stale on; whenever they mention g-query, GQueryClient, or Google AI Mode; or when they want a conversational, multi-turn web search with follow-ups. Also use this skill when asked to set up, debug, extend, or add selectors/features to the g-query browser-automation tool itself.
---

# g-query: Google Search AI Mode via browser automation

g_query is a Python library that drives a real, signed-in Chromium browser
(via Playwright) to `google.com/search?...&udm=50` (Google's AI Mode) and
returns the answer as structured JSON. There is no public API for this --
it's genuinely automating a browser, which shapes everything below: it needs
a one-time human sign-in, it's slower than an API call (seconds to a couple
of minutes), and only one process can touch a given browser profile at a
time.

Full technical detail lives in `references/` -- this file is the workflow;
consult the references when you hit specifics.

## Step 1: Check the environment first

This skill bundles its own copy of the g_query wheel in `dist/` right next to
these scripts -- it does not depend on any other project's `dist/` folder
existing, so it works the same way regardless of where it's installed or run
from. Before writing any code that calls `GQueryClient`, run:

```bash
python .claude/skills/g-query/scripts/check_setup.py --install
```

(Adjust the path if this skill is installed elsewhere.) `--install` actually
installs the bundled wheel and downloads the Chromium binary if either is
missing -- it's not just diagnostic. Omit `--install` to only report status
without changing anything.

This reports exactly what's missing -- package not installed, Chromium
binary not downloaded, or profile not signed in -- with the exact command to
fix whatever `--install` couldn't handle automatically. Don't skip this and
dive straight into writing a query script; half of the confusing failures in
this tool come from skipping setup, not from bugs.

If it still reports `NOT ready` after `--install`, the remaining item is
almost always the sign-in step, which can never be automated: it opens a
real, visible Chrome window and requires actually logging into a Google
account by hand -- you cannot do this yourself, so ask the user to do it and
confirm it printed a JSON result before continuing.

**If a server/app using g_query is already running, stop it before doing a
sign-in check or headed run against the same profile directory.** Two
processes sharing one Chromium profile will fight each other -- see
`references/troubleshooting.md`.

## Step 2: Decide one-shot vs. multi-turn

- **One-shot** (a single factual question, no follow-up expected): simplest,
  use `GQueryClient.query(question)` or the `g-query "question"` CLI.
- **Multi-turn conversation** (the user wants to ask follow-ups on the same
  topic): use `keep_session_open=True` and thread the `session_id` through.
  See `references/api-reference.md` for the exact pattern, and remember to
  call `close_session()` when the conversation ends -- an open session holds
  a live browser page until you do.

```python
from g_query import GQueryClient, GQueryError

with GQueryClient(headless=False) as client:
    try:
        result = client.query("What changed in the Python 3.13 release?")
    except GQueryError as exc:
        # CaptchaDetectedError, SignInRequiredError, AIModeUnavailableError,
        # or GQueryTimeoutError -- see references/api-reference.md
        raise

answer = result.answer_markdown  # or .answer_text, .to_dict(), .to_json()
```

Always keep `headless=False`. `headless=True` looks like it should work but
is empirically unreliable -- see `references/troubleshooting.md` before
touching this default.

## Step 3: Use the result

- `result.answer_markdown` is the cleanest form to show a user or include in
  a written response -- prefer it over `answer_html`.
- When the user asks to see g-query's output (or a comparison against their
  own manual query), paste `answer_markdown`/`answer_text` verbatim --
  including the full citation list and any closing follow-up question Google
  generated. Do not paraphrase, condense, or drop citations "for
  readability"; that silently reintroduces the same kind of gap a broken
  extractor would cause, just at the presentation layer instead of the
  extraction layer. Summarizing/synthesizing the answer into your own words
  is fine when the user just wants the information, not the output itself --
  but say that's what you're doing.
- `result.sources` / `result.follow_up_queries` may legitimately be empty;
  don't treat that as an error.
- `result.extraction_method` (`"known_selector"` vs `"heuristic"`) and
  `result.copied_via_clipboard` tell you how confident to be in the result --
  see `references/api-reference.md`.

## When something breaks

Read `references/troubleshooting.md` first -- it documents real bugs already
found and fixed in this exact tool (profile-lock conflicts, headless
unreliability, Flask threading crashes, stale selectors, follow-up race
conditions), each with root cause and fix. Don't re-diagnose from scratch;
check whether it's already a known issue.

If it's specifically that `answer_text` comes back empty or
`AIModeUnavailableError` starts appearing for queries that used to work, the
fix is almost always updating the CSS selectors in `g_query/selectors.py` --
not changing calling code. The troubleshooting reference has the exact
DevTools-capture workflow for finding correct replacement selectors.

## Things not to do

- Do not remove or shorten `min_request_interval`, or add concurrency to
  hammer Google with parallel requests -- this tool automates a personal,
  already-authenticated browser session and should behave like one human
  clicking around, not a scraper.
- Do not attempt to auto-solve CAPTCHAs or script around a sign-in wall --
  raise/report it and let a human resolve it in a headed window.
- Do not commit or share the contents of the profile directory
  (`~/.g-query/profile` by default) -- it contains live Google session
  cookies.
- Do not run two g_query processes against the same `profile_dir`
  simultaneously (see Step 1).

## Reference files

- `references/api-reference.md` -- full `GQueryClient`/`GQueryResult`
  signatures, the output JSON schema, and the exception table.
- `references/troubleshooting.md` -- every real gotcha hit while building
  this tool, with root cause and fix.
- `scripts/check_setup.py` -- environment readiness check + auto-install
  (Step 1).
- `dist/g_query-*.whl` -- the bundled package itself. This is what makes the
  skill self-contained; don't delete it.
- `scripts/refresh_bundled_wheel.py` -- maintenance only, for whoever
  maintains g_query's source: rebuilds the wheel from `src/g_query` and
  refreshes the copy in `dist/`. Not needed for normal use of this skill, and
  only works when run from inside the g-query project itself.
