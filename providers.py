"""Single code path for free API providers (all OpenAI-compatible chat endpoints)."""
import http.client, io, json, os, re, socket, threading, time, urllib.parse, urllib.request, urllib.error

import activity as T
import cache as C

UA = "ai-workers/1.6.2 (+https://github.com/msozturktr/ai-workers) python-urllib"

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
        # max_tokens includes hidden thinking; only actual usage is billed
        "thinking_in_max_tokens": True,
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "key_env": ["GROQ_API_KEY"],
        "default_model": "openai/gpt-oss-120b",
        "light_model": "openai/gpt-oss-20b",
        # free tier 8K tokens/min per model (continuously refilling bucket); max_tokens is not
        # charged up front, only the real prompt+completion. Input is limited per request by
        # min(tpm, itpm), see input_budget().
        "tpm": 8000,
        "itpm": {"qwen/qwen3.8-27b": 7000},  # separate input-tokens-per-minute limits
        # every model has its own bucket -> siblings absorb load before Gemini (paid) is used
        "pool": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        "reasoning": True,  # gpt-oss models accept reasoning_effort
        "max_input_chars": 32_000,
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
                    "max_tokens": 4096,
                    "system": "You are a research analyst. Read the given source carefully and answer only from facts stated in it. Write 'unclear' where you are not sure. Default format: short bullets with numbered citations."},
    "summarizer":  {"provider": "groq", "model": "openai/gpt-oss-120b", "whole_files": True,
                    "max_tokens": 2048, "min_tokens": 512,
                    # Gemini keeps thinking here: measured on a Turkish transcript, "minimal" missed
                    # items and fields the default caught (5.8 s vs 18 s, +2.8K thinking tokens)
                    "reasoning_effort": {"groq": "low"},
                    "system": "Summarize the text faithfully. Do not add new information or opinions. Default format: at most 8 bullets."},
    "coder":       {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "max_tokens": 4096, "min_tokens": 1536, "reasoning_effort": {"gemini": "low"},
                    "system": "You are a software engineer. Produce only working code; no explanations unless asked. Follow the existing language and style conventions."},
    "reviewer":    {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "whole_files": True, "max_tokens": 4096,
                    "system": "You are a code/text reviewer. List only concrete, verifiable defects. For each: where, why it is wrong, how it is triggered. If there are none, say there are no findings."},
    "translator":  {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "reasoning_effort": {"groq": "low", "gemini": "minimal"},
                    "max_tokens": 4096, "min_tokens": 512, "out_ratio": 1.3,
                    "system": "Translate the given text into the requested language. Translate every word of natural language; keep code, identifiers, file paths, URLs and formatting unchanged. No explanations; return only the translation."},
    "classifier":  {"provider": "groq", "model": "openai/gpt-oss-20b",
                    "reasoning_effort": {"groq": "low", "gemini": "minimal"},
                    "max_tokens": 512, "min_tokens": 256,
                    "fallback_models": {"gemini": "gemini-3.5-flash-lite"},
                    "system": "You are a classifier. Return only the requested label and nothing else."},
    "extractor":   {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "reasoning_effort": {"groq": "low", "gemini": "minimal"},
                    "max_tokens": 2048, "min_tokens": 512,
                    "fallback_models": {"gemini": "gemini-3.5-flash-lite"}, "validate": "json",
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


# Chars-per-token is learned per (provider, model) from real usage; the default is
# conservative for mixed code/prose (real ratio is ~3.5-4; a low value overestimates tokens,
# which is the safe side for rate limits). Estimates add a 5% safety margin.
CHARS_PER_TOKEN = 3.2
CHARS_PER_TOKEN_INTL = 2.6   # non-ASCII-heavy text (e.g. Turkish) tokenizes much worse
CPT_MIN, CPT_MAX, CPT_ALPHA, CPT_MARGIN = 2.0, 5.0, 0.3, 1.05

STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
_state_lock = threading.RLock()
_state_loaded = False
_cpt = {}        # (provider, model, cls) -> learned chars per token; cls = "ascii" | "intl"
_cooldown = {}   # (provider, model) -> (until_ts, reason)
_itpm = {}       # (provider, model) -> learned input-tokens-per-minute limit


def _load_state():
    """Lazily load cooldowns and learned ratios from STATE_FILE (expired entries dropped)."""
    global _state_loaded
    with _state_lock:
        if _state_loaded:
            return
        _state_loaded = True
        try:
            with open(STATE_FILE) as f:
                st = json.load(f)
            now = time.time()
            for k, v in (st.get("cooldown") or {}).items():
                p, _, m = k.partition("|")
                if float(v[0]) > now:
                    _cooldown.setdefault((p, m), (float(v[0]), str(v[1])))
            for k, v in (st.get("cpt") or {}).items():
                parts = k.split("|")
                if len(parts) != 3 or parts[2] not in ("ascii", "intl"):
                    continue  # old 2-part keys are ignored
                _cpt.setdefault(tuple(parts), min(CPT_MAX, max(CPT_MIN, float(v))))
            for k, v in (st.get("itpm") or {}).items():
                p, _, m = k.partition("|")
                if int(v) > 0:
                    _itpm.setdefault((p, m), int(v))
        except Exception:
            pass


def _save_state():
    try:
        with _state_lock:
            now = time.time()
            st = {"cooldown": {f"{p}|{m}": [u, r] for (p, m), (u, r) in _cooldown.items()
                               if u > now},
                  "cpt": {f"{p}|{m}|{c}": v for (p, m, c), v in _cpt.items()},
                  "itpm": {f"{p}|{m}": v for (p, m), v in _itpm.items()}}
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = f"{STATE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def script_class(text):
    """"ascii" when at most 2% of the characters are non-ASCII, else "intl"."""
    if not text:
        return "ascii"
    return "ascii" if sum(1 for c in text if ord(c) > 127) <= 0.02 * len(text) else "intl"


def _default_cpt(cls):
    return CHARS_PER_TOKEN_INTL if cls == "intl" else CHARS_PER_TOKEN


def chars_per_token(provider, model=None, cls="ascii"):
    """Learned chars/token for (provider, model, cls), else the provider's average for that
    class, else the class default."""
    _load_state()
    with _state_lock:
        v = _cpt.get((provider, model, cls))
        if v:
            return v
        vals = [x for (p, _m, c), x in _cpt.items() if p == provider and c == cls]
    return sum(vals) / len(vals) if vals else _default_cpt(cls)


PROBE_SLACK = 1.2


def _probe_slack(provider, cls="ascii"):
    """Allow 20% over the input limit while no ratio is learned for this provider and script
    class: the default is conservative, and a rejected 413/429 costs nothing but teaches it."""
    _load_state()
    with _state_lock:
        known = any(p == provider and c == cls for (p, _m, c) in _cpt)
    return 1.0 if known else PROBE_SLACK


def est_tokens(provider, model, chars, cls="ascii"):
    return chars / chars_per_token(provider, model, cls) * CPT_MARGIN


CPT_MIN_CHARS = 2000  # below this the fixed chat-template overhead (~70 tokens) skews the ratio


def _learn_cpt(provider, model, chars, in_tokens, cls="ascii"):
    if chars < CPT_MIN_CHARS:
        return
    try:
        obs = chars / float(in_tokens)
    except (TypeError, ValueError, ZeroDivisionError):
        return
    if obs <= 0:
        return
    obs = min(CPT_MAX, max(CPT_MIN, obs))
    with _state_lock:
        _load_state()
        old = _cpt.get((provider, model, cls)) or chars_per_token(provider, model, cls)
        _cpt[(provider, model, cls)] = min(CPT_MAX, max(CPT_MIN, (1 - CPT_ALPHA) * old + CPT_ALPHA * obs))
    _save_state()


def _set_cooldown(provider, model, until, reason):
    _load_state()
    with _state_lock:
        _cooldown[(provider, model)] = (until, reason)
    _save_state()


def _cooling(provider, model):
    """'cooling down until HH:MM: reason' while (provider, model) is in cooldown, else None."""
    _load_state()
    with _state_lock:
        c = _cooldown.get((provider, model))
        if c and c[0] <= time.time():
            del _cooldown[(provider, model)]
            c = None
    if not c:
        return None
    return f"cooling down until {time.strftime('%H:%M', time.localtime(c[0]))}: {c[1]}"


_DAILY_RE = re.compile(r"per day|\bTPD\b|\bRPD\b|free-models-per-day", re.I)


def _cooldown_from_error(provider, model, code, detail, headers):
    """Set a cooldown for errors that will not clear within a retry. Returns True if set."""
    now = time.time()
    try:
        ra = float((headers or {}).get("retry-after"))
    except (TypeError, ValueError):
        ra = None
    if code == 429:
        if _DAILY_RE.search(detail or ""):
            if provider == "openrouter" and "free-models-per-day" in (detail or "").lower():
                until = (int(now) // 86400 + 1) * 86400
            else:
                until = now + (ra if ra is not None else 3600)
            _set_cooldown(provider, model, until, "daily limit")
            return True
        if ra is not None and ra > 60:
            _set_cooldown(provider, model, now + ra, f"retry-after {int(ra)}s")
            return True
    elif code == 404 and "model_not_found" in (detail or "").lower():
        _set_cooldown(provider, model, now + 6 * 3600, "model not found")
        return True
    return False


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*\n(.*?)\n?```\s*$", re.S)


def strip_fence(text):
    """Strip surrounding whitespace and a single surrounding ``` / ```json fence."""
    t = (text or "").strip()
    m = _FENCE_RE.match(t)
    return m.group(1).strip() if m else t


_NONE_STRIP = "*`.\"' \t\r\n_-"


def is_none(text):
    """True when an answer is just NONE (ignoring case, whitespace, markdown/punctuation and a ``` fence)."""
    t = strip_fence(text).strip(_NONE_STRIP)
    return t.lower() == "none"


_HDR_RE = re.compile(r"^### FILE: .*$", re.M)


def strip_echoed_header(text, prompt):
    """Drop a first line echoing a '### FILE: <label>' header that is really present in the prompt."""
    if not text or not prompt:
        return text
    t = text.lstrip("\n")
    first, sep, rest = t.partition("\n")
    first = first.rstrip()
    if first.startswith("### FILE: ") and first in _HDR_RE.findall(prompt):
        return rest.lstrip("\n") if sep else ""
    return text


def _validate(spec, res):
    """Role output validation. Returns an error string, or None when ok (res["text"] may be normalised)."""
    if spec.get("validate") == "json":
        if is_none(res.get("text")):
            res["text"] = "NONE"
            return None
        t = strip_fence(res.get("text"))
        try:
            json.loads(t)
        except ValueError:
            return "invalid JSON"
        res["text"] = t
    return None


_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def _learned_413(provider, model, chars, max_tokens, detail, cls="ascii"):
    m = re.search(r"Requested (\d+)", detail or "")
    if m:
        in_tok = int(m.group(1)) - (max_tokens or 0)
        if in_tok > 0:
            _learn_cpt(provider, model, chars, in_tok, cls)


_ITPM_RE = re.compile(r"input tokens per minute \(ITPM\)\D{0,20}?Limit (\d+)", re.I)
_TRY_AGAIN_RE = re.compile(r"try again in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", re.I)


def _learn_itpm(provider, model, detail):
    """Remember an input-tokens-per-minute limit named in an error body. True if one was found."""
    m = _ITPM_RE.search(detail or "")
    if not m or int(m.group(1)) <= 0:
        return False
    _load_state()
    with _state_lock:
        _itpm[(provider, model)] = int(m.group(1))
    _save_state()
    return True


def _parse_try_again(detail):
    """Seconds from 'Please try again in 2.9325s' (also 1m5.4s, 250ms); None if absent."""
    m = _TRY_AGAIN_RE.search(detail or "")
    if not m:
        return None
    mult = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * mult[u.lower()]
               for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", m.group(1), re.I))


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


def itpm_limit(provider, model=None):
    """Input-tokens-per-minute limit: learned from errors, else configured, else None."""
    _load_state()
    with _state_lock:
        v = _itpm.get((provider, model))
    return v or ((PROVIDERS.get(provider) or {}).get("itpm") or {}).get(model)


def input_limit(provider, model=None):
    """Per-request input token ceiling: min(TPM, ITPM) where known, else None."""
    lims = [x for x in (tpm_limit(provider, model), itpm_limit(provider, model)) if x]
    return min(lims) if lims else None


INPUT_MARGIN_TOKENS = 200


def _input_cap_chars(provider, model, cls="ascii"):
    """Hard per-request input size in characters (no output allowance)."""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return 0
    lim = input_limit(provider, model)
    if not lim:
        return cfg["max_input_chars"]
    return min(cfg["max_input_chars"],
               max(0, int((lim - INPUT_MARGIN_TOKENS) * chars_per_token(provider, model, cls)
                          / CPT_MARGIN * _probe_slack(provider, cls))))


def input_budget(provider, max_tokens, model=None, cls="ascii"):
    """Input characters (prompt + system) to plan a call/chunk around: the per-request input
    limit minus an expected-output allowance of min(max_tokens, 1024) tokens
    (None = auto-sized: assume 1024)."""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return 0
    if max_tokens is None:
        max_tokens = 1024
    model = model or cfg.get("default_model")
    lim = input_limit(provider, model)
    if not lim:
        return cfg["max_input_chars"]
    allow = min(max_tokens, 1024)
    return min(cfg["max_input_chars"],
               max(0, int((lim - INPUT_MARGIN_TOKENS - allow) * chars_per_token(provider, model, cls) / CPT_MARGIN)))


# Token bucket per (provider, model): capacity = limit, refills limit/60 per second. A request
# is admitted when level >= need (estimated input + small output allowance); the real usage is
# synced from response headers (or settled from usage) afterwards. level may go negative after
# a 429 that told us how long to wait.
_buckets = {}   # key -> {"level": float, "ts": float, "inflight": [float, ...]}
_tpm_lock = threading.Lock()
TPM_MAX_WAIT = 30.0
OUT_ESTIMATE = 256


def _bucket(key, limit, t):
    """Refilled bucket for key; caller holds _tpm_lock."""
    b = _buckets.get(key)
    if b is None:
        b = _buckets[key] = {"level": float(limit), "ts": t, "inflight": []}
    elif t > b["ts"]:
        b["level"] = min(float(limit), b["level"] + (t - b["ts"]) * limit / 60.0)
        b["ts"] = t
    return b


def _tpm_acquire(key, need, limit, now=None, sleep=None, block=True):
    """Reserve `need` tokens. Returns ("ok", seconds_waited), ("busy", None) when block=False
    and the bucket cannot cover it now, or ("long", wait_seconds) when covering it would take
    longer than TPM_MAX_WAIT in total (nothing reserved in either failure case)."""
    now = now or time.time
    sleep = sleep or time.sleep
    need = min(float(need), float(limit))
    rate = limit / 60.0
    waited = 0.0
    while True:
        with _tpm_lock:
            b = _bucket(key, limit, now())
            if b["level"] >= need:
                b["level"] -= need
                b["inflight"].append(need)
                return "ok", waited
            wait = (need - b["level"]) / rate + 0.05
            if not block:
                return "busy", None
            if waited + wait > TPM_MAX_WAIT:
                return "long", round(waited + wait, 1)
        sleep(wait)
        waited += wait


def _tpm_room(key, need, limit, now=None):
    """True if `need` tokens are available right now (no reservation)."""
    now = now or time.time
    with _tpm_lock:
        return _bucket(key, limit, now())["level"] >= min(float(need), float(limit))


def _drop_inflight(b, need):
    for i in range(len(b["inflight"]) - 1, -1, -1):
        if abs(b["inflight"][i] - need) < 1e-9:
            del b["inflight"][i]
            return


def _tpm_release(key, need, limit, now=None):
    """Give back an unused reservation (the request never ran)."""
    now = now or time.time
    need = min(float(need), float(limit))
    with _tpm_lock:
        b = _bucket(key, limit, now())
        _drop_inflight(b, need)
        b["level"] = min(float(limit), b["level"] + need)


def _tpm_settle(key, need, actual, limit, now=None):
    """No header available: replace the reservation by the actual prompt+completion tokens."""
    now = now or time.time
    need = min(float(need), float(limit))
    with _tpm_lock:
        b = _bucket(key, limit, now())
        _drop_inflight(b, need)
        b["level"] = min(float(limit), b["level"] + need - actual)


def _tpm_sync(key, need, remaining, limit, now=None):
    """Provider reported `remaining` tokens: trust it, minus other in-flight reservations.
    Also covers other processes sharing the key (their usage is inside `remaining`)."""
    now = now or time.time
    need = min(float(need), float(limit))
    with _tpm_lock:
        b = _bucket(key, limit, now())
        _drop_inflight(b, need)
        b["level"] = min(float(limit), float(remaining) - sum(b["inflight"]))
        b["ts"] = now()


def _tpm_penalize(key, need, wait, limit, now=None):
    """429 told us to wait `wait` s: set the level so `need` becomes available after that long.
    Drops this call's reservation."""
    now = now or time.time
    need = min(float(need), float(limit))
    with _tpm_lock:
        b = _bucket(key, limit, now())
        _drop_inflight(b, need)
        b["level"] = need - wait * limit / 60.0


def api_key(provider):
    for env in PROVIDERS[provider]["key_env"]:
        v = os.environ.get(env)
        if v:
            return v
    return None


def available_providers():
    return [p for p in PROVIDERS if api_key(p)]


_POOL_MAX_IDLE = 8
_pool = {}  # (scheme, host, port) -> [idle connections]
_pool_lock = threading.Lock()
_STALE = (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError,
          http.client.BadStatusLine, http.client.CannotSendRequest,
          http.client.ResponseNotReady)


def _conn_factory(scheme, host, port, timeout):
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    return cls(host, port, timeout=timeout)


def _pool_get(pk):
    with _pool_lock:
        idle = _pool.get(pk)
        return idle.pop() if idle else None


def _pool_put(pk, conn):
    with _pool_lock:
        idle = _pool.setdefault(pk, [])
        if len(idle) < _POOL_MAX_IDLE:
            idle.append(conn)
            return
    conn.close()


def _post_urllib(url, key, payload, timeout):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), dict(r.headers)


def _post(url, key, payload, timeout=180):
    """POST JSON over a pooled keep-alive connection -> (json, headers dict)."""
    if os.environ.get("AI_WORKERS_NO_KEEPALIVE"):
        return _post_urllib(url, key, payload, timeout)
    body = json.dumps(payload).encode()
    u = urllib.parse.urlsplit(url)
    scheme = u.scheme or "https"
    port = u.port or (443 if scheme == "https" else 80)
    pk = (scheme, u.hostname, port)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": UA, "Accept": "application/json"}
    for attempt in (0, 1):
        conn = _pool_get(pk) if attempt == 0 else None
        reused = conn is not None
        if conn is None:
            conn = _conn_factory(scheme, u.hostname, port, timeout)
        else:
            conn.timeout = timeout
            if getattr(conn, "sock", None) is not None:
                conn.sock.settimeout(timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            r = conn.getresponse()
            data = r.read()
        except _STALE:
            conn.close()
            if reused:
                continue  # stale idle connection: retry once on a fresh one
            raise
        except BaseException:
            conn.close()
            raise
        hdrs = r.msg
        if r.will_close or (hdrs.get("Connection") or "").lower() == "close":
            conn.close()
        else:
            _pool_put(pk, conn)
        if r.status >= 400:
            raise urllib.error.HTTPError(url, r.status, r.reason, hdrs, io.BytesIO(data))
        return json.loads(data.decode()), dict(hdrs)


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
            "think": (res.get("tokens") or {}).get("think") or 0,
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
            prev = snap.get(provider)
            models = dict(prev.get("models") or {}) if isinstance(prev, dict) else {}
            models[model] = {"ts": row["ts"], **rl}
            snap[provider] = {"ts": row["ts"], "model": model, **rl, "models": models}
            tmp = f"{RATELIMIT_SNAPSHOT}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f, indent=2)
            os.replace(tmp, RATELIMIT_SNAPSHOT)
    except Exception:
        pass  # measurement should never break execution


def _record_cached(hit, role):
    """Ledger row for a cache hit (no quota used; counted only in efficiency stats)."""
    try:
        tk = hit.get("tokens") or {}
        with _record_lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            row = {"ts": int(time.time()), "provider": hit.get("provider"),
                   "model": hit.get("model"), "role": role, "ok": True, "in": 0, "out": 0,
                   "cached": True, "saved_in": tk.get("in") or 0, "saved_out": tk.get("out") or 0}
            with open(LEDGER, "a") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _retry_delay(headers, attempt, detail=None):
    """Respect the provider's wait hint: 'try again in Xs' in the body, else Retry-After."""
    x = _parse_try_again(detail)
    if x is not None:
        return max(0.5, min(x + 0.25, 60))
    try:
        ra = float((headers or {}).get("retry-after"))
        return max(0.5, min(ra + 0.5, 60))
    except (TypeError, ValueError):
        return min(2 ** attempt * 2, 20)


def chat(provider, prompt, system=None, model=None, max_tokens=4096,
         temperature=0.2, retries=4, timeout=180, role=None, reasoning_effort=None,
         retry_429=True):
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
                    retries, timeout, role, reasoning_effort, retry_429)
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
    if isinstance(reasoning_effort, dict):  # per-provider efforts
        eff = reasoning_effort.get(provider)
    else:  # plain string keeps the old meaning: Groq only
        eff = reasoning_effort if provider == "groq" else None
    if eff:
        if provider == "gemini":
            payload["reasoning_effort"] = eff
        elif PROVIDERS[provider].get("reasoning") and str(model).startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = eff
    return payload


def _think_tokens(usage):
    """Hidden thinking tokens: reasoning_tokens when reported (already inside
    completion_tokens), else total - prompt - completion (Gemini bills them separately)."""
    try:
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if rt:
            return int(rt)
        p, c, t = (usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        if None in (p, c, t):
            return 0
        return max(0, int(t) - int(p) - int(c))
    except (TypeError, ValueError, AttributeError):
        return 0


def _chat(sp, provider, prompt, system, model, max_tokens, temperature, retries, timeout, role,
          reasoning_effort=None, retry_429=True):
    if provider not in PROVIDERS:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"unknown provider: {provider}"}
    cfg = PROVIDERS[provider]
    key = api_key(provider)
    if not key:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"missing key ({ENV_FILE} -> {cfg['key_env'][0]}=...)"}
    cool = _cooling(provider, model or cfg["default_model"])
    if cool:
        return {"ok": False, "skipped": True, "provider": provider,
                "model": model or cfg["default_model"], "error": cool}
    size = len(prompt) + len(system or "")
    cls = script_class((system or "") + prompt)
    budget = _input_cap_chars(provider, model or cfg["default_model"], cls)
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
    est_in = est_tokens(provider, payload["model"], size, cls)
    need = est_in + min(max_tokens, OUT_ESTIMATE)
    reserved = False
    for attempt in range(retries):
        try:
            if tpm and not reserved:  # one reservation per call (re-acquired after a 429)
                st, waited = _tpm_acquire(tkey, need, tpm, block=retry_429)
                if st == "busy":  # sibling candidates exist: do not queue, let the router move on
                    return {"ok": False, "skipped": True, "provider": provider,
                            "model": payload["model"], "error": "busy"}
                if st == "long":  # a guaranteed 429 later: let the router try the next provider
                    return {"ok": False, "skipped": True, "provider": provider,
                            "model": payload["model"], "error": f"would wait {waited:.0f}s"}
                reserved = True
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
            text = _THINK_RE.sub("", (choice.get("message") or {}).get("content") or "")
            usage = data.get("usage") or {}
            res = {
                "ok": True, "provider": provider, "model": payload["model"],
                "text": text.strip(),
                "tokens": {"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
                           "think": _think_tokens(usage)},
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
            if usage.get("prompt_tokens"):
                _learn_cpt(provider, payload["model"], size, usage["prompt_tokens"], cls)
            if tpm and reserved:
                rem = _rl_from_headers(hdrs).get("remaining-tokens")
                try:
                    _tpm_sync(tkey, need, float(rem), tpm)
                except (TypeError, ValueError):
                    if usage.get("prompt_tokens") is not None:
                        _tpm_settle(tkey, need, (usage.get("prompt_tokens") or 0)
                                    + (usage.get("completion_tokens") or 0), tpm)
                    else:
                        _tpm_release(tkey, need, tpm)
            _record(provider, payload["model"], role, res, hdrs)
            sp.set(ratelimit=_rl_from_headers(hdrs) or None, tries=attempt + 1)
            return res
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:600]
            last = f"HTTP {e.code}: {detail}"
            wait_hint = _parse_try_again(detail) if e.code == 429 else None
            if e.code == 429 and tpm and reserved:
                if wait_hint is None:
                    try:
                        wait_hint = float((e.headers or {}).get("retry-after"))
                    except (TypeError, ValueError):
                        pass
                if wait_hint is not None:  # bucket empty until then; retry must wait for room
                    _tpm_penalize(tkey, need, wait_hint, tpm)
                else:
                    _tpm_release(tkey, need, tpm)
                reserved = False
            if e.code in (413, 429) and _learn_itpm(provider, payload["model"], detail):
                lim = input_limit(provider, payload["model"])
                if lim and est_in > lim - INPUT_MARGIN_TOKENS:
                    break  # this input can never fit; the router moves on
            if e.code == 400 and "reasoning_effort" in payload and attempt < retries - 1:
                # some models reject an effort value (gemini-3.5-flash-lite: "none"); answer without it
                payload.pop("reasoning_effort")
                sp.note("retry", attempt=attempt + 1, error=last, delay=0, http=400)
                continue
            if e.code == 413:
                _learned_413(provider, payload["model"], size, max_tokens, detail, cls)
                break
            if _cooldown_from_error(provider, payload["model"], e.code, detail, e.headers):
                break  # daily limit / long retry-after / unknown model: retrying is pointless
            if e.code == 429 and not retry_429:
                break  # a sibling candidate can take over right now
            if e.code in (408, 409, 425, 429, 500, 502, 503, 504) and attempt < retries - 1:
                delay = _retry_delay(e.headers, attempt, detail if e.code == 429 else None)
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


THINK_ALLOWANCE = 16384


def _auto_tokens(spec, provider, model, size, max_tokens, cls="ascii"):
    """(max_tokens, skip_error) for one candidate. Explicit max_tokens passes through."""
    if max_tokens is not None:
        return max_tokens, None
    cap = spec.get("max_tokens", 4096)
    floor = spec.get("min_tokens", 512)
    est = est_tokens(provider, model, size, cls)
    if spec.get("out_ratio"):
        floor = max(floor, int(est * spec["out_ratio"]))
    lim = input_limit(provider, model)
    if lim and est > (lim - INPUT_MARGIN_TOKENS) * _probe_slack(provider, cls):
        return None, (f"input too large for {provider}/{model}: ~{int(est)} tokens > "
                      f"{lim - INPUT_MARGIN_TOKENS}")
    mt = max(cap, floor)
    if (PROVIDERS.get(provider) or {}).get("thinking_in_max_tokens"):
        eff = spec.get("reasoning_effort")
        eff = eff.get(provider) if isinstance(eff, dict) else None  # a plain string is Groq-only
        if eff not in ("minimal", "none"):
            mt += THINK_ALLOWANCE  # thinking (measured 2.8K-15K tokens) must not eat the answer
    return mt, None


def run(spec, prompt, max_tokens=None, model=None, no_fallback=False, role=None, no_cache=False):
    """Cached front of _route(): an identical job within the TTL costs no provider call."""
    key = None
    if not no_cache:
        key = C.make_key(role=role, system=spec.get("system"), provider=spec.get("provider"),
                         model=spec.get("model"), pool=spec.get("pool"),
                         fallback_models=spec.get("fallback_models"), validate=spec.get("validate"),
                         reasoning_effort=spec.get("reasoning_effort"), override=model,
                         max_tokens=max_tokens, prompt=prompt)
        hit = C.get(key)
        if hit:
            hit["cached"] = True
            hit["route"] = []
            _record_cached(hit, role)
            return hit
    res = _route(spec, prompt, max_tokens, model, no_fallback, role)
    if res.get("ok") and isinstance(res.get("text"), str):
        res["text"] = strip_echoed_header(res["text"], prompt)
    if key and res.get("ok") and not res.get("warning_note"):
        C.put(key, res)
    return res


def _route(spec, prompt, max_tokens=None, model=None, no_fallback=False, role=None):
    """Route one job: primary model, same-provider siblings (pool), then other providers.

    Returns the chat() result plus "route": candidates tried/skipped before the winner
    (each {"provider", "model", "error"}). max_tokens=None sizes the answer per candidate."""
    system = spec.get("system")
    kw = dict(system=system, role=role, reasoning_effort=spec.get("reasoning_effort"))
    prov = spec["provider"]
    pcfg = PROVIDERS.get(prov) or {}
    prim = model or spec.get("model") or pcfg.get("default_model")
    same = [(prov, prim)]
    if not model:
        pool = spec.get("pool") or pcfg.get("pool") or []
        same += [(prov, m) for m in pool if m != prim]
    groups = [same]
    if not no_fallback:
        fm = spec.get("fallback_models") or {}
        groups += [[(q, fm.get(q) or PROVIDERS[q]["default_model"])] for q in FALLBACK_ORDER if q != prov]

    size = len(prompt) + len(system or "")
    cls = script_class((system or "") + prompt)
    route, best, dead = [], None, set()
    for group in groups:
        viable = []
        for p, m in group:
            if p in dead:
                continue
            err = _cooling(p, m)
            mt = None
            if not err:
                mt, err = _auto_tokens(spec, p, m, size, max_tokens, cls)
            if err:
                route.append({"provider": p, "model": m, "error": err})
            else:
                viable.append((p, m, mt))

        def has_room(c):
            lim = tpm_limit(c[0], c[1])
            return not lim or _tpm_room((c[0], c[1]),
                                        est_tokens(c[0], c[1], size, cls) + min(c[2], OUT_ESTIMATE), lim)
        viable.sort(key=lambda c: not has_room(c))  # stable: candidates with room first
        for i, (p, m, mt) in enumerate(viable):
            if p in dead:
                continue
            res = chat(p, prompt, model=m, max_tokens=mt,
                       retry_429=(i == len(viable) - 1), **kw)
            if res.get("ok"):
                reduced = max_tokens is None and mt < spec.get("max_tokens", 4096)
                if reduced and (res.get("truncated") or res.get("warning")):
                    best = best or res
                    route.append({"provider": p, "model": m,
                                  "error": f"truncated at auto max_tokens {mt}"})
                    dead.add(p)  # same-provider siblings share the TPM tier: go to next provider
                    continue
                verr = _validate(spec, res)
                if verr:
                    res["warning_note"] = "output is not valid JSON"
                    best = best or res
                    route.append({"provider": p, "model": m, "error": verr})
                    continue
                res["route"] = route
                return res
            route.append({"provider": p, "model": res.get("model") or m,
                          "error": res.get("error")})
            if str(res.get("error")).startswith(("missing key", "unknown provider")):
                dead.add(p)  # siblings cannot work either
    if best:
        best["route"] = route
        return best
    return {"ok": False, "error": "all providers failed", "route": route}


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
