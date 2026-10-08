/* ai-workers UI core: state, data, i18n, formatting and actions shared by every theme.
 *
 * A theme ("view") registers AW.views[name] = { mount(root), unmount(), update(what) } and
 * renders everything inside #root from AW.state. `update` receives one of:
 *   'usage'   quota snapshot changed          'calls'  call list / filters / pause changed
 *   'detail'  selected call detail changed    'live'   stream connection changed
 *   'tab'     tab switched                    'tick'   once a second (live timers)
 *   'all'     language changed or first render
 */
(() => {
'use strict';
const AW = window.AW = { views: {} };

/* ------------------------------------------------------------ storage + i18n */
const store = AW.store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};
let LANG = store.get('aw.lang', (navigator.language || 'en').toLowerCase().startsWith('tr') ? 'tr' : 'en');
if (!I18N[LANG]) LANG = 'en';
let nf = new Intl.NumberFormat(LANG === 'tr' ? 'tr-TR' : 'en-US');

AW.lang = () => LANG;
AW.locale = () => LANG === 'tr' ? 'tr-TR' : 'en-US';
const t = AW.t = (key, vars) => {
  let s = (I18N[LANG] || {})[key] ?? I18N.en[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ''));
  return s;
};
AW.tp = (key, count, vars) => t(count === 1 && I18N.en[key + '_one'] ? key + '_one' : key, { n: count, ...vars });
AW.bx = s => {  /* translate known backend phrases */
  s = String(s ?? '');
  if (LANG !== 'tr' || !s) return s;
  for (const k of Object.keys(BACKEND_TR).sort((a, b) => b.length - a.length)) s = s.split(k).join(BACKEND_TR[k]);
  return s;
};
AW.trLabel = x => String(x || '').toLowerCase().trim()
  .replace('all models', t('lbl_all_models')).replace('session', t('lbl_session'))
  .replace('week', t('lbl_week')).replace('current', '').trim();
AW.upper = s => String(s ?? '').toLocaleUpperCase(AW.locale());

/* ------------------------------------------------------------ formatting */
const n = AW.n = v => (v === null || v === undefined) ? '—' : nf.format(v);
const esc = AW.esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
AW.kfmt = v => v == null ? '—' : v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(v);
const dfmt = AW.dfmt = s => s == null ? '—' : s < 1 ? Math.round(s * 1000) + ' ms'
  : s < 60 ? s.toFixed(1) + ' s' : Math.floor(s / 60) + 'm ' + String(Math.round(s % 60)).padStart(2, '0') + 's';
AW.tfmt = ts => new Date(ts * 1000).toLocaleTimeString(AW.locale(), { hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23' });
AW.dtfmt = ts => {
  const d = new Date(ts * 1000);
  return d.toDateString() === new Date().toDateString() ? AW.tfmt(ts)
    : d.toLocaleDateString(AW.locale(), { month:'short', day:'numeric' }) + ' ' + AW.tfmt(ts);
};
AW.ago = ts => {
  const s = Date.now() / 1000 - ts;
  return s < 5 ? t('just_now') : s < 60 ? t('s_ago', { n: Math.floor(s) }) : s < 3600 ? t('m_ago', { n: Math.floor(s / 60) })
    : s < 86400 ? t('h_ago', { n: Math.floor(s / 3600) }) : t('d_ago', { n: Math.floor(s / 86400) });
};
const nowS = AW.nowS = () => Date.now() / 1000;
AW.stLabel = k => { const v = t('s_' + k); return v === 's_' + k ? k : v; };
AW.who = x => (x && x.source === 'mcp') ? 'Claude Code' : (x && x.source === 'cli') ? 'CLI' : ((x && x.source) || t('unknown'));
AW.attempts = node => {
  const out = [];
  (function walk(x) { for (const c of x.children || []) { if (c.kind === 'attempt') out.push(c); walk(c); } })(node);
  return out;
};
AW.jobs = d => (d.children || []).filter(c => c.kind === 'job');
AW.statusOf = pct => pct === null || pct === undefined ? { key:'none', label:t('st_unknown') }
  : pct > 50 ? { key:'good', label:t('st_good') } : pct > 20 ? { key:'warning', label:t('st_warning') }
  : pct > 5 ? { key:'serious', label:t('st_serious') } : { key:'critical', label:t('st_critical') };
AW.mb = bytes => bytes == null ? '—' : (bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0) + ' MB';

/* ------------------------------------------------------------ state */
const S = AW.state = {
  tab: 'overview', usage: null, model: null, auto: true, showTable: false,
  calls: [], shown: [], sel: null, detail: null, paused: false, live: false, settings: null, storage: null,
  filters: { q: '', status: '', tool: '', prov: '' },
  job: null, attempt: null, ptab: {}, open: new Set(), blobs: new Map(), wrap: true,
};
let VIEW = null, SKIN = null;
const emit = what => { if (VIEW) try { VIEW.update(what); } catch (e) { console.error(e); } };

/* ------------------------------------------------------------ usage → overview model */
function buildModel(d) {
  const meters = [], notes = [];
  let tightest = null, req = 0, tok = 0, fail = 0;
  const consider = (pct, label, extra) => { if (pct !== null && (!tightest || pct < tightest.pct)) tightest = { pct, label, ...extra }; };
  const pctOf = (r, l) => (l && l > 0 && r !== null && r !== undefined) ? Math.max(0, Math.min(100, r / l * 100)) : null;
  for (const w of d.workers || []) {
    req += w.today_requests || 0;
    tok += (w.today_tokens_in || 0) + (w.today_tokens_out || 0);
    fail += w.today_failed || 0;
    if (!w.ready) { meters.push({ kind: 'nokey', provider: w.provider, name: w.provider }); continue; }
    if (w.models && w.per_model_limits) {
      const group = { kind: 'group', provider: w.provider, name: w.provider, tier: w.tier, source: AW.bx(w.source),
                      requests: w.today_requests, models: w.models.length, children: [] };
      meters.push(group);
      for (const m of w.models) {
        const q = m.quota || {}, dq = q.day_requests;
        if (!dq) continue;
        const ft = m.free_tier;
        const pct = pctOf(dq.remaining, dq.limit);
        const mt = { kind: 'model', provider: w.provider, name: m.model, short: m.model.replace(/^gemini-/, ''),
          remaining: dq.remaining, limit: dq.limit, pct, unit: 'req_day', reset: t('daily'), requests: m.requests_today,
          minReq: q.min_requests, minTok: q.min_tokens,
          free: ft && ft.day_remaining !== null ? { remaining: ft.day_remaining, limit: ft.limits.rpd } : null };
        group.children.push(mt); meters.push(mt);
        consider(pct, `${w.provider} · ${m.model}`, { remaining: dq.remaining, limit: dq.limit });
        if (ft && ft.exceeded) notes.push(t('exceeds_free', { m: m.model, x: AW.bx(ft.exceeded.join(', ')) }));
      }
      if (w.today_failed) notes.push(t('failed_requests', { p: w.provider, n: w.today_failed }));
      continue;
    }
    const q = w.quota;
    let src = AW.bx(w.source || '');
    if (w.as_of_age_sec > 120) src += ' · ' + t('measured_ago', { n: Math.round(w.as_of_age_sec / 60) });
    if (q && q.requests) {
      const rq = q.requests, pct = pctOf(rq.remaining, rq.limit);
      meters.push({ kind: 'provider', provider: w.provider, name: w.provider, remaining: rq.remaining, limit: rq.limit, pct,
                    unit: 'req_day', reset: AW.bx(rq.reset), source: src, requests: w.today_requests });
      consider(pct, w.provider, { remaining: rq.remaining, limit: rq.limit });
      if (q.tokens && q.tokens.limit) meters.push({ kind: 'window', provider: w.provider, name: `${w.provider} ${t('token_window')}`,
        remaining: q.tokens.remaining, limit: q.tokens.limit, pct: pctOf(q.tokens.remaining, q.tokens.limit),
        unit: 'tok_min', reset: AW.bx(q.tokens.reset), source: t('per_min_tokens') });
      if (q.per_minute && q.per_minute.limit) meters.push({ kind: 'window', provider: w.provider, name: `${w.provider} ${t('per_min_requests')}`,
        remaining: q.per_minute.remaining, limit: q.per_minute.limit, pct: pctOf(q.per_minute.remaining, q.per_minute.limit),
        unit: 'req_min', reset: '60s', source: t('local_counter') });
    } else {
      meters.push({ kind: 'unlimited', provider: w.provider, name: w.provider, requests: w.today_requests, source: AW.bx(w.source) });
      if (w.note) notes.push(`${w.provider}: ${AW.bx(w.note)}`);
    }
    if (w.credits && w.free_tier === false) notes.push(t('credit', { p: w.provider, r: w.credits.remaining, l: w.credits.limit }));
  }
  const c = d.claude || {}, live = c.live || {};
  const limits = (live.limits || []).filter(L => typeof L.remaining_pct === 'number')
    .map(L => ({ label: AW.trLabel(L.label), pct: L.remaining_pct, used: L.used_pct, resets: AW.bx(L.resets) }));
  for (const L of limits) consider(L.pct, 'Claude · ' + L.label, { resets: L.resets, claude: true });
  const tt = c.totals || {};
  return {
    generated: d.generated_at, meters, notes, tightest, kpis: { req, tok, fail },
    claude: { limits, pending: !!live.pending, ok: !!live.ok, error: live.error ? AW.bx(live.error) : null,
              age: live.age_sec, refreshing: !!live.refreshing,
              calls: tt.calls || 0, out: tt.out || 0, cacheRead: tt.cache_read || 0, sessions: c.today_sessions || 0,
              spans: live.spans || [], lastEvent: (live.ok && limits.length) ? null : c.last_quota_event },
    workers: d.workers || [],
  };
}

/* ------------------------------------------------------------ data loading */
let usageTimer = null;
AW.refreshUsage = async () => {
  try {
    const d = await (await fetch('/api/usage', { cache:'no-store' })).json();
    if (d.error) { S.usageError = d.error; } else { S.usage = d; S.model = buildModel(d); S.usageError = null; }
  } catch (e) { S.usageError = t('unreachable'); }
  emit('usage');
};
AW.setAuto = on => {
  S.auto = on;
  clearInterval(usageTimer);
  if (on) usageTimer = setInterval(AW.refreshUsage, 20000);
  emit('usage');
};
AW.toggleTable = () => { S.showTable = !S.showTable; emit('usage'); };

AW.loadCalls = async () => {
  let d;
  try { d = await (await fetch('/api/activity?limit=500', { cache:'no-store' })).json(); }
  catch (e) { setLive(false); return; }
  S.calls = d.calls || [];
  S.settings = d.settings;
  S.storage = d.storage_bytes ?? null;
  if (!S.paused) S.shown = S.calls;
  emit('calls');
  if (S.sel) {
    const c = S.calls.find(x => x.id === S.sel);
    const sig = c ? JSON.stringify(c) : null;
    if (sig !== S.selSig) { S.selSig = sig; AW.loadDetail(S.sel, true); }
  }
};
AW.loadDetail = async (id, quiet) => {
  let d;
  try {
    const r = await fetch('/api/activity/' + encodeURIComponent(id), { cache:'no-store' });
    if (!r.ok) throw new Error('not found');
    d = await r.json();
  } catch (e) { if (!quiet) { S.detail = null; S.detailMissing = true; emit('detail'); } return; }
  if (id !== S.sel) return;
  const sig = JSON.stringify(d);
  if (quiet && sig === S.detailSig) return;
  S.detailSig = sig; S.detail = d; S.detailMissing = false;
  emit('detail');
};
AW.select = id => {
  if (!id || id === S.sel) return;
  S.sel = id; S.detail = null; S.detailSig = null; S.job = null; S.attempt = null; S.open.clear();
  const c = S.calls.find(x => x.id === id);
  S.selSig = c ? JSON.stringify(c) : null;
  history.replaceState(null, '', '#activity/' + id);
  emit('calls'); emit('detail');
  AW.loadDetail(id);
};
AW.selectJob = id => { S.job = id; S.attempt = null; emit('detail'); };
AW.selectAttempt = id => { S.attempt = id; emit('detail'); };
AW.setPtab = (key, tab) => { S.ptab[key] = tab; emit('detail'); };
AW.toggleOpen = id => { S.open.has(id) ? S.open.delete(id) : S.open.add(id); emit('detail'); };
AW.toggleWrap = () => { S.wrap = !S.wrap; emit('detail'); };

/* current job / attempt (views without nested expansion show one attempt at a time) */
AW.currentJob = () => {
  const d = S.detail; if (!d) return null;
  const jobs = AW.jobs(d);
  return jobs.find(j => j.id === S.job) || jobs.find(j => j.status === 'error') || jobs[0] || null;
};
AW.currentAttempt = () => {
  const d = S.detail; if (!d) return null;
  const all = AW.attempts(d);
  if (S.attempt) { const a = all.find(x => x.id === S.attempt); if (a) return a; }
  const j = AW.currentJob(); const atts = j ? AW.attempts(j) : all;
  return atts.filter(a => a.status === 'ok').pop() || atts[atts.length - 1] || null;
};

AW.filtered = () => {
  const f = S.filters, q = f.q.trim().toLowerCase();
  return S.shown.filter(c => {
    if (f.status === 'running' && c.status !== 'running') return false;
    if (f.status === 'ok' && c.status !== 'ok') return false;
    if (f.status === 'failed' && !['error', 'partial', 'abandoned', 'warning'].includes(c.status)) return false;
    if (f.tool && c.tool !== f.tool) return false;
    if (f.prov && !c.providers.some(p => p.startsWith(f.prov + '/'))) return false;
    if (q) {
      const hay = [c.title, c.tool, c.project, c.client, AW.who(c), c.status, AW.stLabel(c.status), ...c.roles, ...c.providers].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
};
AW.filterOptions = () => ({
  status: ['', 'running', 'ok', 'failed'],
  tool: ['', ...[...new Set(S.calls.map(c => c.tool))].sort()],
  prov: ['', ...[...new Set(S.calls.flatMap(c => c.providers.map(p => p.split('/')[0])))].sort()],
});
AW.filterLabel = (key, v) => {
  if (key === 'status') return { '': t('all_statuses'), running: t('running'), ok: t('succeeded'), failed: t('failed_partial') }[v];
  if (!v) return key === 'tool' ? t('all_tools') : t('all_providers');
  return v;
};
AW.setFilter = (key, v) => { S.filters[key] = v; emit('calls'); };
AW.cycleFilter = key => {  /* rotary knobs */
  const opts = AW.filterOptions()[key];
  AW.setFilter(key, opts[(opts.indexOf(S.filters[key]) + 1) % opts.length]);
};
AW.setPaused = on => { S.paused = on; if (!on) S.shown = S.calls; emit('calls'); };
AW.freshCount = () => S.paused ? S.calls.filter(c => !S.shown.some(x => x.id === c.id)).length : 0;
AW.running = () => S.calls.filter(c => c.status === 'running');
AW.recentFailure = () => S.calls.some(c => ['error', 'abandoned'].includes(c.status) && nowS() - (c.end || c.ts) < 900);

/* payloads: inline text or a deduplicated blob fetched on demand */
AW.payloadText = p => {
  if (!p) return { text: '', partial: false, size: 0 };
  if (p.ref) {
    const full = S.blobs.get(p.ref);
    return full != null ? { text: full, partial: false, size: p.size, ref: p.ref } : { text: p.preview || '', partial: true, size: p.size, ref: p.ref };
  }
  return { text: p.text ?? '', partial: false, size: p.size ?? (p.text || '').length };
};
AW.loadBlob = async ref => {
  if (S.blobs.has(ref)) return S.blobs.get(ref);
  const r = await fetch('/api/blob/' + ref, { cache:'no-store' });
  if (!r.ok) throw new Error(await r.text());
  const x = await r.text();
  S.blobs.set(ref, x);
  emit('detail');
  return x;
};
AW.copyPayload = async p => {
  try { AW.copy(p.ref ? await AW.loadBlob(p.ref) : (p.text ?? '')); } catch (e) { AW.toast(t('payload_unavailable')); }
};
AW.copy = async text => {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
  }
  AW.toast(t('copied', { n: n(text.length) }));
};
let toastT;
AW.toast = msg => {
  let el = document.getElementById('aw-toast');
  if (!el) { el = document.createElement('div'); el.id = 'aw-toast'; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove('show'), 1600);
};

/* attempt tabs shared by every theme */
AW.attemptTabs = a => [['system', t('t_system')], ['input', t('t_input')], ['response', t('t_response')], ['error', t('t_error')], ['raw', t('t_raw')]]
  .filter(([k]) => (k !== 'error' || a.error) && (k !== 'response' || a.response));
AW.attemptTab = a => {
  const tabs = AW.attemptTabs(a), want = S.ptab[a.id] || (a.status === 'ok' ? 'response' : a.status === 'running' ? 'input' : 'error');
  return tabs.some(([k]) => k === want) ? want : tabs[1][0];
};
AW.attemptPayload = (a, tab) => {
  if (tab === 'system') return a.system || null;
  if (tab === 'input') return a.prompt;
  if (tab === 'response') return a.response;
  if (tab === 'error') return { text: a.error, size: String(a.error).length };
  const raw = { ...a }; delete raw.children;
  const txt = JSON.stringify(raw, null, 2);
  return { text: txt, size: txt.length };
};
AW.noteText = nt => nt.name === 'retry'
  ? t('retry_note', { n: nt.attempt, why: nt.http ? t('http_n', { n: nt.http }) : t('error_word'), d: dfmt(nt.delay) })
  : nt.name === 'fallback' ? t('fallback_note', { p: nt.provider, next: (nt.next || []).join(', ') }) : `${nt.name}`;

/* provider groups of a call for flow diagrams */
AW.stations = d => {
  const agg = new Map();
  for (const a of AW.attempts(d)) {
    const k = `${a.provider}/${a.model}`;
    const g = agg.get(k) || { provider: a.provider, model: a.model, ok: 0, error: 0, skipped: 0, running: 0, tin: 0, tout: 0, dur: 0, errors: [] };
    g[['ok', 'error', 'skipped', 'running'].includes(a.status) ? a.status : 'error']++;
    g.tin += (a.tokens || {}).in || 0; g.tout += (a.tokens || {}).out || 0; g.dur += a.dur || 0;
    if (a.error) g.errors.push(a.error);
    agg.set(k, g);
  }
  return [...agg.values()].map(g => ({ ...g, status: g.running ? 'running' : g.ok ? 'ok' : g.error ? 'error' : 'skipped' }));
};
AW.httpCode = err => { const m = /HTTP (\d{3})/.exec(err || ''); return m ? m[1] : null; };

/* ------------------------------------------------------------ live stream */
function setLive(on) { if (S.live !== on) { S.live = on; emit('live'); } }
let pending = null;
const schedule = () => { clearTimeout(pending); pending = setTimeout(AW.loadCalls, 200); };
function connect() {
  if (!window.EventSource) { setInterval(AW.loadCalls, 3000); return; }
  const es = new EventSource('/api/stream');
  es.addEventListener('hello', () => { setLive(true); schedule(); });
  es.addEventListener('activity', schedule);
  es.onopen = () => setLive(true);
  es.onerror = () => setLive(false);
}

/* ------------------------------------------------------------ tabs, skins, language */
AW.setTab = tab => {
  S.tab = tab === 'activity' ? 'activity' : 'overview';
  store.set('aw.tab', S.tab);
  document.documentElement.dataset.page = S.tab;
  history.replaceState(null, '', '#' + S.tab + (S.tab === 'activity' && S.sel ? '/' + S.sel : ''));
  emit('tab');
};
AW.setLang = lang => {
  LANG = I18N[lang] ? lang : 'en';
  store.set('aw.lang', LANG);
  nf = new Intl.NumberFormat(AW.locale());
  document.documentElement.lang = LANG;
  if (S.usage) S.model = buildModel(S.usage);
  emit('all');
};
AW.SKINS = ['modern', 'synthwave', 'apollo'];
AW.setSkin = skin => {
  if (!AW.SKINS.includes(skin)) skin = 'modern';
  SKIN = skin;
  store.set('aw.skin', skin);
  document.documentElement.dataset.skin = skin;
  for (const l of document.querySelectorAll('link[data-skin-css]')) l.disabled = l.dataset.skinCss !== skin;
  if (VIEW && VIEW.unmount) VIEW.unmount();
  const root = document.getElementById('root');
  root.innerHTML = '';
  VIEW = AW.views[skin];
  VIEW.mount(root);
  emit('all');
};
AW.skin = () => SKIN;
AW.setMode = mode => {  /* modern theme only: light / dark / system */
  if (mode) document.documentElement.dataset.theme = mode; else delete document.documentElement.dataset.theme;
  store.set('aw.mode', mode || '');
};
AW.toggleMode = () => {
  const cur = document.documentElement.dataset.theme;
  const dark = cur ? cur === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  AW.setMode(dark ? 'light' : 'dark');
};

/* shared controls markup (theme picker + language), styled by each theme */
AW.controlsHTML = (extraClass = '') => `
  <div class="aw-controls ${extraClass}">
    <select class="aw-skin" aria-label="${esc(t('theme_label'))}" title="${esc(t('theme_label'))}">
      ${AW.SKINS.map(s => `<option value="${s}"${s === SKIN ? ' selected' : ''}>${esc(t('skin_' + s))}</option>`).join('')}
    </select>
    <div class="aw-lang" role="group" title="${esc(t('lang_title'))}">
      ${['en', 'tr'].map(l => `<button type="button" data-lang="${l}" aria-pressed="${l === LANG}">${l.toUpperCase()}</button>`).join('')}
    </div>
  </div>`;
AW.bindControls = root => {
  root.addEventListener('change', e => { if (e.target.classList.contains('aw-skin')) AW.setSkin(e.target.value); });
  root.addEventListener('click', e => { const b = e.target.closest('[data-lang]'); if (b) AW.setLang(b.dataset.lang); });
};

/* ------------------------------------------------------------ keyboard + timers */
document.addEventListener('keydown', e => {
  if (S.tab !== 'activity') return;
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
  if (e.key === 'Escape' && typing) { if (S.filters.q) AW.setFilter('q', ''); document.activeElement.blur(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { const q = document.querySelector('[data-role="search"]'); if (q) { e.preventDefault(); q.focus(); } return; }
  const dir = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key];
  if (!dir) return;
  e.preventDefault();
  const rows = AW.filtered();
  if (!rows.length) return;
  const i = rows.findIndex(r => r.id === S.sel);
  AW.select(rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + dir))].id);
  const el = document.querySelector(`[data-call="${S.sel}"]`);
  if (el) el.scrollIntoView({ block: 'nearest' });
});
setInterval(() => {
  for (const el of document.querySelectorAll('[data-since]')) el.textContent = dfmt(nowS() - +el.dataset.since);
  emit('tick');
}, 1000);
setInterval(AW.loadCalls, 15000);  /* safety net if the stream silently stalls */

/* ------------------------------------------------------------ boot */
AW.boot = () => {
  document.documentElement.lang = LANG;
  AW.setMode(store.get('aw.mode', '') || null);
  const [h0, h1] = location.hash.slice(1).split('/');
  S.tab = (h0 === 'activity' || h0 === 'overview') ? h0 : store.get('aw.tab', 'overview');
  document.documentElement.dataset.page = S.tab;
  if (h0 === 'activity' && h1) { S.sel = h1; AW.loadDetail(h1); }
  AW.setSkin(store.get('aw.skin', 'modern'));
  connect(); AW.loadCalls(); AW.refreshUsage(); AW.setAuto(true);
};
})();
