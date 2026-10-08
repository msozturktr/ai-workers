# ai-workers

Claude Code icin isci havuzu MCP server'i. Beyin = Claude (orkestratör), isciler = ucretsiz API modelleri.

## Organizasyon semasi

    sen -> Claude Code (planlama, karar, kalite kontrolu)
             |
         MCP: ai-workers
           +-- delegate(role, task, files?)   tek is
           +-- fanout(items[] | files[])      N paralel is (dosya basina bir is)
           +-- ask(provider, prompt)     dogrudan model cagrisi
           +-- models(provider)          canli model listesi
           +-- org_status()              anahtar/rol durumu

## Roller (varsayilan)

| rol         | saglayici  | model                    | ise yarar yer                     |
|-------------|------------|--------------------------|-----------------------------------|
| researcher  | gemini     | gemini-3.6-flash         | uzun dokuman okuma, arastirma     |
| summarizer  | groq       | openai/gpt-oss-120b      | toplu ozetleme                    |
| coder       | groq       | openai/gpt-oss-120b      | kod taslagi, bolerplate           |
| reviewer    | openrouter | nvidia/nemotron-3-ultra-550b-a55b:free | ikinci goz / elestiri |
| translator  | groq       | openai/gpt-oss-120b      | ceviri                            |
| classifier  | groq       | openai/gpt-oss-20b       | etiketleme, triage (en hizli)     |
| extractor   | groq       | openai/gpt-oss-120b      | metinden yapisal JSON cikarma     |

Toplu isler Groq'ta (ucretsiz, gunde 1000 istek); Gemini (Tier 1, ucretli) sadece
researcher ve Groq'un girdi butcesini asan buyuk-context isler icin.

## Dosya besleme (token tasarrufunun asil kaynagi)

`delegate` ve `fanout` `files` parametresi alir: mutlak yol, dizin veya glob (`~` olur).
Sunucu dosyayi kendisi okur; icerik Claude'un context'ine hic girmez, sadece isci ciktisi doner.

    delegate(role="summarizer", task="...", files=["~/Projeler/gripsim/README.md"])
    fanout(role="classifier", task="...", files=["~/Projeler/gripsim/scripts/**/*.cs"])

- `delegate`: tum dosyalar tek girdi olarak birlesir.
- `fanout`: her dosya ayri is; buyuk dosya satir sinirinda parcalanir (`chunk_chars`,
  varsayilan rolun saglayici butcesi: Groq ~11K, Gemini 200K karakter). En fazla 200 is.
- Atlananlar: gizli dosyalar (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `*secret*`,
  `~/.ssh`, `~/.config/ai-workers` ...), ikili dosyalar, 8 MB ustu. Dizin/glob taramasinda
  `.git`, `node_modules`, `__pycache__`, `.godot`, `obj`, `bin`, `.next`, `.venv` atlanir.
- Saglayici girdi butceleri (`max_input_chars`): groq 12K, openrouter 200K, gemini 1.5M.
  Asan girdi ag cagrisi yapilmadan sonraki saglayiciya duser.

## Claude Code entegrasyonu

- `initialize` yanitinda `instructions` gonderilir: Claude her oturumda ne zaman isciye
  vermesi gerektigini ve `files` kullanimini sistem prompt'unda gorur.
- `delegate` ve `fanout` `_meta["anthropic/alwaysLoad"]=true` ile isaretli: tool search'e
  ertelenmez, her oturumda tam sema ile yuklu gelir. Diger tool'lar ertelenmis kalir.
- Tool cagrilari ayri thread'lerde calisir; uzun bir fanout `ping`'i ve diger cagrilari bloklamaz.

Rolleri degistirmek/eklemek: `~/.config/ai-workers/config.json`

    {
      "roles": {
        "coder":  { "provider": "groq", "model": "openai/gpt-oss-120b", "system": "..." },
        "seo":    { "provider": "gemini", "model": "gemini-3.6-flash", "system": "Sen bir SEO editorusun..." }
      }
    }

## Anahtarlar

`~/.config/ai-workers/env` (mode 600):

    GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
    GROQ_API_KEY=...          # https://console.groq.com/keys
    OPENROUTER_API_KEY=...    # https://openrouter.ai/keys

En az bir anahtar yeterli; eksik saglayicinin isi otomatik olarak digerine duser (fallback sirasi: groq -> gemini -> openrouter).

## Kurulum (uygulama olarak)

    bash ~/AI/ai-workers/install.sh

Uc sey kurar:

- `~/.local/bin/ai-workers` - komut
- `ai-workers.service` (systemd --user) - pano servisi, girislerde otomatik baslar
- uygulama menusu kisayolu (`ai-workers`, tiklayinca panoyu tarayicida acar)

Kontrol: `ai-workers doctor`

## Komutlar

    ai-workers                     panoyu ac (servis kapaliysa baslatir)
    ai-workers usage               kalan kullanim raporu  (--json ham veri)
    ai-workers status              saglayici + rol durumu
    ai-workers roles [-v]          tanimli roller
    ai-workers run <rol> "<is>" [-f yol ...]     tek is; stdin verilirse veri olarak eklenir
    ai-workers fanout <rol> "<is>" [-f glob ...] stdin'deki her satir / her dosya ayri paralel is
    ai-workers models <saglayici>  canli model listesi
    ai-workers serve               panoyu on planda calistir
    ai-workers start|stop|restart|logs   servis yonetimi
    ai-workers doctor              kurulum kontrolu

Ornekler:

    echo "Merhaba dunya" | ai-workers run translator "Ingilizceye cevir."
    ai-workers fanout summarizer "Dosyayi ozetle" -f '~/Projeler/gripsim/*.md' -c 4

## Kalan kullanim panosu

    python3 ~/AI/ai-workers/dashboard.py          # http://127.0.0.1:8765
    python3 ~/AI/ai-workers/dashboard.py --once   # terminale JSON

Sadece 127.0.0.1'e baglanir, 20 sn'de bir kendini yeniler. MCP tarafinda ayni veri `usage` tool'unda.

Kalan kullanim her saglayicida ayni kaynaktan gelmiyor:

| saglayici  | kalan kullanim      | kaynak                                      |
|------------|---------------------|---------------------------------------------|
| groq       | canli, kesin        | her yanitin `x-ratelimit-*` header'lari     |
| openrouter | canli, kesin        | `/api/v1/key` -> `free_model_daily_requests`|
| gemini     | yerel sayac, **model bazli** | kota header'i YOK; limitler config.json'da model model |
| claude     | **canli, kesin**    | `claude -p /usage` -> oturum ve haftalik kalan % (120 sn onbellek) |

Claude'un canli yuzdeleri `claude -p /usage` ciktisindan ayristirilir ve
`~/.config/ai-workers/claude_live.json`'da 120 sn onbelleklenir; bayatlayinca arka plan
thread'i tazeler, pano beklemez. Bu sayim bu makinedeki oturumlara dayanir - diger
cihazlar ve claude.ai dahil degildir.

Panodaki hero sayisi **tum saglayicilar arasindaki en siki kotadir** (Claude dahil).

Her istek `~/.config/ai-workers/ledger.jsonl`'e yazilir (ts, saglayici, model, rol, token, hata).
Groq'un son rate-limit durumu `~/.config/ai-workers/ratelimit.json`'da tutulur.

Gemini limitleri **model bazlidir** (AI Studio > Rate Limits sayfasindan elle girilir):

    "limits": { "gemini": { "tier": "Tier 1", "models": {
      "gemini-3.6-flash":      { "rpm": 1000, "tpm": 2000000, "rpd": 10000 },
      "gemini-3.5-flash-lite": { "rpm": 4000, "tpm": 4000000, "rpd": 150000 }
    }}}

Pano her model icin ayri metre cizer (gunluk istek) ve altinda dakikalik istek/token
kullanimini gosterir. Limitler degisirse (tier yukseltme) bu dosyayi guncelle -
Gemini bunu API'den bildirmiyor.

**Dikkat:** Tier 1 ucretsiz katman degildir; faturalandirma bagli bir seviyedir.
`free_tier_models` altinda ucretsiz katman limitleri de tutulur (AI Studio'daki
"Compare: Free tier" farklarindan hesaplandi) ve pano her Gemini modeli icin
"ucretsizde X/Y kalirdi" yazar; asildiginda uyari verir.

Ucretsiz katmanda Gemini cok dar: Flash modelleri **gunde 20 istek**, Flash Lite
**gunde 500**. Karsilastirma icin Groq gunde 1.000, OpenRouter gunde 50.
Yani ucretsiz kalmak gerekiyorsa toplu isler Groq'a verilmeli; Gemini sadece
1M context gereken az sayida ise ayrilmali.

## Dogrulanmis notlar (2026-10-01 canli test)

- Groq'a User-Agent basligi sart; yoksa Cloudflare `HTTP 403 error code 1010` dondurur
- OpenRouter'da `openai/gpt-oss-*:free` kaldirildi; gercek ucretsiz liste icin `models(free_only=true)`
- `gemini-2.5-flash-lite` yeni hesaplara kapali -> `gemini-3.5-flash-lite`
- Gemini reasoning modelleri dusunme adiminda token yiyor: `max_tokens` < 512 ise bos yanit gelebilir
- OpenRouter ucretsiz katman gunluk ~50 istek -> yuksek hacimli `fanout` icin gemini/groq kullan

## Dayaniklilik

- 429/5xx/timeout -> 4 deneme; saglayicinin `Retry-After` degerine uyar, yoksa ustel geri cekilme
- Rolun saglayicisi tamamen duserse (anahtar yok, girdi cok buyuk, hata) diger saglayicilara fallback;
  fanout'ta tek isin hatasi digerlerini etkilemez
- OpenRouter'in HTTP 200 govdesinde dondurdugu hatalar basarisiz sayilir
- Groq ucretsiz katman model basina 8K token/dk: paralel buyuk isler 429 alir ve Gemini'ye duser
- `fanout` concurrency varsayilani 4 (ucretsiz katman rate-limitleri icin); Groq'ta 8'e kadar guvenli
- Model adlari ucretsiz katmanlarda degisiyor -> tahmin etmek yerine `models` ile canli listeyi dogrula

## Elle test

    printf '%s\n' \
      '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
      '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"org_status","arguments":{}}}' \
      | python3 server.py
