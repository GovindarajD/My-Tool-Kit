/**
 * VS Code LLM Bridge
 *
 * Starts an HTTP server on port 8766 that exposes VS Code's Language
 * Model API (vscode.lm.selectChatModels, pinned to vendor: 'copilot' --
 * GitHub Copilot's signed-in models -- since that is the only vendor
 * ACTUALLY REGISTERED in practice today; the API itself
 * (registerLanguageModelChatProvider) lets any extension register its own
 * vendor, so this is an empirical fact about what's installed, not a
 * hard platform limit -- see GET /vendors below) as an OpenAI-compatible
 * /v1/chat/completions SSE endpoint. Originally built single-purpose as
 * "KiCad Copilot Bridge" for one dashboard project; generalized so ANY
 * external tool that can speak the OpenAI chat-completions wire format
 * can use it -- a coding agent (Claude Code, Codex CLI, a custom
 * script), a dashboard, or anything else. See agent_usage_guide.md for a
 * consumer-facing guide.
 *
 * Endpoints:
 *   GET  /                      — a small browser-based test UI (media/index.html):
 *                                  pick backend/vendor/model, chat, attach images,
 *                                  see streamed responses and per-call cost. Not
 *                                  required for programmatic use -- purely a
 *                                  human-facing way to poke the bridge without
 *                                  writing a client.
 *   GET  /health                — {"status":"ok","model":"<name>","port":8766,"anthropicConfigured":bool}
 *   GET  /models                — {"models":[{"id":"...","name":"..."},...]}
 *   GET  /vendors                — diagnostic: EVERY vscode.lm vendor/model
 *                                  registered by ANY extension, not just
 *                                  Copilot -- answers "is something else
 *                                  (e.g. Claude Code) registered at all"
 *   GET  /extensions[?filter=x]  — diagnostic: every INSTALLED extension's
 *                                  real id/name, from vscode.extensions.all
 *                                  -- ground truth for which real extension
 *                                  (if any) is behind a /vendors label
 *   GET  /anthropic/models      — proxies Anthropic's own model list using
 *                                  the configured key (see command below)
 *   POST /v1/chat/completions   — OpenAI-compatible streaming SSE. Defaults
 *                                  to vscode.lm vendor 'copilot'; pass an
 *                                  explicit "vendor" field (any value GET
 *                                  /vendors reports) to reach a different
 *                                  registered provider instead (though see
 *                                  the KNOWN LIMITATION below for why that
 *                                  rarely produces real output); pass
 *                                  {"backend": "anthropic", "model": "..."}
 *                                  to bypass vscode.lm and call Anthropic's
 *                                  real API directly (metered API billing);
 *                                  pass {"backend": "claude-cli"} to shell
 *                                  out to `claude -p` instead, riding the
 *                                  user's Claude subscription via
 *                                  CLAUDE_CODE_OAUTH_TOKEN rather than
 *                                  metered billing -- confirmed live to
 *                                  actually work, including reading local
 *                                  images, unlike the vscode.lm
 *                                  'claude-code' vendor (see KNOWN
 *                                  LIMITATION below for why that's a
 *                                  different, non-working path)
 *
 * Commands: "LLM Bridge: Set/Clear Anthropic API Key" manage the "anthropic"
 * backend's key; "LLM Bridge: Set/Clear Claude CLI OAuth Token" manage the
 * "claude-cli" backend's token (generate with `claude setup-token`). Both
 * stored via VS Code SecretStorage, never in plaintext settings.
 *
 * SSE wire format:
 *   data: {"id":"c1","choices":[{"delta":{"content":"TOKEN"}}]}\n\n
 *   ...
 *   data: [DONE]\n\n
 *
 * Vision input (added for SynapticRobots' copilot_bridge_responder, which
 * needs this bridge to actually judge real camera frames, not just their
 * file paths as text): a message's `content` may be either a plain string
 * (existing behavior, unchanged) or an OpenAI-style array of
 * `{type:"text",text}` / `{type:"image_url",image_url:{url}}` parts,
 * where `url` MUST be a `data:<mime>;base64,<...>` URL -- this bridge
 * never fetches a remote URL on a caller's behalf. Images convert to
 * `vscode.LanguageModelDataPart.image()`, feature-detected at runtime: on
 * a VS Code build without image-part support, images are dropped and the
 * model is told explicitly so, rather than silently guessing from text
 * alone and sounding confident about something it never saw.
 *
 * KNOWN, EXHAUSTIVELY-TESTED LIMITATION (do not re-investigate without new
 * evidence): other vscode.lm vendors installed alongside 'copilot' (seen:
 * 'claude-code' -- confirmed as the real, active Anthropic.claude-code
 * extension via GET /extensions, not a naming coincidence -- and
 * 'copilotcli') resolve a real model and complete sendRequest() with NO
 * error, but their response stream yields ZERO chunks every time. Ruled
 * out: consent (canSendRequest() reads true both before and during the
 * call), request shape (tried justification + modelOptions, no change),
 * and an identity-based filter (VS Code's own types confirm a provider
 * has no documented way to see which extension is calling it). This is a
 * deliberate choice inside that provider's own closed implementation
 * (most likely: real output only through its own native UI/session, not
 * a generic external sendRequest()) -- not fixable from this bridge's
 * side. See agent_usage_guide.md Step 6 for the full reproduction.
 */

import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';

// ── Constants ─────────────────────────────────────────────────────────────────

const BRIDGE_PORT = 8766;
const ALLOWED_ORIGINS = [
    'http://127.0.0.1:8765',
    'http://localhost:8765',
    'http://127.0.0.1:8766',
    'http://localhost:8766',
];

// ── Module-level state ────────────────────────────────────────────────────────

let _server: http.Server | undefined;
let _statusBar: vscode.StatusBarItem | undefined;
let _output: vscode.OutputChannel | undefined;
let _accessInfo: vscode.LanguageModelAccessInformation | undefined;
let _secrets: vscode.SecretStorage | undefined;
let _extensionPath: string | undefined;
let _uiHtmlCache: string | undefined;

const ANTHROPIC_API_KEY_SECRET = 'vscodeLlmBridge.anthropicApiKey';
const ANTHROPIC_API_VERSION = '2023-06-01';
const CLAUDE_CLI_OAUTH_TOKEN_SECRET = 'vscodeLlmBridge.claudeCliOAuthToken';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Collect the raw POST body as a UTF-8 string. */
function collectBody(req: http.IncomingMessage): Promise<string> {
    return new Promise((resolve, reject) => {
        const parts: Buffer[] = [];
        req.on('data', (chunk: Buffer) => parts.push(chunk));
        req.on('end', () => resolve(Buffer.concat(parts).toString('utf8')));
        req.on('error', reject);
    });
}

/**
 * Apply CORS headers.
 * Only reflects origins in the allow-list; falls back to same-origin semantics
 * so the endpoint is never publicly accessible.
 */
function addCorsHeaders(req: http.IncomingMessage, res: http.ServerResponse): void {
    const origin = req.headers['origin'] ?? '';
    const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
    res.setHeader('Access-Control-Allow-Origin', allowed);
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
    res.setHeader('Access-Control-Max-Age', '86400');
}

/** Fetch available Copilot models (cached for the lifetime of this request). */
async function getCopilotModels(): Promise<vscode.LanguageModelChat[]> {
    return vscode.lm.selectChatModels({ vendor: 'copilot' });
}

/** Fetch EVERY chat model registered by ANY extension, no vendor filter --
 * diagnostic only (GET /vendors), never used by the actual chat-completions
 * path above, which stays pinned to vendor: 'copilot' on purpose. Answers
 * "is some other extension (e.g. a Claude Code-style assistant) registered
 * as its own vscode.lm vendor at all" empirically, instead of guessing. */
async function getAllModels(): Promise<vscode.LanguageModelChat[]> {
    return vscode.lm.selectChatModels();
}

/** True only if the installed VS Code's LM API exposes LanguageModelDataPart
 * (added after this extension's declared minimum ^1.90.0) -- feature-detected
 * at runtime rather than assumed, so an older VS Code degrades to text-only
 * instead of throwing on a missing constructor. */
function supportsImageParts(): boolean {
    return typeof (vscode as unknown as { LanguageModelDataPart?: unknown }).LanguageModelDataPart !== 'undefined';
}

/** One OpenAI-style content part: plain text, or a data:/http(s): image URL.
 * `content` on an incoming message is either a plain string (existing,
 * text-only callers keep working unchanged) or an array of these. */
type ContentPart =
    | { type: 'text'; text: string }
    | { type: 'image_url'; image_url: { url: string } };

const DATA_URL_RE = /^data:([^;,]+);base64,(.+)$/s;

/** Decodes a data: URL into raw bytes + mime type. Returns null (never
 * throws) for anything else (e.g. an http(s) URL) -- LanguageModelDataPart.image()
 * needs raw bytes, not a remote fetch, and this bridge has no business
 * reaching out to arbitrary URLs on a caller's behalf. Callers must supply
 * frames as base64 data URLs. */
function decodeDataUrlImage(url: string): { bytes: Uint8Array; mime: string } | null {
    const match = DATA_URL_RE.exec(url);
    if (!match) { return null; }
    const [, mime, b64] = match;
    try {
        return { bytes: new Uint8Array(Buffer.from(b64, 'base64')), mime };
    } catch {
        return null;
    }
}

// ── Direct Anthropic backend ─────────────────────────────────────────────────
//
// Added after exhaustively confirming vscode.lm cannot reach Claude Code's
// or GitHub Copilot CLI's own vendor registrations for real output (see the
// module docstring above and agent_usage_guide.md Step 6) -- researched via
// g-query/deep-research-skill, which also disproved two commonly-suggested
// workarounds (Continue.dev and Cline do NOT register vscode.lm providers
// either; verified against Cline's own real absence from GET /vendors on
// this machine, and against the actual GitHub issue a g-query citation
// pointed at, which turned out to be unrelated to this topic entirely).
//
// This backend bypasses vscode.lm COMPLETELY -- it calls Anthropic's real
// Messages API (https://api.anthropic.com/v1/messages) directly over HTTPS
// (Node's built-in `https`, no new npm dependency, matching this extension's
// existing zero-runtime-dependency style), using the user's OWN Anthropic
// API key. This is a genuinely independent path: it does not depend on
// Copilot's quota, VS Code's LM API, or any other extension's undocumented
// internal behavior -- only on the user having a real console.anthropic.com
// API key with billing configured. IMPORTANT: a Claude.ai chat subscription
// (Pro/Max) does NOT include API credits -- API access is separate billing.

interface AnthropicContentPart {
    type: 'text' | 'image';
    text?: string;
    source?: { type: 'base64'; media_type: string; data: string };
}
interface AnthropicMessage {
    role: 'user' | 'assistant';
    content: AnthropicContentPart[];
}

/** Mirrors convertMessages() above but targets Anthropic's own message
 * shape: a separate top-level `system` string (not a message in the array),
 * and image parts as {type:"image", source:{type:"base64", media_type, data}}
 * rather than OpenAI's {type:"image_url", image_url:{url}}. */
function convertMessagesToAnthropic(
    messages: { role: string; content: string | ContentPart[] }[],
): { system: string; messages: AnthropicMessage[] } {
    const systemParts: string[] = [];
    const result: AnthropicMessage[] = [];

    for (const m of messages) {
        if (m.role === 'system') {
            const text = typeof m.content === 'string'
                ? m.content
                : m.content.map(p => (p.type === 'text' ? p.text : '')).join('\n');
            if (text.trim()) { systemParts.push(text.trim()); }
            continue;
        }

        const role: 'user' | 'assistant' = m.role === 'assistant' ? 'assistant' : 'user';

        if (typeof m.content === 'string') {
            if (!m.content.trim()) { continue; }
            result.push({ role, content: [{ type: 'text', text: m.content }] });
            continue;
        }

        const parts: AnthropicContentPart[] = [];
        for (const part of m.content) {
            if (part.type === 'text') {
                if (part.text?.trim()) { parts.push({ type: 'text', text: part.text }); }
            } else if (part.type === 'image_url') {
                const decoded = decodeDataUrlImage(part.image_url?.url ?? '');
                if (decoded) {
                    parts.push({
                        type: 'image',
                        source: {
                            type: 'base64',
                            media_type: decoded.mime,
                            data: Buffer.from(decoded.bytes).toString('base64'),
                        },
                    });
                }
            }
        }
        if (parts.length) { result.push({ role, content: parts }); }
    }

    return { system: systemParts.join('\n\n'), messages: result };
}

/** Calls Anthropic's Messages API directly, streaming, and translates its
 * own SSE event shape (`content_block_delta` with `delta.type: "text_delta"`)
 * into plain text deltas via callbacks -- the caller (the /v1/chat/completions
 * handler below) re-wraps those into this bridge's existing OpenAI-chunk SSE
 * format, so nothing downstream needs new parsing logic to use this backend.
 * Returns the underlying http.ClientRequest so the caller can abort it if the
 * HTTP client disconnects (mirrors the vscode.lm path's CancellationTokenSource). */
function callAnthropicMessages(
    apiKey: string,
    model: string,
    system: string,
    messages: AnthropicMessage[],
    maxTokens: number,
    onDelta: (text: string) => void,
    onDone: () => void,
    onError: (message: string) => void,
): http.ClientRequest {
    const body = JSON.stringify({
        model,
        ...(system ? { system } : {}),
        messages,
        max_tokens: maxTokens,
        stream: true,
    });

    const req = https.request(
        {
            hostname: 'api.anthropic.com',
            path: '/v1/messages',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-api-key': apiKey,
                'anthropic-version': ANTHROPIC_API_VERSION,
                'Content-Length': Buffer.byteLength(body),
            },
        },
        (res) => {
            if (res.statusCode && res.statusCode >= 400) {
                let errBody = '';
                res.setEncoding('utf8');
                res.on('data', (chunk: string) => { errBody += chunk; });
                res.on('end', () => onError(`Anthropic API HTTP ${res.statusCode}: ${errBody || '(no body)'}`));
                return;
            }
            let buf = '';
            res.setEncoding('utf8');
            res.on('data', (chunk: string) => {
                buf += chunk;
                const lines = buf.split('\n');
                buf = lines.pop() ?? '';
                for (const line of lines) {
                    if (!line.startsWith('data: ')) { continue; }
                    const data = line.slice('data: '.length).trim();
                    if (!data) { continue; }
                    try {
                        const evt = JSON.parse(data);
                        if (evt.type === 'content_block_delta' && evt.delta?.type === 'text_delta') {
                            onDelta(evt.delta.text as string);
                        } else if (evt.type === 'error') {
                            onError(evt.error?.message ?? 'unknown Anthropic error');
                        }
                    } catch { /* skip malformed SSE line */ }
                }
            });
            res.on('end', onDone);
        },
    );
    req.on('error', (err) => onError(err.message));
    req.write(body);
    req.end();
    return req;
}

// ── Claude CLI backend ───────────────────────────────────────────────────────
//
// A THIRD, distinct backend, added after confirming live (via a real
// `claude -p` invocation, not just documentation) that Anthropic's own CLI
// honors CLAUDE_CODE_OAUTH_TOKEN in non-interactive/print mode and CAN read
// local images through its own Read tool -- unlike the direct-Anthropic
// backend above, this rides the user's Claude Pro/Max/Team/Enterprise
// SUBSCRIPTION (via `claude setup-token`), not metered per-token API
// billing, and unlike the vscode.lm 'claude-code' vendor (Step 6 in
// agent_usage_guide.md), it actually produces real output because it drives
// the CLI's own documented headless entry point instead of an internal,
// UI-only vscode.lm registration.
//
// IMPORTANT: `--bare` mode does NOT read CLAUDE_CODE_OAUTH_TOKEN (confirmed
// in Anthropic's own docs) -- never add that flag here.

interface ClaudeCliResult {
    text: string;
    totalCostUsd: number | null;
}

/** Writes each image_url part to a real temp file (the CLI's Read tool
 * needs an actual file path, not inline base64) and returns their paths for
 * the caller to reference in the prompt AND clean up afterward. */
function writeTempImages(messages: { role: string; content: string | ContentPart[] }[]): string[] {
    const paths: string[] = [];
    for (const m of messages) {
        if (typeof m.content === 'string') { continue; }
        for (const part of m.content) {
            if (part.type !== 'image_url') { continue; }
            const decoded = decodeDataUrlImage(part.image_url?.url ?? '');
            if (!decoded) { continue; }
            const ext = decoded.mime.split('/')[1]?.split('+')[0] || 'png';
            const filePath = path.join(os.tmpdir(), `vscode-llm-bridge-${Date.now()}-${paths.length}.${ext}`);
            fs.writeFileSync(filePath, Buffer.from(decoded.bytes));
            paths.push(filePath);
        }
    }
    return paths;
}

/** Linearizes an OpenAI-style messages array into the single prompt string
 * `claude -p` takes -- the CLI has no separate structured messages
 * parameter, so system/user/assistant turns are folded into one prompt with
 * clear role labels, ending with an explicit instruction naming any temp
 * image file paths for the Read tool to open. */
function buildClaudeCliPrompt(
    messages: { role: string; content: string | ContentPart[] }[],
    imagePaths: string[],
): string {
    const lines: string[] = [];
    for (const m of messages) {
        const text = typeof m.content === 'string'
            ? m.content
            : m.content.filter(p => p.type === 'text').map(p => p.text).join('\n');
        if (!text.trim()) { continue; }
        const label = m.role === 'system' ? 'Instructions' : m.role === 'assistant' ? 'Assistant' : 'User';
        lines.push(`${label}: ${text.trim()}`);
    }
    if (imagePaths.length) {
        lines.push(`Also use the Read tool to view the following image(s) before answering: ${imagePaths.join(', ')}`);
    }
    return lines.join('\n\n');
}

/** Spawns `claude -p <prompt> --allowedTools Read --output-format json` with
 * CLAUDE_CODE_OAUTH_TOKEN injected directly into the child process's
 * environment (NOT relied upon from the ambient shell -- an env var set in
 * one terminal after VS Code already started never reaches an
 * already-running extension host, confirmed the hard way). `--allowedTools
 * Read` is the minimum needed for image input and nothing more invasive
 * (no Bash/Edit) -- this bridge should never grant a headless CLI call
 * write/execute access on the caller's behalf without being asked to. */
function callClaudeCli(oauthToken: string, prompt: string, model?: string): Promise<ClaudeCliResult> {
    return new Promise((resolve, reject) => {
        // The prompt is sent over stdin, NEVER as a CLI argument (`claude -p`
        // with no positional prompt reads stdin instead -- documented at
        // code.claude.com/docs/en/headless). This isn't just style: on
        // Windows, `shell: true` (required below so CreateProcess can resolve
        // `claude`, which is a .cmd/.ps1 shim from a global npm install, not a
        // .exe) makes Node join the command into a single string for cmd.exe
        // with only naive space-wrapping -- an embedded newline (which a
        // multi-turn prompt always has, from buildClaudeCliPrompt's `\n\n`
        // joins between turns) breaks cmd.exe's line-based parsing and
        // silently truncates everything after it, dropping the trailing
        // `--output-format json` flag. Reproduced live: a single-line
        // first-turn prompt worked; the very next multi-line, multi-turn
        // prompt made `claude` fall back to its default plain-text output,
        // which then failed the JSON.parse below with the model's own reply
        // text as the "unparseable" content -- not a truncated/garbled
        // prompt, but a real answer to a request that silently lost one flag.
        // Fixed args (no dynamic content past `--model`, which is a short,
        // space-free id) never touch this failure mode.
        const args = ['-p', '--allowedTools', 'Read', '--output-format', 'json'];
        if (model) { args.push('--model', model); }

        const child = childProcess.spawn('claude', args, {
            env: { ...process.env, CLAUDE_CODE_OAUTH_TOKEN: oauthToken },
            shell: process.platform === 'win32',
        });

        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8'); });
        child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8'); });

        child.on('error', (err) => {
            reject(new Error(
                `Could not launch 'claude' CLI (${err.message}). Confirm it's installed and on PATH `
                + `(run "claude --version" in a terminal to check).`,
            ));
        });

        child.on('close', (code) => {
            if (code !== 0) {
                reject(new Error(`claude CLI exited with code ${code}: ${stderr || '(no stderr)'}`));
                return;
            }
            try {
                const parsed = JSON.parse(stdout);
                if (parsed.is_error) {
                    reject(new Error(`claude CLI reported an error: ${parsed.result ?? stdout}`));
                    return;
                }
                resolve({ text: parsed.result ?? '', totalCostUsd: parsed.total_cost_usd ?? null });
            } catch {
                reject(new Error(`Could not parse claude CLI output as JSON: ${stdout.slice(0, 500)}`));
            }
        });

        // Swallow EPIPE if the process has already exited by the time we
        // write -- surfaced instead via the exit code / stderr above, not as
        // an unhandled 'error' event crashing the extension host.
        child.stdin.on('error', () => { /* handled via close/error above */ });
        child.stdin.write(prompt, 'utf8');
        child.stdin.end();
    });
}

/** Convert an OpenAI-format messages array to vscode.LanguageModelChatMessage[]. */
function convertMessages(
    messages: { role: string; content: string | ContentPart[] }[],
): vscode.LanguageModelChatMessage[] {
    const result: vscode.LanguageModelChatMessage[] = [];
    const imagesSupported = supportsImageParts();
    let droppedImages = 0;

    // Collect system messages and prepend as a single User turn
    const systemParts = messages
        .filter(m => m.role === 'system')
        .map(m => (typeof m.content === 'string' ? m.content : m.content.map(p => (p.type === 'text' ? p.text : '')).join('\n')).trim())
        .filter(Boolean);

    if (systemParts.length > 0) {
        result.push(vscode.LanguageModelChatMessage.User(`[SYSTEM]\n${systemParts.join('\n\n')}`));
    }

    // Non-system messages in order
    for (const m of messages) {
        if (m.role === 'system') { continue; }

        if (typeof m.content === 'string') {
            const content = m.content.trim();
            if (!content) { continue; }
            result.push(m.role === 'assistant'
                ? vscode.LanguageModelChatMessage.Assistant(content)
                // 'user' or any unknown role → User
                : vscode.LanguageModelChatMessage.User(content));
            continue;
        }

        // Multi-part content (text + image_url) -- vision-capable request.
        const parts: (vscode.LanguageModelTextPart | vscode.LanguageModelDataPart)[] = [];
        for (const part of m.content) {
            if (part.type === 'text') {
                const text = (part.text ?? '').trim();
                if (text) { parts.push(new vscode.LanguageModelTextPart(text)); }
            } else if (part.type === 'image_url') {
                if (!imagesSupported) { droppedImages++; continue; }
                const decoded = decodeDataUrlImage(part.image_url?.url ?? '');
                if (!decoded) { droppedImages++; continue; }
                parts.push(vscode.LanguageModelDataPart.image(decoded.bytes, decoded.mime));
            }
        }
        if (!parts.length) { continue; }
        result.push(m.role === 'assistant'
            ? vscode.LanguageModelChatMessage.Assistant(parts)
            : vscode.LanguageModelChatMessage.User(parts));
    }

    if (droppedImages > 0) {
        // Never silently degrade a vision request into a text-only one
        // without saying so -- the caller (a Responder judging a real
        // camera frame) needs to know its verdict is now unfounded, not
        // trust a confident-looking answer that never actually saw the image.
        result.push(vscode.LanguageModelChatMessage.User(
            `[BRIDGE NOTICE] ${droppedImages} image part(s) in this request could not be delivered ` +
            `(installed VS Code's Language Model API has no image support, or the image data was ` +
            `malformed) -- answer ONLY from the text above, and say explicitly that no image was seen ` +
            `rather than guessing.`,
        ));
    }

    // Ensure the final message is from the user (Copilot requires this)
    if (!result.length || result[result.length - 1].role !== vscode.LanguageModelChatMessageRole.User) {
        result.push(vscode.LanguageModelChatMessage.User('Please continue.'));
    }

    return result;
}

// ── HTTP request handler ──────────────────────────────────────────────────────

async function handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
): Promise<void> {
    addCorsHeaders(req, res);

    // Pre-flight
    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    const url = req.url ?? '/';

    // ── GET / ────────────────────────────────────────────────────────────────
    // Serves the bundled browser test UI (media/index.html) -- a human-facing
    // convenience, not part of the programmatic contract. Cached in memory
    // after the first read since it never changes at runtime.
    if (req.method === 'GET' && url === '/') {
        try {
            if (!_uiHtmlCache) {
                const uiPath = path.join(_extensionPath ?? __dirname, 'media', 'index.html');
                _uiHtmlCache = fs.readFileSync(uiPath, 'utf8');
            }
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            res.end(_uiHtmlCache);
        } catch (err) {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: `Could not load UI: ${err instanceof Error ? err.message : String(err)}` }));
        }
        return;
    }

    // ── GET /health ──────────────────────────────────────────────────────────
    if (req.method === 'GET' && url === '/health') {
        const models = await getCopilotModels();
        const modelName = models[0]?.name ?? 'unknown';
        const anthropicConfigured = Boolean(await _secrets?.get(ANTHROPIC_API_KEY_SECRET));
        const claudeCliConfigured = Boolean(await _secrets?.get(CLAUDE_CLI_OAUTH_TOKEN_SECRET));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            status: 'ok',
            model: modelName,
            port: BRIDGE_PORT,
            available: models.length > 0,
            anthropicConfigured,
            claudeCliConfigured,
        }));
        return;
    }

    // ── GET /models ──────────────────────────────────────────────────────────
    if (req.method === 'GET' && url === '/models') {
        const models = await getCopilotModels();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            models: models.map(m => ({ id: m.id, name: m.name, vendor: m.vendor })),
        }));
        return;
    }

    // ── GET /extensions ──────────────────────────────────────────────────────
    // Diagnostic only: every installed extension's real id/name/activation
    // state, straight from vscode.extensions.all -- ground truth for "is an
    // extension actually named/published as Claude Code installed at all",
    // independent of (and a cross-check against) whatever vendor strings
    // GET /vendors reports. A vscode.lm vendor string is just a label an
    // extension chose when registering -- it is NOT proof of which real
    // extension, if any, is behind it. Optional ?filter=<substring> narrows
    // the list (case-insensitive match against id or name).
    if (req.method === 'GET' && url?.startsWith('/extensions')) {
        const parsed = new URL(url, `http://127.0.0.1:${BRIDGE_PORT}`);
        const filter = (parsed.searchParams.get('filter') ?? '').toLowerCase();
        const all = vscode.extensions.all
            .map(e => ({
                id: e.id,
                name: (e.packageJSON as { displayName?: string; name?: string })?.displayName
                    ?? (e.packageJSON as { name?: string })?.name ?? e.id,
                publisher: (e.packageJSON as { publisher?: string })?.publisher ?? '',
                isActive: e.isActive,
            }))
            .filter(e => !filter || e.id.toLowerCase().includes(filter) || e.name.toLowerCase().includes(filter));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ count: all.length, extensions: all }));
        return;
    }

    // ── GET /vendors ─────────────────────────────────────────────────────────
    // Diagnostic only: every vscode.lm-registered model from EVERY vendor,
    // not just 'copilot'. This bridge's own /v1/chat/completions endpoint
    // stays pinned to vendor 'copilot' regardless of what this returns --
    // it exists purely to answer "is anything else (e.g. a Claude Code-style
    // assistant) registered as its own vscode.lm vendor in this VS Code
    // window right now" empirically. Cross-check with GET /extensions --
    // a vendor string is just a self-chosen label, not proof of which real
    // extension (if any) is actually behind it.
    if (req.method === 'GET' && url === '/vendors') {
        const models = await getAllModels();
        const vendors = [...new Set(models.map(m => m.vendor))];
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            vendors,
            models: models.map(m => ({
                id: m.id, name: m.name, vendor: m.vendor, family: m.family,
                // true = this extension can send it a request right now; false = explicitly
                // denied; undefined = consent was never asked (VS Code shows a consent dialog
                // ONLY on a sendRequest() call made in direct response to a user action --
                // an HTTP-triggered call like this bridge's never qualifies, so this reads
                // undefined for any vendor beyond whatever's already been approved in the past).
                canSendRequest: _accessInfo?.canSendRequest(m),
            })),
        }));
        return;
    }

    // ── GET /anthropic/models ────────────────────────────────────────────────
    // Diagnostic + lookup helper for the direct-Anthropic backend below --
    // this bridge never hardcodes a "current" Anthropic model id (they
    // change over time), so this proxies Anthropic's own GET /v1/models
    // using whichever API key is configured, letting a caller discover
    // real, currently-valid ids to pass as "model" when using
    // {"backend": "anthropic"}.
    if (req.method === 'GET' && url === '/anthropic/models') {
        const apiKey = await _secrets?.get(ANTHROPIC_API_KEY_SECRET);
        if (!apiKey) {
            res.writeHead(401, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'No Anthropic API key configured. Run "LLM Bridge: Set Anthropic API Key" first.' }));
            return;
        }
        const anthropicReq = https.request(
            {
                hostname: 'api.anthropic.com', path: '/v1/models', method: 'GET',
                headers: { 'x-api-key': apiKey, 'anthropic-version': ANTHROPIC_API_VERSION },
            },
            (anthropicRes) => {
                let body = '';
                anthropicRes.setEncoding('utf8');
                anthropicRes.on('data', (chunk: string) => { body += chunk; });
                anthropicRes.on('end', () => {
                    res.writeHead(anthropicRes.statusCode ?? 502, { 'Content-Type': 'application/json' });
                    res.end(body);
                });
            },
        );
        anthropicReq.on('error', (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: `Could not reach Anthropic API: ${err.message}` }));
        });
        anthropicReq.end();
        return;
    }

    // ── POST /v1/chat/completions ─────────────────────────────────────────────
    if (req.method === 'POST' && url === '/v1/chat/completions') {
        let rawBody: string;
        try {
            rawBody = await collectBody(req);
        } catch {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Failed to read request body' }));
            return;
        }

        let payload: {
            messages?: { role: string; content: string | ContentPart[] }[];
            model?: string;
            vendor?: string;
            max_tokens?: number;
            temperature?: number;
            stream?: boolean;
            justification?: string;
            model_options?: { [name: string]: unknown };
            backend?: string;
        };
        try {
            payload = JSON.parse(rawBody);
        } catch {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Invalid JSON' }));
            return;
        }

        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        if (!messages.length) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'messages array is required and must not be empty' }));
            return;
        }

        // ── backend: "anthropic" -- bypasses vscode.lm entirely ──────────────
        // Default remains vscode.lm ("copilot" vendor) for full backward
        // compatibility; this branch only runs when explicitly requested.
        if (payload.backend === 'anthropic') {
            const apiKey = await _secrets?.get(ANTHROPIC_API_KEY_SECRET);
            if (!apiKey) {
                res.writeHead(401, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    error: 'No Anthropic API key configured. Run "LLM Bridge: Set Anthropic API Key" '
                        + 'from the VS Code command palette (key from console.anthropic.com -- note this '
                        + 'is separate billing from a Claude.ai Pro/Max chat subscription).',
                }));
                return;
            }
            if (!payload.model) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    error: 'backend "anthropic" requires an explicit "model" field (e.g. a model id '
                        + 'from GET /anthropic/models) -- this bridge never hardcodes a default Anthropic '
                        + 'model id, since it goes stale the moment Anthropic ships a new one.',
                }));
                return;
            }

            const { system, messages: anthropicMessages } = convertMessagesToAnthropic(messages);
            if (!anthropicMessages.length) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: 'No non-empty user/assistant messages after conversion' }));
                return;
            }

            res.writeHead(200, {
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            });

            const chatId = `chatcmpl-${Date.now()}`;
            let chunkCount = 0;
            let charCount = 0;
            _output?.appendLine(`[chat] backend=anthropic model=${payload.model} messages=${anthropicMessages.length}`);

            const anthropicReq = callAnthropicMessages(
                apiKey,
                payload.model,
                system,
                anthropicMessages,
                payload.max_tokens ?? 1024,
                (text) => {
                    chunkCount++;
                    charCount += text.length;
                    const sseData = JSON.stringify({
                        id: chatId, object: 'chat.completion.chunk',
                        choices: [{ index: 0, delta: { content: text }, finish_reason: null }],
                    });
                    res.write(`data: ${sseData}\n\n`);
                },
                () => {
                    _output?.appendLine(`[chat] backend=anthropic model=${payload.model} done: ${chunkCount} chunk(s), ${charCount} char(s) total`);
                    const doneData = JSON.stringify({
                        id: chatId, object: 'chat.completion.chunk',
                        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
                    });
                    res.write(`data: ${doneData}\n\n`);
                    res.write('data: [DONE]\n\n');
                    res.end();
                },
                (message) => {
                    _output?.appendLine(`[chat] backend=anthropic model=${payload.model} ERROR: ${message}`);
                    res.write(`data: ${JSON.stringify({ error: message })}\n\n`);
                    res.end();
                },
            );
            req.on('close', () => anthropicReq.destroy());
            return;
        }

        // ── backend: "claude-cli" -- shells out to `claude -p`, rides the ──
        // user's Claude subscription (via CLAUDE_CODE_OAUTH_TOKEN), not
        // metered API billing. Not streaming internally (claude -p
        // --output-format json blocks until done) -- emitted as a single
        // SSE delta plus a done event carrying the real cost this call
        // incurred, so a caller can track spend per request.
        if (payload.backend === 'claude-cli') {
            const oauthToken = await _secrets?.get(CLAUDE_CLI_OAUTH_TOKEN_SECRET);
            if (!oauthToken) {
                res.writeHead(401, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    error: 'No Claude CLI OAuth token configured. Run "LLM Bridge: Set Claude CLI OAuth Token" '
                        + 'from the command palette (generate one with `claude setup-token` in a terminal -- '
                        + 'requires an active Claude Pro/Max/Team/Enterprise subscription).',
                }));
                return;
            }

            let tempImagePaths: string[] = [];
            let prompt: string;
            try {
                tempImagePaths = writeTempImages(messages);
                prompt = buildClaudeCliPrompt(messages, tempImagePaths);
            } catch (err) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: `Failed to prepare claude-cli request: ${err instanceof Error ? err.message : String(err)}` }));
                return;
            }
            if (!prompt.trim()) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: 'No non-empty message content to send to claude-cli' }));
                return;
            }

            res.writeHead(200, {
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            });

            const chatId = `chatcmpl-${Date.now()}`;
            _output?.appendLine(`[chat] backend=claude-cli images=${tempImagePaths.length} promptChars=${prompt.length}`);

            const cleanupTempImages = () => {
                for (const p of tempImagePaths) {
                    try { fs.unlinkSync(p); } catch { /* best-effort cleanup */ }
                }
            };

            try {
                const result = await callClaudeCli(oauthToken, prompt, payload.model);
                _output?.appendLine(
                    `[chat] backend=claude-cli done: ${result.text.length} char(s), `
                    + `total_cost_usd=${result.totalCostUsd ?? 'unknown'}`,
                );
                const sseData = JSON.stringify({
                    id: chatId, object: 'chat.completion.chunk',
                    choices: [{ index: 0, delta: { content: result.text }, finish_reason: null }],
                });
                res.write(`data: ${sseData}\n\n`);
                const doneData = JSON.stringify({
                    id: chatId, object: 'chat.completion.chunk',
                    choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
                    usage: { total_cost_usd: result.totalCostUsd },
                });
                res.write(`data: ${doneData}\n\n`);
                res.write('data: [DONE]\n\n');
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                _output?.appendLine(`[chat] backend=claude-cli ERROR: ${message}`);
                res.write(`data: ${JSON.stringify({ error: message })}\n\n`);
            } finally {
                cleanupTempImages();
                res.end();
            }
            return;
        }

        // vendor (added alongside GET /vendors): defaults to 'copilot' for
        // full backward compatibility with every existing caller that never
        // sends this field. Any vendor GET /vendors reports can be requested
        // explicitly -- e.g. "claude-code" -- to reach a DIFFERENT
        // registered chat-model provider with its own separate
        // account/quota, entirely independent of Copilot's.
        const requestedVendor = payload.vendor || 'copilot';
        const models = await vscode.lm.selectChatModels({ vendor: requestedVendor });
        if (!models.length) {
            res.writeHead(503, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                error: `No model available for vendor '${requestedVendor}'. ` +
                    `Check GET /vendors for what's actually registered right now.`,
            }));
            return;
        }

        // Use the requested model id if it matches, else default to first
        const requestedId = payload.model ?? '';
        const model = (requestedId && requestedId !== 'copilot')
            ? (models.find(m => m.id === requestedId) ?? models[0])
            : models[0];

        const lmMessages = convertMessages(messages);

        // Set up SSE response
        res.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        });

        const cts = new vscode.CancellationTokenSource();
        req.on('close', () => cts.cancel());

        const chatId = `chatcmpl-${Date.now()}`;
        _output?.appendLine(
            `[chat] vendor=${requestedVendor} model=${model.id} messages=${lmMessages.length} `
            + `(request had ${messages.length} raw message(s)); canSendRequest=${_accessInfo?.canSendRequest(model)}`,
        );

        let chunkCount = 0;
        let charCount = 0;
        try {
            const requestOptions: vscode.LanguageModelChatRequestOptions = {};
            if (payload.justification) { requestOptions.justification = payload.justification; }
            if (payload.model_options) { requestOptions.modelOptions = payload.model_options; }
            const response = await model.sendRequest(lmMessages, requestOptions, cts.token);
            for await (const chunk of response.text) {
                if (cts.token.isCancellationRequested) { break; }
                chunkCount++;
                charCount += chunk.length;
                const sseData = JSON.stringify({
                    id: chatId,
                    object: 'chat.completion.chunk',
                    choices: [
                        {
                            index: 0,
                            delta: { content: chunk },
                            finish_reason: null,
                        },
                    ],
                });
                res.write(`data: ${sseData}\n\n`);
            }
            _output?.appendLine(
                `[chat] vendor=${requestedVendor} model=${model.id} done: ${chunkCount} chunk(s), ${charCount} char(s) total`
                + (chunkCount === 0 ? ' -- EMPTY RESPONSE (no text ever emitted by this provider for this request)' : ''),
            );
            // Terminal event — finish_reason: stop
            const doneData = JSON.stringify({
                id: chatId,
                object: 'chat.completion.chunk',
                choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
            });
            res.write(`data: ${doneData}\n\n`);
            res.write('data: [DONE]\n\n');
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            _output?.appendLine(`[chat] vendor=${requestedVendor} model=${model.id} ERROR: ${msg}`);
            if (!cts.token.isCancellationRequested) {
                res.write(`data: ${JSON.stringify({ error: msg })}\n\n`);
            }
        } finally {
            cts.dispose();
            res.end();
        }
        return;
    }

    // ── 404 fallback ─────────────────────────────────────────────────────────
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not found' }));
}

// ── Server lifecycle ──────────────────────────────────────────────────────────

function startServer(outputChannel: vscode.OutputChannel): Promise<void> {
    return new Promise((resolve, reject) => {
        if (_server) {
            outputChannel.appendLine('[bridge] Server already running.');
            resolve();
            return;
        }

        const server = http.createServer((req: http.IncomingMessage, res: http.ServerResponse) => {
            handleRequest(req, res).catch(err => {
                outputChannel.appendLine(`[bridge] Unhandled error: ${err}`);
                if (!res.headersSent) {
                    res.writeHead(500, { 'Content-Type': 'application/json' });
                }
                res.end(JSON.stringify({ error: 'Internal server error' }));
            });
        });

        server.on('error', (err: NodeJS.ErrnoException) => {
            if (err.code === 'EADDRINUSE') {
                outputChannel.appendLine(`[bridge] Port ${BRIDGE_PORT} already in use — another bridge instance may be running.`);
                setStatusBar('conflict');
            } else {
                outputChannel.appendLine(`[bridge] Server error: ${err.message}`);
                setStatusBar('error');
            }
            reject(err);
        });

        // Bind only to loopback — never expose to the network
        server.listen(BRIDGE_PORT, '127.0.0.1', () => {
            _server = server;
            outputChannel.appendLine(`[bridge] Listening on http://127.0.0.1:${BRIDGE_PORT}`);
            setStatusBar('running');
            resolve();
        });
    });
}

function stopServer(outputChannel: vscode.OutputChannel): Promise<void> {
    return new Promise(resolve => {
        if (!_server) {
            resolve();
            return;
        }
        _server.close(() => {
            _server = undefined;
            outputChannel.appendLine('[bridge] Server stopped.');
            setStatusBar('stopped');
            resolve();
        });
    });
}

// ── Status bar ────────────────────────────────────────────────────────────────

type BridgeState = 'running' | 'stopped' | 'error' | 'conflict';

function setStatusBar(state: BridgeState): void {
    if (!_statusBar) { return; }
    switch (state) {
        case 'running':
            _statusBar.text = `$(plug) LLM Bridge :${BRIDGE_PORT}`;
            _statusBar.tooltip = `VS Code LLM Bridge running on port ${BRIDGE_PORT}\nClick to open the test UI in your browser.`;
            _statusBar.backgroundColor = undefined;
            refreshStatusBarDetails();
            break;
        case 'stopped':
            _statusBar.text = '$(debug-disconnect) LLM Bridge: stopped';
            _statusBar.tooltip = 'VS Code LLM Bridge is stopped. Run "LLM Bridge: Start" to restart.';
            _statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
            break;
        case 'error':
        case 'conflict':
            _statusBar.text = '$(warning) LLM Bridge: error';
            _statusBar.tooltip = state === 'conflict'
                ? `Port ${BRIDGE_PORT} is already in use`
                : 'VS Code LLM Bridge encountered an error';
            _statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
            break;
    }
    _statusBar.show();
}

/** Fills in the "running" status bar's text/tooltip with real vendor/model
 * counts and backend configuration state, queried live from vscode.lm and
 * SecretStorage -- previously this just said "running on port 8766" with no
 * indication of what was actually reachable behind it. Runs asynchronously
 * after setStatusBar('running') already showed a basic tooltip, so a slow
 * vscode.lm query never blocks the status bar from appearing. */
async function refreshStatusBarDetails(): Promise<void> {
    if (!_statusBar) { return; }
    try {
        const [copilotModels, allModels, anthropicConfigured, claudeCliConfigured] = await Promise.all([
            getCopilotModels(),
            getAllModels(),
            Promise.resolve(_secrets?.get(ANTHROPIC_API_KEY_SECRET)).then(Boolean),
            Promise.resolve(_secrets?.get(CLAUDE_CLI_OAUTH_TOKEN_SECRET)).then(Boolean),
        ]);
        if (!_server || !_statusBar) { return; } // bridge was stopped/torn down while this was in flight
        const vendors = [...new Set(allModels.map(m => m.vendor))];
        _statusBar.text = `$(plug) LLM Bridge :${BRIDGE_PORT} ($(circuit-board) ${allModels.length})`;
        const md = new vscode.MarkdownString(undefined, true);
        md.appendMarkdown(`**VS Code LLM Bridge** — running on port ${BRIDGE_PORT}\n\n`);
        md.appendMarkdown(`- Vendors registered: ${vendors.length ? vendors.join(', ') : '(none)'}\n`);
        md.appendMarkdown(`- Models registered: ${allModels.length} total (Copilot: ${copilotModels.length})\n`);
        md.appendMarkdown(`- Copilot picker model: ${copilotModels[0]?.name ?? 'none signed in'}\n`);
        md.appendMarkdown(`- Anthropic backend: ${anthropicConfigured ? 'configured' : 'not set'}\n`);
        md.appendMarkdown(`- Claude CLI backend: ${claudeCliConfigured ? 'configured' : 'not set'}\n\n`);
        md.appendMarkdown(`Click to open the test UI in your browser.`);
        _statusBar.tooltip = md;
    } catch (err) {
        // Non-fatal -- leave the plain tooltip setStatusBar('running') already set.
        _output?.appendLine(`[bridge] Could not refresh status bar details: ${err}`);
    }
}

// ── Extension entry points ────────────────────────────────────────────────────

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    const output = vscode.window.createOutputChannel('VS Code LLM Bridge');
    context.subscriptions.push(output);
    _output = output;
    _accessInfo = context.languageModelAccessInformation;
    _secrets = context.secrets;
    _extensionPath = context.extensionUri.fsPath;
    output.appendLine('[bridge] Extension activating…');

    _statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    // Clicking the status bar opens the browser test UI rather than stopping
    // the bridge -- stopping is a deliberate, rarer action better reached via
    // the command palette than a one-click accident on the status bar.
    _statusBar.command = 'vscodeLlmBridge.openUi';
    context.subscriptions.push(_statusBar);

    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.openUi', () => {
            vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${BRIDGE_PORT}/`));
        }),
    );

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.start', async () => {
            try {
                await startServer(output);
                vscode.window.showInformationMessage(`VS Code LLM Bridge started on port ${BRIDGE_PORT}.`);
            } catch (err) {
                vscode.window.showErrorMessage(`Failed to start LLM Bridge: ${err}`);
            }
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.stop', async () => {
            await stopServer(output);
            vscode.window.showInformationMessage('VS Code LLM Bridge stopped.');
        }),
    );

    // Direct-Anthropic backend key management -- stored via SecretStorage,
    // NEVER in plaintext workspace/user settings (an earlier sketch of this
    // feature used vscode.workspace.getConfiguration() for the API key,
    // which would have written it in cleartext to settings.json -- fixed
    // here before ever shipping that version).
    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.setAnthropicKey', async () => {
            const key = await vscode.window.showInputBox({
                prompt: 'Anthropic API key (from console.anthropic.com -- separate billing from a Claude.ai Pro/Max chat subscription)',
                password: true,
                ignoreFocusOut: true,
                placeHolder: 'sk-ant-...',
            });
            if (!key) { return; }
            await _secrets?.store(ANTHROPIC_API_KEY_SECRET, key.trim());
            vscode.window.showInformationMessage('Anthropic API key stored securely. Use {"backend": "anthropic", "model": "..."} in requests.');
        }),
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.clearAnthropicKey', async () => {
            await _secrets?.delete(ANTHROPIC_API_KEY_SECRET);
            vscode.window.showInformationMessage('Anthropic API key cleared.');
        }),
    );

    // Claude CLI backend token management -- same SecretStorage discipline
    // as the Anthropic key above. Generate the token with `claude
    // setup-token` in a terminal first (requires an active Claude
    // subscription); this command only stores whatever you paste in.
    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.setClaudeCliToken', async () => {
            const token = await vscode.window.showInputBox({
                prompt: 'Claude CLI OAuth token (generate with `claude setup-token` in a terminal -- requires an active Claude Pro/Max/Team/Enterprise subscription)',
                password: true,
                ignoreFocusOut: true,
            });
            if (!token) { return; }
            await _secrets?.store(CLAUDE_CLI_OAUTH_TOKEN_SECRET, token.trim());
            vscode.window.showInformationMessage('Claude CLI OAuth token stored securely. Use {"backend": "claude-cli"} in requests.');
        }),
    );
    context.subscriptions.push(
        vscode.commands.registerCommand('vscodeLlmBridge.clearClaudeCliToken', async () => {
            await _secrets?.delete(CLAUDE_CLI_OAUTH_TOKEN_SECRET);
            vscode.window.showInformationMessage('Claude CLI OAuth token cleared.');
        }),
    );

    // Auto-start on activation
    try {
        await startServer(output);
    } catch (err) {
        output.appendLine(`[bridge] Auto-start failed: ${err}`);
        // Non-fatal — user can start manually via command
    }

    output.appendLine('[bridge] Extension activated.');
}

export async function deactivate(): Promise<void> {
    if (_server) {
        await new Promise<void>(resolve => _server!.close(() => resolve()));
        _server = undefined;
    }
}
