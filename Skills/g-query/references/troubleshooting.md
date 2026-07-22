# g-query troubleshooting

Every entry here was a real bug or gotcha hit while building/testing g-query,
not a hypothetical. Read the relevant section before assuming something new
is broken -- it's very likely one of these.

## "The browser window closed / a second process yanked the browser"

Chromium only allows **one process** to hold a given persistent profile
directory (`~/.g-query/profile`) at a time. Running the CLI sign-in step and
a long-running server (or two `GQueryClient` instances) against the same
`profile_dir` simultaneously causes one to pull the browser out from under
the other -- surfaces as `GQueryError` about the browser closing
unexpectedly, or a raw `greenlet.error`/`TargetClosedError`.

**Fix:** never run two g_query processes against the same profile_dir at
once. Stop any running server before doing a headed sign-in against its
profile directory.

## Headless mode times out even though headed works

`headless=True` is unreliable in practice: Google can fingerprint headless
Chromium as automated traffic and quietly serve regular web results instead
of AI Mode, even with a fully signed-in persistent profile. There's no clean
error -- it surfaces as `GQueryTimeoutError`/`AIModeUnavailableError` after
every selector (and the heuristic fallback) is exhausted, ~1-2 minutes,
because the AI Mode container simply never renders.

This was empirically re-confirmed *after* fixing an unrelated selector bug:
even with correct selectors, a headless run still found nothing after ~3
minutes while headed worked in ~4 seconds. The two problems are independent
-- don't assume fixing selectors will also fix headless.

**Fix:** default to `headless=False`. For unattended/server use where a
popping-up window is undesirable, use `hide_window=True` alongside
`headless=False` instead of reaching for `headless=True` -- it pushes the
Chromium window off-screen (`--window-position=-32000,-32000`) while staying
a genuine headed browser as far as Chromium/Google are concerned. Only use
`hide_window=False` (the default) for the one-time interactive sign-in step,
where a human needs to actually see and click in the window.

## Flask (or any threaded WSGI dev server) crashes with a greenlet/thread error

`greenlet.error: Cannot switch to a different thread` when calling
`GQueryClient` from a Flask app. Flask's dev server defaults `threaded=True`
-- each request gets a new OS thread -- but Playwright's sync API is
thread-affine: the browser can only be driven from the exact thread that
created it. If your client is a singleton reused across requests (the normal
pattern), request #2+ crashes.

**Fix:** run the dev server with `threaded=False` (a production WSGI server
with a single dedicated worker thread for g_query calls works too, but keep
one worker <-> one GQueryClient instance).

## Selectors don't match at all (`AIModeUnavailableError` / heuristic fires constantly)

Google's AI Mode markup uses obfuscated, frequently-changing CSS class names.
When a whole set of selectors stops matching, get **real** markup instead of
guessing. Two ways to do this, from the g-query project (not this skill's
bundled copy):

**Automated** -- `python tools/diagnose_selectors.py "your query"` opens the
query headed, then for every configured `answer_container`/`source_links`
selector prints how many visible elements matched and a text preview of each,
plus saves the full page HTML/screenshot to the current directory. Fastest
way to see exactly which selector (if any) is matching the wrong thing.

**Manual (DevTools)**, when you need to see the raw markup yourself:
1. Run a query headed (`headless=False`) and let it render.
2. In the open Chrome window, right-click the answer text -> Inspect.
3. In DevTools Elements panel, right-click the highlighted element -> Copy ->
   Copy outerHTML.
4. Look for Google's own stable identifiers rather than class names:
   `data-container-id`, `jsname`, `aria-label`, `role` attributes tend to
   survive redesigns much better than `class="xY7z2q"`-style obfuscated
   classes. Do NOT match on `jsuid` -- that's a per-render random id, not a
   stable identifier, and will never match twice.
5. Update the relevant list in `g_query/selectors.py` (leave the old entries
   as trailing fallbacks in case Google A/B tests different templates), then
   run `scripts/refresh_bundled_wheel.py` to sync this skill's bundled copy.

As of 2026-07, confirmed-working selectors:
- Answer container: `[data-container-id="main-col"]`, `[jsname="KFl8ub"]`
- Copy button: `button[aria-label="Copy text" i]` (must be an EXACT match, not
  a substring -- see "answer_text/answer_markdown come back as the query
  itself" below)
- Source citation links: `a.PMDqCb` -- these are icon-only "View related
  links" pills with no visible text; the source name has to come from
  `aria-label` (format: `"<name> (+N) – View related links"`), not
  `inner_text()`. If `sources` comes back empty even though `answer_html`
  clearly contains citation links, this selector (or its aria-label parsing)
  is what's stale, not the answer container.

## answer_text/answer_markdown come back as the query itself, silently

Found via a live bug report (three affected queries: a news-style query, a
"top headline" query, and an odds/sports query) -- `answer_text` was
literally the input query string (e.g. querying "robots news" returned
`answer_text == "robots news"`), while `answer_html` had the real answer the
whole time and `extraction_method` still reported `"known_selector"` (i.e.
no exception, looked successful).

**Root cause:** Google renders a *separate* "Copy `<the search query>`"
button elsewhere on certain AI Mode page layouts (a "copy/share this search"
affordance, unrelated to the answer) whose `aria-label` also contains the
word "copy". The old `copy_button` selector list included a `*="copy" i`
substring fallback in addition to the exact `"Copy text"` match -- under a
timing race (the exact button transiently reading 0 matches while the page
was still finishing its render), the loop fell through to the substring
selector, which matched *both* buttons, and `.last` picked the wrong one.
Clicking it copied the query text into the clipboard, and `answer_text`
prioritizes clipboard content over the (correctly-matched) DOM text.

**Fix, two layers:**
1. `copy_button` in `selectors.py` now contains *only* the exact-match
   selector -- no substring fallback that could ever match a differently-
   purposed "Copy `<query>`" button.
2. `_extract_via_copy_button()` takes the original `question` and rejects
   (returns `None`, falling back to the DOM-scraped text) any clipboard
   content that matches the question verbatim -- a real AI Mode answer is
   never just the question restated, so this is a safe backstop even if a
   similar mix-up happens again for a reason not yet seen.

If `copied_via_clipboard` is `false` more often than expected after this fix,
that's the safe fallback path working as intended, not a regression -- the
DOM-scraped `answer_text`/`answer_markdown` (via `markdownify`) are still
independently correct.

The `chat_input` selectors (for follow-up conversations) are **unverified
best-effort guesses** -- they worked in testing but were never confirmed
against a real capture the way the answer container was. If follow-ups start
raising `AIModeUnavailableError`, capture that input box's real markup the
same way.

## Follow-up (multi-turn) answer comes back with the *previous* turn's text

Two related bugs were found and fixed here, worth knowing about if similar
symptoms reappear:

1. **Clipboard race**: clicking a follow-up turn's Copy button was found to
   not reliably re-populate the OS clipboard (a focus/binding quirk on
   dynamically-added buttons). Reading the clipboard right after silently
   returned a stale previous-turn value instead of failing loudly. Fix: the
   clipboard-copy enhancement is only attempted on the *first* turn of a
   query/session -- `copied_via_clipboard` is always `false` on follow-ups,
   which is expected, not a regression.
2. **Streaming**: AI Mode answers render progressively, like an LLM chat
   reply. The first observed difference from the previous turn's text can be
   a mid-stream, not-yet-finished partial answer. Fix: follow-up extraction
   requires the text to stay unchanged for ~1.5s (a stability/debounce
   window) before accepting it as the finished answer -- this is why
   follow-up turns are a bit slower than the first turn.

If you see stale/wrong text on a follow-up again, check whether it's a new
variant of one of these two races before assuming the fix regressed.

## Query hangs / "nothing happens" in a web UI

Before assuming something is frozen: server-side request logging (e.g.
Werkzeug's access log) only prints a line *after* the response is sent, not
when the request starts. A slow query that's still trying selector fallbacks
can legitimately take 1-2 minutes with zero visible server output. Add
client-side elapsed-time feedback (a ticking status message) rather than
assuming a hang -- and if it's still hanging past ~3 minutes, that's a real
problem, not just slowness.

## Anonymous/never-signed-in profile

Without a real signed-in Google session, expect: no AI Mode content at all
(regular web results instead), far more frequent CAPTCHA/unusual-traffic
walls, no personalization, and less session stability. Signing in once
(`g-query "hello" --headed`) is the mechanism that makes Google serve AI Mode
content at all -- it isn't just about avoiding captchas.
