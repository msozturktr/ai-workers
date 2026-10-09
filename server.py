#!/usr/bin/env python3
"""ai-workers: MCP server that exposes free API models to Claude Code as worker tools.

JSON-RPC over stdio. No external dependencies.
"""
import json, os, secrets, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity as T
import providers as P
import usage as U
import sources as S

PROTOCOL = "2025-06-18"
SERVER = {"name": "ai-workers", "version": "1.5.0"}

INSTRUCTIONS = """ai-workers: cheap worker models (Groq/OpenRouter free, Gemini for big inputs) for mechanical text work, so Claude's tokens go to thinking.
Hand off: summaries, translation, classification, extraction, log/test-output scanning, per-file analysis, first-pass review, drafts.
Keep: architecture, code edits, security/data-loss decisions, and verifying worker output (it is an unreviewed draft).
Files: never Read-and-paste. Pass absolute paths/dirs/globs in `files`; the server reads them and skips secrets. Save command output to a file first.
One job -> delegate. Many files/items -> fanout (one job per file). Set max_tokens to what the answer needs: smaller keeps larger inputs on free Groq."""

ALWAYS = {"anthropic/alwaysLoad": True}

TOOLS = [
    {
        "name": "org_status",
        "description": "Organization status: which providers have keys, which roles are defined, which model each role uses. Call as first step or when a worker errors out.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delegate",
        "description": "Run one job on a worker model (role = provider + model + prompt, see org_status). Pass file content via `files`, never pasted. Falls back to other providers automatically.",
        "_meta": ALWAYS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "researcher | summarizer | coder | reviewer | translator | classifier | extractor"},
                "task": {"type": "string", "description": "Instruction, including the output format."},
                "input": {"type": "string", "description": "Short inline data; use files for file content."},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Absolute paths, dirs or globs (~ ok), merged into one input."},
                "model": {"type": "string", "description": "Override the role's model."},
                "max_tokens": {"type": "integer", "description": "Answer budget (default 4096). Lower is cheaper and keeps larger inputs on Groq."},
                "no_fallback": {"type": "boolean", "description": "Only try the role's own provider."},
            },
            "required": ["role", "task"],
        },
    },
    {
        "name": "fanout",
        "description": "Run many jobs in parallel (one per item or per file) instead of repeated delegate calls. File content never enters your context.",
        "_meta": ALWAYS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Default role."},
                "task": {"type": "string", "description": "Default instruction."},
                "items": {
                    "type": "array",
                    "description": "Strings (used as input) or {input, task?, role?, model?, label?}.",
                    "items": {"type": ["string", "object"]},
                },
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Absolute paths/dirs/globs; one job per file (large files chunked unless whole_files)."},
                "chunk_chars": {"type": "integer", "description": "Chunk size; default fits the provider budget."},
                "whole_files": {"type": "boolean", "description": "Never chunk files (default for summarizer, researcher, reviewer)."},
                "concurrency": {"type": "integer", "description": "Parallel requests (default 4; Groq handles up to 8)."},
                "max_tokens": {"type": "integer", "description": "Answer budget per job (default 4096)."},
            },
            "required": [],
        },
    },
    {
        "name": "usage",
        "_meta": {"anthropic/searchHint": "ai-workers quota remaining usage limit"},
        "description": "Remaining usage report: quotas for each provider (Groq/OpenRouter live, Gemini local counter) and today's token consumption on Claude side. Call before starting a bulk fanout or when user asks 'how much is left'.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask",
        "description": "Direct single call to a specific provider/model. Use to bypass role abstraction, try a new model, or perform model comparison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["gemini", "groq", "openrouter"]},
                "prompt": {"type": "string"},
                "system": {"type": "string"},
                "model": {"type": "string"},
                "max_tokens": {"type": "integer"},
                "temperature": {"type": "number"},
            },
            "required": ["provider", "prompt"],
        },
    },
    {
        "name": "models",
        "description": "Fetch live model list of a provider. When model names change (free tiers change frequently), verify the actual list here, do not guess.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["gemini", "groq", "openrouter"]},
                "free_only": {"type": "boolean", "description": "Only meaningful for openrouter: filters models ending with ':free'."},
            },
            "required": ["provider"],
        },
    },
]


def _compose(task, data):
    return f"{task}\n\n--- DATA ---\n{data}" if data else task


def _roles():
    return P.load_roles()


def t_org_status(_a):
    roles = _roles()
    have = P.available_providers()
    lines = ["# ai-workers organization status", "", "## Providers"]
    for name, cfg in P.PROVIDERS.items():
        mark = "READY" if name in have else "NO KEY"
        lines.append(f"- {name}: {mark} (default: {cfg['default_model']}, light: {cfg['light_model']})")
    if not have:
        lines += ["", f"No keys found. Add to {P.ENV_FILE} file:",
                  "  GEMINI_API_KEY=...", "  GROQ_API_KEY=...", "  OPENROUTER_API_KEY=..."]
    lines += ["", "## Roles"]
    for name, spec in roles.items():
        ready = "" if spec["provider"] in have else "  [provider not ready -> fallback will be used]"
        lines.append(f"- {name}: {spec['provider']} / {spec['model']}{ready}")
    lines += ["", f"To edit roles: {P.CONFIG_FILE}"]
    return "\n".join(lines)


def t_delegate(a):
    roles = _roles()
    role = a["role"]
    if role not in roles:
        return f"ERROR: role '{role}' does not exist. Available roles: {', '.join(roles)}"
    spec = roles[role]
    kw = dict(system=spec.get("system"), model=a.get("model") or spec.get("model"),
              max_tokens=a.get("max_tokens", 4096), role=role,
              reasoning_effort=spec.get("reasoning_effort"))
    data, nfiles, skipped = a.get("input"), 0, []
    if a.get("files"):
        text, nfiles, skipped, err = S.bundle(a["files"])
        if err:
            return f"ERROR: {err}"
        if not nfiles:
            return "ERROR: no readable files" + S.format_skipped(skipped)
        data = f"{data}\n\n{text}" if data else text
    prompt = _compose(a["task"], data)
    with T.span("job", label="delegate", role=role, task=T.blob(a["task"]),
                files=nfiles or None, skipped=len(skipped) or None) as job:
        res = P.chat(spec["provider"], prompt, **kw)
        if not res["ok"] and not a.get("no_fallback"):
            order = [p for p in P.FALLBACK_ORDER if p != spec["provider"]]
            job.note("fallback", provider=spec["provider"], error=res.get("error"), next=order)
            # in fallback, role's model becomes invalid; leave it to provider's default
            kw.pop("model")
            res2 = P.chat_with_fallback(order, prompt, **kw)
            if res2.get("ok"):
                res2["fallback_from"] = [{"provider": spec["provider"], "error": res["error"]}]
                res = res2
        _job_result(job, res)
    if not res.get("ok"):
        return ("WORKER FAILED\n" + json.dumps(res, ensure_ascii=False, indent=2)
                + S.format_skipped(skipped))
    if res.get("warning"):
        return f"WARNING: {res['warning']}"
    if res.get("truncated"):
        res["text"] += _TRUNC_NOTE
    head = f"[{role} -> {res['provider']}/{res['model']}"
    if nfiles:
        head += f", {nfiles} files {len(prompt) // 1000}K chars."
    head += "]"
    if res.get("fallback_from"):
        head += f" (fallback: {_fb_summary(res['fallback_from'])})"
    return f"{head}\n\n{res['text']}" + S.format_skipped(skipped)


_TRUNC_NOTE = "\n\n[WARNING: response TRUNCATED at max_tokens limit - incomplete; increase max_tokens and try again]"


def _job_result(job, res):
    job.set(status="ok" if res.get("ok") else "error", provider=res.get("provider"),
            model=res.get("model"), error=None if res.get("ok") else res.get("error"),
            fallback=bool(res.get("fallback_from")) or None)


def _fb_summary(fb):
    if isinstance(fb, str):
        return fb
    return ", ".join(f"{x['provider']}: {str(x.get('error'))[:80]}" for x in fb)


MAX_JOBS = 200


def t_fanout(a):
    roles = _roles()
    items, default_role, default_task = list(a.get("items") or []), a.get("role"), a.get("task")
    conc = max(1, min(int(a.get("concurrency", 4)), 12))
    max_tok = a.get("max_tokens", 4096)

    skipped = []
    if a.get("files"):
        if default_role not in roles or not default_task:
            return "ERROR: shared 'role' (valid) and 'task' are required with files."
        budget = P.input_budget(roles[default_role]["provider"], max_tok) - len(default_task) \
            - len(roles[default_role].get("system") or "") - 500
        whole = a.get("whole_files", roles[default_role].get("whole_files", False))
        if whole:
            # no chunking: fit into the provider with the largest budget (fallback falls there)
            biggest = max(c["max_input_chars"] for c in P.PROVIDERS.values())
            size = int(a.get("chunk_chars") or biggest - len(default_task) - 2000)
        else:
            size = int(a.get("chunk_chars") or min(max(budget, 4000), 200_000))
        file_items, skipped, err = S.items(a["files"], size)
        if err:
            return f"ERROR: {err}"
        items += file_items
    if not items:
        return "ERROR: no jobs (items is empty and files matched no readable files)" \
            + S.format_skipped(skipped)
    if len(items) > MAX_JOBS:
        return (f"ERROR: {len(items)} jobs > {MAX_JOBS}. Narrow glob or increase chunk_chars "
                "(to avoid exhausting daily quotas in a single call).")

    jobs = []
    for i, it in enumerate(items):
        if isinstance(it, str):
            it = {"input": it}
        role = it.get("role") or default_role
        task = it.get("task") or default_task
        if not role or not task:
            return "ERROR: role and task are required for each job (either provide shared 'role'/'task' or specify per item)."
        if role not in roles:
            return f"ERROR: role '{role}' does not exist. Available roles: {', '.join(roles)}"
        jobs.append({"idx": i, "label": it.get("label") or f"#{i+1}", "role": role,
                     "task": task, "input": it.get("input"), "model": it.get("model")})

    def run(j):
        try:
            return run_job(j)
        except Exception as e:  # exception in a single job should not crash the entire fanout
            return j, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def run_job(j):
        spec = roles[j["role"]]
        with T.span("job", label=j["label"], idx=j["idx"], role=j["role"],
                    task=T.blob(j["task"])) as job:
            res = P.chat(spec["provider"], _compose(j["task"], j["input"]),
                         system=spec.get("system"), model=j["model"] or spec.get("model"),
                         max_tokens=max_tok, role=j["role"],
                         reasoning_effort=spec.get("reasoning_effort"))
            if not res["ok"]:
                order = [p for p in P.FALLBACK_ORDER if p != spec["provider"]]
                job.note("fallback", provider=spec["provider"], error=res.get("error"), next=order)
                alt = P.chat_with_fallback(order, _compose(j["task"], j["input"]),
                                           system=spec.get("system"), max_tokens=max_tok,
                                           role=j["role"],
                                           reasoning_effort=spec.get("reasoning_effort"))
                if alt.get("ok"):
                    alt["fallback_from"] = spec["provider"]
                    res = alt
                else:
                    res = {"ok": False, "error": _fb_summary(
                        [{"provider": spec["provider"], "error": res.get("error")}]
                        + alt.get("tried", []))}
            _job_result(job, res)
        return j, res

    with ThreadPoolExecutor(max_workers=conc) as ex:
        results = [f.result() for f in [ex.submit(T.bound(run), j) for j in jobs]]

    ok = sum(1 for _, r in results if r.get("ok"))
    out = [f"# fanout: {ok}/{len(results)} successful (concurrency={conc})"]
    for j, r in sorted(results, key=lambda x: x[0]["idx"]):
        if r.get("ok"):
            fb = f" fallback<-{r['fallback_from']}" if r.get("fallback_from") else ""
            body = (r["text"] or "(empty response)") + (_TRUNC_NOTE if r.get("truncated") else "")
            if r.get("warning"):
                body = f"WARNING: {r['warning']}"
            out.append(f"\n## {j['label']} [{j['role']} -> {r['provider']}/{r['model']}{fb}]\n{body}")
        else:
            out.append(f"\n## {j['label']} [FAILED]\n{r.get('error') or r.get('tried')}")
    return "\n".join(out) + S.format_skipped(skipped)


def t_ask(a):
    with T.span("job", label="ask") as job:
        res = P.chat(a["provider"], a["prompt"], system=a.get("system"), model=a.get("model"),
                     max_tokens=a.get("max_tokens", 4096), temperature=a.get("temperature", 0.2))
        _job_result(job, res)
    if not res["ok"]:
        return "FAILED\n" + json.dumps(res, ensure_ascii=False, indent=2)
    warn = f"\nWARNING: {res['warning']}" if res.get("warning") else ""
    trunc = _TRUNC_NOTE if res.get("truncated") else ""
    return f"[{res['provider']}/{res['model']} tokens={res['tokens']}]{warn}\n\n{res['text']}{trunc}"


def t_models(a):
    r = P.list_models(a["provider"], a.get("free_only", False))
    if not r.get("ok"):
        return f"FAILED: {r.get('error')}"
    return f"{r['provider']}: {r['count']} models\n" + "\n".join(f"- {m}" for m in r["models"])


def t_usage(_a):
    return U.format_report(U.snapshot())


HANDLERS = {"org_status": t_org_status, "delegate": t_delegate, "fanout": t_fanout,
            "ask": t_ask, "models": t_models, "usage": t_usage}


_send_lock = threading.Lock()


def _title(name, a):
    first = lambda s: " ".join(str(s or "").split())[:120]
    if name in ("delegate", "fanout"):
        n = len(a.get("items") or [])
        extra = f" ({n} items)" if name == "fanout" and n else ""
        return f"{a.get('role') or 'mixed roles'}{extra}: {first(a.get('task'))}"
    if name == "ask":
        return f"{a.get('provider')}: {first(a.get('prompt'))}"
    if name == "models":
        return f"{a.get('provider')} model list"
    return name.replace("_", " ")


def _args_for_log(a):
    out = {}
    for k, v in a.items():
        if v is None or v == [] or v == "":
            continue
        if k in ("input", "prompt", "system", "task"):
            out[k] = T.blob(v)
        elif k == "items":
            out[k] = T.blob(json.dumps(v, ensure_ascii=False, indent=1))
            out["item_count"] = len(v) if isinstance(v, list) else None
        else:
            out[k] = v
    return out


def _status_of(text):
    if text.startswith(("ERROR", "WORKER FAILED", "FAILED", "SERVER ERROR")):
        return "error"
    if text.startswith("WARNING"):
        return "warning"
    if text.startswith("# fanout: "):
        ok, total = text[10:].split(" ", 1)[0].split("/")
        return "ok" if ok == total else ("error" if ok == "0" else "partial")
    return "ok"


MAX_RESULT_CHARS = 60_000   # Claude Code rejects MCP tool results above ~25K tokens
KEEP_RESULTS = 50
RESULTS_DIR = os.path.join(P.CONFIG_DIR, "results")


def _spill(name, text):
    """Save an oversized result to RESULTS_DIR (newest KEEP_RESULTS kept); returns the path."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{name}-{secrets.token_hex(3)}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        old = sorted(n for n in os.listdir(RESULTS_DIR) if n.endswith(".md"))[:-KEEP_RESULTS]
        for n in old:
            try:
                os.remove(os.path.join(RESULTS_DIR, n))
            except OSError:
                pass
    except OSError:
        pass
    return path


def invoke(name, args):
    """Run a tool and record it as one activity call (shared by MCP and the CLI)."""
    with T.span("call", tool=name, title=_title(name, args), args=_args_for_log(args)) as call:
        text = HANDLERS[name](args)
        call.set(status=_status_of(text), result=T.blob(text))
        if len(text) > MAX_RESULT_CHARS:
            try:
                path = _spill(name, text)
            except OSError as e:
                path = f"(could not save: {e})"
            text = (text[:MAX_RESULT_CHARS]
                    + f"\n\n[ai-workers: result truncated \u2014 {len(text)} characters in total. "
                      f"Full result: {path} (pass it in `files` to a worker to condense it, "
                      "or read it in parts).]")
        return text


def send(obj):
    line = json.dumps(obj) + "\n"
    with _send_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _call_tool(mid, name, args):
    """Run tool in a separate thread: long fanout shouldn't block ping and other calls."""
    try:
        text = invoke(name, args)
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": text}]}})
    except Exception:
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "SERVER ERROR\n" + traceback.format_exc()}],
            "isError": True,
        }})


def main():
    T.configure(source="mcp")
    pool = ThreadPoolExecutor(max_workers=6)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        try:
            if method == "initialize":
                ci = (msg.get("params") or {}).get("clientInfo") or {}
                T.configure(client=" ".join(str(x) for x in (ci.get("name"), ci.get("version")) if x))
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER,
                    "instructions": INSTRUCTIONS,
                }})
            elif method == "initialized":
                continue
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                fn = HANDLERS.get(name)
                if not fn:
                    send({"jsonrpc": "2.0", "id": mid,
                          "error": {"code": -32602, "message": f"unknown tool: {name}"}})
                    continue
                pool.submit(_call_tool, mid, name, args)
            elif method and method.startswith("notifications/"):
                continue
            elif mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": f"unsupported method: {method}"}})
        except Exception:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "SERVER ERROR\n" + traceback.format_exc()}],
                    "isError": True,
                }})
    pool.shutdown(wait=True)  # stdin closed: finish ongoing jobs


if __name__ == "__main__":
    main()
