"""Remaining usage calculation. Uses the best available resource for each provider."""
import glob, json, os, re, subprocess, threading, time
import providers as P

CLAUDE_DIR = os.path.expanduser("~/.claude")
DAY = 86400

CLAUDE_LIVE_CACHE = os.path.join(P.CONFIG_DIR, "claude_live.json")
CLAUDE_LIVE_TTL = 120          # sec: do not re-fetch below this duration
_refresh_lock = threading.Lock()
_refreshing = False

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# "Current session: 14% used - resets Oct 1, 11:39pm (Europe/Istanbul)"
LIMIT_RE = re.compile(
    r"Current\s+([^:\n]+?):\s*(\d+)%\s*used(?:[^\S\n]*[·|-]\s*resets\s*([^(\n]+))?",
    re.I)
SPAN_RE = re.compile(r"Last\s+(\d+\s*\w+)\s*[·|-]\s*([\d.,]+)\s*requests?"
                     r"(?:\s*[·|-]\s*([\d.,]+)\s*sessions?)?", re.I)


def parse_claude_usage(text):
    """Parses `claude -p /usage` output into a structured format."""
    t = ANSI.sub("", text or "")
    limits = []
    for label, pct, reset in LIMIT_RE.findall(t):
        limits.append({
            "label": " ".join(label.split()),
            "used_pct": int(pct),
            "remaining_pct": max(0, 100 - int(pct)),
            "resets": (reset or "").strip(" ·-\t") or None,
        })
    spans = []
    for span, reqs, sess in SPAN_RE.findall(t):
        spans.append({
            "span": " ".join(span.split()),
            "requests": int(reqs.replace(",", "").replace(".", "")),
            "sessions": int(sess.replace(",", "").replace(".", "")) if sess else None,
        })
    plan = None
    if re.search(r"subscription", t, re.I):
        plan = "subscription"
    elif re.search(r"API|credit", t, re.I):
        plan = "api"
    return {"limits": limits, "spans": spans, "plan": plan,
            "raw_lines": [l for l in (x.strip() for x in t.splitlines()) if l][:14]}


def _read_live_cache():
    try:
        return json.load(open(CLAUDE_LIVE_CACHE))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def fetch_claude_live(timeout=120):
    """Runs `claude -p /usage` and writes to cache. Blocks - call in background."""
    try:
        r = subprocess.run(["claude", "-p", "/usage"], capture_output=True, text=True,
                           timeout=timeout, cwd="/tmp",
                           env={**os.environ, "CLAUDE_CODE_SIMPLE": ""})
        out = (r.stdout or "") + (r.stderr or "")
        parsed = parse_claude_usage(out)
        ok = bool(parsed["limits"])
        data = {"ts": int(time.time()), "ok": ok, **parsed}
        if not ok:
            data["error"] = (out.strip()[:300] or "output empty")
    except subprocess.TimeoutExpired:
        data = {"ts": int(time.time()), "ok": False, "error": f"timeout ({timeout}s)",
                "limits": [], "spans": []}
    except FileNotFoundError:
        data = {"ts": int(time.time()), "ok": False, "error": "claude CLI not found",
                "limits": [], "spans": []}
    except Exception as e:
        data = {"ts": int(time.time()), "ok": False,
                "error": f"{type(e).__name__}: {e}", "limits": [], "spans": []}
    try:
        os.makedirs(P.CONFIG_DIR, exist_ok=True)
        with open(CLAUDE_LIVE_CACHE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
    return data


def claude_live(max_age=CLAUDE_LIVE_TTL, block_if_empty=False):
    """Live quota from cache; refreshes in background if stale (does not block the dashboard)."""
    global _refreshing
    cached = _read_live_cache()
    age = (int(time.time()) - cached["ts"]) if cached else None

    if cached is None and block_if_empty:
        return {**fetch_claude_live(), "age_sec": 0, "refreshing": False}

    stale = cached is None or age > max_age
    if stale:
        with _refresh_lock:
            if not _refreshing:
                _refreshing = True

                def job():
                    global _refreshing
                    try:
                        fetch_claude_live()
                    finally:
                        _refreshing = False

                threading.Thread(target=job, daemon=True).start()

    if cached is None:
        return {"ok": False, "pending": True, "refreshing": True, "limits": [], "spans": [],
                "note": "fetching initial measurement (claude -p /usage)"}
    return {**cached, "age_sec": age, "refreshing": _refreshing}


def _cfg():
    try:
        return json.load(open(P.CONFIG_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _ledger(since=None):
    rows = []
    try:
        with open(P.LEDGER) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is None or r.get("ts", 0) >= since:
                    rows.append(r)
    except FileNotFoundError:
        pass
    return rows


def _snapshot():
    try:
        return json.load(open(P.RATELIMIT_SNAPSHOT))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _local_day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _model_breakdown(provider, rows_today, now, model_limits, free_limits=None):
    """Per-model quota for providers with model-based limits (Gemini)."""
    seen = {}
    for r in rows_today:
        if r["provider"] != provider:
            continue
        seen.setdefault(r["model"], []).append(r)
    # show models that have defined limits but were not used today
    for m in model_limits:
        seen.setdefault(m, [])

    out = []
    for model, rs in sorted(seen.items()):
        lim = model_limits.get(model) or {}
        free = (free_limits or {}).get(model) or {}
        last_min = [r for r in rs if r["ts"] >= now - 60]
        tok_min = sum((r.get("in") or 0) + (r.get("out") or 0) for r in last_min)
        row = {
            "model": model,
            "requests_today": len(rs),
            "tokens_today": sum((r.get("in") or 0) + (r.get("out") or 0) for r in rs),
            "requests_last_min": len(last_min),
            "tokens_last_min": tok_min,
            "limits": lim or None,
            "quota": None,
        }
        if lim:
            q = {}
            if lim.get("rpd"):
                q["day_requests"] = {"used": len(rs), "limit": lim["rpd"],
                                     "remaining": max(0, lim["rpd"] - len(rs)),
                                     "reset": "daily"}
            if lim.get("rpm"):
                q["min_requests"] = {"used": len(last_min), "limit": lim["rpm"],
                                     "remaining": max(0, lim["rpm"] - len(last_min)),
                                     "reset": "60 sec"}
            if lim.get("tpm"):
                q["min_tokens"] = {"used": tok_min, "limit": lim["tpm"],
                                   "remaining": max(0, lim["tpm"] - tok_min),
                                   "reset": "60 sec"}
            row["quota"] = q
        if free:
            over = []
            if free.get("rpd") and len(rs) > free["rpd"]:
                over.append(f"daily requests {len(rs)}>{free['rpd']}")
            if free.get("rpm") and len(last_min) > free["rpm"]:
                over.append(f"min requests {len(last_min)}>{free['rpm']}")
            if free.get("tpm") and tok_min > free["tpm"]:
                over.append(f"min tokens {tok_min}>{free['tpm']}")
            row["free_tier"] = {
                "limits": free,
                "day_remaining": max(0, free["rpd"] - len(rs)) if free.get("rpd") else None,
                "exceeded": over or None,
            }
        out.append(row)
    return out


def workers_usage():
    """Worker models usage: live quota + local counter."""
    day0 = _local_day_start()
    rows_today = _ledger(day0)
    now = int(time.time())
    rows_min = [r for r in rows_today if r["ts"] >= now - 60]
    snap = _snapshot()
    limits = (_cfg().get("limits") or {})

    out = []
    for prov in P.PROVIDERS:
        mine = [r for r in rows_today if r["provider"] == prov]
        rec = {
            "provider": prov,
            "ready": bool(P.api_key(prov)),
            "today_requests": len(mine),
            "today_failed": sum(1 for r in mine if not r.get("ok")),
            "today_tokens_in": sum(r.get("in") or 0 for r in mine),
            "today_tokens_out": sum(r.get("out") or 0 for r in mine),
            "last_minute_requests": sum(1 for r in rows_min if r["provider"] == prov),
            "source": "local counter",
            "quota": None,
        }

        if prov == "groq":
            s = snap.get("groq")
            if s:
                try:
                    rr, lr = int(s.get("remaining-requests", 0)), int(s.get("limit-requests", 0))
                    rt, lt = int(s.get("remaining-tokens", 0)), int(s.get("limit-tokens", 0))
                    rec["quota"] = {
                        "requests": {"remaining": rr, "limit": lr,
                                     "reset": s.get("reset-requests")},
                        "tokens": {"remaining": rt, "limit": lt,
                                   "reset": s.get("reset-tokens")},
                    }
                    rec["source"] = "live (response headers)"
                    rec["as_of_age_sec"] = now - int(s.get("ts", now))
                except (TypeError, ValueError):
                    pass

        elif prov == "openrouter":
            info = P.key_info("openrouter")
            if info.get("ok"):
                f = info.get("free_model_daily_requests") or {}
                rec["quota"] = {
                    "requests": {"remaining": f.get("remaining"), "limit": f.get("limit"),
                                 "used": f.get("used"), "reset": "daily (UTC)"},
                }
                rec["credits"] = {"limit": info.get("limit"),
                                  "remaining": info.get("limit_remaining"),
                                  "usage": info.get("usage")}
                rec["free_tier"] = info.get("is_free_tier")
                rec["source"] = "live (/api/v1/key)"
                rec["as_of_age_sec"] = 0
            else:
                rec["note"] = info.get("error")

        elif prov == "gemini":
            lim = limits.get("gemini") or {}
            model_limits = lim.get("models") or {}
            rec["tier"] = lim.get("tier")
            rec["source"] = "local counter (Gemini does not provide quota headers)"
            if model_limits:
                rec["models"] = _model_breakdown(prov, rows_today, now, model_limits,
                                                 lim.get("free_tier_models"))
                rec["per_model_limits"] = True
            else:
                # legacy provider-level limit definition
                rpd, rpm = lim.get("requests_per_day"), lim.get("requests_per_minute")
                if rpd or rpm:
                    rec["quota"] = {}
                    if rpd:
                        rec["quota"]["requests"] = {
                            "remaining": max(0, rpd - len(mine)), "limit": rpd,
                            "used": len(mine), "reset": "daily"}
                    if rpm:
                        rec["quota"]["per_minute"] = {
                            "remaining": max(0, rpm - rec["last_minute_requests"]),
                            "limit": rpm, "used": rec["last_minute_requests"]}
                else:
                    rec["note"] = ("Limit not defined: config.json -> "
                                   "limits.gemini.models")
        out.append(rec)
    return out


def claude_usage():
    """Claude side: actual token consumption from transcripts + last quota event.

    Note: subscription 'remaining %' value is server-side; there is no continuous local feed.
    """
    day0 = _local_day_start()
    files = glob.glob(os.path.join(CLAUDE_DIR, "projects", "**", "*.jsonl"), recursive=True)
    today_files = [f for f in files if os.path.getmtime(f) >= day0]

    by_model, sessions = {}, set()
    last_quota = None
    for fp in today_files:
        try:
            for line in open(fp, errors="replace"):
                if '"usage"' not in line and '"quotaLimits"' not in line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                q = m.get("quotaLimits")
                if isinstance(q, dict) and q.get("resetsAt"):
                    if not last_quota or q["resetsAt"] > last_quota.get("resetsAt", 0):
                        last_quota = {**q, "file": os.path.basename(fp)}
                msg = m.get("message") or {}
                u = msg.get("usage") or m.get("usage")
                if isinstance(u, dict):
                    model = msg.get("model") or m.get("model") or "unknown"
                    d = by_model.setdefault(model, {"in": 0, "out": 0, "cache_read": 0,
                                                    "cache_write": 0, "calls": 0})
                    d["in"] += u.get("input_tokens") or 0
                    d["out"] += u.get("output_tokens") or 0
                    d["cache_read"] += u.get("cache_read_input_tokens") or 0
                    d["cache_write"] += u.get("cache_creation_input_tokens") or 0
                    d["calls"] += 1
                    sessions.add(fp)
        except OSError:
            continue

    # scan past quota events too (last known state if no file for today)
    if last_quota is None:
        for fp in sorted(files, key=os.path.getmtime, reverse=True)[:40]:
            try:
                for line in open(fp, errors="replace"):
                    if '"quotaLimits"' not in line:
                        continue
                    try:
                        q = (json.loads(line) or {}).get("quotaLimits")
                    except json.JSONDecodeError:
                        continue
                    if isinstance(q, dict) and q.get("resetsAt"):
                        if not last_quota or q["resetsAt"] > last_quota.get("resetsAt", 0):
                            last_quota = {**q, "file": os.path.basename(fp)}
            except OSError:
                continue
            if last_quota:
                break

    return {
        "today_sessions": len(sessions),
        "by_model": by_model,
        "totals": {
            k: sum(v[k] for v in by_model.values())
            for k in ("in", "out", "cache_read", "cache_write", "calls")
        } if by_model else {},
        "last_quota_event": last_quota,
        "live": claude_live(),
        "caveat": ("Live percentages come from `claude -p /usage` output and rely on "
                   "sessions on this machine; other devices and claude.ai are not included."),
    }


def snapshot():
    return {"generated_at": int(time.time()), "workers": workers_usage(),
            "claude": claude_usage()}


def format_report(snap):
    out = ["# Remaining usage", "", "## Worker models"]
    for w in snap["workers"]:
        if not w["ready"]:
            out.append(f"- {w['provider']}: no key")
            continue
        if w.get("models") and w.get("per_model_limits"):
            out.append(f"- {w['provider']} ({w.get('tier') or 'per-model limit'}): "
                       f"today {w['today_requests']} requests | source: {w['source']}")
            for m in w["models"]:
                mq = m.get("quota") or {}
                dq = mq.get("day_requests") or {}
                rpm = mq.get("min_requests") or {}
                tpm = mq.get("min_tokens") or {}
                if not dq:
                    continue
                pct = dq["remaining"] / dq["limit"] * 100 if dq.get("limit") else None
                out.append(
                    f"  - {m['model']}: REMAINING {dq['remaining']}/{dq['limit']} daily requests"
                    + (f" ({pct:.0f}%)" if pct is not None else "")
                    + f" | min requests {rpm.get('used')}/{rpm.get('limit')}"
                    + f" | min tokens {tpm.get('used')}/{tpm.get('limit')}"
                    + f" | today {m['requests_today']} requests, {m['tokens_today']} tokens")
                ft = m.get("free_tier") or {}
                if ft.get("exceeded"):
                    out.append(f"    ! FREE TIER EXCEEDED: {', '.join(ft['exceeded'])}")
                elif ft.get("day_remaining") is not None:
                    out.append(f"    would remain in free tier: "
                               f"{ft['day_remaining']}/{ft['limits']['rpd']} daily requests")
            if w.get("today_failed"):
                out.append(f"  ! {w['today_failed']} failed requests")
            continue

        q = w.get("quota") or {}
        rq = q.get("requests") or {}
        bits = [f"today {w['today_requests']} requests",
                f"{w['today_tokens_in']}+{w['today_tokens_out']} tokens"]
        if rq.get("limit"):
            pct = (rq["remaining"] / rq["limit"] * 100) if rq.get("remaining") is not None else None
            bits.insert(0, f"REMAINING {rq['remaining']}/{rq['limit']}"
                           + (f" ({pct:.0f}%)" if pct is not None else ""))
            if rq.get("reset"):
                bits.append(f"resets: {rq['reset']}")
        if q.get("tokens", {}).get("limit"):
            t = q["tokens"]
            bits.append(f"token window {t['remaining']}/{t['limit']}")
        if w.get("today_failed"):
            bits.append(f"{w['today_failed']} FAILED")
        out.append(f"- {w['provider']}: " + " | ".join(bits))
        out.append(f"  source: {w['source']}")
        if w.get("note"):
            out.append(f"  note: {w['note']}")
    c = snap["claude"]
    live = c.get("live") or {}
    out += ["", "## Claude (live subscription limits)"]
    if live.get("pending"):
        out.append("- fetching initial measurement (claude -p /usage), ask again in a few seconds")
    elif live.get("ok") and live.get("limits"):
        age = live.get("age_sec") or 0
        for L in live["limits"]:
            out.append(f"- {L['label']}: REMAINING {L['remaining_pct']}% "
                       f"({L['used_pct']}% used)"
                       + (f", resets {L['resets']}" if L.get("resets") else ""))
        spans = ", ".join(
            f"{x['span']}: {x['requests']} requests"
            + (f"/{x['sessions']} sessions" if x.get("sessions") else "")
            for x in (live.get("spans") or []))
        if spans:
            out.append(f"- Anthropic count -> {spans}")
        out.append(f"- measurement age: {age} sec (120 sec cache)")
    elif live.get("error"):
        out.append(f"- could not fetch live limits: {live['error']}")
    tt = c.get("totals") or {}
    out += ["", "## Claude (local transcript count, today)",
            f"- {tt.get('calls', 0)} requests | output {tt.get('out', 0)} tok | "
            f"cache read {tt.get('cache_read', 0)} tok | {c.get('today_sessions', 0)} sessions"]
    q = None if (live.get("ok") and live.get("limits")) else c.get("last_quota_event")
    if q:
        import datetime
        ts = datetime.datetime.fromtimestamp(q["resetsAt"])
        past = ts < datetime.datetime.now()
        out.append(f"- last limit event: {q.get('rateLimitType')} / {q.get('status')}, "
                   f"resets {ts:%Y-%m-%d %H:%M}" + (" (PAST, not up to date)" if past else ""))
    out.append(f"- {c.get('caveat')}")
    return "\n".join(out)


def report():
    return format_report(snapshot())


if __name__ == "__main__":
    print(report())
