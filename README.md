# ai-workers

[![CI](https://github.com/msozturktr/ai-workers/actions/workflows/ci.yml/badge.svg)](https://github.com/msozturktr/ai-workers/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

A worker-pool [MCP](https://modelcontextprotocol.io) server for Claude Code. Claude stays the
orchestrator (planning, decisions, review); mechanical work such as bulk summaries,
translation, classification and log scanning goes to cheap or free API models
(Groq, Gemini, OpenRouter).

The main trick is **file feeding**: Claude passes only paths, the server reads the files and
returns only the worker's answer, so file contents never enter Claude's context. In a measured
run this cut Claude-side tokens by **~85%** ([details](#measured-token-savings)).

- Standard library only: no `pip install` for the server, CLI and dashboard.
- Automatic fallback across providers, retry with `Retry-After`, per-job failure isolation.
- Secret files (`.env`, keys, `~/.ssh`, ...) are never sent to a provider.
- Desktop app with a live **activity log**: who sent what to which model, every retry and
  fallback, the exact prompts and responses, and what was returned to Claude.
- Remaining free-tier quota for every provider (and Claude itself).

![Activity log](docs/activity.png)

## Quick start

Requirements: Python 3.10+ and an API key for at least one provider. `install.sh` (CLI,
systemd dashboard service, menu shortcut) targets Linux; the MCP server itself runs anywhere
Python does.

```bash
git clone https://github.com/msozturktr/ai-workers.git
cd ai-workers

mkdir -p ~/.config/ai-workers
printf 'GROQ_API_KEY=...\n' > ~/.config/ai-workers/env    # see "Keys" below
chmod 600 ~/.config/ai-workers/env

claude mcp add ai-workers --scope user -- python3 "$PWD/server.py"
bash install.sh       # optional: `ai-workers` CLI + dashboard
ai-workers doctor     # optional: check the setup
```

Then just ask Claude Code for the work, e.g. *"summarize every file in src/ in 3 bullets"*;
the server's instructions tell it to use `fanout` with `files`.

## How it works

```
you -> Claude Code (planning, decision, quality control)
         |
     MCP: ai-workers
       +-- delegate(role, task, files?)   single job
       +-- fanout(items[] | files[])      N parallel jobs (one per file)
       +-- ask(provider, prompt)          direct model call
       +-- models(provider)               live model list
       +-- org_status()                   key/role status
```

## Default roles

| role        | provider   | model                                 | useful for                         |
|-------------|------------|---------------------------------------|------------------------------------|
| researcher  | gemini     | gemini-3.6-flash                      | long document reading, research   |
| summarizer  | groq       | openai/gpt-oss-120b                   | bulk summarization                |
| coder       | groq       | openai/gpt-oss-120b                   | code scaffolding, boilerplate      |
| reviewer    | openrouter | nvidia/nemotron-3-ultra-550b-a55b:free| second-eye / critique              |
| translator  | groq       | openai/gpt-oss-120b                   | translation                        |
| classifier  | groq       | openai/gpt-oss-20b                    | tagging, triage (fastest)          |
| extractor   | groq       | openai/gpt-oss-120b                   | structured JSON extraction |

Bulk jobs run on Groq (free, 1,000 requests/day); Gemini (Tier 1, paid) is used only for the researcher role and for large-context jobs that exceed Groq’s input budget.

Output language: workers reply in the language of the **task** instruction, not of the input
data (e.g. an English task over Turkish-commented code gets an English answer), unless the
task asks for another language. A length/format requested in the task overrides the role's
default format. Role system prompts are in English; a rule enforcing this is appended to
every role, including custom roles from `config.json`.

## File feeding (the main source of token savings)

`delegate` and `fanout` accept a `files` parameter: absolute path, directory, or glob (`~` works).  
The server reads the file itself; the content never enters Claude’s context, only the worker’s output is returned.

```python
delegate(role="summarizer", task="...", files=["~/code/myapp/README.md"])
fanout(role="classifier", task="...", files=["~/code/myapp/src/**/*.py"])
```

- `delegate`: all files are merged into a single input.  
- `fanout`: each file is a separate job. Maximum 200 jobs.
  - Roles with `whole_files` (`summarizer`, `researcher`, `reviewer`) never split a file; one
    that exceeds Groq's budget falls back to Gemini whole. Chunks lose context and made
    summaries invent things, so understanding-type work gets the full file.
  - Other roles split large files on line boundaries (`chunk_chars`, default: the role's
    provider budget, Groq ≈ 11K, Gemini 200K characters). Fine for local work such as
    extraction or classification. Override per call with `whole_files: true|false`.  
- Skipped: secret files (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `*secret*`, `~/.ssh`, `~/.config/ai-workers` …), binary files, > 8 MB. During directory/glob scans, `.git`, `node_modules`, `__pycache__`, `.godot`, `obj`, `bin`, `.next`, `.venv` are ignored.  
- Provider input budgets (`max_input_chars`): groq 12 K, openrouter 200 K, gemini 1.5 M. Input over a provider's budget falls through to the next provider without a network call.

## Measured token savings

Measured on 2026-10-08 from Claude Code's own transcript (the `usage` of consecutive assistant
messages, i.e. how much each step grew Claude's context). Task: summarize 4 C# source files
(a game project's drivetrain module, ~47K characters, ~1,100 lines) in 3-5 bullets each.

| Claude-side tokens                           | Claude does it (`Read` x4 + writes summaries) | Delegated (`fanout(files=...)`) | Saving   |
|----------------------------------------------|----------------------------------------------:|--------------------------------:|---------:|
| Input added to context                       | ~23,900 (file contents)                       | ~4,250 (worker summaries)       |          |
| Output                                       | ~4,200 (summaries, assumed same length)       | ~70 (the tool call)             |          |
| **Total tokens**                             | **~28,100**                                   | **~4,320**                      | **~85%** |
| Cost-weighted (output x5, cache write x1.25) | ~50,900                                       | ~5,700                          | **~89%** |
| Carried into every later turn (cache read)   | ~28,100                                       | ~4,250                          | ~85%     |

Worker side: 6 of 7 jobs (large files are chunked) ran on Groq for free; 1 hit Groq's 8K TPM
limit and fell back to Gemini (~3.5K input tokens, paid Tier 1).

What the number depends on:

- **Input/output ratio.** Savings are largest when a lot is read and little comes back
  (summaries, classification, log scanning). When the output is as long as the input
  (translation, drafts) it still lands in Claude's context; write it to a file instead
  (`ai-workers run translator "..." -f doc.md > out.md` from a shell). This README was
  translated that way.
- **Verification is not counted.** Worker output is an unreviewed draft; spot-checking it
  costs Claude tokens.
- **Chunking hurts quality.** Whole-file summaries were accurate. Chunks of a large file
  (`Engine.cs` was split in 3) lack context, and the worker guessed API names that do not
  exist. Fixed since: `summarizer`, `researcher` and `reviewer` now send whole files
  (`whole_files`), so a file too big for Groq goes to Gemini in one piece.
- Single measurement (n=1): treat it as an order of magnitude, not a constant.

## Claude Code integration

- The `initialize` response includes `instructions`: Claude sees when to hand off to a worker and how to use `files` in the system prompt of every session.  
- `delegate` and `fanout` are marked with `_meta["anthropic/alwaysLoad"]=true`: they are not deferred to tool search and are loaded with the full schema each session. Other tools remain deferred.  
- Tool calls run in separate threads; a long `fanout` does not block the `ping` or other calls.

Changing/adding roles: `~/.config/ai-workers/config.json`

```json
{
  "roles": {
    "coder": { "provider": "groq", "model": "openai/gpt-oss-120b", "system": "..." },
    "seo":   { "provider": "gemini", "model": "gemini-3.6-flash", "system": "You are an SEO editor..." }
  }
}
```

## Keys

`~/.config/ai-workers/env` (mode 600):

```text
GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
GROQ_API_KEY=...          # https://console.groq.com/keys
OPENROUTER_API_KEY=...    # https://openrouter.ai/keys
```

At least one key is sufficient; work for a provider without a key automatically falls back to the next one (fallback order: groq → gemini → openrouter).

## Installation (as an application)

```bash
bash install.sh
```

Installs:

- `~/.local/bin/ai-workers` – command
- `ai-workers.service` (systemd --user) – dashboard backend, starts automatically on login
- `io.github.msozturktr.AiWorkers.desktop` + icon – the desktop app in the application menu

Check: `ai-workers doctor`

## Desktop app and activity log

`ai-workers` (or the menu entry) opens a native window (GTK 3 + WebKitGTK) with two tabs:

- **Overview** – remaining quota per provider and model, and Claude's own limits.
- **Activity** – every tool call, live:
  - *Now running* strip: calls in flight, the models being waited on, elapsed time.
  - Call list with search and filters (status, tool, provider); pause to freeze it.
  - Call detail: a flow diagram (Claude Code → ai-workers → providers → result), the request
    arguments, a timeline of jobs with one bar segment per provider attempt, and for every
    attempt the system prompt, the exact input, the response, errors, retries (`HTTP 429 –
    waited 3.5 s`), fallbacks, token counts, rate-limit headers and the raw event.
  - Keyboard: `/` search, `j`/`k` or arrows to move, `Ctrl+R` reload, `Ctrl+±` zoom, `F11`.

The window is single-instance and remembers its size. It talks to the dashboard service, or
serves the UI itself when the service is not running. Needs the system packages
`python-gobject` and `webkit2gtk-4.1` (Arch) / `python3-gi gir1.2-webkit2-4.1` (Debian/Ubuntu);
without them `ai-workers app` opens the same UI in the browser (`ai-workers open`).

From a terminal: `ai-workers activity` lists recent calls, `ai-workers activity -f` follows
them live.

**What is logged and where.** Calls from Claude Code (MCP) and from the CLI are written to
`~/.config/ai-workers/trace/` (`events-YYYYMMDD.jsonl` plus deduplicated payload files),
including full prompts, file contents sent to workers and responses. Secret files are filtered
before anything is sent, so they never reach the log either. Old data is pruned automatically;
tune or disable it in `config.json`:

```json
{ "trace": { "enabled": true, "retention_days": 7, "max_mb": 200 } }
```

The dashboard binds to 127.0.0.1 only, rejects requests whose `Host` header is not a loopback
name (DNS-rebinding protection) and never sends CORS headers, so other web pages cannot read
the log.

## Commands

```text
ai-workers                     open the desktop app (same as `ai-workers app`)
ai-workers activity [-f] [-n N] recent calls; -f follows live activity (alias: trace)
ai-workers open                open the dashboard in the browser
ai-workers usage               remaining-usage report  (--json raw data)
ai-workers status              provider + role status
ai-workers roles [-v]          defined roles
ai-workers run <role> "<task>" [-f path ...]   single job; piped stdin is the input (ignored with -f)
ai-workers fanout <role> "<task>" [-f glob ...] each line of stdin / each file becomes a separate parallel job
ai-workers models <provider>   live model list
ai-workers serve               run the dashboard backend in the foreground
ai-workers start|stop|restart|logs   service management
ai-workers doctor              installation check
```

Examples:

```bash
echo "Merhaba dunya" | ai-workers run translator "Translate to English."
ai-workers fanout summarizer "Summarize the file" -f '~/code/myapp/docs/*.md' -c 4
```

## Remaining-usage data

```text
python3 dashboard.py          # http://127.0.0.1:8765
python3 dashboard.py --once   # JSON to terminal
```

Binds only to 127.0.0.1 and refreshes every 20 s. The same data appears in the `usage` tool on the MCP side.

Remaining usage does not come from a single source for each provider:

| provider   | remaining usage | source                                          |
|------------|-----------------|-------------------------------------------------|
| groq       | live, exact     | each response’s `x-ratelimit-*` headers        |
| openrouter | live, exact     | `/api/v1/key` → `free_model_daily_requests`    |
| gemini     | local counter, **model-specific** | no quota header; limits are in `config.json` per model |
| claude     | **live, exact** | `claude -p /usage` → session and weekly remaining % (120 s cache) |

Claude’s live percentages are extracted from the `claude -p /usage` output and cached for 120 s in `~/.config/ai-workers/claude_live.json`; a background thread refreshes the cache when stale, so the dashboard does not wait. This count is based on sessions on this machine only – it does **not** include other devices or claude.ai.

The headline number on the dashboard is **the smallest quota among all providers** (including Claude).

Every request is logged to `~/.config/ai-workers/ledger.jsonl` (timestamp, provider, model, role, tokens, error). Groq’s latest rate-limit status is stored in `~/.config/ai-workers/ratelimit.json`.

Gemini limits are **model-specific** (entered manually from AI Studio → Rate Limits page):

```json
"limits": { "gemini": { "tier": "Tier 1", "models": {
  "gemini-3.6-flash":      { "rpm": 1000, "tpm": 2000000, "rpd": 10000 },
  "gemini-3.5-flash-lite": { "rpm": 4000, "tpm": 4000000, "rpd": 150000 }
}}}
```

The dashboard draws a separate meter for each model (daily requests) and shows minute-level request/token usage underneath. If limits change (tier upgrade), update this file – Gemini does not report changes via the API.

**Note:** Tier 1 is not a free tier; it is a billed level. Free-tier limits are also stored under `free_tier_models` (derived from AI Studio’s “Compare: Free tier” differences) and the dashboard writes “X/Y free remaining” for each Gemini model; it warns when the free quota is exhausted.

The free tier for Gemini is very tight: Flash models **20 requests/day**, Flash Lite **500 requests/day**. For comparison, Groq offers 1,000/day, OpenRouter 50/day. To stay free, bulk jobs should go to Groq; Gemini should be reserved for a few jobs that need its 1M-token context.

## Verified notes (live test 2026-10-01)

- Groq requires a `User-Agent` header; otherwise Cloudflare returns **HTTP 403 error code 1010**.  
- In OpenRouter, `openai/gpt-oss-*:free` was removed; use `models(free_only=true)` for the actual free list.  
- `gemini-2.5-flash-lite` is disabled for new accounts → use `gemini-3.5-flash-lite`.  
- Gemini reasoning models consume tokens during the thinking step: if `max_tokens` < 512 the response may be empty.  
- OpenRouter’s free tier allows ~50 requests/day → use Gemini/Groq for high-volume `fanout`.

## Resilience

- 429/5xx/timeout → 4 retries; respects provider’s `Retry-After` header, otherwise exponential backoff.  
- If a role’s provider completely fails (missing key, input too large, error) it falls back to other providers; a single job failure in a `fanout` does not affect the others.  
- Errors returned in OpenRouter’s HTTP 200 body are counted as failures.  
- Groq free tier allows 8 K tokens/min per model: large parallel jobs may hit 429 and fall back to Gemini.  
- `fanout` concurrency defaults to 4 (to stay within free-tier rate limits); Groq can safely handle up to 8.  
- Model names change in free tiers → verify the live list with `models` instead of guessing.

## Development

```bash
python3 -m unittest discover -s tests -v                    # offline, no keys needed
AI_WORKERS_LIVE=1 python3 -m unittest discover -s tests     # also hits real APIs (uses quota)
```

The offline suite covers file resolution and secret filtering, chunking, provider skip and
fallback logic, role loading, the MCP protocol over stdio, the activity log (span trees,
multi-process writes, retention) and the dashboard API. Tests run with a temporary
`HOME` and no API keys, so they never touch your config, ledger or quotas. CI runs them on
Python 3.10 to 3.13.

Manual smoke test:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"org_status","arguments":{}}}' \
  | python3 server.py
```

Contributions are welcome. Please keep the project dependency-free and add a test for any
behavior change.

## License

[MIT](LICENSE)
