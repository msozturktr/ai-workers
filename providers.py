"""Ucretsiz API saglayicilari icin tek kod yolu (hepsi OpenAI-uyumlu chat endpoint)."""
import json, os, threading, time, urllib.request, urllib.error

UA = "ai-workers/1.0 (+https://github.com/local/ai-workers) python-urllib"

CONFIG_DIR = os.path.expanduser("~/.config/ai-workers")
ENV_FILE = os.path.join(CONFIG_DIR, "env")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _load_env_file():
    """~/.config/ai-workers/env dosyasindaki KEY=VAL satirlarini ortama ekler."""
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
        # 1M token context; Tier 1 ucretli -> buyuk-context isler buraya
        "max_input_chars": 1_500_000,
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "key_env": ["GROQ_API_KEY"],
        "default_model": "openai/gpt-oss-120b",
        "light_model": "openai/gpt-oss-20b",
        # ucretsiz katman model basina 8K token/dk (girdi+cikti); ~3-4K token girdi
        # paralel islerin ayni dakikaya sigmasini saglar -> kucuk isler
        "max_input_chars": 12_000,
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
    "researcher":  {"provider": "gemini", "model": "gemini-3.6-flash",
                    "system": "Sen bir arastirma analistisin. Verilen kaynagi dikkatle oku, sadece metinde gecen olgulara dayanarak yanitla. Emin olmadigin yerde 'belirsiz' yaz. Kisa, maddeli, atif numarali yanit ver."},
    "summarizer":  {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "system": "Metni sadiklikla ozetle. Yeni bilgi ekleme, yorum katma. Cikti: en fazla 8 madde."},
    "coder":       {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "system": "Sen bir yazilim muhendisisin. Sadece calisan kod uret, aciklama istenmedikce yazma. Mevcut dil/stil kurallarina uy."},
    "reviewer":    {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "system": "Sen bir kod/metin elestirmenisin. Yalnizca somut, dogrulanabilir kusurlari listele. Her bulgu icin: nerede, neden hatali, nasil tetiklenir. Kusur yoksa 'bulgu yok' yaz."},
    "translator":  {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "system": "Verilen metni istenen dile cevir. Terimleri koru, aciklama ekleme, sadece cevirisini dondur."},
    "classifier":  {"provider": "groq", "model": "openai/gpt-oss-20b",
                    "system": "Sen bir siniflandiricisin. Yalnizca istenen etiketi dondur, baska hicbir sey yazma."},
    "extractor":   {"provider": "groq", "model": "openai/gpt-oss-120b",
                    "system": "Metinden istenen alanlari cikar ve gecerli JSON olarak dondur. Bulunmayan alan icin null kullan. JSON disinda hicbir sey yazma."},
}

# Groq ucretsiz ve genis kotali; Gemini (Tier 1, ucretli) buyuk-context isler icin;
# OpenRouter ucretsiz ama gunde ~50 istek -> en son.
FALLBACK_ORDER = ["groq", "gemini", "openrouter"]


def load_roles():
    roles = dict(DEFAULT_ROLES)
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f)
        for name, spec in (user.get("roles") or {}).items():
            base = dict(roles.get(name, {}))
            base.update(spec)
            roles[name] = base
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return roles


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
    """Groq/OpenAI-uyumlu rate-limit header'larini ayikla."""
    out = {}
    for k, v in (h or {}).items():
        kl = k.lower()
        if kl.startswith("x-ratelimit-"):
            out[kl[len("x-ratelimit-"):]] = v
    return out


_record_lock = threading.Lock()


def _record(provider, model, role, res, headers):
    """Ledger'a bir satir ekle ve saglayicinin son rate-limit durumunu sakla."""
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
                snap = json.load(open(RATELIMIT_SNAPSHOT))
            except (FileNotFoundError, json.JSONDecodeError):
                snap = {}
            snap[provider] = {"ts": row["ts"], "model": model, **rl}
            with open(RATELIMIT_SNAPSHOT, "w") as f:
                json.dump(snap, f, indent=2)
    except Exception:
        pass  # olcum asla isi bozmaz


def _retry_delay(headers, attempt):
    """Saglayicinin Retry-After degerine uy (Groq TPM limitinde saniyeler mertebesinde)."""
    try:
        ra = float((headers or {}).get("retry-after"))
        return max(0.5, min(ra + 0.5, 60))
    except (TypeError, ValueError):
        return min(2 ** attempt * 2, 20)


def chat(provider, prompt, system=None, model=None, max_tokens=4096,
         temperature=0.2, retries=4, timeout=180, role=None):
    """Tek bir worker cagrisi. Rate-limit/5xx durumunda geri cekilerek tekrar dener.

    Asla exception atmaz: anahtar yoksa, saglayici bilinmiyorsa veya girdi saglayicinin
    butcesini asiyorsa ag cagrisi yapmadan {"ok": False, "skipped": True} doner ki
    fallback bir sonrakine gecebilsin.
    """
    if provider not in PROVIDERS:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"bilinmeyen saglayici: {provider}"}
    cfg = PROVIDERS[provider]
    key = api_key(provider)
    if not key:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"anahtar yok ({ENV_FILE} -> {cfg['key_env'][0]}=...)"}
    size = len(prompt) + len(system or "")
    if size > cfg["max_input_chars"]:
        return {"ok": False, "skipped": True, "provider": provider, "model": model,
                "error": f"girdi cok buyuk: {size} karakter > {provider} siniri "
                         f"{cfg['max_input_chars']}"}
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    payload = {
        "model": model or cfg["default_model"],
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    last = None
    for attempt in range(retries):
        try:
            data, hdrs = _post(cfg["url"], key, payload, timeout)
            if data.get("error") and not data.get("choices"):
                # OpenRouter bazi upstream hatalarini HTTP 200 govdesinde dondurur
                err = data["error"]
                code = err.get("code") if isinstance(err, dict) else None
                last = f"HTTP 200 govde hatasi {code}: {json.dumps(err, ensure_ascii=False)[:600]}"
                if code in (408, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(min(2 ** attempt * 2, 20))
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
                    "Bos yanit: model dusunme adiminda token butcesini tuketti. "
                    "max_tokens'i artir (reasoning modelleri icin >=512)."
                    if not text.strip() and choice.get("finish_reason") == "length" else None
                ),
                # metin var ama max_tokens'ta kesildi -> cagiran eksik cikti oldugunu bilmeli
                "truncated": bool(text.strip()) and choice.get("finish_reason") == "length",
            }
            _record(provider, payload["model"], role, res, hdrs)
            return res
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:600]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (408, 409, 425, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(_retry_delay(e.headers, attempt))
                continue
            break
        except Exception as e:  # ag hatasi / timeout
            last = f"{type(e).__name__}: {e}"
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt * 2, 20))
                continue
            break
    res = {"ok": False, "provider": provider, "model": payload["model"], "error": last}
    _record(provider, payload["model"], role, res, None)
    return res


def chat_with_fallback(providers, prompt, **kw):
    """Sirayla dene; ilk basarili yaniti dondur. Hangi saglayicilarin dustugunu raporla."""
    tried = []
    for p in providers:
        res = chat(p, prompt, **kw)
        if res["ok"]:
            if tried:
                res["fallback_from"] = tried
            return res
        tried.append({"provider": p, "error": res["error"]})
    return {"ok": False, "error": "tum saglayicilar basarisiz", "tried": tried}


def key_info(provider):
    """Saglayicinin kendi kota endpoint'i varsa canli veriyi getir."""
    key = api_key(provider)
    if not key:
        return {"ok": False, "error": "anahtar yok"}
    if provider != "openrouter":
        return {"ok": False, "error": "bu saglayici kota endpoint'i sunmuyor"}
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
        return {"ok": False, "error": "anahtar yok"}
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
