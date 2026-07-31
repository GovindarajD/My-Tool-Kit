# Agent Usage Guide — vscode_llm_bridge

A practical, protocol-level guide for **any** coding agent or tool that
wants to call this bridge: GitHub Copilot Chat itself, Claude Code,
Codex CLI, a custom Python/Node script, or anything else that can make
an HTTP request. If you are an LLM agent reading this to figure out how
to use the bridge, this file is written for you directly — every example
below is runnable as shown.

**Base URL:** `http://127.0.0.1:8766` (loopback only — never reachable
over the network, and never will be).

**If you're a human, not an agent:** open `http://127.0.0.1:8766/` in a
browser (or click the `LLM Bridge :8766` status bar item, or run "LLM
Bridge: Open Web UI") for a small built-in chat UI — pick a backend/
vendor/model, send messages, attach images, and see the streamed
response and per-call cost without writing any code. Everything below
this point is for a programmatic caller; the UI is a convenience layer
on top of the exact same endpoints.

---

## Step 1 — Check availability before doing anything else

```bash
curl -s http://127.0.0.1:8766/health
```

```json
{"status": "ok", "model": "Claude Sonnet 4.6", "port": 8766, "available": true}
```

- If the request fails to connect at all: the extension isn't running,
  or something else already holds port 8766 (only one process can bind
  it). Nothing else in this guide will work until this succeeds.
- If it succeeds but `"available": false`: the extension is running but
  no Copilot model is currently signed in / selected in VS Code. Ask the
  user to open VS Code, check the Copilot Chat model picker, and confirm
  they're signed in.
- **`model` names whatever is CURRENTLY selected in the user's Copilot
  Chat model picker in that VS Code window** — this can be Claude,
  GPT-4o, or anything else Copilot's multi-model catalog offers,
  depending entirely on the user's own picker state. Don't assume it's
  any specific vendor; read this field if your logic depends on which
  model you're actually talking to.
- This response never includes a `"bridge"` field. If a probe you're
  reusing from elsewhere expects one, that was written against a
  different, similarly-shaped bridge extension — not this one.

## Step 2 — A basic text-only request

```bash
curl -s -N http://127.0.0.1:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "Say hello in one word."}],
    "stream": true,
    "max_tokens": 64
  }'
```

**The response is ALWAYS Server-Sent Events (SSE), regardless of the
`stream` value you send.** There is no non-streaming mode. Each event:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

To get the final text, concatenate every `choices[0].delta.content`
value across events until you see `data: [DONE]`. A minimal Python
accumulator:

```python
import json, urllib.request

def chat(prompt: str) -> str:
    body = json.dumps({
        "model": "copilot",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 512,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8766/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    text = ""
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            break
        chunk = json.loads(payload)
        if "error" in chunk:
            raise RuntimeError(chunk["error"])
        text += chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
    return text
```

A complete, tested reference implementation (with proper error typing,
timeouts, and image support) already exists in this repo at
`copilot_bridge_responder/client.py` (`CopilotBridgeClient`) — read that
file for a production-quality version of the same logic rather than
reimplementing it from scratch if you're working in this repo.

## Step 3 — Sending images (vision input)

The bridge accepts OpenAI's standard multi-part `content` shape. **Images
MUST be `data:` URLs (base64-encoded) — the bridge never fetches a
remote `http(s)://` URL on your behalf.**

```json
{
  "model": "copilot",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What does this image show?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KG..."}}
      ]
    }
  ],
  "stream": true
}
```

```python
import base64

def image_data_url(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = "image/png" if path.endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{b64}"
```

**Two ways image input can fail silently if you don't check for them:**

1. **If the installed VS Code build has no image-part support**, or an
   image URL is malformed, the bridge drops the image(s) and injects an
   extra message telling the model explicitly: *"N image part(s) in
   this request could not be delivered ... answer ONLY from the text
   above, and say explicitly that no image was seen rather than
   guessing."* If your prompt asked a vision question and the response
   reads like it's guessing or hedging about not seeing an image, that's
   this notice working as intended — not a bug to route around.
2. **A confident-sounding answer is not proof the model actually saw the
   image.** If correctness matters, ask a question whose answer is only
   derivable FROM the image (e.g. "what color is this?" for a
   known-color test image) rather than trusting tone alone — see
   `tests/system/test_copilot_bridge_live.py`'s own
   `test_bridge_actually_sees_the_image_not_just_the_prompt_text` for
   exactly this pattern.

## Step 4 — Error handling

Two distinct failure shapes, both of which you must handle — neither
looks like a normal HTTP error:

**A. Rejected before any SSE stream starts** (bad request, no model
available at all):
```json
{"error": "No GitHub Copilot model available. Ensure Copilot is signed in."}
```
This arrives as a plain HTTP 4xx/5xx JSON body, not SSE.

**B. Failure mid-stream** (the request was accepted, but something went
wrong while generating — e.g. GitHub Copilot's own usage quota is
exhausted):
```
data: {"error": "You've reached your monthly credit limit. Please enable additional paid credits, upgrade to Copilot Pro+, or wait until your credits reset on <date>."}
```
This arrives as a normal `200 OK` SSE event with an `"error"` key instead
of `"choices"` — **you must inspect every SSE chunk for an `"error"`
key**, not just assume `choices[0].delta.content` exists. Treat a quota
message specifically as an external, time-bound limitation (not a code
defect) — it resolves itself when the user's quota resets, not from any
retry logic on your end.

## Step 5 — Model selection (usually don't)

`"model": "copilot"` in every example above means "use whatever's
currently selected in the picker" — this is almost always what you want,
since the bridge doesn't manage its own model catalog independent of VS
Code's UI. You can request a specific model id (from `GET /v1/models`),
but if that id isn't in the currently-available list, the bridge falls
back to the first available model rather than erroring — don't assume a
specific `model` value in your request guarantees that exact model
responded; check `GET /health`'s own `model` field if you need to know
for certain.

## Step 6 — Reaching another assistant (e.g. Claude Code) through this bridge

**Full investigation trail, every step backed by a live test on a real
machine — not assumption at any point:**

1. **Check what's actually registered:**
   ```bash
   curl -s http://127.0.0.1:8766/vendors
   ```
   Returned `{"vendors": ["copilot", "copilotcli", "claude-code"], ...}`
   — so other extensions DO register their own `vscode.lm` vendor via
   `vscode.lm.registerLanguageModelChatProvider(vendor, provider)`. (An
   earlier version of this note wrongly assumed only Copilot does —
   corrected here after testing, not left uncorrected.)

2. **Confirm which real extension is actually installed**, since a
   vendor string is just a self-chosen label, not proof of identity:
   ```bash
   curl -s "http://127.0.0.1:8766/extensions?filter=claude"
   ```
   Returned `Anthropic.claude-code` ("Claude Code for VS Code"),
   `isActive: true` — genuinely installed, not a naming coincidence with
   some other tool.

3. **Target it directly** via the `vendor` field:
   ```json
   {"vendor": "claude-code", "messages": [{"role": "user", "content": "..."}]}
   ```
   Model resolves correctly (`claude-sonnet-5`), `sendRequest()`
   completes with **zero errors**, and the response stream yields
   **zero chunks** — confirmed via this extension's own Output-channel
   logging: `done: 0 chunk(s), 0 char(s) total -- EMPTY RESPONSE`.

4. **Ruled out a consent gate** — `LanguageModelAccessInformation.canSendRequest(model)`
   read `true` for `claude-code` both before AND at the moment of the
   call (logged inline: `canSendRequest=true`). Not a permission
   denial.

5. **Ruled out request-shape/politeness issues** — added `justification`
   (documented as "explains why access is needed") and `modelOptions`
   passthrough, tried both populated. **No change — still 0 chunks.**

6. **Ruled out a caller-identity filter being even possible** — checked
   VS Code's own type definitions for `ProvideLanguageModelChatResponseOptions`
   and `PrepareLanguageModelChatModelOptions` (what a provider actually
   receives): neither exposes the calling extension's identity anywhere.
   A provider has no documented way to distinguish "called from Claude
   Code's own UI" from "called from an arbitrary external extension" —
   so if it IS behaving differently based on that, it's doing so through
   some undocumented, non-public mechanism this bridge cannot see or
   influence.

7. **Reproduced identically on a second, independently-implemented
   vendor** (`copilotcli`) — ruling out a bug specific to one provider.

**Conclusion:** `claude-code`'s provider registration is real, resolvable,
and fully authorized to receive requests — every check the documented
API exposes says "yes, proceed" — yet it produces no output for a
generic external `sendRequest()` call regardless of what's varied on the
calling side. This is a deliberate choice inside that provider's own
closed implementation (most likely: only do real work when invoked
through its own native chat-participant/session flow), not a bug in this
bridge and not something fixable from the calling side with the current
public `vscode.lm` API. **Don't keep retrying this expecting a different
result without new evidence the provider's own behavior changed** — this
was tested to the limit of what the documented API surface allows.

**Practical implication:** your GitHub Copilot quota being exhausted does
NOT mean you can fall back to Claude Code (or GitHub Copilot CLI) through
this bridge — despite both being visibly registered, selectable, and
fully authorized, neither produces usable output this way. Your Claude
Code session itself is presumably still fine on its own (open its native
"CLAUDE CODE" tab directly) — it's specifically *bridging* it through an
external `vscode.lm` call that silently produces nothing, not the
session itself being broken.

**Before giving up on a fallback entirely**, two commonly-suggested
alternatives were checked via research (g-query) and found to be
**equally unusable, for the same reason** — verified, not assumed:

- **Continue.dev** — does not register a `vscode.lm` chat model provider
  at all (it only *consumes* the API, same as Cline below).
- **Cline** (installed on the machine this was tested on, extension id
  `saoudrizwan.claude-dev`) — also does not register a provider; this
  matches the direct, empirical fact that `"cline"`/`"claude-dev"` never
  appeared in this bridge's own `GET /vendors` output on that machine.

So "install a different bring-your-own-model extension" is not a working
route either, at least for these two — they're consumers of `vscode.lm`,
not providers, so there's nothing there for an external bridge to reach.

## Step 7 — The one fallback that actually works: a direct Anthropic backend

Since no `vscode.lm` vendor other than `copilot` produces usable output
(Step 6), this bridge (v0.7.0+) offers a second, **completely
independent** path that bypasses `vscode.lm` entirely: calling
Anthropic's real Messages API directly over HTTPS, using your own
Anthropic API key.

**Setup (one-time):**
1. Get an API key from **console.anthropic.com** (Anthropic's developer
   console) with billing configured. **This is separate from a Claude.ai
   Pro/Max chat subscription** — a chat subscription does NOT include
   API credits; you need a distinct API key and its own billing.
2. In VS Code, run the command **"LLM Bridge: Set Anthropic API Key"**
   (Ctrl+Shift+P / Cmd+Shift+P → type the command name) and paste the
   key when prompted. It's stored via VS Code's `SecretStorage` — never
   written to plaintext settings.
3. Confirm it's stored: `GET /health` now includes `"anthropicConfigured": true`.

**Usage — same SSE response shape as every other backend, so no new
client-side parsing is needed:**

```bash
curl -s http://127.0.0.1:8766/anthropic/models   # discover a real, current model id first
```

```json
{
  "backend": "anthropic",
  "model": "claude-opus-4-1-20250805",
  "messages": [{"role": "user", "content": "Say hello in one word."}],
  "max_tokens": 100
}
```

**`model` is REQUIRED for this backend and this bridge never guesses a
default** — Anthropic's model ids change over time, and a hardcoded
guess would eventually go stale silently. Always check `GET
/anthropic/models` for a real, currently-valid id rather than copying
one from an example that may be old by the time you read it.

Image (vision) input works identically to Step 3 — the same
`{"type": "image_url", "image_url": {"url": "data:..."}}` parts are
accepted and translated into Anthropic's own `{"type": "image",
"source": {"type": "base64", "media_type": ..., "data": ...}}` shape
internally; you don't need to format them differently for this backend.

**Errors specific to this backend:**
- `401` with no API key configured → run the "Set Anthropic API Key" command.
- `400` with no `model` field → check `GET /anthropic/models`.
- An in-stream `{"error": "..."}` chunk → Anthropic's own API error
  (invalid key, insufficient credits, rate limit, etc.) — read the
  message directly, it's Anthropic's real error text, not translated.

In Python, via `copilot_bridge_responder`:

```python
from copilot_bridge_responder import CopilotBridgeClient

client = CopilotBridgeClient()
if client.anthropic_configured():
    text = client.chat_with_images(
        "Say hello in one word.", [],
        backend="anthropic", model="claude-opus-4-1-20250805",
    )
```

## Step 8 — Riding your Claude subscription instead: the claude-cli backend

The Anthropic backend (Step 7) works, but it's **metered API billing**,
separate from any Claude.ai Pro/Max/Team/Enterprise chat subscription.
If you'd rather use a subscription you're already paying for, this
bridge (v0.8.0+) offers a **third, independent backend**: it shells out
to the `claude` CLI (`claude -p`, headless/print mode) instead of
calling `api.anthropic.com` directly. This still bypasses `vscode.lm`
entirely (Step 6's dead end doesn't apply here) — it's a real
`child_process.spawn` of the `claude` binary on the host machine.

**Setup (one-time):**
1. Install the `claude` CLI and confirm it's on `PATH`: `claude --version`.
2. Generate a long-lived OAuth token tied to your subscription:
   `claude setup-token` — this prints a token **once** (not saved
   anywhere by the CLI itself), authenticating as your Claude
   Pro/Max/Team/Enterprise subscription, NOT metered per-token API
   billing. This is a completely different credential from an Anthropic
   Console API key (Step 7) — don't confuse the two.
3. In VS Code, run the command **"LLM Bridge: Set Claude CLI OAuth
   Token"** and paste the token when prompted. Stored via
   `SecretStorage`, same as the Anthropic key — never plaintext.
4. Confirm it's stored: `GET /health` now includes
   `"claudeCliConfigured": true`.

**Usage:**

```json
{
  "backend": "claude-cli",
  "messages": [{"role": "user", "content": "Say hello in one word."}],
  "stream": true
}
```

`model` is optional here (omit it to use the CLI's own default model
selection; pass one to override, forwarded as `claude -p --model
<value>`). Image input works the same `image_url` data: URL shape as
every other backend — internally, the bridge decodes each image to a
real temporary file (the CLI's `Read` tool needs an actual file path,
not inline bytes) and tells the model to read it, cleaning the temp
file up afterward regardless of success or failure.

**This backend deliberately grants the CLI only `--allowedTools
"Read"`** — no `Bash`, no `Edit`, nothing that could write to or execute
on the host from a request this bridge merely proxies. It also never
passes `--bare`, since bare mode does not read
`CLAUDE_CODE_OAUTH_TOKEN` (confirmed from `code.claude.com/docs`) — the
token is injected directly into the spawned child process's own `env`,
not relied upon via ambient shell inheritance.

**Real per-call cost is available**, unlike every other backend here.
The CLI's own `--output-format json` reports `total_cost_usd` for each
invocation (this is the actual dollar cost against your subscription's
usage, not a token count) — the bridge surfaces it back on the final SSE
chunk's `usage.total_cost_usd` field:

```
data: {"id":"...","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"total_cost_usd":0.07864395}}
```

In Python, via `copilot_bridge_responder`, use
`chat_with_images_detailed()` (not `chat_with_images()`) to get this
value back as a `ChatResult(text=..., total_cost_usd=...)`:

```python
from copilot_bridge_responder import CopilotBridgeClient

client = CopilotBridgeClient()
if client.claude_cli_configured():
    result = client.chat_with_images_detailed(
        "Describe this image in one sentence.",
        ["/path/to/frame.png"],
        backend="claude-cli",
    )
    print(result.text, result.total_cost_usd)
```

**Errors specific to this backend:**
- `401` with no OAuth token configured → run the "Set Claude CLI OAuth
  Token" command, generating the token first with `claude setup-token`.
- An error naming "Could not launch 'claude' CLI" → the binary isn't
  installed or isn't on the `PATH` the VS Code extension host process
  sees (which may differ from your interactive shell's `PATH` on some
  systems) — run `claude --version` in the *same* terminal VS Code was
  launched from to check.
- A non-zero CLI exit or `is_error: true` in its own JSON output →
  surfaced verbatim as the bridge's error text; it's the CLI's real
  error, not translated.

**Which backend to pick:** "anthropic" (Step 7) if you specifically want
metered, pay-as-you-go API billing independent of any subscription;
"claude-cli" (this step) if you want to spend against a subscription
you already have and want visibility into what each call actually
costs. Both are fully independent of `vscode.lm` and of each other —
neither depends on Copilot or Claude Code's own extension being
installed at all.

## Known limitations to design around, not route around

- **Only one bridge instance can hold port 8766 at a time.** If a
  differently-named/branded bridge extension is already running, this
  one won't start — check `/health`'s exact response shape (Step 1) to
  know which one actually answered.
- **No conversation/session state.** Every request is stateless — if you
  need multi-turn context, include the full message history yourself in
  every request's `messages` array.
- **Tied to VS Code being open with this extension active.** This is not
  a standalone always-on service; if VS Code closes, the bridge goes
  down with it.
- **Serves whatever Copilot's picker has selected** — not a specific,
  pinned model your code can rely on across sessions unless the user
  keeps their picker on the same model.
