"""Single code path for free API providers (all OpenAI-compatible chat endpoints)."""
import json, os, threading, time, urllib.request, urllib.error

import activity as T

UA = "ai-workers/1.5 (+https://github.com/msozturktr/ai-workers) python-urllib"

CONFIG_DIR = os.path.expanduser("~/.config/ai-workers")
ENV_FILE = os.path.join(CONFIG_DIR, "env")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _load_env_file():
    """Adds KEY=VAL lines in ~/.config/ai-workers/env file to the environment."""
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except FileNotFoundError:
        pass


_load_env_file()

PROVIDERS = {
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/openai/models",
        "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "default_model": "gemini-3.6-flash",
        "light_model": "gemini-3.5-flash-lite",
        # 1M token context; Tier 1 paid -> large-context tasks go here
        "max_input_chars": 1_500_000,
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "key_env": ["GROQ_API_KEY"],
        "default_model": "openai/gpt-oss-120b",
        "light_model": "openai/gpt-oss-20b",
        # free tier 8K tokens/min per model; Groq counts max_tokens up front, so the real
        # input budget is derived per call by input_budget(); this is only the hard cap.
        "tpm": 8000,
        "reasoning": True,  # gpt-oss models accept reasoning_effort
        "max_input_chars": 24_000,
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_env": ["OPENROUTER_API_KEY"],
        "default_model": "nvidia/nemotron-3-super-120b-a12b:free",
        "light_model": "nvidia/nemotron-3.5-lightning:free",
        "max_input_chars": 200_000,
    },
}

DEFAULT_ROLES = {
    # System prompts are in English: Turkish prompts pulled the model towards
    # Turkish and the prompt's format even when the task was in English (measured). Format is default,
    # if the task requests something else, the task wins.
    "researcher":  {"provider": "gemini", "model": "gemini-3.6-flash", "whole_files": True,
                    "system": "You are a research analyst. Read the given source carefully and answer only from facts stated in it. Write 'unclear' where you are not sure. Default format: short bullets with numbered citations."},
    "summarizer":  {"provider": "groq", "model": "openai/gpt-oss-120b", "whole_files": True,
                    "system": "Summarize the text faithfully. Do not add new information or opinions. Default format: at most 8 bullets."},
    "coder":       {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "system": "You are a software engineer. Produce only working code; no explanations unless asked. Follow the existing language and style conventions."},
    "reviewer":    {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "whole_files": True,
                    "system": "You are a code/text reviewer. List only concrete, verifiable defects. For each: where, why it is wrong, how it is triggered. If there are none, say there are no findings."},
    "translator":  {"provider": "groq", "model": "openai/gpt-oss-120b", "reasoning_effort": "low",
                    "system": "Translate the given text into the requested language. Translate every word of natural language; keep code, identifiers, file paths, URLs and formatting unchanged. No explanations; return only the translation."},
    "classifier":  {"provider": "groq", "model": "openai/gpt-oss-20b", "reasoning_effort": "low",
                    "system": "You are a classifier. Return only the requested label and nothing else."},
    "extractor":   {"provider": "groq", "model": "openai/gpt-oss-120b", "reasoning_effort": "low",
                    "system": "Extract the requested fields from the text and return them as valid JSON. Use null for missing fields. Output nothing but JSON."},
}

# Groq is free with a large quota; Gemini (Tier 1, paid) is for large-context tasks;
# OpenRouter is free but ~50 requests/day -> last.
FALLBACK_ORDER = ["groq", "gemini", "openrouter"]


# whole_files: file is not split in fanout. When a chunk is out of context, summary/review
# hallucinates (measured: non-existent method names in Engine.cs chunks). Files that don't fit in Groq
# fall back as a whole to the large-context provider.

# Without this rule, the model drifts to the language of the input data (e.g., Turkish code comments).
LANGUAGE_RULE = ("Unless the task explicitly asks for a different output language, "
                 "reply in the language the TASK instruction is written in - not the "
                 "language of the input data or of this system prompt. If the task asks "
                 "for a specific length or format, it overrides the default format.")


def load_roles():
    roles = {k: dict(v) for k, v in DEFAULT_ROLES.items()}
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f)
        for name, spec in (user.get("roles") or {}).items():
            base = dict(roles.get(name, {}))
            base.update(spec)
            roles[name] = base
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    for spec in roles.values():
        spec["system"] = f"{spec.get('system') or ''}\n\n{LANGUAGE_RULE}".strip()
    return roles


# Conservative chars-per-token estimate for mixed code/prose (real ratio is ~3.5-4;
# a low value overestimates tokens, which is the safe side for rate limits).
CHARS_PER_TOKEN = 3.2

_learned_tpm = {}
_learned_lock = threading.Lock()


def _learn_tpm(provider, model, headers):
    """Remember the tokens-per-minute limit the provider reported for this model."""
    try:
        v = int(float(_rl_from_headers(headers).get("limit-tokens")))
    except (TypeError, ValueError):
        return
    if v > 0:
        with _learned_lock:
            _learned_tpm[(provider, model)] = v


def tpm_limit(provider, model=None):
    """Tokens-per-minute limit: learned from headers, else configured, else None."""
    with _learned_lock:
        v = _learned_tpm.get((provider, model))
    return v or (PROVIDERS.get(provider) or {}).get("tpm")


def input_budget(provider, max_tokens, model=None):
    """Max input characters (prompt + system) a call may use, given max_tokens."""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return 0
    tpm = tpm_limit(provider, model or cfg.get("default_model"))
    if not tpm:
        return cfg["max_input_chars"]
    return min(cfg["max_input_chars"],
               max(0, int((tpm - max_tokens - 200) * CHARS_PER_TOKEN)))


_tpm_windows = {}
_tpm_lock = threading.Lock()
TPM_MAX_WAIT = 30.0


def _tpm_acquire(key, need, limit, now=time.time, sleep=time.sleep):
    """Reserve `need` tokens in the 60 s sliding window for `key`; sleeps while the window
    is full (at most TPM_MAX_WAIT in total, then proceeds anyway). Returns seconds waited."""
    cap = 0.95 * limit
    waited = 0.0
    while True:
        with _tpm_lock:
            t = now()
            win = _tpm_windows.setdefault(key, [])
            win[:] = [e for e in win if e[0] > t - 60]
            total = sum(e[1] for e in win)
            wait = 0.0
            if total + need > cap and waited < TPM_MAX_WAIT:
                for ts, tok in win:  # oldest first
                    total -= tok
                    wait = ts + 60 - t
                    if total + need <= cap:
                        break
                wait = min(max(wait, 0.0) + 0.05, TPM_MAX_WAIT - waited)
            if wait <= 0:
                win.append([t, need])
                return waited
        sleep(wait)
        waited += wait


def _tpm_release(key, need):
    """Drop the newest reservation of `need` tokens (the request was rejected)."""
    with _tpm_lock:
        win = _tpm_windows.get(key, [])
        for i in range(len(win) - 1, -1, -1):
            if win[i][1] == need:
                del win[i]
                return


def _tpm_settle(key, need, actual):
    """Replace the newest reservation of `need` tokens with the actual usage."""
    with _tpm_lock:
        for e in reversed(_tpm_windows.get(key, [])):
            if e[1] == need:
                e[1] = actual
                return


def api_key(provider):
    for env in PROVIDERS[provider]["key_env"]:
        v = os.environ.get(env)
        if v:
            return v
    return None


def available_providers():
    return [p for p in PROVIDERS if api_key(p)]


def _post(url, key, payload, timeout=180):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), dict(r.headers)


LEDGER = os.path.join(CONFIG_DIR, "ledger.jsonl")
RATELIMIT_SNAPSHOT = os.path.join(CONFIG_DIR, "ratelimit.json")


def _rl_from_headers(h):
    """Extract Groq/OpenAI-compatible rate-limit headers."""
    out = {}
    for k, v in (h or {}).items():
        kl = k.lower()
        if kl.startswith("x-ratelimit-"):
            out[kl[len("x-ratelimit-"):]] = v
    return out


_record_lock = threading.Lock()


def _record(provider, model, role, res, headers):
    """Append a line to the ledger and store the provider's latest rate-limit status."""
    with _record_lock:
        _record_unlocked(provider, model, role, res, headers)


def _record_unlocked(provider, model, role, res, headers):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        row = {
            "ts": int(time.time()), "provider": provider, "model": model, "role": role,
            "ok": bool(res.get("ok")),
            "in": (res.get("tokens") or {}).get("in") or 0,
            "out": (res.get("tokens") or {}).get("out") or 0,
        }
        if not res.get("ok"):
            row["error"] = str(res.get("error"))[:200]
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")
        rl = _rl_from_headers(headers)
        if rl:
            try:
                with open(RATELIMIT_SNAPSHOT) as f:
                    snap = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                snap = {}
            snap[provider] = {"ts": row["ts"], "model": model, **rl}
            tmp = f"{RATELIMIT_SNAPSHOT}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f, indent=2)
            os.replace(tmp, RATELIMIT_SNAPSHOT)
    except Exception:
        pass  # measurement should never break execution


def _retry_delay(headers, attempt):
    """Respect the provider's Retry-After value (on the order of seconds for Groq TPM limit)."""
    try:
        ra = float((headers or {}).get("retry-after"))
        return max(0.5, min(ra + 0.5, 60))
    except (TypeError, ValueError):
        return min(2 ** attempt * 2, 20)


def chat(provider, prompt, system=None, model=None, max_tokens=4096,
         temperature=0.2, retries=4, timeout=180, role=None, reasoning_effort=None):
    """Single worker call. Backs off and retries on rate-limit/5xx conditions.

    Never raises an exception: if no key, unknown provider, or input exceeds provider
    budget, returns {"ok": False, "skipped": True} without making a network call so
    fallback can move to the next one. Every call is recorded as a trace "attempt".
    """
    cfg = PROVIDERS.get(provider) or {}
    with T.span("attempt", provider=provider, model=model or cfg.get("default_model"),
                role=role, max_tokens=max_tokens, temperature=temperature,
                reasoning_effort=reasoning_effort,
                system=T.blob(system), prompt=T.blob(prompt)) as sp:
        res = _chat(sp, provider, prompt, system, model, max_tokens, temperature,
                    retries, timeout, role, reasoning_effort)
        tok = res.get("tokens")
        sp.set(status="ok" if res.get("ok") else ("skipped" if res.get("skipped") else "error"),
               model=res.get("model") or model or cfg.get("default_model"),
               error=res.get("error"), tokens=tok, finish_reason=res.get("finish_reason"),
               truncated=res.get("truncated") or None, warning=res.get("warning"),
               response=T.blob(res.get("text")) if res.get("ok") else None)
        return res


def _build_payload(provider, model, msgs, max_tokens, temperature, reasoning_effort=None):
    """Request body; reasoning_effort is sent only to providers/models that accept it."""
    payload = {"model": model, "messages": msgs, "max_tokens": max_tokens,
               "temperature": temperature}
    if (reasoning_effort and PROVIDERS[provider].get("reasoning")
            and str(model).startswith("openai/gpt-oss")):
        payload["reasoning_effort"] = reasoning_effort
    return payload


def _chat(sp, provider, prompt, system, model, max_tokens, temperature, retries, timeout, role,
          reasoning_effort=None):
    if provider not in PROVIDERS:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"unknown provider: {provider}"}
    cfg = PROVIDERS[provider]
    key = api_key(provider)
    if not key:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"missing key ({ENV_FILE} -> {cfg['key_env'][0]}=...)"}
    size = len(prompt) + len(system or "")
    budget = input_budget(provider, max_tokens, model or cfg["default_model"])
    if size > budget:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"input too large: {size} characters > {provider} budget "
                         f"{budget} (max_tokens {max_tokens})"}
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    retries = max(1, retries)
    payload = _build_payload(provider, model or cfg["default_model"], msgs, max_tokens,
                             temperature, reasoning_effort)
    last = None
    tpm = tpm_limit(provider, payload["model"])
    tkey = (provider, payload["model"])
    need = size / CHARS_PER_TOKEN + max_tokens
    reserved = False
    for attempt in range(retries):
        try:
            if tpm and not reserved:  # one reservation per call (re-acquired after a 429)
                reserved = True
                waited = _tpm_acquire(tkey, need, tpm)
                if waited > 0.5:
                    sp.note("throttle", waited=round(waited, 1), need=int(need))
            data, hdrs = _post(cfg["url"], key, payload, timeout)
            if data.get("error") and not data.get("choices"):
                # OpenRouter returns some upstream errors in the HTTP 200 body
                err = data["error"]
                code = err.get("code") if isinstance(err, dict) else None
                try:
                    code = int(code)
                except (TypeError, ValueError):
                    pass
                last = f"HTTP 200 body error {code}: {json.dumps(err, ensure_ascii=False)[:600]}"
                if code in (408, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    delay = min(2 ** attempt * 2, 20)
                    sp.note("retry", attempt=attempt + 1, error=last, delay=delay)
                    time.sleep(delay)
                    continue
                break
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            usage = data.get("usage") or {}
            res = {
                "ok": True, "provider": provider, "model": payload["model"],
                "text": text.strip(),
                "tokens": {"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens")},
                "finish_reason": choice.get("finish_reason"),
                "warning": (
                    "Empty response: model exhausted token budget in thinking step. "
                    "Increase max_tokens (>=512 for reasoning models)."
                    if not text.strip() and choice.get("finish_reason") == "length" else None
                ),
                # text exists but was cut off at max_tokens -> caller should know output is truncated
                "truncated": bool(text.strip()) and choice.get("finish_reason") == "length",
            }
            _learn_tpm(provider, payload["model"], hdrs)
            if tpm and usage.get("prompt_tokens") is not None:
                # Groq charges max_tokens up front, so keep it reserved; only the input
                # estimate is corrected to the real prompt size.
                _tpm_settle(tkey, need, (usage.get("prompt_tokens") or 0) + max_tokens)
            _record(provider, payload["model"], role, res, hdrs)
            sp.set(ratelimit=_rl_from_headers(hdrs) or None, tries=attempt + 1)
            return res
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:600]
            last = f"HTTP {e.code}: {detail}"
            if e.code == 429 and tpm and reserved:
                _tpm_release(tkey, need)  # rejected requests are not charged; the retry
                reserved = False          # must wait for room in the window again
            if e.code in (408, 409, 425, 429, 500, 502, 503, 504) and attempt < retries - 1:
                delay = _retry_delay(e.headers, attempt)
                sp.note("retry", attempt=attempt + 1, error=last, delay=delay, http=e.code)
                time.sleep(delay)
                continue
            break
        except Exception as e:  # network error / timeout
            last = f"{type(e).__name__}: {e}"
            if attempt < retries - 1:
                delay = min(2 ** attempt * 2, 20)
                sp.note("retry", attempt=attempt + 1, error=last, delay=delay)
                time.sleep(delay)
                continue
            break
    res = {"ok": False, "provider": provider, "model": payload["model"], "error": last}
    _record(provider, payload["model"], role, res, None)
    sp.set(tries=attempt + 1)
    return res


def chat_with_fallback(providers, prompt, **kw):
    """Try in order; return the first successful response. Report which providers failed."""
    tried = []
    for p in providers:
        res = chat(p, prompt, **kw)
        if res["ok"]:
            if tried:
                res["fallback_from"] = tried
            return res
        tried.append({"provider": p, "error": res["error"]})
    return {"ok": False, "error": "all providers failed", "tried": tried}


def key_info(provider):
    """Fetch live data if the provider has its own quota endpoint."""
    key = api_key(provider)
    if not key:
        return {"ok": False, "error": "missing key"}
    if provider != "openrouter":
        return {"ok": False, "error": "this provider does not offer a quota endpoint"}
    req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"ok": True, **(json.loads(r.read().decode()).get("data") or {})}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def list_models(provider, free_only=False):
    key = api_key(provider)
    if not key:
        return {"ok": False, "error": "missing key"}
    url = PROVIDERS[provider]["models_url"]
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "User-Agent": UA,
                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    if free_only:
        ids = [i for i in ids if i.endswith(":free")]
    return {"ok": True, "provider": provider, "count": len(ids), "models": sorted(ids)}
