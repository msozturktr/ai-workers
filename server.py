#!/usr/bin/env python3
"""ai-workers: ucretsiz API modellerini Claude Code'a worker tool'u olarak acan MCP server.

Stdio uzerinden JSON-RPC. Harici bagimlilik yok.
"""
import json, os, sys, threading, traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers as P
import usage as U
import sources as S

PROTOCOL = "2025-06-18"
SERVER = {"name": "ai-workers", "version": "1.1.0"}

INSTRUCTIONS = """ai-workers: Claude'un token'ini korumak icin isci model havuzu (Groq/OpenRouter ucretsiz, Gemini buyuk-context).
Varsayilan: mekanik isi kendin yapma, isciye ver -> toplu ozet, ceviri, siniflandirma/etiketleme, log ve cikti tarama, buyuk dosya/dizin ozeti, dosya basina tekrarli analiz, taslak metin, regex/veri donusturme, boilerplate taslagi.
ICERIGI YAPISTIRMA: dosya uzerinde is varsa `files` parametresine MUTLAK yol/glob/dizin ver (orn. ["~/Projeler/gripsim/scripts/**/*.cs"]). Sunucu dosyayi kendisi okur, icerik senin context'ine hic girmez; sana sadece isci ciktisi doner. Bir dosyayi once Read edip sonra input'a kopyalamak token israfidir.
Tek is -> delegate. N dosya / N parca -> fanout (files ile her dosya ayri is olur; buyuk dosyalar otomatik parcalanir, Groq sinirini asan girdi otomatik Gemini'ye duser).
Gizli dosyalar (.env, anahtarlar, ~/.ssh ...) otomatik atlanir. Isci ciktisi dogrulanmamis taslaktir: kritik noktada kontrol et. Mimari karar, kritik kod duzenlemesi, guvenlik/veri kaybi riski olan isler sende kalir.
Komut ciktisi (log, test ciktisi) taranacaksa once scratchpad'e dosyaya yaz, sonra files ile ver."""

ALWAYS = {"anthropic/alwaysLoad": True}

TOOLS = [
    {
        "name": "org_status",
        "description": "Organizasyonun durumu: hangi saglayicilarda anahtar var, hangi roller tanimli, her rol hangi model. Ilk is olarak veya bir worker hata verdiginde cagir.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delegate",
        "description": "Tek bir isi isci modele devret (rol = saglayici + model + sistem promptu). Dosya uzerinde is icin icerigi yapistirma: `files` ile mutlak yol/glob ver, sunucu kendisi okur (token tasarrufu). Rolun saglayicisi hata verir ya da girdi sinirini asarsa digerlerine otomatik gecer. Cok is/cok dosya varsa fanout kullan.",
        "_meta": ALWAYS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "org_status'tan bir rol adi: researcher, summarizer, coder, reviewer, translator, classifier, extractor"},
                "task": {"type": "string", "description": "Isci icin net talimat. Ne istedigini ve cikti formatini acikca yaz."},
                "input": {"type": "string", "description": "Uzerinde calisilacak kisa veri/metin (opsiyonel). Dosya icerigi icin bunun yerine files kullan."},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Mutlak dosya yolu, dizin veya glob listesi (~ olur). Sunucu okur ve hepsini tek girdi olarak isciye verir. Gizli/ikili dosyalar atlanir."},
                "model": {"type": "string", "description": "Rolun varsayilan modelini ez (opsiyonel)."},
                "max_tokens": {"type": "integer", "description": "Varsayilan 4096."},
                "no_fallback": {"type": "boolean", "description": "true ise sadece rolun kendi saglayicisini dener."},
            },
            "required": ["role", "task"],
        },
    },
    {
        "name": "fanout",
        "description": "Isleri paralel olarak isci havuzuna dagit. Toplu ozetleme, ceviri, siniflandirma, dosya basina analiz gibi N parcali isler icin bunu kullan - tek tek delegate cagirma. `files` verirsen her dosya ayri is olur (buyuk dosya otomatik parcalanir); icerik senin context'ine girmez.",
        "_meta": ALWAYS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Tum isler icin varsayilan rol."},
                "task": {"type": "string", "description": "Tum isler icin varsayilan talimat."},
                "items": {
                    "type": "array",
                    "description": "Is listesi. Her oge ya duz metin (input olarak kullanilir) ya da {input, task?, role?, model?, label?} nesnesi.",
                    "items": {"type": ["string", "object"]},
                },
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Mutlak yol/dizin/glob listesi. Her dosya (ya da buyuk dosyanin her parcasi) ortak role+task ile ayri is olur. items ile birlikte kullanilabilir."},
                "chunk_chars": {"type": "integer", "description": "files icin parca boyu (karakter). Varsayilan: rolun saglayici sinirina gore (Groq ~11K, Gemini 200K)."},
                "concurrency": {"type": "integer", "description": "Es zamanli istek sayisi. Ucretsiz katman rate-limitleri icin varsayilan 4; Groq'ta 8'e kadar cikilabilir."},
                "max_tokens": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "usage",
        "_meta": {"anthropic/searchHint": "ai-workers kota kalan kullanim limit"},
        "description": "Kalan kullanim raporu: her saglayicinin kotasi (Groq/OpenRouter canli, Gemini yerel sayac) ve Claude tarafinin bugunku token tuketimi. Toplu bir fanout baslatmadan once veya kullanici 'ne kadar kaldi' diye sordugunda cagir.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask",
        "description": "Belirli bir saglayici/modele dogrudan tek cagri. Rol soyutlamasini atlamak, yeni bir model denemek veya model karsilastirmasi yapmak icin.",
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
        "description": "Bir saglayicinin canli model listesini cek. Model adlari degistiginde (ucretsiz katmanlar sik degisir) gercek listeyi buradan dogrula, tahmin etme.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["gemini", "groq", "openrouter"]},
                "free_only": {"type": "boolean", "description": "Sadece openrouter icin anlamli: ':free' ile bitenleri filtreler."},
            },
            "required": ["provider"],
        },
    },
]


def _compose(task, data):
    return f"{task}\n\n--- VERI ---\n{data}" if data else task


def _roles():
    return P.load_roles()


def t_org_status(_a):
    roles = _roles()
    have = P.available_providers()
    lines = ["# ai-workers organizasyon durumu", "", "## Saglayicilar"]
    for name, cfg in P.PROVIDERS.items():
        mark = "HAZIR" if name in have else "ANAHTAR YOK"
        lines.append(f"- {name}: {mark} (varsayilan: {cfg['default_model']}, hafif: {cfg['light_model']})")
    if not have:
        lines += ["", f"Hicbir anahtar yok. {P.ENV_FILE} dosyasina ekle:",
                  "  GEMINI_API_KEY=...", "  GROQ_API_KEY=...", "  OPENROUTER_API_KEY=..."]
    lines += ["", "## Roller"]
    for name, spec in roles.items():
        ready = "" if spec["provider"] in have else "  [saglayicisi hazir degil -> fallback kullanilir]"
        lines.append(f"- {name}: {spec['provider']} / {spec['model']}{ready}")
    lines += ["", f"Rolleri duzenlemek icin: {P.CONFIG_FILE}"]
    return "\n".join(lines)


def t_delegate(a):
    roles = _roles()
    role = a["role"]
    if role not in roles:
        return f"HATA: '{role}' rolu yok. Mevcut roller: {', '.join(roles)}"
    spec = roles[role]
    kw = dict(system=spec.get("system"), model=a.get("model") or spec.get("model"),
              max_tokens=a.get("max_tokens", 4096), role=role)
    data, nfiles, skipped = a.get("input"), 0, []
    if a.get("files"):
        text, nfiles, skipped, err = S.bundle(a["files"])
        if err:
            return f"HATA: {err}"
        if not nfiles:
            return "HATA: okunabilir dosya yok" + S.format_skipped(skipped)
        data = f"{data}\n\n{text}" if data else text
    prompt = _compose(a["task"], data)
    if a.get("no_fallback"):
        res = P.chat(spec["provider"], prompt, **kw)
    else:
        order = [spec["provider"]] + [p for p in P.FALLBACK_ORDER if p != spec["provider"]]
        # fallback'te rolun modeli gecersiz kalir; saglayicinin varsayilanina birak
        res = P.chat(spec["provider"], prompt, **kw)
        if not res["ok"]:
            kw.pop("model")
            res2 = P.chat_with_fallback(order[1:], prompt, **kw)
            if res2.get("ok"):
                res2["fallback_from"] = [{"provider": spec["provider"], "error": res["error"]}]
                res = res2
    if not res.get("ok"):
        return ("WORKER BASARISIZ\n" + json.dumps(res, ensure_ascii=False, indent=2)
                + S.format_skipped(skipped))
    if res.get("warning"):
        return f"UYARI: {res['warning']}"
    if res.get("truncated"):
        res["text"] += _TRUNC_NOTE
    head = f"[{role} -> {res['provider']}/{res['model']}"
    if nfiles:
        head += f", {nfiles} dosya {len(prompt) // 1000}K kar."
    head += "]"
    if res.get("fallback_from"):
        head += f" (fallback: {_fb_summary(res['fallback_from'])})"
    return f"{head}\n\n{res['text']}" + S.format_skipped(skipped)


_TRUNC_NOTE = "\n\n[UYARI: yanit max_tokens sinirinda KESILDI - eksik; max_tokens'i artirip tekrar dene]"


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
            return "HATA: files ile ortak 'role' (gecerli) ve 'task' gerekli."
        prov = P.PROVIDERS.get(roles[default_role]["provider"]) or {}
        budget = prov.get("max_input_chars", 200_000) - len(default_task) \
            - len(roles[default_role].get("system") or "") - 500
        size = int(a.get("chunk_chars") or min(max(budget, 4000), 200_000))
        file_items, skipped, err = S.items(a["files"], size)
        if err:
            return f"HATA: {err}"
        items += file_items
    if not items:
        return "HATA: is yok (items bos ve files hicbir okunabilir dosyayla eslesmedi)" \
            + S.format_skipped(skipped)
    if len(items) > MAX_JOBS:
        return (f"HATA: {len(items)} is > {MAX_JOBS}. Glob'u daralt veya chunk_chars'i buyut "
                "(gunluk kotalari bir cagrida tuketmemek icin).")

    jobs = []
    for i, it in enumerate(items):
        if isinstance(it, str):
            it = {"input": it}
        role = it.get("role") or default_role
        task = it.get("task") or default_task
        if not role or not task:
            return "HATA: her is icin bir rol ve bir task gerekli (ya ortak 'role'/'task' ver ya da oge icinde belirt)."
        if role not in roles:
            return f"HATA: '{role}' rolu yok. Mevcut roller: {', '.join(roles)}"
        jobs.append({"idx": i, "label": it.get("label") or f"#{i+1}", "role": role,
                     "task": task, "input": it.get("input"), "model": it.get("model")})

    def run(j):
        try:
            return run_job(j)
        except Exception as e:  # tek isin hatasi tum fanout'u dusurmesin
            return j, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def run_job(j):
        spec = roles[j["role"]]
        res = P.chat(spec["provider"], _compose(j["task"], j["input"]),
                     system=spec.get("system"), model=j["model"] or spec.get("model"),
                     max_tokens=max_tok, role=j["role"])
        if not res["ok"]:
            order = [p for p in P.FALLBACK_ORDER if p != spec["provider"]]
            alt = P.chat_with_fallback(order, _compose(j["task"], j["input"]),
                                       system=spec.get("system"), max_tokens=max_tok,
                                       role=j["role"])
            if alt.get("ok"):
                alt["fallback_from"] = spec["provider"]
                res = alt
            else:
                res = {"ok": False, "error": _fb_summary(
                    [{"provider": spec["provider"], "error": res.get("error")}]
                    + alt.get("tried", []))}
        return j, res

    with ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(run, jobs))

    ok = sum(1 for _, r in results if r.get("ok"))
    out = [f"# fanout: {ok}/{len(results)} basarili (concurrency={conc})"]
    for j, r in sorted(results, key=lambda x: x[0]["idx"]):
        if r.get("ok"):
            fb = f" fallback<-{r['fallback_from']}" if r.get("fallback_from") else ""
            body = (r["text"] or "(bos yanit)") + (_TRUNC_NOTE if r.get("truncated") else "")
            if r.get("warning"):
                body = f"UYARI: {r['warning']}"
            out.append(f"\n## {j['label']} [{j['role']} -> {r['provider']}/{r['model']}{fb}]\n{body}")
        else:
            out.append(f"\n## {j['label']} [BASARISIZ]\n{r.get('error') or r.get('tried')}")
    return "\n".join(out) + S.format_skipped(skipped)


def t_ask(a):
    res = P.chat(a["provider"], a["prompt"], system=a.get("system"), model=a.get("model"),
                 max_tokens=a.get("max_tokens", 4096), temperature=a.get("temperature", 0.2))
    if not res["ok"]:
        return "BASARISIZ\n" + json.dumps(res, ensure_ascii=False, indent=2)
    warn = f"\nUYARI: {res['warning']}" if res.get("warning") else ""
    trunc = _TRUNC_NOTE if res.get("truncated") else ""
    return f"[{res['provider']}/{res['model']} tokens={res['tokens']}]{warn}\n\n{res['text']}{trunc}"


def t_models(a):
    r = P.list_models(a["provider"], a.get("free_only", False))
    if not r.get("ok"):
        return f"BASARISIZ: {r.get('error')}"
    return f"{r['provider']}: {r['count']} model\n" + "\n".join(f"- {m}" for m in r["models"])


def t_usage(_a):
    return U.format_report(U.snapshot())


HANDLERS = {"org_status": t_org_status, "delegate": t_delegate, "fanout": t_fanout,
            "ask": t_ask, "models": t_models, "usage": t_usage}


_send_lock = threading.Lock()


def send(obj):
    line = json.dumps(obj) + "\n"
    with _send_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _call_tool(mid, fn, args):
    """Tool'u ayri thread'de calistir: uzun fanout ping'i ve diger cagrilari bloklamasin."""
    try:
        text = fn(args)
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": text}]}})
    except Exception:
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "SERVER HATASI\n" + traceback.format_exc()}],
            "isError": True,
        }})


def main():
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
                          "error": {"code": -32602, "message": f"bilinmeyen tool: {name}"}})
                    continue
                pool.submit(_call_tool, mid, fn, args)
            elif method and method.startswith("notifications/"):
                continue
            elif mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": f"desteklenmeyen method: {method}"}})
        except Exception:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "SERVER HATASI\n" + traceback.format_exc()}],
                    "isError": True,
                }})
    pool.shutdown(wait=True)  # stdin kapandi: suren isler bitsin


if __name__ == "__main__":
    main()
