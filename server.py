#!/usr/bin/env python3
"""ai-workers: MCP server that exposes free API models to Claude Code as worker tools.

JSON-RPC over stdio. No external dependencies.
"""
import json, os, re, secrets, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity as T
import providers as P
import usage as U
import sources as S

PROTOCOL = "2025-06-18"
SERVER = {"name": "ai-workers", "version": "1.6.1"}

INSTRUCTIONS = """ai-workers: cheap worker models (Groq/OpenRouter free, Gemini for big inputs) for mechanical text work, so Claude's tokens go to thinking.
Hand off: summaries, translation, classification, extraction, log/test-output scanning, per-file analysis, first-pass review, drafts.
Keep: architecture, code edits, security/data-loss decisions, and verifying worker output (it is an unreviewed draft).
Files: never Read-and-paste. Pass absolute paths/dirs/globs in `files`; the server reads them and skips secrets. Save command output to a file first.
One job -> delegate. Many files/items -> fanout (one job per file). Leave max_tokens unset unless you need a long answer (auto-sized to stay on free Groq).
Results you only need to store or pass on: output_file/output_dir. Many parts -> one answer: fanout reduce. Scans (which files mention X?): drop_none."""

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
                "max_tokens": {"type": "integer", "description": "Answer budget; default auto-sized per role (leave unset)."},
                "no_fallback": {"type": "boolean", "description": "Only try the role's own provider."},
                "no_cache": {"type": "boolean", "description": "Skip the 7-day result cache."},
                "output_file": {"type": "string", "description": "Write the answer here; returns path + preview."},
                "overwrite": {"type": "boolean", "description": "Allow replacing output_file/output_dir."},
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
                "concurrency": {"type": "integer", "description": "Parallel requests (default 6, max 12)."},
                "max_tokens": {"type": "integer", "description": "Answer budget; default auto-sized per role (leave unset)."},
                "no_cache": {"type": "boolean", "description": "Skip the 7-day result cache."},
                "pack": {"type": "boolean", "description": "Pack many tiny items into few requests (auto for >=4 short classifier/extractor/translator items with same task; false disables)."},
                "drop_none": {"type": "boolean", "description": "Append 'reply NONE if nothing relevant' to the task and omit NONE answers."},
                "output_dir": {"type": "string", "description": "Write each answer to <dir>/NNN-label.md; returns the file list."},
                "reduce": {"type": "string", "description": "Merge all outputs into one answer with this instruction."},
                "reduce_role": {"type": "string", "description": "Default summarizer."},
                "output_file": {"type": "string", "description": "With reduce: write the merged answer here."},
                "overwrite": {"type": "boolean", "description": "Allow replacing output_file/output_dir."},
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


def _out_path(path):
    """Expanded absolute path, or None when `path` is not absolute."""
    p = os.path.expanduser(str(path))
    return p if os.path.isabs(p) else None


def _check_out(path, overwrite, is_dir=False):
    """(expanded path, error|None) for an output_file / output_dir request."""
    p = _out_path(path)
    if not p:
        return None, f"ERROR: output path must be absolute: {path}"
    if S._is_secret(p):
        return None, f"ERROR: refusing to write to a secret path: {p}"
    if is_dir:
        if os.path.isfile(p):
            return None, f"ERROR: output_dir is a file: {p}"
        if os.path.isdir(p) and os.listdir(p) and not overwrite:
            return None, f"ERROR: output_dir {p} is not empty (pass overwrite=true to reuse it)"
    elif os.path.exists(p) and not overwrite:
        return None, f"ERROR: {p} already exists (pass overwrite=true to replace it)"
    return p, None


def _write_file(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _preview(text, lines=3, limit=300):
    out = [l.strip() for l in text.splitlines() if l.strip()][:lines]
    pv = "\n".join(out)
    return pv if len(pv) <= limit else pv[:limit - 3] + "..."


def _saved(path, text):
    """Write `text` to `path`; the short confirmation + preview that is returned instead of it."""
    _write_file(path, text)
    n_lines = len(text.splitlines())
    return f"saved {len(text)} chars, {n_lines} lines -> {path}\n{_preview(text)}"


def _slug(label):
    return re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")[:60] or "job"


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
        for pm in cfg.get("pool") or []:
            c = P._cooling(name, pm)
            if c:
                lines.append(f"  - pool {pm} [{c}]")
    if not have:
        lines += ["", f"No keys found. Add to {P.ENV_FILE} file:",
                  "  GEMINI_API_KEY=...", "  GROQ_API_KEY=...", "  OPENROUTER_API_KEY=..."]
    lines += ["", "## Roles"]
    for name, spec in roles.items():
        ready = "" if spec["provider"] in have else "  [provider not ready -> fallback will be used]"
        cool = P._cooling(spec["provider"], spec["model"])
        cool = f" [{cool}]" if cool else ""
        lines.append(f"- {name}: {spec['provider']} / {spec['model']}{cool}{ready}")
    lines += ["", f"To edit roles: {P.CONFIG_FILE}"]
    return "\n".join(lines)


def t_delegate(a):
    roles = _roles()
    role = a["role"]
    if role not in roles:
        return f"ERROR: role '{role}' does not exist. Available roles: {', '.join(roles)}"
    spec = roles[role]
    out_path = None
    if a.get("output_file"):
        out_path, err = _check_out(a["output_file"], a.get("overwrite"))
        if err:
            return err
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
        res = P.run(spec, prompt, max_tokens=a.get("max_tokens"), model=a.get("model"),
                    no_fallback=bool(a.get("no_fallback")), role=role,
                    no_cache=bool(a.get("no_cache")))
        if res.get("route"):
            job.note("fallback", route=res["route"])
        if res.get("cached"):
            job.note("cache", provider=res.get("provider"), model=res.get("model"))
        _job_result(job, res)
    if not res.get("ok"):
        return ("WORKER FAILED\n" + json.dumps(res, ensure_ascii=False, indent=2)
                + S.format_skipped(skipped))
    if res.get("warning"):
        return f"WARNING: {res['warning']}"
    head = f"[{role} -> {res['provider']}/{res['model']}{' cached' if res.get('cached') else ''}"
    if nfiles:
        head += f", {nfiles} files {len(prompt) // 1000}K chars."
    head += "]"
    shown = [x for x in res.get("route") or [] if x.get("error") != "busy"]
    if shown:
        head += f" (fallback: {_fb_summary(shown)})"
    trunc = (_TRUNC_NOTE if res.get("truncated") else "") + (_INVALID_NOTE if res.get("warning_note") else "")
    if out_path:
        try:
            body = _saved(out_path, res["text"])
        except OSError as e:
            return f"ERROR: could not write {out_path}: {e}\n\n{res['text']}"
        return f"{head}\n\n{body}{trunc}" + S.format_skipped(skipped)
    return f"{head}\n\n{res['text']}{trunc}" + S.format_skipped(skipped)


_INVALID_NOTE = "\n\n[WARNING: output is not valid JSON]"
_TRUNC_NOTE = "\n\n[WARNING: response TRUNCATED at max_tokens limit - incomplete; increase max_tokens and try again]"


def _job_result(job, res):
    job.set(status="ok" if res.get("ok") else "error", provider=res.get("provider"),
            model=res.get("model"), error=None if res.get("ok") else res.get("error"),
            fallback=bool(res.get("route")) or None)


def _fb_summary(fb):
    if isinstance(fb, str):
        return fb
    out = []  # same provider + same error kind -> one entry (keeps headers short)
    for x in fb:
        err = str(x.get("error"))
        if x.get("model"):
            err = err.replace(f" for {x['provider']}/{x['model']}", "")
        err = re.sub(r" > \d+$", "", err)[:80]
        if out and out[-1][0] == x["provider"] and out[-1][2] == err:
            out[-1][1].append(x.get("model"))
            continue
        out.append([x["provider"], [x.get("model")], err])
    return ", ".join(
        f"{p}/{ms[0]}: {e}" if len(ms) == 1 and ms[0] else f"{p} ({len(ms)} models): {e}"
        for p, ms, e in out)


PACK_ROLES = ("classifier", "extractor", "translator")
PACK_MAX_ITEMS, PACK_MAX_CHARS, PACK_ITEM_LIMIT, PACK_MIN_ITEMS = 20, 6000, 1500, 4
_PACK_INSTR = ("Apply the task above to EACH numbered item independently. Reply with ONLY a JSON "
               "object mapping each item number (as a string) to that item's answer (string, or "
               "JSON value if the task asks for JSON). Include every item number exactly once.")


_NONE_SUFFIX = "\n\nIf the input contains nothing relevant to this task, reply with exactly NONE."


def _is_none(text):
    """True when an answer is just NONE (ignoring case, whitespace, surrounding punctuation/markdown)."""
    return (text or "").strip().strip("*`.\"' \t\r\n").strip().lower() == "none"


def _pack_eligible(jobs, pack, drop_none=False):
    """Packing needs one shared role+task, no per-item model and plain-text inputs."""
    if pack is False or not jobs:
        return False
    if len({(j["role"], j["task"]) for j in jobs}) != 1 or any(j["model"] for j in jobs):
        return False
    if any(not isinstance(j["input"], str) or not j["input"] for j in jobs):
        return False
    if pack is True:
        return True
    return (len(jobs) >= PACK_MIN_ITEMS
            and (jobs[0]["role"] in PACK_ROLES or (drop_none and jobs[0]["role"] == "summarizer"))
            and all(len(j["input"]) <= PACK_ITEM_LIMIT for j in jobs))


def _make_packs(jobs):
    packs, cur, size = [], [], 0
    for j in jobs:
        n = len(j["input"])
        if cur and (len(cur) >= PACK_MAX_ITEMS or size + n > PACK_MAX_CHARS):
            packs.append(cur)
            cur, size = [], 0
        cur.append(j)
        size += n
    if cur:
        packs.append(cur)
    return packs


def _pack_prompt(task, pack):
    body = "\n\n".join(f"### ITEM {n}\n{j['input']}" for n, j in enumerate(pack, 1))
    return f"{task}\n\n{_PACK_INSTR}\n\n{body}"


def _parse_pack(text):
    """-> {item number (str): answer text} or None when the reply is not a JSON object."""
    t = P.strip_fence(text)
    try:
        d = json.loads(t)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    return {str(k): v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            for k, v in d.items()}


def _tag(r):
    rt = r.get("route")
    fb = (f" fallback<-{rt[0]['provider']}/{rt[0].get('model')}"
          if rt and rt[0]["provider"] != r["provider"] else "")
    return f"{r['provider']}/{r['model']}{' cached' if r.get('cached') else ''}{fb}"


def _render_compact(head, results):
    """One line per item when every successful answer is a short single line; else None."""
    goods = [(j, r) for j, r in results if r.get("ok")]
    if not goods:
        return None
    for _, r in goods:
        t = r.get("text") or ""
        if r.get("truncated") or r.get("warning") or r.get("warning_note") or not t.strip() or "\n" in t.strip() or len(t.strip()) > 200:
            return None
    from collections import Counter
    dom = Counter((j["role"], f"{r['provider']}/{r['model']}") for j, r in goods).most_common(1)[0][0]
    lines = [f"{head} [{dom[0]} -> {dom[1]}]"]
    for j, r in results:
        if r.get("ok"):
            suffix = ""
            if _tag(r) != dom[1] or j["role"] != dom[0]:
                suffix = f" [{j['role']} -> {_tag(r)}]" if j["role"] != dom[0] else f" [{_tag(r)}]"
            lines.append(f"- {j['label']}: {r['text'].strip()}{suffix}")
        else:
            lines.append(f"- {j['label']}: FAILED: {str(r.get('error') or r.get('tried'))[:200]}")
    return "\n".join(lines)


MAX_JOBS = 200


def t_fanout(a):
    roles = _roles()
    items, default_role, default_task = list(a.get("items") or []), a.get("role"), a.get("task")
    conc = max(1, min(int(a.get("concurrency", 6)), 12))
    max_tok = a.get("max_tokens")
    no_cache = bool(a.get("no_cache"))
    drop_none = bool(a.get("drop_none"))
    reduce_task = a.get("reduce")
    reduce_role = a.get("reduce_role") or "summarizer"
    out_file = out_dir = None
    if reduce_task and reduce_role not in roles:
        return f"ERROR: reduce_role '{reduce_role}' does not exist. Available roles: {', '.join(roles)}"
    if a.get("output_file"):
        if not reduce_task:
            return "ERROR: output_file in fanout needs `reduce` (use output_dir for per-job files)."
        out_file, err = _check_out(a["output_file"], a.get("overwrite"))
        if err:
            return err
    if a.get("output_dir"):
        out_dir, err = _check_out(a["output_dir"], a.get("overwrite"), is_dir=True)
        if err:
            return err

    skipped = []
    if a.get("files"):
        if default_role not in roles or not default_task:
            return "ERROR: shared 'role' (valid) and 'task' are required with files."
        budget = P.input_budget(roles[default_role]["provider"],
                                max_tok or roles[default_role].get("min_tokens", 1024),
                                cls=P.script_class(default_task)) - len(default_task) \
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
        if drop_none:
            task += _NONE_SUFFIX
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
            res = P.run(spec, _compose(j["task"], j["input"]), max_tokens=max_tok,
                        model=j["model"], role=j["role"], no_cache=no_cache)
            if res.get("route"):
                job.note("fallback", route=res["route"])
            if res.get("cached"):
                job.note("cache", provider=res.get("provider"), model=res.get("model"))
            if not res["ok"]:
                res = {"ok": False, "error": _fb_summary(res.get("route") or [])
                       or res.get("error")}
            _job_result(job, res)
        return j, res

    def run_pack(args):
        n, total, pack = args
        spec = roles[pack[0]["role"]]
        try:
            with T.span("job", label=f"pack {n}/{total} ({len(pack)} items)", role=pack[0]["role"],
                        task=T.blob(pack[0]["task"])) as job:
                res = P.run(spec, _pack_prompt(pack[0]["task"], pack), max_tokens=max_tok,
                            role=pack[0]["role"], no_cache=no_cache)
                if res.get("route"):
                    job.note("fallback", route=res["route"])
                if res.get("cached"):
                    job.note("cache", provider=res.get("provider"), model=res.get("model"))
                if not res["ok"]:
                    res = {"ok": False, "error": _fb_summary(res.get("route") or [])
                           or res.get("error")}
                _job_result(job, res)
        except Exception as e:
            return pack, {}
        if not res.get("ok") or res.get("truncated") or res.get("warning"):
            return pack, {}
        answers = _parse_pack(res.get("text"))
        if answers is None:
            return pack, {}
        base = {k: v for k, v in res.items() if k not in ("text", "truncated", "warning", "warning_note")}
        return pack, {j["idx"]: {**base, "text": answers[str(n)]}
                      for n, j in enumerate(pack, 1) if str(n) in answers}

    packed_n = packed_m = 0
    done = {}
    with ThreadPoolExecutor(max_workers=conc) as ex:
        if _pack_eligible(jobs, a.get("pack"), drop_none):
            packs = _make_packs(jobs)
            for pack, got in [f.result() for f in [
                    ex.submit(T.bound(run_pack), (n, len(packs), p)) for n, p in enumerate(packs, 1)]]:
                done.update({idx: r for idx, r in got.items()})
            packed_n, packed_m = len(done), len(packs)
        by_idx = {j["idx"]: j for j in jobs}
        results = [(by_idx[i], r) for i, r in done.items()]
        results += [f.result() for f in [ex.submit(T.bound(run), j) for j in jobs
                                         if j["idx"] not in done]]
    pack_note = f", packed {packed_n} items into {packed_m} requests" if packed_n else ""

    results.sort(key=lambda x: x[0]["idx"])
    ok = sum(1 for _, r in results if r.get("ok"))
    good = [(j, r) for j, r in results if r.get("ok") and not r.get("warning")]
    none_idx = set()
    if drop_none:
        none_idx = {j["idx"] for j, r in good if _is_none(r.get("text"))}
        good = [(j, r) for j, r in good if j["idx"] not in none_idx]
    dn_note = f", {len(good)} with results ({len(none_idx)} NONE omitted)" if drop_none else ""

    written = {}
    if out_dir:
        try:
            for j, r in good:
                text = r["text"] or ""
                path = os.path.join(out_dir, f"{j['idx'] + 1:03d}-{_slug(j['label'])}.md")
                _write_file(path, text)
                written[j["idx"]] = (path, len(text))
        except OSError as e:
            return f"ERROR: could not write to {out_dir}: {e}"

    def failed_lines():
        return [f"- {j['label']}: {str(r.get('error') or r.get('warning') or r.get('tried'))[:200]}"
                for j, r in results
                if j["idx"] not in {g["idx"] for g, _ in good} | none_idx]

    if reduce_task and good:
        joined = "\n\n".join(
            f"## {j['label']}\n{r['text'] or '(empty response)'}" + (_TRUNC_NOTE if r.get("truncated") else "")
            for j, r in good)
        with T.span("job", label="reduce", role=reduce_role, task=T.blob(reduce_task)) as job:
            rres = P.run(roles[reduce_role], _compose(reduce_task, joined), max_tokens=None,  # map budget would truncate the merge
                         role=reduce_role, no_cache=no_cache)
            if rres.get("route"):
                job.note("fallback", route=rres["route"])
            if rres.get("cached"):
                job.note("cache", provider=rres.get("provider"), model=rres.get("model"))
            _job_result(job, rres)
        if rres.get("ok") and not rres.get("warning"):
            cached = " cached" if rres.get("cached") else ""
            out = [f"# fanout: {ok}/{len(results)} successful{dn_note}{pack_note} -> reduced "
                   f"[{reduce_role} -> {rres['provider']}/{rres['model']}{cached}]"]
            if out_dir:
                out.append(f"per-job outputs: {len(written)} files in {out_dir}")
            trunc = _TRUNC_NOTE if rres.get("truncated") else ""
            if out_file:
                try:
                    out.append("\n" + _saved(out_file, rres["text"]) + trunc)
                except OSError as e:
                    out.append(f"\nERROR: could not write {out_file}: {e}\n\n{rres['text']}{trunc}")
            else:
                out.append("\n" + rres["text"] + trunc)
            fl = failed_lines()
            if fl:
                out.append("\n## Failed jobs\n" + "\n".join(fl))
            return "\n".join(out) + S.format_skipped(skipped)
        reduce_err = rres.get("warning") or _fb_summary(rres.get("route") or []) or rres.get("error")
        note = f"\nREDUCE FAILED ({reduce_role}): {reduce_err}\nPer-job outputs follow."
    else:
        note = ""

    shown = [(j, r) for j, r in results if j["idx"] not in none_idx]
    conc_note = f" (concurrency={conc})" if not (drop_none and not good) else ""
    head = f"# fanout: {ok}/{len(results)} successful{dn_note}{conc_note}{pack_note}" + note
    if not reduce_task and not out_dir:
        compact = _render_compact(head, shown)
        if compact:
            return compact + S.format_skipped(skipped)
    out = [head]
    if out_dir:
        out.append(f"output_dir: {out_dir}")
    for j, r in shown:
        if r.get("ok"):
            rt = r.get("route")
            # a busy same-provider sibling taking over is load balancing, not a fallback
            fb = (f" fallback<-{rt[0]['provider']}/{rt[0].get('model')}"
                  if rt and rt[0]["provider"] != r["provider"] else "")
            cached = " cached" if r.get("cached") else ""
            tag = f"{r['provider']}/{r['model']}{cached}{fb}"
            if j["idx"] in written:
                path, n = written[j["idx"]]
                out.append(f"- {j['label']} [{tag}] -> {os.path.basename(path)} ({n} chars)"
                           + (" TRUNCATED" if r.get("truncated") else ""))
                continue
            body = (r["text"] or "(empty response)") + (_TRUNC_NOTE if r.get("truncated") else "") \
                + (_INVALID_NOTE if r.get("warning_note") else "")
            if r.get("warning"):
                body = f"WARNING: {r['warning']}"
            out.append(f"\n## {j['label']} [{j['role']} -> {tag}]\n{body}")
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
        if not isinstance(msg, dict):  # valid JSON but not a JSON-RPC object
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
