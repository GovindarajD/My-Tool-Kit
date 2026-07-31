# vscode_llm_bridge

A small VS Code extension that exposes **VS Code's Language Model API**
(`vscode.lm.selectChatModels`) as a local, OpenAI-compatible HTTP/SSE
endpoint on `http://127.0.0.1:8766`. In practice, today, that means it
exposes **whatever model is currently selected in your GitHub Copilot
Chat model picker** — Copilot is the only vendor VS Code's public LM API
currently exposes — to any external tool that can speak the standard
OpenAI `chat/completions` wire format.

**Extension id:** `local-dev.vscode-llm-bridge` (package name
`vscode-llm-bridge`, since VS Code's marketplace manifest rules disallow
underscores in that field — this project's own name/branding uses the
underscore form, `vscode_llm_bridge`, everywhere else: documentation,
output channel, status bar).

## Why this exists

VS Code's Language Model API is only callable **from inside another VS
Code extension** — there is no way for an external process (a CLI tool,
a Python script, a different agent framework) to call it directly. This
extension is the bridge: it runs inside VS Code (where it *can* call
`vscode.lm`), and re-exposes that capability as a plain local HTTP
endpoint any external tool can hit with a normal HTTP client.

**Originally built single-purpose** as "KiCad Copilot Bridge" for one
KiCad electronics-design dashboard project. Generalized (same session
that added image support) once it became clear the same bridge is
useful to *any* coding agent or tool that wants programmatic access to
whatever model your Copilot subscription has selected — not just that
one dashboard.

## What it's good for, and what it isn't

- **Good for:** giving a CLI agent, script, or another tool access to
  your Copilot-selected model without that tool needing its own API key
  or authentication — it rides on your already-signed-in VS Code
  session.
- **Three backends, not one.** The default path rides your signed-in VS
  Code/Copilot session (`vscode.lm`) — no separate key needed, but tied
  to VS Code being open and whatever Copilot's picker has selected. As
  of v0.7.0, an explicit `{"backend": "anthropic", "model": "..."}`
  bypasses `vscode.lm` entirely and calls Anthropic's own API directly
  with your own API key (`console.anthropic.com` — separate, metered
  billing from a Claude.ai chat subscription) — a genuinely independent
  path that doesn't depend on VS Code, Copilot, or any other extension's
  behavior. As of v0.8.0, `{"backend": "claude-cli"}` is a second,
  independent fallback that shells out to the `claude` CLI (`claude -p`)
  instead, riding your existing Claude subscription
  (`CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`) rather than
  metered API billing, and surfaces real per-call cost
  (`total_cost_usd`) back to the caller. See "Reaching another
  assistant" below and `agent_usage_guide.md` Steps 7-8 for setup and
  usage of both.
- **Not "Claude Code" or any other standalone agent tool.** This bridge
  exposes a *model* (a chat-completions endpoint), not an *agent* (a
  tool-use harness with its own file/shell/etc. tools). If Copilot's
  picker happens to have a Claude model selected, you get that model's
  raw text/vision completions through this endpoint — you do not get
  Claude Code's own agentic behavior, tool use, or session state.
- **Cannot actually reach Claude Code's own session for a real
  completion — confirmed by exhaustive live testing, not assumption.**
  `Anthropic.claude-code` IS genuinely installed and active (confirmed
  via `GET /extensions`) and DOES register its own `vscode.lm` vendor
  (`GET /vendors` shows `"claude-code"` alongside `"copilot"` and
  `"copilotcli"`) — earlier notes here wrongly assumed otherwise, and
  were corrected once tested rather than left wrong. `POST
  /v1/chat/completions` accepts an explicit `"vendor"` field to target
  it. **But it produces zero output every time**, despite: the model
  resolving correctly, `sendRequest()` completing with no error,
  `canSendRequest()` reading `true` both before and during the call
  (ruling out a consent gate), and populating `justification`/
  `modelOptions` changing nothing. VS Code's own type definitions
  confirm a provider has no documented way to even see which extension
  is calling it, so this can't be an identity-based filter through any
  public mechanism. Reproduced identically on a second, independent
  vendor (`copilotcli`). Full reproduction with log evidence:
  `agent_usage_guide.md` Step 6. **Conclusion: this is a deliberate
  choice inside that provider's own closed implementation** (serve real
  completions only through its own native UI, not generic external
  callers) — not a bug here, and not fixable by changing what this
  bridge sends. Your GitHub Copilot quota and Claude Code
  account/session remain two entirely separate billing relationships
  either way; this bridge just can't act as a usable path to the latter.
  Also checked (via g-query research, verified against real evidence
  rather than trusted at face value): Continue.dev and Cline **also**
  don't register `vscode.lm` providers — they're consumers of the API,
  not providers, so they're not a working alternative route either. **The
  one fallback that DOES work** is the direct Anthropic backend above.

## Try it in a browser first

As of v0.9.0, `http://127.0.0.1:8766/` serves a small built-in test page
(`media/index.html`) — no separate client needed to try the bridge out:
pick a backend (vscode.lm/Copilot, direct Anthropic, or claude-cli),
pick a vendor and model where relevant, chat, attach images, and watch
the streamed response (plus per-call cost, for backends that report
it). Click the `LLM Bridge :8766` status bar item (bottom right) to open
it, or run the **"LLM Bridge: Open Web UI"** command. It's a plain,
dependency-free HTML/JS page — no build step, no framework — served
straight off disk by the extension itself; useful for a human sanity
check, not part of the programmatic API contract.

The status bar tooltip itself (hover, don't click, to just read it) now
also shows live counts — vendors registered, total models, which one
Copilot's picker currently has selected, and whether the Anthropic/
claude-cli backends are configured — instead of just "running on port
8766".

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Browser test UI (v0.9.0+) — see above |
| `GET` | `/health` | `{"status":"ok","model":"<name>","port":8766,"available":<bool>}` — probe this first; `available:false` means no Copilot model is signed in/selected |
| `GET` | `/models` | `{"models":[{"id":"...","name":"...","vendor":"..."}]}` — native format |
| `GET` | `/v1/models` | OpenAI-compatible model list `{"object":"list","data":[...]}` |
| `GET` | `/vendors` | **Diagnostic** (added v0.3.0): `{"vendors":[...],"models":[{..., "canSendRequest": bool\|undefined}]}` — EVERY `vscode.lm` model registered by ANY extension, not just Copilot, plus a live consent check per model. Tested finding: other assistants (Claude Code, GitHub Copilot CLI) DO show up here with `canSendRequest: true`, but do NOT produce real output through `/v1/chat/completions` — see "What it's good for, and what it isn't" above |
| `GET` | `/extensions[?filter=x]` | **Diagnostic** (added v0.5.0): `{"count":N,"extensions":[{"id","name","publisher","isActive"}]}` — every ACTUALLY INSTALLED extension, ground truth independent of self-chosen `/vendors` labels |
| `GET` | `/anthropic/models` | **Added v0.7.0.** Proxies Anthropic's own model list using the configured key — use this to find a real, current model id rather than guessing one. `401` if no key is configured yet |
| `POST` | `/v1/chat/completions` | OpenAI-compatible **streaming SSE only** (`stream: true` is assumed; the server always streams). Defaults to `vscode.lm` vendor `"copilot"`; an explicit `"vendor"` field (added v0.4.0), plus optional `"justification"`/`"model_options"` (added v0.6.1), can target anything `/vendors` lists — but only `copilot` is confirmed to actually produce output (see above). **`{"backend": "anthropic", "model": "..."}`** (added v0.7.0) bypasses `vscode.lm` entirely for a genuinely independent, working fallback — see `agent_usage_guide.md` Step 7. **`{"backend": "claude-cli"}`** (added v0.8.0) shells out to the `claude` CLI instead, riding your Claude subscription and reporting real per-call cost — see Step 8 |

**Commands** (Ctrl+Shift+P / Cmd+Shift+P): `LLM Bridge: Start` / `Stop`,
(added v0.7.0) `LLM Bridge: Set Anthropic API Key` / `Clear Anthropic
API Key`, (added v0.8.0) `LLM Bridge: Set Claude CLI OAuth Token` /
`Clear Claude CLI OAuth Token`, and (added v0.9.0) `LLM Bridge: Open Web
UI` (also bound to clicking the status bar item) — all keys/tokens are
stored via VS Code's `SecretStorage`, never in plaintext settings.

See **`agent_usage_guide.md`** for the full request/response contract,
image (vision) input, and error handling any calling agent needs to know
about — this file is deliberately kept short and points there for the
consumer-facing detail.

## Install / build

This is a local, unpublished (`publisher: "local-dev"`) extension — it
is never published to the VS Code Marketplace.

```bash
cd reference/vscode-extension
npm install          # first time only
npm run compile      # or: npx tsc -p ./
npx --yes @vscode/vsce package --allow-missing-repository
```

That produces `vscode-llm-bridge-<version>.vsix` in this directory.
Install/reinstall it in VS Code: **Extensions view → `...` menu → Install
from VSIX...** → pick the `.vsix` file → reload the window when prompted.

**If you already have an older version installed** (including the
original `kicad-copilot-bridge` build), reinstalling from a new `.vsix`
with a bumped version replaces it — you don't need to manually uninstall
first, but you DO need to reload the VS Code window for the new code to
actually take effect. A stale window keeps running the old JS in memory.

## Confirming which extension is actually running

**Only one process can hold port 8766 at a time.** If you have multiple
similarly-purposed bridge extensions installed across different VS Code
windows/profiles, only one of them is actually answering requests.
Confirm which one by checking the exact shape of `/health`:

```bash
curl http://127.0.0.1:8766/health
```

This extension's `/health` response has **no `"bridge"` field** —
`{"status":"ok","model":"...","port":8766,"available":true}` exactly. A
different extension's health handler may include extra fields (e.g. a
`"bridge"` identifier naming itself) — if you see one, a different
extension is the one actually running, not this one, regardless of
which one you think you have open.

## Development notes

- `src/extension.ts` is the entire implementation — one file, no build
  tooling beyond `tsc`. Read its own module docstring for the full
  vision-input contract.
- `supportsImageParts()` feature-detects `vscode.LanguageModelDataPart`
  at runtime rather than assuming a VS Code version — an older VS Code
  degrades to text-only with an explicit in-band notice to the model,
  never a silent, confident-looking-but-unfounded answer.
- Bound to `127.0.0.1` only — never exposed to the network.
- `ALLOWED_ORIGINS` is a CORS allow-list for browser-based callers (e.g.
  a local dashboard's own frontend); a non-browser HTTP client (Python's
  `urllib`, `curl`, most agent frameworks) never sends an `Origin`
  header and CORS doesn't apply to it at all.
